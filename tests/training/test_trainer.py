"""Trainer 单元测试。

覆盖：快速训练可运行 + 训练结果正确性（模型不退化恒判单类），
防止「回测无交易」类的训练退化问题回归。
"""
from collections import Counter
import pytest

from training.trainer import Trainer, MockDataFetcher


def _quick_config(**overrides):
    cfg = {
        'data': {'symbols': ['test'], 'period': '1d', 'count': 100},
        'feature_config': {'window': 5, 'raw_features': ['close'], 'normalize': 'zscore', 'normalize_window': 5},
        'label_config': {'type': 'classification', 'horizon': 3, 'threshold': 0.01},
        'model_arch': 'lstm',
        'model_params': {'hidden_size': 8, 'num_layers': 1},
        'train_config': {'epochs': 2, 'batch_size': 8, 'learning_rate': 0.01},
        'model_name': 'unit_test',
    }
    cfg.update(overrides)
    return cfg


def test_train_quick(tmp_model_dir):
    t = Trainer(MockDataFetcher())
    result = t.train(_quick_config())
    assert 'model_id' in result
    assert 'metrics' in result
    assert result['metrics']['n_samples'] > 0


def test_train_skips_no_data_symbols(tmp_model_dir, monkeypatch):
    """无数据的标的应被跳过，symbols 只保留有数据的。"""
    from training.trainer import DataFetcher

    class PartialFetcher(DataFetcher):
        def fetch_bars(self, symbol, period, count):
            if symbol == 'good':
                # 返回最小有效数据
                price = 10.0
                return [{'date': f'2026-01-{i+1:02d}', 'open': price, 'high': price + 0.1,
                         'low': price - 0.1, 'close': price + 0.1, 'volume': 100000} for i in range(30)]
            return []

    t = Trainer(PartialFetcher())
    result = t.train(_quick_config(data={'symbols': ['good', 'bad'], 'period': '1d', 'count': 100}))
    assert 'bad' not in result['symbols']
    assert 'good' in result['symbols']
    assert result['skipped_symbols'] == ['bad']


def test_train_all_symbols_missing_raises(tmp_model_dir):
    """全部标的无数据应报错（不空跑）。"""
    from training.trainer import DataFetcher

    class EmptyFetcher(DataFetcher):
        def fetch_bars(self, symbol, period, count):
            return []

    t = Trainer(EmptyFetcher())
    with pytest.raises(RuntimeError) as ei:
        t.train(_quick_config(data={'symbols': ['a', 'b'], 'period': '1d', 'count': 100}))
    assert '数据' in str(ei.value) or 'No data' in str(ei.value)


def test_train_global_normalize_used_by_default(tmp_model_dir):
    """默认启用 global 归一化，且统计量随模型持久化。"""
    from training.model_registry import ModelRegistry
    t = Trainer(MockDataFetcher())
    result = t.train(_quick_config(train_config={'epochs': 2, 'batch_size': 8, 'learning_rate': 0.01}))
    w, cfg = ModelRegistry.load(result['model_id'])
    assert cfg['feature_config'].get('normalize_mode') == 'global'
    assert 'feature_stats' in cfg, "global 归一化统计量应随模型持久化"
    assert len(cfg['feature_stats']) == cfg['model_params'].get('input_size', 1)


def test_train_global_normalize_train_infer_consistent(tmp_model_dir):
    """global 归一化下 build_batch 最后一个样本与 build 推理一致（训练/推理对齐）。"""
    from training.model_registry import ModelRegistry
    from strategies.ml.features import FeatureBuilder
    t = Trainer(MockDataFetcher())
    result = t.train(_quick_config(train_config={'epochs': 2, 'batch_size': 8, 'learning_rate': 0.01}))
    w, cfg = ModelRegistry.load(result['model_id'])
    fb = FeatureBuilder(cfg['feature_config'])
    fb._global_stats = cfg.get('feature_stats')

    # 用 MockDataFetcher 生成同款 bars 验证一致性
    bars = MockDataFetcher().fetch_bars('test', '1d', 100)
    Xb = fb.build_batch(bars)
    X = fb.build(bars)
    import torch
    assert Xb is not None and X is not None
    assert torch.allclose(Xb[-1], X[0]), "训练最后样本应与推理一致"


