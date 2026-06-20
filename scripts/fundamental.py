"""
基本面数据接入模块。

通过 xtdata 获取股票基本面指标，集成到特征工程中。

主要指标：
  - 换手率 (turnover_rate)
  - 市盈率 (pe_ttm)
  - 市净率 (pb)
  - 总市值 (total_mv)

用法：
    from fundamental import FundamentalFetcher

    ff = FundamentalFetcher()
    df_fund = ff.get_fundamentals(["000001.SZ", "600000.SH"], count=60)
    # 或直接用 mock 数据离线测试
    df_fund = FundamentalFetcher.mock_fundamentals(count=60)
"""

import sys
sys.path.insert(0, r"D:\software\python\Lib\site-packages")

import numpy as np
import pandas as pd

# xtquant 中 get_market_data_ex 支持的财务指标字段
FUND_FIELDS = [
    "turnoverRate",   # 换手率
    "pe_ttm",         # 市盈率 TTM
    "pb",             # 市净率
    "totalMV",        # 总市值
]

# 内部 xtdata 字段名 → 项目标准列名
FIELD_MAP = {
    "turnoverRate": "turnover_rate",
    "pe_ttm": "pe_ttm",
    "pb": "pb",
    "totalMV": "total_mv",
}


class FundamentalFetcher:
    """基本面数据获取封装。"""

    def __init__(self):
        self._connected = False

    def connect(self) -> bool:
        """连接 QMT 数据服务"""
        from xtquant import xtdata
        xtdata.connect()
        self._connected = True
        return True

    def get_fundamentals(self, stock_list: list[str],
                         count: int = 60) -> pd.DataFrame:
        """
        获取股票列表的基本面日频数据。

        :param stock_list: 股票代码列表，如 ["000001.SZ", "600000.SH"]
        :param count: 获取条数
        :return: DataFrame，列: turnover_rate/pe_ttm/pb/total_mv，
                 index: (date, code)
        """
        from xtquant import xtdata
        if not self._connected:
            self.connect()

        try:
            raw = xtdata.get_market_data_ex(
                list(FIELD_MAP.keys()),
                stock_list,
                period="1d",
                count=count,
            )
        except Exception:
            return pd.DataFrame()

        if raw is None or (hasattr(raw, "empty") and raw.empty()):
            return pd.DataFrame()

        frames = []
        for field_name in FIELD_MAP:
            col_name = FIELD_MAP[field_name]
            try:
                field_data = raw[field_name]  # DataFrame: index=codes, columns=dates
                # 转置：变 index=dates, columns=codes
                field_df = field_data.T
                field_df.columns = [f"{col_name}_{c}" for c in field_df.columns]
                frames.append(field_df)
            except (KeyError, Exception):
                continue

        if not frames:
            return pd.DataFrame()

        result = pd.concat(frames, axis=1)
        result.index.name = "date"
        return result

    def merge_features(self, kline_df: pd.DataFrame,
                       fund_df: pd.DataFrame,
                       stock_code: str) -> pd.DataFrame:
        """
        将基本面数据合并到 K 线特征 DataFrame 中。

        :param kline_df: K 线 DataFrame，必须有 'date' 列
        :param fund_df: get_fundamentals() 返回的 DataFrame
        :param stock_code: 当前股票代码（用于匹配列名）
        :return: 合并后的 DataFrame
        """
        if fund_df.empty:
            return kline_df

        # 把 date 对齐
        df = kline_df.copy()
        df["date_dt"] = pd.to_datetime(df["date"]).dt.normalize()

        if isinstance(fund_df.index, pd.DatetimeIndex):
            fund_aligned = fund_df.copy()
            fund_aligned.index = fund_aligned.index.normalize()
        else:
            return kline_df

        # 找到该股票在基本面数据中的列
        for fund_field, local_name in FIELD_MAP.items():
            col = f"{local_name}_{stock_code}"
            if col in fund_aligned.columns:
                # 按日期合并
                df = df.join(fund_aligned[col], on="date_dt", how="left")
                # 前向填充缺失值
                df[col] = df[col].ffill()

        df = df.drop(columns=["date_dt"])
        return df

    @staticmethod
    def mock_fundamentals(count: int = 100,
                          stock_list: list[str] | None = None) -> pd.DataFrame:
        """
        生成模拟基本面数据，供离线测试使用。
        数据带有合理的范围约束和自相关性。
        """
        if stock_list is None:
            stock_list = ["000001.SZ"]

        np.random.seed(43)
        dates = pd.date_range(end=pd.Timestamp.now(), periods=count, freq="D")

        data = {}
        for code in stock_list:
            # 模拟 PE (5~50，有自相关)
            pe_raw = np.cumsum(np.random.randn(count) * 0.5) + 20
            pe_raw = np.clip(pe_raw, 5, 50)
            data[f"pe_ttm_{code}"] = list(pe_raw)

            # 模拟 PB (0.5~8)
            pb_raw = np.cumsum(np.random.randn(count) * 0.1) + 2.5
            pb_raw = np.clip(pb_raw, 0.5, 8)
            data[f"pb_{code}"] = list(pb_raw)

            # 模拟换手率 (0.1%~15%)
            turnover = np.abs(np.random.randn(count) * 2 + 3)
            turnover = np.clip(turnover, 0.1, 15)
            data[f"turnover_rate_{code}"] = list(turnover)

            # 模拟总市值 (亿) (10~5000)
            mv = np.cumsum(np.random.randn(count) * 20) + 500
            mv = np.clip(mv, 10, 5000)
            data[f"total_mv_{code}"] = list(mv)

        df = pd.DataFrame(data, index=dates)
        df.index.name = "date"
        return df


# ── 便捷函数 ────────────────────────────────────────────────────

def enrich_features(kline_df: pd.DataFrame, stock_code: str,
                    count: int = 200) -> pd.DataFrame:
    """
    获取基本面数据并合并到 K 线特征中。

    在线模式：从 QMT 获取；离线模式：使用 mock 数据。
    """
    fetcher = FundamentalFetcher()

    try:
        fund_df = fetcher.get_fundamentals([stock_code], count=count)
        if fund_df.empty:
            fund_df = fetcher.mock_fundamentals(count=count,
                                                stock_list=[stock_code])
    except Exception:
        fund_df = fetcher.mock_fundamentals(count=count,
                                            stock_list=[stock_code])

    return fetcher.merge_features(kline_df, fund_df, stock_code)


if __name__ == "__main__":
    # 自测：离线 mock 数据
    print("=== 基本面 Mock 数据测试 ===")
    df = FundamentalFetcher.mock_fundamentals(count=10, stock_list=["000001.SZ"])
    print(df.tail())
    print(f"\n形状: {df.shape}")

    # 测试合并
    from data_fetcher import DataFetcher
    kline = DataFetcher.mock_kline(100)
    merged = enrich_features(kline, "000001.SZ", count=100)
    print(f"\n合并后列数: {len(merged.columns)}")
    print(f"新增列: {[c for c in merged.columns if c not in kline.columns]}")
