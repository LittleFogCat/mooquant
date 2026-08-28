# -*- coding: utf-8 -*-
"""
mookquant * 日内做T策略（intraday_t）

教科书式正反T框架（A 股 T+1 约束下的底仓做T）：
  趋势门（日线，用昨日及以前日线，无前视）：
    收盘 > MA20 -> 只做正向T（先买后卖）
    收盘 < MA20 -> 只做反向T（先卖后买）
    MA20 ± band 内 -> 观望

  分时触发（1m + 当日分时均线 vwap）：
    正T买点：price < vwap×(1-buyDev) 且 5m RSI 超卖
    正T卖点：price > vwap×(1+sellDev)（或 5m RSI 超买）
    反T卖点：price > vwap×(1+sellDev) 且 5m RSI 超买 -> 卖底仓
    反T买点：price < vwap×(1-buyDev) -> 买回等量还原底仓

  T仓管理：
    单次做T量 = tRatio × 总资产 / price（整手化）
    日内次数上限 maxTCount；14:50 后不再开新仓（撮合层 14:55 强平兜底）

设计要点：
  - 策略只发 Signal（qty + lot_tag 表达意图），仓位约束/撮合由引擎兜底
  - 依赖 ctx.bars_by_period["1d"/"5m"/"1m"] 与 ctx.intraday_vwap()，
    均由日内回测引擎/实盘执行器提供（无前视保证在框架层）
  - trigger_mode = "intraday"（分钟驱动；实盘执行器按此调度）
"""

from strategies.base import StrategyBase, Signal
from strategies.indicators import sma, rsi


