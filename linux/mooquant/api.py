"""桌面端只读 HTTP API 的客户端封装。

只用标准库 `urllib` —— 目标机没有 pip，装不了 requests。

两个必须守住的点：

1. 服务端在非 2xx 时**同样返回完整信封**（401 / 404 / 429），而 `urllib` 遇到非 2xx
   会抛 `HTTPError`。`HTTPError` 本身是 file-like，必须读它的 body 才能拿到真正的
   `msg`；只 `except URLError` 会把错误信息整条丢掉。
2. token 走 `X-Api-Token` 请求头而不是 query —— 服务端两种都收，但走 URL 会让 token
   出现在日志、历史和 `ps` 里。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

from . import __version__
from .config import MAX_BATCH_CODES
from .errors import ApiError, ApiFormatError, NotFoundError, TransportError
from .models import Bar, Quote, SearchItem, StockItem, normalize_code

DEFAULT_TIMEOUT = 8.0

# 服务端 `/history` 的 period 枚举（routes.js）
PERIODS = ("1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mon")
# 服务端 `/history` 的复权枚举
DIVIDEND_TYPES = ("none", "front", "back", "front_ratio", "back_ratio")


def _chunked(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class ApiClient:
    """一次命令生命周期内复用的 HTTP 客户端。"""

    def __init__(
        self, api_base: str, token: str = "", timeout: float = DEFAULT_TIMEOUT
    ) -> None:
        base = str(api_base or "").strip().rstrip("/")
        if not base:
            raise NotFoundError(
                "尚未配置服务地址。先跑 `mooquant discover`，"
                "或 `mooquant config set-url http://<主机>:12678/mooquant/api`"
            )
        self.api_base = base
        self.token = str(token or "").strip()
        self.timeout = timeout
        # 最近一次响应头，供 status / -v 展示数据源与版本
        self.last_source = ""
        self.last_version = ""

    # ---- 各路由 ----

    def endpoints(self) -> dict:
        """服务自描述接口总览（`GET /mooquant/api`）。"""
        return self._request("")

    def status(self, probe: bool = True) -> dict:
        return self._request("/status", {"probe": 1 if probe else 0})

    def quote(self, code: str) -> Quote:
        """单只快照。服务端返回裸对象而非信封里的数组。"""
        data = self._request("/quote", {"code": normalize_code(code)})
        if not isinstance(data, dict):
            raise ApiFormatError("/quote 的 data 应为对象")
        return Quote.from_json(data)

    def quotes(self, codes: Iterable[str]) -> list[Quote]:
        """批量快照。

        >50 只自动切片（服务端单请求上限，见 config/default.json 的 maxBatchCodes），
        结果按**输入顺序**排列。服务端没返回的代码（进了 `errors[]`）补一行占位，
        否则它会从表格里静默消失，看不出哪里不对。
        """
        ordered: list[str] = []
        for raw in codes:
            code = normalize_code(raw)
            if code and code not in ordered:  # 去重且保序，避免重复行白占请求配额
                ordered.append(code)
        if not ordered:
            return []

        by_code: dict[str, Quote] = {}
        for chunk in _chunked(ordered, MAX_BATCH_CODES):
            data = self._request(
                "/quotes", {"codes": ",".join(chunk), "skipError": 1}
            )
            for item in (data or {}).get("quotes") or []:
                quote = Quote.from_json(item)
                by_code[quote.code] = quote

        return [by_code.get(code) or Quote.placeholder(code) for code in ordered]

    def history(
        self,
        code: str,
        period: str = "1d",
        count: int = 60,
        dividend: str = "front",
    ) -> dict:
        """K 线历史。返回服务端原始 dict（`{code, period, count, bars[]}`）。

        这里**不本地校验** count / period 的边界：服务端自己会回 `10002 PARAM_RANGE`
        和带说明的 `msg`，忠实转述比在客户端复刻一遍规则更不容易和它对不上。
        """
        return self._request(
            "/history",
            {
                "code": normalize_code(code),
                "period": period,
                "count": count,
                "dividendType": dividend,
            },
        )

    def search(self, keyword: str, limit: int = 20) -> list[SearchItem]:
        """按代码 / 名称 / 拼音首字母搜索。服务端返回**裸数组**。"""
        data = self._request("/search", {"q": keyword, "limit": limit})
        if not isinstance(data, list):
            raise ApiFormatError("/search 的 data 应为数组")
        return [SearchItem.from_json(item) for item in data if isinstance(item, dict)]

    def stocks(
        self,
        offset: int = 0,
        limit: int = 100,
        type: str | None = None,
        exchange: str | None = None,
        keyword: str | None = None,
    ) -> dict:
        """股票列表分页。返回服务端原始 dict（`{total, offset, limit, count, stocks[]}`）。"""
        return self._request(
            "/stocks",
            {
                "offset": offset,
                "limit": limit,
                "type": type,
                "exchange": exchange,
                "q": keyword,
            },
        )

    def stocks_of(self, data: dict) -> list[StockItem]:
        return [
            StockItem.from_json(item)
            for item in (data or {}).get("stocks") or []
            if isinstance(item, dict)
        ]

    def bars_of(self, data: dict) -> list[Bar]:
        return [
            Bar.from_json(item)
            for item in (data or {}).get("bars") or []
            if isinstance(item, dict)
        ]

    # ---- 内部 ----

    def _request(self, path: str, params: dict | None = None) -> Any:
        """发一次 GET 并拆掉 `{code,msg,data}` 信封，返回 `data`。"""
        url = self.api_base + path
        query = {
            key: value
            for key, value in (params or {}).items()
            if value is not None and value != ""
        }
        if query:
            url += "?" + urllib.parse.urlencode(query)

        request = urllib.request.Request(url, method="GET")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", f"mooquant-cli/{__version__}")
        if self.token:
            request.add_header("X-Api-Token", self.token)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status, headers, body = response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            # 非 2xx 也带完整信封，必须把 body 读出来
            status, headers, body = exc.code, exc.headers, exc.read()
        except urllib.error.URLError as exc:
            raise TransportError(self._unreachable(exc.reason)) from exc
        except TimeoutError as exc:
            raise TransportError(
                f"请求超时（{self.timeout}s）：{url}。桌面端可能正忙或网络不通。"
            ) from exc
        except OSError as exc:
            raise TransportError(self._unreachable(exc)) from exc

        self._remember(headers)

        try:
            envelope = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiFormatError(
                f"响应不是合法 JSON（HTTP {status}）：{url}"
            ) from exc
        if not isinstance(envelope, dict) or "code" not in envelope:
            raise ApiFormatError(f"响应缺少 {{code,msg,data}} 信封（HTTP {status}）：{url}")

        try:
            code = int(envelope.get("code"))
        except (TypeError, ValueError) as exc:
            raise ApiFormatError(f"响应的 code 不是整数：{envelope.get('code')!r}") from exc

        if code != 0:
            raise ApiError(code, str(envelope.get("msg") or ""), status)
        return envelope.get("data")

    def _remember(self, headers: Any) -> None:
        source = headers.get("X-Mooquant-Source") if headers else None
        version = headers.get("X-Mooquant-Version") if headers else None
        if source:
            self.last_source = source
        if version:
            self.last_version = version

    def _unreachable(self, reason: Any) -> str:
        hint = "确认桌面端已启动，且设置里已开启本地服务。"
        return f"连不上 {self.api_base}：{reason}。{hint}"
