"""backtest_engine 回测引擎单元测试。

覆盖回测的完整流水线：真实/模拟数据获取 -> 策略信号 -> 交易撮合 -> 净值/绩效指标。
使用 monkeypatch 隔离 xtquant 与数据库缓存，保证测试确定性、不依赖外部数据源。
"""
import pytest

from backtest_engine import run_backtest
from _shared import generate_mock_bars


@pytest.fixture
def real_bars():
    """一段确定性日 K（80 根波动数据），保证 ma_cross 金叉/死叉触发交易。

    用正弦叠加趋势构造涨跌交替，让 fast=3 / slow=8 均线多次交叉。
    """
    import math
    out = []
    price = 10.0
    for i in range(80):
        # 趋势 + 正弦波动
        trend = 0.002
        wave = 0.015 * math.sin(i / 4.0)
        price = price * (1 + trend + wave)
        out.append({
            "date": f"2024-01-{(i % 27) + 1:02d}",
            "open": round(price / 1.004, 2),
            "high": round(price * 1.01, 2),
            "low": round(price * 0.99, 2),
            "close": round(price, 2),
            "volume": 1000000 + i * 10000,
        })
    return out


def _make_params(**overrides):
    base = {
        "strategy": {"type": "ma_cross", "params": {"fast": 3, "slow": 8}},
        "symbols": ["sh600000"],
        "startDate": "2024-01-01",
        "endDate": "2024-02-29",
        "initialCapital": 1000000,
        "commission": 0.0003,
        "slippage": 0.001,
    }
    base.update(overrides)
    return base


def test_backtest_produces_trades_with_ma_cross(monkeypatch, real_bars):
    """波动数据 + ma_cross 应产生至少一笔买入交易，且交易/净值/指标齐全。"""
    import backtest_engine
    monkeypatch.setattr(backtest_engine, "fetch_real_bars", lambda *a, **k: (real_bars, None))
    monkeypatch.setattr(backtest_engine, "_fetch_stock_name", lambda *a: "测试股")

    res = run_backtest(_make_params(strategy={"type": "ma_cross", "params": {"fast": 3, "slow": 8}}))
    assert res["dataSource"] == "real"
    assert len(res["trades"]) > 0, "波动数据下 ma_cross 应产生交易"
    assert any(t["side"] == "buy" for t in res["trades"])
    assert len(res["equityCurve"]) == len(real_bars)
    m = res["metrics"]
    assert m["totalTrades"] == len(res["trades"])
    assert m["finalCapital"] > 0
    assert "totalReturn" in m and "maxDrawdown" in m and "sharpeRatio" in m


def test_backtest_uses_mock_when_no_real_data(monkeypatch):
    """xtquant 无数据时回退模拟数据，且回测仍可运行。"""
    import backtest_engine
    monkeypatch.setattr(backtest_engine, "fetch_real_bars", lambda *a, **k: (None, "无历史数据"))

    res = run_backtest(_make_params())
    assert res["dataSource"] == "mock"
    assert len(res["bars"]) >= 5
    assert len(res["equityCurve"]) >= 5


def test_backtest_empty_symbols_raises():
    """无标的应报错。"""
    with pytest.raises(ValueError):
        run_backtest(_make_params(symbols=[]))


def test_backtest_insufficient_bars_raises(monkeypatch):
    """数据不足 5 根应报错。"""
    import backtest_engine
    short = [{"date": "2024-01-01", "open": 1, "high": 1.1, "low": 0.9, "close": 1, "volume": 100}]
    monkeypatch.setattr(backtest_engine, "fetch_real_bars", lambda *a, **k: (short, None))
    with pytest.raises(ValueError):
        run_backtest(_make_params())


def test_backtest_unknown_strategy_raises(monkeypatch, real_bars):
    """未知策略类型应明确报错（不静默回落）。"""
    import backtest_engine
    monkeypatch.setattr(backtest_engine, "fetch_real_bars", lambda *a, **k: (real_bars, None))
    with pytest.raises(ValueError) as ei:
        run_backtest(_make_params(strategy={"type": "no_such_strategy", "params": {}}))
    assert "未知策略类型" in str(ei.value) or "no_such_strategy" in str(ei.value)


def test_backtest_multi_symbol_raises(monkeypatch, real_bars):
    """当前仅支持单标的。"""
    import backtest_engine
    monkeypatch.setattr(backtest_engine, "fetch_real_bars", lambda *a, **k: (real_bars, None))
    with pytest.raises(ValueError) as ei:
        run_backtest(_make_params(symbols=["sh600000", "sh600001"]))
    assert "单标的" in str(ei.value)


def test_backtest_shell_without_model_raises(monkeypatch, real_bars):
    """壳策略未绑定/未激活模型时应报错。"""
    import backtest_engine
    monkeypatch.setattr(backtest_engine, "fetch_real_bars", lambda *a, **k: (real_bars, None))
    # 无激活模型
    from strategies.ml.base import MLStrategyBase
    monkeypatch.setattr(MLStrategyBase, "_get_active_model_id", staticmethod(lambda: ""))
    with pytest.raises(ValueError) as ei:
        run_backtest(_make_params(strategy={"type": "shell", "params": {}}))
    assert "壳策略" in str(ei.value) or "模型" in str(ei.value)


def test_backtest_shell_with_model_resolves(monkeypatch, real_bars, tmp_model_dir):
    """壳策略绑定模型后应解析为具体 ML 策略并运行（训练一个临时模型验证链路）。"""
    import backtest_engine
    from training.trainer import Trainer, MockDataFetcher
    from training.model_registry import ModelRegistry

    # 在 tmp 模型目录里快速训练一个 lstm 模型
    t = Trainer(MockDataFetcher())
    result = t.train({
        'data': {'symbols': ['test'], 'period': '1d', 'count': 100},
        'feature_config': {'window': 5, 'raw_features': ['close'], 'normalize': 'zscore',
                           'normalize_mode': 'global'},
        'label_config': {'type': 'classification', 'horizon': 3, 'threshold': 0.01},
        'model_arch': 'lstm',
        'model_params': {'hidden_size': 8, 'num_layers': 1},
        'train_config': {'epochs': 2, 'batch_size': 8, 'learning_rate': 0.01},
        'model_name': 'shell_test',
    })
    mid = result['model_id']
    monkeypatch.setattr(backtest_engine, "fetch_real_bars", lambda *a, **k: (real_bars, None))
    monkeypatch.setattr(backtest_engine, "_fetch_stock_name", lambda *a: "测试股")
    res = run_backtest(_make_params(strategy={"type": "shell", "params": {"model_id": mid, "margin": 0.01}}))
    # ML 策略可能不交易，但至少不抛错且结构完整
    assert res["dataSource"] == "real"
    assert "metrics" in res and "trades" in res
