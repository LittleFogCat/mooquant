# mookquant * LSTM trend prediction strategy (example ML strategy)
# Uses a pretrained LSTM model to predict future price direction.

from strategies.base import Signal
from strategies.registry import register
from strategies.ml.base import MLStrategyBase


@register
class LSTMTrendStrategy(MLStrategyBase):
    name = 'lstm_trend'
    display_name = 'LSTM\u8d8b\u52bf\u9884\u6d4b'
    description = '\u57fa\u4e8eLSTM\u6a21\u578b\u9884\u6d4b\u6da8\u8dcc\u6982\u7387'
    version = '1.0'
    trigger_mode = 'bar'
    model_arch = 'lstm'
    is_ml = True

    params_schema = [
        {'key': 'model_id', 'type': 'string', 'label': '\u6a21\u578bID',
         'description': '\u5728\u6a21\u578b\u7ba1\u7406\u4e2d\u8bad\u7ec3\u5e76\u83b7\u53d6\u6a21\u578bID'},
        {'key': 'buy_threshold', 'type': 'float', 'default': 0.6,
         'min': 0.5, 'max': 1.0, 'label': '\u4e70\u5165\u6982\u7387\u9608\u503c',
         'description': '\u6a21\u578b\u9884\u6d4b\u4e0a\u6da8\u6982\u7387\u8d85\u8fc7\u6b64\u503c\u65f6\u4e70\u5165'},
        {'key': 'sell_threshold', 'type': 'float', 'default': 0.4,
         'min': 0.0, 'max': 0.5, 'label': '\u5356\u51fa\u6982\u7387\u9608\u503c',
         'description': '\u6a21\u578b\u9884\u6d4b\u4e0a\u6da8\u6982\u7387\u4f4e\u4e8e\u6b64\u503c\u65f6\u5356\u51fa'},
    ]

    def interpret_output(self, output, bar, ctx):
        import torch
        probs = torch.softmax(output, dim=-1)
        prob_up = probs[0][2].item()
        prob_down = probs[0][0].item()
        buy_th = self.params.get('buy_threshold', 0.6)
        sell_th = self.params.get('sell_threshold', 0.4)
        if prob_up > buy_th:
            return Signal(
                action='buy',
                reason='LSTM\u9884\u6d4b\u4e0a\u6da8\u6982\u7387' + str(round(prob_up, 2)),
                strength=prob_up,
                indicators={'prob_up': round(prob_up, 4), 'prob_down': round(prob_down, 4)},
            )
        if prob_up < sell_th:
            return Signal(
                action='sell',
                reason='LSTM\u9884\u6d4b\u4e0a\u6da8\u6982\u7387\u4ec5' + str(round(prob_up, 2)),
                strength=1 - prob_up,
                indicators={'prob_up': round(prob_up, 4), 'prob_down': round(prob_down, 4)},
            )
        return None
