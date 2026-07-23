# mookquant * HTTP model server
# Exposes model layer via HTTP for QMT shell strategy and local UI.
# Uses Python stdlib http.server - zero external dependencies.

import json
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Ensure bridge dir is in path
BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

from strategies.registry import load_all, get, list_strategies, save_strategy, delete_strategy
from strategies.base import Context
from training.model_registry import ModelRegistry
from training.pipeline import TrainPipeline
from training.builtin_models import ensure_builtin_models

# Active model persistence
ACTIVE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'models', 'active.json')


_ARCH_STRATEGY_MAP = {
    'lstm': 'lstm_trend',
}


def _get_active_model():
    """Return active model dict {model_id, strategy} or {}."""
    if os.path.exists(ACTIVE_FILE):
        try:
            with open(ACTIVE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _set_active_model(model_id):
    """Activate a model. Auto-detects strategy from model arch."""
    strategy = 'lstm_trend'
    try:
        meta = ModelRegistry.get_meta(model_id)
        arch = meta.get('arch', '')
        strategy = _ARCH_STRATEGY_MAP.get(arch, 'lstm_trend')
    except Exception:
        pass
    os.makedirs(os.path.dirname(ACTIVE_FILE), exist_ok=True)
    with open(ACTIVE_FILE, 'w') as f:
        json.dump({'model_id': model_id, 'strategy': strategy}, f, ensure_ascii=False)


# Global state
_pipeline = TrainPipeline()
_strategies_loaded = False


def _ensure_loaded():
    global _strategies_loaded
    if not _strategies_loaded:
        load_all()
        # Import ML models so they register
        try:
            import strategies.ml.models  # noqa
            import strategies.ml.builtin.lstm_trend  # noqa
        except ImportError:
            pass  # torch not available, ML strategies skip
        ensure_builtin_models()
        _strategies_loaded = True


def _compute_signal(strategy_name, bars, params, symbol):
    # Compute signal: instantiate strategy, feed bars, return last signal.
    strat_cls = get(strategy_name)
    # MLStrategyBase.on_after_init auto-uses active model when no model_id given.
    strat = strat_cls(params or {})
    ctx = Context()
    ctx.symbol = symbol or ''
    ctx.is_backtest = False
    ctx.period = '1d'
    strat.on_init(ctx)
    strat.on_after_init(ctx)
    signal = None
    for i, bar in enumerate(bars):
        ctx.bars = bars[:i + 1]
        ctx.barpos = i
        signal = strat.on_bar(bar, ctx)
    strat.on_stop(ctx)
    if signal is None:
        return {'action': 'hold', 'reason': '\u65e0\u4fe1\u53f7'}
    return signal.to_dict()


class ModelHandler(BaseHTTPRequestHandler):

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
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
                # Shell strategy: use active model's strategy
                active = _get_active_model()
                if active and active.get('strategy'):
                    name = active['strategy']
                    if not (params or {}).get('model_id'):
                        params = dict(params or {})
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
            try:
                meta = save_strategy(name, code)
                self._send_json({'strategy': meta})
            except Exception as e:
                self._send_error(str(e), 500, 'INTERNAL')
        elif path == '/train':
            config = self._read_body()
            task_id = _pipeline.start_train(config)
            self._send_json({'task_id': task_id})
        elif len(parts) == 3 and parts[0] == 'models' and parts[2] == 'activate':
            model_id = parts[1]
            try:
                _set_active_model(model_id)
                self._send_json({'ok': True, 'model_id': model_id})
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


def run(port=8765):
    _ensure_loaded()
    server = HTTPServer(('127.0.0.1', port), ModelHandler)
    print('[model-server] listening on 127.0.0.1:' + str(port))
    server.serve_forever()


if __name__ == '__main__':
    port = int(os.environ.get('MODEL_SERVER_PORT', '8765'))
    run(port)
