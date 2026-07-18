"""
mookquant · 回测引擎（Python）

协议：stdio JSON-RPC（每行一条 JSON）

请求示例：
  {"id":"1","method":"backtest.run","params":{
    "strategy": {"type":"ma_cross","params":{"fast":5,"slow":20}},
    "symbols": ["sh600519"],
    "startDate":"2024-01-01","endDate":"2024-06-30",
    "initialCapital":1000000,"commission":0.0003,"slippage":0.001
  }}

响应：
  {"id":"1","result":{"metrics":{...},"trades":[...],"equityCurve":[...]}}
"""
import json
import sys
import os
import time
import random
import math

# SQLite cache module (same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as db_cache


# ----------------------------------------------------------------------
# 编码修复
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
    sys.stderr.write("[backtest] {}\n".format(msg))
    sys.stderr.flush()


def reply(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


# ----------------------------------------------------------------------
# 模拟历史数据生成（无 xtquant 时的回退方案）
# 用确定性随机游走生成日 K 线，确保回测可运行
# ----------------------------------------------------------------------
def generate_mock_bars(symbol, start_date, end_date):
    """生成模拟日 K 线数据"""
    from datetime import datetime, timedelta

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    # 用 symbol 做种子，保证同标的每次生成一致
    seed_val = sum(ord(c) for c in symbol) + 42
    rng = random.Random(seed_val)

    base_price = 50 + rng.random() * 200
    bars = []
    current = start
    price = base_price

    while current <= end:
        # 跳过周末
        if current.weekday() < 5:
            daily_return = rng.gauss(0, 0.02)  # 日波动 2%
            open_price = price
            close_price = price * (1 + daily_return)
            high_price = max(open_price, close_price) * (1 + abs(rng.gauss(0, 0.008)))
            low_price = min(open_price, close_price) * (1 - abs(rng.gauss(0, 0.008)))
            volume = int(rng.uniform(500000, 5000000))

            bars.append({
                "date": current.strftime("%Y-%m-%d"),
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
                "volume": volume,
            })
            price = close_price
        current += timedelta(days=1)

    return bars


# ----------------------------------------------------------------------
# 技术指标
# ----------------------------------------------------------------------
def sma(closes, period):
    """简单移动平均"""
    result = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            window = closes[i - period + 1: i + 1]
            result.append(sum(window) / period)
    return result


# ----------------------------------------------------------------------
# 策略实现
# ----------------------------------------------------------------------
def strategy_ma_cross(bars, params):
    """双均线策略：快线上穿慢线买入，下穿卖出"""
    fast = int(params.get("fast", 5))
    slow = int(params.get("slow", 20))
    closes = [b["close"] for b in bars]

    ma_fast = sma(closes, fast)
    ma_slow = sma(closes, slow)

    signals = []  # (date, action)  action: "buy" | "sell"
    for i in range(1, len(bars)):
        if ma_fast[i] is None or ma_slow[i] is None:
            continue
        if ma_fast[i - 1] is None or ma_slow[i - 1] is None:
            continue
        # 金叉
        if ma_fast[i - 1] <= ma_slow[i - 1] and ma_fast[i] > ma_slow[i]:
            signals.append((bars[i]["date"], "buy"))
        # 死叉
        elif ma_fast[i - 1] >= ma_slow[i - 1] and ma_fast[i] < ma_slow[i]:
            signals.append((bars[i]["date"], "sell"))

    return signals


def strategy_momentum(bars, params):
    """动量策略：N 日涨幅超过阈值买入，低于阈值卖出"""
    lookback = int(params.get("lookback", 10))
    threshold = float(params.get("threshold", 0.03))
    closes = [b["close"] for b in bars]

    signals = []
    for i in range(lookback, len(bars)):
        ret = (closes[i] - closes[i - lookback]) / closes[i - lookback]
        if ret > threshold:
            signals.append((bars[i]["date"], "buy"))
        elif ret < -threshold:
            signals.append((bars[i]["date"], "sell"))

    return signals


def strategy_mean_reversion(bars, params):
    """均值回归策略：价格偏离均线超过阈值时反向操作"""
    period = int(params.get("period", 20))
    deviation = float(params.get("deviation", 0.03))
    closes = [b["close"] for b in bars]
    ma = sma(closes, period)

    signals = []
    for i in range(period, len(bars)):
        if ma[i] is None:
            continue
        diff = (closes[i] - ma[i]) / ma[i]
        if diff < -deviation:
            signals.append((bars[i]["date"], "buy"))   # 跌破均值，买入
        elif diff > deviation:
            signals.append((bars[i]["date"], "sell"))  # 超过均值，卖出

    return signals


def _to_xtcode(raw):
    """将 UI 代码转换为 xtquant 标准格式: sh600036 -> 600036.SH"""
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
    return s.upper()


def fetch_real_bars(symbol, start_date, end_date, dividend_type="front"):
    """\u4ece\u6570\u636e\u5e93\u7f13\u5b58\u6216 xtquant \u83b7\u53d6\u771f\u5b9e\u5386\u53f2\u65e5K\u7ebf\u6570\u636e\uff08\u524d\u590d\u6743\uff09

    Returns: (bars, error_msg)  bars=None \u65f6 error_msg \u6709\u503c
    """
    xt_code = _to_xtcode(symbol)
    if not xt_code:
        return None, "\u65e0\u6548\u7684\u80a1\u7968\u4ee3\u7801"

    # dividend_type passed from caller

    # 1. \u5148\u67e5\u6570\u636e\u5e93\u7f13\u5b58
    cached = db_cache.query_bars_by_date(xt_code, "1d", start_date, end_date, dividend_type)
    if cached and len(cached) > 0:
        db_start = cached[0]["date"]
        db_end = cached[-1]["date"]
        if db_start <= start_date and db_end >= end_date:
            log("\u56de\u6d4b\u4f7f\u7528\u6570\u636e\u5e93\u7f13\u5b58: {} \u6761".format(len(cached)))
            return cached, None

    # 2. \u7f13\u5b58\u4e0d\u591f\uff0c\u4ece xtquant \u83b7\u53d6
    custom_path = os.environ.get("XTQUANT_PATH", "")
    if custom_path and custom_path not in sys.path:
        sys.path.insert(0, custom_path)

    try:
        from xtquant import xtdata
    except ImportError:
        if cached:
            log("xtquant \u4e0d\u53ef\u7528\uff0c\u4f7f\u7528\u90e8\u5206\u6570\u636e\u5e93\u7f13\u5b58: {} \u6761".format(len(cached)))
            return cached, None
        return None, "xtquant \u4e0d\u53ef\u7528\uff0c\u4f7f\u7528\u6a21\u62df\u6570\u636e"

    port = int(os.environ.get("QMT_PORT", "58610"))

    try:
        xtdata.connect(port=port)
    except Exception as e:
        if cached:
            log("\u8fde\u63a5 miniQMT \u5931\u8d25\uff0c\u4f7f\u7528\u90e8\u5206\u6570\u636e\u5e93\u7f13\u5b58: {} \u6761".format(len(cached)))
            return cached, None
        return None, "\u8fde\u63a5 miniQMT \u5931\u8d25: {}".format(e)

    try:
        start_time = start_date.replace("-", "")
        end_time = end_date.replace("-", "")

        xtdata.download_history_data(
            xt_code, period="1d",
            start_time=start_time, end_time=end_time,
            incrementally=True,
        )

        data = xtdata.get_market_data_ex(
            [], [xt_code], period="1d",
            start_time=start_time, end_time=end_time,
            dividend_type=dividend_type,
        ) or {}

        df = data.get(xt_code)
        if df is None or len(df) == 0:
            if cached:
                return cached, None
            return None, "\u65e0\u5386\u53f2\u6570\u636e"

        bars = []
        for idx, row in df.iterrows():
            date_str = str(idx)
            if len(date_str) == 8:
                date_fmt = date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:8]
            else:
                date_fmt = date_str
            bars.append({
                "date": date_fmt,
                "open": float(row.get("open", 0) or 0),
                "high": float(row.get("high", 0) or 0),
                "low": float(row.get("low", 0) or 0),
                "close": float(row.get("close", 0) or 0),
                "volume": float(row.get("volume", 0) or 0) * 100,
            })

        # 3. \u4fdd\u5b58\u5230\u6570\u636e\u5e93
        if bars:
            saved = db_cache.save_bars(xt_code, "1d", bars, dividend_type)
            log("\u4fdd\u5b58 {} \u6761\u5230\u6570\u636e\u5e93".format(saved))

        return bars, None
    except Exception as e:
        if cached:
            log("xtquant \u83b7\u53d6\u5931\u8d25\uff0c\u4f7f\u7528\u90e8\u5206\u6570\u636e\u5e93\u7f13\u5b58: {} \u6761".format(len(cached)))
            return cached, None
        return None, "\u83b7\u53d6\u6570\u636e\u5931\u8d25: {}".format(e)

