"""
mookquant * 策略基类、信号模型、上下文对象

设计要点（参考 docs/api/QMT_API_Full.md）：
  1. 生命周期 on_init/on_after_init/on_bar/on_tick/on_stop 对应 QMT
     init/after_init/handlebar/subscribe/stop。
  2. on_after_init 与 on_init 分离：on_init 只做参数/订阅，on_after_init
     做数据预热与全量预计算（QMT 文档：init 中 gmd 只能读本地数据）。
  3. 跨 bar 状态用 self._state（面向对象），避开 QMT ContextInfo 逐 bar
     回退的坑（QMT 文档 3.5/3.6 警告）。
  4. Context 接口取各平台能力交集（最大公约数），策略不直接接触 xtquant。
  5. Signal.target_position 对应 QMT order_target_percent（调仓至比例）。
"""

from dataclasses import dataclass, field
from types import ModuleType
from typing import Optional, List, Dict, Any


# ----------------------------------------------------------------------
# 信号模型
# ----------------------------------------------------------------------
@dataclass
class Signal:
    """单标的交易信号。

    Attributes:
        action: 交易动作 "buy" | "sell" | "hold"
        reason: 信号原因（用于日志/展示）
        strength: 信号强度 [0,1]，可用于仓位管理
        target_position: 目标仓位比例 [0,1]，None 表示不调仓。
            对应 QMT order_target_percent。设置后由执行层据此调仓。
        indicators: 当前指标快照，用于日志/展示
    """
    action: str
    reason: str = ""
    strength: float = 1.0
    target_position: Optional[float] = None
    indicators: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "strength": self.strength,
            "targetPosition": self.target_position,
            "indicators": self.indicators,
        }


@dataclass
class PortfolioSignal:
    """组合调仓信号（多标的选股策略用）。

    与单标的 Signal 区分：输出一个目标持仓组合 {symbol: 目标比例}，
    适合多因子选股、等权组合等场景（参考 docs/api/backtrade_sample.py）。

    Attributes:
        targets: {symbol: target_percent} 目标持仓比例
        reason: 调仓原因
    """
    targets: Dict[str, float] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"targets": dict(self.targets), "reason": self.reason}


# ----------------------------------------------------------------------
# 上下文对象
# ----------------------------------------------------------------------
class Context:
    """策略上下文（跨平台最大公约数）。

    回测引擎与实盘执行器各自提供 Context 的具体实现，策略只依赖本接口，
    不直接接触 xtquant 或 QMT，从而保证策略代码可跨平台通用/导出。

    平台能力差异通过 has_feature() 探测，策略据此降级或提示。
    """

    def __init__(self):
        self.bars: List[dict] = []          # 当前标的历史K线（到当前bar为止）
        self.position: Optional[object] = None    # 当前持仓对象（平台特定）
        self.account: Optional[object] = None     # 资金对象（平台特定）
        self.indicators: Optional[ModuleType] = None  # 指标库引用
        self.is_backtest: bool = False      # 是否回测模式（对应 QMT do_back_test）
        self.barpos: int = 0                # 当前bar索引（对应 QMT barpos）
        self.symbol: str = ""               # 当前标的
        self.period: str = "1d"             # 当前周期（对应 QMT period）
        self._platform: str = "mookquant"   # 运行平台标识
        self._features: set = set()         # 平台支持的能力集合

    @property
    def platform(self) -> str:
        """当前运行平台：mookquant / qmt / ptrade / joinquant ..."""
        return self._platform

    def has_feature(self, name: str) -> bool:
        """探测平台是否支持某能力（如 sector_stocks/financial_data/l2_quote）。

        平台特有能力（QMT 板块成分股、PTRade L2 等）无法跨平台通用时，
        策略应先探测再使用，避免在不支持的平台崩溃。
        """
        return name in self._features

    # -- 取数接口（由具体实现覆盖）--
    def get_bars(self, symbol: str, count: int, period: str = "1d") -> List[dict]:
        """获取单标的历史K线，返回 list[dict]。"""
        raise NotImplementedError

    def get_bars_panel(self, symbols: List[str], count: int, period: str = "1d"):
        """获取多标的面板数据（选股策略用），返回 {symbol: list[dict]} 或 DataFrame。

        多因子选股等场景需要一次拉一篮子股票做向量化（参考
        backtrade_sample.py 的 get_market_data_ex 多标的用法）。
        """
        raise NotImplementedError

    def get_snapshot(self, symbol: str) -> dict:
        """获取实时快照。"""
        raise NotImplementedError

    def get_sector_stocks(self, sector: str) -> List[str]:
        """获取板块成分股（需平台支持，先用 has_feature 探测）。"""
        raise NotImplementedError

    def get_instrument_detail(self, symbol: str) -> dict:
        """获取合约详情（总股本、上市日期等，需平台支持）。"""
        raise NotImplementedError

    # -- 下单接口（由具体实现覆盖）--
    def order_target_percent(self, symbol: str, percent: float):
        """调仓至目标比例（对应 QMT order_target_percent，跨平台明星函数）。"""
        raise NotImplementedError

    def order_target_value(self, symbol: str, value: float):
        """调仓至目标持仓金额。"""
        raise NotImplementedError

    def order_shares(self, symbol: str, shares: int):
        """按股数下单。"""
        raise NotImplementedError

    def cancel_order(self, order_id: str):
        """撤单。"""
        raise NotImplementedError

    # -- 查询接口（由具体实现覆盖）--
    def get_position(self, symbol: str) -> Any:
        """查询单标的持仓。"""
        raise NotImplementedError

    def get_account(self) -> Any:
        """查询资金。"""
        raise NotImplementedError


