"""
AI 选股策略（scikit-learn 机器学习）

包含两种策略：
  1. offline_predict()  — 用模拟数据做 ML 预测（无需 QMT，随时可跑）
  2. live_predict()     — 接入 QMT 实时数据做预测（需 QMT 运行 + 已训练模型）

特征工程：
  - 过去 N 日收益率
  - 波动率
  - 成交量变化
  - 价格相对位置

模型持久化：
  - save_model() / load_model() 使用 joblib 保存/加载模型到 models/ 目录
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, r"D:\software\python\Lib\site-packages")

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, confusion_matrix

import joblib

DEFAULT_MODEL_DIR = "models"
DEFAULT_MODEL_NAME = "latest_model.joblib"
DEFAULT_META_NAME = "latest_metadata.json"


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


def save_model(model, metadata: dict, model_dir: str = DEFAULT_MODEL_DIR,
               model_name: str = DEFAULT_MODEL_NAME,
               meta_name: str = DEFAULT_META_NAME):
    """保存模型和元数据到 models/ 目录"""
    os.makedirs(model_dir, exist_ok=True)

    model_path = os.path.join(model_dir, model_name)
    meta_path = os.path.join(model_dir, meta_name)

    joblib.dump(model, model_path)

    import json
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False, default=str)

    print(f"[模型已保存] {model_path}")
    print(f"[元数据已保存] {meta_path}")


def load_model(model_dir: str = DEFAULT_MODEL_DIR,
               model_name: str = DEFAULT_MODEL_NAME,
               meta_name: str = DEFAULT_META_NAME) -> tuple:
    """加载模型和元数据。返回 (model, metadata) 或 (None, None)"""
    model_path = os.path.join(model_dir, model_name)
    meta_path = os.path.join(model_dir, meta_name)

    if not os.path.exists(model_path):
        return None, None

    model = joblib.load(model_path)

    import json
    metadata = {}
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

    return model, metadata


def offline_predict(show_detail: bool = True) -> dict:
    """
    离线训练 + 预测（使用模拟数据）。
    使用 TimeSeriesSplit 做时序交叉验证，训练完成后保存模型。
    返回模型性能指标。
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

    # ── 时序交叉验证 ──────────────────────────────────────────
    tscv = TimeSeriesSplit(n_splits=5)
    fold_accuracies = []
    fold_cms = []

    if show_detail:
        print("[时序交叉验证] TimeSeriesSplit (n_splits=5)")

    for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

        # 切除训练集最后 5 行（shift(-5) 产生的标签污染）
        if len(X_train) > 5:
            X_train = X_train.iloc[:-5]
            y_train = y_train.iloc[:-5]

        model = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42)
        model.fit(X_train, y_train)
        y_pred = model.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        cm = confusion_matrix(y_test, y_pred)
        fold_accuracies.append(acc)
        fold_cms.append(cm)

        if show_detail:
            up_acc = cm[1][1] / (cm[1][0] + cm[1][1]) if (cm[1][0] + cm[1][1]) > 0 else 0
            down_acc = cm[0][0] / (cm[0][0] + cm[0][1]) if (cm[0][0] + cm[0][1]) > 0 else 0
            print(f"  Fold {fold + 1}: 准确率={acc:.2%}, 看涨={up_acc:.2%}, 看跌={down_acc:.2%}")

    avg_acc = np.mean(fold_accuracies)
    if show_detail:
        print(f"\n  平均准确率: {avg_acc:.2%} ({len(fold_accuracies)} folds)")

    # ── 在全量数据上训练最终模型 ───────────────────────────────
    # 切除最后 5 行避免标签污染
    if len(X) > 5:
        X_train_all = X.iloc[:-5]
        y_train_all = y.iloc[:-5]
    else:
        X_train_all = X
        y_train_all = y

    final_model = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42)
    final_model.fit(X_train_all, y_train_all)

    # ── 计算特征重要性 ─────────────────────────────────────────
    importance = sorted(zip(feature_cols, final_model.feature_importances_),
                        key=lambda x: x[1], reverse=True)

    # ── 保存模型 ───────────────────────────────────────────────
    metadata = {
        "training_date": datetime.now().isoformat(),
        "feature_cols": feature_cols,
        "accuracy_avg": round(float(avg_acc), 4),
        "accuracy_folds": [round(float(a), 4) for a in fold_accuracies],
        "n_estimators": 100,
        "max_depth": 6,
        "train_samples": len(X_train_all),
    }
    save_model(final_model, metadata)

    result = {
        "accuracy": round(float(avg_acc), 4),
        "train_samples": len(X_train_all),
        "fold_count": len(fold_accuracies),
        "feature_importance": importance,
        "model": final_model,
    }

    if show_detail:
        print()
        print("[特征重要性排序]")
        for feat, imp in importance:
            print(f"   {feat:15s}  {imp:.4f}")

    return result


