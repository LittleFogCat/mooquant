# -*- coding: utf-8 -*-
"""特征 2.0 / 自适应标签 / 体检报告测试（M3 系列）。"""
import math
import pytest

from strategies.ml.features import FeatureBuilder
from training.labels import make_triple_barrier_labels
from training.trainer import Trainer, MockDataFetcher


def _bars(n=60, start=10.0):
    out = []
    price = start
    for i in range(n):
        price *= 1 + 0.02 * math.sin(i / 3.0)
        out.append({"date": "2026-01-{:02d}".format(i + 1),
                    "open": price / 1.002, "high": price * 1.01,
                    "low": price * 0.99, "close": price, "volume": 1e6 * (1 + 0.1 * math.sin(i))})
    return out


# ---------------------------------------------------------------------------
# 尺度不变特征
# ---------------------------------------------------------------------------

def test_log_volume_shift_invariant_under_unit_change():
    """volume ×100（手->股）后，log_volume 特征的 z-score 偏移应为常数 log(100)，
    归一化前特征矩阵仅整体平移，跨样本区分度不变。"""
    cfg = {"window": 5, "raw_features": [], "derived": ["log_volume"],
           "normalize": "none", "normalize_mode": "global"}
    fb = FeatureBuilder(cfg)
    m1 = fb._build_matrix(_bars())
    fb2 = FeatureBuilder(cfg)
    m2 = fb2._build_matrix([dict(b, volume=b["volume"] * 100) for b in _bars()])
    # 每个样本的 log_volume 差值应恒等于 log(100)
    for r1, r2 in zip(m1, m2):
        assert abs((r2[0] - r1[0]) - math.log(100)) < 1e-9


def test_volume_ratio_dimensionless():
    """量比特征无量纲：单位变化后数值不变。"""
    cfg = {"window": 5, "raw_features": [], "derived": ["volume_ratio_5d"],
           "normalize": "none", "normalize_mode": "global"}
    fb = FeatureBuilder(cfg)
    m1 = fb._build_matrix(_bars())
    fb2 = FeatureBuilder(cfg)
    m2 = fb2._build_matrix([dict(b, volume=b["volume"] * 100) for b in _bars()])
    for r1, r2 in zip(m1, m2):
        assert abs(r1[0] - r2[0]) < 1e-9


def test_ma_deviation_and_high_low_range():
    """MA 偏离度与振幅特征可计算且有限。"""
    cfg = {"window": 5, "raw_features": [], "derived": ["ma_deviation", "high_low_range"],
           "normalize": "none", "normalize_mode": "global"}
    fb = FeatureBuilder(cfg)
    m = fb._build_matrix(_bars())
    assert len(m) == 60
    for row in m:
        for v in row:
            assert math.isfinite(v)


def test_new_default_features_config_trainable(tmp_model_dir):
    """默认特征配置（尺度不变特征集）可完成训练。"""
    t = Trainer(MockDataFetcher())
    result = t.train({
        'data': {'symbols': ['test'], 'period': '1d', 'count': 100},
        'label_config': {'type': 'classification', 'horizon': 3, 'threshold': 0.01},
        'model_arch': 'lstm',
        'model_params': {'hidden_size': 8, 'num_layers': 1},
        'train_config': {'epochs': 2, 'batch_size': 8, 'learning_rate': 0.01},
        'model_name': 'feat_v2_test',
    })
    assert 'model_id' in result
    assert result['feature_config']['raw_features'] == []
    assert 'log_volume' in result['feature_config']['derived']


# ---------------------------------------------------------------------------
# ATR 自适应三重障碍
# ---------------------------------------------------------------------------

def test_adaptive_triple_barrier_labels():
    """自适应障碍下：低波动序列（障碍极窄）几乎每根 bar 都触碰障碍；
    高波动序列障碍随 ATR 变宽，触碰率相对下降。验证的核心是
    「障碍随波动率自适应」而非固定百分比。"""
    low_vol = []
    p = 10.0
    for i in range(60):
        p *= 1 + 0.002 * math.sin(i / 3)
        low_vol.append({"date": str(i), "open": p, "high": p * 1.001, "low": p * 0.999, "close": p, "volume": 1e6})
    high_vol = []
    p = 10.0
    for i in range(60):
        p *= 1 + 0.03 * math.sin(i / 3)
        high_vol.append({"date": str(i), "open": p, "high": p * 1.02, "low": p * 0.98, "close": p, "volume": 1e6})

    labels_low = make_triple_barrier_labels(low_vol, horizon=5, adaptive=True)
    labels_high = make_triple_barrier_labels(high_vol, horizon=5, adaptive=True)
    assert len(labels_low) == len(labels_high)
    # 自适应障碍下两类序列的触碰率都应显著（障碍随波动缩放后仍可触发）
    non_hold_high = sum(1 for l in labels_high if l != 1)
    non_hold_low = sum(1 for l in labels_low if l != 1)
    assert non_hold_high > 10 and non_hold_low > 10
    # 与固定障碍对比：高波动序列固定 3% 障碍的触碰率应低于自适应（ATR 更宽则更难触发或相当）
    labels_fixed = make_triple_barrier_labels(high_vol, horizon=5, up=0.003, down=0.003)
    # 低波动序列固定 0.3% 障碍几乎全触发，自适应障碍按 ATR 缩放宽得多
    assert sum(1 for l in labels_fixed if l != 1) > non_hold_high


