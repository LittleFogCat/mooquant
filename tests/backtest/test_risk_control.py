# -*- coding: utf-8 -*-
"""统一风控层（D2.1/D2.2/D2.3）单元测试：risk_control.py。

纯逻辑测试，不依赖行情源/引擎，覆盖：
止损/止盈/时间止损触发、熔断（连续亏损冻结开新仓）、仓位比例与 strength 缩放、
市场状态过滤 regime_mask（D2.3）。
"""
import pytest

from risk_control import RiskController, regime_mask


def _bar(low=10.0, high=11.0, close=10.5):
    return {"low": low, "high": high, "close": close}


# ---------------------------------------------------------------------------
# 退出触发
# ---------------------------------------------------------------------------

def test_stop_loss_trigger():
    r = RiskController({"stopLoss": 0.05})
    r.mark_entry(0)
    # 当日 low 触及 成本×(1-5%) → 触发止损，按触发价返回
    reason, price = r.exit_trigger(_bar(low=9.4, high=10.5, close=10.0), 10.0, 1)
    assert reason == "止损"
    assert abs(price - 9.5) < 1e-9


def test_take_profit_trigger():
    r = RiskController({"takeProfit": 0.05})
    r.mark_entry(0)
    reason, price = r.exit_trigger(_bar(low=10.0, high=10.6, close=10.2), 10.0, 1)
    assert reason == "止盈"
    assert abs(price - 10.5) < 1e-9


def test_time_stop_trigger():
    r = RiskController({"maxHoldDays": 3})
    r.mark_entry(2)  # 第 2 根 bar 建仓
    # 第 5 根 bar（index=5）持有 3 个交易日 → 触发时间止损（按收盘价）
    reason, price = r.exit_trigger(_bar(low=10.0, high=10.2, close=10.1), 10.0, 5)
    assert reason == "时间止损"
    assert abs(price - 10.1) < 1e-9
    # 未满 3 日不触发
    assert r.exit_trigger(_bar(low=10.0, high=10.2, close=10.1), 10.0, 4) is None


def test_no_trigger_when_flat():
    r = RiskController({"stopLoss": 0.05, "takeProfit": 0.05, "maxHoldDays": 3})
    assert r.exit_trigger(_bar(low=1.0, high=100.0, close=50.0), 0.0, 5) is None


def test_risk_off_by_default():
    """默认全部关闭：任何行情都不触发退出。"""
    r = RiskController()
    r.mark_entry(0)
    assert r.exit_trigger(_bar(low=0.01, high=1000.0, close=500.0), 10.0, 10) is None


# ---------------------------------------------------------------------------
# 熔断（连续亏损冻结开新仓）
# ---------------------------------------------------------------------------

def test_circuit_breaker_blocks_new_entry():
    r = RiskController({"maxConsecLosses": 2, "cooldownDays": 3})
    # 两次连续亏损
    r.mark_close(-100, 5)
    assert r.can_open(6)  # 1 次亏损未达阈值
    r.mark_close(-80, 10)
    assert not r.can_open(12)  # 冻结至 10+3=13
    assert not r.can_open(13)
    assert r.can_open(14)  # 解冻


def test_winning_trade_resets_consecutive_losses():
    r = RiskController({"maxConsecLosses": 2, "cooldownDays": 3})
    r.mark_close(-100, 5)
    r.mark_close(50, 8)  # 盈利 → 重置
    r.mark_close(-90, 9)  # 仅 1 次亏损
    assert r.can_open(10)


def test_circuit_breaker_off_by_default():
    r = RiskController()
    for i in range(10):
        r.mark_close(-100, i)
    assert r.can_open(100)


def test_mark_close_clears_entry():
    r = RiskController({"stopLoss": 0.05})
    r.mark_entry(3)
    r.mark_close(10, 5)
    assert r.entry_index == -1


# ---------------------------------------------------------------------------
# 仓位
# ---------------------------------------------------------------------------

def test_position_pct_default_full():
    r = RiskController()
    assert r.position_pct() == 1.0
    assert r.target_value(1_000_000) == 1_000_000


def test_position_pct_cap():
    r = RiskController({"maxPositionPct": 0.4})
    assert abs(r.position_pct() - 0.4) < 1e-9
    assert abs(r.target_value(1_000_000) - 400_000) < 1e-6


