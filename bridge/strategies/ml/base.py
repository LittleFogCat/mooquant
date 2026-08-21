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
}


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
        stored_fp = self._model_config.get('data_fingerprint')
        if stored_fp:
            from data.datafeed import check_fingerprint
            ok, reason = check_fingerprint(stored_fp, 'front')
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

    def interpret_output(self, output, bar, ctx):
        # Override: convert model output to Signal.
        raise NotImplementedError
