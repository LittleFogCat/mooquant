import pytest
from training.model_registry import ModelRegistry

def test_save_and_load(tmp_model_dir):
    from strategies.ml.models.registry import build_model
    import torch
    m = build_model('lstm', {'input_size': 2, 'hidden_size': 8, 'num_layers': 1, 'output_size': 3})
    cfg = {'model_arch': 'lstm', 'model_params': {'input_size': 2, 'hidden_size': 8, 'num_layers': 1, 'output_size': 3}}
    mid = ModelRegistry.save(m, cfg, {'val_loss': 0.5}, 'test')
    assert mid.startswith('m_')
    w, c = ModelRegistry.load(mid)
    assert w is not None
    out = w.forward(torch.randn(1, 5, 2))
    assert out.shape == (1, 3)

def test_list_and_delete(tmp_model_dir):
    from strategies.ml.models.registry import build_model
    m = build_model('mlp', {'input_size': 2, 'seq_len': 5, 'hidden_sizes': [8], 'output_size': 3})
    mid = ModelRegistry.save(m, {'model_arch': 'mlp', 'model_params': {}}, {}, 'test')
    assert len(ModelRegistry.list_models()) >= 1
    ModelRegistry.delete(mid)
    assert all(mo['model_id'] != mid for mo in ModelRegistry.list_models())

def test_load_not_found(tmp_model_dir):
    with pytest.raises(FileNotFoundError):
        ModelRegistry.load('nope')
