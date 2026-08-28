# mookquant * Transformer trend prediction strategy
# Uses a pretrained Transformer model to predict future price direction.
# 信号决策：D3.5 期望收益决策（概率 × 期望幅度 − 成本，见 ml/base.py）。

from strategies.registry import register
from strategies.ml.base import MLStrategyBase


@register
class TransformerTrendStrategy(MLStrategyBase):
    name = 'transformer_trend'
    display_name = 'Transformer趋势预测'
    description = '基于Transformer模型预测涨跌概率'
    version = '1.0'
    trigger_mode = 'bar'
    model_arch = 'transformer'
    is_ml = True

    params_schema = [
        {'key': 'model_id', 'type': 'string', 'label': '模型ID',
         'description': '在模型管理中训练并获取模型ID'},
        {'key': 'upReturn', 'type': 'float', 'default': 0, 'min': 0, 'max': 0.5,
         'label': '上涨期望幅度', 'description': '模型判"涨"时的期望收益幅度（0=自动用训练标签阈值）'},
        {'key': 'downReturn', 'type': 'float', 'default': 0, 'min': 0, 'max': 0.5,
         'label': '下跌期望幅度', 'description': '模型判"跌"时的期望收益幅度（0=自动用训练标签阈值）'},
        {'key': 'roundtripCost', 'type': 'float', 'default': 0.003, 'min': 0, 'max': 0.1,
         'label': '往返成本', 'description': '一次往返的交易成本（佣金+印花税+滑点），期望收益需覆盖它才触发'},
        {'key': 'minEv', 'type': 'float', 'default': 0, 'min': 0, 'max': 0.1,
         'label': '期望收益阈值', 'description': '净期望收益需超过该值才触发（0=覆盖成本即可）'},
    ]

    def interpret_output(self, output, bar, ctx):
        import torch
        probs = torch.softmax(output, dim=-1)
        prob_up = probs[0][2].item()
        prob_down = probs[0][0].item()
        prob_flat = probs[0][1].item()
        return self._interpret_probs(prob_up, prob_down, prob_flat, bar, ctx)
