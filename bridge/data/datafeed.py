# -*- coding: utf-8 -*-
"""mookquant * 统一数据访问层（datafeed）

全项目唯一的行情取数入口。训练（trainer）/ 回测（backtest_engine）/
信号（model_server /signal）等所有模块必须经由本模块获取K线，
严禁各自直接调用 xtquant —— 本次「回测无交易」事故的根因就是
训练与回测各自取数、单位/复权口径漂移。

统一口径（务必遵守）：
  - volume：一律为「股」（xtquant 原始为「手」，内部 ×100）
  - dividend_type：默认 front_ratio（前复权比例版，v0.1.16+）。front（前复权）对早期历史
    （2001-2016 茅台等）会复权出负/零价格，front_ratio 是比例实现、无此 bug 且价格与
    front 几乎一致（差异 <0.2%），故全链路统一用它
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
# v3: 默认复权口径 front → front_ratio（前复权比例版，修复早期历史负价格 bug）
DATA_SPEC_VERSION = 3


def data_fingerprint(dividend_type="front_ratio"):
    """返回当前数据口径指纹（存入模型 config，加载时校验）。"""
    spec = {
        "spec_version": DATA_SPEC_VERSION,
        "volume_unit": "shares",       # 成交量统一为股
        "dividend_type": dividend_type,
        "fields": ["date", "open", "high", "low", "close", "volume"],
    }
    raw = json.dumps(spec, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def check_fingerprint(stored, dividend_type="front_ratio"):
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

def validate_bars(bars, symbol="", period="1d"):
    """校验 bar 列表的数据质量，返回问题列表（空列表 = 通过）。

    检查项：缺列/零价/OHLC交叉/明显缺口（缺口检查仅日线：分钟数据
    午休 11:30-13:00 与停牌日天然有洞，不按缺口论）。
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
        problems.append("{}: {}/{} 根bar OHLC 交叉（high<low 等）".format(symbol, bad_ohlc))

    # 停牌长缺口：日线下相邻 bar 间隔 >15 个自然日视为可疑（长假+停牌）；
    # 分钟线跳过（午休/停牌天然有洞）
    is_intraday = str(period).endswith("m") or (n > 0 and len(str(bars[0].get("date", ""))) > 10)
    if not is_intraday:
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


def _format_minute_date_str(s):
    """分钟 bar 日期字段归一：YYYYMMDDHHMM(ss) -> "YYYY-MM-DD HH:MM"。

    datafeed 层与 qmt_server._format_date_str 同口径，保证回测/实盘/缓存
    三个入口的分钟 bar date 字段一致（日内撮合按 date[:10] 判交易日、
    按 date 字符串比较做无前视切片，格式必须统一）。
    """
    digits = ''.join(c for c in str(s) if c.isdigit())
    if len(digits) == 8:
        return digits[:4] + "-" + digits[4:6] + "-" + digits[6:8]
    if len(digits) >= 12:
        return digits[:4] + "-" + digits[4:6] + "-" + digits[6:8] + " " + digits[8:10] + ":" + digits[10:12]
    return str(s)


