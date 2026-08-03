# mookquant * Model registry: persistence + LRU cache + ModelWrapper

import os
import json
import time
import threading
from collections import OrderedDict


class ModelWrapper:
    # Unified model interface.
    def forward(self, tensor):
        raise NotImplementedError
    def eval(self):
        pass


class TorchModelWrapper(ModelWrapper):
    # Wraps a torch.nn.Module for inference.
    def __init__(self, model):
        self._model = model
        self._model.eval()
    def forward(self, tensor):
        import torch
        with torch.no_grad():
            return self._model(tensor)


class DummyModelWrapper(ModelWrapper):
    # Pure-Python model. Returns input unchanged. No torch dependency.
    def forward(self, tensor):
        return tensor


MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'data', 'models')


class ModelRegistry:
    _cache = OrderedDict()  # model_id -> (wrapper, config), LRU ordered
    _cache_max = 10
    _lock = threading.Lock()

    @classmethod
    def _ensure_dir(cls):
        os.makedirs(MODEL_DIR, exist_ok=True)

    @classmethod
    def _gen_id(cls):
        return 'm_' + str(int(time.time() * 1000)) + '_' + os.urandom(3).hex()

    @classmethod
    def save(cls, model, config, metrics, name):
        import torch
        with cls._lock:
            cls._ensure_dir()
            model_id = cls._gen_id()
            mdir = os.path.join(MODEL_DIR, model_id)
            os.makedirs(mdir, exist_ok=True)
            torch.save(model.state_dict(), os.path.join(mdir, 'model.pt'))
            meta = {
                'model_id': model_id, 'name': name,
                'arch': config.get('model_arch', ''),
                'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'metrics': metrics, 'n_samples': metrics.get('n_samples', 0),
                'symbols': config.get('data', {}).get('symbols', []),
                'status': 'active',
                'parent_model_id': config.get('parent_model_id'),
            }
            with open(os.path.join(mdir, 'meta.json'), 'w') as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
            with open(os.path.join(mdir, 'config.json'), 'w') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            cls._update_index()
            return model_id

    @classmethod
    def load(cls, model_id):
        # Fast path: cache hit (O(1) move_to_end under lock)
        with cls._lock:
            if model_id in cls._cache:
                cls._cache.move_to_end(model_id)
                return cls._cache[model_id]
        # Slow path: load from disk WITHOUT holding lock (avoid blocking)
        mdir = os.path.join(MODEL_DIR, model_id)
        if not os.path.exists(mdir):
            raise FileNotFoundError(model_id)
        with open(os.path.join(mdir, 'config.json')) as f:
            config = json.load(f)
        # Dummy models do not need torch
        if config.get('model_type') == 'dummy':
            wrapper = DummyModelWrapper()
        else:
            arch = config.get('model_arch', '')
            params = config.get('model_params', {})
            from strategies.ml.models.registry import build_model
            model = build_model(arch, params)
            import torch
            model.load_state_dict(torch.load(
                os.path.join(mdir, 'model.pt'), weights_only=True))
            wrapper = TorchModelWrapper(model)
        # Write back to cache (double-check under lock)
        with cls._lock:
            if model_id in cls._cache:
                cls._cache.move_to_end(model_id)
                return cls._cache[model_id]
            cls._cache[model_id] = (wrapper, config)
            if len(cls._cache) > cls._cache_max:
                cls._cache.popitem(last=False)  # O(1) LRU eviction
            return (wrapper, config)

    @classmethod
    def list_models(cls):
        cls._ensure_dir()
        idx_path = os.path.join(MODEL_DIR, 'index.json')
        if not os.path.exists(idx_path):
            return []
        with open(idx_path) as f:
            return json.load(f)

    @classmethod
    def get_meta(cls, model_id):
        mdir = os.path.join(MODEL_DIR, model_id)
        meta_path = os.path.join(mdir, 'meta.json')
        if not os.path.exists(meta_path):
            raise FileNotFoundError(model_id)
        with open(meta_path) as f:
            return json.load(f)

    @classmethod
    def update_meta(cls, model_id, patch):
        """Update model metadata fields (name, status, etc.)."""
        with cls._lock:
            mdir = os.path.join(MODEL_DIR, model_id)
            meta_path = os.path.join(mdir, 'meta.json')
            if not os.path.exists(meta_path):
                raise FileNotFoundError(model_id)
            with open(meta_path) as f:
                meta = json.load(f)
            meta.update(patch)
            with open(meta_path, 'w') as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
            cls._update_index()
            return meta

    @classmethod
    def delete(cls, model_id):
        import shutil
        with cls._lock:
            mdir = os.path.join(MODEL_DIR, model_id)
            if os.path.exists(mdir):
                shutil.rmtree(mdir)
            cls._cache.pop(model_id, None)  # safe, no ValueError
            cls._update_index()
            return True

    @classmethod
    def _update_index(cls):
        cls._ensure_dir()
        index = []
        for name in os.listdir(MODEL_DIR):
            if name == 'index.json':
                continue
            meta_path = os.path.join(MODEL_DIR, name, 'meta.json')
            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    index.append(json.load(f))
        with open(os.path.join(MODEL_DIR, 'index.json'), 'w') as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
