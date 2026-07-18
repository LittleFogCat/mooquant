"""
mookquant * 双均线交叉策略

快线上穿慢线（金叉）买入，下穿（死叉）卖出。
迁移自 backtest_engine.py 的 strategy_ma_cross，信号逻辑完全一致。

增量等价说明：
  原批量版对每个 i 检查 ma[i-1] vs ma[i]；
  增量版 on_bar 时 ctx.bars 到当前为止，取序列 [-2]（上一根）与 [-1]（当前）
  比较，等价于原版在 i = len-1 处的判断。
"""

from strategies.base import StrategyBase, Signal
from strategies.indicators import sma


class MaCrossStrategy(StrategyBase):
    """双均线交叉策略。"""

    name = "ma_cross"
    display_name = "双均线交叉"
    description = "快线上穿慢线买入，下穿卖出"
    version = "1.0"
    trigger_mode = "bar"
    params_schema = [
        {"key": "fast", "type": "int", "default": 5, "min": 1, "max": 60, "label": "快线周期"},
        {"key": "slow", "type": "int", "default": 20, "min": 2, "max": 250, "label": "慢线周期"},
    ]

    def on_bar(self, bar, ctx):
        bars = ctx.bars
        fast = int(self.params.get("fast", 5))
        slow = int(self.params.get("slow", 20))
        closes = [b["close"] for b in bars]

        # 需要 slow+1 根才能比较上一根与当前根的均线
        if len(closes) < slow + 1:
            return None

        ma_fast = sma(closes, fast)
        ma_slow = sma(closes, slow)

        # 上一根 / 当前根均线值
        f_prev, f_now = ma_fast[-2], ma_fast[-1]
        s_prev, s_now = ma_slow[-2], ma_slow[-1]
        if None in (f_prev, f_now, s_prev, s_now):
            return None

        # 金叉：快线由下穿上
        if f_prev <= s_prev and f_now > s_now:
            return Signal(action="buy", reason="金叉",
                          indicators={"ma_fast": round(f_now, 4), "ma_slow": round(s_now, 4)})
        # 死叉：快线由上穿下
        if f_prev >= s_prev and f_now < s_now:
            return Signal(action="sell", reason="死叉",
                          indicators={"ma_fast": round(f_now, 4), "ma_slow": round(s_now, 4)})
        return None
