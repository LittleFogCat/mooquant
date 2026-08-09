# mookquant * MLP model architecture (experimental)
# Simple multi-layer perceptron for feature-based prediction.

import torch
import torch.nn as nn

from .registry import register_model


@register_model('mlp')
class MLPModel(nn.Module):
    # Multi-layer perceptron. Flattens input then passes through hidden layers.
    # Input: (batch, seq_len, n_features) -> flatten -> (batch, seq_len*n_features)
    # Output: (batch, output_size) classification, (batch,) regression

    def __init__(self, input_size, seq_len, hidden_sizes=None,
                 dropout=0.1, output_size=3, task='classification'):
        super().__init__()
        self.task = task
        if hidden_sizes is None:
            hidden_sizes = [128, 64]

        flat_size = input_size * seq_len
        layers = []
        prev = flat_size
        for h in hidden_sizes:
            layers.append(nn.Linear(prev, h))
            layers.append(nn.ReLU())
            layers.append(nn.Dropout(dropout))
            prev = h
        layers.append(nn.Linear(prev, output_size))
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        x = x.flatten(1)  # (batch, seq_len*n_features)
        logits = self.net(x)
        if self.task == 'classification':
            return logits
        # 回归任务：仅单输出时 squeeze，避免多输出回归维度错误
        if logits.shape[-1] == 1:
            return logits.squeeze(-1)
        return logits
