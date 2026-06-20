"""
特征工程扩展模块。

提供三类因子：
  1. 动量因子 — RSI、MACD、KDJ (随机指标)
  2. 量价因子 — 量价相关性、OBV、成交量突破
  3. 价格因子 — 布林带位置、ATR、价格斜率

用法：
    from features import add_momentum_features, add_volume_features, add_price_features
    df = add_all_features(kline_df)
"""

import sys
sys.path.insert(0, r"D:\software\python\Lib\site-packages")

import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════
# 1. 动量因子
# ═══════════════════════════════════════════════════════════════

def add_rsi(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    """相对强弱指数 RSI。返回 DataFrame 新增 'rsi_{window}' 列。"""
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window, min_periods=window).mean()
    avg_loss = loss.rolling(window, min_periods=window).mean()
    rs = avg_gain / (avg_loss + 1e-8)
    df[f"rsi_{window}"] = 100 - (100 / (1 + rs))
    return df


def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26,
             signal: int = 9) -> pd.DataFrame:
    """MACD 指标。新增 macd / macd_signal / macd_hist 列。"""
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    return df


def add_kdj(df: pd.DataFrame, n: int = 9, m1: int = 3,
            m2: int = 3) -> pd.DataFrame:
    """KDJ 随机指标。新增 k / d / j 列。"""
    low_n = df["low"].rolling(n, min_periods=n).min()
    high_n = df["high"].rolling(n, min_periods=n).max()
    rsv = (df["close"] - low_n) / (high_n - low_n + 1e-8) * 100

    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    j = 3 * k - 2 * d

    df["kdj_k"] = k
    df["kdj_d"] = d
    df["kdj_j"] = j
    return df


# ═══════════════════════════════════════════════════════════════
# 2. 量价因子
# ═══════════════════════════════════════════════════════════════

def add_volume_corr(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """滚动量价相关系数。"""
    df["vol_price_corr"] = (
        df["volume"]
        .rolling(window, min_periods=window)
        .corr(df["close"])
    )
    return df


def add_obv(df: pd.DataFrame) -> pd.DataFrame:
    """能量潮 OBV：价格涨则累加成交量，跌则减去。"""
    direction = np.where(df["close"] > df["close"].shift(1), 1,
                         np.where(df["close"] < df["close"].shift(1), -1, 0))
    df["obv"] = (direction * df["volume"]).cumsum()
    return df


def add_volume_breakout(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """成交量突破：当前成交量 / N 日均量。"""
    df["vol_ma"] = df["volume"].rolling(window).mean()
    df["vol_breakout"] = df["volume"] / (df["vol_ma"] + 1e-8)
    df.drop(columns=["vol_ma"], inplace=True)
    return df


# ═══════════════════════════════════════════════════════════════
# 3. 价格因子
# ═══════════════════════════════════════════════════════════════

def add_bollinger(df: pd.DataFrame, window: int = 20,
                  std_mult: float = 2.0) -> pd.DataFrame:
    """布林带。新增 bb_upper / bb_lower / bb_position / bb_width 列。"""
    ma = df["close"].rolling(window).mean()
    std = df["close"].rolling(window).std()
    df["bb_upper"] = ma + std_mult * std
    df["bb_lower"] = ma - std_mult * std
    df["bb_position"] = (df["close"] - df["bb_lower"]) / (
        df["bb_upper"] - df["bb_lower"] + 1e-8
    )
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / (ma + 1e-8)
    return df


def add_atr(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    """平均真实波幅 ATR。"""
    high, low, close = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - close).abs(),
            (low - close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.rolling(window).mean()
    return df


def add_price_slope(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """价格线性回归斜率（日化）。"""
    df["price_slope"] = (
        df["close"]
        .rolling(window)
        .apply(lambda x: np.polyfit(range(len(x)), x, 1)[0], raw=True)
        / df["close"]
    )
    return df


# ═══════════════════════════════════════════════════════════════
# 批量添加
# ═══════════════════════════════════════════════════════════════

FEATURE_GROUPS = {
    "momentum": ["rsi_14", "macd", "macd_signal", "macd_hist",
                  "kdj_k", "kdj_d", "kdj_j"],
    "volume": ["vol_price_corr", "obv", "vol_breakout"],
    "price": ["bb_position", "bb_width", "atr", "price_slope"],
}


def add_all_features(df: pd.DataFrame) -> pd.DataFrame:
    """添加全部扩展特征到 DataFrame。"""
    # 动量
    add_rsi(df)
    add_macd(df)
    add_kdj(df)
    # 量价
    add_volume_corr(df)
    add_obv(df)
    add_volume_breakout(df)
    # 价格
    add_bollinger(df)
    add_atr(df)
    add_price_slope(df)
    return df


def get_feature_columns() -> list[str]:
    """返回所有扩展特征列名列表。"""
    cols = []
    for group in FEATURE_GROUPS.values():
        cols.extend(group)
    return cols


# ── 集成到 ai_strategy._make_features ──────────────────────────
def enrich_features(df: pd.DataFrame,
                    groups: list[str] | None = None) -> pd.DataFrame:
    """
    将扩展特征合并到已有的特征 DataFrame。

    :param df: _make_features 的输出 DataFrame
    :param groups: 要添加的特征组，默认全部；可选 ['momentum', 'volume', 'price']
    :return: 富集后的 DataFrame
    """
    if groups is None:
        groups = list(FEATURE_GROUPS.keys())

    features_df = df.copy()

    if "momentum" in groups:
        add_rsi(features_df)
        add_macd(features_df)
        add_kdj(features_df)

    if "volume" in groups:
        add_volume_corr(features_df)
        add_obv(features_df)
        add_volume_breakout(features_df)

    if "price" in groups:
        add_bollinger(features_df)
        add_atr(features_df)
        add_price_slope(features_df)

    return features_df


if __name__ == "__main__":
    # 自测
    from data_fetcher import DataFetcher
    kline = DataFetcher.mock_kline(200)
    df = add_all_features(kline.copy())
    new_cols = [c for c in df.columns if c not in kline.columns]
    print(f"原始列数: {len(kline.columns)}")
    print(f"扩展后列数: {len(df.columns)}")
    print(f"新增特征 ({len(new_cols)}):")
    for c in new_cols:
        null_pct = df[c].isna().mean()
        print(f"  {c:20s}  缺失率={null_pct:.1%}")
