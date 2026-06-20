"""
Top-N 选股模块 (Phase 3.3)

遍历股票池 → 模型批量打分 → 返回置信度最高的 Top-N 标的。

优化批量推理效率（使用预加载的模型，避免每次重载）。

用法：
    from selector import StockSelector

    selector = StockSelector(stock_pool=["000001.SZ", "600000.SH", ...])
    top10 = selector.select_top_n(n=10)
"""

import sys
sys.path.insert(0, r"D:\software\python\Lib\site-packages")

import pandas as pd
from typing import Optional


class StockSelector:
    """Top-N 选股器。"""

    def __init__(self,
                 stock_pool: list[str] | None = None,
                 model_dir: str = "models",
                 feature_cols: list[str] | None = None):
        """
        :param stock_pool: 候选股票池
        :param model_dir: 模型目录
        :param feature_cols: 特征列列表（None 则从 metadata 读取）
        """
        self.stock_pool = stock_pool or []
        self.model_dir = model_dir
        self.feature_cols = feature_cols

        # 延迟加载
        self._model = None
        self._metadata = None

    def _load_model(self) -> bool:
        """加载模型（惰性，仅加载一次）。"""
        if self._model is not None:
            return True

        from ai_strategy import load_model
        model, metadata = load_model(model_dir=self.model_dir)
        if model is None:
            print("[错误] 未找到训练好的模型")
            return False

        self._model = model
        self._metadata = metadata

        if self.feature_cols is None:
            self.feature_cols = metadata.get("feature_cols", [
                "ret_1", "ret_5", "ret_10", "ret_20",
                "volatility_5", "volatility_10",
                "volume_ratio", "pos_20",
            ])
        return True

    def load_stock_pool_from_csv(self, csv_dir: str,
                                  market: str = "sh") -> list[str]:
        """从 data/ 目录自动加载股票池。"""
        import os, glob

        pattern = os.path.join(csv_dir, market, "*.csv")
        files = glob.glob(pattern)
        codes = []
        for f in files:
            code = os.path.splitext(os.path.basename(f))[0]
            suffix_map = {"sh": ".SH", "SH": ".SH", "sz": ".SZ", "SZ": ".SZ", "bj": ".BJ", "BJ": ".BJ"}
            suffix = suffix_map.get(market, ".SZ")
            codes.append(f"{code}{suffix}")
        self.stock_pool = codes
        return codes

    def select_top_n(self, n: int = 10,
                      min_confidence: float = 0.55,
                      verbose: bool = True) -> pd.DataFrame:
        """
        从股票池中选出 Top-N 只最看涨的股票。

        :param n: 返回数量
        :param min_confidence: 最低置信度阈值
        :param verbose: 是否打印进度
        :return: DataFrame，按置信度降序，列: code/confidence/prediction/close
        """
        if not self.stock_pool:
            print("[错误] 股票池为空。请先 load_stock_pool_from_csv() 或传入 stock_pool")
            return pd.DataFrame()

        if not self._load_model():
            return pd.DataFrame()

        from data_fetcher import DataFetcher
        from ai_strategy import _make_features

        fetcher = DataFetcher()
        fetcher.connect()

        results = []
        total = len(self.stock_pool)

        if verbose:
            print(f"[选股] 扫描 {total} 只股票...")

        for i, code in enumerate(self.stock_pool):
            if verbose and (i + 1) % 50 == 0:
                print(f"  进度: {i + 1}/{total} ({len(results)} 候选)")

            try:
                kline = fetcher.get_kline(code, period="1d", count=200)
                if kline.empty:
                    continue

                features = _make_features(kline)
                if len(features) < 30:
                    continue

                missing = [c for c in self.feature_cols
                          if c not in features.columns]
                if missing:
                    continue

                latest = features.iloc[-1:][self.feature_cols]
                prob = self._model.predict_proba(latest)[0]
                pred = self._model.predict(latest)[0]
                confidence = max(prob)

                if confidence < min_confidence:
                    continue

                results.append({
                    "code": code,
                    "prediction": "up" if pred == 1 else "down",
                    "confidence": round(float(confidence), 4),
                    "prob_up": round(float(prob[1]), 4),
                    "close": round(float(features.iloc[-1]["close"]), 2),
                    "ret_1": round(float(features.iloc[-1].get("ret_1", 0)), 4),
                })
            except Exception as e:
                if verbose and i < 10:
                    print(f"  [跳过] {code}: {e}")
                continue

        if not results:
            print("[警告] 无符合条件的股票")
            return pd.DataFrame()

        df = pd.DataFrame(results).sort_values("confidence", ascending=False)
        df = df.head(n).reset_index(drop=True)

        if verbose:
            print(f"\n[Top-{n} 选股结果]")
            for j, (_, row) in enumerate(df.iterrows()):
                direction = "↑" if row["prediction"] == "up" else "↓"
                print(f"  {j+1:2d}. {row['code']:>12s} {direction}  "
                      f"置信度={row['confidence']:.2%}  收盘={row['close']}")

        return df

    def get_score_table(self, n: int = 20) -> pd.DataFrame:
        """获取打分表（所有通过最低置信度的股票）。"""
        return self.select_top_n(n=n, min_confidence=0.50, verbose=False)


# ── 便捷函数 ────────────────────────────────────────────────────

def select_from_pool(stock_pool: list[str],
                     n: int = 10,
                     min_confidence: float = 0.55) -> pd.DataFrame:
    """快速选股：给定股票池 → Top-N。"""
    selector = StockSelector(stock_pool=stock_pool)
    return selector.select_top_n(n=n, min_confidence=min_confidence)


if __name__ == "__main__":
    print("=== 选股器自测（离线 mock）===\n")

    # 由于需要 QMT 在线才能实际拉数据，这里仅测试离线路径
    from data_fetcher import DataFetcher
    from ai_strategy import offline_predict

    # 确保有训练好的模型
    print("[准备] 先训练模型...")
    result = offline_predict(show_detail=False)

    # 测试选股器（用 mock 数据生成一小批预测）
    selector = StockSelector(stock_pool=["000001.SZ", "600000.SH", "600001.SH"])
    selector._load_model()

    if selector._model:
        print(f"\n[OK] 模型已加载，训练日期: {selector._metadata.get('training_date')}")
        print("（在线模式下 select_top_n() 会从 QMT 拉取实时数据）")
