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
        {'key': 'margin', 'type': 'float', 'default': 0.05,
         'min': 0.0, 'max': 0.5, 'label': '\u7f6e\u4fe1\u5ea6\u5dee\u9608\u503c',
         'description': '\u9884\u6d4b\u7c7b\u522b\u7684\u6982\u7387\u9700\u6bd4\u5176\u4ed6\u4e24\u7c7b\u9ad8\u51fa\u7684\u5dee\u503c\uff0c\u5dee\u503c\u8d8a\u5927\u4ea4\u6613\u8d8a\u5c11'},
    ]

    def interpret_output(self, output, bar, ctx):
        import torch
        probs = torch.softmax(output, dim=-1)
        prob_up = probs[0][2].item()
        prob_down = probs[0][0].item()
        prob_flat = probs[0][1].item()
        # \u76f8\u5bf9\u5224\u51b3\uff1a\u4e0a\u6da8/\u4e0b\u8dcc\u6982\u7387\u987b\u540c\u65f6\u9ad8\u4e8e"\u6a2a\u76d8"\u4e0e\u5176\u4ed6\u65b9\u5411\u624d\u89e6\u53d1\u3002
        # \u907f\u514d\u7edd\u5bf9\u9608\u503c\u5728 softmax \u6982\u7387\u538b\u7f29\uff08\u6a21\u578b\u96c6\u4e2d\u5728 flat \u7c7b\uff09\u65f6\u6c38\u8fdc\u65e0\u6cd5\u89e6\u53d1\u3002
        margin = self.params.get('margin', 0.05)
        if prob_up > prob_flat + margin and prob_up > prob_down + margin:
            return Signal(
                action='buy',
                reason='LSTM\u9884\u6d4b\u4e0a\u6da8\u6982\u7387' + str(round(prob_up, 2)),
                strength=prob_up,
                indicators={'prob_up': round(prob_up, 4), 'prob_down': round(prob_down, 4), 'prob_flat': round(prob_flat, 4)},
            )
        if prob_down > prob_flat + margin and prob_down > prob_up + margin:
            return Signal(
                action='sell',
                reason='LSTM\u9884\u6d4b\u4e0b\u8dcc\u6982\u7387' + str(round(prob_down, 2)),
                strength=prob_down,
                indicators={'prob_up': round(prob_up, 4), 'prob_down': round(prob_down, 4), 'prob_flat': round(prob_flat, 4)},
            )
        return None
