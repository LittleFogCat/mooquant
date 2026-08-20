# -*- coding: utf-8 -*-
"""mookquant * 统一数据访问层（datafeed）

全项目唯一的行情取数入口。训练（trainer）/ 回测（backtest_engine）/
信号（model_server /signal）等所有模块必须经由本模块获取K线，
严禁各自直接调用 xtquant —— 本次「回测无交易」事故的根因就是
训练与回测各自取数、单位/复权口径漂移。

统一口径（务必遵守）：
  - volume：一律为「股」（xtquant 原始为「手」，内部 ×100）
  - dividend_type：默认 front（前复权），可显式传参覆盖
  - 代码格式：内部 UI 码（sh600519）， xtquant 码（600519.SH）转换只在这里做
  - bar 字段：{date, open, high, low, close, volume}，date 为 YYYY-MM-DD

口径指纹（fingerprint）：
  取数口径的摘要串。训练时写入模型 config.json，推理/回测加载模型时
  校验指纹是否一致，不一致直接报错，防止静默口径漂移。
"""
import os
import sys
import json
import hashlib

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BRIDGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BRIDGE_ROOT not in sys.path:
    sys.path.insert(0, _BRIDGE_ROOT)

from _shared import to_xtcode, generate_mock_bars  # noqa: E402

import db as db_cache  # noqa: E402

# ---------------------------------------------------------------------------
# 口径指纹
# ---------------------------------------------------------------------------

# 口径版本号：改动取数口径（单位/复权/字段）时必须 +1，并同步更新知识库
DATA_SPEC_VERSION = 2