# ----------------------------------------------------------------------
# 策略基类
# ----------------------------------------------------------------------
class StrategyBase:
    """策略基类：所有策略继承此类，实现 on_bar 即可。

    生命周期（参考 QMT）：
        on_init       -> QMT init        启动一次，参数/订阅
        on_after_init -> QMT after_init  init 后一次，数据预热/预计算
        on_bar        -> QMT handlebar   每根K线（必须实现）
        on_tick       -> QMT subscribe   tick级（trigger_mode='tick'时）
        on_stop       -> QMT stop        停止前一次，仅清理

    元数据（类属性）供 UI 自动生成参数表单与策略列表：
        name/display_name/description/version/trigger_mode/params_schema
    """

    # -- 元数据（类属性，子类覆盖）--
    name: str = "base"
    display_name: str = "策略基类"
    description: str = ""
    version: str = "1.0"
    trigger_mode: str = "bar"          # "bar" | "tick" | "schedule"
    params_schema: List[Dict[str, Any]] = []

    def __init__(self, params: Optional[dict] = None):
        self.params: Dict[str, Any] = self._merge_defaults(params or {})
        self._state: Dict[str, Any] = {}   # 策略实例状态，跨bar保留

    def _merge_defaults(self, params: dict) -> dict:
        """合并参数默认值（从 params_schema 取 default）。"""
        merged = {}
        for p in self.params_schema:
            merged[p["key"]] = p.get("default")
        merged.update(params or {})
        return merged

    # -- 生命周期（子类按需覆盖）--
    def on_init(self, ctx: Context):
        """初始化：订阅、设参数。此时不宜取最新行情（参考 QMT 文档）。"""
        pass

    def on_after_init(self, ctx: Context):
        """初始化后：数据预热、全量预计算（参考 backtrade_sample.py 的 after_init）。"""
        pass

    def on_bar(self, bar: dict, ctx: Context):
        """每根K线触发。必须实现。返回 Signal/PortfolioSignal/None。"""
        raise NotImplementedError

    def on_tick(self, tick: dict, ctx: Context):
        """tick级触发。trigger_mode='tick'时用。返回 Signal/None。"""
        return None

    def on_stop(self, ctx: Context):
        """停止清理。仅轻量操作，不可下单/撤单（参考 QMT stop 警告）。"""
        pass

    # -- 元数据导出 --
    def metadata(self) -> dict:
        """返回策略元数据（供 UI 与 registry）。"""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "version": self.version,
            "trigger_mode": self.trigger_mode,
            "params_schema": self.params_schema,
        }
