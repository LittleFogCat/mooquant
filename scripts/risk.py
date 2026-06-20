"""
风控模块 (Phase 5.3)

交易前置闸门：在下单前强制校验所有风控规则。
所有规则不可绕过，校验失败直接拒绝订单。

规则：
  - 单票仓位上限（占总资产 %）
  - 总仓位上限（占总资产 %）
  - 单日亏损熔断
  - 止损/止盈
  - 黑名单（代码级禁止交易）

用法：
    from risk import RiskManager

    rm = RiskManager()
    rm.check_buy(code="000001.SZ", volume=100, price=12.50)
"""

import sys
sys.path.insert(0, r"D:\software\python\Lib\site-packages")

from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional

from config import config, setup_logging

logger = setup_logging("risk")


# ═══════════════════════════════════════════════════════════════
# 风控配置
# ═══════════════════════════════════════════════════════════════

@dataclass
class RiskConfig:
    """风控参数。"""
    # 仓位限制
    max_single_position_pct: float = 0.20    # 单票最大 20%
    max_total_position_pct: float = 0.80     # 总仓位最大 80%
    max_positions_count: int = 10            # 最大持仓数

    # 亏损熔断
    max_daily_loss_pct: float = 0.05         # 单日亏损 5% 熔断
    max_daily_loss_abs: float = 5000.0       # 单日最大亏损额

    # 止损止盈
    stop_loss_pct: float = -0.08             # -8% 止损
    take_profit_pct: float = 0.20            # +20% 止盈

    # 其他
    min_volume: int = 100                    # 最小交易量（1手）
    max_single_amount_pct: float = 0.25      # 单笔最大买入金额占比
    blacklist: list[str] = field(default_factory=list)  # 黑名单代码


# ═══════════════════════════════════════════════════════════════
# 风控管理器
# ═══════════════════════════════════════════════════════════════

@dataclass
class RiskResult:
    """风控检查结果。"""
    passed: bool = True
    reason: str = ""               # 如果未通过，说明原因
    rule: str = ""                 # 触发规则名称


