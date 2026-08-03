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
# 策略框架（回测与实盘共用同一份策略代码）
from strategies.registry import load_all, get as get_strategy
from strategies.base import Context
import db as db_cache
from _shared import to_xtcode as _to_xtcode, generate_mock_bars


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
# 技术指标与策略实现已迁移至 strategies 包（base/indicators/builtin），
# 回测与实盘共用同一份策略代码，详见 bridge/strategies/


def _merge_bars(bars):
    """合并多根 bar 为一根 OHLCV bar。"""
    if not bars:
        return None
    merged = {
        "date": bars[0]["date"],
        "open": bars[0]["open"],
        "high": max(b["high"] for b in bars),
        "low": min(b["low"] for b in bars),
        "close": bars[-1]["close"],
        "volume": sum(b["volume"] for b in bars),
    }
    if "time" in bars[0]:
        merged["time"] = bars[0]["time"]
    if "amount" in bars[0]:
        merged["amount"] = sum(b.get("amount", 0) for b in bars)
    return merged


def _aggregate_weekly(bars):
    """日线聚合成周线（按 ISO 周）。"""
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
    """日线聚合成月线（按 YYYY-MM）。"""
    result = []
    current_key = None
    current_group = []
    for bar in bars:
        try:
            month_key = bar["date"][:7]
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


def _aggregate_bars(bars, target_period):
    """将基础周期 bar 聚合为目标周期（周线/月线从日线聚合）。"""
    if not bars:
        return []
    if target_period == "1w":
        return _aggregate_weekly(bars)
    elif target_period == "1mon":
        return _aggregate_monthly(bars)
    return bars