def data_fingerprint(dividend_type="front"):
    """返回当前数据口径指纹（存入模型 config，加载时校验）。"""
    spec = {
        "spec_version": DATA_SPEC_VERSION,
        "volume_unit": "shares",       # 成交量统一为股
        "dividend_type": dividend_type,
        "fields": ["date", "open", "high", "low", "close", "volume"],
    }
    raw = json.dumps(spec, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def check_fingerprint(stored, dividend_type="front"):
    """校验模型持久化的指纹与当前口径是否一致。

    Returns:
        (ok: bool, reason: str)
    """
    if not stored:
        # 旧模型无指纹：不算失败，但提示重训
        return False, "模型缺少数据口径指纹（旧版本训练），建议重新训练"
    current = data_fingerprint(dividend_type)
    if stored == current:
        return True, ""
    return False, "数据口径指纹不匹配（训练时口径与当前不一致），请重新训练模型"


# ---------------------------------------------------------------------------
# 数据质量校验
# ---------------------------------------------------------------------------

def validate_bars(bars, symbol=""):
    """校验 bar 列表的数据质量，返回问题列表（空列表 = 通过）。

    检查项：缺列/零价/OHLC交叉/明显缺口。
    """
    problems = []
    if not bars:
        problems.append("{}: 无数据".format(symbol))
        return problems

    required = ("open", "high", "low", "close")
    n = len(bars)
    bad_ohlc = 0
    zero_price = 0
    for i, b in enumerate(bars):
        for k in required:
            v = b.get(k)
            if v is None or (isinstance(v, (int, float)) and v <= 0):
                zero_price += 1
                break
        else:
            o, h, l, c = b["open"], b["high"], b["low"], b["close"]
            if h < max(o, c) or l > min(o, c) or h < l:
                bad_ohlc += 1

    if zero_price:
        problems.append("{}: {}/{} 根bar价格缺失或<=0".format(symbol, zero_price, n))
    if bad_ohlc:
        problems.append("{}: {}/{} 根bar OHLC 交叉（high<low 等）".format(symbol, bad_ohlc, n))

    # 停牌长缺口：日线下相邻 bar 间隔 >15 个自然日视为可疑（长假+停牌）
    try:
        from datetime import datetime
        gaps = 0
        for i in range(1, n):
            d0 = datetime.strptime(str(bars[i - 1]["date"])[:10], "%Y-%m-%d")
            d1 = datetime.strptime(str(bars[i]["date"])[:10], "%Y-%m-%d")
            if (d1 - d0).days > 15:
                gaps += 1
        if gaps > 0:
            problems.append("{}: {} 处 >15 自然日的数据缺口（可能停牌）".format(symbol, gaps))
    except (ValueError, KeyError, TypeError):
        problems.append("{}: 存在无法解析的日期字段".format(symbol))
    return problems


# ---------------------------------------------------------------------------
# 涨跌停判定（A股）
# ---------------------------------------------------------------------------

def limit_ratio(xt_code):
    """按代码推断涨跌停幅度：创业板/科创板 20%，ST 简化按 5%（名称不可知时按主板 10%）。"""
    code = (xt_code or "").split(".")[0]
    if not code:
        return 0.10
    if code.startswith("30") or code.startswith("68"):
        return 0.20
    if code.startswith("8") or code.startswith("4"):  # 北交所
        return 0.30
    return 0.10


def is_limit_up(xt_code, close, prev_close):
    """收盘涨停判定（按板块幅度，容差 0.2% 处理四舍五入）。"""
    if not prev_close or prev_close <= 0 or close is None:
        return False
    return close >= prev_close * (1 + limit_ratio(xt_code) - 0.002)


def is_limit_down(xt_code, close, prev_close):
    """收盘跌停判定。"""
    if not prev_close or prev_close <= 0 or close is None:
        return False
    return close <= prev_close * (1 - limit_ratio(xt_code) + 0.002)


# ---------------------------------------------------------------------------
# 取数核心
# ---------------------------------------------------------------------------

def _xtdata():
    """惰性导入 xtquant.xtdata，失败返回 None。"""
    custom_path = os.environ.get("XTQUANT_PATH", "")
    if custom_path and custom_path not in sys.path:
        sys.path.insert(0, custom_path)
    try:
        from xtquant import xtdata
        return xtdata
    except ImportError:
        return None


def _connect(xtdata):
    port = int(os.environ.get("QMT_PORT", "58610"))
    xtdata.connect(port=port)


def _df_to_bars(df):
    """xtquant DataFrame -> 统一 bar 列表（volume 手->股）。"""
    bars = []
    for idx, row in df.iterrows():
        date_str = str(idx)
        if len(date_str) == 8:
            date_fmt = date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:8]
        else:
            date_fmt = date_str[:10]
        bars.append({
            "date": date_fmt,
            "open": float(row.get("open", 0) or 0),
            "high": float(row.get("high", 0) or 0),
            "low": float(row.get("low", 0) or 0),
            "close": float(row.get("close", 0) or 0),
            "volume": float(row.get("volume", 0) or 0) * 100,  # 手 -> 股
        })
    return bars