def _df_to_bars(df):
    """xtquant DataFrame -> 统一 bar 列表（volume 手->股，amount 保留）。

    清洗：丢弃价格缺失/<=0/NaN 的 bar（前复权历史数据可能出现负价，front_ratio 下应极少），
    避免脏数据进入缓存与训练集。

    分钟周期说明：
      - date 归一为 "YYYY-MM-DD HH:MM"
      - amount 为 xtquant 原始成交额（元），供分时均线（vwap）计算
    """
    bars = []
    for idx, row in df.iterrows():
        date_str = str(idx)
        if len(date_str) == 8:
            date_fmt = date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:8]
        elif len(date_str) > 10:
            date_fmt = _format_minute_date_str(date_str)
        else:
            date_fmt = date_str[:10]
        try:
            o = float(row.get("open") or 0)
            h = float(row.get("high") or 0)
            l = float(row.get("low") or 0)
            c = float(row.get("close") or 0)
            v = float(row.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        # 价格缺失/非正/NaN -> 丢弃（不写入缓存，也不进训练）
        if not (o > 0 and h > 0 and l > 0 and c > 0):
            continue
        if o != o or h != h or l != l or c != c:
            continue
        try:
            amt = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        bars.append({
            "date": date_fmt,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v * 100,  # 手 -> 股
            "amount": amt,
        })
    return bars


def fetch_bars(symbol, period="1d", count=None, start_date=None, end_date=None,
               dividend_type="front_ratio", use_cache=True, validate=True):
    """获取K线（统一口径入口）。

    优先级：SQLite 缓存（区间完整时）-> xtquant 实时拉取 -> None（不在此处回落 mock，
    由调用方决定是否用 mock，避免训练误用模拟数据）。

    Args:
        symbol: UI 码（sh600519 / 600519.SH 均可）
        period: 1d/1m/5m/... （周线 1w/月线 1mon 内部先取日线再聚合）
        count: 取最近 N 根（与 start/end 二选一）
        start_date/end_date: YYYY-MM-DD 区间
        dividend_type: front_ratio/front/none/back
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
    # D0.4：显式带 spec_version，缓存口径不匹配（旧版本/口径漂移）时自动忽略并重新拉取
    # 分钟周期说明：缓存里分钟 bar 的 date 是 "YYYY-MM-DD HH:MM"，与 end_date
    #   "YYYY-MM-DD" 直接字符串比较恒为 False（"2026-08-28 14:56" < "2026-08-28"），
    #   完整性判断必须取 date[:10] 归一后再比，否则分钟缓存永远不命中、反复全量重拉
    if use_cache:
        if start_date and end_date:
            cached = db_cache.query_bars_by_date(xt_code, fetch_period, start_date, end_date,
                                                 dividend_type, spec_version=DATA_SPEC_VERSION)
            if cached and cached[0]["date"][:10] <= start_date and cached[-1]["date"][:10] >= end_date:
                return _aggregate(cached, period), "cache"
        else:
            # count 模式：取缓存最近 count 根（可能不足，继续走 xtquant 补全）
            cached = db_cache.query_bars(xt_code, fetch_period, count or -1, dividend_type,
                                          spec_version=DATA_SPEC_VERSION)
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
        # 分钟数据必须先显式 download 到本地，get_market_data_ex 才能读到
        # （QMT 客户端通常只预下载日线；不下载直接读分钟会返回空 DataFrame，
        #  这是「分钟线建模/回测失败」的根因之一）
        if fetch_period != "1d" or (start_date and end_date):
            if start_date and end_date:
                st, et = start_date.replace("-", ""), end_date.replace("-", "")
                xtdata.download_history_data(xt_code, period=fetch_period,
                                             start_time=st, end_time=et, incrementally=True)
            else:
                xtdata.download_history_data(xt_code, period=fetch_period,
                                             incrementally=True)

        if start_date and end_date:
            st, et = start_date.replace("-", ""), end_date.replace("-", "")
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
                db_cache.save_bars(xt_code, fetch_period, bars, dividend_type,
                                   spec_version=DATA_SPEC_VERSION)
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


def fetch_bars_or_mock(symbol, start_date, end_date, period="1d", dividend_type="front_ratio"):
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


# ---------------------------------------------------------------------------
# D1.4 数据健康监控 + 全市场增量同步（轻量版，无新依赖）
# ---------------------------------------------------------------------------

def _market_of(code):
    """按代码推断市场（SH/SZ/BJ/US/其他）。"""
    c = str(code or "")
    up = c.upper()
    if up.endswith('.SH') or up.startswith('SH'):
        return 'SH'
    if up.endswith('.SZ') or up.startswith('SZ'):
        return 'SZ'
    if up.endswith('.BJ') or up.startswith('BJ'):
        return 'BJ'
    if up.endswith('.US'):
        return 'US'
    digits = ''.join(ch for ch in c if ch.isdigit())
    if len(digits) == 6:
        if digits[0] in ('6', '9', '5'):
            return 'SH'
        if digits[0] in ('0', '2', '3'):
            return 'SZ'
        if digits[0] in ('4', '8'):
            return 'BJ'
    return '其他'


def coverage_report(ref_date=None, days_back=7):
    """数据健康/覆盖率报告（D1.4）：市场覆盖率、数据新旧度、缺口提示。

    基于 SQLite 缓存（日线 front_ratio）与 stocks 表。

    Args:
        ref_date: 参考日期 "YYYY-MM-DD"（默认今天；测试可注入）
        days_back: 最近 N 个自然日内有数据视为"新"

    Returns:
        {total_stocks, covered_stocks, coverage_rate, by_market,
         newest_date, stale_days, stale_stocks, problems}
    """
    import db as db_cache
    from datetime import datetime

    if ref_date is None:
        ref_date = datetime.now().strftime('%Y-%m-%d')
    ref = datetime.strptime(ref_date, '%Y-%m-%d')

    stocks = db_cache.get_all_stocks()
    coverage = db_cache.get_kline_coverage()
    total = len(stocks)

    by_market = {}
    covered_codes = set()
    for s in stocks:
        code = s.get('code') or ''
        key = to_xtcode(code)
        m = _market_of(code)
        by_market.setdefault(m, {'total': 0, 'covered': 0})
        by_market[m]['total'] += 1
        if key and key in coverage:
            by_market[m]['covered'] += 1
            covered_codes.add(key)
    covered = len(covered_codes)

    newest = None
    stale_codes = []
    for code, c in coverage.items():
        if newest is None or c['last'] > newest:
            newest = c['last']
        try:
            last = datetime.strptime(str(c['last'])[:10], '%Y-%m-%d')
            if (ref - last).days > days_back:
                stale_codes.append(code)
        except (ValueError, TypeError):
            pass

    problems = []
    if total == 0:
        problems.append('股票列表为空，请先在行情页同步股票列表')
    elif total and covered / total < 0.5:
        problems.append('覆盖率过低（{:.0%}），建议执行全市场同步（python bridge/market_sync.py）'.format(covered / total))
    if stale_codes:
        problems.append('{} 只标的日线数据超过 {} 天未更新'.format(len(stale_codes), days_back))

    return {
        'total_stocks': total,
        'covered_stocks': covered,
        'coverage_rate': round(covered / total, 4) if total else 0,
        'by_market': {m: {'total': v['total'], 'covered': v['covered']} for m, v in by_market.items()},
        'newest_date': newest,
        'stale_days': days_back,
        'stale_stocks': len(stale_codes),
        'problems': problems,
    }


def sync_market(limit=None, period='1d', progress=None):
    """全市场日线增量同步（D1.4 轻量版）：遍历 stocks 表逐标的走 fetch_bars 更新缓存。

    幂等（INSERT OR IGNORE + 区间覆盖），可反复执行；单标的失败记入 errors 不中断。
    真实拉取需 miniQMT；可用 --limit 试跑。

    Args:
        limit: 只同步前 N 只（测试/试跑）
        period: 周期（默认 1d）
        progress: 回调 (done, total, code)

    Returns:
        {total, synced, skipped, updated_bars, errors}
    """
    import db as db_cache

    stocks = db_cache.get_all_stocks()
    if limit:
        stocks = stocks[:limit]
    total = len(stocks)
    synced = skipped = updated = 0
    errors = []
    for i, s in enumerate(stocks):
        code = s.get('code') or ''
        if not code:
            skipped += 1
            continue
        try:
            bars, _source = fetch_bars(code, period=period, count=1500,
                                       dividend_type='front_ratio')
            if bars:
                synced += 1
                updated += len(bars)
            else:
                skipped += 1
        except Exception as e:
            errors.append('{}: {}'.format(code, e))
            skipped += 1
        if progress:
            try:
                progress(i + 1, total, code)
            except Exception:
                pass
    return {
        'total': total,
        'synced': synced,
        'skipped': skipped,
        'updated_bars': updated,
        'errors': errors[:20],
    }
