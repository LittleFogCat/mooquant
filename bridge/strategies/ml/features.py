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
        # normalize: zscore | minmax | none
        # normalize_mode: rolling(旧, 逐样本滚动窗, 默认) | global(新, 全序列逐列统计,
        #   跨样本有区分度, 训练/推理一致性更好, 推荐用于真实训练)
        self.normalize = config.get('normalize', 'zscore')
        self.norm_mode = config.get('normalize_mode', 'rolling')
        self.norm_window = config.get('normalize_window', 20)
        # 缓存全序列统计量（global 模式）：{col_index: (mean, std|min, max)}
        self._global_stats = None

    @property
    def n_features(self) -> int:
        # 按 _column_order 实际展开长度计算（macd/boll/kdj 各贡献多列）
        return len(self._column_order())

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
            # ---- M3.1 尺度不变特征（v2）：天然对单位/价格水平免疫 ----
            elif feat == 'log_volume':
                # log 成交量：volume ×100 单位漂移只造成 log(100)≈4.6 的常数平移，
                # 远小于 z-score 归一化的影响；且分布更接近正态
                cols[feat] = [math.log(v) if v and v > 0 else 0.0 for v in volumes]
            elif feat == 'volume_ratio_5d':
                # 量比：当日量 / 近5日均量（无量纲，跨标的可比）
                vals = []
                for i in range(len(volumes)):
                    start = max(0, i-4)
                    window = volumes[start:i+1]
                    avg_v = sum(window)/len(window) if window else 0
                    vals.append(volumes[i]/avg_v if avg_v > 0 else 1.0)
                cols[feat] = vals
            elif feat == 'ma_deviation':
                # 价格相对 20 日均线偏离度（无量纲，替代裸 close）
                vals = []
                for i in range(len(closes)):
                    start = max(0, i-19)
                    window = closes[start:i+1]
                    m = sum(window)/len(window)
                    vals.append((closes[i]-m)/m if m != 0 else 0.0)
                cols[feat] = vals
            elif feat == 'high_low_range':
                # 振幅：(high-low)/close -- 需要 highs/lows，由调用方保证传入
                highs = self._last_highs or [c for c in closes]
                lows = self._last_lows or [c for c in closes]
                vals = []
                for i in range(len(closes)):
                    c = closes[i]
                    vals.append((highs[i]-lows[i])/c if c != 0 else 0.0)
                cols[feat] = vals
            elif feat == 'close_return':
                # 累计对数收益（相对首日）：保留趋势信息且尺度不变
                base = closes[0] if closes and closes[0] != 0 else 1.0
                cols[feat] = [math.log(c/base) if c > 0 and base > 0 else 0.0 for c in closes]
        return cols

    def _build_matrix(self, bars):
        # Build (len_bars, n_features) matrix from all feature columns.
        closes = [b.get('close', 0) for b in bars]
        highs = [b.get('high', 0) for b in bars]
        lows = [b.get('low', 0) for b in bars]
        volumes = [b.get('volume', 0) for b in bars]
        # high_low_range 等特征需要 highs/lows，通过实例属性传递（_calc_derived 签名保持兼容）
        self._last_highs = highs
        self._last_lows = lows

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
        # Global 模式：按列统计整个序列的 mean/std（zscore）或 min/max（minmax），
        # 训练与推理都用同一套统计量，跨样本特征有区分度（替代逐样本滚动归一化）。
        if self.norm_mode == 'global':
            if self._global_stats is None:
                self._global_stats = self._fit_stats(matrix)
            return self._transform(matrix, self._global_stats)
        # 旧 rolling 模式：逐样本滚动窗口归一化（保兼容）
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

    def _fit_stats(self, matrix):
        n_cols = len(matrix[0]) if matrix else 0
        stats = []
        for j in range(n_cols):
            col = [matrix[i][j] for i in range(len(matrix))]
            m = sum(col) / len(col)
            var = sum((x - m) ** 2 for x in col) / len(col)
            std = math.sqrt(var) if var > 0 else 1.0
            lo, hi = min(col), max(col)
            stats.append({'mean': m, 'std': std, 'min': lo, 'max': hi})
        return stats

    def _transform(self, matrix, stats):
        if self.normalize == 'none':
            return matrix
        result = []
        for i in range(len(matrix)):
            row = []
            for j in range(len(matrix[i])):
                s = stats[j] if j < len(stats) else {'mean': 0.0, 'std': 1.0, 'min': 0.0, 'max': 1.0}
                if self.normalize == 'zscore':
                    row.append((matrix[i][j] - s['mean']) / s['std'] if s['std'] != 0 else 0.0)
                elif self.normalize == 'minmax':
                    row.append((matrix[i][j] - s['min']) / (s['max'] - s['min']) if s['max'] != s['min'] else 0.0)
                else:
                    row.append(matrix[i][j])
            result.append(row)
        return result

    def build(self, bars):
        # Single sample for inference. Returns torch.Tensor (1, window, n_features) or None.
        # global 模式用全序列统计，无需 norm_window 滚动缓冲；rolling 模式仍需足够长度
        min_len = self.window + (0 if self.norm_mode == 'global' else self.norm_window)
        if len(bars) < min_len:
            return None
        matrix = self._build_matrix(bars)
        matrix = self._normalize(matrix)
        # OOD 防御：global 归一化下若最近样本特征 |z| 异常大（>10σ），
        # 说明输入数据口径与训练统计量不一致（如成交量单位漂移）。同一路径
        # 只告警一次，避免逐 bar 推理刷屏
        if self.norm_mode == 'global' and matrix:
            if not getattr(self, '_ood_warned', False):
                last = matrix[-1]
                bad_cols = [j for j, v in enumerate(last) if abs(v) > 10]
                if bad_cols:
                    self._ood_warned = True
                    import sys
                    sys.stderr.write('[feature-ood] WARNING: {} features beyond 10 sigma at latest bar '
                                     '(cols={}). Input data scale may differ from training stats.\n'.format(
                                         len(bad_cols), bad_cols[:8]))
        window_data = matrix[-self.window:]
        import torch
        return torch.tensor(window_data, dtype=torch.float32).unsqueeze(0)

    def build_batch(self, bars):
        # Batch samples for training. Returns torch.Tensor (n_samples, window, n_features).
        min_len = self.window + (0 if self.norm_mode == 'global' else self.norm_window)
        if len(bars) < min_len:
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
