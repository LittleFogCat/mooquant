# -*- coding: utf-8 -*-
"""稳健性分析（D4.2）测试：成本敏感性 / 参数敏感性 / 时间切片 / 综合判定。

在统一回测引擎之上跑多次（静默、不落盘），全部用 monkeypatch 隔离真实数据源。
"""
import math
import pytest

from backtest_engine import run_backtest


def _trend(n=120):
    """趋势 + 正弦波动的确定性日 K（momentum 可产生大量交易）。"""
    out = []
    price = 10.0
    for i in range(n):
        wave = 0.015 * math.sin(i / 6.0)
        price = price * (1 + 0.002 + wave)
        out.append({
            "date": "2024-{:02d}-{:02d}".format(i // 27 + 1, i % 27 + 1),
            "open": round(price / 1.004, 2), "high": round(price * 1.01, 2),
            "low": round(price * 0.99, 2), "close": round(price, 2), "volume": 1e7,
        })
    return out


BARS = _trend(120)


def _run(**overrides):
    import backtest_engine as be
    be.fetch_real_bars = lambda *a, **k: (BARS, None)
    be._fetch_stock_name = lambda *a: "测试股"
    params = {
        "strategy": {"type": "momentum", "params": {"lookback": 5, "threshold": 0.005}},
        "symbols": ["sh600000"],
        "startDate": "2024-01-01", "endDate": "2024-06-30",
        "initialCapital": 1000000, "commission": 0.0003, "slippage": 0.001,
        "fillModel": "next_open",
        "robustness": True,
    }
    params.update(overrides)
    return run_backtest(params)


@pytest.fixture(scope="module")
def robust():
    """稳健性分析结果（只跑一次，供多个用例复用）。"""
    return _run()


def test_robustness_report_structure(robust):
    """D4.2：稳健性报告结构齐全。"""
    rb = robust["robustness"]
    assert set(rb.keys()) == {"cost", "params", "slices", "verdict"}
    # 成本三档
    assert len(rb["cost"]) == 3
    assert {c["scale"] for c in rb["cost"]} == {"x0.5", "x1.0", "x2.0"}
    for c in rb["cost"]:
        assert "totalReturn" in c and "totalTrades" in c and "maxDrawdown" in c
    # 综合判定
    assert rb["verdict"]["overall"] in ("green", "yellow", "red")
    assert rb["verdict"]["summary"]
    for k in ("cost", "params", "slices"):
        assert k in rb["verdict"] and "grade" in rb["verdict"][k]


def test_robustness_cost_monotonic(robust):
    """D4.2：成本越高收益越低（单调不增）。"""
    rets = [c["totalReturn"] for c in robust["robustness"]["cost"]]
    assert rets[0] >= rets[1] >= rets[2]


def test_robustness_param_neighborhood(robust):
    """D4.2：数值策略参数做 ±20% 邻域扫描。"""
    params = robust["robustness"]["params"]
    keys = {p["param"] for p in params}
    assert "lookback" in keys and "threshold" in keys
    for p in params:
        assert len(p["runs"]) == 3
        assert {x["factor"] for x in p["runs"]} == {"x0.8", "x1.0", "x1.2"}


def test_robustness_slices_when_long_range(robust):
    """D4.2：区间跨度 >= 120 天时做时间切片。"""
    assert len(robust["robustness"]["slices"]) == 2
    for s in robust["robustness"]["slices"]:
        assert "slice" in s and "totalReturn" in s


def test_robustness_off_by_default():
    """D4.2：未开启时结果不带 robustness 字段（零开销）。"""
    res = _run(robustness=False)
    assert "robustness" not in res
    assert "metrics" in res


def test_robustness_main_result_intact(robust):
    """D4.2：主结果正常（含持久化 backtestId），稳健性子运行不影响主结果。"""
    assert "metrics" in robust
    assert robust["backtestId"], "主结果应正常持久化"
