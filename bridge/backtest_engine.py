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
from dataclasses import dataclass, field as dc_field
from typing import Optional, List, Dict

# SQLite cache module (same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 策略框架（回测与实盘共用同一份策略代码）
from strategies.registry import load_all, get as get_strategy
from strategies.base import Context, PortfolioSignal, Signal, slice_upto
import db as db_cache
from _shared import to_xtcode as _to_xtcode, generate_mock_bars, generate_mock_minute_bars


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


# D4.2 稳健性分析子运行期间置 True，跳过进度推送（避免多次回测刷屏）
_SILENT_PROGRESS = False


def log(msg):
    sys.stderr.write("[backtest] {}\n".format(msg))
    sys.stderr.flush()
    # D6.1 日志落盘（data/logs/backtest.log），失败不影响主流程
    try:
        from _logging import info as _log_info
        _log_info('backtest', msg)
    except Exception:
        pass


def reply(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def emit_progress(progress, stage, detail=""):
    """向主进程推送回测进度通知（无 id 的 JSON-RPC notification）。

    进度值 0-100，stage 取值：fetch(取数) / signal(生成信号) / match(撮合) / finish。
    稳健性分析（D4.2）子运行期间置 _SILENT_PROGRESS 跳过，避免多次回测刷屏。
    """
    if _SILENT_PROGRESS:
        return
    try:
        sys.stdout.write(json.dumps({
            "id": None,
            "method": "backtest.progress",
            "params": {"progress": int(round(progress)), "stage": stage, "detail": detail},
        }, ensure_ascii=False))
        sys.stdout.write("\n")
        sys.stdout.flush()
    except Exception:
        pass


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


def fetch_real_bars(symbol, start_date, end_date, dividend_type="front_ratio", period="1d"):
    """从统一数据访问层获取真实历史K线（front_ratio 前复权比例版，volume=股）。

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


def _resolve_strategy_type(strategy_type, strategy_params):
    """解析策略类型：registry 加载 + 壳策略按绑定/激活模型解析为具体 ML 策略。

    Returns:
        (strat_cls, resolved_params)
    """
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
        strategy_params = dict(strategy_params)
        strategy_params['model_id'] = model_id

    try:
        strat_cls = get_strategy(strategy_type)
    except KeyError:
        raise ValueError('未知策略类型: ' + strategy_type)
    return strat_cls, strategy_params


# ----------------------------------------------------------------------
# 日内回测（做T）：lot 批次仓位 + 交易日粒度 T+1 + 昨收涨跌停 + 底仓/T仓分账
# ----------------------------------------------------------------------
class _AccountSnapshot:
    """策略可见的账户快照（简单属性对象，避免策略依赖引擎内部结构）。"""
    def __init__(self, cash, total_assets):
        self.cash = cash                 # 可用现金（T资金）
        self.total_assets = total_assets # 总资产（现金+持仓市值）


def _aggregate_minutes_from_1m(bars_1m, n):
    """把 1m bars 聚合为 n 分钟 bars（按交易日分组、每 n 根合 1）。

    与 qmt_server._aggregate_minutes 同规则：跨日/跨午休不合并；
    聚合 bar 的 date 取桶内首根 1m bar 的时间（起始标注口径，
    slice_upto 的完成度判定对此口径保守安全）。
    """
    result = []
    cur_day = None
    group = []
    for b in bars_1m:
        d = str(b.get("date", ""))[:10]
        if d != cur_day:
            if group:
                result.extend(_merge_chunks(group, n))
            cur_day = d
            group = []
        group.append(b)
    if group:
        result.extend(_merge_chunks(group, n))
    return result


def _merge_chunks(day_bars, n):
    """把单日 1m bars 按 n 根一组聚合。"""
    out = []
    for i in range(0, len(day_bars), n):
        chunk = day_bars[i:i + n]
        out.append({
            "date": chunk[0]["date"],
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(b.get("volume", 0) for b in chunk),
            "amount": sum(b.get("amount", 0) for b in chunk),
        })
    return out


class _PositionSnapshot:
    """策略可见的持仓快照（底仓/T仓分层视图）。"""
    def __init__(self, core_shares, t_shares, sellable_t_shares, sellable_core_shares):
        self.core_shares = core_shares             # 底仓股数
        self.t_shares = t_shares                   # T仓股数
        self.sellable_t_shares = sellable_t_shares         # 可卖T仓（T+1解禁后）
        self.sellable_core_shares = sellable_core_shares   # 可卖底仓


@dataclass
class _Lot:
    """持仓批次：T+1 与底仓/T仓分账的最小单位。

    Attributes:
        shares: 本批股数（整手）
        cost: 本批成本价（含买入费用前的成交价）
        entry_dt: 入场时间（分钟bar时间戳）
        available_day: 最早可卖交易日 "YYYY-MM-DD"（T仓=T+1次日；底仓=建仓日即可卖）
        tag: "core"（底仓）| "t"（T仓）
    """
    shares: int
    cost: float
    entry_dt: str
    available_day: str
    tag: str


def _run_intraday_backtest(params):
    """日内回测（分钟周期驱动，做T场景）。

    与日线路径的关键差异：
      - T+1 按「交易日」解禁（lot.available_day），而非按 bar 解禁
      - 涨跌停按「昨日收盘」判定（分钟 bar 的前一根收盘无意义）
      - 仓位为 lot 批次列表：底仓(core) + T仓(t) 分层
      - 底仓首日自动建立（basePositionShares 参数，占用初始资金）
      - 尾盘 14:55 强制平 T 仓（forceEodClose 默认开）+ 14:57 后禁开新仓
      - 绩效分账：做T收益(tPnl) / 底仓收益(corePnl) / 总收益

    params 新增（相对日线路径）：
      basePositionShares: int  底仓股数（0=无底仓纯日内）
      forceEodClose: bool      尾盘强制平T仓（默认 True）
    """
    from data import datafeed as _df

    strategy_cfg = params.get("strategy", {})
    strategy_type = strategy_cfg.get("type", "ma_cross")
    strategy_params = strategy_cfg.get("params", {})
    symbols = params.get("symbols", []) or strategy_cfg.get("symbols", [])
    if not symbols:
        raise ValueError("策略标的不能为空")
    if len(symbols) > 1:
        raise ValueError("日内回测当前仅支持单标的（做T场景），多标的请用组合回测")
    symbol = symbols[0]

    start_date = params.get("startDate", "2024-01-01")
    end_date = params.get("endDate", "2024-06-30")
    initial_capital = float(params.get("initialCapital", 1000000))
    commission_rate = float(params.get("commission", 0.0003))
    slippage_rate = float(params.get("slippage", 0.001))
    stamp_tax = float(params.get("stampTax", 0.0005))
    transfer_fee = float(params.get("transferFee", 0.00001))
    dividend_type = params.get("dividendType", "front_ratio")
    period = params.get("period", "1m")
    allow_mock = bool(params.get("allowMock", False))
    base_position_shares = int(params.get("basePositionShares", 0) or 0)
    force_eod_close = bool(params.get("forceEodClose", True))
    max_vol_ratio = float(params.get("maxVolumeRatio", 0.25))
    TICK = 0.01
    MIN_COMMISSION = 5.0
    EOD_FLAT_TIME = "14:55"   # 尾盘强平时刻
    NO_NEW_OPEN_TIME = "14:57"  # 禁开新仓时刻

    # ---- 取数：驱动周期（1m）为主，日线/5m 辅助（多周期联动）----
    emit_progress(5, 'fetch', '加载分钟行情数据')
    bars, fetch_err = fetch_real_bars(symbol, start_date, end_date, dividend_type, period)
    data_source = None
    if bars and len(bars) > 0:
        data_source = "real"
        log("日内回测使用真实数据: {} 条".format(len(bars)))
    else:
        if not allow_mock:
            raise ValueError(
                "无法获取真实分钟行情数据（{}）。已默认拒绝使用模拟数据回测，"
                "请确认 miniQMT 已连接并已下载该标的分钟历史数据，"
                "或在回测配置中显式勾选「允许使用模拟数据」。".format(fetch_err))
        # 分钟 mock（仅链路自测用）
        bars = generate_mock_minute_bars(symbol, start_date, end_date, period="1m")
        data_source = "mock"
        log("日内回测使用模拟数据（allowMock）: {} 条".format(len(bars)))
    if len(bars) < 60:
        raise ValueError("回测数据不足（{} 根分钟bar），请扩大时间范围".format(len(bars)))

    # 日线（趋势门）：取全量历史（切片函数自行处理无前视，只需足够预热）
    daily_bars, _ = fetch_real_bars(symbol, "2000-01-01", end_date, dividend_type, "1d")
    # 5m（触发指标 RSI）：由 1m 就地聚合（每 5 根合 1，按日分组防跨日/跨午休）
    bars_5m = _aggregate_minutes_from_1m(bars, 5)
    bars_by_period_full = {"1m": bars, "5m": bars_5m, "1d": daily_bars or []}

    # ---- 策略实例 ----
    strat_cls, strategy_params = _resolve_strategy_type(strategy_type, strategy_params)
    strat = strat_cls(strategy_params)
    ctx = Context()
    ctx.symbol = symbol
    ctx.is_backtest = True
    ctx.period = period
    ctx.indicators = None
    strat.on_init(ctx)
    strat.on_after_init(ctx)

    # ---- 账户状态 ----
    xt_code = _to_xtcode(symbol)
    cash = initial_capital
    lots: List[_Lot] = []
    core_shares = 0        # 底仓股数（不变量：除建仓日外恒定）
    core_cost = 0.0        # 底仓成本价
    trades = []
    equity_curve = []
    skipped_signals = []
    t_pnl_total = 0.0      # 做T累计已实现盈亏（T仓差价 + 先卖后买卖回差价）
    t_round_count = 0      # 做T往返次数（一买一卖或一卖一买记一次）
    t_open_flag = False    # 当前是否有未完成的做T往返

    def _round_tick(p):
        return round(round(p / TICK) * TICK, 2)

    def _commission_of(amount):
        c = amount * commission_rate
        return max(c, MIN_COMMISSION) if amount > 0 else 0.0

    def _trading_days_of(bars_1m):
        """从分钟 bars 提取交易日序列（升序去重）。"""
        days = []
        seen = set()
        for b in bars_1m:
            d = str(b["date"])[:10]
            if d not in seen:
                seen.add(d)
                days.append(d)
        return days

    trading_days = _trading_days_of(bars)
    next_day_map = {d: (trading_days[i + 1] if i + 1 < len(trading_days) else d)
                    for i, d in enumerate(trading_days)}
    first_day = trading_days[0] if trading_days else ""

    def _total_shares():
        return sum(l.shares for l in lots)

    def _t_shares():
        return sum(l.shares for l in lots if l.tag == "t")

    def _sellable_lots(day):
        """T+1 可卖 lot 列表（按入场时间 FIFO）。"""
        return [l for l in lots if l.available_day <= day]

    def _buy(qty, price, dt, day, tag, reason):
        """买入 qty 股，创建新 lot。受现金/涨跌停（调用方已判）/整手约束（调用方已整手化）。

        lot.cost 记「含买入费用的摊薄成本」（(价×量+佣金+过户费)/量），
        保证后续卖出 pnl 与 restore 结转都自动含买入费，分账自洽。
        """
        nonlocal cash
        amount = price * qty
        commission = _commission_of(amount)
        transfer = amount * transfer_fee
        cost = amount + commission + transfer
        if cost > cash or qty <= 0:
            return False
        cash -= cost
        diluted = cost / qty
        if tag == "t":
            avail_day = next_day_map.get(day, day)
        else:
            avail_day = day  # 底仓当日即可卖（视为T-1前已有）
        lots.append(_Lot(shares=qty, cost=diluted, entry_dt=dt,
                         available_day=avail_day, tag=tag))
        trades.append({"date": dt, "side": "buy", "symbol": symbol,
                       "price": price, "quantity": qty,
                       "amount": round(cost, 2), "pnl": None,
                       "reason": reason, "lotTag": tag})
        return True

    def _sell(lot: _Lot, qty, price, dt, reason):
        """卖出指定 lot 的 qty 股（先卖后买还原底仓 = 卖 core lot）。

        做T盈亏归因：
          - 卖 t lot：盈亏 = 卖出净得 - 买回成本（标准日内差价）
          - 卖 core lot（先卖后买）：盈亏挂账，待买回 lot 成交时结转
            （_t_pnl_pending 記录已卖底仓待还原成本）
        """
        nonlocal cash, t_pnl_total, t_round_count, t_open_flag, core_shares, core_cost
        amount = price * qty
        commission = _commission_of(amount)
        proceeds = amount - commission - amount * (stamp_tax + transfer_fee)
        pnl = proceeds - lot.cost * qty
        cash += proceeds
        lot.shares -= qty
        if lot.shares <= 0:
            lots.remove(lot)
        if lot.tag == "core":
            core_shares -= qty
            if core_shares <= 0:
                core_shares = 0
                core_cost = 0.0
            # （底仓成本不因做T卖买变动：买回同数量还原时加权恢复）
            # 先卖后买：卖出底仓的差价在买回时结转（见 _buy 后的还原检查）
            _pending_core_restore.append({"shares": qty, "sell_proceeds": proceeds,
                                          "sell_dt": dt, "cost": lot.cost})
            trades.append({"date": dt, "side": "sell", "symbol": symbol,
                           "price": price, "quantity": qty,
                           "amount": round(proceeds, 2), "pnl": None,
                           "reason": reason, "lotTag": "core"})
        else:
            t_pnl_total += pnl
            trades.append({"date": dt, "side": "sell", "symbol": symbol,
                           "price": price, "quantity": qty,
                           "amount": round(proceeds, 2), "pnl": round(pnl, 2),
                           "reason": reason, "lotTag": "t"})
            if not any(l.tag == "t" for l in lots):
                if t_open_flag:
                    t_round_count += 1
                    t_open_flag = False
        return pnl

    # 先卖后买还原账本：卖出底仓后待买回的记录
    _pending_core_restore = []

    def _restore_core_check(dt, day):
        """买回后检查底仓还原：买回 lot 数量 >= 待还原数量时结转做T盈亏。

        先卖后买做T：卖底仓(sell core) -> 买回(buy t) -> 两笔差价 = 做T收益。
        买回价 < 卖出净得 -> 正收益。还原完成时把对应数量的 t lot 转为
        core lot（底仓真正还原：股数与标签都回到初始状态）。
        """
        nonlocal t_pnl_total, t_round_count, t_open_flag, core_shares, core_cost
        if not _pending_core_restore:
            return
        t_lots_shares = _t_shares()
        pending_shares = sum(p["shares"] for p in _pending_core_restore)
        if t_lots_shares < pending_shares:
            return
        # FIFO 消耗 t lot：还原数量对应的部分转为 core lot（真实还原：
        # 从 t lot 扣减股份，新建/合并到 core lot），其余留在 t。
        # lot.cost 已是含买入费的摊薄成本（_buy 保证），直接结转
        buy_cost_total = 0.0
        remaining = pending_shares
        for l in [l for l in lots if l.tag == "t"]:
            take = min(l.shares, remaining)
            if take <= 0:
                continue
            buy_cost_total += l.cost * take
            l.shares -= take
            if l.shares <= 0:
                lots.remove(l)
            # 转成 core lot（与既有 core lot 合并，成本加权）
            old_core = core_shares
            core_shares += take
            core_cost = ((core_cost * old_core + l.cost * take) / core_shares
                         if core_shares > 0 else l.cost)
            lots.append(_Lot(shares=take, cost=l.cost, entry_dt=l.entry_dt,
                             available_day=l.available_day, tag="core"))
            remaining -= take
            if remaining <= 0:
                break
        sell_proceeds_total = sum(p["sell_proceeds"] for p in _pending_core_restore)
        restore_pnl = sell_proceeds_total - buy_cost_total
        t_pnl_total += restore_pnl
        trades.append({"date": dt, "side": "restore", "symbol": symbol,
                       "price": None, "quantity": pending_shares,
                       "amount": round(sell_proceeds_total, 2),
                       "pnl": round(restore_pnl, 2),
                       "reason": "底仓还原完成（先卖后买做T结转）", "lotTag": "core"})
        _pending_core_restore.clear()
        if t_open_flag:
            t_round_count += 1
            t_open_flag = False

    # ---- 首日建底仓（开盘价）----
    base_cost_total = 0.0    # 底仓原始建仓总成本（含费；corePnl 锚定基准）
    if base_position_shares > 0:
        first_bar = bars[0]
        first_day_open = first_bar.get("open") or first_bar["close"]
        price = _round_tick(first_day_open * (1 + slippage_rate))
        qty = base_position_shares
        amount = price * qty
        commission = _commission_of(amount)
        transfer = amount * transfer_fee
        cost = amount + commission + transfer
        if cost > cash:
            raise ValueError(
                "初始资金不足以建立底仓（需 {:.0f} 元买 {} 股），请降低 basePositionShares 或增加初始资金"
                .format(cost, qty))
        cash -= cost
        core_shares = qty
        core_cost = cost / qty  # 含费摊薄成本（与分账口径一致）
        base_cost_total = cost
        lots.append(_Lot(shares=qty, cost=core_cost, entry_dt=first_bar["date"],
                         available_day=first_day, tag="core"))
        trades.append({"date": first_bar["date"], "side": "buy", "symbol": symbol,
                       "price": price, "quantity": qty, "amount": round(cost, 2),
                       "pnl": None, "reason": "建立底仓", "lotTag": "core"})

    # ---- 主循环：逐 1m bar 驱动 ----
    n_bars = len(bars)
    prev_day_close = None    # 昨日收盘（涨跌停判定基准，跨日更新）
    cur_day = None
    day_bar_index = 0
    day1m_bars = []          # 当日 1m bars（分时均线/日内策略状态用）
    _eod_flat_done_day = ""  # 尾盘强平哨兵：记录已执行强平的交易日

    for i, bar in enumerate(bars):
        dt = str(bar["date"])
        day = dt[:10]
        hm = dt[11:16] if len(dt) > 10 else "09:30"

        # 新交易日：解禁检查按 lot.available_day 逐日自然生效（无需显式操作），
        # 更新昨收基准
        if day != cur_day:
            if cur_day is not None and day1m_bars:
                prev_day_close = day1m_bars[-1]["close"]
            cur_day = day
            day_bar_index = 0
            day1m_bars = []

        # 无前视切片 + 填充 ctx
        ctx.bars = day1m_bars  # 语义：当日分钟序列（旧字段复用，兼容指标取当日）
        ctx.bars_by_period = slice_upto(bars_by_period_full, dt)
        # 基周期 bars 需含当前 bar（当前 bar 收盘已知时点）
        ctx.bars_by_period["1m"] = ctx.bars_by_period.get("1m", []) + [bar]
        ctx.bars_by_period["1m"] = [b for b in ctx.bars_by_period["1m"] if str(b["date"]) <= dt]
        ctx.barpos = i
        ctx.trading_day = day
        ctx.intraday_pos = day_bar_index
        day1m_bars.append(bar)
        day_bar_index += 1

        close = bar["close"]
        # 账户快照（策略侧仓位测算依据；简单对象，避免策略依赖引擎内部结构）
        ctx.account = _AccountSnapshot(cash=cash, total_assets=cash + _total_shares() * close)
        ctx.position = _PositionSnapshot(core_shares=core_shares, t_shares=_t_shares(),
                                         sellable_t_shares=sum(l.shares for l in _sellable_lots(day)
                                                               if l.tag == "t"),
                                         sellable_core_shares=sum(l.shares for l in _sellable_lots(day)
                                                                  if l.tag == "core"))
        limit_up = _df.is_limit_up(xt_code, close, prev_day_close)
        limit_down = _df.is_limit_down(xt_code, close, prev_day_close)

        # ---- 信号计算 ----
        sig = strat.on_bar(bar, ctx)
        if sig is not None and not isinstance(sig, Signal):
            sig = None  # PortfolioSignal 等不适用于日内单标的路径

        # ---- 撮合：风控退出 > 卖出 > 买入 > 尾盘强平 ----
        # 1) 卖出信号（含先卖后买：sell+core）
        if sig is not None and sig.action == "sell":
            want_qty = sig.qty
            tag = sig.lot_tag or "t"
            sellable = _sellable_lots(day)
            if tag == "core":
                # 先卖后买：只卖底仓 lot（available_day 已满足：底仓建仓日即可卖）
                core_lots = [l for l in sellable if l.tag == "core"]
                if not core_lots:
                    skipped_signals.append({"date": dt, "side": "sell",
                                            "reason": "无可用底仓可卖（T+1或无底仓）"})
                elif limit_down:
                    skipped_signals.append({"date": dt, "side": "sell", "reason": "跌停无法卖出"})
                else:
                    qty = want_qty if want_qty else core_lots[0].shares
                    qty = min(qty, sum(l.shares for l in core_lots))
                    qty = int(qty / 100) * 100
                    if qty > 0:
                        price = _round_tick(close * (1 - slippage_rate))
                        _sell(core_lots[0], qty, price, dt, sig.reason or "先卖后买：卖出底仓")
                        t_open_flag = True
                    else:
                        skipped_signals.append({"date": dt, "side": "sell", "reason": "卖出数量不足一手"})
            else:
                # 卖 T 仓（正向做T的卖出/反向做T的平仓买回前半段不涉及）
                t_sellable = [l for l in sellable if l.tag == "t"]
                if not t_sellable:
                    skipped_signals.append({"date": dt, "side": "sell",
                                            "reason": "无可用T仓可卖（T+1限制或无T仓）"})
                elif limit_down:
                    skipped_signals.append({"date": dt, "side": "sell", "reason": "跌停无法卖出"})
                else:
                    qty = want_qty if want_qty else sum(l.shares for l in t_sellable)
                    qty = min(qty, sum(l.shares for l in t_sellable))
                    qty = int(qty / 100) * 100
                    if qty > 0:
                        price = _round_tick(close * (1 - slippage_rate))
                        remain = qty
                        for l in list(t_sellable):
                            take = min(l.shares, remain)
                            if take > 0:
                                _sell(l, take, price, dt, sig.reason or "信号卖出T仓")
                                remain -= take
                            if remain <= 0:
                                break
                    else:
                        skipped_signals.append({"date": dt, "side": "sell", "reason": "卖出数量不足一手"})

        # 2) 买入信号（正向做T买入 / 反向做T买回还原底仓）
        if sig is not None and sig.action == "buy":
            if hm >= NO_NEW_OPEN_TIME:
                skipped_signals.append({"date": dt, "side": "buy", "reason": "尾盘（14:57后）禁开新仓"})
            elif limit_up:
                skipped_signals.append({"date": dt, "side": "buy", "reason": "涨停无法买入"})
            else:
                want_qty = sig.qty
                # 反向做T买回：优先补足待还原底仓
                pending_shares = sum(p["shares"] for p in _pending_core_restore)
                if pending_shares > 0:
                    qty = want_qty if want_qty else pending_shares
                    qty = min(qty, pending_shares)
                    qty = int(qty / 100) * 100
                    # 成交量约束
                    max_by_vol = int(bar.get("volume", 0) * max_vol_ratio / 100) * 100
                    if max_by_vol > 0:
                        qty = min(qty, max_by_vol)
                    price = _round_tick(close * (1 + slippage_rate))
                    if qty > 0:
                        ok = _buy(qty, price, dt, day, "t", sig.reason or "买回还原底仓")
                        if ok:
                            _restore_core_check(dt, day)
                else:
                    qty = want_qty
                    if qty is None:
                        # 无指定数量：按剩余现金八成（保守，留滑点/费用余量）
                        price_approx = _round_tick(close * (1 + slippage_rate))
                        qty = int(cash * 0.8 / price_approx / 100) * 100
                    qty = int(qty / 100) * 100
                    max_by_vol = int(bar.get("volume", 0) * max_vol_ratio / 100) * 100
                    if max_by_vol > 0:
                        qty = min(qty, max_by_vol)
                    price = _round_tick(close * (1 + slippage_rate))
                    if qty > 0:
                        _buy(qty, price, dt, day, "t", sig.reason or "T仓买入")

        # 3) 尾盘强平 T 仓（forceEodClose）：还原底仓语义（卖出未还原部分按 T 仓处理）
        #    哨兵 _eod_flat_done：每个交易日只在首根 >= 14:55 的 bar 执行一次
        if force_eod_close and hm >= EOD_FLAT_TIME:
            if _eod_flat_done_day != day:
                sellable = _sellable_lots(day)
                t_sellable = [l for l in sellable if l.tag == "t"]
                if t_sellable and not limit_down:
                    price = _round_tick(close * (1 - slippage_rate))
                    for l in list(t_sellable):
                        if l.shares > 0:
                            _sell(l, l.shares, price, dt, "尾盘强平T仓")
                # 未还原的底仓卖出部分：以当日收盘买回还原
                pending_shares = sum(p["shares"] for p in _pending_core_restore)
                if pending_shares > 0 and not limit_up:
                    price = _round_tick(close * (1 + slippage_rate))
                    qty = int(pending_shares / 100) * 100
                    max_by_vol = int(bar.get("volume", 0) * max_vol_ratio / 100) * 100
                    if max_by_vol > 0:
                        qty = min(qty, max_by_vol)
                    ok = _buy(qty, price, dt, day, "t", "尾盘买回还原底仓")
                    if ok:
                        _restore_core_check(dt, day)
                _eod_flat_done_day = day

        # ---- 净值 ----
        mv = sum(l.shares * close for l in lots)
        total = cash + mv
        equity_curve.append({"date": dt, "value": round(total, 2)})

        if i % 200 == 0 or i == n_bars - 1:
            emit_progress(10 + int(85 * (i + 1) / n_bars), 'match',
                          '日内撮合 {} / {}'.format(i + 1, n_bars))

    strat.on_stop(ctx)
    emit_progress(96, 'finish', '计算绩效指标')

    # ---- 绩效指标（含做T分账）----
    final_price = bars[-1]["close"]
    final_value = cash + sum(l.shares * final_price for l in lots)
    total_return = (final_value - initial_capital) / initial_capital * 100

    # 底仓收益：锚定「原始建仓成本」（做T的卖买还原不应改变底仓成本基准）。
    # 口径：corePnl = (期末价 - 原始建仓含费成本/股) × 期末底仓股数
    pending_shares_end = sum(p["shares"] for p in _pending_core_restore)
    core_pnl = 0.0
    if core_shares > 0 and base_cost_total > 0:
        core_pnl = (final_price - base_cost_total / base_position_shares) * core_shares
    # 未还原底仓缺口（尾盘买回失败等）：卖出净得已进现金但底仓缺失，
    # 差价（净得 - 原始成本）按做T盈亏结转入 tPnl（还原失败 = 一次不完整的反向T）
    if pending_shares_end > 0 and base_cost_total > 0:
        base_cost_per_share = base_cost_total / base_position_shares
        sell_proceeds_end = sum(p["sell_proceeds"] for p in _pending_core_restore)
        t_pnl_total += sell_proceeds_end - base_cost_per_share * pending_shares_end

    days = len(trading_days)
    n_trading_days = days if days > 0 else 1
    if days >= 60:
        annual_return = ((final_value / initial_capital) ** (252 / days) - 1) * 100
        annual_return_note = None
    else:
        annual_return = 0.0
        annual_return_note = "回测区间过短（{} 个交易日），年化收益率失真，请参考总收益率".format(days)

    # 日收益率（按交易日聚合净值）
    day_equity = {}
    for pt in equity_curve:
        d = str(pt["date"])[:10]
        day_equity[d] = pt["value"]  # 同日覆盖取最后一根
    day_values = [day_equity[d] for d in sorted(day_equity.keys())]
    daily_returns = []
    for j in range(1, len(day_values)):
        if day_values[j - 1] > 0:
            daily_returns.append((day_values[j] - day_values[j - 1]) / day_values[j - 1])
    if daily_returns:
        avg_ret = sum(daily_returns) / len(daily_returns)
        std_ret = math.sqrt(sum((r - avg_ret) ** 2 for r in daily_returns) / len(daily_returns))
        sharpe = (avg_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0
    else:
        sharpe, avg_ret, std_ret = 0, 0.0, 0.0
    annual_vol = std_ret * math.sqrt(252) * 100 if daily_returns else 0
    downside = [r for r in daily_returns if r < 0]
    downside_std = math.sqrt(sum(r * r for r in downside) / len(downside)) if downside else 0
    sortino = (avg_ret / downside_std * math.sqrt(252)) if downside_std > 0 else 0

    peak = 0
    max_dd = 0
    for v in day_values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak * 100
            if dd < max_dd:
                max_dd = dd
    calmar = (annual_return / abs(max_dd)) if max_dd < 0 else 0

    sell_trades = [t for t in trades if t["side"] == "sell" and t.get("pnl") is not None]
    wins = [t for t in sell_trades if t["pnl"] > 0]
    losses = [t for t in sell_trades if t["pnl"] <= 0]
    win_rate = (len(wins) / len(sell_trades) * 100) if sell_trades else 0
    avg_win = (sum(t["pnl"] for t in wins) / len(wins)) if wins else 0
    avg_loss = (abs(sum(t["pnl"] for t in losses) / len(losses))) if losses else 1
    profit_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0

    metrics = {
        "totalReturn": round(total_return, 2),
        "benchmarkReturn": 0.0,  # 日内路径不提供基准（语义不同，避免误导）
        "excessReturn": None,
        "annualReturn": round(annual_return, 2),
        "annualReturnNote": annual_return_note,
        "maxDrawdown": round(max_dd, 2),
        "sharpeRatio": round(sharpe, 2),
        "sortinoRatio": round(sortino, 2),
        "calmarRatio": round(calmar, 2),
        "annualVolatility": round(annual_vol, 2),
        "avgHoldDays": 0,  # 日内持仓以分钟计
        "winRate": round(win_rate, 2),
        "winRateInclOpen": round(win_rate, 2),
        "realizedPnl": round(sum(t.get("pnl") or 0 for t in sell_trades), 2),
        "openPnl": round(core_pnl, 2),
        "profitLossRatio": round(profit_loss_ratio, 2),
        "totalTrades": len([t for t in trades if t["side"] in ("buy", "sell")]),
        "skippedSignals": len(skipped_signals),
        "finalCapital": round(final_value, 2),
        # ---- 做T分账 ----
        "intraday": {
            "tPnl": round(t_pnl_total, 2),
            "corePnl": round(core_pnl, 2),
            "coreShares": core_shares,
            "coreCost": round(core_cost, 2) if core_cost > 0 else 0,
            "tRounds": t_round_count,
            "tPerDay": round(t_round_count / max(n_trading_days, 1), 2),
            "avgTRoundPnl": round(t_pnl_total / t_round_count, 2) if t_round_count else 0,
            "pendingRestoreShares": pending_shares_end,
            "tradingDays": n_trading_days,
            "finalTShares": _t_shares(),
        },
    }

    # 日线聚合 bars（UI 图表用：日线 OHLC 聚合）
    daily_agg = []
    if bars:
        cur = None
        for b in bars:
            d = str(b["date"])[:10]
            if cur is None or cur["date"] != d:
                if cur:
                    daily_agg.append(cur)
                cur = {"date": d, "open": b["open"], "high": b["high"],
                       "low": b["low"], "close": b["close"], "volume": b.get("volume", 0)}
            else:
                cur["high"] = max(cur["high"], b["high"])
                cur["low"] = min(cur["low"], b["low"])
                cur["close"] = b["close"]
                cur["volume"] += b.get("volume", 0)
        if cur:
            daily_agg.append(cur)

    result = {
        "metrics": metrics,
        "trades": trades,
        "equityCurve": equity_curve,
        "monthlyReturns": [],
        "skippedSignals": skipped_signals[:100],
        "dataSource": data_source,
        "fillModel": "intraday_1m",
        "risk": None,
        "regime": None,
        "name": _fetch_stock_name(symbol),
        "symbol": symbol,
        "period": period,
        "basePositionShares": base_position_shares,
        "intraday": True,
        "bars": daily_agg,
        "equityCurveGranularity": "1m",
    }

    # 结果持久化（日内快照）
    if not params.get("noPersist", False):
        try:
            result_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      'data', 'backtest_results')
            os.makedirs(result_dir, exist_ok=True)
            bt_id = 'bt_' + str(int(time.time() * 1000))
            snapshot = {
                "id": bt_id,
                "createdAt": time.strftime('%Y-%m-%dT%H:%M:%S'),
                "symbol": symbol,
                "intraday": True,
                "strategy": strategy_type,
                "strategyParams": strategy_params,
                "startDate": start_date,
                "endDate": end_date,
                "period": period,
                "dividendType": dividend_type,
                "initialCapital": initial_capital,
                "basePositionShares": base_position_shares,
                "commission": commission_rate,
                "slippage": slippage_rate,
                "forceEodClose": force_eod_close,
                "dataSource": data_source,
                "metrics": metrics,
            }
            with open(os.path.join(result_dir, bt_id + '.json'), 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)
            result["backtestId"] = bt_id
        except Exception as e:
            log("日内回测结果持久化失败（不影响本次结果）: {}".format(e))

    return result


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
    # 日内分支：分钟周期驱动（做T/日内策略）。日线/周线路径零改动。
    period = params.get("period", "1d")
    if period in ("1m", "5m", "15m", "30m", "60m"):
        return _run_intraday_backtest(params)

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
    dividend_type = params.get("dividendType", "front_ratio")
    period = params.get("period", "1d")

    # D0.1 撮合口径三档：close（收盘成交，乐观）/ next_open（次日开盘成交，保守，默认）/ tick（未实现）
    fill_model = str(params.get("fillModel", "next_open")).strip().lower()
    if fill_model == "tick":
        raise ValueError("tick 撮合口径需要分钟级数据，当前版本尚未实现，请使用 close 或 next_open")
    if fill_model not in ("close", "next_open"):
        raise ValueError("未知撮合口径 fillModel: {}（可选：close / next_open）".format(fill_model))
    # D0.3 阻断 mock 静默降级：真实数据不可用时默认拒绝，需显式 allowMock
    allow_mock = bool(params.get("allowMock", False))
    # D2.1/D2.2 统一风控层（止损/止盈/时间止损/熔断/仓位），默认全部关闭，保持兼容
    from risk_control import RiskController, regime_mask
    risk = RiskController(params)
    # D2.3 市场状态过滤（regime）：指数趋势过滤，默认关闭
    regime_enabled = bool(params.get("regimeEnabled", False))
    regime_index = str(params.get("regimeIndex", "000300.SH") or "000300.SH").strip()
    regime_fast = int(params.get("regimeFast", 20) or 20)
    regime_own_ma = bool(params.get("regimeOwnMa", False))

    # D2.4 组合回测：多标的 + 组合策略（is_portfolio=True，返回 PortfolioSignal）走组合路径
    if len(symbols) > 1:
        _pcls, _pparams = _resolve_strategy_type(strategy_type, strategy_params)
        if getattr(_pcls, "is_portfolio", False):
            return _run_portfolio_backtest(params, _pcls, _pparams)
        raise ValueError(
            "策略 {} 为单标的策略，当前不支持多标的回测；"
            "多标的组合请使用组合策略（如 portfolio_equal_weight）".format(strategy_type))
    symbol = symbols[0]
    stock_name = _fetch_stock_name(symbol)

    # 优先使用 xtquant 真实数据，失败时默认拒绝模拟数据（D0.3：阻断静默降级）
    emit_progress(5, 'fetch', '加载行情数据')
    bars, fetch_err = fetch_real_bars(symbol, start_date, end_date, dividend_type, period)
    if bars and len(bars) > 0:
        log("回测使用真实数据: {} 条".format(len(bars)))
        data_source = "real"
    else:
        if not allow_mock:
            raise ValueError(
                "无法获取真实行情数据（{}）。已默认拒绝使用模拟数据回测，"
                "请确认 miniQMT 已连接并已下载该标的历史数据，"
                "或在回测配置中显式勾选「允许使用模拟数据」。".format(fetch_err))
        log("回测使用模拟数据（allowMock）: {}".format(fetch_err))
        bars = generate_mock_bars(symbol, start_date, end_date)
        data_source = "mock"


    if len(bars) < 5:
        raise ValueError("回测数据不足，请扩大时间范围")

    # D2.3 市场状态过滤：获取指数数据并计算逐日做多许可掩码
    # （regime 判定用「前一交易日」指数状态，无前视；指数不可用时降级为不生效并告警）
    regime_ok = None
    regime_warn = None
    if regime_enabled:
        from data import datafeed as _df0
        index_bars, _idx_src = _df0.fetch_bars(regime_index, period=period,
                                               start_date=start_date, end_date=end_date,
                                               dividend_type=dividend_type)
        regime_ok, regime_warn = regime_mask(bars, index_bars, regime_fast, regime_own_ma)
        if regime_warn:
            log("regime: {}".format(regime_warn))

    # 解析策略类型（registry 加载 + 壳策略按绑定/激活模型解析为具体 ML 策略）
    strat_cls, strategy_params = _resolve_strategy_type(strategy_type, strategy_params)
    strat = strat_cls(strategy_params)

    ctx = Context()
    ctx.symbol = symbol
    ctx.is_backtest = True
    ctx.period = "1d"
    strat.on_init(ctx)
    strat.on_after_init(ctx)

    signal_map = {}  # date -> Signal
    n_bars = len(bars)
    for i, bar in enumerate(bars):
        ctx.bars = bars[:i + 1]
        ctx.barpos = i
        sig = strat.on_bar(bar, ctx)
        if sig is not None and sig.action in ("buy", "sell", "hold"):
            signal_map[bar["date"]] = sig
        # 信号生成阶段进度：10% -> 50%（与撮合阶段共享总进度 5%~95%）
        if i % 10 == 0 or i == n_bars - 1:
            emit_progress(10 + int(40 * (i + 1) / n_bars), 'signal',
                          '生成信号 {} / {}'.format(i + 1, n_bars))
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

    def _sellable_qty():
        """T+1 下可卖股数（无持仓时 0）。"""
        if position <= 0:
            return 0
        return position if not enable_t1 else min(position, avail_position)

    def _sell(qty, price, date_str, index, reason=None):
        """按指定价卖出 qty 股（信号卖出/目标仓位减仓/风控退出共用）。

        统一成交成本、盈亏计算与平仓后熔断状态更新（D2.1）。
        Returns: 已实现盈亏
        """
        nonlocal cash, position, avail_position, cost_price
        amount = price * qty
        commission = _commission_of(amount)
        proceeds = amount - commission - amount * (stamp_tax + transfer_fee)
        pnl = proceeds - cost_price * qty
        cash += proceeds
        position -= qty
        avail_position = min(avail_position, position)
        if position == 0:
            cost_price = 0.0
            risk.mark_close(pnl, index)
        trades.append({
            "date": date_str,
            "side": "sell",
            "symbol": symbol,
            "price": price,
            "quantity": qty,
            "amount": round(proceeds, 2),
            "pnl": round(pnl, 2),
            "reason": reason,
        })
        return pnl

    prev_close = None
    pending_sig = None  # next_open 口径：上一根 bar 收盘生成的信号，在本根 bar 开盘执行
    for i, bar in enumerate(bars):
        date_str = bar["date"]
        close = bar["close"]
        # T+1 解禁：新的一天，昨日买入的持仓今日可卖
        if enable_t1:
            avail_position = position

        # 撮合口径（D0.1）：选择本 bar 执行的信号与成交价
        #   close     -> 信号与本根 bar 收盘价同时成交（乐观：收盘看到信号即以收盘价成交）
        #   next_open -> 上一根 bar 收盘生成的信号在本根 bar 开盘成交（保守：真实世界只能次日开盘买入）
        if fill_model == "next_open":
            sig = pending_sig
            exec_price = bar.get("open") or close
            pending_sig = signal_map.get(date_str)
        else:
            sig = signal_map.get(date_str)
            exec_price = close
        action = sig.action if sig else None
        target_pos = sig.target_position if sig else None
        limit_up = _df.is_limit_up(xt_code, exec_price, prev_close)
        limit_down = _df.is_limit_down(xt_code, exec_price, prev_close)

        # D2.3 市场状态过滤：判定本日是否允许做多（无前视）
        #   i>0 用「前一交易日」指数状态；i=0 初始日用当日状态（close 口径无前视）
        regime_allowed = True
        if regime_ok is not None:
            gate_date = str(bars[i - 1]["date"])[:10] if i > 0 else str(bars[i]["date"])[:10]
            regime_allowed = regime_ok.get(gate_date, True)

        # --- 目标仓位调仓（支持 target_position 信号）---
        if target_pos is not None:
            total_asset = cash + position * exec_price
            target_value = total_asset * target_pos
            target_qty = int(target_value / exec_price / 100) * 100
            if target_qty > position:
                was_flat = position == 0
                if not regime_allowed:
                    skipped_signals.append({"date": date_str, "side": "buy",
                                            "reason": "市场状态过滤（指数/个股低于均线，禁止做多）"})
                elif not risk.can_open(i):
                    skipped_signals.append({"date": date_str, "side": "buy",
                                            "reason": "连续亏损熔断（暂停开新仓）"})
                else:
                    delta = target_qty - position
                    price = _round_tick(exec_price * (1 + slippage_rate))
                    if limit_up:
                        skipped_signals.append({"date": date_str, "side": "buy", "reason": "涨停无法买入"})
                    else:
                        # D2.2 仓位上限：目标比例 × 风控仓位比例
                        cap_qty = int(total_asset * risk.position_pct(sig.strength if sig else 1.0)
                                      / exec_price / 100) * 100
                        if cap_qty > 0:
                            target_qty = min(target_qty, cap_qty)
                            delta = target_qty - position
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
                            if was_flat:
                                risk.mark_entry(i)
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
                    price = _round_tick(exec_price * (1 - slippage_rate))
                    _sell(delta, price, date_str, i, reason="目标仓位减仓")

        # --- 执行交易信号（buy/sell）---
        elif action == "buy" and position == 0:
            if not regime_allowed:
                skipped_signals.append({"date": date_str, "side": "buy",
                                        "reason": "市场状态过滤（指数/个股低于均线，禁止做多）"})
            elif not risk.can_open(i):
                skipped_signals.append({"date": date_str, "side": "buy",
                                        "reason": "连续亏损熔断（暂停开新仓）"})
            else:
                price = _round_tick(exec_price * (1 + slippage_rate))
                if limit_up:
                    skipped_signals.append({"date": date_str, "side": "buy", "reason": "涨停无法买入"})
                else:
                    max_qty = int(cash / (price * (1 + buy_cost_rate)) / 100) * 100
                    # D2.2 仓位约束：单笔最大仓位（占总资产，可选按 strength 缩放）
                    pos_cap = int(risk.target_value(cash, sig.strength if sig else 1.0)
                                  / price / 100) * 100
                    max_qty = min(max_qty, pos_cap)
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
                            risk.mark_entry(i)
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
                qty = _sellable_qty()
                price = _round_tick(exec_price * (1 - slippage_rate))
                _sell(qty, price, date_str, i, reason="信号卖出")

        # D2.1 风控层：止损/止盈/时间止损（统一强制退出，尊重 T+1 与涨跌停）
        if position > 0:
            trigger = risk.exit_trigger(bar, cost_price, i)
            if trigger is not None:
                r_reason, r_price = trigger
                if limit_down:
                    skipped_signals.append({"date": date_str, "side": "sell",
                                            "reason": "跌停无法卖出（{}）".format(r_reason)})
                elif enable_t1 and avail_position <= 0:
                    skipped_signals.append({"date": date_str, "side": "sell",
                                            "reason": "T+1限制，当日买入不可卖（{}）".format(r_reason)})
                else:
                    qty = _sellable_qty()
                    fill = _round_tick(r_price)
                    _sell(qty, fill, date_str, i, reason=r_reason)

        # 计算当日净值（基于当天的实际持仓状态）
        mv = position * close if position > 0 else 0
        total = cash + mv
        equity_curve.append({
            "date": date_str,
            "value": round(total, 2),
        })
        prev_close = close
        # 撮合阶段进度：50% -> 95%（与信号生成阶段共享总进度 5%~95%）
        if i % 10 == 0 or i == n_bars - 1:
            emit_progress(50 + int(45 * (i + 1) / n_bars), 'match',
                          '撮合交易 {} / {}'.format(i + 1, n_bars))

    emit_progress(96, 'finish', '计算绩效指标')

    # 最终估值
    final_bar = bars[-1]
    final_price = final_bar["close"]
    final_value = cash + (position * final_price if position > 0 else 0)

    # 绩效指标
    total_return = (final_value - initial_capital) / initial_capital * 100

    # 年化收益率（D0.2 短区间失真保护：<60 个交易日不年化，避免指数放大误导）
    days = len(bars)
    if days >= 60:
        annual_return = ((final_value / initial_capital) ** (252 / max(days, 1)) - 1) * 100 if days > 0 else 0
        annual_return_note = None
    else:
        annual_return = 0.0
        annual_return_note = "回测区间过短（{} 个交易日），年化收益率失真，请参考总收益率".format(days)

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

    # D0.2 未平仓浮盈亏 & 含未实现口径胜率：持仓中的浮动盈亏按一笔虚拟交易计入，
    # 避免「回测末尾仍持仓」导致胜率只看已实现、与实际总收益口径不一致
    realized_pnl = sum(t.get("pnl") or 0 for t in sell_trades)
    open_pnl = 0.0
    if position > 0 and cost_price > 0:
        open_pnl = (final_price - cost_price) * position
    if position > 0:
        n_incl = len(sell_trades) + 1
        wins_incl = len(wins) + (1 if open_pnl > 0 else 0)
        win_rate_incl_open = wins_incl / n_incl * 100 if n_incl > 0 else 0
    else:
        win_rate_incl_open = win_rate

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
        "annualReturnNote": annual_return_note,
        "maxDrawdown": round(max_dd, 2),
        "sharpeRatio": round(sharpe, 2),
        "sortinoRatio": round(sortino, 2),
        "calmarRatio": round(calmar, 2),
        "annualVolatility": round(annual_vol, 2),
        "avgHoldDays": avg_hold_days,
        "winRate": round(win_rate, 2),
        "winRateInclOpen": round(win_rate_incl_open, 2),
        "realizedPnl": round(realized_pnl, 2),
        "openPnl": round(open_pnl, 2),
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
        "fillModel": fill_model,
        "risk": risk.summary(),
        "regime": ({
            "enabled": True,
            "index": regime_index,
            "fast": regime_fast,
            "ownMa": regime_own_ma,
            "allowedDays": sum(1 for v in (regime_ok or {}).values() if v),
            "blockedDays": sum(1 for v in (regime_ok or {}).values() if not v),
            "warning": regime_warn,
        } if regime_enabled else None),
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

    # 回测结果持久化（含配置快照，供历史对比 A/B；稳健性子运行不落盘）
    if not params.get("noPersist", False):
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
                "fillModel": fill_model,
                "allowMock": allow_mock,
                "risk": risk.summary(),
                "regime": ({
                    "index": regime_index, "fast": regime_fast, "ownMa": regime_own_ma,
                    "warning": regime_warn,
                } if regime_enabled else None),
                "dataSource": data_source,
                "metrics": metrics,
            }
            with open(os.path.join(result_dir, bt_id + '.json'), 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)
            result["backtestId"] = bt_id
        except Exception as e:
            log("回测结果持久化失败（不影响本次结果）: {}".format(e))

    # D4.2 稳健性分析：成本/参数/时间切片敏感性（防过拟合诊断）
    if params.get("robustness"):
        result["robustness"] = _run_robustness(params, strategy_type, strategy_params)

    return result


# ----------------------------------------------------------------------
# 组合回测（D2.4）：多标的 + 组合策略（PortfolioSignal）
# ----------------------------------------------------------------------
def _run_portfolio_backtest(params, strat_cls, strategy_params):
    """多标的组合回测。

    适用：组合策略（is_portfolio=True，on_bar 返回 PortfolioSignal），如 portfolio_equal_weight。
    特性：
      - 逐标的取数（真实优先，allowMock 回落），统一日期 + forward-fill 估值，停牌日不可交易
      - 组合级现金、先卖后买再平衡、成本/滑点/tick/整手/成交量约束/T+1/涨跌停
      - regime 市场状态门控（复用 regime_mask）、逐标的止损/止盈/时间止损、
        组合级连续亏损熔断
      - 组合净值曲线、等权买入持有基准、分标的贡献
    """
    from strategies.base import PortfolioSignal, Context
    from data import datafeed as _df
    from risk_control import RiskController, regime_mask

    symbols = list(params.get("symbols", []) or [])
    start_date = params.get("startDate", "2024-01-01")
    end_date = params.get("endDate", "2024-06-30")
    initial_capital = float(params.get("initialCapital", 1000000))
    commission_rate = float(params.get("commission", 0.0003))
    slippage_rate = float(params.get("slippage", 0.001))
    stamp_tax = float(params.get("stampTax", 0.0005))
    transfer_fee = float(params.get("transferFee", 0.00001))
    dividend_type = params.get("dividendType", "front_ratio")
    period = params.get("period", "1d")
    fill_model = str(params.get("fillModel", "next_open")).strip().lower()
    allow_mock = bool(params.get("allowMock", False))
    enable_t1 = bool(params.get("enableT1", True))
    max_vol_ratio = float(params.get("maxVolumeRatio", 0.25))
    risk = RiskController(params)
    regime_enabled = bool(params.get("regimeEnabled", False))
    regime_index = str(params.get("regimeIndex", "000300.SH") or "000300.SH").strip()
    regime_fast = int(params.get("regimeFast", 20) or 20)
    regime_own_ma = bool(params.get("regimeOwnMa", False))

    TICK = 0.01
    MIN_COMMISSION = 5.0

    def _round_tick(p):
        return round(round(p / TICK) * TICK, 2)

    def _commission_of(amount):
        c = amount * commission_rate
        return max(c, MIN_COMMISSION) if amount > 0 else 0.0

    # ---- 取数（真实优先，失败按 allowMock 回落 mock）----
    emit_progress(5, 'fetch', '加载组合行情数据')
    bars_by_symbol = {}
    data_sources = set()
    for sym in symbols:
        bars, err = fetch_real_bars(sym, start_date, end_date, dividend_type, period)
        if bars and len(bars) > 0:
            bars_by_symbol[sym] = bars
            data_sources.add("real")
        else:
            if not allow_mock:
                raise ValueError(
                    "无法获取 {} 真实行情（{}）。组合回测默认拒绝使用模拟数据，"
                    "请连接 miniQMT 或显式勾选「允许使用模拟数据」。".format(sym, err))
            bars_by_symbol[sym] = generate_mock_bars(sym, start_date, end_date)
            data_sources.add("mock")
            log("{} 使用模拟数据（allowMock）".format(sym))

    symbols = [s for s in symbols if s in bars_by_symbol and bars_by_symbol[s]]
    if not symbols:
        raise ValueError("组合回测：全部标的数据不可用")
    all_dates = sorted(set().union(*[set(b['date'] for b in bars_by_symbol[s]) for s in symbols]))
    if len(all_dates) < 5:
        raise ValueError("回测数据不足，请扩大时间范围")
    date_bars = {s: {b['date']: b for b in bars_by_symbol[s]} for s in symbols}
    data_source = "mock" if "mock" in data_sources else "real"
    log("组合回测: {} 标的 / {} 个交易日".format(len(symbols), len(all_dates)))

    # ---- regime（组合级门控）----
    regime_ok, regime_warn = None, None
    if regime_enabled:
        index_bars, _ = _df.fetch_bars(regime_index, period=period,
                                       start_date=start_date, end_date=end_date,
                                       dividend_type=dividend_type)
        flat = [b for s in symbols for b in bars_by_symbol[s]]
        regime_ok, regime_warn = regime_mask(flat, index_bars, regime_fast, regime_own_ma)
        if regime_warn:
            log("regime: {}".format(regime_warn))

    # ---- 策略实例 ----
    strat = strat_cls(strategy_params)
    ctx = Context()
    ctx.is_backtest = True
    ctx.period = period
    ctx.universe = symbols
    strat.on_init(ctx)
    strat.on_after_init(ctx)

    cash = initial_capital
    positions = {s: 0 for s in symbols}
    avail = {s: 0 for s in symbols}
    cost_price = {s: 0.0 for s in symbols}
    entry_idx = {s: -1 for s in symbols}
    trades = []
    equity_curve = []
    skipped_signals = []
    last_close = {s: None for s in symbols}
    prev_close = {s: None for s in symbols}
    consec_losses = 0
    cooldown_until = -1
    pending_targets = None
    pending_reason = ""

    def _track_close(pnl, i):
        nonlocal consec_losses, cooldown_until
        if risk.max_consec_losses <= 0 or pnl is None:
            return
        if pnl < 0:
            consec_losses += 1
            if consec_losses >= risk.max_consec_losses:
                cooldown_until = i + risk.cooldown_days
        else:
            consec_losses = 0

    def _sell_one(s, qty, price, date, reason):
        nonlocal cash
        amount = price * qty
        commission = _commission_of(amount)
        proceeds = amount - commission - amount * (stamp_tax + transfer_fee)
        pnl = proceeds - cost_price[s] * qty
        cash += proceeds
        positions[s] -= qty
        avail[s] = min(avail[s], positions[s])
        if positions[s] == 0:
            cost_price[s] = 0.0
            entry_idx[s] = -1
        trades.append({"date": date, "side": "sell", "symbol": s, "price": price,
                       "quantity": qty, "amount": round(proceeds, 2),
                       "pnl": round(pnl, 2), "reason": reason})
        return pnl

    def _rebalance(targets, date, i, reason):
        nonlocal cash
        total_asset = cash + sum(positions[s] * (last_close[s] or 0) for s in symbols)
        if total_asset <= 0:
            return
        plan = {}
        for s in symbols:
            pct = float(targets.get(s, 0.0) or 0.0)
            if risk.max_position_pct < 1.0 or risk.strength_scaling:
                pct = min(pct, risk.position_pct(1.0))
            price = last_close[s] or 0
            if price <= 0:
                continue
            plan[s] = int(total_asset * pct / price / 100) * 100
        # 先卖（释放现金）
        for s in symbols:
            target_qty = plan.get(s, 0)
            cur = positions[s]
            if target_qty >= cur:
                continue
            b = date_bars[s].get(date)
            if b is None:
                continue  # 停牌不可交易
            exec_price = (b.get("open") or b["close"]) if fill_model == "next_open" else b["close"]
            limit_down = _df.is_limit_down(_to_xtcode(s), exec_price, prev_close[s])
            sellable = cur if not enable_t1 else min(cur, avail[s])
            if limit_down or (enable_t1 and sellable <= 0):
                r2 = "跌停无法卖出" if limit_down else "T+1限制，当日买入不可卖"
                skipped_signals.append({"date": date, "side": "sell", "reason": r2})
                continue
            delta = min(cur - target_qty, sellable)
            price = _round_tick(exec_price * (1 - slippage_rate))
            pnl = _sell_one(s, delta, price, date, reason or "组合再平衡")
            _track_close(pnl, i)
        # 后买（受组合现金约束）
        for s in symbols:
            target_qty = plan.get(s, 0)
            cur = positions[s]
            if target_qty <= cur:
                continue
            b = date_bars[s].get(date)
            if b is None:
                continue
            if regime_ok is not None:
                gate_date = all_dates[i - 1] if i > 0 else all_dates[i]
                if not regime_ok.get(gate_date, True):
                    skipped_signals.append({"date": date, "side": "buy",
                                            "reason": "市场状态过滤（指数/个股低于均线，禁止做多）"})
                    continue
            if risk.max_consec_losses > 0 and i <= cooldown_until:
                skipped_signals.append({"date": date, "side": "buy", "reason": "连续亏损熔断（暂停开新仓）"})
                continue
            exec_price = (b.get("open") or b["close"]) if fill_model == "next_open" else b["close"]
            limit_up = _df.is_limit_up(_to_xtcode(s), exec_price, prev_close[s])
            if limit_up:
                skipped_signals.append({"date": date, "side": "buy", "reason": "涨停无法买入"})
                continue
            price = _round_tick(exec_price * (1 + slippage_rate))
            delta = target_qty - cur
            max_by_vol = int(b.get("volume", 0) * max_vol_ratio / 100) * 100
            if max_by_vol > 0:
                delta = min(delta, max_by_vol)
            while delta > 0:
                amount = price * delta
                commission = _commission_of(amount)
                cost = amount + commission + amount * transfer_fee
                if cost <= cash:
                    break
                delta -= 100
            if delta > 0:
                cash -= cost
                if cur > 0:
                    cost_price[s] = (cost_price[s] * cur + price * delta) / (cur + delta)
                else:
                    cost_price[s] = price
                positions[s] += delta
                if not enable_t1:
                    avail[s] = positions[s]
                if cur == 0:
                    entry_idx[s] = i
                trades.append({"date": date, "side": "buy", "symbol": s, "price": price,
                               "quantity": delta, "amount": round(cost, 2), "pnl": None})

    def _risk_exits(date, i):
        for s in symbols:
            if positions[s] <= 0:
                continue
            b = date_bars[s].get(date)
            if b is None:
                continue
            trigger = None
            cp = cost_price[s]
            if risk.stop_loss > 0 and b.get("low") is not None:
                sp = cp * (1 - risk.stop_loss)
                if b["low"] <= sp:
                    trigger = ("止损", sp)
            if trigger is None and risk.take_profit > 0 and b.get("high") is not None:
                tp = cp * (1 + risk.take_profit)
                if b["high"] >= tp:
                    trigger = ("止盈", tp)
            if trigger is None and risk.max_hold_days > 0 and entry_idx[s] >= 0:
                if i - entry_idx[s] >= risk.max_hold_days:
                    trigger = ("时间止损", b["close"])
            if trigger is None:
                continue
            reason, rp = trigger
            limit_down = _df.is_limit_down(_to_xtcode(s), b.get("close") or rp, prev_close[s])
            sellable = positions[s] if not enable_t1 else min(positions[s], avail[s])
            if limit_down or (enable_t1 and sellable <= 0):
                r2 = "跌停无法卖出" if limit_down else "T+1限制，当日买入不可卖"
                skipped_signals.append({"date": date, "side": "sell",
                                        "reason": "{}（{}）".format(r2, reason)})
                continue
            pnl = _sell_one(s, sellable, _round_tick(rp), date, reason)
            _track_close(pnl, i)

    # ---- 主循环（统一日期 + forward-fill 估值）----
    for i, date in enumerate(all_dates):
        for s in symbols:
            b = date_bars[s].get(date)
            if b:
                last_close[s] = b["close"]
        if enable_t1:
            for s in symbols:
                avail[s] = positions[s]

        # 逐日面板 + 信号
        ctx.symbol = ""
        ctx.barpos = i
        panel = {}
        for s in symbols:
            panel[s] = [date_bars[s][d] for d in all_dates[:i + 1] if d in date_bars[s]]
        ctx.bars_panel = panel
        common_bar = {"date": date, "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}
        sig = strat.on_bar(common_bar, ctx)

        # 撮合口径：close 当日执行 / next_open 次日开盘执行（组合级）
        if fill_model == "next_open":
            targets = pending_targets
            t_reason = pending_reason
            pending_targets = sig.targets if isinstance(sig, PortfolioSignal) else None
            pending_reason = getattr(sig, "reason", "") if isinstance(sig, PortfolioSignal) else ""
        else:
            targets = sig.targets if isinstance(sig, PortfolioSignal) else None
            t_reason = getattr(sig, "reason", "") if isinstance(sig, PortfolioSignal) else ""

        if targets is not None:
            _rebalance(targets, date, i, t_reason)

        _risk_exits(date, i)

        total = cash + sum(positions[s] * (last_close[s] or 0) for s in symbols)
        equity_curve.append({"date": date, "value": round(total, 2)})

        for s in symbols:
            if date_bars[s].get(date):
                prev_close[s] = date_bars[s][date]["close"]

        if i % 10 == 0 or i == len(all_dates) - 1:
            emit_progress(50 + int(45 * (i + 1) / len(all_dates)), 'match',
                          '组合撮合 {} / {}'.format(i + 1, len(all_dates)))

    strat.on_stop(ctx)
    emit_progress(96, 'finish', '计算绩效指标')

    # ---- 绩效指标（组合级）----
    final_value = cash + sum(positions[s] * (last_close[s] or 0) for s in symbols)
    total_return = (final_value - initial_capital) / initial_capital * 100

    days = len(all_dates)
    if days >= 60:
        annual_return = ((final_value / initial_capital) ** (252 / max(days, 1)) - 1) * 100
        annual_return_note = None
    else:
        annual_return = 0.0
        annual_return_note = "回测区间过短（{} 个交易日），年化收益率失真，请参考总收益率".format(days)

    peak = 0
    max_dd = 0
    for pt in equity_curve:
        v = pt["value"]
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak * 100
            if dd < max_dd:
                max_dd = dd

    daily_returns = []
    for j in range(1, len(equity_curve)):
        prev_v = equity_curve[j - 1]["value"]
        curr_v = equity_curve[j]["value"]
        if prev_v > 0:
            daily_returns.append((curr_v - prev_v) / prev_v)
    if daily_returns:
        avg_ret = sum(daily_returns) / len(daily_returns)
        std_ret = math.sqrt(sum((r - avg_ret) ** 2 for r in daily_returns) / len(daily_returns))
        sharpe = (avg_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0
    else:
        sharpe, avg_ret, std_ret = 0, 0.0, 0.0
    annual_vol = std_ret * math.sqrt(252) * 100 if daily_returns else 0
    downside = [r for r in daily_returns if r < 0]
    downside_std = math.sqrt(sum(r * r for r in downside) / len(downside)) if downside else 0
    sortino = (avg_ret / downside_std * math.sqrt(252)) if downside_std > 0 else 0
    calmar = (annual_return / abs(max_dd)) if max_dd < 0 else 0

    sell_trades = [t for t in trades if t["side"] == "sell" and t.get("pnl") is not None]
    wins = [t for t in sell_trades if t["pnl"] > 0]
    losses = [t for t in sell_trades if t["pnl"] <= 0]
    win_rate = (len(wins) / len(sell_trades) * 100) if sell_trades else 0
    avg_win = (sum(t["pnl"] for t in wins) / len(wins)) if wins else 0
    avg_loss = (abs(sum(t["pnl"] for t in losses) / len(losses))) if losses else 1
    profit_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0
    realized_pnl = sum(t.get("pnl") or 0 for t in sell_trades)
    open_pnl = sum((last_close[s] - cost_price[s]) * positions[s] for s in symbols if positions[s] > 0)
    n_open = sum(1 for s in symbols if positions[s] > 0)
    if n_open > 0:
        wins_incl = len(wins) + sum(1 for s in symbols
                                    if positions[s] > 0 and (last_close[s] - cost_price[s]) * positions[s] > 0)
        n_incl = len(sell_trades) + n_open
        win_rate_incl_open = wins_incl / n_incl * 100 if n_incl > 0 else 0
    else:
        win_rate_incl_open = win_rate

    # 基准：等权买入持有（每标的自首根 bar 至末根 bar）
    per_sym_ret = {}
    for s in symbols:
        first = bars_by_symbol[s][0]["close"]
        last = last_close[s] or first
        per_sym_ret[s] = ((last - first) / first * 100) if first > 0 else 0
    benchmark_return = sum(per_sym_ret.values()) / len(symbols)
    excess_return = total_return - benchmark_return

    per_symbol = {}
    for s in symbols:
        per_symbol[s] = {
            "realizedPnl": round(sum(t.get("pnl") or 0 for t in sell_trades if t["symbol"] == s), 2),
            "openPnl": round((last_close[s] - cost_price[s]) * positions[s], 2) if positions[s] > 0 else 0.0,
            "trades": sum(1 for t in trades if t["symbol"] == s),
            "return": round(per_sym_ret[s], 2),
        }

    metrics = {
        "totalReturn": round(total_return, 2),
        "benchmarkReturn": round(benchmark_return, 2),
        "excessReturn": round(excess_return, 2),
        "annualReturn": round(annual_return, 2),
        "annualReturnNote": annual_return_note,
        "maxDrawdown": round(max_dd, 2),
        "sharpeRatio": round(sharpe, 2),
        "sortinoRatio": round(sortino, 2),
        "calmarRatio": round(calmar, 2),
        "annualVolatility": round(annual_vol, 2),
        "avgHoldDays": 0,
        "winRate": round(win_rate, 2),
        "winRateInclOpen": round(win_rate_incl_open, 2),
        "realizedPnl": round(realized_pnl, 2),
        "openPnl": round(open_pnl, 2),
        "profitLossRatio": round(profit_loss_ratio, 2),
        "totalTrades": len(trades),
        "skippedSignals": len(skipped_signals),
        "finalCapital": round(final_value, 2),
    }

    result = {
        "metrics": metrics,
        "trades": trades,
        "equityCurve": equity_curve,
        "skippedSignals": skipped_signals[:100],
        "dataSource": data_source,
        "fillModel": fill_model,
        "risk": risk.summary(),
        "regime": ({
            "enabled": True, "index": regime_index, "fast": regime_fast, "ownMa": regime_own_ma,
            "allowedDays": sum(1 for v in (regime_ok or {}).values() if v),
            "blockedDays": sum(1 for v in (regime_ok or {}).values() if not v),
            "warning": regime_warn,
        } if regime_enabled else None),
        "portfolio": True,
        "symbols": symbols,
        "perSymbol": per_symbol,
        "name": "组合回测",
        "symbol": ", ".join(symbols),
        "bars": [],
    }

    # 结果持久化（组合快照；稳健性子运行不落盘）
    if not params.get("noPersist", False):
        try:
            result_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                      'data', 'backtest_results')
            os.makedirs(result_dir, exist_ok=True)
            bt_id = 'bt_' + str(int(time.time() * 1000))
            snapshot = {
                "id": bt_id,
                "createdAt": time.strftime('%Y-%m-%dT%H:%M:%S'),
                "symbol": ", ".join(symbols),
                "portfolio": True,
                "strategy": strat_cls.name,
                "strategyParams": strategy_params,
                "startDate": start_date,
                "endDate": end_date,
                "period": period,
                "dividendType": dividend_type,
                "initialCapital": initial_capital,
                "commission": commission_rate,
                "slippage": slippage_rate,
                "fillModel": fill_model,
                "allowMock": allow_mock,
                "risk": risk.summary(),
                "dataSource": data_source,
                "metrics": metrics,
            }
            with open(os.path.join(result_dir, bt_id + '.json'), 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)
            result["backtestId"] = bt_id
        except Exception as e:
            log("回测结果持久化失败（不影响本次结果）: {}".format(e))

    # D4.2 稳健性分析：成本/参数/时间切片敏感性（组合路径同样支持）
    if params.get("robustness"):
        result["robustness"] = _run_robustness(params, strategy_type, strategy_params)

    return result


def _results_dir():
    """回测结果目录（data/backtest_results）。"""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'data', 'backtest_results')


def list_backtest_results(limit=50):
    """列出历史回测结果（data/backtest_results/*.json），按时间倒序。

    D4.3 回测历史管理：供 UI 历史列表与 A/B 对比。
    """
    result_dir = _results_dir()
    if not os.path.isdir(result_dir):
        return []
    items = []
    for fn in sorted(os.listdir(result_dir), reverse=True):
        if not fn.endswith('.json'):
            continue
        try:
            with open(os.path.join(result_dir, fn), encoding='utf-8') as f:
                snap = json.load(f)
            items.append({
                "backtestId": snap.get("id") or fn[:-5],
                "createdAt": snap.get("createdAt", ""),
                "symbol": snap.get("symbol", ""),
                "portfolio": bool(snap.get("portfolio")),
                "strategy": snap.get("strategy", ""),
                "startDate": snap.get("startDate", ""),
                "endDate": snap.get("endDate", ""),
                "fillModel": snap.get("fillModel", ""),
                "metrics": snap.get("metrics", {}),
            })
        except Exception:
            continue
        if len(items) >= limit:
            break
    return items


def compare_backtest_results(ids):
    """对比多个历史回测的关键配置与指标（A/B），只返回存在的 id。"""
    result_dir = _results_dir()
    out = []
    for bid in ids or []:
        path = os.path.join(result_dir, str(bid) + '.json')
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding='utf-8') as f:
                snap = json.load(f)
            out.append({
                "backtestId": bid,
                "createdAt": snap.get("createdAt", ""),
                "symbol": snap.get("symbol", ""),
                "portfolio": bool(snap.get("portfolio")),
                "strategy": snap.get("strategy", ""),
                "strategyParams": snap.get("strategyParams", {}),
                "startDate": snap.get("startDate", ""),
                "endDate": snap.get("endDate", ""),
                "fillModel": snap.get("fillModel", ""),
                "risk": snap.get("risk", {}),
                "metrics": snap.get("metrics", {}),
            })
        except Exception:
            continue
    return out


# ----------------------------------------------------------------------
# 稳健性分析（D4.2）：成本/参数/时间切片敏感性
# ----------------------------------------------------------------------
def _run_robustness(params, strategy_type, strategy_params):
    """稳健性分析：成本敏感性 / 参数敏感性 / 时间切片（防过拟合诊断）。

    在统一回测引擎之上以修改后的参数重跑多次（静默、不落盘），
    返回 {cost, params, slices, verdict} 供 UI 展示。
    """
    import copy
    from datetime import datetime

    base = copy.deepcopy(params)
    base['noPersist'] = True
    base.pop('robustness', None)

    report = {}
    global _SILENT_PROGRESS
    _SILENT_PROGRESS = True
    try:
        # ---- 1. 成本敏感性（佣金/印花税/过户费 × 0.5/1/2，滑点不变）----
        cost_rows = []
        for scale in (0.5, 1.0, 2.0):
            p = copy.deepcopy(base)
            p['commission'] = round(float(base.get('commission', 0.0003)) * scale, 6)
            p['stampTax'] = round(float(base.get('stampTax', 0.0005)) * scale, 6)
            p['transferFee'] = round(float(base.get('transferFee', 0.00001)) * scale, 6)
            try:
                r = run_backtest(p)
            except Exception as e:
                log("稳健性[成本x{}]运行失败: {}".format(scale, e))
                continue
            cost_rows.append({
                "scale": "x{}".format(scale),
                "totalReturn": r['metrics']['totalReturn'],
                "annualReturn": r['metrics'].get('annualReturn'),
                "maxDrawdown": r['metrics']['maxDrawdown'],
                "totalTrades": r['metrics']['totalTrades'],
                "winRate": r['metrics'].get('winRate'),
            })
        report['cost'] = cost_rows

        # ---- 2. 参数敏感性（数值参数 ±20%）----
        param_rows = []
        try:
            strat_cls, _sp = _resolve_strategy_type(strategy_type, strategy_params)
            schema = list(getattr(strat_cls, 'params_schema', []) or [])
            for pdef in schema:
                key = pdef.get('key', '')
                if not key:
                    continue
                base_val = strategy_params.get(key, pdef.get('default'))
                if base_val is None:
                    continue
                try:
                    base_val = float(base_val)
                except (TypeError, ValueError):
                    continue
                if base_val == 0:
                    continue
                row = {"param": key, "base": round(base_val, 4), "runs": []}
                for factor in (0.8, 1.0, 1.2):
                    p = copy.deepcopy(base)
                    p['strategy']['params'][key] = round(base_val * factor, 6)
                    try:
                        r = run_backtest(p)
                        row['runs'].append({
                            "factor": "x{}".format(factor),
                            "value": round(base_val * factor, 4),
                            "totalReturn": r['metrics']['totalReturn'],
                            "maxDrawdown": r['metrics']['maxDrawdown'],
                            "totalTrades": r['metrics']['totalTrades'],
                        })
                    except Exception as e:
                        log("稳健性[参数 {} x{}]运行失败: {}".format(key, factor, e))
                if len(row['runs']) == 3:
                    param_rows.append(row)
        except Exception as e:
            log("参数敏感性分析跳过: {}".format(e))
        report['params'] = param_rows

        # ---- 3. 时间切片（区间 ≥120 天时对半）----
        slice_rows = []
        try:
            st, et = base.get('startDate', ''), base.get('endDate', '')
            if st and et:
                d0 = datetime.strptime(st, '%Y-%m-%d')
                d1 = datetime.strptime(et, '%Y-%m-%d')
                if (d1 - d0).days >= 120:
                    mid = d0 + (d1 - d0) / 2
                    bounds = [(st, mid.strftime('%Y-%m-%d')), (mid.strftime('%Y-%m-%d'), et)]
                    for bs, be in bounds:
                        p = copy.deepcopy(base)
                        p['startDate'], p['endDate'] = bs, be
                        try:
                            r = run_backtest(p)
                            slice_rows.append({
                                "slice": "{} ~ {}".format(bs, be),
                                "totalReturn": r['metrics']['totalReturn'],
                                "totalTrades": r['metrics']['totalTrades'],
                                "finalCapital": r['metrics']['finalCapital'],
                            })
                        except Exception as e:
                            log("稳健性[切片 {}]运行失败: {}".format(bs, e))
        except Exception as e:
            log("时间切片分析跳过: {}".format(e))
        report['slices'] = slice_rows
    finally:
        _SILENT_PROGRESS = False

    # ---- 4. 综合结论 ----
    report['verdict'] = _robustness_verdict(cost_rows, param_rows, slice_rows)
    return report


def _robustness_verdict(cost_rows, param_rows, slice_rows):
    """稳健性综合判定（红黄绿三档）。"""
    grade_label = {'green': '稳健', 'yellow': '一般', 'red': '脆弱'}

    # 成本稳健性
    cost_grade, cost_msg = 'yellow', ''
    if len(cost_rows) >= 3:
        base_ret = cost_rows[1]['totalReturn']
        cost2_ret = cost_rows[2]['totalReturn']
        if base_ret < 0:
            cost_grade = 'red'
            cost_msg = '基准收益为负，先改善策略本身（正期望是稳健的前提）'
        elif cost2_ret >= 0:
            cost_grade = 'green'
            cost_msg = '成本翻倍仍正收益，对交易成本不敏感（稳健）'
        else:
            cost_grade = 'yellow'
            cost_msg = '成本翻倍即转负，边际收益薄，对成本敏感'

    # 参数稳健性（邻域正收益占比，不含基准档）
    param_grade, param_msg = 'yellow', ''
    neighborhood = sum(1 for r in param_rows for run in r['runs'] if run['factor'] != 'x1.0')
    if neighborhood > 0:
        positive = sum(1 for r in param_rows for run in r['runs']
                       if run['factor'] != 'x1.0' and run['totalReturn'] > 0)
        frac = positive / neighborhood
        if frac >= 0.6:
            param_grade = 'green'
            param_msg = '参数邻域多数正收益，非参数巧合（稳健）'
        elif frac >= 0.4:
            param_grade = 'yellow'
            param_msg = '参数邻域正负参半，需谨慎对待'
        else:
            param_grade = 'red'
            param_msg = '参数邻域多数亏损，疑似过拟合（警惕）'
    else:
        param_msg = '无数值参数可分析'

    # 时间切片
    slice_grade, slice_msg = 'yellow', ''
    if slice_rows:
        pos = sum(1 for s in slice_rows if s['totalReturn'] > 0)
        frac = pos / len(slice_rows)
        if frac >= 0.5:
            slice_grade = 'green'
            slice_msg = '多数时间段正收益，非单段依赖'
        else:
            slice_grade = 'red'
            slice_msg = '收益集中在部分时间段，稳定性差'
    else:
        slice_msg = '区间过短（<120 天），未做时间切片'

    grades = [cost_grade, param_grade, slice_grade]
    if 'red' in grades:
        overall = 'red'
    elif grades.count('green') >= 2:
        overall = 'green'
    else:
        overall = 'yellow'

    return {
        'overall': overall,
        'summary': '稳健性评级：{}（成本:{} / 参数:{} / 时间:{}）'.format(
            grade_label[overall], grade_label[cost_grade],
            grade_label[param_grade], grade_label[slice_grade]),
        'cost': {'grade': cost_grade, 'msg': cost_msg},
        'params': {'grade': param_grade, 'msg': param_msg},
        'slices': {'grade': slice_grade, 'msg': slice_msg},
    }


# ----------------------------------------------------------------------
# 调度
# ----------------------------------------------------------------------
METHOD_MAP = {
    "ping": lambda p: {"pong": True},
    "backtest.run": run_backtest,
    "backtest.list": lambda p: {"items": list_backtest_results(int(p.get('limit', 50) or 50))},
    "backtest.compare": lambda p: {"items": compare_backtest_results(p.get('ids', []) or [])},
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
