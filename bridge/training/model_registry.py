# mookquant * Model registry: persistence + LRU cache + ModelWrapper

import os
import json
import shutil
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


class SklearnModelWrapper(ModelWrapper):
    """包装 sklearn 模型（GBDT 表格基线）用于推理。

    forward 返回类别概率张量（与 GBDTModel.forward 一致，上层跳过 softmax）。
    """
    def __init__(self, model):
        self._model = model

    def forward(self, tensor):
        import torch
        return torch.tensor(self._model.predict_proba(tensor), dtype=torch.float32)


MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'data', 'models')

# 回收站：软删除的模型目录移到这里。_update_index 只扫描 MODEL_DIR/<name>/meta.json，
# 而 .trash 下没有 meta.json，因此天然被索引扫描跳过。
TRASH_DIR = os.path.join(MODEL_DIR, '.trash')
# 列表显示顺序（唯一持久化真源）；不存在时保持文件系统原有顺序
ORDER_FILE = os.path.join(MODEL_DIR, 'order.json')


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
        with cls._lock:
            cls._ensure_dir()
            model_id = cls._gen_id()
            mdir = os.path.join(MODEL_DIR, model_id)
            os.makedirs(mdir, exist_ok=True)
            model_type = config.get('model_type', '')
            if model_type == 'sklearn':
                # sklearn 基线（GBDT）：整体 pickle 持久化（含 dims 与分类器）
                import pickle
                with open(os.path.join(mdir, 'model.pkl'), 'wb') as f:
                    pickle.dump(model, f, protocol=pickle.HIGHEST_PROTOCOL)
            else:
                import torch
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
        elif config.get('model_type') == 'sklearn':
            # sklearn 基线（GBDT）：反序列化后包 SklearnModelWrapper
            import pickle
            with open(os.path.join(mdir, 'model.pkl'), 'rb') as f:
                model = pickle.load(f)
            wrapper = SklearnModelWrapper(model)
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

    # ---- 回收站（软删除）：目录移入 .trash/，彻底删除才 rmtree ----

    @classmethod
    def _read_order(cls):
        if not os.path.exists(ORDER_FILE):
            return []
        try:
            with open(ORDER_FILE) as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
        order = data.get('order') if isinstance(data, dict) else data
        return order if isinstance(order, list) else []

    @classmethod
    def _apply_order(cls, index):
        # order.json 决定列表顺序；未列入的模型追加在末尾（保持原相对顺序）
        order = cls._read_order()
        if not order:
            return index
        rank = {mid: i for i, mid in enumerate(order)}
        listed = [m for m in index if m.get('model_id') in rank]
        unlisted = [m for m in index if m.get('model_id') not in rank]
        listed.sort(key=lambda m: rank[m['model_id']])
        return listed + unlisted

    @classmethod
    def set_order(cls, model_ids):
        with cls._lock:
            cls._ensure_dir()
            ids = [str(i) for i in (model_ids or [])]
            with open(ORDER_FILE, 'w') as f:
                json.dump({'order': ids}, f, indent=2, ensure_ascii=False)
            cls._update_index()
            return {'order': ids}

    @classmethod
    def trash(cls, model_ids):
        """软删除：模型目录移入回收站。内置模型拒绝（无法恢复其训练产物）。"""
        trashed, skipped = [], []
        with cls._lock:
            cls._ensure_dir()
            os.makedirs(TRASH_DIR, exist_ok=True)
            for model_id in model_ids or []:
                mdir = os.path.join(MODEL_DIR, model_id)
                if not os.path.exists(os.path.join(mdir, 'meta.json')):
                    skipped.append({'model_id': model_id, 'reason': 'not_found'})
                    continue
                try:
                    with open(os.path.join(mdir, 'meta.json')) as f:
                        meta = json.load(f)
                except (json.JSONDecodeError, IOError):
                    meta = {}
                if meta.get('status') == 'builtin':
                    skipped.append({'model_id': model_id, 'reason': 'builtin'})
                    continue
                dest = os.path.join(TRASH_DIR, model_id)
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                os.rename(mdir, dest)
                with open(os.path.join(dest, '.trash_meta.json'), 'w') as f:
                    json.dump({'deleted_at': time.strftime('%Y-%m-%dT%H:%M:%S')},
                              f, indent=2, ensure_ascii=False)
                cls._cache.pop(model_id, None)
                trashed.append(model_id)
            # 被删的模型若正激活，清空 active.json，避免悬空指针
            if trashed:
                active_file = os.path.join(MODEL_DIR, 'active.json')
                if os.path.exists(active_file):
                    try:
                        with open(active_file) as f:
                            active = json.load(f)
                    except (json.JSONDecodeError, IOError):
                        active = {}
                    if active.get('model_id') in trashed:
                        with open(active_file, 'w') as f:
                            json.dump({}, f)
            cls._update_index()
        return {'trashed': trashed, 'skipped': skipped}

    @classmethod
    def list_trash(cls):
        if not os.path.exists(TRASH_DIR):
            return []
        items = []
        for name in os.listdir(TRASH_DIR):
            meta_path = os.path.join(TRASH_DIR, name, 'meta.json')
            if not os.path.exists(meta_path):
                continue
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue
            deleted_at = ''
            sidecar = os.path.join(TRASH_DIR, name, '.trash_meta.json')
            if os.path.exists(sidecar):
                try:
                    with open(sidecar) as f:
                        deleted_at = json.load(f).get('deleted_at', '')
                except (json.JSONDecodeError, IOError):
                    pass
            meta['deleted_at'] = deleted_at
            items.append(meta)
        items.sort(key=lambda m: m.get('deleted_at') or '', reverse=True)
        return items

    @classmethod
    def restore(cls, model_ids):
        restored, skipped = [], []
        with cls._lock:
            for model_id in model_ids or []:
                src = os.path.join(TRASH_DIR, model_id)
                if not os.path.exists(os.path.join(src, 'meta.json')):
                    skipped.append({'model_id': model_id, 'reason': 'not_found'})
                    continue
                dest = os.path.join(MODEL_DIR, model_id)
                if os.path.exists(dest):
                    skipped.append({'model_id': model_id, 'reason': 'conflict'})
                    continue
                sidecar = os.path.join(src, '.trash_meta.json')
                if os.path.exists(sidecar):
                    os.remove(sidecar)
                os.rename(src, dest)
                cls._cache.pop(model_id, None)
                restored.append(model_id)
            cls._update_index()
        return {'restored': restored, 'skipped': skipped}

    @classmethod
    def purge(cls, model_ids):
        """彻底删除：从回收站物理移除，不可恢复。"""
        purged, skipped = [], []
        with cls._lock:
            for model_id in model_ids or []:
                tdir = os.path.join(TRASH_DIR, model_id)
                if not os.path.exists(tdir):
                    skipped.append({'model_id': model_id, 'reason': 'not_found'})
                    continue
                shutil.rmtree(tdir)
                purged.append(model_id)
        return {'purged': purged, 'skipped': skipped}

    @classmethod
    def empty_trash(cls):
        count = 0
        with cls._lock:
            if os.path.exists(TRASH_DIR):
                for name in os.listdir(TRASH_DIR):
                    target = os.path.join(TRASH_DIR, name)
                    if os.path.isdir(target):
                        shutil.rmtree(target)
                        count += 1
        return {'purged': count}

    @classmethod
    def delete(cls, model_id):
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
        index = cls._apply_order(index)
        with open(os.path.join(MODEL_DIR, 'index.json'), 'w') as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
