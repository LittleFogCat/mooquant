# pytest fixtures shared across all tests

import sys
import os
import json
import pytest

# Ensure bridge dir is in path
BRIDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'bridge')
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)


@pytest.fixture
def sample_bars():
    # 60 bars of mock daily data with a trend.
    bars = []
    price = 35.0
    for i in range(60):
        change = 0.02 if i < 30 else -0.02
        c = max(0.01, price + change)
        bars.append({
            'date': f'2026-01-{i+1:02d}',
            'open': price, 'high': c + 0.1, 'low': c - 0.1,
            'close': c, 'volume': 1000000 + i * 10000,
        })
        price = c
    return bars


@pytest.fixture
def small_bars():
    # 30 bars for quick tests.
    bars = []
    price = 10.0
    for i in range(30):
        c = price + 0.1
        bars.append({
            'date': f'2026-03-{i+1:02d}',
            'open': price, 'high': c + 0.05, 'low': price - 0.05,
            'close': c, 'volume': 500000,
        })
        price = c
    return bars


@pytest.fixture
def tmp_model_dir(tmp_path, monkeypatch):
    # Redirect ModelRegistry to a temp directory.
    from training import model_registry
    # 本机环境问题：sklearn/GBDT 测试之后，pytest 的 tmp_path 首次创建目录会出现
    # ~32s 的异常 setup 慢速（拖慢全量套件）。改用 stdlib tempfile 直接建目录规避。
    import tempfile
    import shutil
    d = tempfile.mkdtemp(prefix='mookquant_models_')
    monkeypatch.setattr(model_registry, 'MODEL_DIR', d)
    # Clear cache
    model_registry.ModelRegistry._cache.clear()
    yield d
    shutil.rmtree(d, ignore_errors=True)
