from training.trainer import Trainer, MockDataFetcher

def test_train_quick(tmp_model_dir):
    t = Trainer(MockDataFetcher())
    result = t.train({
        'data': {'symbols': ['test'], 'period': '1d', 'count': 100},
        'feature_config': {'window': 5, 'raw_features': ['close'], 'normalize': 'zscore', 'normalize_window': 5},
        'label_config': {'type': 'classification', 'horizon': 3, 'threshold': 0.01},
        'model_arch': 'lstm',
        'model_params': {'hidden_size': 8, 'num_layers': 1},
        'train_config': {'epochs': 2, 'batch_size': 8, 'learning_rate': 0.01},
        'model_name': 'unit_test',
    })
    assert 'model_id' in result
    assert 'metrics' in result
    assert result['metrics']['n_samples'] > 0
