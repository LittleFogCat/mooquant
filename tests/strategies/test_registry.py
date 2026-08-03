import os
import pytest
import strategies.registry as reg
from strategies.registry import (
    load_all, list_strategies, get,
    _validate_strategy_code, save_strategy, delete_strategy,
)

def test_load_all():
    names = load_all()
    assert "ma_cross" in names

def test_list_strategies():
    load_all()
    strats = list_strategies()
    assert len(strats) >= 3
    names = [s["name"] for s in strats]
    assert "ma_cross" in names

def test_get_strategy():
    load_all()
    cls = get("ma_cross")
    assert cls.name == "ma_cross"


# ---- S6.2: AST dangerous-import check ----

def test_validate_safe_code():
    """S6.2: safe imports pass AST check"""
    _validate_strategy_code("from strategies.base import StrategyBase")
    _validate_strategy_code("import math\nimport numpy")

def test_validate_dangerous_code():
    """S6.2: dangerous imports are blocked"""
    for code in ["import os", "import subprocess", "from shutil import move",
                 "import socket", "from pickle import loads", "import ctypes"]:
        with pytest.raises(ValueError):
            _validate_strategy_code(code)

def test_validate_syntax_error():
    """S6.2: syntax errors are caught as ValueError"""
    with pytest.raises(ValueError):
        _validate_strategy_code("def (:")

def test_save_rejects_dangerous_code(tmp_path, monkeypatch):
    """S6.2: save_strategy rejects code with dangerous imports"""
    monkeypatch.setattr(reg, "_user_dir", lambda: str(tmp_path))
    evil_code = (
        "import os\n"
        "from strategies.base import StrategyBase\n"
        "class evil(StrategyBase):\n"
        '    name = "evil"\n'
        "    def on_bar(self, b, c): pass\n"
    )
    with pytest.raises(ValueError):
        save_strategy("evil", evil_code)

def test_delete_soft_delete(tmp_path, monkeypatch):
    """S6.3: delete moves file to .trash instead of removing"""
    monkeypatch.setattr(reg, "_user_dir", lambda: str(tmp_path))
    safe_code = (
        "from strategies.base import StrategyBase\n"
        "class test_del(StrategyBase):\n"
        '    name = "test_del"\n'
        "    def on_bar(self, bar, ctx): return None\n"
    )
    save_strategy("test_del", safe_code)
    assert os.path.exists(os.path.join(str(tmp_path), "test_del.py"))

    delete_strategy("test_del")
    assert not os.path.exists(os.path.join(str(tmp_path), "test_del.py"))

    trash_dir = os.path.join(str(tmp_path), ".trash")
    assert os.path.isdir(trash_dir)
    trashed = os.listdir(trash_dir)
    assert len(trashed) == 1
    assert trashed[0].startswith("test_del_")
