"""
通达信（TDX）日线数据读取器。

支持读取通达信/平安证券等客户端的本地 .day 文件，
转换为项目标准 DataFrame 格式（date, open, high, low, close, volume, amount, code）。
"""

import os
import struct
import pandas as pd


def _parse_tdx_code(filename: str) -> str:
    """
    从 TDX 文件名推断股票代码和市场。

    TDX 命名规则: sh600000.day / sz000001.day / bj830799.day
    返回格式: "600000.SH" / "000001.SZ" / "830799.BJ"
    """
    name = os.path.splitext(os.path.basename(filename))[0]
    market = name[:2].upper()
    ticker = name[2:]
    market_map = {"SH": "SH", "SZ": "SZ", "BJ": "BJ"}
    return f"{ticker}.{market_map.get(market, market)}"


def _parse_day_records(data: bytes, code: str) -> list[dict]:
    """解析 .day 文件字节数据为 record dict 列表（使用 iter_unpack 批量解析）"""
    records = []
    record_count = len(data) // 32
    data = data[: record_count * 32]

    for date_int, open_p, high_p, low_p, close_p, amount, volume, _, _ in struct.iter_unpack(
        "IIIIIfIhh", data
    ):
        if date_int < 19900101 or date_int > 20991231:
            continue
        records.append(
            {
                "date": pd.to_datetime(str(date_int), format="%Y%m%d"),
                "open": open_p / 1000.0,
                "high": high_p / 1000.0,
                "low": low_p / 1000.0,
                "close": close_p / 1000.0,
                "volume": volume,
                "amount": amount,
                "code": code,
            }
        )
    return records


def read_tdx_bytes(data: bytes, code: str) -> pd.DataFrame:
    """
    从内存中的 .day 文件字节数据解析，返回标准 DataFrame。

    参数
    ----
    data : bytes
        .day 文件的完整字节内容
    code : str
        股票代码，如 "600000.SH"
    """
    records = _parse_day_records(data, code)
    df = pd.DataFrame(records)
    if not df.empty:
        df = df.sort_values("date").reset_index(drop=True)
    return df


def read_tdx_day(filepath: str) -> pd.DataFrame:
    """
    读取单个通达信 .day 日线文件，返回标准 DataFrame。

    格式说明: 每条记录 32 字节 (date:4, o/h/l/c:4×4, amount:4, volume:4, reserved:8)
    """
    code = _parse_tdx_code(filepath)
    filesize = os.path.getsize(filepath)
    if filesize == 0:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount", "code"])
    with open(filepath, "rb") as f:
        raw = f.read()
    df = read_tdx_bytes(raw, code)
    return df


def read_tdx_dir(directory: str, pattern: str = "*.day") -> pd.DataFrame:
    """
    批量读取目录下所有 .day 文件，合并为单个 DataFrame。

    参数
    ----
    directory : str
        如 "D:/zd_pazq/vipdoc/sh/lday/"
    pattern : str
        文件名匹配模式，默认 "*.day"

    返回
    ----
    pd.DataFrame
        所有股票的合并数据
    """
    import glob

    files = sorted(glob.glob(os.path.join(directory, pattern)))
    frames = []
    for f in files:
        df = read_tdx_day(f)
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount", "code"])

    return pd.concat(frames, ignore_index=True).sort_values(["code", "date"]).reset_index(drop=True)


def find_tdx_vipdoc(root: str) -> str | None:
    """
    从通达信安装目录定位 vipdoc 路径。

    参数
    ----
    root : str
        通达信安装根目录

    返回
    ----
    str | None
        vipdoc 路径，未找到则返回 None
    """
    candidates = [
        os.path.join(root, "vipdoc"),
        os.path.join(root, "T0002"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def list_markets(vipdoc_path: str) -> dict[str, str]:
    """
    列出 vipdoc 下的市场及其日线数据目录。

    返回
    ----
    dict[str, str]
        {"SH": "D:/.../vipdoc/sh/lday", "SZ": "D:/.../vipdoc/sz/lday", ...}
    """
    markets = {}
    for mkt in ["sh", "sz", "bj"]:
        lday = os.path.join(vipdoc_path, mkt, "lday")
        if os.path.isdir(lday):
            markets[mkt.upper()] = lday
    return markets
