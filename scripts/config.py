"""
项目配置中心 (Phase 4.1)

统一管理路径、参数、模型配置。支持 YAML 文件覆盖默认值。

用法：
    from config import config
    print(config.data_dir)
    print(config.model.n_estimators)

配置文件优先级：
    1. 环境变量 QUANTDEMO_CONFIG 指定的路径
    2. 项目根目录 config.yaml
    3. 代码中的 DEFAULT_CONFIG
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# 默认配置
# ═══════════════════════════════════════════════════════════════

# 自动检测项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class DataConfig:
    """数据路径配置。"""
    root: str = os.path.join(_PROJECT_ROOT, "data")
    sh_dir: str = os.path.join(_PROJECT_ROOT, "data", "sh")
    sz_dir: str = os.path.join(_PROJECT_ROOT, "data", "sz")
    bj_dir: str = os.path.join(_PROJECT_ROOT, "data", "bj")
    tmp_dir: str = os.path.join(_PROJECT_ROOT, "tmp")
    tdx_url: str = "https://data.tdx.com.cn/vipdoc/hsjday.zip"
    tdx_zip: str = os.path.join(_PROJECT_ROOT, "tmp", "hsjday.zip")
    tdx_extract: str = os.path.join(_PROJECT_ROOT, "tmp", "extract")


@dataclass
class ModelConfig:
    """模型参数配置。"""
    name: str = "randomforest"
    n_estimators: int = 100
    max_depth: int = 6
    random_state: int = 42
    cv_splits: int = 5


@dataclass
class FeatureConfig:
    """特征工程配置。"""
    base: list[str] = field(default_factory=lambda: [
        "ret_1", "ret_5", "ret_10", "ret_20",
        "volatility_5", "volatility_10",
        "volume_ratio", "pos_20",
    ])
    momentum: bool = True
    volume_features: bool = True
    price_features: bool = True
    label_forward_days: int = 5


@dataclass
class BacktestConfig:
    """回测配置。"""
    initial_capital: float = 100000
    max_position_pct: float = 0.2
    top_n: int = 5
    stamp_duty: float = 0.0005
    commission: float = 0.0003
    slippage: float = 0.001


@dataclass
class SignalConfigData:
    """信号生成配置。"""
    buy_confidence: float = 0.60
    sell_confidence: float = 0.60
    min_prob: float = 0.45
    hold_cooling_days: int = 3


@dataclass
class ReportConfig:
    """报告输出配置。"""
    dir: str = os.path.join(_PROJECT_ROOT, "reports")
    format: str = "csv"  # csv / json / both
    verbose: bool = True


@dataclass
class LogConfig:
    """日志配置。"""
    level: str = "INFO"
    dir: str = os.path.join(_PROJECT_ROOT, "logs")
    to_file: bool = True
    to_console: bool = True


@dataclass
class QMTConfig:
    """QMT 环境配置。"""
    xtquant_path: str = r"D:\software\python\Lib\site-packages"
    qmt_exe: str = r"D:\国金QMT交易端模拟\bin.x64\XtItClient.exe"
    auto_connect: bool = False


@dataclass
class ProjectConfig:
    """顶层配置。"""
    project_root: str = _PROJECT_ROOT
    data: DataConfig = field(default_factory=DataConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    signals: SignalConfigData = field(default_factory=SignalConfigData)
    report: ReportConfig = field(default_factory=ReportConfig)
    log: LogConfig = field(default_factory=LogConfig)
    qmt: QMTConfig = field(default_factory=QMTConfig)
    models_dir: str = os.path.join(_PROJECT_ROOT, "models")

    def ensure_dirs(self):
        """创建配置中引用的所有目录。"""
        for d in [self.data.tmp_dir, self.models_dir,
                   self.report.dir, self.log.dir]:
            os.makedirs(d, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# 从 YAML 加载
# ═══════════════════════════════════════════════════════════════

def _load_yaml_overrides(config_obj: ProjectConfig,
                         yaml_path: str) -> ProjectConfig:
    """从 YAML 文件覆盖配置。"""
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except ImportError:
        # pip install pyyaml 未安装时静默跳过
        return config_obj
    except FileNotFoundError:
        return config_obj
    except Exception:
        return config_obj

    _apply_dict(config_obj, data)
    return config_obj


def _apply_dict(obj, data: dict):
    """递归应用字典到 dataclass 字段。"""
    for key, value in data.items():
        if hasattr(obj, key):
            sub_obj = getattr(obj, key)
            if isinstance(sub_obj, object) and hasattr(sub_obj, "__dataclass_fields__"):
                if isinstance(value, dict):
                    _apply_dict(sub_obj, value)
            else:
                setattr(obj, key, value)


# ═══════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════

def _find_config_yaml() -> Optional[str]:
    """查找 config.yaml。"""
    candidates = [
        os.environ.get("QUANTDEMO_CONFIG"),
        os.path.join(_PROJECT_ROOT, "config.yaml"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


# 构建全局配置实例
config = ProjectConfig()
_yaml_path = _find_config_yaml()
if _yaml_path:
    config = _load_yaml_overrides(config, _yaml_path)

config.ensure_dirs()


def setup_logging(name: str = "quantdemo") -> "logging.Logger":
    """初始化日志系统 (Phase 4.2)。"""
    import logging

    logger = logging.getLogger(name)
    level = getattr(logging, config.log.level.upper(), logging.INFO)
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if config.log.to_file:
        os.makedirs(config.log.dir, exist_ok=True)
        fh = logging.FileHandler(
            os.path.join(config.log.dir, f"{name}.log"),
            encoding="utf-8",
        )
        fh.setLevel(level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    if config.log.to_console:
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    return logger


if __name__ == "__main__":
    print(f"项目根目录: {config.project_root}")
    print(f"数据目录:   {config.data.root}")
    print(f"模型目录:   {config.models_dir}")
    print(f"报告目录:   {config.report.dir}")
    print(f"日志目录:   {config.log.dir}")
    print(f"QMT 路径:   {config.qmt.xtquant_path}")
    print(f"特征列:     {config.features.base}")
    print(f"模型:       {config.model.name} (n={config.model.n_estimators})")

    logger = setup_logging()
    logger.info("配置加载成功")
    print("[OK] 日志系统就绪")
