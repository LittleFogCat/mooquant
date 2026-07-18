"""
mookquant * 技术指标库

约定：所有指标接收 list[float]，返回与输入等长的序列；
      不足计算周期的位置为 None。策略取 [-1]（当前）与 [-2]（上一根）。

这样既支持增量（on_bar 取最新值）也支持批量（回测取全序列），
与原 backtest_engine.py 的 sma 行为一致，迁移成本低。
"""

import math
from typing import List, Optional, Tuple


def sma(closes: List[float], period: int) -> List[Optional[float]]:
    """简单移动平均。返回完整序列，不足 period 处为 None。"""
    result: List[Optional[float]] = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            window = closes[i - period + 1: i + 1]
            result.append(sum(window) / period)
    return result


def ema(closes: List[float], period: int) -> List[Optional[float]]:
    """指数移动平均。首值用 SMA 初始化。"""
    result: List[Optional[float]] = []
    k = 2.0 / (period + 1)
    prev: Optional[float] = None
    for i, c in enumerate(closes):
        if i < period - 1:
            result.append(None)
        elif i == period - 1:
            prev = sum(closes[:period]) / period
            result.append(prev)
        else:
            prev = c * k + prev * (1 - k)
            result.append(prev)
    return result


def macd(closes: List[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[List, List, List]:
    """MACD 指标。返回 (dif, dea, hist) 三个等长序列。"""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    dif: List[Optional[float]] = []
    for f, s in zip(ema_fast, ema_slow):
        dif.append(None if (f is None or s is None) else f - s)
    # dea = EMA(dif, signal)，跳过前导 None
    first_valid = next((i for i, d in enumerate(dif) if d is not None), len(dif))
    dif_valid = [d for d in dif[first_valid:] if d is not None]
    dea_valid = ema(dif_valid, signal) if len(dif_valid) >= signal else [None] * len(dif_valid)
    pad = len(dif) - len(dea_valid)
    dea = [None] * pad + dea_valid if pad >= 0 else dea_valid[-len(dif):]
    hist = [None if (d is None or e is None) else 2 * (d - e) for d, e in zip(dif, dea)]
    return dif, dea, hist


def rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """RSI 相对强弱指标（Wilder 平滑）。"""
    n = len(closes)
    result: List[Optional[float]] = [None] * n
    if n <= period:
        return result
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains += change
        else:
            losses += -change
    avg_gain = gains / period
    avg_loss = losses / period
    result[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, n):
        change = closes[i] - closes[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return result


def boll(closes: List[float], period: int = 20,
         std_dev: float = 2.0) -> Tuple[List, List, List]:
    """布林带。返回 (upper, middle, lower) 三个等长序列。"""
    mid = sma(closes, period)
    upper: List[Optional[float]] = []
    lower: List[Optional[float]] = []
    for i in range(len(closes)):
        if i < period - 1 or mid[i] is None:
            upper.append(None)
            lower.append(None)
            continue
        window = closes[i - period + 1: i + 1]
        m = mid[i]
        variance = sum((x - m) ** 2 for x in window) / period
        sd = math.sqrt(variance)
        upper.append(m + std_dev * sd)
        lower.append(m - std_dev * sd)
    return upper, mid, lower


def kdj(highs: List[float], lows: List[float], closes: List[float],
        n: int = 9, m1: int = 3, m2: int = 3) -> Tuple[List, List, List]:
    """KDJ 随机指标。返回 (k, d, j) 三个等长序列。"""
    length = len(closes)
    k_list: List[Optional[float]] = [None] * length
    d_list: List[Optional[float]] = [None] * length
    j_list: List[Optional[float]] = [None] * length
    prev_k = 50.0
    prev_d = 50.0
    for i in range(length):
        if i < n - 1:
            continue
        window_high = highs[i - n + 1: i + 1]
        window_low = lows[i - n + 1: i + 1]
        hn = max(window_high)
        ln = min(window_low)
        rsv = (closes[i] - ln) / (hn - ln) * 100 if hn != ln else 50.0
        k = (m1 - 1) / m1 * prev_k + rsv / m1
        d = (m2 - 1) / m2 * prev_d + k / m2
        j = 3 * k - 2 * d
        k_list[i] = k
        d_list[i] = d
        j_list[i] = j
        prev_k = k
        prev_d = d
    return k_list, d_list, j_list
