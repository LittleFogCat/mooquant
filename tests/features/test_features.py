from strategies.ml.features import FeatureBuilder

def test_n_features():
    fb = FeatureBuilder({'window': 10, 'raw_features': ['close', 'volume'], 'derived': ['return_1d'], 'normalize': 'zscore', 'normalize_window': 10})
    assert fb.n_features == 3

def test_build_single(sample_bars):
    fb = FeatureBuilder({'window': 10, 'raw_features': ['close'], 'normalize': 'zscore', 'normalize_window': 10})
    t = fb.build(sample_bars)
    assert t is not None
    assert t.shape == (1, 10, 1)

def test_build_batch(sample_bars):
    fb = FeatureBuilder({'window': 10, 'raw_features': ['close'], 'normalize': 'zscore', 'normalize_window': 10})
    t = fb.build_batch(sample_bars)
    assert t is not None
    assert t.shape[1] == 10

def test_insufficient_bars():
    fb = FeatureBuilder({'window': 20, 'normalize_window': 20})
    assert fb.build([{'close': 1}]) is None
