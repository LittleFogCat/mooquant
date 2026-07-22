# mookquant * Transformer model architecture (experimental)

import math
import torch
import torch.nn as nn

from .registry import register_model


class PositionalEncoding(nn.Module):
    # Sinusoidal positional encoding for Transformer.

    def __init__(self, d_model, dropout=0.1, max_len=500):
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0)  # (1, max_len, d_model)
        self.register_buffer('pe', pe)

    def forward(self, x):
        x = x + self.pe[:, :x.size(1), :]
        return self.dropout(x)


@register_model('transformer')
class TransformerModel(nn.Module):
    # Transformer Encoder time-series prediction model.
    # Input: (batch, seq_len, n_features)
    # Output: (batch, output_size) classification, (batch,) regression

    def __init__(self, input_size, d_model=64, nhead=4, num_layers=2,
                 dim_feedforward=128, dropout=0.1, output_size=3,
                 task='classification'):
        super().__init__()
        self.task = task
        self.input_proj = nn.Linear(input_size, d_model)
        self.pos_encoder = PositionalEncoding(d_model, dropout)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead,
            dim_feedforward=dim_feedforward,
            dropout=dropout, batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers)
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(d_model, output_size)

    def forward(self, x):
        x = self.input_proj(x)
        x = self.pos_encoder(x)
        x = self.encoder(x)
        pooled = x.mean(dim=1)
        pooled = self.dropout(pooled)
        logits = self.fc(pooled)
        if self.task == 'classification':
            return logits
        return logits.squeeze(-1)