def test_position_pct_strength_scaling():
    r = RiskController({"maxPositionPct": 0.5, "strengthScaling": True})
    assert abs(r.position_pct(0.6) - 0.3) < 1e-9
    # strength 越界 clip 到 [0,1]
    assert abs(r.position_pct(2.0) - 0.5) < 1e-9
    assert abs(r.position_pct(-0.5) - 0.0) < 1e-9
    # 未开启缩放时 strength 不影响
    r2 = RiskController({"maxPositionPct": 0.5})
    assert abs(r2.position_pct(0.1) - 0.5) < 1e-9


def test_summary_reports_config():
    r = RiskController({"stopLoss": 0.05, "maxConsecLosses": 2, "maxPositionPct": 0.3})
    s = r.summary()
    assert s["stopLoss"] == 0.05
    assert s["maxConsecLosses"] == 2
    assert s["maxPositionPct"] == 0.3


# ---------------------------------------------------------------------------
# 市场状态过滤 regime_mask（D2.3）
# ---------------------------------------------------------------------------

def _index(dates, closes):
    return [{"date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1}
            for d, c in zip(dates, closes)]


def _days(n, start="2024-01-01"):
    from datetime import date, timedelta
    out, d = [], date(*map(int, start.split("-")))
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_regime_rising_index_allows():
    """指数上升（close>=MA20）→ 做多许可。末 6 日 MA20 均有效。"""
    dates = _days(25)
    closes = [3000 + i * 8 for i in range(25)]
    bars = [{"date": d, "close": 10.0 + i} for i, d in enumerate(dates[-6:])]
    mask, warn = regime_mask(bars, _index(dates, closes), fast=20)
    assert warn is None
    assert all(mask[d] for d in dates[-6:])


def test_regime_falling_index_blocks():
    """指数下跌（close<MA20）→ 禁止做多。末 6 日 MA20 均有效。"""
    dates = _days(25)
    closes = [3000 - i * 8 for i in range(25)]
    bars = [{"date": d, "close": 10.0 + i} for i, d in enumerate(dates[-6:])]
    mask, warn = regime_mask(bars, _index(dates, closes), fast=20)
    assert warn is None
    assert all(not mask[d] for d in dates[-6:])


def test_regime_insufficient_index_warns():
    """指数数据不足（< fast 根）→ 返回 None + 警告（调用方降级为不生效）。"""
    dates = _days(10)
    bars = [{"date": d, "close": 10.0} for d in dates[-5:]]
    mask, warn = regime_mask(bars, _index(dates, [3000] * 10), fast=20)
    assert mask is None
    assert "不足" in warn


def test_regime_missing_index_date_conservative():
    """指数缺个股某交易日 → 该日保守禁止做多。"""
    dates = _days(25)
    closes = [3000 + i * 8 for i in range(25)]
    # 个股比指数多一天（该日指数无数据）
    sym_dates = dates[-7:] + ["2024-02-15"]
    bars = [{"date": d, "close": 10.0 + i} for i, d in enumerate(sym_dates)]
    mask, warn = regime_mask(bars, _index(dates, closes), fast=20)
    assert mask[sym_dates[-1]] is False


def test_regime_own_ma_gate():
    """use_own_ma=True：指数上升但个股低于自身 MA20 → 禁止做多。"""
    idx_dates = _days(40)
    idx_closes = [3000 + i * 8 for i in range(40)]
    # 个股 25 根，前段上涨后段下跌（末尾低于自身 MA20）
    sym_dates = idx_dates[-25:]
    own = [10.0 + i for i in range(25)]  # 单调上升 → 高于自身 MA20
    own_dn = [10.0 - i for i in range(25)]  # 单调下跌 → 低于自身 MA20
    bars_up = [{"date": d, "close": c} for d, c in zip(sym_dates, own)]
    bars_dn = [{"date": d, "close": c} for d, c in zip(sym_dates, own_dn)]

    mask_up, _ = regime_mask(bars_up, _index(idx_dates, idx_closes), fast=20, use_own_ma=True)
    mask_dn, _ = regime_mask(bars_dn, _index(idx_dates, idx_closes), fast=20, use_own_ma=True)
    assert all(mask_up[d] for d in sym_dates[-3:])      # 个股上升 → 放行
    assert all(not mask_dn[d] for d in sym_dates[-3:])  # 个股下跌 → 禁止
