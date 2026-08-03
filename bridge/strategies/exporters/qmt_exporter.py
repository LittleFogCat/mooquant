"""
mookquant * QMT 策略导出器（适配壳包装模式）

将 mookquant 策略导出为可在 QMT 客户端运行的单文件脚本。
策略逻辑原样保留（inspect.getsource），外面套 QMT 生命周期壳 +
Context 适配层，使回测与实盘跑的是同一份 on_bar。

生成脚本结构：
  1. 头部 + import + 框架兼容层（StrategyBase/Signal/指标内联）
  2. 策略定义（原样复制，逻辑零修改）
  3. Context 适配层（QMT ContextInfo -> mookquant Context）
  4. QMT 生命周期壳（init/handlebar/stop）
"""

import inspect
import re
import datetime
from .base import ExporterBase


# 模板（用 @@占位符@@ 替换，避免花括号转义）
_TEMPLATE = """#coding:gbk
# === mookquant 导出 | 策略: @@NAME@@ (@@DISPLAY@@) | @@TS@@ ===
# 用法：QMT客户端 -> 模型研究 -> 新建模型 -> 粘贴本代码 -> 运行/回测
# 策略逻辑与 mookquant 框架内回测/实盘完全一致（同一份 on_bar）。
# 生成方式：适配壳包装，策略类原样保留，不修改任何业务逻辑。

import pandas as pd
import numpy as np
from typing import List, Optional, Tuple

# QMT passorder 常量（参考 xtconstant，避免 magic number）
_OP_BUY = 23       # xtconstant.STOCK_BUY
_OP_SELL = 24      # xtconstant.STOCK_SELL
_ORDER_TYPE = 1101  # 股票账户单边双向
_PRICE_LATEST = 5   # xtconstant.LATEST_PRICE

# ============================================================
# 第一部分：框架兼容层（使策略类脱离 mookquant 包也能定义运行）
# ============================================================
class StrategyBase:
    def __init__(self, params=None):
        self.params = params or {}
        self._state = {}


class Signal:
    def __init__(self, action, reason="", strength=1.0, target_position=None, indicators=None):
        self.action = action
        self.reason = reason
        self.strength = strength
        self.target_position = target_position
        self.indicators = indicators or {}


@@INDICATORS@@

# ============================================================
# 第二部分：策略定义（原样复制自 mookquant，逻辑零修改）
# ============================================================
@@SOURCE@@

# ============================================================
# 第三部分：Context 适配层（QMT ContextInfo -> mookquant Context）
# ============================================================
class _Ctx:
    def __init__(self, ContextInfo):
        self._ci = ContextInfo
        self.symbol = ContextInfo.stockcode + '.' + ContextInfo.market
        self.is_backtest = getattr(ContextInfo, 'do_back_test', False)
        self.bars = []
        self.barpos = 0
        self._accid = getattr(ContextInfo, 'accountid', '') or ''

    def get_bars(self, symbol, count, period='1d'):
        data = self._ci.get_market_data_ex(
            ['open', 'high', 'low', 'close', 'volume'], [symbol],
            period=period, count=count)
        if symbol not in data:
            return []
        df = data[symbol]
        bars = []
        for i in range(len(df)):
            bars.append({
                'date': str(df.index[i]),
                'open': float(df['open'].iloc[i]),
                'high': float(df['high'].iloc[i]),
                'low': float(df['low'].iloc[i]),
                'close': float(df['close'].iloc[i]),
                'volume': float(df['volume'].iloc[i]),
            })
        return bars

    def order_target_percent(self, symbol, percent):
        # 回测用 order_target_percent，实盘用 passorder（按 do_back_test 分支）
        if self.is_backtest:
            order_target_percent(symbol, percent, 'MARKET', -1, self._ci)
        else:
            account = get_trade_detail_data(self._accid, 'stock', 'account')
            avail = account[0].m_dAvailable if account else 0
            holdings = get_trade_detail_data(self._accid, 'stock', 'position')
            hold_vol = 0
            for h in holdings:
                if h.m_strInstrumentID + '.' + h.m_strExchangeID == symbol:
                    hold_vol = h.m_nVolume
            # S4: 实盘下单价取 tick 最新价（非 K 线收盘价），减少滑点
            tick_data = xtdata.get_full_tick([symbol])
            tick = tick_data.get(symbol, {}) if tick_data else {}
            price = float(tick.get('lastPrice', 0)) if tick else 0
            if price <= 0:
                # tick 不可用时回退到最近 K 线收盘价
                data = self._ci.get_market_data_ex(['close'], [symbol], count=1)
                price = float(data[symbol]['close'].iloc[-1]) if symbol in data else 0
            if price <= 0:
                return
            target_qty = int((avail + hold_vol * price) * percent / price / 100) * 100
            delta = target_qty - hold_vol
            if delta > 0:
                passorder(_OP_BUY, _ORDER_TYPE, self._accid, symbol, _PRICE_LATEST, -1, delta, self._ci)
            elif delta < 0:
                passorder(_OP_SELL, _ORDER_TYPE, self._accid, symbol, _PRICE_LATEST, -1, -delta, self._ci)

    def get_position(self, symbol):
        holdings = get_trade_detail_data(self._accid, 'stock', 'position')
        for h in holdings:
            if h.m_strInstrumentID + '.' + h.m_strExchangeID == symbol:
                return h
        return None

    def get_account(self):
        return get_trade_detail_data(self._accid, 'stock', 'account')


# ============================================================
# 第四部分：QMT 生命周期壳（init/handlebar/stop）
# ============================================================
class _G:
    pass


g = _G()


def init(ContextInfo):
    g.strategy = @@CLS@@(@@PARAMS@@)
    g.ctx = _Ctx(ContextInfo)
    if hasattr(g.strategy, 'on_init'):
        g.strategy.on_init(g.ctx)
    if hasattr(g.strategy, 'on_after_init'):
        g.strategy.on_after_init(g.ctx)


def handlebar(ContextInfo):
    g.ctx._ci = ContextInfo
    if not ContextInfo.is_last_bar():
        return
    bars = g.ctx.get_bars(g.ctx.symbol, 100)
    if not bars:
        return
    g.ctx.bars = bars
    g.ctx.barpos = len(bars) - 1
    sig = g.strategy.on_bar(bars[-1], g.ctx)
    if sig is None:
        return
    tp = getattr(sig, 'target_position', None)
    if tp is not None:
        g.ctx.order_target_percent(g.ctx.symbol, tp)
    elif sig.action == 'buy':
        g.ctx.order_target_percent(g.ctx.symbol, 0.9)
    elif sig.action == 'sell':
        g.ctx.order_target_percent(g.ctx.symbol, 0.0)


def stop(ContextInfo):
    if hasattr(g.strategy, 'on_stop'):
        g.strategy.on_stop(g.ctx)
"""


