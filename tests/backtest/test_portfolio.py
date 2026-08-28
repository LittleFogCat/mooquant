# -*- coding: utf-8 -*-
"""组合回测（D2.4）测试：组合等权策略 + 组合撮合路径 + regime 门控 + 单标的限制。

全部用 monkeypatch 隔离真实数据源，数据确定性构造。
"""
import pytest

from backtest_engine import run_backtest
from strategies.base import PortfolioSignal


def _bars(up=True):
    """10 根日 K：up=True 上涨（10→~12），up=False 下跌（10→~9）。"""
    out = []
    price = 10.0
    factor = 1.02 if up else 0.99
    for i in range(10):
        price *= factor
        out.append({"date": "2024-01-{:02d}".format(i + 1),
                    "open": round(price / 1.001, 2), "high": round(price * 1.01, 2),
                    "low": round(price * 0.99, 2), "close": round(price, 2),
                    "volume": 1e7})
    return out


BARS = {"sh600000": _bars(True), "sh600001": _bars(False)}


def _run(**overrides):
    import backtest_engine as be
    be.fetch_real_bars = lambda sym, *a, **k: (BARS.get(sym, []), None if sym in BARS else "无数据")
    be._fetch_stock_name = lambda *a: "测试股"
    params = {
        "strategy": {"type": "portfolio_equal_weight",
                     "params": {"rebalanceDays": 5, "rebalanceDrift": 0}},
        "symbols": ["sh600000", "sh600001"],
        "startDate": "2024-01-01", "endDate": "2024-01-31",
        "initialCapital": 1000000, "commission": 0.0003, "slippage": 0.001,
        "fillModel": "close",
    }
    params.update(overrides)
    return run_backtest(params)


# ---------------------------------------------------------------------------
# 组合等权策略单元测试
# ---------------------------------------------------------------------------

def test_equal_weight_strategy_returns_portfolio_signal():
    from strategies.registry import load_all, get as get_strategy
    from strategies.base import Context
    load_all()
    cls = get_strategy("portfolio_equal_weight")
    strat = cls({"rebalanceDays": 5})
    ctx = Context()
    ctx.universe = ["sh600000", "sh600001"]
    ctx.barpos = 0
    ctx.bars_panel = {s: [{"date": "2024-01-01", "close": 10.0}] for s in ("sh600000", "sh600001")}
    sig = strat.on_bar({"date": "2024-01-01"}, ctx)
    assert isinstance(sig, PortfolioSignal)
    assert set(sig.targets) == {"sh600000", "sh600001"}
    assert abs(sig.targets["sh600000"] - 0.5) < 1e-9
    # 非再平衡日返回 None
    ctx.barpos = 2
    assert strat.on_bar({"date": "2024-01-03"}, ctx) is None


# ---------------------------------------------------------------------------
# 组合撮合路径集成测试
# ---------------------------------------------------------------------------

def test_portfolio_initial_allocates_equal_weight():
    """D2.4：首个交易日按等权建仓两只标的。"""
    res = _run()
    assert res.get("portfolio") is True
    assert res["symbols"] == ["sh600000", "sh600001"]
    first_day = [t for t in res["trades"] if t["side"] == "buy" and t["date"] == "2024-01-01"]
    assert len(first_day) == 2, "首日应对两只标的同时建仓"
    for t in first_day:
        # 等权 ≈ 50 万（留整手/成本余量）
        assert 450_000 <= t["amount"] <= 550_000, t
    assert len(res["equityCurve"]) == 10


def test_portfolio_rebalances():
    """D2.4：rebalanceDays=5 时除初始建仓外还有再平衡（价格漂移触发卖出/补仓）。"""
    res = _run()
    buys = [t for t in res["trades"] if t["side"] == "buy"]
    sells = [t for t in res["trades"] if t["side"] == "sell"]
    assert len(buys) >= 3, "应有初始建仓 + 再平衡买入"
    assert len(sells) >= 1, "上涨标的超配应触发卖出再平衡"
    assert all(t.get("symbol") in ("sh600000", "sh600001") for t in res["trades"])


def test_portfolio_metrics_and_contributions():
    """D2.4：组合级指标齐全，分标的贡献符合涨跌方向。"""
    res = _run()
    m = res["metrics"]
    for k in ("totalReturn", "benchmarkReturn", "excessReturn", "annualReturn",
              "maxDrawdown", "sharpeRatio", "winRateInclOpen", "totalTrades", "finalCapital"):
        assert k in m, "缺少指标 " + k
    assert res["perSymbol"]["sh600000"]["return"] > 0  # A 上涨
    assert res["perSymbol"]["sh600001"]["return"] < 0  # B 下跌
    assert m["finalCapital"] > 0


def test_portfolio_single_strategy_multi_symbol_raises():
    """单标的策略 + 多标的 → 明确报错（不静默跑第一个）。"""
    import backtest_engine as be
    be.fetch_real_bars = lambda sym, *a, **k: (BARS.get(sym, []), None)
    with pytest.raises(ValueError) as ei:
        run_backtest({
            "strategy": {"type": "ma_cross", "params": {}},
            "symbols": ["sh600000", "sh600001"],
            "startDate": "2024-01-01", "endDate": "2024-01-31",
            "initialCapital": 1000000, "fillModel": "close",
        })
    assert "单标的" in str(ei.value) or "组合策略" in str(ei.value)


def test_portfolio_regime_falling_blocks_all(monkeypatch):
    """D2.3+D2.4：下跌市下组合建仓全部被 regime 阻断。"""
    import backtest_engine as be
    import data.datafeed as df
    from datetime import date, timedelta

    def idx_bars():
        out, d, n = [], date(2024, 1, 1), 0
        while len(out) < 25:
            if d.weekday() < 5:
                out.append({"date": d.isoformat(), "close": 3000 - 8 * n})
                n += 1
            d += timedelta(days=1)
        return out

    monkeypatch.setattr(be, "fetch_real_bars",
                        lambda sym, *a, **k: (BARS.get(sym, []), None if sym in BARS else "无数据"))
    monkeypatch.setattr(be, "_fetch_stock_name", lambda *a: "测试股")
    monkeypatch.setattr(df, "fetch_bars", lambda symbol, *a, **k: (idx_bars(), "xtquant"))

    res = run_backtest({
        "strategy": {"type": "portfolio_equal_weight", "params": {}},
        "symbols": ["sh600000", "sh600001"],
        "startDate": "2024-01-01", "endDate": "2024-01-31",
        "initialCapital": 1000000, "fillModel": "close",
        "regimeEnabled": True, "regimeIndex": "000300.SH", "regimeFast": 20,
    })
    assert not any(t["side"] == "buy" for t in res["trades"]), "下跌市应阻断全部建仓"
    skips = [s for s in res.get("skippedSignals", []) if "市场状态过滤" in s.get("reason", "")]
    assert skips, "应有市场状态过滤跳过记录"
    assert res["regime"]["blockedDays"] > 0
