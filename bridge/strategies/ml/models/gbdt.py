# mookquant * GBDT model architecture (table baseline, sklearn)
# HistGradientBoostingClassifier: CPU-friendly, robust on small samples.
# LightGBM-like strong baseline without extra dependencies (sklearn only).

import numpy as np

from .registry import register_model


@register_model('gbdt')
class GBDTModel:
    """sklearn HistGradientBoosting 分类基线（表格特征强基线）。

    输入 (n_samples, seq_len, n_features) → 展平为 (n_samples, seq_len*n_features)。
    - fit(X, y)：训练（X 可为 torch Tensor 或 numpy）
    - predict_proba(X)：返回类别概率（与标签类别 0=sell/1=flat/2=buy 对齐）
    - forward(X)：返回概率张量（兼容 torch 推理入口；output_is_probability=True 供上层跳过 softmax）
    """

    output_is_probability = True
    is_sklearn = True

    def __init__(self, input_size, seq_len=20, n_estimators=200, learning_rate=0.1,
                 max_depth=None, task='classification', output_size=3):
        from sklearn.ensemble import HistGradientBoostingClassifier
        if task != 'classification':
            raise ValueError('GBDT 基线当前仅支持分类任务（classification），回归请用 torch 模型')
        self.input_size = input_size
        self.seq_len = seq_len
        self.output_size = output_size
        self.clf = HistGradientBoostingClassifier(
            max_iter=int(n_estimators),
            learning_rate=float(learning_rate),
            max_depth=int(max_depth) if max_depth else None,
            early_stopping=False,
            random_state=42,
        )

    def _flatten(self, X):
        """转 numpy 并展平为 (n, seq_len*n_features)。"""
        arr = X.numpy() if hasattr(X, 'numpy') else np.asarray(X)
        if arr.ndim == 3:
            arr = arr.reshape(len(arr), -1)
        return arr

    def fit(self, X, y):
        X2 = self._flatten(X)
        y2 = y.numpy() if hasattr(y, 'numpy') else np.asarray(y)
        self.clf.fit(X2, y2.ravel())
        return self

    def predict_proba(self, X):
        """返回 (n, output_size) 类别概率；缺失类别时按 classes_ 对齐补零。"""
        X2 = self._flatten(X)
        proba = self.clf.predict_proba(X2)
        if proba.shape[1] != self.output_size:
            full = np.zeros((len(proba), self.output_size))
            classes = self.clf.classes_.astype(int)
            for col, c in enumerate(classes):
                if 0 <= c < self.output_size:
                    full[:, c] = proba[:, col]
            proba = full
        return proba

    def forward(self, X):
        import torch
        return torch.tensor(self.predict_proba(X), dtype=torch.float32)
