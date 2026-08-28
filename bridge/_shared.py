"""Shared utilities: code conversion + mock data generation.

L5+L7: Extracted from backtest_engine.py and qmt_server.py to avoid duplication.
"""
import random
from datetime import datetime, timedelta


def to_xtcode(raw):
    """Convert UI code to xtquant format: sh600036 -> 600036.SH"""
    s = (raw or "").strip()
    if not s:
        return ""
    lower = s.lower()
    if "." in lower:
        head, _, tail = lower.partition(".")
        return head.upper() + "." + tail.upper()
    if lower.startswith("sh"):
        return lower[2:].upper() + ".SH"
    if lower.startswith("sz"):
        return lower[2:].upper() + ".SZ"
    if lower.startswith("bj"):
        return lower[2:].upper() + ".BJ"
    if lower.isdigit() and len(lower) == 6:
        f = lower[0]
        if f in ("6", "9", "5"):
            return lower + ".SH"
        if f in ("0", "2", "3"):
            return lower + ".SZ"
        return lower + ".SH"  # 未知首字符兜底（如 1/4/7/8）
    if lower.isalpha():
        return lower.upper() + ".US"
    return s.upper()


def generate_mock_bars(symbol, start_date=None, end_date=None, count=None):
    """Generate mock daily K-line bars.

    By date range (start_date/end_date) or by count.
    Uses symbol as seed for deterministic output.
    """
    seed_val = sum(ord(c) for c in symbol) + 42
    rng = random.Random(seed_val)
    base_price = 50 + rng.random() * 200
    bars = []
    price = base_price

    if count is not None:
        n = min(count if count > 0 else 60, 500)
        now = datetime.now()
        for i in range(n):
            ret = rng.gauss(0, 0.02)
            open_p = price
            close_p = price * (1 + ret)
            high_p = max(open_p, close_p) * (1 + abs(rng.gauss(0, 0.008)))
            low_p = min(open_p, close_p) * (1 - abs(rng.gauss(0, 0.008)))
            vol = int(rng.uniform(500000, 5000000))
            dt = now - timedelta(days=n - 1 - i)
            bars.append({
                "time": int(dt.timestamp()),
                "date": dt.strftime("%Y-%m-%d"),
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close_p, 2),
                "volume": vol,
            })
            price = close_p
    else:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        current = start
        while current <= end:
            if current.weekday() < 5:
                ret = rng.gauss(0, 0.02)
                open_p = price
                close_p = price * (1 + ret)
                high_p = max(open_p, close_p) * (1 + abs(rng.gauss(0, 0.008)))
                low_p = min(open_p, close_p) * (1 - abs(rng.gauss(0, 0.008)))
                vol = int(rng.uniform(500000, 5000000))
                bars.append({
                    "time": int(current.timestamp()),
                    "date": current.strftime("%Y-%m-%d"),
                    "open": round(open_p, 2),
                    "high": round(high_p, 2),
                    "low": round(low_p, 2),
                    "close": round(close_p, 2),
                    "volume": vol,
                })
                price = close_p
            current += timedelta(days=1)

    return bars


def generate_mock_minute_bars(symbol, start_date, end_date, period="1m"):
    """Generate mock intraday minute K-line bars (deterministic per symbol).

    每交易日 240 根 1m bar（09:30-11:30 / 13:00-15:00），日内 U 型波动
    （开盘/尾盘波动大、午间平缓），日间漂移由日种子决定。用于无 miniQMT
    环境的日内回测链路自测（正式回测默认拒绝 mock，D0.3 规则沿用）。

    Args:
        period: "1m"（当前仅支持 1m；5m 等由调用方聚合）
    Returns:
        list[dict]  date 为 "YYYY-MM-DD HH:MM"，含 amount（元）
    """
    minutes = int(period.rstrip("m") or 1)
    if minutes != 1:
        raise ValueError("generate_mock_minute_bars 仅支持 1m")

    seed_val = sum(ord(c) for c in symbol) + 7
    rng = random.Random(seed_val)
    base_price = 50 + rng.random() * 200

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    # A 股分钟时间表：09:30-11:29 + 13:00-14:59（每根 bar 标注其开始时间）
    session_times = []
    for h, m in [(9, 30 + i) for i in range(30)] + [(10, i) for i in range(60)] \
            + [(11, i) for i in range(30)] + [(13, i) for i in range(60)] \
            + [(14, i) for i in range(60)]:
        session_times.append((h, m))

    bars = []
    current = start
    day_price = base_price
    while current <= end:
        if current.weekday() < 5:
            # 日间漂移：隔夜收益（±3%）
            day_price = day_price * (1 + rng.gauss(0, 0.015))
            if day_price <= 1:
                day_price = 10.0
            price = day_price
            day_seed = seed_val + current.toordinal()
            day_rng = random.Random(day_seed)
            for idx, (h, m) in enumerate(session_times):
                # U 型波动：开盘半小时与尾盘半小时波动放大
                if idx < 30 or idx >= 210:
                    vol = 0.0016
                else:
                    vol = 0.0006
                ret = day_rng.gauss(0, vol)
                open_p = price
                close_p = price * (1 + ret)
                if close_p <= 0.01:
                    close_p = 0.01
                high_p = max(open_p, close_p) * (1 + abs(day_rng.gauss(0, vol / 2)))
                low_p = min(open_p, close_p) * (1 - abs(day_rng.gauss(0, vol / 2)))
                vol_shares = int(day_rng.uniform(3000, 60000))
                bars.append({
                    "date": current.strftime("%Y-%m-%d") + " {:02d}:{:02d}".format(h, m),
                    "open": round(open_p, 2),
                    "high": round(high_p, 2),
                    "low": round(low_p, 2),
                    "close": round(close_p, 2),
                    "volume": vol_shares,
                    "amount": round((open_p + high_p + low_p + close_p) / 4 * vol_shares, 2),
                })
                price = close_p
            day_price = price
        current += timedelta(days=1)
    return bars
