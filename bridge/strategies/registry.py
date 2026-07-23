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


def _user_dir() -> str:
    """返回用户策略目录的绝对路径。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "data", "strategies", "user"))


def _is_valid_name(name: str) -> bool:
    """校验策略名：合法标识符，不以数字开头，仅字母/数字/下划线。"""
    if not name or not isinstance(name, str):
        return False
    if name[0].isdigit():
        return False
    return name.replace("_", "").isalnum()


def _is_builtin(name: str) -> bool:
    """判断是否为内置策略（不允许覆盖/删除）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.exists(os.path.join(here, "builtin", name + ".py"))


def get_strategy_code(name: str) -> str:
    """Get strategy source code.

    User strategies: read from data/strategies/user/{name}.py.
    Built-in strategies: use inspect.getsource.
    """
    user_path = os.path.join(_user_dir(), name + '.py')
    if os.path.exists(user_path):
        with open(user_path, 'r', encoding='utf-8') as f:
            return f.read()
    strat_cls = get(name)
    import inspect
    return inspect.getsource(strat_cls)


def save_strategy(name: str, code: str) -> dict:
    """保存用户策略代码到 data/strategies/user/ 并即时注册。

    用户通过 UI 编写策略代码后调用此方法，代码写入 ``{user_dir}/{name}.py``
    并即时 import 注册，无需重启。

    Args:
        name: 策略类型名（同时用作文件名，需合法标识符；代码中类的
            ``name`` 属性必须与此一致）
        code: 策略 Python 源码（须含一个 ``StrategyBase`` 子类）

    Returns:
        策略元数据（``StrategyBase.metadata()`` 返回值）

    Raises:
        ValueError: 名称不合法、与内置策略冲突、代码中类属性 name 不匹配
        RuntimeError: 代码加载/注册失败（含语法错误等）
    """
    if not _is_valid_name(name):
        raise ValueError("策略名只能包含字母、数字、下划线，且不能以数字开头: " + str(name))
    if _is_builtin(name):
        raise ValueError("不能覆盖内置策略: " + name)

    udir = _user_dir()
    os.makedirs(udir, exist_ok=True)
    fpath = os.path.join(udir, name + ".py")

    with open(fpath, "w", encoding="utf-8") as f:
        f.write(code)

    # 移除旧模块缓存与旧注册（修改策略时需重新加载）
    mod_full = "strategies.user." + name
    sys.modules.pop(mod_full, None)
    _REGISTRY.pop(name, None)

    _ensure_bridge_in_path()
    try:
        spec = importlib.util.spec_from_file_location(mod_full, fpath)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[mod_full] = mod
        _collect_and_register(mod, name)
    except Exception:
        import traceback
        raise RuntimeError("策略代码加载失败:\n" + traceback.format_exc())

    if name not in _REGISTRY:
        raise ValueError(
            "注册失败：代码中未找到 name='" + name + "' 的 StrategyBase 子类")
    return _REGISTRY[name]().metadata()


def delete_strategy(name: str) -> bool:
    """删除用户策略：移除文件 + 注销注册。

    Args:
        name: 策略类型名

    Returns:
        True 表示删除成功

    Raises:
        ValueError: 不存在或为内置策略
    """
    if _is_builtin(name):
        raise ValueError("不能删除内置策略: " + name)
    fpath = os.path.join(_user_dir(), name + ".py")
    if not os.path.exists(fpath):
        raise ValueError("用户策略不存在: " + name)
    os.remove(fpath)
    _REGISTRY.pop(name, None)
    sys.modules.pop("strategies.user." + name, None)
    return True