class QmtExporter(ExporterBase):
    """QMT 策略导出器：适配壳包装模式。"""

    PLATFORM = "qmt"

    def export(self, strategy_class, params=None):
        cls_name = strategy_class.__name__
        meta = strategy_class().metadata()

        # 1) 策略源码（原样获取，去掉 strategies 包 import）
        source = inspect.getsource(strategy_class)
        source = self._strip_strategy_imports(source)

        # 2) 内联策略用到的指标函数
        indicators_code = self._inline_indicators(source)

        # 3) 参数默认值
        defaults = strategy_class(params).params

        # 4) 填充模板
        script = _TEMPLATE
        script = script.replace("@@NAME@@", meta["name"])
        script = script.replace("@@DISPLAY@@", meta["display_name"])
        script = script.replace("@@TS@@", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
        script = script.replace("@@INDICATORS@@", indicators_code)
        script = script.replace("@@SOURCE@@", source)
        script = script.replace("@@CLS@@", cls_name)
        script = script.replace("@@PARAMS@@", repr(defaults))
        return script

    @staticmethod
    def _strip_strategy_imports(source):
        """去掉 from strategies... / import strategies... 行（兼容层已提供依赖）。"""
        lines = source.split("\n")
        out = [ln for ln in lines if not re.match(r"\s*(from strategies|import strategies)", ln)]
        return "\n".join(out)

    @staticmethod
    def _inline_indicators(source):
        """检测策略源码用到的指标函数，内联其源码（自包含，不依赖 strategies 包）。"""
        from .. import indicators as ind
        all_inds = ["sma", "ema", "macd", "rsi", "boll", "kdj"]
        used = [name for name in all_inds if re.search(r"\b" + name + r"\b", source)]
        if not used:
            return "# （本策略未使用内置指标）"
        parts = ["# 指标库（从 strategies.indicators 内联，自包含）"]
        for name in used:
            fn = getattr(ind, name)
            parts.append(inspect.getsource(fn))
        return "\n".join(parts)
