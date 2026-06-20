"""
信号生成模块 (Phase 3.2)

将模型预测（概率 + 置信度）转换为 buy / hold / sell 交易信号。

策略：
  - 默认规则：confidence > 阈值 且 预测方向="up" → buy
  - 可配置多级阈值、反转信号、冷却期等

用法：
    from signals import SignalGenerator
    sg = SignalGenerator()
    signals = sg.generate(predictions_df)
"""

import sys
sys.path.insert(0, r"D:\software\python\Lib\site-packages")

import pandas as pd
from dataclasses import dataclass


@dataclass
class SignalConfig:
    """信号生成配置。"""
    # 买入阈值：置信度 >= 此值才生成 buy 信号
    buy_confidence: float = 0.60
    # 卖出阈值：看跌置信度 >= 此值才生成 sell 信号
    sell_confidence: float = 0.60
    # 持有冷却期：买入后 N 天内不卖出
    hold_cooling_days: int = 3
    # 最低预测概率：prob_up 或 prob_down 低于此值视为 hold
    min_prob: float = 0.45


class SignalGenerator:
    """信号生成器。"""

    def __init__(self, config: SignalConfig | None = None):
        self.config = config or SignalConfig()
        self._last_buy: dict[str, int] = {}  # code → bar_index

    def generate(self, predictions: pd.DataFrame) -> pd.DataFrame:
        """
        生成交易信号。

        :param predictions: DataFrame，需含 code / prediction / confidence 列
        :return: 追加 signal 列（buy / sell / hold）的 DataFrame
        """
        df = predictions.copy()
        df["signal"] = "hold"

        for i, row in df.iterrows():
            code = row["code"]
            pred = row.get("prediction", "hold")
            conf = row.get("confidence", 0.5)

            if pred == "up" and conf >= self.config.buy_confidence:
                # 检查冷却期
                last_buy = self._last_buy.get(code, -999)
                if i - last_buy >= self.config.hold_cooling_days:
                    df.at[i, "signal"] = "buy"
                    self._last_buy[code] = i

            elif pred == "down" and conf >= self.config.sell_confidence:
                df.at[i, "signal"] = "sell"

            elif conf < self.config.min_prob:
                df.at[i, "signal"] = "hold"

        return df

    def filter_buy(self, predictions: pd.DataFrame,
                   top_n: int | None = None) -> pd.DataFrame:
        """返回仅 buy 信号的 DataFrame，可选 Top-N 按置信度排序。"""
        df = self.generate(predictions)
        buys = df[df["signal"] == "buy"].sort_values(
            "confidence", ascending=False
        )
        if top_n:
            buys = buys.head(top_n)
        return buys

    def filter_sell(self, predictions: pd.DataFrame) -> pd.DataFrame:
        """返回仅 sell 信号的 DataFrame。"""
        df = self.generate(predictions)
        return df[df["signal"] == "sell"]


# ── 便捷函数 ────────────────────────────────────────────────────

def generate_signals(predictions: pd.DataFrame,
                     buy_conf: float = 0.60,
                     sell_conf: float = 0.60) -> pd.DataFrame:
    """快速生成信号。"""
    config = SignalConfig(buy_confidence=buy_conf, sell_confidence=sell_conf)
    sg = SignalGenerator(config)
    return sg.generate(predictions)


def top_buys(predictions: pd.DataFrame,
             n: int = 10,
             min_confidence: float = 0.55) -> pd.DataFrame:
    """快速获取 Top-N 买入标的。"""
    df = predictions[predictions["prediction"] == "up"]
    df = df[df["confidence"] >= min_confidence]
    return df.sort_values("confidence", ascending=False).head(n)


if __name__ == "__main__":
    import random
    random.seed(42)

    # 模拟预测
    codes = [f"600{i:03d}" for i in range(20)]
    data = []
    for code in codes:
        for day in range(5):
            data.append({
                "date": f"2026-06-{20+day:02d}",
                "code": code,
                "prediction": random.choice(["up", "down"]),
                "confidence": random.uniform(0.4, 0.9),
            })

    preds = pd.DataFrame(data)

    sg = SignalGenerator()
    signals = sg.generate(preds)

    buys = signals[signals["signal"] == "buy"]
    sells = signals[signals["signal"] == "sell"]
    holds = signals[signals["signal"] == "hold"]

    print(f"总预测: {len(signals)}")
    print(f"  Buy:  {len(buys)} ({len(buys)/len(signals)*100:.0f}%)")
    print(f"  Sell: {len(sells)} ({len(sells)/len(signals)*100:.0f}%)")
    print(f"  Hold: {len(holds)} ({len(holds)/len(signals)*100:.0f}%)")

    print(f"\nTop 5 Buys:")
    for _, b in buys.head(5).iterrows():
        print(f"  {b['code']}  置信度={b['confidence']:.2%}  {b['date']}")
