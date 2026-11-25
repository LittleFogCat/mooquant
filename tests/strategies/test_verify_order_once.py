"""下单链路上手策略（data/strategies/user/verify_order_once.py）单测

验证三件事：
  1. 只在配置日期出手，其它日期一律不动（避免它变成天天买入的漏洞）
  2. 出手时带显式 qty 并向下取整到整手（执行器默认按 90% 可用资金下单，
     验证用策略必须自己控量）
  3. 参数缺失/非法时安静返回 None，不抛异常打断信号计算
"""

import importlib.util
import os

import pytest

STRATEGY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "strategies", "user", "verify_order_once.py",
)


def load_cls():
    if not os.path.exists(STRATEGY_FILE):
        pytest.skip("验证策略文件不存在（仅本机验证用）")
    spec = importlib.util.spec_from_file_location("verify_order_once", STRATEGY_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.VerifyOrderOnceStrategy


def make_bars(days):
    return [
        {"date": d, "open": 5.0, "high": 5.1, "low": 4.9, "close": 5.0, "volume": 1000000}
        for d in days
    ]


def last_signal(strat, bars):
    """按 model_server._compute_signal 的方式全量重放，取最后一根的信号。"""
    from strategies.base import Context

    ctx = Context()
    ctx.symbol = "sh601288"
    ctx.is_backtest = False
    ctx.period = "1d"
    signal = None
    for i, bar in enumerate(bars):
        ctx.bars = bars[: i + 1]
        ctx.barpos = i
        signal = strat.on_bar(bar, ctx)
    return signal


DAYS = [f"2026-11-{d:02d}" for d in range(17, 26)]  # 2026-11-17 ~ 2026-11-25


def test_fires_on_target_day_with_lot_rounded_qty():
    cls = load_cls()
    strat = cls({"only_on": "2026-11-26", "qty": 250})
    bars = make_bars(DAYS + ["2026-11-26"])
    sig = last_signal(strat, bars)
    assert sig is not None, "目标日期应出手"
    assert sig.action == "buy"
    assert sig.qty == 200, "250 股应向下取整到 200（整手）"
    assert "verify_only" in (sig.indicators or {})


def test_silent_on_other_days():
    cls = load_cls()
    strat = cls({"only_on": "2026-11-26", "qty": 100})
    # 最后一天不是目标日期 → 不动
    assert last_signal(strat, make_bars(DAYS)) is None
    # 目标日期还没到（历史里出现过但之后还有别的日子）→ 最后一根不是目标日，也不动
    assert last_signal(strat, make_bars(["2026-11-26"] + DAYS)) is None


def test_empty_or_invalid_params_are_silent():
    cls = load_cls()
    assert last_signal(cls({"qty": 100}), make_bars(DAYS + ["2026-11-26"])) is None, "没配日期就不该出手"
    # 股数不足一手 → 取整后为 0 → 不动
    assert last_signal(cls({"only_on": "2026-11-26", "qty": 99}), make_bars(DAYS + ["2026-11-26"])) is None


def test_qty_floor_is_100():
    cls = load_cls()
    # 传 0 / None 时按默认 100 处理，不能变成 0 股或负数
    for qty in (0, None):
        sig = last_signal(cls({"only_on": "2026-11-26", "qty": qty}), make_bars(DAYS + ["2026-11-26"]))
        assert sig is not None and sig.qty == 100, f"qty={qty} 时应用默认 100 股，实际 {getattr(sig, 'qty', None)}"