def fetch_real_bars(symbol, start_date, end_date, dividend_type="front", period="1d"):
    """\u4ece\u6570\u636e\u5e93\u7f13\u5b58\u6216 xtquant \u83b7\u53d6\u771f\u5b9e\u5386\u53f2\u65e5K\u7ebf\u6570\u636e\uff08\u524d\u590d\u6743\uff09

    Returns: (bars, error_msg)  bars=None \u65f6 error_msg \u6709\u503c
    """
    xt_code = _to_xtcode(symbol)
    if not xt_code:
        return None, "\u65e0\u6548\u7684\u80a1\u7968\u4ee3\u7801"

    # 周线/月线：xtquant 不直接支持，先取日线再由调用方聚合
    fetch_period = "1d" if period in ("1w", "1mon") else period

    # 1. \u5148\u67e5\u6570\u636e\u5e93\u7f13\u5b58
    cached = db_cache.query_bars_by_date(xt_code, fetch_period, start_date, end_date, dividend_type)
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
            xt_code, period=fetch_period,
            start_time=start_time, end_time=end_time,
            incrementally=True,
        )

        data = xtdata.get_market_data_ex(
            [], [xt_code], period=fetch_period,
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
            saved = db_cache.save_bars(xt_code, fetch_period, bars, dividend_type)
            log("\u4fdd\u5b58 {} \u6761\u5230\u6570\u636e\u5e93".format(saved))

        return bars, None
    except Exception as e:
        if cached:
            log("xtquant \u83b7\u53d6\u5931\u8d25\uff0c\u4f7f\u7528\u90e8\u5206\u6570\u636e\u5e93\u7f13\u5b58: {} \u6761".format(len(cached)))
            return cached, None
        return None, "\u83b7\u53d6\u6570\u636e\u5931\u8d25: {}".format(e)



# ----------------------------------------------------------------------
# 回测引擎
# ----------------------------------------------------------------------
def _fetch_stock_name(symbol):
    """Fetch stock display name via xtquant; fall back to the code itself on failure."""
    xt_code = _to_xtcode(symbol)
    custom_path = os.environ.get("XTQUANT_PATH", "")
    if custom_path and custom_path not in sys.path:
        sys.path.insert(0, custom_path)
    try:
        from xtquant import xtdata
        try:
            port = int(os.environ.get("QMT_PORT", "58610"))
            xtdata.connect(port=port)
        except Exception:
            pass
        info = xtdata.get_instrument_detail(xt_code) or {}
        name = info.get("InstrumentName", "")
        return name if name else symbol
    except Exception:
        return symbol


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
    stamp_tax = float(params.get("stampTax", 0.0005))        # M2: 印花税（卖出收取）
    transfer_fee = float(params.get("transferFee", 0.00001))  # M2: 过户费（双向收取）
    buy_cost_rate = commission_rate + transfer_fee
    sell_cost_rate = commission_rate + stamp_tax + transfer_fee
    dividend_type = params.get("dividendType", "front")
    period = params.get("period", "1d")

    if len(symbols) > 1:
        raise ValueError("当前仅支持单标的回测，多标的请使用 PortfolioSignal 组合策略")
    symbol = symbols[0]
    stock_name = _fetch_stock_name(symbol)

    # 优先使用 xtquant 真实数据，失败时回退到模拟数据
    bars, fetch_err = fetch_real_bars(symbol, start_date, end_date, dividend_type, period)
    if bars and len(bars) > 0:
        log("回测使用真实数据: {} 条".format(len(bars)))
        data_source = "real"
    else:
        log("回测使用模拟数据: {}".format(fetch_err))
        bars = generate_mock_bars(symbol, start_date, end_date)
        data_source = "mock"


    if len(bars) < 5:
        raise ValueError("回测数据不足，请扩大时间范围")

    # 生成信号（策略框架：registry 加载 + 逐 bar 调 on_bar，回测与实盘共用同一逻辑）
    load_all()
    try:
        strat_cls = get_strategy(strategy_type)
    except KeyError:
        log("未知策略类型 {}，回落 ma_cross".format(strategy_type))
        strat_cls = get_strategy("ma_cross")
    strat = strat_cls(strategy_params)

    ctx = Context()
    ctx.symbol = symbol
    ctx.is_backtest = True
    ctx.period = "1d"
    strat.on_init(ctx)
    strat.on_after_init(ctx)

    signal_map = {}  # date -> Signal
    for i, bar in enumerate(bars):
        ctx.bars = bars[:i + 1]
        ctx.barpos = i
        sig = strat.on_bar(bar, ctx)
        if sig is not None and sig.action in ("buy", "sell", "hold"):
            signal_map[bar["date"]] = sig
    strat.on_stop(ctx)
    log("生成信号 {} 个（策略 {}）".format(len(signal_map), strategy_type))

    # 模拟交易 + 逐日计算净值（在同一个循环中完成）
    cash = initial_capital
    position = 0  # 持仓股数
    cost_price = 0.0
    trades = []
    equity_curve = []

    for bar in bars:
        date_str = bar["date"]
        sig = signal_map.get(date_str)
        action = sig.action if sig else None
        target_pos = sig.target_position if sig else None

        # 目标仓位调仓（支持 target_position 信号；内置策略不触发，走下方 buy/sell）
        if target_pos is not None:
            total_asset = cash + position * bar["close"]
            target_value = total_asset * target_pos
            target_qty = int(target_value / bar["close"] / 100) * 100
            if target_qty > position:
                delta = target_qty - position
                price = bar["close"] * (1 + slippage_rate)
                cost = price * delta * (1 + buy_cost_rate)
                if cost <= cash and delta > 0:
                    cash -= cost
                    if position == 0:
                        cost_price = price
                    else:
                        cost_price = (cost_price * position + price * delta) / target_qty
                    position = target_qty
                    trades.append({"date": date_str, "side": "buy", "symbol": symbol,
                                   "price": round(price, 2), "quantity": delta,
                                   "amount": round(cost, 2), "pnl": None})
            elif target_qty < position:
                delta = position - target_qty
                price = bar["close"] * (1 - slippage_rate)
                proceeds = price * delta * (1 - sell_cost_rate)
                pnl = proceeds - cost_price * delta
                cash += proceeds
                position = target_qty
                if position == 0:
                    cost_price = 0.0
                trades.append({"date": date_str, "side": "sell", "symbol": symbol,
                               "price": round(price, 2), "quantity": delta,
                               "amount": round(proceeds, 2), "pnl": round(pnl, 2)})

        # 执行交易信号（buy/sell，与改造前撮合逻辑完全一致）
        elif action == "buy" and position == 0:
            price = bar["close"] * (1 + slippage_rate)
            max_qty = int(cash / (price * (1 + buy_cost_rate)) / 100) * 100
            if max_qty > 0:
                cost = price * max_qty * (1 + buy_cost_rate)
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
            proceeds = price * position * (1 - sell_cost_rate)
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
        log("warning: max_dd {:.2f}% is abnormal (should be >= -100%), check data integrity".format(max_dd))

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
        "name": stock_name,
        "symbol": symbol,
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
