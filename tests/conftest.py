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
    monkeypatch.setattr(model_registry, 'MODEL_DIR', str(tmp_path / 'models'))
    # Clear cache
    model_registry.ModelRegistry._cache.clear()
    model_registry.ModelRegistry._cache_order.clear()
    return str(tmp_path / 'models')
