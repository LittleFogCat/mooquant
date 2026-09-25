"""领域模型。

字段形状与桌面端保持一致（见 AGENTS.md「行情数据领域模型统一形状」与
`main/datasources/mock.js`）。这里只做「服务端 JSON → 便于渲染的对象」的薄映射，
原始字典存在 `raw` 里，供 `--json` 原样输出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _as_float(value: Any) -> float:
    """服务端可能给数字、数字字符串或 null，统一成 float。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


# 占位符：数据源没有该项时会给这些值，渲染时应视同「没有」，不要显示成内容
_PLACEHOLDERS = {"", "-", "—", "――", "--", "－", "N/A"}


def _clean(value: Any) -> str:
    """去掉字段里的占位符，返回空串表示「无此信息」。"""
    text = _as_str(value).strip()
    return "" if text in _PLACEHOLDERS else text


# 裸 6 位数字代码按首位推断交易所。服务端只认带前缀的 `sh600519`，
# 用户在命令行上手敲的往往是 `600519`，这里替它补全。
_BARE_EXCHANGE = {
    "6": "sh",  # 600/601/603/605 主板、688 科创板
    "5": "sh",  # 51x/56x 场内基金
    "9": "sh",  # 900 B 股（920 开头归北交所，见下）
    "0": "sz",  # 000/001/002/003 主板与中小板
    "1": "sz",  # 15x 基金、12x 可转债
    "2": "sz",  # 200 B 股
    "3": "sz",  # 300 创业板
    "4": "bj",  # 43x 北交所
    "8": "bj",  # 83x/87x/88x 北交所
}


def normalize_code(code: str) -> str:
    """把用户输入的代码归一成服务端认的 `sh600519` 形式。

    - 去空白、统一小写
    - 已是 `sh600519` / `600519.sh` / `AAPL` 形式的原样保留
    - **纯 6 位数字**按首位推断交易所补前缀，`920xxx` 归北交所
    """
    text = _as_str(code).strip().lower()
    if not text:
        return ""
    if len(text) == 6 and text.isdigit():
        if text.startswith("92"):
            return "bj" + text
        exchange = _BARE_EXCHANGE.get(text[0])
        if exchange:
            return exchange + text
    return text


@dataclass
class Quote:
    """行情快照，对应 `/quotes` 的 `quotes[]` 元素。"""

    code: str
    name: str
    price: float = 0.0
    change: float = 0.0
    change_percent: float = 0.0
    prev_close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: float = 0.0
    turnover: float = 0.0
    industry: str = ""
    market: str = ""
    market_name: str = ""
    currency: str = "CNY"
    timestamp: str = ""
    # 服务端没有返回该代码（代码失效 / 数据源缺失），渲染成占位行而不是静默消失
    missing: bool = False
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: dict) -> Quote:
        return cls(
            code=_as_str(obj.get("code")),
            name=_as_str(obj.get("name")),
            price=_as_float(obj.get("price")),
            change=_as_float(obj.get("change")),
            change_percent=_as_float(obj.get("changePercent")),
            prev_close=_as_float(obj.get("prevClose")),
            open=_as_float(obj.get("open")),
            high=_as_float(obj.get("high")),
            low=_as_float(obj.get("low")),
            volume=_as_float(obj.get("volume")),
            turnover=_as_float(obj.get("turnover")),
            industry=_clean(obj.get("industry")),
            market=_as_str(obj.get("market")),
            market_name=_as_str(obj.get("marketName")),
            currency=_as_str(obj.get("currency")) or "CNY",
            timestamp=_as_str(obj.get("timestamp")),
            raw=obj,
        )

    @classmethod
    def placeholder(cls, code: str) -> Quote:
        """服务端 `errors[]` 里出现的代码：保留一行，标出它没取到。"""
        return cls(code=code, name=code, missing=True)


@dataclass
class Bar:
    """K 线，对应 `/history` 的 `bars[]` 元素。"""

    time: str
    date: str
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: dict) -> Bar:
        return cls(
            time=_as_str(obj.get("time")),
            date=_as_str(obj.get("date")),
            open=_as_float(obj.get("open")),
            high=_as_float(obj.get("high")),
            low=_as_float(obj.get("low")),
            close=_as_float(obj.get("close")),
            volume=_as_float(obj.get("volume")),
            raw=obj,
        )


@dataclass
class SearchItem:
    """搜索结果，对应 `/search` 的裸数组元素。"""

    code: str
    name: str
    industry: str = ""
    type: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: dict) -> SearchItem:
        return cls(
            code=_as_str(obj.get("code")),
            name=_as_str(obj.get("name")),
            industry=_clean(obj.get("industry")),
            type=_as_str(obj.get("type")),
            raw=obj,
        )


@dataclass
class StockItem:
    """股票列表项，对应 `/stocks` 的 `stocks[]` 元素（注意：无 `industry`）。"""

    code: str
    name: str
    exchange: str = ""
    type: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: dict) -> StockItem:
        return cls(
            code=_as_str(obj.get("code")),
            name=_as_str(obj.get("name")),
            exchange=_as_str(obj.get("exchange")),
            type=_as_str(obj.get("type")),
            raw=obj,
        )


@dataclass
class ServerTarget:
    """UDP 信标应答解析出的服务端目标。

    `api_base` 直接取自报文的 `urls[0]`，它**已经包含** `/mooquant/api` 前缀。
    """

    api_base: str
    source: str = ""
    token_required: bool = False
    name: str = ""
    version: str = ""
    hostname: str = ""
    host: str = ""
    port: int = 0

    @classmethod
    def from_beacon(cls, payload: dict) -> ServerTarget | None:
        urls = payload.get("urls")
        if not isinstance(urls, list) or not urls:
            return None
        return cls(
            api_base=_as_str(urls[0]).rstrip("/"),
            source=_as_str(payload.get("source")),
            token_required=bool(payload.get("tokenRequired")),
            name=_as_str(payload.get("name")),
            version=_as_str(payload.get("appVersion")),
            hostname=_as_str(payload.get("hostname")),
            host=_as_str(payload.get("host")),
            port=int(payload.get("port") or 0),
        )