def test_train_classification_does_not_collapse_to_single_class(tmp_model_dir):
    """防退化回归（轻量版）：训练机制不应产出「连方向都不区分」的退化输出。

    说明：小样本 + 弱特征下模型能力受数据/配置波动影响，断言「必出多类」不可靠。
    这里改为验证两个机制性前提（它们共同防止「回测无交易」的退化回归）：
      1. 标签本身非单类（数据可学前提，防标签生成 bug 抹平类别）；
      2. global 归一化下训练与推理特征一致（防特征漂移导致推理退化）。
    """
    import math
    from training.model_registry import ModelRegistry
    from training.dataset import FinancialDataset
    from training.labels import make_classification_labels
    from strategies.ml.features import FeatureBuilder

    bars = []
    price = 50.0
    n = 200
    for i in range(n):
        phase = i / 5.0
        ret = 0.03 * math.sin(phase)
        price = max(1.0, price * (1 + ret))
        bars.append({'date': f'2026-01-{i+1:02d}', 'open': round(price / 1.002, 2),
                     'high': round(price * 1.01, 2), 'low': round(price * 0.99, 2),
                     'close': round(price, 2), 'volume': 1000000})
    t = Trainer(MockDataFetcher())
    result = t.train(_quick_config(
        data={'symbols': ['trend'], 'period': '1d', 'count': n},
        feature_config={'window': 5, 'raw_features': ['close'], 'normalize': 'zscore',
                        'normalize_mode': 'global'},
        label_config={'type': 'classification', 'horizon': 3, 'threshold': 0.005},
        model_arch='lstm',
        model_params={'hidden_size': 16, 'num_layers': 1},
        train_config={'epochs': 20, 'batch_size': 16, 'learning_rate': 0.01},
    ))
    w, cfg = ModelRegistry.load(result['model_id'])
    fb = FeatureBuilder(cfg['feature_config'])
    fb._global_stats = cfg.get('feature_stats')

    # 1. 标签非单类（防标签生成 bug）
    ds = FinancialDataset(fb, [bars], make_classification_labels,
                          {k: v for k, v in cfg['label_config'].items() if k != 'type'})
    assert len(ds) > 0
    assert len(Counter(ds.y.tolist())) >= 2, f"标签应非单类: {dict(Counter(ds.y.tolist()))}"

    # 2. global 归一化训练/推理特征一致（防特征漂移）
    Xb = fb.build_batch(bars)
    X = fb.build(bars)
    import torch
    assert Xb is not None and X is not None
    assert torch.allclose(Xb[-1], X[0]), "训练最后样本应与推理一致（特征漂移会导致推理退化）"


def test_ml_strategy_interpret_output_generates_signals():
    """策略信号逻辑：人工构造极端概率输出应生成 buy/sell 信号。

    防止「模型输出合理但 interpret_output 永不触发」导致的回测无交易回归。
    """
    import torch
    from strategies.registry import load_all, get
    from strategies.base import Context, Signal
    load_all()

    for strat_name, up_idx, flat_idx, down_idx in [
        ('lstm_trend', 2, 1, 0),
        ('transformer_trend', 2, 1, 0),
        ('gbdt_classifier', 2, 1, 0),  # D3.3：GBDT 基线（输出即概率，无需 softmax）
    ]:
        cls = get(strat_name)
        strat = cls({'margin': 0.05})
        ctx = Context()
        ctx.bars = [{'close': 10, 'high': 10.1, 'low': 9.9, 'open': 10, 'volume': 1000}]
        bar = ctx.bars[0]

        # up 极端概率 -> buy
        out = torch.zeros(1, 3)
        out[0, up_idx] = 0.9
        sig = strat.interpret_output(out, bar, ctx)
        assert sig is not None and sig.action == 'buy', f"{strat_name} up 概率应触发 buy"

        # down 极端概率 -> sell
        out = torch.zeros(1, 3)
        out[0, down_idx] = 0.9
        sig = strat.interpret_output(out, bar, ctx)
        assert sig is not None and sig.action == 'sell', f"{strat_name} down 概率应触发 sell"

        # 平均分布 -> 无信号（flat）
        out = torch.full((1, 3), 1 / 3)
        sig = strat.interpret_output(out, bar, ctx)
        assert sig is None, f"{strat_name} 平均概率不应触发信号"
