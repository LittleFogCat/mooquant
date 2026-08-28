# -*- coding: utf-8 -*-
"""mookquant · 统一风控层（D2.1/D2.2）

回测引擎与实盘执行器共用同一套风控逻辑，避免「回测有风控、实盘裸奔」。

职责：
  - 退出信号：止损（成本价×比例）、止盈、时间止损（持有 N 个交易日强制退出）
  - 熔断：连续亏损达到阈值后冻结开新仓 N 个交易日
  - 仓位：单笔最大仓位比例（占总资产），可选按信号 strength 缩放

约定：
  - 纯逻辑、无副作用、不依赖行情源；由调用方（撮合循环/执行器）逐 bar 驱动
  - 所有退出/熔断参数默认关闭（=0），保持既有回测行为兼容
  - 参数命名与回测 params 一致（stopLoss/takeProfit/maxHoldDays/
    maxConsecLosses/cooldownDays/maxPositionPct/strengthScaling）
"""


class RiskController:
    """统一风控控制器（无状态输入，由调用方驱动）。"""

    def __init__(self, config=None):
        config = config or {}
        # 退出
        self.stop_loss = float(config.get('stopLoss', 0) or 0)          # 止损比例，0=关闭
        self.take_profit = float(config.get('takeProfit', 0) or 0)      # 止盈比例，0=关闭
        self.max_hold_days = int(config.get('maxHoldDays', 0) or 0)     # 时间止损，0=关闭
        # 熔断
        self.max_consec_losses = int(config.get('maxConsecLosses', 0) or 0)  # 0=关闭
        self.cooldown_days = int(config.get('cooldownDays', 5) or 5)    # 熔断冻结天数
        # 仓位
        self.max_position_pct = float(config.get('maxPositionPct', 1.0) or 1.0)
        self.strength_scaling = bool(config.get('strengthScaling', False))
        # 运行状态
        self.consec_losses = 0        # 当前连续亏损次数
        self.cooldown_until = -1      # 该 bar index 之前禁止开新仓
        self.entry_index = -1         # 当前持仓建仓 bar index（-1=空仓）

    # ------------------------------------------------------------------
    # 退出判定（止损/止盈/时间止损）
    # ------------------------------------------------------------------
    def exit_trigger(self, bar, cost_price, index):
        """返回强制退出 (reason, fill_price) 或 None。

        - 止损：当日 low 触及 成本×(1-stopLoss) → 按触发价成交（市场惯例）
        - 止盈：当日 high 触及 成本×(1+takeProfit) → 按触发价成交
        - 时间止损：持仓交易日数 >= maxHoldDays → 按当日收盘价成交
        """
        if cost_price <= 0 or self.entry_index < 0:
            return None
        low = bar.get('low')
        high = bar.get('high')
        close = bar.get('close')
        if self.stop_loss > 0 and low is not None:
            stop_price = cost_price * (1 - self.stop_loss)
            if low <= stop_price:
                return ('止损', stop_price)
        if self.take_profit > 0 and high is not None:
            tp_price = cost_price * (1 + self.take_profit)
            if high >= tp_price:
                return ('止盈', tp_price)
        if self.max_hold_days > 0:
            hold_days = index - self.entry_index
            if hold_days >= self.max_hold_days and close is not None:
                return ('时间止损', close)
        return None

    # ------------------------------------------------------------------
    # 开仓准入（连续亏损熔断）
    # ------------------------------------------------------------------
    def can_open(self, index):
        """是否允许开新仓（熔断冻结期内禁止）。"""
        if self.max_consec_losses > 0 and index <= self.cooldown_until:
            return False
        return True

    def mark_entry(self, index):
        """记录建仓 bar index（从空仓变为持仓时调用）。"""
        self.entry_index = index

    def mark_close(self, pnl, index):
        """持仓全部平仓后更新熔断状态（pnl 为已实现盈亏）。"""
        self.entry_index = -1
        if self.max_consec_losses <= 0 or pnl is None:
            return
        if pnl < 0:
            self.consec_losses += 1
            if self.consec_losses >= self.max_consec_losses:
                self.cooldown_until = index + self.cooldown_days
        else:
            self.consec_losses = 0

    # ------------------------------------------------------------------
    # 仓位
    # ------------------------------------------------------------------
    def position_pct(self, strength=1.0):
        """有效仓位比例 = maxPositionPct ×（可选）strength 缩放，clip 到 [0,1]。"""
        pct = self.max_position_pct
        if self.strength_scaling:
            try:
                s = float(strength)
            except (TypeError, ValueError):
                s = 1.0
            pct *= max(0.0, min(1.0, s))
        return max(0.0, min(1.0, pct))

    def target_value(self, total_asset, strength=1.0):
        """目标持仓市值 = 总资产 × 有效仓位比例。"""
        return total_asset * self.position_pct(strength)

    # ------------------------------------------------------------------
    # 报告（供 UI/日志/快照）
    # ------------------------------------------------------------------
    def summary(self):
        return {
            'stopLoss': self.stop_loss,
            'takeProfit': self.take_profit,
            'maxHoldDays': self.max_hold_days,
            'maxConsecLosses': self.max_consec_losses,
            'cooldownDays': self.cooldown_days,
            'maxPositionPct': self.max_position_pct,
            'strengthScaling': self.strength_scaling,
        }


# ---------------------------------------------------------------------------
# 市场状态过滤（D2.3，regime filter）
# ---------------------------------------------------------------------------

def regime_mask(bars, index_bars, fast=20, use_own_ma=False):
    """计算逐日市场状态掩码：{date: 该日是否允许做多}。

    规则（趋势过滤，避免在下跌市中做多被反复收割）：
      - 指数收盘价 >= 指数 MA(fast)（市场趋势向上）才允许做多
      - use_own_ma=True 时叠加：个股自身收盘价 >= 自身 MA(fast)
      - 指数数据不足（< fast 根）→ 返回 (None, 警告文案)，由调用方决定降级
      - 指数缺某交易日数据 → 该日保守置 False（禁止做多）

    Args:
        bars: 个股日 K（含 date/close）
        index_bars: 指数日 K（含 date/close）
        fast: 均线周期（默认 20 日）
        use_own_ma: 是否同时要求个股站上自身均线

    Returns:
        (mask_or_None, warn_or_None)
    """
    from strategies.indicators import sma

    if not index_bars or len(index_bars) < fast:
        return None, "指数数据不足（{} 根 < MA{} 所需），市场状态过滤已降级为不生效".format(
            len(index_bars or []), fast)

    idx_dates = [str(b['date'])[:10] for b in index_bars]
    idx_closes = [float(b['close']) for b in index_bars]
    idx_ma = sma(idx_closes, fast)
    idx_ok = {}
    for d, c, m in zip(idx_dates, idx_closes, idx_ma):
        idx_ok[d] = (m is not None) and c >= m

    own_ok = None
    if use_own_ma:
        own_dates = [str(b['date'])[:10] for b in bars]
        own_closes = [float(b['close']) for b in bars]
        own_ma = sma(own_closes, fast)
        own_ok = {}
        for d, c, m in zip(own_dates, own_closes, own_ma):
            own_ok[d] = (m is not None) and c >= m

    mask = {}
    for b in bars:
        d = str(b['date'])[:10]
        idx_state = idx_ok.get(d)
        if idx_state is None:
            mask[d] = False  # 指数无该日数据 → 保守禁止做多
            continue
        if own_ok is not None:
            mask[d] = idx_state and own_ok.get(d, False)
        else:
            mask[d] = idx_state
    return mask, None
