# mookquant * LSTM model architecture (experimental)

import torch
import torch.nn as nn

from .registry import register_model


@register_model('lstm')
class LSTMModel(nn.Module):
    # LSTM time-series prediction model.
    # Input: (batch, seq_len, n_features)
    # Output: (batch, output_size) classification, (batch,) regression

    def __init__(self, input_size, hidden_size=64, num_layers=2,
                 dropout=0.1, output_size=3, task='classification'):
        super().__init__()
        self.task = task
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_size, output_size)

    def forward(self, x):
        out, _ = self.lstm(x)
        last = out[:, -1, :]
        last = self.dropout(last)
        logits = self.fc(last)
        if self.task == 'classification':
            return logits
        # 回归任务：仅单输出时 squeeze，避免多输出回归维度错误
        if logits.shape[-1] == 1:
            return logits.squeeze(-1)
        return logits
