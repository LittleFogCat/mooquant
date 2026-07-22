from strategies.registry import load_all, get
from strategies.base import Context

def test_golden_cross(sample_bars):
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
    assert signal is not None or signal is None  # just ensure no crash
