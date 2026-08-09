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