def fetch_bars(symbol, period="1d", count=None, start_date=None, end_date=None,
               dividend_type="front", use_cache=True, validate=True):
    """获取K线（统一口径入口）。

    优先级：SQLite 缓存（区间完整时）-> xtquant 实时拉取 -> None（不在此处回落 mock，
    由调用方决定是否用 mock，避免训练误用模拟数据）。

    Args:
        symbol: UI 码（sh600519 / 600519.SH 均可）
        period: 1d/1m/5m/... （周线 1w/月线 1mon 内部先取日线再聚合）
        count: 取最近 N 根（与 start/end 二选一）
        start_date/end_date: YYYY-MM-DD 区间
        dividend_type: front/none/back
        use_cache: 是否允许读 SQLite 缓存（拉到后也会写缓存）
        validate: 是否做质量校验

    Returns:
        (bars, data_source)  data_source: "cache" | "xtquant" | "empty"
    """
    xt_code = to_xtcode(symbol)
    if not xt_code:
        return [], "empty"

    fetch_period = "1d" if period in ("1w", "1mon") else period

    # --- 1. 缓存（按日期区间查询时才可靠；count 模式先换算成日期区间查询）---
    if use_cache:
        if start_date and end_date:
            cached = db_cache.query_bars_by_date(xt_code, fetch_period, start_date, end_date, dividend_type)
            if cached and cached[0]["date"] <= start_date and cached[-1]["date"] >= end_date:
                return _aggregate(cached, period), "cache"
        else:
            # count 模式：取缓存最近 count 根（可能不足，继续走 xtquant 补全）
            cached = db_cache.query_bars(xt_code, fetch_period, count or -1, dividend_type)
            if cached and count and len(cached) >= count:
                return _aggregate(cached[-count:], period), "cache"

    # --- 2. xtquant ---
    xtdata = _xtdata()
    if xtdata is None:
        return [], "empty"
    try:
        _connect(xtdata)
    except Exception:
        return [], "empty"

    try:
        if start_date and end_date:
            st, et = start_date.replace("-", ""), end_date.replace("-", "")
            xtdata.download_history_data(xt_code, period=fetch_period,
                                         start_time=st, end_time=et, incrementally=True)
            data = xtdata.get_market_data_ex(
                [], [xt_code], period=fetch_period, start_time=st, end_time=et,
                dividend_type=dividend_type) or {}
        else:
            data = xtdata.get_market_data_ex(
                [], [xt_code], period=fetch_period,
                count=count if count and count > 0 else 1500,
                dividend_type=dividend_type) or {}

        df = data.get(xt_code)
        if df is None or len(df) == 0:
            return [], "empty"
        bars = _df_to_bars(df)
        if bars and use_cache:
            try:
                db_cache.save_bars(xt_code, fetch_period, bars, dividend_type)
            except Exception:
                pass
        return _aggregate(bars, period), "xtquant"
    except Exception:
        return [], "empty"


def _aggregate(bars, target_period):
    """日线聚合为周线/月线（聚合逻辑单一实现：backtest_engine 的私有函数）。"""
    if not bars or target_period in ("1d", None):
        return bars
    import backtest_engine as _be
    if target_period == "1w":
        return _be._aggregate_weekly(bars)
    if target_period == "1mon":
        return _be._aggregate_monthly(bars)
    return bars


def fetch_bars_or_mock(symbol, start_date, end_date, period="1d", dividend_type="front"):
    """回测专用：真实数据优先，拉不到回落 mock（mock bars 带 is_mock=True 标记）。"""
    bars, source = fetch_bars(symbol, period=period, start_date=start_date,
                              end_date=end_date, dividend_type=dividend_type)
    if bars:
        return bars, source
    mock = generate_mock_bars(symbol, start_date, end_date)
    for b in mock:
        b["is_mock"] = True
    return mock, "mock"


def summary(bars, symbol=""):
    """数据体检摘要：日期范围/有效bar数/缺失天数/涨跌停bar占比。训练前打印。"""
    if not bars:
        return {"symbol": symbol, "n": 0}
    closes = [b["close"] for b in bars]
    prev = [None] + closes[:-1]
    n_limit = 0
    xt_code = to_xtcode(symbol)
    for c, p in zip(closes, prev):
        if p and (is_limit_up(xt_code, c, p) or is_limit_down(xt_code, c, p)):
            n_limit += 1
    try:
        from datetime import datetime
        d0 = datetime.strptime(str(bars[0]["date"])[:10], "%Y-%m-%d")
        d1 = datetime.strptime(str(bars[-1]["date"])[:10], "%Y-%m-%d")
        span_days = (d1 - d0).days + 1
        expected = span_days * 5 // 7  # 粗略交易日
        missing = max(0, expected - len(bars))
    except (ValueError, KeyError):
        span_days, missing = -1, -1
    return {
        "symbol": symbol, "n": len(bars),
        "start": str(bars[0]["date"])[:10], "end": str(bars[-1]["date"])[:10],
        "span_days": span_days, "missing_est": missing,
        "limit_ratio": round(n_limit / len(bars), 4),
    }
