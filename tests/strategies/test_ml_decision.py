# -*- coding: utf-8 -*-
"""D3.5 期望收益决策测试：概率 × 期望幅度 − 成本的触发语义。"""
import pytest

from strategies.ml.base import expected_value_decision


def test_ev_buy_when_dominant_and_covered():
    action, ev, detail = expected_value_decision(0.7, 0.1, 0.2, up_return=0.02,
                                                 down_return=0.02, roundtrip_cost=0.003)
    assert action == 'buy'
    assert ev > 0
    assert '期望收益' in detail


def test_ev_sell_when_dominant_and_covered():
    action, ev, _ = expected_value_decision(0.2, 0.1, 0.7, up_return=0.02,
                                            down_return=0.02, roundtrip_cost=0.003)
    assert action == 'sell'
    assert ev < 0


def test_ev_no_trade_when_cost_not_covered():
    """D3.5 核心：方向占优但期望收益不覆盖成本 → 不交易（避免被成本收割）。"""
    action, ev, _ = expected_value_decision(0.5, 0.1, 0.4, up_return=0.01,
                                            down_return=0.01, roundtrip_cost=0.02)
    assert action is None
    assert ev < 0


def test_ev_no_trade_when_flat_dominant():
    action, _, _ = expected_value_decision(0.3, 0.5, 0.2, up_return=0.02,
                                           down_return=0.02, roundtrip_cost=0.003)
    assert action is None


def test_ev_min_threshold():
    """min_ev 提高后不再触发（覆盖成本之外的额外要求）。"""
    kwargs = dict(up_return=0.02, down_return=0.02, roundtrip_cost=0.003)
    action1, _, _ = expected_value_decision(0.7, 0.1, 0.2, min_ev=0.001, **kwargs)
    assert action1 == 'buy'
    action2, _, _ = expected_value_decision(0.7, 0.1, 0.2, min_ev=0.02, **kwargs)
    assert action2 is None


def test_ev_expected_returns_from_model_config():
    """策略 upReturn=0 时自动用训练标签阈值（模型加载场景）。"""
    from strategies.registry import load_all, get
    from strategies.base import Context
    import torch
    load_all()
    cls = get('lstm_trend')
    strat = cls({})
    strat._model_config = {'label_config': {'type': 'classification', 'threshold': 0.03}}
    up, down = strat._expected_returns()
    assert abs(up - 0.03) < 1e-9 and abs(down - 0.03) < 1e-9

    ctx = Context()
    ctx.bars = [{'close': 10, 'open': 10, 'high': 10.1, 'low': 9.9, 'volume': 1}]
    out = torch.zeros(1, 3)
    out[0, 2] = 0.9  # up 概率主导
    sig = strat.interpret_output(out, ctx.bars[0], ctx)
    assert sig is not None and sig.action == 'buy'
    assert '期望收益' in sig.reason
    assert 'ev' in sig.indicators
