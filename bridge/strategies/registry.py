"""
mookquant * 策略自动注册

扫描 builtin/ 与 data/strategies/user/ 目录，自动 import 并注册策略类。
用户加新策略 = 丢一个 .py 到 data/strategies/user/，重启即生效，
框架代码零修改。

注册后可通过 get(name) 取策略类，list_strategies() 列出元数据。
"""

import os
import sys
import importlib
import importlib.util
import inspect
from typing import Dict, List, Type

from .base import StrategyBase

_REGISTRY: Dict[str, Type[StrategyBase]] = {}


def register(cls):
    """注册策略类（也可作装饰器使用）。非 StrategyBase 子类被忽略。"""
    if not inspect.isclass(cls) or not issubclass(cls, StrategyBase):
        return cls
    if cls is StrategyBase:
        return cls
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> Type[StrategyBase]:
    """获取策略类。未注册时抛 KeyError。"""
    if name not in _REGISTRY:
        raise KeyError("未知策略类型: {}（已注册: {}）".format(name, list(_REGISTRY.keys())))
    return _REGISTRY[name]


def list_strategies() -> List[dict]:
    """列出所有已注册策略的元数据（供 UI 渲染参数表单）。"""
    return [cls().metadata() for cls in _REGISTRY.values()]


def _ensure_bridge_in_path():
    """确保 bridge 目录在 sys.path，使策略文件可 from strategies.base import ..."""
    bridge_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if bridge_dir not in sys.path:
        sys.path.insert(0, bridge_dir)


def _load_builtin(builtin_dir: str):
    """加载 builtin/ 目录下的策略（作为 strategies.builtin.xxx 包模块导入）。"""
    if not os.path.isdir(builtin_dir):
        return
    _ensure_bridge_in_path()
    init_file = os.path.join(builtin_dir, "__init__.py")
    if not os.path.exists(init_file):
        with open(init_file, "w", encoding="utf-8") as f:
            f.write('"""mookquant builtin strategies"""\n')
    for fname in sorted(os.listdir(builtin_dir)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        modname = fname[:-3]
        full = "strategies.builtin." + modname
        try:
            importlib.import_module(full)
            mod = sys.modules[full]
            _collect_and_register(mod, modname)
        except Exception as e:
            sys.stderr.write("[registry] load builtin {} failed: {}\n".format(full, e))
            import traceback
            traceback.print_exc(file=sys.stderr)


def _load_user(user_dir: str):
    """加载用户策略目录（用 spec_from_file_location，不依赖包路径）。"""
    if not os.path.isdir(user_dir):
        return
    _ensure_bridge_in_path()
    for fname in sorted(os.listdir(user_dir)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        modname = fname[:-3]
        fpath = os.path.join(user_dir, fname)
        try:
            spec = importlib.util.spec_from_file_location("strategies.user." + modname, fpath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _collect_and_register(mod, modname)
        except Exception as e:
            sys.stderr.write("[registry] load user {} failed: {}\n".format(fpath, e))
            import traceback
            traceback.print_exc(file=sys.stderr)


def _collect_and_register(mod, modname: str):
    """收集模块中定义的 StrategyBase 子类并注册。"""
    for attr in dir(mod):
        obj = getattr(mod, attr)
        if (inspect.isclass(obj) and issubclass(obj, StrategyBase)
                and obj is not StrategyBase
                and obj.__module__ == mod.__name__):
            register(obj)


def load_all() -> List[str]:
    """加载所有策略：先 builtin 再 user。返回已注册策略名列表。"""
    _REGISTRY.clear()
    here = os.path.dirname(os.path.abspath(__file__))
    builtin_dir = os.path.join(here, "builtin")
    user_dir = os.path.join(here, "..", "..", "data", "strategies", "user")
    _load_builtin(builtin_dir)
    _load_user(os.path.abspath(user_dir))
    return list(_REGISTRY.keys())
