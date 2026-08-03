# mookquant * Financial dataset for PyTorch training

import torch
from torch.utils.data import Dataset


class FinancialDataset(Dataset):
    # Dataset built from bars + FeatureBuilder + label function.
    # Supports multi-symbol data concatenation.

    def __init__(self, feature_builder, bars_list, label_fn, label_params):
        self.X_list = []
        self.y_list = []

        for bars in bars_list:
            X = feature_builder.build_batch(bars)
            if X is None:
                continue
            labels = label_fn(bars, **label_params)
            # 对齐约定（消除 look-ahead bias）：
            #   X[i] 特征窗口为 bars[i : i+window]，窗口右端 = i+window-1
            #   labels[j] 是从 closes[j] 出发预测 closes[j+horizon] 的收益
            #   因此 X[i] 应配 labels[i + window - 1]（从窗口右端出发）
            #   推理时 build() 取 matrix[-window:]（右端=len-1），对应 labels[len-1]
            #   训练与推理对齐一致
            offset = feature_builder.window - 1
            n = min(len(X), len(labels) - offset)
            if n <= 0:
                continue
            self.X_list.append(X[:n])
            paired = labels[offset:offset + n]
            if isinstance(paired[0], float):
                self.y_list.append(torch.tensor(paired, dtype=torch.float32))
            else:
                self.y_list.append(torch.tensor(paired, dtype=torch.long))

        if self.X_list:
            self.X = torch.cat(self.X_list, dim=0)
            self.y = torch.cat(self.y_list, dim=0)
        else:
            self.X = torch.empty(0)
            self.y = torch.empty(0)

    def __len__(self):
        return len(self.y)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]
