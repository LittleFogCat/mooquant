"""
mookquant * 均值回归策略

价格偏离均线超过阈值时反向操作：跌破均线买入，超过均线卖出。
迁移自 backtest_engine.py 的 strategy_mean_reversion，信号逻辑完全一致。
"""

from strategies.base import StrategyBase, Signal
from strategies.indicators import sma


class MeanReversionStrategy(StrategyBase):
    """均值回归策略。"""

    name = "mean_reversion"
    display_name = "均值回归"
    description = "价格偏离均线超阈值时反向操作"
    version = "1.0"
    trigger_mode = "bar"
    params_schema = [
        {"key": "period", "type": "int", "default": 20, "min": 2, "max": 250, "label": "均线周期"},
        {"key": "deviation", "type": "float", "default": 0.03, "min": 0, "max": 1, "label": "偏离阈值"},
    ]

    def on_bar(self, bar, ctx):
        bars = ctx.bars
        period = int(self.params.get("period", 20))
        deviation = float(self.params.get("deviation", 0.03))
        closes = [b["close"] for b in bars]

        if len(closes) < period + 1:
            return None

        ma_series = sma(closes, period)
        ma_now = ma_series[-1]
        if ma_now is None or ma_now == 0:
            return None

        diff = (closes[-1] - ma_now) / ma_now

        # 跌破均值：买入
        if diff < -deviation:
            return Signal(action="buy", reason="跌破均值 {:.2%}".format(diff),
                          indicators={"ma": round(ma_now, 4), "deviation": round(diff, 4)})
        # 超过均值：卖出
        if diff > deviation:
            return Signal(action="sell", reason="超过均值 {:.2%}".format(diff),
                          indicators={"ma": round(ma_now, 4), "deviation": round(diff, 4)})
        return None