def live_predict(stock_code: str = "000001.SZ") -> dict:
    """
    接入 QMT 实时数据做预测（需 QMT 客户端运行 + 已训练模型）。

    与旧版不同：不再每次取数据→当场训练，而是加载持久化的模型做 inference。
    如果 models/ 下没有已训练的模型，返回错误。
    """
    from data_fetcher import DataFetcher

    # ── 加载已训练模型 ─────────────────────────────────────────
    model, metadata = load_model()
    if model is None:
        return {
            "ok": False,
            "msg": "未找到已训练模型。请先运行 offline_predict() 训练并保存模型。"
        }

    feature_cols = metadata.get("feature_cols", [
        "ret_1", "ret_5", "ret_10", "ret_20",
        "volatility_5", "volatility_10",
        "volume_ratio", "pos_20",
    ])

    if show_detail := True:
        print(f"[加载模型] 训练日期: {metadata.get('training_date', 'unknown')}")
        print(f"[加载模型] 准确率: {metadata.get('accuracy_avg', 'unknown')}")

    # ── 获取实时数据 ───────────────────────────────────────────
    fetcher = DataFetcher()
    fetcher.connect()

    kline = fetcher.get_kline(stock_code, period="1d", count=200)
    if kline.empty:
        return {"ok": False, "msg": f"未能获取 {stock_code} 的数据"}

    features = _make_features(kline)
    if len(features) < 30:
        return {"ok": False, "msg": "数据不足（需至少30条有效特征行）"}

    # 验证特征列一致性
    missing = [c for c in feature_cols if c not in features.columns]
    if missing:
        return {"ok": False, "msg": f"数据缺少特征列: {missing}"}

    # ── 对最新一天做推理 ───────────────────────────────────────
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
        "model_training_date": metadata.get("training_date", "unknown"),
    }

    print(f"[{stock_code}] 最新预测: {result['prediction']}")
    print(f"   置信度: {result['confidence']:.2%}")
    print(f"   最新收盘价: {result['latest_close']}")
    print(f"   模型训练日期: {result['model_training_date']}")

    return result


# ═══════════════════════════════════════════════════════════════
# 2.4 多模型对比
# ═══════════════════════════════════════════════════════════════

MODEL_REGISTRY: dict[str, tuple] = {}


def _register_model(name: str, requires: list[str] | None = None):
    """返回一个装饰器，将被装饰函数注册为模型工厂。"""
    if requires is None:
        requires = []

    def decorator(factory):
        MODEL_REGISTRY[name] = (factory, requires)
        return factory

    return decorator


def _get_model_class(name: str):
    if name in MODEL_REGISTRY:
        factory, requires = MODEL_REGISTRY[name]
        for pkg in requires:
            try:
                __import__(pkg)
            except ImportError:
                print(f"[警告] 缺少 '{pkg}'，模型 '{name}' 不可用")
                return None
        return factory()
    return None


@_register_model("randomforest", requires=[])
def _make_randomforest():
    return RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42)


@_register_model("xgboost", requires=["xgboost"])
def _make_xgboost():
    import xgboost as xgb
    return xgb.XGBClassifier(
        n_estimators=100, max_depth=6, learning_rate=0.1,
        random_state=42, eval_metric="logloss",
    )


@_register_model("lightgbm", requires=["lightgbm"])
def _make_lightgbm():
    import lightgbm as lgb
    return lgb.LGBMClassifier(
        n_estimators=100, max_depth=6, learning_rate=0.1,
        random_state=42, verbose=-1,
    )


