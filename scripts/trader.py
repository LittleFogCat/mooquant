"""
交易执行模块 (Phase 5.1 + 5.4 + 5.5)

封装 xtquant.xttrader，提供：
  - connect / disconnect
  - 下单 (买/卖，限价/市价) / 撤单
  - 查询持仓、委托、成交、资产
  - 回调注册（异步通知）
  - Dry-run 模式（只打印不下单）
  - 订单状态机追踪

用法：
    from trader import Trader

    trader = Trader(dry_run=True)  # 默认 dry-run，安全
    trader.connect(account="123456")
    trader.buy("000001.SZ", volume=100, price=12.50)
    positions = trader.get_positions()
"""

import sys
sys.path.insert(0, r"D:\software\python\Lib\site-packages")

import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Callable

from config import config, setup_logging

logger = setup_logging("trader")


# ═══════════════════════════════════════════════════════════════
# 订单状态
# ═══════════════════════════════════════════════════════════════

ORDER_STATUS = {
    48: "未报",      # 待发送
    49: "待报",      # 已发送待确认
    50: "已报",      # 交易所已接受
    51: "已报待撤",   # 等待撤单确认
    52: "部成待撤",   # 部分成交待撤
    53: "部成",       # 部分成交
    54: "已成",       # 全部成交
    55: "已撤",       # 已撤单
    56: "废单",       # 交易所拒绝
    57: "未知",       # 状态未知
}


@dataclass
class Order:
    """订单状态追踪。"""
    order_id: int = 0              # xttrader 返回的请求序号
    sys_id: str = ""               # 交易所系统编号
    code: str = ""
    action: str = ""               # "buy" / "sell"
    price: float = 0.0
    volume: int = 0
    filled_volume: int = 0
    status: int = 48               # ORDER_STATUS 中的键
    status_text: str = "未报"
    create_time: str = ""
    update_time: str = ""
    strategy: str = "quantdemo"
    remark: str = ""

    def update(self, status: int, filled_volume: int = 0, sys_id: str = ""):
        self.status = status
        self.status_text = ORDER_STATUS.get(status, "未知")
        self.filled_volume = filled_volume
        if sys_id:
            self.sys_id = sys_id
        self.update_time = datetime.now().isoformat()


# ═══════════════════════════════════════════════════════════════
# 交易回调
# ═══════════════════════════════════════════════════════════════

class TraderCallback:
    """接收 xttrader 异步回调，更新订单状态和持仓信息。"""

    def __init__(self, trader: "Trader"):
        self.trader = trader

    def on_connected(self):
        logger.info("交易服务已连接")

    def on_disconnected(self):
        logger.warning("交易服务断开连接")

    def on_stock_order(self, order_data):
        """委托回报。"""
        logger.debug(f"委托回报: {order_data}")

    def on_stock_trade(self, trade_data):
        """成交回报。"""
        logger.info(f"成交: {trade_data.m_strInstrumentID} "
                     f"{trade_data.m_nOrderActionType} "
                     f"{trade_data.m_nVolume}股 @{trade_data.m_dTradePrice}")

    def on_stock_asset(self, asset_data):
        """资金变动。"""
        logger.debug(f"资金: 可用={asset_data.m_dAvailable} "
                      f"总资产={asset_data.m_dTotalAsset}")

    def on_stock_position(self, position_data):
        """持仓变动。"""
        logger.debug(f"持仓变动: {position_data}")

    def on_order_error(self, error_data):
        """下单错误。"""
        logger.error(f"下单错误: {error_data}")

    def on_cancel_error(self, error_data):
        """撤单错误。"""
        logger.error(f"撤单错误: {error_data}")

    def on_order_stock_async_response(self, response):
        """异步下单响应。"""
        logger.debug(f"异步下单响应: {response}")

    def on_cancel_order_stock_async_response(self, response):
        """异步撤单响应。"""
        logger.debug(f"异步撤单响应: {response}")

    def on_account_status(self, status):
        """账户状态变更。"""
        logger.info(f"账户状态: {status}")


# ═══════════════════════════════════════════════════════════════
# Trader — 交易执行器
# ═══════════════════════════════════════════════════════════════

