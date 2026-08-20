# -*- coding: utf-8 -*-
"""datafeed 统一数据访问层单元测试。

覆盖：口径指纹、数据质量校验、涨跌停判定、mock 回落标记。
不依赖 xtquant（monkeypatch 隔离）。
"""
import pytest

from data import datafeed


# ---------------------------------------------------------------------------
# 口径指纹
# ---------------------------------------------------------------------------

def test_fingerprint_stable_and_divided():
    """同口径指纹稳定；不同复权口径指纹不同。"""
    fp1 = datafeed.data_fingerprint('front')
    fp2 = datafeed.data_fingerprint('front')
    fp3 = datafeed.data_fingerprint('none')
    assert fp1 == fp2
    assert fp1 != fp3
    assert len(fp1) == 16


def test_check_fingerprint():
    """匹配指纹通过；不匹配/缺失指纹给出人话原因。"""
    fp = datafeed.data_fingerprint('front')
    ok, _ = datafeed.check_fingerprint(fp, 'front')
    assert ok
    ok, reason = datafeed.check_fingerprint(datafeed.data_fingerprint('none'), 'front')
    assert not ok and '不一致' in reason
    ok, reason = datafeed.check_fingerprint('', 'front')
    assert not ok and '指纹' in reason


# ---------------------------------------------------------------------------
# 数据质量校验
# ---------------------------------------------------------------------------

def _good_bars(n=30):
    out = []
    price = 10.0
    for i in range(n):
        price *= 1.001
        out.append({"date": "2024-01-{:02d}".format(i + 1),
                    "open": price / 1.002, "high": price * 1.01,
                    "low": price * 0.99, "close": price, "volume": 1e6})
    return out


def test_validate_bars_good_data():
    assert datafeed.validate_bars(_good_bars(), "test") == []


def test_validate_bars_detects_ohlc_cross():
    bars = _good_bars()
    bars[5]["high"] = bars[5]["low"] - 1  # high < low
    problems = datafeed.validate_bars(bars, "test")
    assert any("OHLC" in p for p in problems)


def test_validate_bars_detects_zero_price():
    bars = _good_bars()
    bars[3]["close"] = 0
    problems = datafeed.validate_bars(bars, "test")
    assert any("<=0" in p for p in problems)


def test_validate_bars_detects_gap():
    bars = _good_bars()
    bars[10]["date"] = "2024-03-15"  # 突然跳到一个月后
    problems = datafeed.validate_bars(bars, "test")
    assert any("缺口" in p for p in problems)


# ---------------------------------------------------------------------------
# 涨跌停判定
# ---------------------------------------------------------------------------

def test_limit_up_down_main_board():
    """主板 10%：close 达到前收 1.10 倍（容差内）判定涨停。"""
    assert datafeed.is_limit_up("600519.SH", 11.0, 10.0)
    assert not datafeed.is_limit_up("600519.SH", 10.5, 10.0)
    assert datafeed.is_limit_down("600519.SH", 9.0, 10.0)
    assert not datafeed.is_limit_down("600519.SH", 9.5, 10.0)


def test_limit_up_down_chinext():
    """创业板 20%。"""
    assert datafeed.is_limit_up("300750.SZ", 12.0, 10.0)
    assert not datafeed.is_limit_up("300750.SZ", 11.0, 10.0)


def test_limit_up_down_star():
    """科创板 20%。"""
    assert datafeed.is_limit_up("688981.SH", 24.0, 20.0)
    assert not datafeed.is_limit_up("688981.SH", 22.0, 20.0)


def test_limit_with_invalid_inputs():
    assert not datafeed.is_limit_up("600519.SH", None, 10.0)
    assert not datafeed.is_limit_up("600519.SH", 11.0, 0)


# ---------------------------------------------------------------------------
# 数据体检摘要
# ---------------------------------------------------------------------------

def test_summary_basic():
    bars = _good_bars(50)
    s = datafeed.summary(bars, "sh600519")
    assert s["n"] == 50
    assert "start" in s and "end" in s
    assert 0 <= s["limit_ratio"] <= 1


def test_fetch_bars_or_mock_marks_mock():
    """mock 回落时 bars 带 is_mock 标记。"""
    import data.datafeed as df
    orig = df.fetch_bars
    df.fetch_bars = lambda *a, **k: ([], "empty")
    try:
        bars, source = df.fetch_bars_or_mock("sh600519", "2024-01-01", "2024-03-01")
        assert source == "mock"
        assert bars and all(b.get("is_mock") for b in bars)
    finally:
        df.fetch_bars = orig
