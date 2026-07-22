from training.labels import make_classification_labels, make_regression_labels, make_triple_barrier_labels

def test_classification_labels():
    bars = [{'close': 10.0 + i * 0.5} for i in range(20)]
    labels = make_classification_labels(bars, horizon=5, threshold=0.02)
    assert len(labels) == 15
    assert all(l in (0, 1, 2) for l in labels)
    assert labels[0] == 2

def test_regression_labels():
    bars = [{'close': 10.0 + i * 0.3} for i in range(20)]
    labels = make_regression_labels(bars, horizon=5)
    assert len(labels) == 15
    assert all(isinstance(l, float) for l in labels)

def test_triple_barrier():
    bars = [{'close': 10.0 + i * 0.8} for i in range(30)]
    labels = make_triple_barrier_labels(bars, horizon=10, up=0.05, down=0.05)
    assert len(labels) == 20
    assert all(l in (0, 1, 2) for l in labels)