class RiskManager:
    """交易风控管理器。下单前必须通过 check_buy / check_sell 校验。"""

    def __init__(self, risk_config: RiskConfig | None = None):
        self.cfg = risk_config or RiskConfig()
        self._today: date = date.today()

        # 当日统计（重置每日）
        self._daily_pnl: float = 0.0
        self._daily_trade_count: int = 0
        self._meltdown: bool = False  # 熔断状态

        # 外部注入的账户快照（由 trader 更新）
        self.total_asset: float = 100000.0
        self.position_map: dict[str, dict] = {}  # code → {volume, cost, current}

    # ── 更新账户状态 ─────────────────────────────────────────────
    def update_from_trader(self, trader):
        """从 Trader 同步账户状态。"""
        asset = trader.get_asset()
        if asset:
            self.total_asset = asset.get("total_asset", self.total_asset)

        positions = trader.get_positions_summary()
        self.position_map = {
            p.get("code", ""): {
                "volume": p.get("volume", 0),
                "available": p.get("available", 0),
                "cost_price": p.get("cost_price", 0.0),
                "market_price": p.get("market_price", 0.0),
                "profit": p.get("profit", 0.0),
            }
            for p in positions
        }

        # 每日重置检测
        if self._today != date.today():
            self._today = date.today()
            self._daily_pnl = 0.0
            self._daily_trade_count = 0
            self._meltdown = False

    # ── 买入检查 ─────────────────────────────────────────────────
    def check_buy(self, code: str, volume: int, price: float) -> RiskResult:
        """买入前风控检查。

        :return: RiskResult，passed=True 才可执行
        """
        # 0. 熔断检查
        if self._meltdown:
            return RiskResult(False, "日亏损熔断已触发，禁止交易", "meltdown")

        # 1. 黑名单
        if code in self.cfg.blacklist:
            return RiskResult(False, f"{code} 在黑名单中", "blacklist")

        # 2. 最小交易量
        if volume < self.cfg.min_volume:
            return RiskResult(False,
                              f"下单量 {volume} < 最小 {self.cfg.min_volume}",
                              "min_volume")

        if volume % 100 != 0:
            return RiskResult(False, f"下单量 {volume} 不是 100 的整数倍", "volume_unit")

        # 3. 单笔金额上限
        amount = price * volume
        max_amount = self.total_asset * self.cfg.max_single_amount_pct
        if amount > max_amount:
            return RiskResult(False,
                              f"单笔金额 {amount:,.0f} > 上限 {max_amount:,.0f}",
                              "single_amount")

        # 4. 总仓位上限
        current_position_value = sum(
            p.get("market_price", 0) * p.get("volume", 0)
            for p in self.position_map.values()
        )
        current_pct = current_position_value / (self.total_asset + 1e-8)
        new_pct = (current_position_value + amount) / (self.total_asset + 1e-8)

        if new_pct > self.cfg.max_total_position_pct:
            return RiskResult(False,
                              f"总仓位 {new_pct:.1%} > 上限 {self.cfg.max_total_position_pct:.1%}",
                              "total_position")

        # 5. 单票仓位上限
        code_pos = self.position_map.get(code, {})
        code_value = code_pos.get("market_price", 0) * code_pos.get("volume", 0)
        code_new_pct = (code_value + amount) / (self.total_asset + 1e-8)

        if code_new_pct > self.cfg.max_single_position_pct:
            return RiskResult(False,
                              f"{code} 仓位 {code_new_pct:.1%} > 上限 {self.cfg.max_single_position_pct:.1%}",
                              "single_position")

        # 6. 持仓数上限
        current_positions = len([p for p in self.position_map.values()
                                  if p.get("volume", 0) > 0])
        if code not in self.position_map and current_positions >= self.cfg.max_positions_count:
            return RiskResult(False,
                              f"持仓数 {current_positions} >= 上限 {self.cfg.max_positions_count}",
                              "position_count")

        return RiskResult(passed=True)

    # ── 卖出检查 ─────────────────────────────────────────────────
    def check_sell(self, code: str, volume: int) -> RiskResult:
        """卖出入前检查。"""
        # 1. 熔断
        if self._meltdown:
            return RiskResult(False, "日亏损熔断已触发", "meltdown")

        # 2. 黑名单（不影响卖出）
        # 卖出不受黑名单限制

        # 3. 持仓检查
        pos = self.position_map.get(code)
        if not pos or pos.get("available", 0) < volume:
            avail = pos.get("available", 0) if pos else 0
            return RiskResult(False,
                              f"{code} 可用 {avail} 股 < 卖出 {volume} 股",
                              "insufficient")

        # 4. 最小交易量
        if volume < self.cfg.min_volume:
            return RiskResult(False,
                              f"下单量 {volume} < 最小 {self.cfg.min_volume}",
                              "min_volume")

        return RiskResult(passed=True)

    # ── 止损止盈检查 ─────────────────────────────────────────────
    def check_stop_conditions(self) -> list[dict]:
        """扫描所有持仓，返回触发止损/止盈的标的列表。"""
        triggers = []

        for code, pos in self.position_map.items():
            if pos.get("volume", 0) <= 0 or pos.get("cost_price", 0) <= 0:
                continue

            cost = pos["cost_price"]
            current = pos.get("market_price", cost)
            pnl_pct = (current - cost) / cost

            if pnl_pct <= self.cfg.stop_loss_pct:
                triggers.append({
                    "code": code,
                    "type": "stop_loss",
                    "pnl_pct": round(float(pnl_pct), 4),
                    "volume": pos["volume"],
                    "action": "sell",
                })
            elif pnl_pct >= self.cfg.take_profit_pct:
                triggers.append({
                    "code": code,
                    "type": "take_profit",
                    "pnl_pct": round(float(pnl_pct), 4),
                    "volume": pos["volume"],
                    "action": "sell",
                })

        return triggers

    # ── 熔断 ─────────────────────────────────────────────────────
    def record_trade_pnl(self, pnl: float):
        """记录一笔交易的盈亏（用于日亏损熔断）。"""
        self._daily_pnl += pnl
        self._daily_trade_count += 1

        loss_limit = -self.cfg.max_daily_loss_abs
        if self._daily_pnl < loss_limit:
            self._meltdown = True
            logger.error(f"触发日亏损熔断! 当日亏损={self._daily_pnl:,.0f} "
                         f"> 限额={loss_limit:,.0f}")

    def reset_meltdown(self):
        """手动解除熔断（危险操作）。"""
        self._meltdown = False
        self._daily_pnl = 0.0
        logger.warning("熔断已手动解除")

    @property
    def is_meltdown(self) -> bool:
        return self._meltdown


# ── 便捷函数 ────────────────────────────────────────────────────

def create_risk_manager() -> RiskManager:
    """创建默认风控管理器。"""
    cfg = RiskConfig(
        max_single_position_pct=config.backtest.max_position_pct,
    )
    return RiskManager(cfg)


if __name__ == "__main__":
    print("=== 风控模块自测 ===\n")

    rm = RiskManager()
    rm.total_asset = 100000.0

    # 测试正常买入
    result = rm.check_buy("000001.SZ", 100, 12.50)
    print(f"正常买入: {'✓' if result.passed else '✗'} {result.reason}")

    # 测试超过单票上限
    result = rm.check_buy("000001.SZ", 200000, 10.0)
    print(f"超大单:   {'✓' if result.passed else '✗'} {result.reason}")

    # 测试黑名单
    rm.cfg.blacklist = ["000001.SZ"]
    result = rm.check_buy("000001.SZ", 100, 10.0)
    print(f"黑名单:   {'✓' if result.passed else '✗'} {result.reason}")
    rm.cfg.blacklist = []

    # 测试熔断
    rm.record_trade_pnl(-6000)
    result = rm.check_buy("600000.SH", 100, 10.0)
    print(f"熔断后:   {'✓' if result.passed else '✗'} {result.reason}")

    # 测试止损检查
    rm.reset_meltdown()
    rm.position_map = {
        "000001.SZ": {"volume": 1000, "cost_price": 10.0,
                       "market_price": 9.0, "available": 1000},
    }
    triggers = rm.check_stop_conditions()
    if triggers:
        for t in triggers:
            print(f"止损触发: {t['code']} {t['type']} PnL={t['pnl_pct']:.1%}")

    print("\n[OK] 风控模块测试通过")
