# End-to-end integration test
# Tests the full flow: strategy registration -> signal computation via HTTP

import sys
import os
import time
import threading
from http.server import HTTPServer

BRIDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bridge')
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)


def test_signal_rule_based(sample_bars):
    # Test signal computation without HTTP, directly calling the function.
    from strategies.registry import load_all, get
    from strategies.base import Context
    load_all()
    cls = get('ma_cross')
    strat = cls({'fast': 5, 'slow': 10})
    ctx = Context()
    ctx.symbol = 'test'
    strat.on_init(ctx)
    strat.on_after_init(ctx)
    signal = None
    for i, bar in enumerate(sample_bars):
        ctx.bars = sample_bars[:i+1]
        ctx.barpos = i
        signal = strat.on_bar(bar, ctx)
    strat.on_stop(ctx)
    # Signal should be None or a Signal object
    assert signal is None or hasattr(signal, 'action')


def test_signal_ml_strategy(sample_bars):
    # Test ML strategy signal computation with builtin model.
    from training.builtin_models import ensure_builtin_models
    ensure_builtin_models()
    from strategies.registry import register
    import strategies.ml.builtin.lstm_trend  # noqa: registers via decorator
    from strategies.registry import get
    from strategies.base import Context

    cls = get('lstm_trend')
    strat = cls({'model_id': 'builtin_lstm', 'buy_threshold': 0.5, 'sell_threshold': 0.5})
    ctx = Context()
    ctx.symbol = 'test'
    strat.on_init(ctx)
    strat.on_after_init(ctx)
    signal = None
    for i, bar in enumerate(sample_bars):
        ctx.bars = sample_bars[:i+1]
        ctx.barpos = i
        signal = strat.on_bar(bar, ctx)
    strat.on_stop(ctx)
    assert signal is None or hasattr(signal, 'action')
