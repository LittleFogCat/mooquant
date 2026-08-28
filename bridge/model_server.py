# mookquant * HTTP model server
# Exposes model layer via HTTP for QMT shell strategy and local UI.
# Uses Python stdlib http.server - zero external dependencies.

import ast
import json
import os
import sys
import threading
from collections import OrderedDict
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Ensure bridge dir is in path
BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

from strategies.registry import load_all, get, list_strategies, save_strategy, delete_strategy
from strategies.base import Context
from strategies.ml.base import ARCH_STRATEGY_MAP as _ARCH_STRATEGY_MAP
from training.model_registry import ModelRegistry
from training.pipeline import TrainPipeline
from training.builtin_models import ensure_builtin_models

# Active model persistence
ACTIVE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'models', 'active.json')


# S6: strategy source code safety check
_DANGEROUS_MODULES = frozenset({
    'os', 'subprocess', 'shutil', 'socket', 'ctypes',
    'multiprocessing', 'pickle', 'marshal', 'importlib',
})
_DANGEROUS_BUILTINS = frozenset({
    '__import__', 'eval', 'exec', 'compile', 'open',
    'globals', 'locals', 'vars',
})


def _check_code_safety(code_str):
    """AST static check: block dangerous imports and builtin calls.

    Returns (is_safe, reason). reason describes violation when is_safe=False.
    """
    try:
        tree = ast.parse(code_str)
    except SyntaxError as e:
        return False, 'Syntax error: {}'.format(e)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split('.')[0]
                if root in _DANGEROUS_MODULES:
                    return False, 'Forbidden import: {}'.format(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split('.')[0]
                if root in _DANGEROUS_MODULES:
                    return False, 'Forbidden import from: {}'.format(node.module)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _DANGEROUS_BUILTINS:
                return False, 'Forbidden call: {}()'.format(func.id)
    return True, ''


def _get_active_model():
    """Return active model dict {model_id, strategy} or {}."""
    if os.path.exists(ACTIVE_FILE):
        try:
            with open(ACTIVE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _set_active_model(model_id, force=False):
    """Activate a model. Auto-detects strategy from model arch.

    守门（M3.3）：degraded（体检报告 red）模型激活需 force=True。
    """
    strategy = 'lstm_trend'
    degraded = False
    try:
        meta = ModelRegistry.get_meta(model_id)
        arch = meta.get('arch', '')
        strategy = _ARCH_STRATEGY_MAP.get(arch, 'lstm_trend')
        degraded = bool(meta.get('metrics', {}).get('degraded'))
    except Exception:
        pass
    if degraded and not force:
        raise ValueError('模型体检不合格（degraded），激活需 force=true 确认')
    os.makedirs(os.path.dirname(ACTIVE_FILE), exist_ok=True)
    with open(ACTIVE_FILE, 'w') as f:
        json.dump({'model_id': model_id, 'strategy': strategy}, f, ensure_ascii=False)
    return {'degraded': degraded}


# Global state
_pipeline = TrainPipeline()
_strategies_loaded = False


def _ensure_loaded():
    global _strategies_loaded
    if not _strategies_loaded:
        load_all()  # 含 ML 策略（registry 统一扫描 strategies/ml/builtin）
        # Import ML models so they register
        try:
            import strategies.ml.models  # noqa
        except ImportError:
            pass  # torch not available, ML models skip
        ensure_builtin_models()
        _strategies_loaded = True


# M8: strategy instance pool (avoid re-instantiating + model loading per /signal)
_strategy_pool = OrderedDict()   # (strategy_name, model_id) -> (strat, ctx)
_strategy_pool_max = 10
_strategy_pool_lock = threading.Lock()


def _get_strategy_instance(strategy_name, params):
    """Get or create a strategy instance from pool (thread-safe).

    key = (strategy_name, params.get('model_id', ''))
    Cache hit: reuse instance, reset internal state.
    Cache miss: create and initialize (outside lock to avoid blocking).
    """
    model_id = (params or {}).get('model_id', '')
    key = (strategy_name, model_id)

    with _strategy_pool_lock:
        if key in _strategy_pool:
            _strategy_pool.move_to_end(key)
            strat, ctx = _strategy_pool[key]
            strat._state = {}
            return strat, ctx

    # Cache miss: create new instance (outside lock)
    strat_cls = get(strategy_name)
    strat = strat_cls(params or {})
    ctx = Context()
    ctx.is_backtest = False
    ctx.period = '1d'
    strat.on_init(ctx)
    strat.on_after_init(ctx)

    with _strategy_pool_lock:
        if key in _strategy_pool:
            _strategy_pool.move_to_end(key)
            strat, ctx = _strategy_pool[key]
            strat._state = {}
            return strat, ctx
        _strategy_pool[key] = (strat, ctx)
        if len(_strategy_pool) > _strategy_pool_max:
            _strategy_pool.popitem(last=False)
        return strat, ctx


def _compute_signal(strategy_name, bars, params, symbol):
    # M8: use strategy instance pool to avoid repeated instantiation + model loading
    strat, ctx = _get_strategy_instance(strategy_name, params)
    ctx.symbol = symbol or ''
    ctx.bars = []
    ctx.barpos = 0
    signal = None
    for i, bar in enumerate(bars):
        ctx.bars = bars[:i + 1]
        ctx.barpos = i
        signal = strat.on_bar(bar, ctx)
    strat.on_stop(ctx)
    if signal is None:
        return {'action': 'hold', 'reason': '无信号'}
    return signal.to_dict()


def _normalize_train_config(config):
    """将 UI 训练表单的扁平参数转换为 Trainer 期望的嵌套格式。

    UI 发送：{symbol, period, bar_count, architecture, epochs,
             learning_rate, hidden_size, batch_size, label_type}
    Trainer 期望：{data.{symbols,period,count}, model_arch, model_params,
                  train_config.{epochs,learning_rate,batch_size},
                  label_config.{type}, feature_config}

    已是嵌套格式（含 model_arch/data）时原样返回，兼容脚本直调。
    """
    if not isinstance(config, dict):
        return config
    if 'model_arch' in config or 'data' in config or 'feature_config' in config:
        return config

    nested = {}
    data = {}
    syms = config.get('symbols') or config.get('symbol')
    if syms:
        if isinstance(syms, str):
            syms = [s.strip() for s in syms.split(',') if s.strip()]
        elif isinstance(syms, list):
            syms = [str(s).strip() for s in syms if str(s).strip()]
        data['symbols'] = syms
    if config.get('period'):
        data['period'] = config['period']
    if config.get('bar_count'):
        data['count'] = config['bar_count']
    if config.get('start_date'):
        data['start_date'] = config['start_date']
    if config.get('end_date'):
        data['end_date'] = config['end_date']
    if data:
        nested['data'] = data

    if config.get('architecture'):
        nested['model_arch'] = config['architecture']

    model_params = {}
    if config.get('hidden_size'):
        model_params['hidden_size'] = config['hidden_size']
    if model_params:
        nested['model_params'] = model_params

    train_config = {}
    for src, dst in (('epochs', 'epochs'), ('learning_rate', 'learning_rate'),
                     ('batch_size', 'batch_size')):
        if config.get(src):
            train_config[dst] = config[src]
    if train_config:
        nested['train_config'] = train_config

    label_config = {}
    if config.get('label_type'):
        label_config['type'] = config['label_type']
    if label_config:
        nested['label_config'] = label_config

    for k in ('model_name', 'name', 'parent_model_id'):
        if config.get(k):
            nested[k] = config[k]
    # D3.4 walk-forward 滚动样本外验证
    if config.get('walk_forward'):
        nested['walk_forward'] = True
        if config.get('walk_forward_segments'):
            nested['walk_forward_segments'] = int(config['walk_forward_segments'])
    return nested


class ModelHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        # S6: 不发送 CORS 头。正常调用方为本地 Python 脚本和 Electron 主进程代理，
        # 均非浏览器，不受同源策略限制。移除通配符可阻止恶意网页跨域访问。
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, msg, code=400, error_code='BAD_REQUEST'):
        self._send_json({'error': msg, 'code': error_code}, code)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode('utf-8'))

    def do_OPTIONS(self):
        self._send_json({'status': 'ok'})

    def do_GET(self):
        _ensure_loaded()
        path = urlparse(self.path).path
        parts = [p for p in path.split('/') if p]

        if path == '/health':
            self._send_json({'status': 'ok', 'strategies': len(list_strategies()), 'models': len(ModelRegistry.list_models())})
        elif path == '/strategies':
            self._send_json(list_strategies())
        elif len(parts) == 2 and parts[0] == 'strategy':
            name = parts[1]
            try:
                meta = get(name)().metadata()
                self._send_json(meta)
            except KeyError:
                self._send_error('Strategy not found: ' + name, 404, 'NOT_FOUND')
        elif path == '/models':
            self._send_json(ModelRegistry.list_models())
        elif path == '/models/active':
            active = _get_active_model()
            if active and active.get('model_id'):
                try:
                    meta = ModelRegistry.get_meta(active['model_id'])
                    self._send_json({'model_id': active['model_id'], 'strategy': active.get('strategy', ''), 'meta': meta})
                except FileNotFoundError:
                    self._send_json({'model_id': '', 'strategy': '', 'meta': None})
            else:
                self._send_json({'model_id': '', 'strategy': '', 'meta': None})
        elif len(parts) == 2 and parts[0] == 'models':
            try:
                meta = ModelRegistry.get_meta(parts[1])
                cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'models', parts[1], 'config.json')
                config = {}
                if os.path.exists(cfg_path):
                    with open(cfg_path) as f:
                        config = json.load(f)
                self._send_json({'meta': meta, 'config': config})
            except FileNotFoundError:
                self._send_error('Model not found: ' + parts[1], 404, 'NOT_FOUND')
        elif len(parts) == 3 and parts[0] == 'train' and parts[2] == 'status':
            self._send_json(_pipeline.get_status(parts[1]))
        else:
            self._send_error('Unknown endpoint', 404, 'NOT_FOUND')

    def do_POST(self):
        _ensure_loaded()
        path = urlparse(self.path).path
        parts = [p for p in path.split('/') if p]

        if path == '/signal':
            body = self._read_body()
            name = body.get('strategy', '')
            bars = body.get('bars', [])
            params = body.get('params', {})
            symbol = body.get('symbol', '')
            if not name:
                # Shell strategy: resolve strategy from bound model_id, else active model
                params = dict(params or {})
                model_id = params.get('model_id', '')
                if model_id:
                    try:
                        meta = ModelRegistry.get_meta(model_id)
                        name = _ARCH_STRATEGY_MAP.get(meta.get('arch', ''), 'lstm_trend')
                    except FileNotFoundError:
                        self._send_error('Bound model not found: ' + model_id, 404, 'NOT_FOUND')
                        return
                else:
                    active = _get_active_model()
                    if active and active.get('strategy'):
                        name = active['strategy']
                        params['model_id'] = active.get('model_id', '')
                    else:
                        self._send_error('No strategy specified and no active model')
                        return
            if not bars:
                self._send_error('Missing bars data')
                return
            try:
                result = _compute_signal(name, bars, params, symbol)
                self._send_json(result)
            except KeyError:
                self._send_error('Unknown strategy: ' + name, 404, 'NOT_FOUND')
            except Exception as e:
                self._send_error(str(e), 500, 'INTERNAL')
        elif path == '/strategy':
            body = self._read_body()
            name = body.get('name', '')
            code = body.get('code', '')
            if not name or not code:
                self._send_error('Missing name or code')
                return
            # S6: AST static safety check
            is_safe, reason = _check_code_safety(code)
            if not is_safe:
                self._send_error('Strategy code safety check failed: ' + reason, 403, 'FORBIDDEN')
                return
            try:
                meta = save_strategy(name, code)
                self._send_json({'strategy': meta})
            except Exception as e:
                self._send_error(str(e), 500, 'INTERNAL')
        elif path == '/train':
            config = self._read_body()
            config = _normalize_train_config(config)
            task_id = _pipeline.start_train(config)
            self._send_json({'task_id': task_id})
        elif len(parts) == 3 and parts[0] == 'models' and parts[2] == 'activate':
            model_id = parts[1]
            body = {}
            try:
                body = self._read_body()
            except Exception:
                body = {}
            try:
                info = _set_active_model(model_id, force=bool(body.get('force')))
                self._send_json({'ok': True, 'model_id': model_id, 'degraded': info.get('degraded', False)})
            except ValueError as e:
                self._send_error(str(e), 409, 'DEGRADED')
            except Exception as e:
                self._send_error(str(e), 500, 'INTERNAL')
        else:
            self._send_error('Unknown endpoint', 404, 'NOT_FOUND')

    def do_PUT(self):
        _ensure_loaded()
        path = urlparse(self.path).path
        parts = [p for p in path.split('/') if p]

        if len(parts) == 2 and parts[0] == 'models':
            model_id = parts[1]
            patch = self._read_body()
            try:
                meta = ModelRegistry.update_meta(model_id, patch)
                self._send_json(meta)
            except FileNotFoundError:
                self._send_error('Model not found: ' + model_id, 404, 'NOT_FOUND')
            except Exception as e:
                self._send_error(str(e), 500, 'INTERNAL')
        else:
            self._send_error('Unknown endpoint', 404, 'NOT_FOUND')

    def do_DELETE(self):
        _ensure_loaded()
        path = urlparse(self.path).path
        parts = [p for p in path.split('/') if p]

        if len(parts) == 2 and parts[0] == 'strategy':
            try:
                delete_strategy(parts[1])
                self._send_json({'ok': True})
            except Exception as e:
                self._send_error(str(e), 500, 'INTERNAL')
        elif len(parts) == 2 and parts[0] == 'models':
            try:
                ModelRegistry.delete(parts[1])
                self._send_json({'ok': True})
            except Exception as e:
                self._send_error(str(e), 500, 'INTERNAL')
        else:
            self._send_error('Unknown endpoint', 404, 'NOT_FOUND')

    def log_message(self, format, *args):
        print('[model-server] ' + (format % args))


def run(port=18765):
    _ensure_loaded()
    server = HTTPServer(('127.0.0.1', port), ModelHandler)
    print('[model-server] listening on 127.0.0.1:' + str(port))
    server.serve_forever()


if __name__ == '__main__':
    port = int(os.environ.get('MODEL_SERVER_PORT', '18765'))
    run(port)
