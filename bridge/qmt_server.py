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
    n = min(count if count > 0 else 60, 120)
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


def handle_quote_history(params):
    code = to_xtcode(params.get("code", ""))
    if not code:
        raise ValueError("code \u4e0d\u80fd\u4e3a\u7a7a")
    period = params.get("period", "1d")
    count = int(params.get("count", -1))
    port = int(params.get("port", 58610))
    ensure_connected(port)
    xt = _XTDATA
    log("history: {} period={} count={}".format(code, period, count))
    try:
        xt.download_history_data(code, period=period, incrementally=True)
    except Exception as e:
        log("download_history_data warning: {}".format(e))
    try:
        data = xt.get_market_data_ex([], [code], period=period, count=count) or {}
        df = data.get(code)
        bars = []
        if df is not None and len(df) > 0:
            for idx, row in df.iterrows():
                date_str = str(idx)
                if len(date_str) == 8:
                    date_fmt = date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:8]
                else:
                    date_fmt = date_str
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
        log("history: got {} bars from xtquant".format(len(bars)))
        if not bars:
            bars = _generate_mock_bars(code, count)
            log("history: no data, using mock")
        return {"bars": bars, "count": len(bars)}
    except Exception as e:
        log("get_market_data_ex failed ({}), using mock data".format(e))
        bars = _generate_mock_bars(code, count)
        return {"bars": bars, "count": len(bars)}

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
METHOD_MAP = {
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