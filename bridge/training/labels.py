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


def make_triple_barrier_labels(bars, horizon=10, up=0.05, down=0.05):
    # Triple barrier labeling (Lopez de Prado).
    # First hit up barrier -> buy(2), down barrier -> sell(0), timeout -> hold(1)
    closes = [b['close'] for b in bars]
    labels = []
    for i in range(len(closes) - horizon):
        entry = closes[i]
        label = 1  # hold by default
        for j in range(1, horizon + 1):
            if i + j >= len(closes):
                break
            ret = (closes[i + j] - entry) / entry if entry != 0 else 0.0
            if ret >= up:
                label = 2
                break
            elif ret <= -down:
                label = 0
                break
        labels.append(label)
    return labels
