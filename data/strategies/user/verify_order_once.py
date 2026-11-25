"""
mookquant * 下单链路上手策略（验证用）

目的：把「执行器 → 风控 → 交易服务 → 适配器 → 成交回查」整条自动下单链路真的跑通一次。
      （面对真实盘口，只有真正下过单才知道这一段有没有问题）

行为：只有在配置的日期（参数 only_on）且当日日线 bar 存在时，买入固定股数（参数 qty，
      默认 100 股）。其它日期一律不动，所以它只会产生**一笔小额委托**，不会反复刷单。

为什么不用「无持仓就买」：实盘信号计算走 model_server 的 `_compute_signal`，
它每次把历史 bars 全量重放一遍，`ctx.position` 并不注入，策略无法感知持仓。
因此用「指定日期 + 固定股数」做确定性的单次触发，比依赖持仓状态更可靠。

配合的策略实例建议（见 data/strategies/*.json）：
  schedule: {period: "1d", at: ["09:35"]}
  risk: {maxDailyTrades: 1, maxPositionRatio: 0.01}

验证完成后请停用/删除对应策略实例，它不是交易策略。
"""

from strategies.base import StrategyBase, Signal


class VerifyOrderOnceStrategy(StrategyBase):
    """在指定日期买入固定股数，仅用于验证自动下单链路。"""

    name = "verify_order_once"
    display_name = "下单链路上手（仅验证）"
    description = "在指定交易日买入固定股数，验证自动下单链路；其它日期不动"
    version = "1.0"
    trigger_mode = "bar"
    params_schema = [
        {"key": "only_on", "type": "str", "default": "", "label": "仅在该日期生效（YYYY-MM-DD）"},
        {"key": "qty", "type": "int", "default": 100, "min": 100, "max": 1000, "label": "买入股数"},
    ]

    def on_bar(self, bar, ctx):
        only_on = str(self.params.get("only_on") or "").strip()
        if not only_on:
            return None
        # 只在最后给出的那个交易日的 bar 上出手（model_server 会重放全部历史，
        # 非当日的 bar 直接忽略）
        day = str(bar.get("date", ""))[:10]
        if day != only_on:
            return None

        raw = self.params.get("qty")
        qty = int(raw) if raw not in (None, "") else 100
        if qty <= 0:
            qty = 100  # 未配置/非法值 → 用默认 100 股，而不是变成 0 股
        # A 股整手：向下取整到 100 股；不足一手则不出手
        qty = (qty // 100) * 100
        if qty <= 0:
            return None

        return Signal(
            action="buy",
            reason="链路上手：验证自动下单（{}）".format(day),
            qty=qty,
            indicators={"verify_only": True, "day": day},
        )