STRATEGY_MAP = {
    "ma_cross": strategy_ma_cross,
    "momentum": strategy_momentum,
    "mean_reversion": strategy_mean_reversion,
}


# ----------------------------------------------------------------------
# 回测引擎
# ----------------------------------------------------------------------
def run_backtest(params):
    """
    执行回测

    params:
      strategy: dict  {type, params}
      symbols: list    回测标的（与策略解耦，回测时指定）
      startDate: str
      endDate: str
      initialCapital: float
      commission: float   手续费率
      slippage: float     滑点比例
    """
    strategy_cfg = params.get("strategy", {})
    strategy_type = strategy_cfg.get("type", "ma_cross")
    strategy_params = strategy_cfg.get("params", {})
    symbols = params.get("symbols", []) or strategy_cfg.get("symbols", [])

    if not symbols:
        raise ValueError("策略标的不能为空")

    start_date = params.get("startDate", "2024-01-01")
    end_date = params.get("endDate", "2024-06-30")
    initial_capital = float(params.get("initialCapital", 1000000))
    commission_rate = float(params.get("commission", 0.0003))
    slippage_rate = float(params.get("slippage", 0.001))
    dividend_type = params.get("dividendType", "front")

    symbol = symbols[0]  # 当前支持单标的

    # 优先使用 xtquant 真实数据，失败时回退到模拟数据
    bars, fetch_err = fetch_real_bars(symbol, start_date, end_date, dividend_type)
    if bars and len(bars) > 0:
        log("回测使用真实数据: {} 条".format(len(bars)))
        data_source = "real"
    else:
        log("回测使用模拟数据: {}".format(fetch_err))
        bars = generate_mock_bars(symbol, start_date, end_date)
        data_source = "mock"

    if len(bars) < 5:
        raise ValueError("回测数据不足，请扩大时间范围")

    # 生成信号
    strategy_fn = STRATEGY_MAP.get(strategy_type)
    if strategy_fn is None:
        # custom 类型也走 ma_cross
        strategy_fn = strategy_ma_cross

    signals = strategy_fn(bars, strategy_params)

    # 模拟交易 + 逐日计算净值（在同一个循环中完成）
    cash = initial_capital
    position = 0  # 持仓股数
    cost_price = 0.0
    trades = []
    equity_curve = []

    # 构建日期到信号的索引
    signal_map = {}
    for date_str, action in signals:
        signal_map[date_str] = action

    for bar in bars:
        date_str = bar["date"]
        action = signal_map.get(date_str)

        # 执行交易信号
        if action == "buy" and position == 0:
            price = bar["close"] * (1 + slippage_rate)
            max_qty = int(cash / (price * (1 + commission_rate)) / 100) * 100
            if max_qty > 0:
                cost = price * max_qty * (1 + commission_rate)
                cash -= cost
                position = max_qty
                cost_price = price
                trades.append({
                    "date": date_str,
                    "side": "buy",
                    "symbol": symbol,
                    "price": round(price, 2),
                    "quantity": max_qty,
                    "amount": round(cost, 2),
                    "pnl": None,
                })

        elif action == "sell" and position > 0:
            price = bar["close"] * (1 - slippage_rate)
            proceeds = price * position * (1 - commission_rate)
            pnl = proceeds - position * cost_price
            cash += proceeds
            trades.append({
                "date": date_str,
                "side": "sell",
                "symbol": symbol,
                "price": round(price, 2),
                "quantity": position,
                "amount": round(proceeds, 2),
                "pnl": round(pnl, 2),
            })
            position = 0
            cost_price = 0.0

        # 计算当日净值（基于当天的实际持仓状态）
        mv = position * bar["close"] if position > 0 else 0
        total = cash + mv
        equity_curve.append({
            "date": date_str,
            "value": round(total, 2),
        })

    # 最终估值
    final_bar = bars[-1]
    final_price = final_bar["close"]
    final_value = cash + (position * final_price if position > 0 else 0)

    # 绩效指标
    total_return = (final_value - initial_capital) / initial_capital * 100

    # 年化收益率
    days = len(bars)
    annual_return = ((final_value / initial_capital) ** (252 / max(days, 1)) - 1) * 100 if days > 0 else 0

    # 最大回撤
    peak = 0
    max_dd = 0
    for point in equity_curve:
        v = point["value"]
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak * 100
            if dd < max_dd:
                max_dd = dd
    if max_dd < -100:
        max_dd = -100

    # 夏普比率（简化：用日收益率）
    daily_returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]["value"]
        curr = equity_curve[i]["value"]
        if prev > 0:
            daily_returns.append((curr - prev) / prev)

    if daily_returns:
        avg_ret = sum(daily_returns) / len(daily_returns)
        std_ret = math.sqrt(sum((r - avg_ret) ** 2 for r in daily_returns) / len(daily_returns))
        sharpe = (avg_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0
    else:
        sharpe = 0

    # 胜率、盈亏比
    sell_trades = [t for t in trades if t["side"] == "sell" and t.get("pnl") is not None]
    wins = [t for t in sell_trades if t["pnl"] > 0]
    losses = [t for t in sell_trades if t["pnl"] <= 0]
    win_rate = (len(wins) / len(sell_trades) * 100) if sell_trades else 0
    avg_win = (sum(t["pnl"] for t in wins) / len(wins)) if wins else 0
    avg_loss = (abs(sum(t["pnl"] for t in losses) / len(losses))) if losses else 1
    profit_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0

    # 区间涨幅（买入持有基准）
    first_price = bars[0]["close"]
    last_price = bars[-1]["close"]
    benchmark_return = (last_price - first_price) / first_price * 100 if first_price > 0 else 0
    excess_return = total_return - benchmark_return

    metrics = {
        "totalReturn": round(total_return, 2),
        "benchmarkReturn": round(benchmark_return, 2),
        "excessReturn": round(excess_return, 2),
        "annualReturn": round(annual_return, 2),
        "maxDrawdown": round(max_dd, 2),
        "sharpeRatio": round(sharpe, 2),
        "winRate": round(win_rate, 2),
        "profitLossRatio": round(profit_loss_ratio, 2),
        "totalTrades": len(trades),
        "finalCapital": round(final_value, 2),
    }

    return {
        "metrics": metrics,
        "trades": trades,
        "equityCurve": equity_curve,
        "dataSource": data_source,
        "bars": [{"date": b["date"], "open": b["open"], "high": b["high"],
                   "low": b["low"], "close": b["close"], "volume": b.get("volume", 0)} for b in bars],
    }


# ----------------------------------------------------------------------
# 调度
# ----------------------------------------------------------------------
METHOD_MAP = {
    "ping": lambda p: {"pong": True},
    "backtest.run": run_backtest,
}


def main():
    log("回测引擎启动，监听 stdin ...")
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