class IntradayTStrategy(StrategyBase):
    """日内做T策略（底仓+T仓，日线趋势门 + 分时均线触发）。"""

    name = "intraday_t"
    display_name = "日内做T"
    description = "底仓做T：日线趋势门定方向，分时均线偏离+5m RSI 触发正/反向T"
    version = "1.0"
    trigger_mode = "intraday"
    is_intraday = True   # 标记：需要分钟数据源与日内撮合引擎

    params_schema = [
        {"key": "dailyMaPeriod", "type": "int", "default": 20, "min": 5, "max": 120, "label": "日线趋势门均线"},
        {"key": "trendBand", "type": "float", "default": 0.02, "min": 0, "max": 0.2, "label": "趋势门缓冲带"},
        {"key": "vwapDevBuy", "type": "float", "default": 0.005, "min": 0.0005, "max": 0.05, "label": "低于分时均线买入偏离"},
        {"key": "vwapDevSell", "type": "float", "default": 0.005, "min": 0.0005, "max": 0.05, "label": "高于分时均线卖出偏离"},
        {"key": "rsiPeriod", "type": "int", "default": 14, "min": 2, "max": 60, "label": "5m RSI 周期"},
        {"key": "rsiOversold", "type": "float", "default": 30, "min": 5, "max": 50, "label": "RSI 超卖阈值"},
        {"key": "rsiOverbought", "type": "float", "default": 70, "min": 50, "max": 95, "label": "RSI 超买阈值"},
        {"key": "tRatio", "type": "float", "default": 0.3, "min": 0.05, "max": 1.0, "label": "T资金占总资产比"},
        {"key": "maxTCount", "type": "int", "default": 4, "min": 1, "max": 20, "label": "日内做T次数上限"},
        {"key": "noNewOpenAfter", "type": "str", "default": "14:50", "min": None, "max": None, "label": "此后不再开新仓（HH:MM）"},
    ]

    def on_init(self, ctx):
        # 每日重置状态
        self._state["cur_day"] = ""
        self._state["t_count"] = 0
        self._state["t_open"] = False       # 当前是否有未完成往返（正T已买 / 反T已卖）
        self._state["t_open_qty"] = 0       # 未完成往返的股数
        # 5m RSI 增量缓存（避免每根1m bar 全量重算）
        self._state["rsi_cache_day"] = ""
        self._state["rsi_series"] = None

    def _daily_trend(self, ctx):
        """日线趋势门：1=多（正T），-1=空（反T），0=观望。

        用昨日及以前的日线收盘（切片保证今日进行中日线不暴露），
        真实世界同样只能看到昨日日线。
        """
        daily = ctx.bars_by_period.get("1d") or []
        p = int(self.params.get("dailyMaPeriod", 20))
        if len(daily) < p + 1:
            return 0
        closes = [b["close"] for b in daily]
        ma = sma(closes, p)
        ma_now = ma[-1]
        if ma_now is None or ma_now == 0:
            return 0
        last_close = closes[-1]
        band = float(self.params.get("trendBand", 0.02))
        if last_close > ma_now * (1 + band):
            return 1
        if last_close < ma_now * (1 - band):
            return -1
        return 0

    def _rsi_5m(self, ctx):
        """5m RSI 最新值（增量缓存：当日 5m 收盘序列变化才重算）。"""
        bars_5m = ctx.bars_by_period.get("5m") or []
        p = int(self.params.get("rsiPeriod", 14))
        if len(bars_5m) < p + 1:
            return None
        day = ctx.trading_day
        key_day = self._state.get("rsi_cache_day")
        n = len(bars_5m)
        if key_day != day or self._state.get("rsi_series") is None \
                or self._state.get("rsi_series_len") != n:
            closes = [b["close"] for b in bars_5m]
            series = rsi(closes, p)
            self._state["rsi_cache_day"] = day
            self._state["rsi_series"] = series
            self._state["rsi_series_len"] = n
        else:
            series = self._state["rsi_series"]
        return series[-1] if series else None

    def _t_qty(self, ctx, price):
        """单次做T股数：tRatio × 总资产 / 现价，整手化。

        总资产用引擎注入的账户快照（ctx.account.total_assets）；
        无快照环境（实盘早期）退化为可用现金近似。
        """
        t_ratio = float(self.params.get("tRatio", 0.3))
        acct = getattr(ctx, "account", None)
        if acct is not None and getattr(acct, "total_assets", 0):
            base = acct.total_assets
        elif acct is not None and getattr(acct, "cash", 0):
            base = acct.cash
        else:
            base = self._state.get("est_cash", 0)
        qty = int(base * t_ratio / price / 100) * 100
        return max(qty, 0)

    def _core_qty(self, ctx):
        """可卖底仓股数（引擎持仓快照；无快照时退化为已知底仓参数）。"""
        pos = getattr(ctx, "position", None)
        if pos is not None and getattr(pos, "core_shares", None) is not None:
            return pos.core_shares
        return self._state.get("core_qty_est", 0)

    def on_bar(self, bar, ctx):
        dt = str(bar.get("date", ""))
        day = dt[:10]
        hm = dt[11:16] if len(dt) > 10 else ""

        # ---- 每日重置 ----
        if self._state.get("cur_day") != day:
            self._state["cur_day"] = day
            self._state["t_count"] = 0
            self._state["t_open"] = False
            self._state["t_open_qty"] = 0

        # ---- 环境数据 ----
        price = bar.get("close")
        if price is None or price <= 0:
            return None
        vwap = ctx.intraday_vwap()
        if vwap is None or vwap <= 0:
            return None  # 当日尚无分时数据
        rsi5 = self._rsi_5m(ctx)
        trend = self._daily_trend(ctx)

        dev_buy = float(self.params.get("vwapDevBuy", 0.005))
        dev_sell = float(self.params.get("vwapDevSell", 0.005))
        rsi_os = float(self.params.get("rsiOversold", 30))
        rsi_ob = float(self.params.get("rsiOverbought", 70))
        max_t = int(self.params.get("maxTCount", 4))
        no_open_after = str(self.params.get("noNewOpenAfter", "14:50"))
        t_count = self._state.get("t_count", 0)
        t_open = self._state.get("t_open", False)
        t_open_qty = self._state.get("t_open_qty", 0)
        qty = self._t_qty(ctx, price)

        indicators = {
            "vwap": round(vwap, 4),
            "rsi5m": round(rsi5, 2) if rsi5 is not None else None,
            "trend": trend,
            "tCount": t_count,
            "dev": round((price - vwap) / vwap, 4),
        }

        can_open_new = (hm < no_open_after) and (t_count < max_t)

        # ---- 信号逻辑（状态机）----
        if t_open:
            # 正T持仓：等卖点（price > vwap×(1+devSell) 或 RSI 超买）
            if self._state.get("t_direction") == "long":
                if price > vwap * (1 + dev_sell) or (rsi5 is not None and rsi5 > rsi_ob):
                    self._state["t_open"] = False
                    return Signal(action="sell", qty=t_open_qty, lot_tag="t",
                                  reason="正T卖出：price>分时均线+{:.2%}（RSI {:.0f}）".format(dev_sell, rsi5 or 0),
                                  indicators=indicators)
            else:
                # 反T已卖底仓：等买点还原（price < vwap×(1-devBuy)）
                if price < vwap * (1 - dev_buy):
                    self._state["t_open"] = False
                    return Signal(action="buy", qty=t_open_qty, lot_tag="t",
                                  reason="反T买回还原底仓：price<分时均线-{:.2%}".format(dev_buy),
                                  indicators=indicators)
            return None

        # 空闲：找开仓点
        if not can_open_new:
            return None

        if trend == 1:
            # 正T买点：price < vwap×(1-devBuy) 且（RSI 超卖 或 深偏离 1.5×dev）
            deep = price < vwap * (1 - dev_buy * 1.5)
            if price < vwap * (1 - dev_buy) and ((rsi5 is not None and rsi5 < rsi_os) or deep):
                if qty >= 100:
                    self._state["t_open"] = True
                    self._state["t_open_qty"] = qty
                    self._state["t_direction"] = "long"
                    return Signal(action="buy", qty=qty, lot_tag="t",
                                  reason="正T买入：price<分时均线-{:.2%}（RSI {:.0f}）".format(dev_buy, rsi5 or 0),
                                  indicators=indicators)
        elif trend == -1:
            # 反T卖点：price > vwap×(1+devSell) 且 RSI 超买
            if price > vwap * (1 + dev_sell) and (rsi5 is not None and rsi5 > rsi_ob):
                core_qty = self._core_qty(ctx)
                if core_qty >= 100:
                    qty = self._t_qty(ctx, price)
                    self._state["t_open"] = True
                    self._state["t_open_qty"] = min(qty, core_qty) if qty > 0 else core_qty
                    self._state["t_direction"] = "short"
                    return Signal(action="sell", qty=self._state["t_open_qty"], lot_tag="core",
                                  reason="反T卖出底仓：price>分时均线+{:.2%}（RSI {:.0f}）".format(dev_sell, rsi5 or 0),
                                  indicators=indicators)
        return None

    def on_stop(self, ctx):
        self._state.clear()
