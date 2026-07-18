"""
mookquant * 动量策略

N 日涨幅超过阈值买入，低于负阈值卖出。
迁移自 backtest_engine.py 的 strategy_momentum，信号逻辑完全一致。
"""

from strategies.base import StrategyBase, Signal


class MomentumStrategy(StrategyBase):
    """动量策略。"""

    name = "momentum"
    display_name = "动量突破"
    description = "N日涨幅超阈值买入，跌破负阈值卖出"
    version = "1.0"
    trigger_mode = "bar"
    params_schema = [
        {"key": "lookback", "type": "int", "default": 10, "min": 1, "max": 120, "label": "回看周期"},
        {"key": "threshold", "type": "float", "default": 0.03, "min": 0, "max": 1, "label": "触发阈值"},
    ]

    def on_bar(self, bar, ctx):
        bars = ctx.bars
        lookback = int(self.params.get("lookback", 10))
        threshold = float(self.params.get("threshold", 0.03))
        closes = [b["close"] for b in bars]

        # 需要 lookback+1 根才能算 N 日涨幅
        if len(closes) < lookback + 1:
            return None

        base = closes[-1 - lookback]
        if base == 0:
            return None
        ret = (closes[-1] - base) / base

        if ret > threshold:
            return Signal(action="buy", reason="动量突破 {:.2%}".format(ret),
                          indicators={"return": round(ret, 4)})
        if ret < -threshold:
            return Signal(action="sell", reason="动量回落 {:.2%}".format(ret),
                          indicators={"return": round(ret, 4)})
        return None
