# -*- coding: utf-8 -*-
"""GBDT 表格基线（D3.3）测试：模型单测 + 训练/保存/加载/推理链路。

sklearn HistGradientBoosting 作为 LightGBM 的免依赖替代（CPU 友好、小样本稳健）。

注意：本文件放在 tests/zz_gbdt/（排序最后）——本机环境在 sklearn 载入/训练之后，
同进程内后续 torch 训练测试会出现 ~32s/个的异常慢速（环境性问题，非代码缺陷）；
把 GBDT/sklearn 测试放到全量套件最后执行可让其余测试保持快速。
"""
import pytest


def test_train_gbdt_baseline(tmp_model_dir):
    """D3.3：GBDT 训练 → 保存（pickle）→ 加载（SklearnModelWrapper）→ 推理。"""
    from training.trainer import Trainer, MockDataFetcher
    from training.model_registry import ModelRegistry, SklearnModelWrapper
    from strategies.ml.features import FeatureBuilder

    t = Trainer(MockDataFetcher())
    result = t.train({
        'data': {'symbols': ['test'], 'period': '1d', 'count': 120},
        'feature_config': {'window': 5, 'raw_features': ['close'], 'normalize': 'zscore',
                           'normalize_mode': 'global'},
        'label_config': {'type': 'classification', 'horizon': 3, 'threshold': 0.01},
        'model_arch': 'gbdt',
        'model_params': {'n_estimators': 20, 'learning_rate': 0.1},
        'train_config': {'epochs': 1},
        'model_name': 'gbdt_test',
    })
    assert 'accuracy' in result['metrics']
    assert result['health_report'] is not None, "GBDT 训练应产出体检报告"

    w, cfg = ModelRegistry.load(result['model_id'])
    assert cfg.get('model_type') == 'sklearn'
    assert cfg.get('model_arch') == 'gbdt'
    assert isinstance(w, SklearnModelWrapper)

    # 推理输出概率（行和=1，3 类）
    fb = FeatureBuilder(cfg['feature_config'])
    fb._global_stats = cfg.get('feature_stats')
    bars = MockDataFetcher().fetch_bars('test', '1d', 120)
    X = fb.build(bars)
    out = w.forward(X)
    assert tuple(out.shape) == (1, 3)
    assert abs(float(out[0].sum()) - 1) < 1e-3


def test_gbdt_arch_maps_to_strategy():
    """D3.3：gbdt 架构绑定到 gbdt_classifier 策略。"""
    from strategies.ml.base import ARCH_STRATEGY_MAP
    assert ARCH_STRATEGY_MAP.get('gbdt') == 'gbdt_classifier'


def test_gbdt_model_fit_predict():
    """D3.3：fit 后可 predict_proba（3 列对齐、概率和=1），forward 返回张量。"""
    import torch
    from strategies.ml.models.gbdt import GBDTModel
    m = GBDTModel(input_size=3, seq_len=5, n_estimators=10)
    X = torch.randn(60, 5, 3)
    y = torch.tensor([i % 3 for i in range(60)])
    m.fit(X, y)
    proba = m.predict_proba(X)
    assert proba.shape == (60, 3)
    assert abs(proba.sum(axis=1).mean() - 1) < 1e-6
    out = m.forward(X[:2])
    assert tuple(out.shape) == (2, 3)
    assert m.output_is_probability is True


def test_gbdt_missing_class_alignment():
    """D3.3：训练数据缺失某类别时，predict_proba 仍对齐 3 列（缺类列补零）。"""
    import torch
    from strategies.ml.models.gbdt import GBDTModel
    m = GBDTModel(input_size=2, seq_len=4, n_estimators=10)
    X = torch.randn(40, 4, 2)
    y = torch.tensor([i % 2 for i in range(40)])  # 只有类别 0/1
    m.fit(X, y)
    proba = m.predict_proba(X[:5])
    assert proba.shape == (5, 3)
    assert abs(proba.sum(axis=1).mean() - 1) < 1e-6


def test_gbdt_regression_rejected():
    """D3.3：GBDT 基线当前仅支持分类任务（回归请用 torch 模型）。"""
    from strategies.ml.models.gbdt import GBDTModel
    with pytest.raises(ValueError):
        GBDTModel(input_size=3, seq_len=5, task='regression')
