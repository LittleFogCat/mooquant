"""
QMT 环境检测与连接模块
"""

import sys
import subprocess
from pathlib import Path

# xtquant 安装在系统 Python 下，需要手动加 path
XTSITE = r"D:\software\python\Lib\site-packages"
if XTSITE not in sys.path:
    sys.path.insert(0, XTSITE)

# ── 检测 xtquant ─────────────────────────────────────────────────
def check_xtquant() -> dict:
    """检测 xtquant 是否安装，返回版本信息"""
    try:
        import xtquant
        return {"ok": True, "version": getattr(xtquant, "__version__", "unknown")}
    except ImportError:
        return {"ok": False, "version": None}


# ── 检测 QMT 客户端进程 ──────────────────────────────────────────
def check_qmt_process() -> dict:
    """检测 QMT 交易端是否正在运行"""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq XtItClient.exe"],
            capture_output=True, timeout=5,
            encoding="gbk", errors="replace",
        )
        running = "XtItClient.exe" in result.stdout
        return {"ok": running}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 连接数据服务 ─────────────────────────────────────────────────
def connect_data(timeout_sec: int = 5) -> dict:
    """连接 QMT 数据服务（需先启动 QMT 客户端）"""
    try:
        from xtquant import xtdata
        xtdata.connect()
        return {"ok": True, "msg": "连接成功"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ── 获取市场列表（验证连接）───────────────────────────────────────
def get_markets() -> dict:
    """获取支持的市场列表"""
    try:
        from xtquant.xtdata import get_markets
        markets = get_markets()
        return {"ok": True, "markets": markets}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ── 全量环境检测 ─────────────────────────────────────────────────
def full_check() -> dict:
    """一键全量检测"""
    result = {
        "xtquant": check_xtquant(),
        "qmt_process": check_qmt_process(),
    }
    result["data_service"] = connect_data() if result["qmt_process"]["ok"] \
        else {"ok": False, "msg": "QMT 客户端未运行，跳过"}
    return result
