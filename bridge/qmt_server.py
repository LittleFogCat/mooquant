"""
mookquant · QMT 桥接服务（真实接入版）

协议：stdio JSON-RPC（每行一条 JSON）

请求示例：
  {"id":"1","method":"quote.snapshot","params":{"code":"600519.SH"}}
  {"id":"2","method":"quote.subscribe","params":{"code":"600519.SH","period":"tick"}}
  {"id":"3","method":"quote.history","params":{"code":"600519.SH","period":"1d","count":30}}

响应：
  {"id":"1","result":{...}}
  {"id":"1","error":"错误信息"}

依赖：
  安装主人的 xtquant 包，把解压后的目录加到 PYTHONPATH
  或者把 xtquant 文件夹拷到 site-packages

部署：
  1. 启动 miniQMT 客户端（默认监听 127.0.0.1:58610）
  2. python qmt_server.py
"""
import json
import sys
import time
import threading
import os as _os_mod

# SQLite cache module (same directory)
sys.path.insert(0, _os_mod.path.dirname(_os_mod.path.abspath(__file__)))
import db as db_cache

# 策略框架（回测与实盘共用同一份策略代码）
from strategies.registry import load_all, get as get_strategy, list_strategies, save_strategy, delete_strategy
from strategies.base import Context

# 启动时预加载所有策略（重启加载模式；热加载后续增强）
try:
    load_all()
except Exception as _e:
    sys.stderr.write('[qmt] strategy preload failed: %s\n' % _e)


# ----------------------------------------------------------------------
# 编码修复：Windows 默认 GBK stdout，改成 UTF-8
# ----------------------------------------------------------------------
def _force_utf8_stdout():
    out = sys.stdout
    try:
        out.reconfigure(encoding="utf-8", errors="strict")
    except Exception:
        import io
        sys.stdout = io.TextIOWrapper(out.buffer, encoding="utf-8")


_force_utf8_stdout()


def log(msg):
    sys.stderr.write("[qmt-bridge] {}\n".format(msg))
    sys.stderr.flush()


