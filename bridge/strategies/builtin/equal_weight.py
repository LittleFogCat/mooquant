# -*- coding: utf-8 -*-
"""mookquant * 组合等权轮动策略（D2.4）

对标的篮子按等权配置，定期（每 N 个交易日）再平衡；持仓偏离目标超过阈值时提前再平衡。
返回 PortfolioSignal（{symbol: target_pct}），由回测引擎组合撮合路径执行。

is_portfolio = True：引擎据此将多标的回测分发到组合回测路径（_run_portfolio_backtest）。
"""

from strategies.base import StrategyBase, PortfolioSignal


class EqualWeightStrategy(StrategyBase):
    """组合等权轮动：等权持有篮子，定期/偏离触发再平衡。"""

    name = "portfolio_equal_weight"
    display_name = "组合等权轮动"
    description = "对标的篮子按等权配置，每 N 个交易日再平衡；偏离目标超阈值时提前再平衡"
    version = "1.0"
    trigger_mode = "bar"
    is_portfolio = True
    params_schema = [
        {"key": "rebalanceDays", "type": "int", "default": 5, "min": 1, "max": 60,
         "label": "再平衡周期", "description": "每 N 个交易日进行一次等权再平衡"},
        {"key": "rebalanceDrift", "type": "float", "default": 0.05, "min": 0, "max": 1,
         "label": "偏离阈值", "description": "任一个股权重偏离目标超过该比例时提前再平衡（0=关闭）"},
    ]

    def on_bar(self, bar, ctx):
        symbols = getattr(ctx, "universe", None) or []
        panel = getattr(ctx, "bars_panel", None) or {}
        symbols = [s for s in symbols if panel.get(s)]
        if not symbols:
            return None
        i = int(getattr(ctx, "barpos", 0) or 0)
        days = max(1, int(self.params.get("rebalanceDays", 5) or 5))
        drift = float(self.params.get("rebalanceDrift", 0.05) or 0.05)
        target = 1.0 / len(symbols)

        if i == 0:
            return PortfolioSignal(targets={s: target for s in symbols}, reason="初始建仓")
        if i % days == 0:
            return PortfolioSignal(targets={s: target for s in symbols}, reason="定期再平衡")
        if drift > 0:
            cur = self._current_weights(panel, symbols)
            if any(abs(cur.get(s, 0.0) - target) > drift for s in symbols):
                return PortfolioSignal(targets={s: target for s in symbols}, reason="偏离阈值再平衡")
        return None

    @staticmethod
    def _current_weights(panel, symbols):
        """按最新收盘价计算当前权重（用于偏离检测）。"""
        values = {}
        for s in symbols:
            bars = panel.get(s) or []
            if bars:
                values[s] = float(bars[-1]["close"])
        total = sum(values.values())
        if total <= 0:
            return {}
        return {s: v / total for s, v in values.items()}
