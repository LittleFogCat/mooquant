# mookquant * Label generation for training

from typing import List


def make_classification_labels(bars, horizon=5, threshold=0.02):
    # Classification labels based on future return.
    # 2=buy (return > threshold), 0=sell (return < -threshold), 1=hold
    closes = [b['close'] for b in bars]
    labels = []
    for i in range(len(closes) - horizon):
        ret = (closes[i + horizon] - closes[i]) / closes[i] if closes[i] != 0 else 0.0
        if ret > threshold:
            labels.append(2)
        elif ret < -threshold:
            labels.append(0)
        else:
            labels.append(1)
    return labels


def make_regression_labels(bars, horizon=5):
    # Regression labels: future horizon-day return rate.
    closes = [b['close'] for b in bars]
    labels = []
    for i in range(len(closes) - horizon):
        ret = (closes[i + horizon] - closes[i]) / closes[i] if closes[i] != 0 else 0.0
        labels.append(ret)
    return labels


def make_triple_barrier_labels(bars, horizon=5, up=0.03, down=0.03, adaptive=False, atr_mult=1.0):
    # Triple barrier labeling (Lopez de Prado).
    # First hit up barrier -> buy(2), down barrier -> sell(0), timeout -> hold(1)
    # horizon 默认 5（更短预测窗口，弱特征更可学）；up/down 默认 3%（与 horizon 匹配）
    # adaptive=True 时用 ATR 自适应障碍：up/down 参数失效，障碍 = atr_mult * ATR14/entry，
    # 波动大的标的障碍宽、波动小的障碍窄，跨标的可比（M3.2）
    closes = [b['close'] for b in bars]
    if adaptive:
        highs = [b.get('high', c) for b, c in zip(bars, closes)]
        lows = [b.get('low', c) for b, c in zip(bars, closes)]
        # 逐 bar ATR14（Wilder 平滑近似：直接滚动均值，实现简单且够用）
        atr = []
        trs = []
        for i in range(len(closes)):
            if i == 0:
                tr = highs[i] - lows[i]
            else:
                tr = max(highs[i] - lows[i],
                         abs(highs[i] - closes[i - 1]),
                         abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        for i in range(len(closes)):
            start = max(0, i - 13)
            atr.append(sum(trs[start:i + 1]) / (i - start + 1))
    labels = []
    for i in range(len(closes) - horizon):
        entry = closes[i]
        if adaptive:
            a = atr[i] if i < len(atr) else entry * 0.03
            up_barrier = atr_mult * a / entry if entry != 0 else up
            down_barrier = atr_mult * a / entry if entry != 0 else down
        else:
            up_barrier, down_barrier = up, down
        label = 1  # hold by default
        for j in range(1, horizon + 1):
            if i + j >= len(closes):
                break
            ret = (closes[i + j] - entry) / entry if entry != 0 else 0.0
            if ret >= up_barrier:
                label = 2
                break
            elif ret <= -down_barrier:
                label = 0
                break
        labels.append(label)
    return labels
