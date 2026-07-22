# mookquant * ML strategy base class
# Extends StrategyBase with model loading and inference.
# Subclasses implement build_features and interpret_output.

from strategies.base import StrategyBase, Signal


class MLStrategyBase(StrategyBase):
    # ML strategy skeleton. Loads a trained model on_after_init.
    # Torch is imported lazily - only when ML strategies are used.

    is_ml = True
    model_arch = ''

    def on_after_init(self, ctx):
        model_id = self.params.get('model_id', '')
        if not model_id:
            raise ValueError('ML strategy requires model_id parameter')
        from training.model_registry import ModelRegistry
        from strategies.ml.features import FeatureBuilder
        self._wrapper, self._model_config = ModelRegistry.load(model_id)
        self._wrapper.eval()
        feat_cfg = self._model_config.get('feature_config', {})
        self._feature_builder = FeatureBuilder(feat_cfg)

    def on_bar(self, bar, ctx):
        features = self.build_features(ctx.bars)
        if features is None:
            return None
        output = self._wrapper.forward(features)
        return self.interpret_output(output, bar, ctx)

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
