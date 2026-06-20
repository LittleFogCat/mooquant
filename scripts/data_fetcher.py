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
        fields = ["time", "open", "high", "low", "close", "volume", "amount"]
        raw = xtdata.get_market_data(
            fields,
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

        # ── 提取真实交易日期 ──────────────────────────────────────
        # 根据周期选择时间格式：日线以上用 %Y%m%d，分钟线用 %Y%m%d%H%M%S
        is_intraday = period.endswith("m")
        time_fmt = "%Y%m%d%H%M%S" if is_intraday else "%Y%m%d"

        time_arr = raw.get("time", {}).get(stock_code, [])
        if time_arr and len(time_arr) > 0:
            df["date"] = [
                pd.Timestamp(xtdata.timetag_to_datetime(int(t), time_fmt))
                for t in time_arr
            ]
        else:
            # 回退：从交易日历获取（毫秒时间戳）
            try:
                import datetime as _dt
                today = _dt.date.today()
                start = today - _dt.timedelta(days=count * 3)
                market = "SH" if stock_code.startswith(("6", "9")) else "SZ"
                trading_dates = xtdata.get_trading_dates(
                    market,
                    start_time=start.strftime("%Y%m%d"),
                    end_time=today.strftime("%Y%m%d"),
                    count=count,
                )
                if trading_dates:
                    df["date"] = [pd.Timestamp(int(d), unit="ms").normalize()
                                  for d in trading_dates]
                else:
                    raise ValueError("no trading dates returned")
            except Exception:
                # 最后回退：从今天往前推 count 天（非连续非交易日）
                import datetime as _dt
                today = _dt.datetime.now()
                df["date"] = [
                    today - _dt.timedelta(days=count - i)
                    for i in range(count)
                ]

        return df

    # ── 多周期便捷方法 ──────────────────────────────────────────
    def get_daily_kline(self, stock_code: str, count: int = 100,
                        dividend_type: str = "front") -> pd.DataFrame:
        """获取日线 K 线（便捷方法）"""
        return self.get_kline(stock_code, period="1d", count=count,
                              dividend_type=dividend_type)

    def get_weekly_kline(self, stock_code: str, count: int = 52,
                         dividend_type: str = "front") -> pd.DataFrame:
        """获取周线 K 线"""
        return self.get_kline(stock_code, period="1w", count=count,
                              dividend_type=dividend_type)

    def get_monthly_kline(self, stock_code: str, count: int = 24,
                          dividend_type: str = "front") -> pd.DataFrame:
        """获取月线 K 线"""
        return self.get_kline(stock_code, period="1mon", count=count,
                              dividend_type=dividend_type)

    def get_minute_kline(self, stock_code: str, period: str = "1m",
                         count: int = 240,
                         dividend_type: str = "front") -> pd.DataFrame:
        """获取分钟线 K 线（1m / 5m / 15m / 30m / 60m）"""
        return self.get_kline(stock_code, period=period, count=count,
                              dividend_type=dividend_type)

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
    def mock_kline(count: int = 100, period: str = "1d",
                   stock_code: str = "000001.SZ") -> pd.DataFrame:
        """生成模拟 K 线数据，支持多周期，供离线测试使用。

        :param period: 周期 "1m"/"5m"/"1d"/"1w"/"1mon"
        """
        np.random.seed(42)
        base = 10.0

        # 根据周期生成时间序列
        freq_map = {
            "1m": "min", "5m": "5min", "15m": "15min",
            "30m": "30min", "60m": "h", "1d": "D", "1w": "W", "1mon": "MS",
        }
        freq = freq_map.get(period, "D")
        dates = pd.date_range(end=pd.Timestamp.now(), periods=count, freq=freq)

        closes = base * np.exp(np.cumsum(np.random.randn(count) * 0.02))
        df = pd.DataFrame({
            "date": dates,
            "open": closes * (1 + np.random.randn(count) * 0.005),
            "high": closes * (1 + abs(np.random.randn(count)) * 0.01),
            "low": closes * (1 - abs(np.random.randn(count)) * 0.01),
            "close": closes,
            "volume": np.random.randint(100000, 10000000, count),
            "code": stock_code,
        })
        return df


if __name__ == "__main__":
    # 离线测试：生成模拟数据
    df = DataFetcher.mock_kline(30)
    print("=== 模拟K线数据（30天）===")
    print(df.tail(5))
    print(f"\n数据范围: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")
