# mookquant * Transformer trend prediction strategy
# Uses a pretrained Transformer model to predict future price direction.

from strategies.base import Signal
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
        {'key': 'margin', 'type': 'float', 'default': 0.05,
         'min': 0.0, 'max': 0.5, 'label': '置信度差阈值',
         'description': '预测类别的概率需比其他两类高出的差值，差值越大交易越少'},
    ]

    def interpret_output(self, output, bar, ctx):
        import torch
        probs = torch.softmax(output, dim=-1)
        prob_up = probs[0][2].item()
        prob_down = probs[0][0].item()
        prob_flat = probs[0][1].item()
        # 相对判决：上涨/下跌概率须同时高于"横盘"与其他方向才触发。
        # 避免绝对阈值在 softmax 概率压缩（模型集中在 flat 类）时永远无法触发。
        margin = self.params.get('margin', 0.05)
        if prob_up > prob_flat + margin and prob_up > prob_down + margin:
            return Signal(
                action='buy',
                reason='Transformer预测上涨概率' + str(round(prob_up, 2)),
                strength=prob_up,
                indicators={'prob_up': round(prob_up, 4), 'prob_down': round(prob_down, 4), 'prob_flat': round(prob_flat, 4)},
            )
        if prob_down > prob_flat + margin and prob_down > prob_up + margin:
            return Signal(
                action='sell',
                reason='Transformer预测下跌概率' + str(round(prob_down, 2)),
                strength=prob_down,
                indicators={'prob_up': round(prob_up, 4), 'prob_down': round(prob_down, 4), 'prob_flat': round(prob_flat, 4)},
            )
        return None
