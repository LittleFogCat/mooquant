# mookquant * Builtin models for testing
# Pre-created models that work without training.

import os
import json
import time


DUMMY_MODEL_ID = 'builtin_dummy'


def ensure_builtin_models():
    # Create builtin models if they do not exist.
    from training.model_registry import ModelRegistry, MODEL_DIR, DummyModelWrapper
    os.makedirs(MODEL_DIR, exist_ok=True)

    # 1. Dummy model (no torch needed)
    _ensure_dummy(ModelRegistry, MODEL_DIR)

    # 2. Linear model (quick train on mock data)
    _ensure_trained_builtin(
        ModelRegistry, 'builtin_linear', 'linear_test',
        'mlp', {'input_size': 4, 'seq_len': 10, 'hidden_sizes': [16], 'output_size': 3},
        feat_cfg={'window': 10, 'raw_features': ['close', 'volume'], 'derived': ['return_1d', 'return_5d'], 'normalize': 'zscore', 'normalize_window': 10}
    )

    # 3. Small LSTM (quick train on mock data)
    _ensure_trained_builtin(
        ModelRegistry, 'builtin_lstm', 'lstm_test',
        'lstm', {'input_size': 4, 'hidden_size': 16, 'num_layers': 1, 'output_size': 3},
        feat_cfg={'window': 10, 'raw_features': ['close', 'volume'], 'derived': ['return_1d', 'return_5d'], 'normalize': 'zscore', 'normalize_window': 10}
    )


def _ensure_dummy(ModelRegistry, MODEL_DIR):
    mdir = os.path.join(MODEL_DIR, DUMMY_MODEL_ID)
    if os.path.exists(os.path.join(mdir, 'meta.json')):
        return
    os.makedirs(mdir, exist_ok=True)
    meta = {
        'model_id': DUMMY_MODEL_ID,
        'name': 'Dummy (always hold)',
        'arch': 'dummy',
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'metrics': {},
        'n_samples': 0,
        'symbols': [],
        'status': 'builtin',
    }
    config = {'model_arch': 'dummy', 'model_params': {}, 'model_type': 'dummy',
              'feature_config': {}, 'label_config': {}, 'train_config': {}}
    with open(os.path.join(mdir, 'meta.json'), 'w') as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    with open(os.path.join(mdir, 'config.json'), 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    ModelRegistry._update_index()


def _ensure_trained_builtin(ModelRegistry, model_id, name, arch, model_params, feat_cfg):
    mdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '..', 'data', 'models', model_id)
    mdir = os.path.abspath(mdir)
    if os.path.exists(os.path.join(mdir, 'meta.json')):
        return
    # Quick train on mock data
    from training.trainer import Trainer, MockDataFetcher
    config = {
        'data': {'symbols': ['600036.SH'], 'period': '1d', 'count': 200},
        'feature_config': feat_cfg,
        'label_config': {'type': 'classification', 'horizon': 5, 'threshold': 0.02},
        'model_arch': arch,
        'model_params': model_params,
        'train_config': {'epochs': 5, 'batch_size': 16, 'learning_rate': 0.01, 'val_ratio': 0.2, 'patience': 5},
        'model_name': name,
    }
    trainer = Trainer(MockDataFetcher())
    result = trainer.train(config)
    # Rename the saved model directory to the builtin ID
    saved_id = result['model_id']
    if saved_id != model_id:
        saved_dir = os.path.join(os.path.dirname(mdir), saved_id)
        if os.path.exists(saved_dir):
            os.rename(saved_dir, mdir)
            # Update meta.json with new model_id
            meta_path = os.path.join(mdir, 'meta.json')
            with open(meta_path) as f:
                meta = json.load(f)
            meta['model_id'] = model_id
            meta['name'] = name + ' (builtin)'
            meta['status'] = 'builtin'
            with open(meta_path, 'w') as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
            # Update config.json
            cfg_path = os.path.join(mdir, 'config.json')
            with open(cfg_path) as f:
                cfg = json.load(f)
            with open(cfg_path, 'w') as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            ModelRegistry._update_index()
