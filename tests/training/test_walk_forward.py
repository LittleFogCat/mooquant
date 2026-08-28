# -*- coding: utf-8 -*-
"""D3.4 walk-forward 滚动样本外评估测试（防过拟合核心）。"""
import os
import pytest

from training.trainer import Trainer, MockDataFetcher


def _cfg(**overrides):
    cfg = {
        'data': {'symbols': ['test'], 'period': '1d',
                 'start_date': '2026-01-01', 'end_date': '2026-12-31'},
        'feature_config': {'window': 5, 'raw_features': ['close'], 'normalize': 'zscore',
                           'normalize_mode': 'global'},
        'label_config': {'type': 'classification', 'horizon': 3, 'threshold': 0.005},
        'model_arch': 'lstm',
        'model_params': {'hidden_size': 8, 'num_layers': 1},
        'train_config': {'epochs': 2, 'batch_size': 8, 'learning_rate': 0.01},
        'model_name': 'wf_test',
    }
    cfg.update(overrides)
    return cfg


def test_walk_forward_report_structure(tmp_model_dir):
    """D3.4：walk_forward=True 时训练结果附带稳定性报告。"""
    t = Trainer(MockDataFetcher())
    r = t.train(_cfg(walk_forward=True, walk_forward_segments=3))
    wf = r['walk_forward']
    assert set(wf.keys()) >= {'folds', 'meanAccuracy', 'stdAccuracy', 'nFolds',
                              'stable', 'verdict', 'summary'}
    assert wf['nFolds'] == 2
    assert 0 <= wf['meanAccuracy'] <= 1
    assert wf['stdAccuracy'] >= 0
    assert wf['stable'] in ('稳定', '一般', '不稳定（过拟合风险）')
    for f in wf['folds']:
        assert f['nTest'] > 0 and 'trainRange' in f and 'testRange' in f


def test_walk_forward_folds_are_out_of_sample(tmp_model_dir):
    """D3.4：折叠训练区间必在测试区间之前（无前视）。"""
    t = Trainer(MockDataFetcher())
    r = t.train(_cfg(walk_forward=True, walk_forward_segments=3))
    for f in r['walk_forward']['folds']:
        assert f['trainRange'].split(' ~ ')[1] < f['testRange'].split(' ~ ')[0]


def test_walk_forward_main_saved_folds_not(tmp_model_dir):
    """D3.4：主模型落盘，折叠模型不落盘（save_model=False）。"""
    from training import model_registry
    t = Trainer(MockDataFetcher())
    r = t.train(_cfg(walk_forward=True))
    assert r['model_id'], "主模型应保存"
    saved = [d for d in os.listdir(model_registry.MODEL_DIR) if d.startswith('m_')]
    assert len(saved) == 1, "折叠模型不应落盘: {}".format(saved)


def test_walk_forward_insufficient_data_reports_error(tmp_model_dir):
    """D3.4：数据量不足时主训练成功、walk-forward 报错被捕获（不阻断主流程）。"""
    t = Trainer(MockDataFetcher())
    r = t.train(_cfg(walk_forward=True,
                     data={'symbols': ['test'], 'period': '1d',
                           'start_date': '2026-01-01', 'end_date': '2026-02-28'}))
    assert 'model_id' in r  # 主训练仍成功
    assert 'walk_forward_error' in r


def test_walk_forward_regression_rejected():
    """D3.4：walk-forward 仅支持分类标签。"""
    t = Trainer(MockDataFetcher())
    with pytest.raises(ValueError) as ei:
        t.walk_forward(_cfg(label_config={'type': 'regression', 'horizon': 3}))
    assert '分类' in str(ei.value)


def test_save_model_false_returns_empty_id(tmp_model_dir):
    """D3.4：save_model=False 时不落盘、model_id 为空（折叠用）。"""
    t = Trainer(MockDataFetcher())
    r = t.train(_cfg(save_model=False))
    assert r['model_id'] == ''