class Trader:
    """QMT 交易接口封装。

    默认 dry_run=True：所有下单操作只记录日志，不实际执行。
    设置为 False 才会向券商发送真实委托。
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self._xt_trader = None
        self._account: Optional[str] = None
        self._connected = False
        self._callback: Optional[TraderCallback] = None

        # 订单状态追踪
        self.orders: dict[int, Order] = {}
        self._order_seq = 0

    # ── 连接 ─────────────────────────────────────────────────────
    def connect(self, account: str, path: str | None = None) -> bool:
        """连接 QMT 交易服务。

        :param account: 证券账号（资金账号）
        :param path: QMT 客户端路径（默认从 config 读取）
        """
        from xtquant import xttrader

        if not path:
            path = config.qmt.qmt_exe
            # 修正为 miniQMT 目录
            import os
            path = os.path.join(os.path.dirname(path), "userdata_mini")

        logger.info(f"{'[DRY-RUN] ' if self.dry_run else ''}"
                     f"连接交易服务: account={account}")

        if self.dry_run:
            self._account = account
            self._connected = True
            logger.info("[DRY-RUN] 交易连接成功（模拟）")
            return True

        try:
            self._xt_trader = xttrader.XtQuantTrader(path, 1)  # 1 = 模拟环境
            self._callback = TraderCallback(self)

            # 注册回调
            cb = xttrader.XtQuantTraderCallback()
            cb.on_connected = self._callback.on_connected
            cb.on_disconnected = self._callback.on_disconnected
            cb.on_stock_order = self._callback.on_stock_order
            cb.on_stock_trade = self._callback.on_stock_trade
            cb.on_stock_asset = self._callback.on_stock_asset
            cb.on_stock_position = self._callback.on_stock_position
            cb.on_order_error = self._callback.on_order_error
            cb.on_cancel_error = self._callback.on_cancel_error
            cb.on_order_stock_async_response = (
                self._callback.on_order_stock_async_response
            )
            cb.on_cancel_order_stock_async_response = (
                self._callback.on_cancel_order_stock_async_response
            )
            cb.on_account_status = self._callback.on_account_status

            self._xt_trader.register_callback(cb)
            self._xt_trader.start()
            time.sleep(1)  # 等待连接建立

            connect_result = self._xt_trader.connect()
            if connect_result != 0:
                logger.error(f"连接失败: code={connect_result}")
                return False

            self._xt_trader.subscribe(account)
            time.sleep(0.5)

            self._account = account
            self._connected = True
            logger.info("交易服务连接成功")
            return True

        except Exception as e:
            logger.error(f"连接异常: {e}")
            return False

    def disconnect(self):
        """断开交易连接。"""
        if self.dry_run:
            logger.info("[DRY-RUN] 断开连接")
            self._connected = False
            return

        if self._xt_trader:
            try:
                self._xt_trader.stop()
            except Exception:
                pass
        self._connected = False
        logger.info("交易服务已断开")

    # ── 下单 ─────────────────────────────────────────────────────
    def buy(self, code: str, volume: int, price: float = 0.0,
            price_type: int = 5,  # 5=市价, 11=限价
            strategy: str = "quantdemo",
            remark: str = "") -> int:
        """买入。

        :param code: 股票代码 "000001.SZ"
        :param volume: 买入股数（100 的整数倍）
        :param price: 限价（price_type=11 时有效）
        :param price_type: 5=市价, 11=限价
        :param strategy: 策略名称
        :param remark: 备注
        :return: 订单序号（-1 表示失败）
        """
        return self._order("buy", code, volume, price,
                            price_type, strategy, remark)

    def sell(self, code: str, volume: int, price: float = 0.0,
             price_type: int = 5,
             strategy: str = "quantdemo",
             remark: str = "") -> int:
        """卖出。参数同 buy。"""
        return self._order("sell", code, volume, price,
                            price_type, strategy, remark)

    def _order(self, action: str, code: str, volume: int, price: float,
               price_type: int, strategy: str, remark: str) -> int:
        """内部下单逻辑。"""
        order_type = 23 if action == "buy" else 24
        price_type_name = "市价" if price_type == 5 else f"限价{price}"

        log_msg = (
            f"{'[DRY-RUN] ' if self.dry_run else ''}"
            f"{'买入' if action == 'buy' else '卖出'} "
            f"{code} {volume}股 {price_type_name}"
        )
        logger.info(log_msg)

        if self.dry_run:
            self._order_seq += 1
            seq = self._order_seq
            order = Order(
                order_id=seq, code=code, action=action,
                price=price, volume=volume,
                create_time=datetime.now().isoformat(),
                strategy=strategy, remark=remark,
            )
            order.update(status=54, filled_volume=volume,
                         sys_id=f"DRYRUN-{seq}")  # 模拟全部成交
            self.orders[seq] = order
            logger.info(f"[DRY-RUN] 订单 #{seq} 已模拟成交")
            return seq

        if not self._xt_trader:
            logger.error("未连接交易服务")
            return -1

        try:
            seq = self._xt_trader.order_stock(
                account=self._account,
                stock_code=code,
                order_type=order_type,
                order_volume=volume,
                price_type=price_type,
                price=price,
                strategy_name=strategy,
                order_remark=remark,
            )

            if seq < 0:
                logger.error(f"下单失败: seq={seq}")
                return seq

            order = Order(
                order_id=seq, code=code, action=action,
                price=price, volume=volume,
                create_time=datetime.now().isoformat(),
                strategy=strategy, remark=remark,
            )
            order.update(status=49)  # 待报
            self.orders[seq] = order
            logger.info(f"订单已提交: #{seq}")
            return seq

        except Exception as e:
            logger.error(f"下单异常: {e}")
            return -1

    # ── 撤单 ─────────────────────────────────────────────────────
    def cancel(self, order_id: int) -> bool:
        """撤单。"""
        if self.dry_run:
            if order_id in self.orders:
                self.orders[order_id].update(status=55)  # 已撤
            logger.info(f"[DRY-RUN] 撤单 #{order_id}")
            return True

        try:
            result = self._xt_trader.cancel_order_stock(
                account=self._account,
                order_id=order_id,
            )
            logger.info(f"撤单 #{order_id}: {'成功' if result != -1 else '失败'}")
            return result != -1
        except Exception as e:
            logger.error(f"撤单异常: {e}")
            return False

    # ── 查询 ─────────────────────────────────────────────────────
    def get_positions(self, account: str | None = None) -> list:
        """查询当前持仓。"""
        acct = account or self._account

        if self.dry_run:
            logger.debug("[DRY-RUN] 查询持仓")
            return []

        try:
            return self._xt_trader.query_stock_positions(acct)
        except Exception as e:
            logger.error(f"查询持仓失败: {e}")
            return []

    def get_orders(self, account: str | None = None) -> list:
        """查询当日委托。"""
        acct = account or self._account

        if self.dry_run:
            return [o for o in self.orders.values()
                    if o.status < 56]  # 未终结的订单

        try:
            return self._xt_trader.query_stock_orders(acct)
        except Exception as e:
            logger.error(f"查询委托失败: {e}")
            return []

    def get_trades(self, account: str | None = None) -> list:
        """查询当日成交。"""
        acct = account or self._account

        if self.dry_run:
            return [o for o in self.orders.values()
                    if o.status == 54]

        try:
            return self._xt_trader.query_stock_trades(acct)
        except Exception as e:
            logger.error(f"查询成交失败: {e}")
            return []

    def get_asset(self, account: str | None = None) -> dict | None:
        """查询账户资产。"""
        acct = account or self._account

        if self.dry_run:
            return {
                "available": 100000.0,
                "total_asset": 100000.0,
                "market_value": 0.0,
                "frozen": 0.0,
            }

        try:
            result = self._xt_trader.query_stock_asset(acct)
            return {
                "available": float(result.m_dAvailable),
                "total_asset": float(result.m_dTotalAsset),
                "market_value": float(result.m_dMktValue),
                "frozen": float(result.m_dFrozenCash),
            }
        except Exception as e:
            logger.error(f"查询资产失败: {e}")
            return None

    def get_positions_summary(self) -> list[dict]:
        """持仓摘要（兼容格式）。"""
        positions = self.get_positions()
        result = []
        for p in positions:
            try:
                result.append({
                    "code": p.m_strInstrumentID,
                    "volume": p.m_nVolume,
                    "available": p.m_nCanUseVolume,
                    "cost_price": p.m_dOpenPrice,
                    "market_price": p.m_dLastPrice,
                    "market_value": p.m_dMarketValue,
                    "profit": p.m_dFloatProfit,
                })
            except AttributeError:
                result.append(str(p))
        return result

    @property
    def is_connected(self) -> bool:
        return self._connected


# ── 便捷函数 ────────────────────────────────────────────────────

def create_trader(dry_run: bool = True) -> Trader:
    """创建一个 Trader 实例（默认 dry-run）。"""
    return Trader(dry_run=dry_run)


if __name__ == "__main__":
    print("=== 交易模块自测（dry-run 模式）===\n")

    trader = Trader(dry_run=True)
    trader.connect(account="88888888")

    # 模拟下单
    buy_id = trader.buy("000001.SZ", volume=100, price=12.50,
                         price_type=11, strategy="quantdemo")
    sell_id = trader.sell("600000.SH", volume=200,
                           price_type=5, strategy="quantdemo")

    print(f"\n当前订单:")
    for oid, order in trader.orders.items():
        print(f"  #{oid}: {order.code} {order.action} "
              f"{order.volume}股 @{order.price} "
              f"状态={order.status_text}")

    # 查询
    asset = trader.get_asset()
    if asset:
        print(f"\n账户资产: 可用={asset['available']:,.0f} "
              f"总={asset['total_asset']:,.0f}")

    trader.disconnect()
    print("\n[OK] 交易模块测试通过")