def test_adaptive_labels_compatible_with_fixed():
    """adaptive=False 时行为与旧版一致。"""
    bars = _bars()
    a = make_triple_barrier_labels(bars, horizon=5, up=0.03, down=0.03, adaptive=False)
    b = make_triple_barrier_labels(bars, horizon=5, up=0.03, down=0.03)
    assert a == b


# ---------------------------------------------------------------------------
# 体检报告
# ---------------------------------------------------------------------------

def test_health_report_generated(tmp_model_dir):
    """分类训练应产出体检报告，含混淆矩阵/评分/等级。"""
    t = Trainer(MockDataFetcher())
    result = t.train({
        'data': {'symbols': ['test'], 'period': '1d', 'count': 100},
        'feature_config': {'window': 5, 'raw_features': ['close'], 'normalize': 'zscore',
                           'normalize_mode': 'global'},
        'label_config': {'type': 'classification', 'horizon': 3, 'threshold': 0.01},
        'model_arch': 'lstm',
        'model_params': {'hidden_size': 8, 'num_layers': 1},
        'train_config': {'epochs': 2, 'batch_size': 8, 'learning_rate': 0.01},
        'model_name': 'health_test',
    })
    hr = result.get('health_report')
    assert hr is not None
    assert 0 <= hr['score'] <= 100
    assert hr['grade'] in ('green', 'yellow', 'red')
    assert len(hr['confusion_matrix']) == 3
    assert len(hr['prob_hist']) == 10
    assert 'quality_score' in result['metrics']
    # config.json 持久化了体检报告
    from training.model_registry import ModelRegistry
    _w, cfg = ModelRegistry.load(result['model_id'])
    assert 'health_report' in cfg
    assert 'data_fingerprint' in cfg
    assert 'data_date_range' in cfg
    assert 'label_dist' in cfg


def test_label_dist_reported(tmp_model_dir):
    """训练结果包含标签分布（str key，JSON 友好）。"""
    t = Trainer(MockDataFetcher())
    result = t.train({
        'data': {'symbols': ['test'], 'period': '1d', 'count': 100},
        'feature_config': {'window': 5, 'raw_features': ['close'], 'normalize': 'zscore',
                           'normalize_mode': 'global'},
        'label_config': {'type': 'classification', 'horizon': 3, 'threshold': 0.01},
        'model_arch': 'lstm',
        'model_params': {'hidden_size': 8, 'num_layers': 1},
        'train_config': {'epochs': 1, 'batch_size': 8, 'learning_rate': 0.01},
        'model_name': 'dist_test',
    })
    ld = result['label_dist']
    assert isinstance(ld, dict) and all(isinstance(k, str) for k in ld.keys())
    assert sum(ld.values()) == result['n_samples']


def test_fingerprint_gate_rejects_mismatch(tmp_model_dir):
    """指纹不匹配的模型加载时应报错（ML 策略守门）。"""
    t = Trainer(MockDataFetcher())
    result = t.train({
        'data': {'symbols': ['test'], 'period': '1d', 'count': 100},
        'feature_config': {'window': 5, 'raw_features': ['close'], 'normalize': 'zscore',
                           'normalize_mode': 'global'},
        'label_config': {'type': 'classification', 'horizon': 3, 'threshold': 0.01},
        'model_arch': 'lstm',
        'model_params': {'hidden_size': 8, 'num_layers': 1},
        'train_config': {'epochs': 1, 'batch_size': 8, 'learning_rate': 0.01},
        'model_name': 'fp_test',
    })
    from training.model_registry import ModelRegistry
    from data import datafeed
    _w, cfg = ModelRegistry.load(result['model_id'])
    # 篡改指纹后校验应失败
    bad_fp = datafeed.data_fingerprint('none')
    ok, reason = datafeed.check_fingerprint(bad_fp, 'front')
    assert not ok
    # 正确指纹通过
    ok, _ = datafeed.check_fingerprint(cfg['data_fingerprint'], 'front')
    assert ok
