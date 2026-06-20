"""
AI 选股策略（scikit-learn 机器学习）

包含两种策略：
  1. offline_predict()  — 用模拟数据做 ML 预测（无需 QMT，随时可跑）
  2. live_predict()     — 接入 QMT 实时数据（需 QMT 运行）

特征工程：
  - 过去 N 日收益率
  - 波动率
  - 成交量变化
  - 价格相对位置
"""

import sys
sys.path.insert(0, r"D:\software\python\Lib\site-packages")

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, confusion_matrix


def _make_features(df: pd.DataFrame) -> pd.DataFrame:
    """从 K 线数据构造特征"""
    data = df.copy().sort_values("date")

    # 收益率
    data["ret_1"] = data["close"].pct_change(1)
    data["ret_5"] = data["close"].pct_change(5)
    data["ret_10"] = data["close"].pct_change(10)
    data["ret_20"] = data["close"].pct_change(20)

    # 波动率
    data["volatility_5"] = data["ret_1"].rolling(5).std()
    data["volatility_10"] = data["ret_1"].rolling(10).std()

    # 成交量变化
    data["volume_ma5"] = data["volume"].rolling(5).mean()
    data["volume_ratio"] = data["volume"] / data["volume_ma5"]

    # 价格位置 (当前价在 N 日区间的位置)
    data["high_20"] = data["high"].rolling(20).max()
    data["low_20"] = data["low"].rolling(20).min()
    data["pos_20"] = (data["close"] - data["low_20"]) / (data["high_20"] - data["low_20"] + 1e-8)

    # 标签：未来 5 天涨跌 (1=涨, 0=跌)
    data["fwd_ret_5"] = data["close"].shift(-5) / data["close"] - 1
    data["label"] = (data["fwd_ret_5"] > 0).astype(int)

    return data.dropna()


def offline_predict(show_detail: bool = True) -> dict:
    """
    离线训练 + 预测（使用模拟数据）
    返回模型性能指标
    """
    from data_fetcher import DataFetcher

    # 生成模拟 500 根 K 线
    raw = DataFetcher.mock_kline(500)
    features = _make_features(raw)

    feature_cols = [
        "ret_1", "ret_5", "ret_10", "ret_20",
        "volatility_5", "volatility_10",
        "volume_ratio", "pos_20",
    ]
    X = features[feature_cols]
    y = features["label"]

    # 划分训练/测试
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.3, shuffle=False
    )

    # 训练随机森林
    model = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42)
    model.fit(X_train, y_train)

    # 预测
    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)

    # 特征重要性
    importance = sorted(zip(feature_cols, model.feature_importances_),
                        key=lambda x: x[1], reverse=True)

    result = {
        "accuracy": round(acc, 4),
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "feature_importance": importance,
        "model": model,
    }

    if show_detail:
        cm = confusion_matrix(y_test, y_pred)
        print(f"[模型准确率] {acc:.2%}")
        print(f"[训练样本] {len(X_train)}, [测试样本] {len(X_test)}")
        print()
        print("[特征重要性排序]")
        for feat, imp in importance:
            print(f"   {feat:15s}  {imp:.4f}")
        print()
        print("[混淆矩阵]")
        print(f"          预测跌  预测涨")
        print(f" 实际跌    {cm[0][0]:5d}   {cm[0][1]:5d}")
        print(f" 实际涨    {cm[1][0]:5d}   {cm[1][1]:5d}")
        up_acc = cm[1][1] / (cm[1][0] + cm[1][1]) if (cm[1][0] + cm[1][1]) > 0 else 0
        down_acc = cm[0][0] / (cm[0][0] + cm[0][1]) if (cm[0][0] + cm[0][1]) > 0 else 0
        print(f"\n   上涨预测准确率: {up_acc:.2%}")
        print(f"   下跌预测准确率: {down_acc:.2%}")

    return result


def live_predict(stock_code: str = "000001.SZ") -> dict:
    """
    接入 QMT 实时数据做预测（需 QMT 客户端运行）
    """
    from data_fetcher import DataFetcher
    from xtquant import xtdata

    fetcher = DataFetcher()
    fetcher.connect()

    # 取最近 200 根日线
    kline = fetcher.get_kline(stock_code, period="1d", count=200)
    if kline.empty:
        return {"ok": False, "msg": f"未能获取 {stock_code} 的数据"}

    features = _make_features(kline)
    if len(features) < 30:
        return {"ok": False, "msg": "数据不足"}

    feature_cols = [
        "ret_1", "ret_5", "ret_10", "ret_20",
        "volatility_5", "volatility_10",
        "volume_ratio", "pos_20",
    ]

    # 训练模型
    model = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42)
    model.fit(features[feature_cols], features["label"])

    # 对最新一天预测
    latest = features.iloc[-1:][feature_cols]
    prob = model.predict_proba(latest)[0]
    pred = model.predict(latest)[0]

    detail = features.iloc[-1]
    result = {
        "ok": True,
        "stock": stock_code,
        "prediction": "上涨" if pred == 1 else "下跌",
        "confidence": round(float(max(prob)), 4),
        "latest_close": round(float(detail["close"]), 2),
        "features": {col: round(float(detail[col]), 4) for col in feature_cols},
    }

    print(f"[{stock_code}] 最新预测: {result['prediction']}")
    print(f"   置信度: {result['confidence']:.2%}")
    print(f"   最新收盘价: {result['latest_close']}")
    print(f"   特征值: {result['features']}")

    return result


if __name__ == "__main__":
    import sys
    if "--live" in sys.argv:
        live_predict("000001.SZ")
    else:
        offline_predict()
