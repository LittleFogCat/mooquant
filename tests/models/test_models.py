import torch
import pytest

def test_lstm_forward():
    from strategies.ml.models.registry import build_model
    m = build_model('lstm', {'input_size': 4, 'hidden_size': 16, 'num_layers': 1, 'output_size': 3})
    x = torch.randn(2, 10, 4)
    out = m(x)
    assert out.shape == (2, 3)

def test_transformer_forward():
    from strategies.ml.models.registry import build_model
    m = build_model('transformer', {'input_size': 4, 'd_model': 16, 'nhead': 4, 'num_layers': 1, 'output_size': 3})
    x = torch.randn(2, 10, 4)
    out = m(x)
    assert out.shape == (2, 3)

def test_mlp_forward():
    from strategies.ml.models.registry import build_model
    m = build_model('mlp', {'input_size': 4, 'seq_len': 10, 'hidden_sizes': [16], 'output_size': 3})
    x = torch.randn(2, 10, 4)
    out = m(x)
    assert out.shape == (2, 3)
