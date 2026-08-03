"""S1: verify feature-label alignment eliminates look-ahead bias"""
from strategies.ml.features import FeatureBuilder
from training.dataset import FinancialDataset
from training.labels import make_regression_labels


def test_alignment_no_lookahead(small_bars):
    """
    X[i] feature window right edge = i+window-1,
    label should start from closes[i+window-1] predicting closes[i+window-1+horizon].
    """
    window, horizon = 5, 3
    fb = FeatureBuilder({
        'window': window, 'raw_features': ['close'],
        'normalize': 'none', 'normalize_window': 1,
    })
    ds = FinancialDataset(fb, [small_bars], make_regression_labels, {'horizon': horizon})
    closes = [b['close'] for b in small_bars]

    # First sample: X[0] window right edge = window-1 = 4
    expected_0 = (closes[window - 1 + horizon] - closes[window - 1]) / closes[window - 1]
    assert abs(ds.y[0].item() - expected_0) < 1e-6

    # Last sample
    n = len(ds)
    last_right = (n - 1) + window - 1
    expected_last = (closes[last_right + horizon] - closes[last_right]) / closes[last_right]
    assert abs(ds.y[-1].item() - expected_last) < 1e-6


def test_alignment_sample_count(small_bars):
    """n_samples = min(len(X), len(labels) - (window-1))"""
    window, horizon = 5, 3
    fb = FeatureBuilder({
        'window': window, 'raw_features': ['close'],
        'normalize': 'none', 'normalize_window': 1,
    })
    ds = FinancialDataset(fb, [small_bars], make_regression_labels, {'horizon': horizon})

    n_bars = len(small_bars)
    len_X = n_bars - window + 1
    len_labels = n_bars - horizon
    offset = window - 1
    expected_n = min(len_X, len_labels - offset)
    assert len(ds) == expected_n
    assert expected_n > 0
