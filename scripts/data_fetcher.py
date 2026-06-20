"""
行情数据获取工具（QMT 数据接口）

用法：
    from data_fetcher import DataFetcher
    df = DataFetcher()
    kline = df.get_kline("000001.SZ", period="1d", count=60)
"""

import sys
sys.path.insert(0, r"D:\software\python\Lib\site-packages")

import numpy as np
import pandas as pd


class DataFetcher:
    """QMT 数据获取封装"""

    def __init__(self):
        self._connected = False

    def connect(self) -> bool:
        """连接 QMT 数据服务"""
        from xtquant import xtdata
        xtdata.connect()
        self._connected = True
        return True

    # ── K 线数据 ────────────────────────────────────────────────
    def get_kline(self, stock_code: str, period: str = "1d",
                  count: int = 100, dividend_type: str = "front") -> pd.DataFrame:
        """
        获取 K 线数据
        :param stock_code: 股票代码，如 "000001.SZ"
        :param period:     周期 "1m"/"5m"/"1d"/"1w"
        :param count:      获取条数
        :param dividend_type: 复权方式 "front"(前复权) / "back"(后复权) / "none"(不复权)
        :return: DataFrame
        """
        from xtquant import xtdata
        if not self._connected:
            self.connect()

        dt_map = {"none": 0, "front": 1, "back": 2}
        raw = xtdata.get_market_data(
            ["open", "high", "low", "close", "volume", "amount"],
            [stock_code],
            period=period,
            count=count,
            dividend_type=dt_map.get(dividend_type, 1),
        )
        if raw is None or raw.empty():
            return pd.DataFrame()

        # xtdata 返回的是 3D dict: {field: {code: [values]}}
        records = {}
        for field in ["open", "high", "low", "close", "volume", "amount"]:
            arr = raw.get(field, {}).get(stock_code, [])
            records[field] = list(arr)

        df = pd.DataFrame(records)
        df["code"] = stock_code
        df["date"] = pd.Timestamp.now().normalize() - pd.Timedelta(days=count)
        return df

    # ── 全市场股票列表 ──────────────────────────────────────────
    def get_stock_list(self, market: str = "SH") -> list:
        """
        获取板块股票列表
        :param market: "SH"(沪) / "SZ"(深) / "ALL"(沪深)
        """
        from xtquant.xtdata import get_stock_list_in_sector

        sector_map = {
            "SH": "上证A股",
            "SZ": "深证A股",
            "ALL": "沪深A股",
        }
        sector = sector_map.get(market, market)
        return get_stock_list_in_sector(sector)

    # ── 个股详情 ────────────────────────────────────────────────
    def get_instrument_detail(self, stock_code: str) -> dict:
        """获取股票基本信息"""
        from xtquant.xtdata import get_instrument_detail
        return get_instrument_detail(stock_code)

    # ── 测试函数（无需 QMT 运行）─────────────────────────────────
    @staticmethod
    def mock_kline(count: int = 100) -> pd.DataFrame:
        """生成模拟 K 线数据，供离线测试使用"""
        np.random.seed(42)
        base = 10.0
        dates = pd.date_range(end=pd.Timestamp.now(), periods=count, freq="D")
        closes = base * np.exp(np.cumsum(np.random.randn(count) * 0.02))
        df = pd.DataFrame({
            "date": dates,
            "open": closes * (1 + np.random.randn(count) * 0.005),
            "high": closes * (1 + abs(np.random.randn(count)) * 0.01),
            "low": closes * (1 - abs(np.random.randn(count)) * 0.01),
            "close": closes,
            "volume": np.random.randint(100000, 10000000, count),
            "code": "000001.SZ",
        })
        return df


if __name__ == "__main__":
    # 离线测试：生成模拟数据
    df = DataFetcher.mock_kline(30)
    print("=== 模拟K线数据（30天）===")
    print(df.tail(5))
    print(f"\n数据范围: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")