def reply(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


# ----------------------------------------------------------------------
# xtquant 初始化（惰性、容错）
# ----------------------------------------------------------------------
_XTDATA = None
_CONNECTED = False
_CONNECT_LOCK = threading.Lock()


def ensure_xtquant():
    """按主人路径加载 xtquant；抑制其内部 stdout 输出"""
    global _XTDATA
    if _XTDATA is not None:
        return _XTDATA

    import os
    import io
    import contextlib

    # 主人的 xtquant 路径
    custom_path = os.environ.get("XTQUANT_PATH", "")
    if os.path.isdir(custom_path):
        sys.path.insert(0, custom_path)

    # 抑制 xtquant 内部的 stdout 输出（会破坏 JSON-RPC 协议）
    # 把 stdout 重定向到 /dev/null 或 io.StringIO
    _suppress_stdout = io.StringIO()
    with contextlib.redirect_stdout(_suppress_stdout):
        try:
            from xtquant import xtdata
            _XTDATA = xtdata
        except ImportError as e:
            raise RuntimeError(
                "未找到 xtquant：请确认包已安装或路径正确。原始错误：{}".format(e)
            )

    # 把被抑制的输出写到 stderr（仅调试用）
    leaked = _suppress_stdout.getvalue()
    if leaked:
        log("xtquant 装载时输出（已转 stderr）: " + leaked.replace("\n", " | "))
    log("xtquant 已加载")
    return _XTDATA


def ensure_connected(port=58610):
    """确保已连上 miniQMT；同时抑制 xt.connect 内部的 stdout 输出"""
    global _CONNECTED
    with _CONNECT_LOCK:
        if _CONNECTED:
            return
        xt = ensure_xtquant()

        # 抑制 connect / hello 的 stdout 输出（破坏 JSON-RPC 协议）
        import io as _io
        import contextlib as _ctx
        _suppress = _io.StringIO()
        with _ctx.redirect_stdout(_suppress):
            xt.connect(port=port)
        leaked = _suppress.getvalue()
        if leaked:
            for line in leaked.strip().split("\n"):
                log("xt-quant connect 输出: " + line)

        _CONNECTED = True
        log("已连上 miniQMT port={}".format(port))


# ----------------------------------------------------------------------
# 代码转换工具
# ----------------------------------------------------------------------
def to_xtcode(raw):
    """
    将 UI 代码转换为 xtquant 标准格式：
      sh600519 / 600519.SH -> 600519.SH
      sz000001           -> 000001.SZ
      bj830xxx           -> 830xxx.BJ
      AAPL               -> AAPL.US
    """
    s = (raw or "").strip()
    if not s:
        return ""

    lower = s.lower()
    if "." in lower:
        head, _, tail = lower.partition(".")
        return head.upper() + "." + tail.upper()

    if lower.startswith("sh"):
        return lower[2:].upper() + ".SH"
    if lower.startswith("sz"):
        return lower[2:].upper() + ".SZ"
    if lower.startswith("bj"):
        return lower[2:].upper() + ".BJ"

    if lower.isdigit() and len(lower) == 6:
        f = lower[0]
        if f in ("6", "9", "5"):
            return lower + ".SH"
        if f in ("0", "2", "3"):
            return lower + ".SZ"
        return lower + ".SH"

    if lower.isalpha():
        return lower.upper() + ".US"

    return lower.upper()


def from_xtcode(xtcode):
    """600519.SH -> ('600519', 'sh', '上海', 'CNY')"""
    head, _, market = (xtcode or "").upper().partition(".")
    if market == "SH":
        return head, "sh", "上海", "CNY"
    if market == "SZ":
        return head, "sz", "深圳", "CNY"
    if market == "BJ":
        return head, "bj", "北交所", "CNY"
    if market == "HK":
        return head, "hk", "港股", "HKD"
    return head, "us", "美股", "USD"


# ----------------------------------------------------------------------
# 业务方法
# ----------------------------------------------------------------------
def handle_ping(params):
    """心跳 + 测试 miniQMT 连接"""
    try:
        ensure_connected(params.get("port", 58610) if params else 58610)
        return {"alive": True, "connected": True, "ts": time.time()}
    except Exception as e:
        return {"alive": True, "connected": False, "error": str(e)}


def handle_quote_snapshot(params):
    """
    获取股票快照

    params:
      code: str   UI 代码（sh600519 / sz000001 / 600519.SH）
      port: int   miniQMT 端口（默认 58610）
    """
    code = to_xtcode(params.get("code", ""))
    if not code:
        raise ValueError("code 不能为空")
    port = int(params.get("port", 58610))

    ensure_connected(port)
    xt = _XTDATA

    log("snapshot: {}".format(code))

    tick_data = xt.get_full_tick([code])
    tick = tick_data.get(code) or {}
    if not tick:
        raise RuntimeError("未找到该标的或数据为空: " + code)

    info = {}
    try:
        info = xt.get_instrument_detail(code) or {}
    except Exception as e:
        log("get_instrument_detail 失败（忽略）: {}".format(e))

    # 字段映射（xtquant tick 字段是驼峰）
    number, market, marketName, currency = from_xtcode(code)
    price = float(tick.get("lastPrice", 0) or 0)
    prev = float(tick.get("lastClose", 0) or 0)
    open_ = float(tick.get("open", 0) or 0)
    high = float(tick.get("high", 0) or 0)
    low = float(tick.get("low", 0) or 0)
    # xtquant 的 volume 单位是「手」（1手=100股），UI 统一展示为「股」
    volume_lots = float(tick.get("volume", 0) or 0)
    volume_shares = volume_lots * 100
    amount = float(tick.get("amount", 0) or 0)

    change = price - prev if prev else 0
    change_percent = (change / prev * 100) if prev else 0

    try:
        ts = int(tick.get("time", time.time()))
    except Exception:
        ts = int(time.time())
    try:
        ts_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
    except Exception:
        ts_iso = time.strftime("%Y-%m-%dT%H:%M:%S")

    return {
        "code": code,
        "rawSymbol": params.get("code", ""),
        "name": info.get("InstrumentName", number),
        "industry": "—",
        "market": market,
        "marketName": marketName,
        "currency": currency,
        "price": round(price, 4),
        "open": round(open_, 4),
        "high": round(high, 4),
        "low": round(low, 4),
        "prevClose": round(prev, 4),
        "change": round(change, 4),
        "changePercent": round(change_percent, 4),
        "volume": int(volume_shares),
        "turnover": round(amount, 2),
        "timestamp": ts_iso,
    }


def _generate_mock_bars(code, count):
    import random, time, math
    from datetime import datetime, timedelta
    seed_val = sum(ord(c) for c in code) + 42
    rng = random.Random(seed_val)
    base_price = 50 + rng.random() * 200
    n = min(count if count > 0 else 60, 500)
    bars = []
    price = base_price
    now = datetime.now()
    for i in range(n):
        ret = rng.gauss(0, 0.02)
        open_p = price
        close_p = price * (1 + ret)
        high_p = max(open_p, close_p) * (1 + abs(rng.gauss(0, 0.008)))
        low_p = min(open_p, close_p) * (1 - abs(rng.gauss(0, 0.008)))
        vol = int(rng.uniform(500000, 5000000))
        dt = now - timedelta(days=n - 1 - i)
        bars.append({
            "time": int(dt.timestamp()),
            "date": dt.strftime("%Y-%m-%d"),
            "open": round(open_p, 2),
            "high": round(high_p, 2),
            "low": round(low_p, 2),
            "close": round(close_p, 2),
            "volume": vol,
        })
        price = close_p
    return bars


def _format_date_str(s):
    """Format xtquant date index to YYYY-MM-DD or YYYY-MM-DD HH:mm.

    Handles:
      8 digits  (YYYYMMDD)       -> YYYY-MM-DD
      12 digits (YYYYMMDDHHMM)   -> YYYY-MM-DD HH:mm
      14 digits (YYYYMMDDHHMMSS) -> YYYY-MM-DD HH:mm
    """
    digits = ''.join(c for c in str(s) if c.isdigit())
    if len(digits) == 8:
        return digits[:4] + "-" + digits[4:6] + "-" + digits[6:8]
    elif len(digits) >= 12:
        return digits[:4] + "-" + digits[4:6] + "-" + digits[6:8] + " " + digits[8:10] + ":" + digits[10:12]
    return str(s)


def _parse_bars(df):
    """Parse xtquant DataFrame into list of bar dicts"""
    bars = []
    if df is not None and len(df) > 0:
        for idx, row in df.iterrows():
            date_fmt = _format_date_str(idx)
            bars.append({
                "time": int(row.get("time", 0) or 0),
                "date": date_fmt,
                "open": float(row.get("open", 0) or 0),
                "high": float(row.get("high", 0) or 0),
                "low":  float(row.get("low",  0) or 0),
                "close": float(row.get("close", 0) or 0),
                "volume": float(row.get("volume", 0) or 0) * 100,
                "amount": float(row.get("amount", 0) or 0),
            })
    return bars


def _merge_bars(bars):
    """Merge multiple bars into one OHLCV bar"""
    if not bars:
        return None
    return {
        "time": bars[0]["time"],
        "date": bars[0]["date"],
        "open": bars[0]["open"],
        "high": max(b["high"] for b in bars),
        "low": min(b["low"] for b in bars),
        "close": bars[-1]["close"],
        "volume": sum(b["volume"] for b in bars),
        "amount": sum(b.get("amount", 0) for b in bars),
    }


def _aggregate_bars(bars, target_period):
    """Aggregate base-period bars into target-period bars.

    Weekly/Monthly: aggregate from daily bars.
    5m/15m/30m/60m: aggregate from 1-minute bars.
    """
    if not bars:
        return []
    if target_period == "1w":
        return _aggregate_weekly(bars)
    elif target_period == "1mon":
        return _aggregate_monthly(bars)
    elif target_period in ("5m", "15m", "30m", "60m"):
        n = int(target_period[:-1])
        return _aggregate_minutes(bars, n)
    return bars


def _aggregate_weekly(bars):
    """Aggregate daily bars into weekly bars (ISO week)."""
    from datetime import datetime
    result = []
    current_key = None
    current_group = []
    for bar in bars:
        try:
            dt = datetime.strptime(bar["date"][:10], "%Y-%m-%d")
        except (ValueError, KeyError, TypeError):
            continue
        iso = dt.isocalendar()
        week_key = (iso[0], iso[1])
        if week_key != current_key:
            if current_group:
                result.append(_merge_bars(current_group))
            current_key = week_key
            current_group = [bar]
        else:
            current_group.append(bar)
    if current_group:
        result.append(_merge_bars(current_group))
    return result


def _aggregate_monthly(bars):
    """Aggregate daily bars into monthly bars (by YYYY-MM)."""
    result = []
    current_key = None
    current_group = []
    for bar in bars:
        try:
            month_key = bar["date"][:7]  # YYYY-MM
        except (KeyError, TypeError):
            continue
        if month_key != current_key:
            if current_group:
                result.append(_merge_bars(current_group))
            current_key = month_key
            current_group = [bar]
        else:
            current_group.append(bar)
    if current_group:
        result.append(_merge_bars(current_group))
    return result


def _aggregate_minutes(bars, n):
    """Aggregate 1-minute bars into N-minute bars.

    Groups by trading day first, then chunks every n bars within each day.
    This correctly handles the midday break (no bars during 11:30-13:00).
    """
    from datetime import datetime
    days = {}
    day_order = []
    for bar in bars:
        if bar.get("date"):
            day = bar["date"][:10]
        elif bar.get("time"):
            day = datetime.fromtimestamp(bar["time"]).strftime("%Y-%m-%d")
        else:
            continue
        if day not in days:
            days[day] = []
            day_order.append(day)
        days[day].append(bar)

    result = []
    for day in day_order:
        day_bars = days[day]
        for i in range(0, len(day_bars), n):
            chunk = day_bars[i:i + n]
            if chunk:
                result.append(_merge_bars(chunk))
    return result


def handle_quote_history(params):
    code = to_xtcode(params.get("code", ""))
    if not code:
        raise ValueError("code \u4e0d\u80fd\u4e3a\u7a7a")
    period = params.get("period", "1d")
    count = int(params.get("count", -1))
    port = int(params.get("port", 58610))
    dividend_type = params.get("dividend_type", "front")

    # --- Determine base period ---
    # Weekly/Monthly K: aggregate from daily bars
    # 5m/15m/30m/60m: aggregate from 1-minute bars
    base_period = period
    need_aggregate = False
    if period in ("1w", "1mon"):
        base_period = "1d"
        need_aggregate = True
    elif period in ("5m", "15m", "30m", "60m"):
        base_period = "1m"
        need_aggregate = True

    if not need_aggregate:
        # --- Direct fetch (1d, 1m, tick, etc.) ---
        cached = db_cache.query_bars(code, period, count=count, dividend_type=dividend_type)
        if cached and (count <= 0 or len(cached) >= count):
            log("history: {} bars from DB cache (code={} period={})".format(len(cached), code, period))
            return {"bars": cached, "count": len(cached), "cached": True}

        ensure_connected(port)
        xt = _XTDATA
        log("history: fetching from xtquant (code={} period={} count={})".format(code, period, count))
        try:
            xt.download_history_data(code, period=period, incrementally=True)
        except Exception as e:
            log("download_history_data warning: {}".format(e))
        try:
            data = xt.get_market_data_ex([], [code], period=period, count=count, dividend_type=dividend_type) or {}
            df = data.get(code)
            bars = _parse_bars(df)
            log("history: got {} bars from xtquant".format(len(bars)))
            if bars:
                saved = db_cache.save_bars(code, period, bars, dividend_type=dividend_type)
                log("history: saved {} new bars to DB".format(saved))
            if not bars:
                log("history: no data for {} period={}".format(code, period))
                if cached:
                    return {"bars": cached, "count": len(cached), "cached": True}
            return {"bars": bars, "count": len(bars)}
        except Exception as e:
            log("get_market_data_ex failed: {}".format(e))
            if cached:
                log("history: returning {} bars from DB cache (xtquant failed)".format(len(cached)))
                return {"bars": cached, "count": len(cached), "cached": True}
            return {"bars": [], "count": 0, "error": str(e)}

    # --- Aggregation path ---
    # Calculate how many base bars we need
    base_count = count
    if count > 0:
        multipliers = {"1w": 7, "1mon": 31, "60m": 60, "30m": 30, "15m": 15, "5m": 5}
        mult = multipliers.get(period, 1)
        base_count = count * mult + mult  # extra margin for boundaries

    # 1. Query DB cache for base period
    cached = db_cache.query_bars(code, base_period, count=base_count, dividend_type=dividend_type)
    if cached and (base_count <= 0 or len(cached) >= base_count):
        aggregated = _aggregate_bars(cached, period)
        log("history: {} bars aggregated from {} cached {} bars (code={})".format(
            len(aggregated), len(cached), base_period, code))
        if count > 0:
            aggregated = aggregated[-count:]
        return {"bars": aggregated, "count": len(aggregated), "cached": True, "aggregated": True}

    # 2. Fetch base bars from xtquant
    ensure_connected(port)
    xt = _XTDATA
    log("history: fetching base {} from xtquant (code={} target={} count={})".format(
        base_period, code, period, base_count))
    try:
        xt.download_history_data(code, period=base_period, incrementally=True)
    except Exception as e:
        log("download_history_data warning: {}".format(e))
    try:
        data = xt.get_market_data_ex([], [code], period=base_period, count=base_count, dividend_type=dividend_type) or {}
        df = data.get(code)
        bars = _parse_bars(df)
        log("history: got {} base {} bars from xtquant".format(len(bars), base_period))

        # Save base bars to DB
        if bars:
            saved = db_cache.save_bars(code, base_period, bars, dividend_type=dividend_type)
            log("history: saved {} new base bars to DB".format(saved))

        # Merge cached + fresh, deduplicate by date
        all_bars = (cached or []) + bars
        seen = set()
        deduped = []
        for b in all_bars:
            key = b.get("date") or str(b.get("time", 0))
            if key not in seen:
                seen.add(key)
                deduped.append(b)

        # Aggregate
        aggregated = _aggregate_bars(deduped, period)
        log("history: {} bars aggregated from {} {} bars".format(len(aggregated), len(deduped), base_period))

        if not aggregated and cached:
            aggregated = _aggregate_bars(cached, period)

        if count > 0:
            aggregated = aggregated[-count:]

        return {"bars": aggregated, "count": len(aggregated), "aggregated": True}
    except Exception as e:
        log("get_market_data_ex failed: {}".format(e))
        if cached:
            aggregated = _aggregate_bars(cached, period)
            log("history: returning {} aggregated bars from cache (xtquant failed)".format(len(aggregated)))
            if count > 0:
                aggregated = aggregated[-count:]
            return {"bars": aggregated, "count": len(aggregated), "cached": True, "aggregated": True}
        return {"bars": [], "count": 0, "error": str(e)}


def handle_quote_subscribe(params):
    """订阅实时行情"""
    code = to_xtcode(params.get("code", ""))
    if not code:
        raise ValueError("code 不能为空")
    period = params.get("period", "tick")
    port = int(params.get("port", 58610))

    ensure_connected(port)
    xt = _XTDATA

    def _cb(data):
        try:
            if data and isinstance(data, dict):
                for _code, ticks in data.items():
                    if ticks and len(ticks) > 0:
                        tick = ticks[-1] if isinstance(ticks, list) else ticks
                        push = {
                            "id": None,
                            "push": "tick",
                            "data": {
                                "code": _code,
                                "price": float(tick.get("close", 0) or tick.get("lastPrice", 0) or 0),
                                "open": float(tick.get("open", 0) or 0),
                                "high": float(tick.get("high", 0) or 0),
                                "low": float(tick.get("low", 0) or 0),
                                "volume": float(tick.get("volume", 0) or 0),
                                "amount": float(tick.get("amount", 0) or 0),
                                "timestamp": str(tick.get("time", "")),
                            },
                        }
                        reply(push)
        except Exception as e:
            log("subscribe callback error: {}".format(e))

    xt.subscribe_quote(code, period=period, count=-1, callback=_cb)
    log("subscribed: {} period={}".format(code, period))
    return {"subscribed": True}



# ----------------------------------------------------------------------
# 交易连接（独立于行情）
# ----------------------------------------------------------------------
_TRADER = None
_TRADER_ACC = None
_TRADER_LOCK = threading.Lock()


def ensure_trader():
    """初始化交易连接：XtQuantTrader + 登录 + 订阅账号"""
    global _TRADER, _TRADER_ACC
    if _TRADER is not None:
        return _TRADER, _TRADER_ACC

    import os as _os
    import time as _time

    ensure_xtquant()  # 确保 xtquant 已加载

    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
    from xtquant.xttype import StockAccount

    path = _os.environ.get("QMT_PATH", "")
    if not path:
        raise RuntimeError("未配置 QMT_PATH（miniQMT userdata 目录）")

    account_id = _os.environ.get("QMT_ACCOUNT_ID", "")
    account_type = _os.environ.get("QMT_ACCOUNT_TYPE", "STOCK")
    if not account_id:
        raise RuntimeError("未配置 QMT_ACCOUNT_ID")

    session_id = int(_time.time())

    class _TraderCallback(XtQuantTraderCallback):
        def on_disconnected(self):
            log("交易连接断开")

        def on_stock_order(self, order):
            log("委托回报: {} status={} msg={}".format(
                order.stock_code, order.order_status, order.status_msg))

        def on_stock_trade(self, trade):
            log("成交回报: {} price={} volume={}".format(
                trade.stock_code, trade.traded_price, trade.traded_volume))

        def on_order_error(self, order_error):
            log("委托失败: {} {}".format(
                order_error.order_remark, order_error.error_msg))

        def on_cancel_error(self, cancel_error):
            log("撤单失败: {}".format(cancel_error))

    with _TRADER_LOCK:
        if _TRADER is not None:
            return _TRADER, _TRADER_ACC

        trader = XtQuantTrader(path, session_id)
        trader.register_callback(_TraderCallback())
        trader.start()

        connect_result = trader.connect()
        if connect_result != 0:
            raise RuntimeError("交易连接失败，错误码: {}".format(connect_result))

        acc = StockAccount(account_id, account_type)
        subscribe_result = trader.subscribe(acc)
        if subscribe_result != 0:
            raise RuntimeError("账号订阅失败，错误码: {}".format(subscribe_result))

        _TRADER = trader
        _TRADER_ACC = acc
        log("交易连接成功: account={} type={}".format(account_id, account_type))

    return _TRADER, _TRADER_ACC


def _from_xtcode(xt_code):
    """600519.SH -> sh600519"""
    if not xt_code:
        return ""
    head, _, market = xt_code.upper().partition(".")
    market = market.lower()
    if market in ("sh", "sz", "bj"):
        return market + head
    return xt_code


_ORDER_STATUS_MAP = {
    48: "pending",    # 委托中
    49: "pending",    # 部分成交
    50: "filled",     # 全部成交
    51: "partial",    # 部分撤单
    52: "cancelled",  # 全部撤单
    53: "rejected",   # 委托失败
    55: "cancelled",  # 部分成交后撤单
}


def _map_order_status(status):
    return _ORDER_STATUS_MAP.get(status, "unknown")


# ----------------------------------------------------------------------
# 交易方法
# ----------------------------------------------------------------------

def handle_trade_connect(params):
    """连接交易服务器，返回账号信息"""
    trader, acc = ensure_trader()
    return {"connected": True, "accountId": acc.account_id}


def handle_trade_order(params):
    """下单"""
    from xtquant import xtconstant

    trader, acc = ensure_trader()

    code = to_xtcode(params.get("symbol", ""))
    if not code:
        raise ValueError("symbol 不能为空")

    side = params.get("side", "buy")
    quantity = int(params.get("quantity", 0))
    if quantity <= 0:
        raise ValueError("quantity 必须大于 0")

    order_type = params.get("orderType", "market")
    price = float(params.get("price", 0) or 0)

    xt_order_type = xtconstant.STOCK_BUY if side == "buy" else xtconstant.STOCK_SELL
    if order_type == "limit":
        xt_price_type = xtconstant.FIX_PRICE
        if price <= 0:
            raise ValueError("限价单 price 必须大于 0")
    else:
        xt_price_type = xtconstant.LATEST_PRICE
        price = -1  # 市价单 price 传 -1

    seq = trader.order_stock(
        acc, code, xt_order_type, quantity, xt_price_type, price,
        "mookquant", "")

    log("下单: {} {} {} qty={} type={} price={}".format(
        side, code, quantity, order_type, price))
    return {"orderId": str(seq), "status": "submitted"}


def handle_trade_cancel(params):
    """撤单"""
    trader, acc = ensure_trader()

    order_id = params.get("orderId", "")
    if not order_id:
        raise ValueError("orderId 不能为空")

    trader.cancel_order_stock(acc, order_id)
    log("撤单: {}".format(order_id))
    return {"success": True}


def handle_trade_positions(params):
    """查持仓"""
    trader, acc = ensure_trader()
    positions = trader.query_stock_positions(acc) or []

    result = []
    for p in positions:
        volume = getattr(p, "volume", 0) or 0
        open_price = getattr(p, "open_price", 0) or 0
        market_value = getattr(p, "market_value", 0) or 0
        profit = getattr(p, "profit", 0) or 0

        current_price = (market_value / volume) if volume else 0
        cost = open_price * volume if open_price else 0
        pnl_pct = ((current_price - open_price) / open_price * 100) if open_price else 0

        result.append({
            "symbol": _from_xtcode(getattr(p, "stock_code", "")),
            "name": getattr(p, "stock_code", ""),
            "quantity": volume,
            "canUseVolume": getattr(p, "can_use_volume", 0) or 0,
            "costPrice": round(open_price, 4),
            "currentPrice": round(current_price, 4),
            "marketValue": round(market_value, 2),
            "pnl": round(profit, 2),
            "pnlPct": round(pnl_pct, 2),
        })

    log("查询持仓: {} 条".format(len(result)))
    return result


def handle_trade_orders(params):
    """查当日委托"""
    from xtquant import xtconstant

    trader, acc = ensure_trader()
    orders = trader.query_stock_orders(acc) or []

    result = []
    for o in orders:
        order_type = getattr(o, "order_type", 0)
        price_type = getattr(o, "price_type", 0)
        side = "buy" if order_type == xtconstant.STOCK_BUY else "sell"

        result.append({
            "orderId": str(getattr(o, "order_id", "")),
            "sysId": str(getattr(o, "order_sysid", "")),
            "symbol": _from_xtcode(getattr(o, "stock_code", "")),
            "side": side,
            "price": round(getattr(o, "price", 0) or 0, 4),
            "quantity": getattr(o, "order_volume", 0) or 0,
            "filledQuantity": getattr(o, "traded_volume", 0) or 0,
            "filledPrice": round(getattr(o, "traded_price", 0) or 0, 4),
            "orderType": "limit" if price_type == xtconstant.FIX_PRICE else "market",
            "status": _map_order_status(getattr(o, "order_status", 0)),
            "statusMsg": getattr(o, "status_msg", ""),
            "createdAt": str(getattr(o, "order_time", "")),
        })

    log("查询委托: {} 条".format(len(result)))
    return result


def handle_trade_account(params):
    """查资金"""
    trader, acc = ensure_trader()
    asset = trader.query_stock_asset(acc)

    if not asset:
        return {"totalAssets": 0, "available": 0, "marketValue": 0, "totalPnl": 0}

    cash = getattr(asset, "cash", 0) or 0
    frozen = getattr(asset, "frozen_cash", 0) or 0
    market_value = getattr(asset, "market_value", 0) or 0
    total_asset = getattr(asset, "total_asset", 0) or 0

    log("查询资金: total={} cash={} market={}".format(total_asset, cash, market_value))
    return {
        "totalAssets": round(total_asset, 2),
        "available": round(cash, 2),
        "frozen": round(frozen, 2),
        "marketValue": round(market_value, 2),
        "totalPnl": 0,
    }


# ----------------------------------------------------------------------
# 调度
# ----------------------------------------------------------------------
# 股票列表管理
# ----------------------------------------------------------------------

def _xtcode_to_ui(code):
    """600519.SH -> sh600519"""
    head, _, market = code.upper().partition(".")
    return market.lower() + head, head, market


def handle_stock_sync(params):
    """从 xtquant 拉取多板块股票列表，存入 SQLite + 导出 JSON 缓存

    同步板块：沪深A股、创业板、科创板、京市A股（北交所）、沪深ETF
    按优先级标记 type：ETF > 北交所 > 科创板 > 创业板 > A股
    使用 get_instrument_detail_list() 批量获取详情
    """
    import json as _json
    import io as _io
    import contextlib as _ctx

    port = int(params.get("port", 58610)) if params else 58610
    ensure_connected(port)
    xt = _XTDATA

    # 1. 按优先级获取各板块代码（高优先级先入，低优先级不覆盖）
    #    创业板/科创板是沪深A股的子集，所以先标记特殊类型
    sectors_priority = [
        ("沪深ETF", "ETF"),
        ("京市A股", "北交所"),
        ("科创板", "科创板"),
        ("创业板", "创业板"),
        ("沪深A股", "A股"),
    ]

    all_codes = {}  # xtcode -> type
    _suppress = _io.StringIO()
    with _ctx.redirect_stdout(_suppress):
        for sector_name, stock_type in sectors_priority:
            codes = xt.get_stock_list_in_sector(sector_name)
            for code in codes:
                if code not in all_codes:
                    all_codes[code] = stock_type
            log("stock.sync: {} -> {} codes".format(sector_name, len(codes)))

    log("stock.sync: total unique codes: {}".format(len(all_codes)))
    if not all_codes:
        return {"count": 0, "error": "未获取到股票列表"}

    # 2. 批量获取详情
    code_list = list(all_codes.keys())
    with _ctx.redirect_stdout(_suppress):
        details = xt.get_instrument_detail_list(code_list)
    log("stock.sync: got {} details via batch API".format(len(details)))

    # 3. 构建股票列表
    stocks = []
    for code in code_list:
        detail = details.get(code) or {}
        name = detail.get("InstrumentName", "")
        if not name:
            continue
        head, _, market = code.upper().partition(".")
        ui_code = market.lower() + head
        stocks.append({
            "code": ui_code,
            "xtcode": code,
            "name": name,
            "exchange": market,
            "type": all_codes[code],
        })

    log("stock.sync: {} valid stocks".format(len(stocks)))

    # 4. 存入数据库
    saved = db_cache.save_stocks(stocks)
    log("stock.sync: saved {} stocks to DB".format(saved))

    # 5. 导出 JSON 缓存（供 mock 模式使用）
    project_root = _os_mod.path.dirname(_os_mod.path.dirname(_os_mod.path.abspath(__file__)))
    cache_path = _os_mod.path.join(project_root, "config", "stocks_cache.json")
    cache_data = [
        {"code": s["code"], "name": s["name"], "exchange": s["exchange"], "type": s["type"]}
        for s in stocks
    ]
    with open(cache_path, "w", encoding="utf-8") as f:
        _json.dump(cache_data, f, ensure_ascii=False)
    log("stock.sync: exported {} stocks to {}".format(len(cache_data), cache_path))

    # 6. 统计
    type_counts = {}
    for s in stocks:
        type_counts[s["type"]] = type_counts.get(s["type"], 0) + 1

    return {
        "count": saved,
        "total": len(stocks),
        "typeCounts": type_counts,
        "stocks": cache_data[:20],
    }


def handle_stock_list(params):
    """从数据库读取全部股票列表"""
    db_cache.init_stocks_table()
    count = db_cache.get_stock_count()
    if count == 0:
        return {"count": 0, "stocks": [], "empty": True}
    stocks = db_cache.get_all_stocks()
    return {"count": len(stocks), "stocks": stocks}


# ----------------------------------------------------------------------
# 策略框架 RPC（回测与实盘共用同一份策略代码）
# ----------------------------------------------------------------------
def handle_strategy_list(params):
    """列出所有已注册策略的元数据（供 UI 渲染参数表单）"""
    return {"strategies": list_strategies()}


def handle_strategy_signal(params):
    """实盘信号计算：给定策略类型 + K线 + 参数，返回最新一根 bar 的信号。

    params:
      type: str        策略类型名（如 ma_cross）
      bars: list       K线序列 [{date,open,high,low,close,volume}, ...]
      params: dict     策略参数
      symbol: str      标的代码（可选）
    """
    strategy_type = params.get("type")
    strategy_params = params.get("params") or {}
    bars = params.get("bars") or []
    symbol = params.get("symbol", "")

    if not strategy_type:
        return {"error": "缺少策略类型 type"}
    if not bars:
        return {"error": "缺少 K 线数据 bars"}

    try:
        strat_cls = get_strategy(strategy_type)
    except KeyError:
        return {"error": "未知策略类型: {}".format(strategy_type)}

    strat = strat_cls(strategy_params)
    ctx = Context()
    ctx.symbol = symbol
    ctx.is_backtest = False
    ctx.period = "1d"
    strat.on_init(ctx)
    strat.on_after_init(ctx)

    # 逐 bar 喂到最后一根，取最后一根的信号（实盘只关心当前 bar）
    signal = None
    for i, bar in enumerate(bars):
        ctx.bars = bars[:i + 1]
        ctx.barpos = i
        signal = strat.on_bar(bar, ctx)
    strat.on_stop(ctx)

    if signal is None:
        return {"action": "hold", "reason": "无信号"}
    return signal.to_dict()


def handle_strategy_export(params):
    """导出策略为指定平台脚本字符串（适配壳包装，策略逻辑原样保留）。

    params:
      type: str        策略类型名
      platform: str    目标平台（默认 qmt）
      params: dict     可选，覆盖默认参数
    """
    strategy_type = params.get("type")
    platform = params.get("platform", "qmt")
    if not strategy_type:
        return {"error": "缺少策略类型 type"}
    try:
        strat_cls = get_strategy(strategy_type)
    except KeyError:
        return {"error": "未知策略类型: {}".format(strategy_type)}

    if platform == "qmt":
        from strategies.exporters.qmt_exporter import QmtExporter
        exporter = QmtExporter()
    else:
        return {"error": "不支持的导出平台: {}".format(platform)}

    try:
        script = exporter.export(strat_cls, params.get("params"))
        return {"script": script, "platform": platform, "name": strategy_type}
    except Exception as e:
        return {"error": "导出失败: {}".format(e)}


def handle_strategy_add(params):
    """添加用户自定义策略（写源码到 user/ 目录并即时注册）。

    params:
      name: str   策略类型名（合法标识符，与代码中类属性 name 一致）
      code: str   策略 Python 源码
    """
    name = (params.get("name") or "").strip()
    code = params.get("code") or ""
    if not name:
        return {"error": "缺少策略名 name"}
    if not code:
        return {"error": "缺少策略代码 code"}
    try:
        metadata = save_strategy(name, code)
        return {"strategy": metadata}
    except Exception as e:
        return {"error": str(e)}


def handle_strategy_delete(params):
    """删除用户策略（仅限 user/ 目录下的）。

    params:
      name: str   策略类型名
    """
    name = (params.get("name") or "").strip()
    if not name:
        return {"error": "缺少策略名 name"}
    try:
        delete_strategy(name)
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


# ----------------------------------------------------------------------
METHOD_MAP = {
    "stock.sync": handle_stock_sync,
    "stock.list": handle_stock_list,
    "ping": handle_ping,
    "quote.snapshot": handle_quote_snapshot,
    "quote.history": handle_quote_history,
    "quote.subscribe": handle_quote_subscribe,
    "trade.connect": handle_trade_connect,
    "trade.order": handle_trade_order,
    "trade.cancel": handle_trade_cancel,
    "trade.positions": handle_trade_positions,
    "trade.orders": handle_trade_orders,
    "trade.account": handle_trade_account,
    "strategy.list": handle_strategy_list,
    "strategy.signal": handle_strategy_signal,
    "strategy.export": handle_strategy_export,
    "strategy.add": handle_strategy_add,
    "strategy.delete": handle_strategy_delete,
}


def main():
    log("启动，监听 stdin ...")
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            reply({"error": "JSON 解析失败: {}".format(e)})
            continue
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        fn = METHOD_MAP.get(method)
        if not fn:
            reply({"id": req_id, "error": "未知方法: {}".format(method)})
            continue
        try:
            result = fn(params)
            reply({"id": req_id, "result": result})
        except Exception as e:
            log("handler {} crashed: {}".format(method, e))
            reply({"id": req_id, "error": str(e)})


if __name__ == "__main__":
    main()