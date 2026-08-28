# mookquant * ML strategy base class
# Extends StrategyBase with model loading and inference.
# Subclasses implement build_features and interpret_output.

from strategies.base import StrategyBase, Signal

# Shared arch -> strategy-type mapping (used by model_server /signal and
# backtest engine to resolve a model to its strategy class).
ARCH_STRATEGY_MAP = {
    'lstm': 'lstm_trend',
    'mlp': 'mlp_classifier',
    'transformer': 'transformer_trend',
    'gbdt': 'gbdt_classifier',
}


def expected_value_decision(prob_up, prob_flat, prob_down,
                            up_return=0.02, down_return=0.02,
                            roundtrip_cost=0.003, min_ev=0.0):
    """D3.5 期望收益决策：用「概率 × 期望幅度 − 往返成本」替换粗糙的 margin 判决。

    ev = P(up) × up_return − P(down) × down_return − roundtrip_cost

    - 方向主导（P(up) > P(flat)）且 ev > min_ev → buy
    - 方向主导（P(down) > P(flat)）且 ev < −min_ev → sell
    - 否则不交易（期望收益不覆盖成本，交易只会被成本收割）

    Returns:
        (action_or_None, ev, detail)
    """
    ev = prob_up * up_return - prob_down * down_return - roundtrip_cost
    # 方向须为最大类（P(up) 同时大于 flat 与 down）且净期望收益覆盖阈值才触发，
    # 避免「概率不高但被成本误判」或「up 占优却发卖出」的误触发
    if prob_up > prob_flat and prob_up > prob_down and ev > min_ev:
        return 'buy', ev, '期望收益{:+.3%}'.format(ev)
    if prob_down > prob_flat and prob_down > prob_up and ev < -min_ev:
        return 'sell', ev, '期望收益{:+.3%}'.format(ev)
    return None, ev, ''


class MLStrategyBase(StrategyBase):
    # ML strategy skeleton. Loads a trained model on_after_init.
    # Torch is imported lazily - only when ML strategies are used.

    is_ml = True
    model_arch = ''

    def on_after_init(self, ctx):
        model_id = self.params.get('model_id', '')
        if not model_id:
            # Auto-use active model when no explicit model_id given.
            # Works for both HTTP /signal and stdio RPC strategy.signal.
            model_id = self._get_active_model_id()
            if not model_id:
                raise ValueError('ML strategy requires model_id parameter or an active model')
        from training.model_registry import ModelRegistry
        from strategies.ml.features import FeatureBuilder
        self._wrapper, self._model_config = ModelRegistry.load(model_id)
        self._wrapper.eval()
        # 口径指纹校验（M1.1）：旧模型或口径不一致时明确报错，防止静默漂移
        # 口径统一 front_ratio（前复权比例版，见 datafeed.py），front 旧模型会因指纹不匹配提示重训
        stored_fp = self._model_config.get('data_fingerprint')
        if stored_fp:
            from data.datafeed import check_fingerprint
            ok, reason = check_fingerprint(stored_fp, 'front_ratio')
            if not ok:
                raise ValueError('模型数据口径校验失败: {} (model_id={})'.format(reason, model_id))
        feat_cfg = self._model_config.get('feature_config', {})
        self._feature_builder = FeatureBuilder(feat_cfg)
        # 载入训练时持久化的全局归一化统计量（global 模式），推理与训练特征尺度一致
        stats = self._model_config.get('feature_stats')
        if stats:
            self._feature_builder._global_stats = stats

    def on_bar(self, bar, ctx):
        features = self.build_features(ctx.bars)
        if features is None:
            return None
        output = self._wrapper.forward(features)
        return self.interpret_output(output, bar, ctx)

    @staticmethod
    def _get_active_model_id():
        """Read active model ID from data/models/active.json."""
        import os, json
        active_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))),
            'data', 'models', 'active.json')
        if os.path.exists(active_file):
            try:
                with open(active_file) as f:
                    return json.load(f).get('model_id', '')
            except (json.JSONDecodeError, IOError):
                pass
        return ''

    def metadata(self):
        meta = super().metadata()
        meta['is_ml'] = True
        meta['model_arch'] = self.model_arch
        return meta

    def build_features(self, bars):
        # Override: convert bars to feature tensor.
        return self._feature_builder.build(bars)

    def _expected_returns(self):
        """方向期望收益估计（D3.5）。

        策略参数 upReturn/downReturn 可显式覆盖（>0）；为 0 时从模型训练标签配置推导：
        - classification → ±threshold
        - triple_barrier → ±up / ±down
        模型未加载时回退默认 0.02。
        """
        up = float(self.params.get('upReturn', 0) or 0)
        down = float(self.params.get('downReturn', 0) or 0)
        lc = (getattr(self, '_model_config', None) or {}).get('label_config', {}) or {}
        ltype = lc.get('type', 'classification')
        if up <= 0:
            if ltype == 'classification':
                up = float(lc.get('threshold', 0.02) or 0.02)
            else:
                up = float(lc.get('up', 0.02) or 0.02)
        if down <= 0:
            if ltype == 'classification':
                down = float(lc.get('threshold', 0.02) or 0.02)
            else:
                down = float(lc.get('down', 0.02) or 0.02)
        return max(up, 1e-6), max(down, 1e-6)

    def _interpret_probs(self, prob_up, prob_down, prob_flat, bar, ctx):
        """D3.5 期望收益决策 → Signal（各 ML 策略共用的信号解释入口）。

        Args:
            prob_up / prob_down / prob_flat: 三类概率（0=sell, 1=flat, 2=buy）
        """
        up_ret, down_ret = self._expected_returns()
        roundtrip = float(self.params.get('roundtripCost', 0.003) or 0.003)
        min_ev = float(self.params.get('minEv', 0.0) or 0.0)
        action, ev, detail = expected_value_decision(
            prob_up, prob_flat, prob_down, up_ret, down_ret, roundtrip, min_ev)
        indicators = {
            'prob_up': round(prob_up, 4), 'prob_down': round(prob_down, 4),
            'prob_flat': round(prob_flat, 4), 'ev': round(ev, 4),
        }
        if action == 'buy':
            return Signal(action='buy', reason='预测上涨，' + detail, strength=prob_up,
                          indicators=indicators)
        if action == 'sell':
            return Signal(action='sell', reason='预测下跌，' + detail, strength=prob_down,
                          indicators=indicators)
        return None

    def interpret_output(self, output, bar, ctx):
        # Override: convert model output to Signal.
        raise NotImplementedError
