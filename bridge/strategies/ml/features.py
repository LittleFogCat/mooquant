# mookquant * Feature engineering pipeline
# Converts raw bars to normalized feature tensors.
# Shared between training and inference to prevent feature drift.

import math
from typing import List, Optional

from strategies.indicators import sma, ema, macd, rsi, boll, kdj


class FeatureBuilder:
    # Feature engineering: raw bars -> normalized tensor.
    #
    # Config:
    #   window: int - feature window size (default 20)
    #   indicators: list - [{name, params}] e.g. [{name:ma, params:{period:5}}]
    #   raw_features: list - [close, volume, high, low]
    #   derived: list - [return_1d, return_5d, volatility_20]
    #   normalize: str - zscore | minmax | none
    #   normalize_window: int - rolling window for normalization

    def __init__(self, config: dict):
        self.window = config.get('window', 20)
        self.indicators = config.get('indicators', [])
        self.raw_features = config.get('raw_features', ['close'])
        self.derived = config.get('derived', [])
        self.normalize = config.get('normalize', 'zscore')
        self.norm_window = config.get('normalize_window', 20)

    @property
    def n_features(self) -> int:
        return len(self.raw_features) + len(self.indicators) + len(self.derived)

    def _extract_raw(self, bars):
        cols = {}
        for feat in self.raw_features:
            cols[feat] = [b.get(feat, 0) for b in bars]
        return cols

    def _calc_indicators(self, closes, highs, lows, volumes):
        cols = {}
        for ind in self.indicators:
            name = ind.get('name', '')
            p = ind.get('params', {})
            if name == 'ma':
                vals = sma(closes, p.get('period', 20))
                cols['ma_'+str(p.get('period', 20))] = vals
            elif name == 'ema':
                vals = ema(closes, p.get('period', 20))
                cols['ema_'+str(p.get('period', 20))] = vals
            elif name == 'rsi':
                cols['rsi'+str(p.get('period', 14))] = rsi(closes, p.get('period', 14))
            elif name == 'macd':
                dif, dea, hist = macd(closes, p.get('fast', 12), p.get('slow', 26), p.get('signal', 9))
                cols['macd_dif'] = dif
                cols['macd_dea'] = dea
                cols['macd_hist'] = hist
            elif name == 'boll':
                up, mid, low = boll(closes, p.get('period', 20), p.get('std_dev', 2.0))
                cols['boll_up'] = up
                cols['boll_mid'] = mid
                cols['boll_low'] = low
            elif name == 'kdj':
                k, d, j = kdj(highs, lows, closes, p.get('n', 9), p.get('m1', 3), p.get('m2', 3))
                cols['kdj_k'] = k
                cols['kdj_d'] = d
                cols['kdj_j'] = j
        return cols

    def _calc_derived(self, closes, volumes):
        cols = {}
        for feat in self.derived:
            if feat == 'return_1d':
                vals = [0.0] + [(closes[i]-closes[i-1])/closes[i-1] if closes[i-1] != 0 else 0.0 for i in range(1, len(closes))]
                cols[feat] = vals
            elif feat == 'return_5d':
                vals = [0.0]*min(5, len(closes))
                for i in range(5, len(closes)):
                    vals.append((closes[i]-closes[i-5])/closes[i-5] if closes[i-5] != 0 else 0.0)
                cols[feat] = vals[:len(closes)]
            elif feat == 'volatility_20':
                rets = [0.0] + [(closes[i]-closes[i-1])/closes[i-1] if closes[i-1] != 0 else 0.0 for i in range(1, len(closes))]
                vals = []
                for i in range(len(closes)):
                    start = max(0, i-19)
                    window = rets[start:i+1]
                    if len(window) < 2:
                        vals.append(0.0)
                    else:
                        m = sum(window)/len(window)
                        var = sum((x-m)**2 for x in window)/len(window)
                        vals.append(math.sqrt(var))
                cols[feat] = vals
        return cols

    def _build_matrix(self, bars):
        # Build (len_bars, n_features) matrix from all feature columns.
        closes = [b.get('close', 0) for b in bars]
        highs = [b.get('high', 0) for b in bars]
        lows = [b.get('low', 0) for b in bars]
        volumes = [b.get('volume', 0) for b in bars]

        all_cols = {}
        all_cols.update(self._extract_raw(bars))
        all_cols.update(self._calc_indicators(closes, highs, lows, volumes))
        all_cols.update(self._calc_derived(closes, volumes))

        # Assemble matrix: list of rows, each row is a list of feature values
        n = len(bars)
        matrix = []
        for i in range(n):
            row = []
            for col_name in self._column_order():
                val = all_cols.get(col_name, [None]*n)[i]
                row.append(0.0 if val is None else float(val))
            matrix.append(row)
        return matrix

    def _column_order(self):
        cols = []
        for f in self.raw_features:
            cols.append(f)
        for ind in self.indicators:
            name = ind.get('name', '')
            p = ind.get('params', {})
            if name == 'ma': cols.append('ma_'+str(p.get('period', 20)))
            elif name == 'ema': cols.append('ema_'+str(p.get('period', 20)))
            elif name == 'rsi': cols.append('rsi'+str(p.get('period', 14)))
            elif name == 'macd': cols.extend(['macd_dif', 'macd_dea', 'macd_hist'])
            elif name == 'boll': cols.extend(['boll_up', 'boll_mid', 'boll_low'])
            elif name == 'kdj': cols.extend(['kdj_k', 'kdj_d', 'kdj_j'])
        for d in self.derived:
            cols.append(d)
        return cols

    def _normalize(self, matrix):
        if self.normalize == 'none':
            return matrix
        nw = self.norm_window
        result = []
        for i in range(len(matrix)):
            start = max(0, i - nw + 1)
            window = matrix[start:i+1]
            row = []
            for j in range(len(matrix[i])):
                vals = [window[k][j] for k in range(len(window))]
                if self.normalize == 'zscore':
                    m = sum(vals) / len(vals)
                    var = sum((x - m) ** 2 for x in vals) / len(vals)
                    std = math.sqrt(var) if var > 0 else 1.0
                    row.append((matrix[i][j] - m) / std if std != 0 else 0.0)
                elif self.normalize == 'minmax':
                    lo, hi = min(vals), max(vals)
                    row.append((matrix[i][j] - lo) / (hi - lo) if hi != lo else 0.0)
                else:
                    row.append(matrix[i][j])
            result.append(row)
        return result

    def build(self, bars):
        # Single sample for inference. Returns torch.Tensor (1, window, n_features) or None.
        if len(bars) < self.window + self.norm_window:
            return None
        matrix = self._build_matrix(bars)
        matrix = self._normalize(matrix)
        window_data = matrix[-self.window:]
        import torch
        return torch.tensor(window_data, dtype=torch.float32).unsqueeze(0)

    def build_batch(self, bars):
        # Batch samples for training. Returns torch.Tensor (n_samples, window, n_features).
        if len(bars) < self.window + self.norm_window:
            return None
        matrix = self._build_matrix(bars)
        matrix = self._normalize(matrix)
        samples = []
        for i in range(len(matrix) - self.window + 1):
            samples.append(matrix[i:i + self.window])
        if not samples:
            return None
        import torch
        return torch.tensor(samples, dtype=torch.float32)
