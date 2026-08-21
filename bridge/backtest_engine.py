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
    """从统一数据访问层获取真实历史K线（前复权，volume=股）。

    Returns: (bars, source)  bars 为空时 source 说明原因
    """
    from data import datafeed
    bars, source = datafeed.fetch_bars(symbol, period=period,
                                       start_date=start_date, end_date=end_date,
                                       dividend_type=dividend_type)
    if bars:
        log("回测取数来源: {} ({} 条)".format(source, len(bars)))
        return bars, None
    return None, "真实数据不可用（xtquant 未连接或无该标的数据）"



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
    load_all()  # 含 ML 策略（registry 统一扫描 strategies/ml/builtin）

    # 壳策略：无自身逻辑，按绑定模型（或激活模型）解析出具体 ML 策略
    if strategy_type == 'shell':
        from strategies.ml.base import ARCH_STRATEGY_MAP, MLStrategyBase
        from training.model_registry import ModelRegistry
        model_id = strategy_params.get('model_id', '')
        if not model_id:
            model_id = MLStrategyBase._get_active_model_id()
        if not model_id:
            raise ValueError('壳策略需要绑定模型或激活模型才能回测')
        try:
            meta = ModelRegistry.get_meta(model_id)
        except FileNotFoundError:
            raise ValueError('模型不存在: ' + model_id)
        strategy_type = ARCH_STRATEGY_MAP.get(meta.get('arch', ''), 'lstm_trend')
        strategy_params['model_id'] = model_id

    try:
        strat_cls = get_strategy(strategy_type)
    except KeyError:
        raise ValueError('未知策略类型: ' + strategy_type)
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
    # 撮合规则（v2 商业化升级）：
    #   - T+1：当日买入的股票次日才可卖出（A股制度）
    #   - 涨跌停：收盘涨停不得买入、收盘跌停不得卖出（按板块幅度判定）
    #   - tick 取整：成交价按 0.01 取整；数量按 100 股整手
    #   - 最小佣金：单笔佣金不足 5 元按 5 元计
    #   - 成交量约束：买入量不超过当日成交量的一定比例（默认 25%）
    from data import datafeed as _df
    xt_code = _to_xtcode(symbol)
    cash = initial_capital
    position = 0  # 持仓股数
    avail_position = 0  # T+1 可卖持仓（当日买入的部分次日解禁）
    cost_price = 0.0
    trades = []
    equity_curve = []
    skipped_signals = []  # 因涨跌停/T+1/资金不足被跳过的信号（UI 可展示）

    TICK = 0.01
    MIN_COMMISSION = 5.0
    MAX_VOL_RATIO = float(params.get("maxVolumeRatio", 0.25))
    enable_t1 = bool(params.get("enableT1", True))

    def _round_tick(p):
        return round(round(p / TICK) * TICK, 2)

    def _commission_of(amount):
        c = amount * commission_rate
        return max(c, MIN_COMMISSION) if amount > 0 else 0.0

    prev_close = None
    for bar in bars:
        date_str = bar["date"]
        sig = signal_map.get(date_str)
        action = sig.action if sig else None
        target_pos = sig.target_position if sig else None
        close = bar["close"]
        limit_up = _df.is_limit_up(xt_code, close, prev_close)
        limit_down = _df.is_limit_down(xt_code, close, prev_close)
        # T+1 解禁：新的一天，昨日买入的持仓今日可卖
        if enable_t1:
            avail_position = position

        # --- 目标仓位调仓（支持 target_position 信号）---
        if target_pos is not None:
            total_asset = cash + position * close
            target_value = total_asset * target_pos
            target_qty = int(target_value / close / 100) * 100
            if target_qty > position:
                delta = target_qty - position
                price = _round_tick(close * (1 + slippage_rate))
                if limit_up:
                    skipped_signals.append({"date": date_str, "side": "buy", "reason": "涨停无法买入"})
                else:
                    # 成交量约束
                    max_by_vol = int(bar.get("volume", 0) * MAX_VOL_RATIO / 100) * 100
                    delta = min(delta, max_by_vol) if max_by_vol > 0 else delta
                    amount = price * delta
                    commission = _commission_of(amount)
                    transfer = amount * transfer_fee
                    cost = amount + commission + transfer
                    if cost <= cash and delta > 0:
                        cash -= cost
                        if position == 0:
                            cost_price = price
                        else:
                            cost_price = (cost_price * position + price * delta) / target_qty
                        position = target_qty
                        if not enable_t1:
                            avail_position = position
                        trades.append({"date": date_str, "side": "buy", "symbol": symbol,
                                       "price": price, "quantity": delta,
                                       "amount": round(cost, 2), "pnl": None})
                    elif delta > 0:
                        skipped_signals.append({"date": date_str, "side": "buy", "reason": "资金不足"})
            elif target_qty < position:
                delta = position - target_qty
                sellable = avail_position
                if limit_down or (enable_t1 and sellable <= 0):
                    reason = "跌停无法卖出" if limit_down else "T+1限制，当日买入不可卖"
                    skipped_signals.append({"date": date_str, "side": "sell", "reason": reason})
                else:
                    delta = min(delta, sellable)
                    price = _round_tick(close * (1 - slippage_rate))
                    amount = price * delta
                    commission = _commission_of(amount)
                    proceeds = amount - commission - amount * (stamp_tax + transfer_fee)
                    pnl = proceeds - cost_price * delta
                    cash += proceeds
                    position -= delta
                    avail_position = min(avail_position, position)
                    if position == 0:
                        cost_price = 0.0
                    trades.append({"date": date_str, "side": "sell", "symbol": symbol,
                                   "price": price, "quantity": delta,
                                   "amount": round(proceeds, 2), "pnl": round(pnl, 2)})

        # --- 执行交易信号（buy/sell）---
        elif action == "buy" and position == 0:
            price = _round_tick(close * (1 + slippage_rate))
            if limit_up:
                skipped_signals.append({"date": date_str, "side": "buy", "reason": "涨停无法买入"})
            else:
                max_qty = int(cash / (price * (1 + buy_cost_rate)) / 100) * 100
                # 成交量约束（最小佣金已含在 buy_cost 近似里，此处再精确扣除）
                max_by_vol = int(bar.get("volume", 0) * MAX_VOL_RATIO / 100) * 100
                if max_by_vol > 0:
                    max_qty = min(max_qty, max_by_vol)
                if max_qty > 0:
                    amount = price * max_qty
                    commission = _commission_of(amount)
                    cost = amount + commission + amount * transfer_fee
                    # 最小佣金可能使成本超出资金，回退一手
                    while max_qty > 0 and cost > cash:
                        max_qty -= 100
                        amount = price * max_qty
                        commission = _commission_of(amount)
                        cost = amount + commission + amount * transfer_fee
                    if max_qty > 0:
                        cash -= cost
                        position = max_qty
                        if not enable_t1:
                            avail_position = position
                        cost_price = price
                        trades.append({
                            "date": date_str,
                            "side": "buy",
                            "symbol": symbol,
                            "price": price,
                            "quantity": max_qty,
                            "amount": round(cost, 2),
                            "pnl": None,
                        })

        elif action == "sell" and position > 0:
            if limit_down:
                skipped_signals.append({"date": date_str, "side": "sell", "reason": "跌停无法卖出"})
            elif enable_t1 and avail_position <= 0:
                skipped_signals.append({"date": date_str, "side": "sell", "reason": "T+1限制，当日买入不可卖"})
            else:
                qty = position if not enable_t1 else min(position, avail_position)
                price = _round_tick(close * (1 - slippage_rate))
                amount = price * qty
                commission = _commission_of(amount)
                proceeds = amount - commission - amount * (stamp_tax + transfer_fee)
                pnl = proceeds - qty * cost_price
                cash += proceeds
                position -= qty
                avail_position = min(avail_position, position)
                trades.append({
                    "date": date_str,
                    "side": "sell",
                    "symbol": symbol,
                    "price": price,
                    "quantity": qty,
                    "amount": round(proceeds, 2),
                    "pnl": round(pnl, 2),
                })
                if position == 0:
                    cost_price = 0.0

        # 计算当日净值（基于当天的实际持仓状态）
        mv = position * close if position > 0 else 0
        total = cash + mv
        equity_curve.append({
            "date": date_str,
            "value": round(total, 2),
        })
        prev_close = close

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
        avg_ret, std_ret = 0.0, 0.0

    # 年化波动率 & 索提诺比率（下行波动）
    annual_vol = std_ret * math.sqrt(252) * 100 if daily_returns else 0
    downside = [r for r in daily_returns if r < 0]
    downside_std = math.sqrt(sum(r * r for r in downside) / len(downside)) if downside else 0
    sortino = (avg_ret / downside_std * math.sqrt(252)) if downside_std > 0 else 0

    # 卡玛比率（年化收益 / |最大回撤|）
    calmar = (annual_return / abs(max_dd)) if max_dd < 0 else 0

    # 平均持仓天数与最大连续亏损
    hold_days_list = []
    open_buy = None
    for t in trades:
        if t["side"] == "buy":
            open_buy = t["date"]
        elif t["side"] == "sell" and open_buy:
            try:
                from datetime import datetime as _dt
                d0 = _dt.strptime(str(open_buy)[:10], "%Y-%m-%d")
                d1 = _dt.strptime(str(t["date"])[:10], "%Y-%m-%d")
                hold_days_list.append((d1 - d0).days)
            except ValueError:
                pass
            open_buy = None
    avg_hold_days = round(sum(hold_days_list) / len(hold_days_list), 1) if hold_days_list else 0

    # 月度收益序列（热力图数据）
    monthly_returns = []
    if len(equity_curve) > 1:
        month_key = None
        month_start_val = None
        last_val = None
        for pt in equity_curve:
            k = str(pt["date"])[:7]
            if k != month_key:
                if month_key is not None and month_start_val is not None and month_start_val > 0:
                    monthly_returns.append({"month": month_key,
                                            "return": round((last_val - month_start_val) / month_start_val * 100, 2)})
                month_key = k
                month_start_val = pt["value"]
            last_val = pt["value"]
        if month_key is not None and month_start_val is not None and month_start_val > 0:
            monthly_returns.append({"month": month_key,
                                    "return": round((last_val - month_start_val) / month_start_val * 100, 2)})

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
        "sortinoRatio": round(sortino, 2),
        "calmarRatio": round(calmar, 2),
        "annualVolatility": round(annual_vol, 2),
        "avgHoldDays": avg_hold_days,
        "winRate": round(win_rate, 2),
        "profitLossRatio": round(profit_loss_ratio, 2),
        "totalTrades": len(trades),
        "skippedSignals": len(skipped_signals),
        "finalCapital": round(final_value, 2),
    }

    result = {
        "metrics": metrics,
        "trades": trades,
        "equityCurve": equity_curve,
        "monthlyReturns": monthly_returns,
        "skippedSignals": skipped_signals[:100],  # 截断防响应过大
        "dataSource": data_source,
        "name": stock_name,
        "symbol": symbol,
        "bars": [{"date": b["date"], "open": b["open"], "high": b["high"],
                   "low": b["low"], "close": b["close"], "volume": b.get("volume", 0)} for b in bars],
    }

    # 样本内回测检测：回测区间与模型训练区间重叠时显著警告
    try:
        overlap_warning = None
        model_id_for_check = strategy_params.get('model_id', '')
        if model_id_for_check:
            import training.model_registry as _tmr
            _mc = os.path.join(_tmr.MODEL_DIR, model_id_for_check, 'config.json')
            if os.path.exists(_mc):
                with open(_mc, encoding='utf-8') as f:
                    _cfg = json.load(f)
                dr = _cfg.get('data_date_range') or {}
                ts, te = dr.get('start', ''), dr.get('end', '')
                if ts and te:
                    if not (end_date < ts or start_date > te):
                        overlap_warning = ("样本内回测：回测区间 {}~{} 与模型训练区间 {}~{} 重叠，"
                                           "结果会显著偏乐观，仅用于调试".format(
                                               start_date, end_date, ts, te))
        result["inSampleWarning"] = overlap_warning
    except Exception:
        result["inSampleWarning"] = None

    # 回测结果持久化（含配置快照，供历史对比 A/B）
    try:
        result_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  'data', 'backtest_results')
        os.makedirs(result_dir, exist_ok=True)
        bt_id = 'bt_' + str(int(time.time() * 1000))
        snapshot = {
            "id": bt_id,
            "createdAt": time.strftime('%Y-%m-%dT%H:%M:%S'),
            "symbol": symbol,
            "strategy": strategy_type,
            "strategyParams": {k: v for k, v in strategy_params.items()
                               if k not in ('model_id',)} | {"modelId": strategy_params.get('model_id', '')},
            "startDate": start_date,
            "endDate": end_date,
            "period": period,
            "dividendType": dividend_type,
            "initialCapital": initial_capital,
            "commission": commission_rate,
            "slippage": slippage_rate,
            "dataSource": data_source,
            "metrics": metrics,
        }
        with open(os.path.join(result_dir, bt_id + '.json'), 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        result["backtestId"] = bt_id
    except Exception as e:
        log("回测结果持久化失败（不影响本次结果）: {}".format(e))

    return result


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
