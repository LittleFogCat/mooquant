from strategies.registry import load_all, list_strategies, get

def test_load_all():
    names = load_all()
    assert 'ma_cross' in names

def test_list_strategies():
    load_all()
    strats = list_strategies()
    assert len(strats) >= 3
    names = [s['name'] for s in strats]
    assert 'ma_cross' in names

def test_get_strategy():
    load_all()
    cls = get('ma_cross')
    assert cls.name == 'ma_cross'