def compare_models(show_detail: bool = True) -> dict:
    """对比所有可用模型的准确率（相同时序交叉验证）。

    返回 {model_name: avg_accuracy}。
    """
    from data_fetcher import DataFetcher

    raw = DataFetcher.mock_kline(500)
    features = _make_features(raw)

    feature_cols = [
        "ret_1", "ret_5", "ret_10", "ret_20",
        "volatility_5", "volatility_10",
        "volume_ratio", "pos_20",
    ]
    X = features[feature_cols]
    y = features["label"]

    tscv = TimeSeriesSplit(n_splits=5)
    results = {}

    if show_detail:
        print("[多模型对比] TimeSeriesSplit (n_splits=5)")

    for model_name in sorted(MODEL_REGISTRY.keys()):
        model = _get_model_class(model_name)
        if model is None:
            continue

        fold_accs = []
        for train_idx, test_idx in tscv.split(X):
            X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
            y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

            if len(X_train) > 5:
                X_train = X_train.iloc[:-5]
                y_train = y_train.iloc[:-5]

            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)
            fold_accs.append(accuracy_score(y_test, y_pred))

        avg_acc = np.mean(fold_accs)
        results[model_name] = round(float(avg_acc), 4)

        if show_detail:
            print(f"  {model_name:15s}  {avg_acc:.2%} ({len(fold_accs)} folds)")

    if show_detail and results:
        best = max(results, key=results.get)
        print(f"\n  最佳: {best} ({results[best]:.2%})")

    return results


# ═══════════════════════════════════════════════════════════════
# 2.5 多股票批量预测
# ═══════════════════════════════════════════════════════════════

def batch_predict(stock_list: list[str],
                  verbose: bool = True) -> pd.DataFrame:
    """批量预测多只股票，返回按置信度降序的 DataFrame。

    :param stock_list: 股票代码列表
    :param verbose: 是否打印进度
    """
    from data_fetcher import DataFetcher

    model, metadata = load_model()
    if model is None:
        print("[错误] 未找到已训练模型。先运行 offline_predict()")
        return pd.DataFrame()

    feature_cols = metadata.get("feature_cols", [
        "ret_1", "ret_5", "ret_10", "ret_20",
        "volatility_5", "volatility_10",
        "volume_ratio", "pos_20",
    ])

    fetcher = DataFetcher()
    fetcher.connect()

    results = []
    total = len(stock_list)

    for i, code in enumerate(stock_list):
        if verbose and (i + 1) % 10 == 0:
            print(f"  进度: {i + 1}/{total}")

        try:
            kline = fetcher.get_kline(code, period="1d", count=200)
            if kline.empty:
                continue

            features = _make_features(kline)
            if len(features) < 30:
                continue

            missing = [c for c in feature_cols if c not in features.columns]
            if missing:
                continue

            latest = features.iloc[-1:][feature_cols]
            prob = model.predict_proba(latest)[0]
            pred = model.predict(latest)[0]
            confidence = max(prob)

            if confidence < 0.55:
                continue

            results.append({
                "code": code,
                "prediction": "up" if pred == 1 else "down",
                "confidence": round(float(confidence), 4),
                "prob_up": round(float(prob[1]), 4),
                "close": round(float(features.iloc[-1]["close"]), 2),
            })
        except Exception as e:
            if verbose:
                print(f"  [跳过] {code}: {e}")
            continue

    if not results:
        print("[警告] 无有效预测结果")
        return pd.DataFrame()

    df = pd.DataFrame(results).sort_values("confidence", ascending=False)
    df = df.reset_index(drop=True)

    if verbose:
        print(f"\n[批量预测完成] {len(df)}/{total} 只有效预测")
        top = df.head(5)
        for _, row in top.iterrows():
            d = "↑" if row["prediction"] == "up" else "↓"
            print(f"  {row['code']:>12s} {d}  "
                  f"置信度={row['confidence']:.2%}  收盘={row['close']}")

    return df


if __name__ == "__main__":
    import sys
    if "--live" in sys.argv:
        result = live_predict("000001.SZ")
        if not result["ok"]:
            print(f"错误: {result['msg']}")
    else:
        offline_predict()
