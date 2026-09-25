"""终端渲染层：表格、涨跌配色、JSON 输出。

这里是「视图」，`cli.py` 只负责解析参数、调 API、把结果交给这里的函数。

关键点：中文字符在终端占**两列**，用 `len()` 补空格必然错位。所有列宽统一走
`display_width()`，它会先剥掉 ANSI 转义序列再按东亚全角宽度累计。
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from typing import Iterable, Sequence
from urllib.parse import urlparse

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[90m"
_RED = "\033[31m"
_GREEN = "\033[32m"


# ---------------------------------------------------------------- 宽度与对齐


def display_width(text: str) -> int:
    """终端显示宽度：东亚全角/宽字符算 2 列，ANSI 转义不计入。"""
    plain = _ANSI_RE.sub("", text)
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in plain
    )


def pad(text: str, width: int, align: str = "<") -> str:
    """按显示宽度补齐到 `width` 列（超宽则原样返回）。"""
    fill = " " * max(0, width - display_width(text))
    return fill + text if align == ">" else text + fill


class Table:
    """定宽文本表格，列宽按显示宽度自动计算。"""

    def __init__(self, headers: Sequence[str], aligns: Sequence[str] | None = None):
        self._headers = list(headers)
        self._aligns = list(aligns) if aligns else ["<"] * len(self._headers)
        self._rows: list[list[str]] = []

    def add(self, *cells: object) -> None:
        self._rows.append(["" if c is None else str(c) for c in cells])

    def render(self) -> str:
        widths = [display_width(h) for h in self._headers]
        for row in self._rows:
            for index, cell in enumerate(row[: len(widths)]):
                widths[index] = max(widths[index], display_width(cell))

        lines = [self._format(self._headers, widths)]
        lines.append("  ".join("-" * w for w in widths))
        lines.extend(self._format(row, widths) for row in self._rows)
        return "\n".join(lines)

    def _format(self, cells: Sequence[str], widths: Sequence[int]) -> str:
        padded = [
            pad(cell, widths[index], self._aligns[index])
            for index, cell in enumerate(cells[: len(widths)])
        ]
        return "  ".join(padded).rstrip()


def key_value_block(pairs: Iterable[tuple[str, object]]) -> str:
    """两列「字段 / 值」块，键列按显示宽度对齐。"""
    items = [(key, "" if value is None else str(value)) for key, value in pairs]
    width = max((display_width(key) for key, _ in items), default=0)
    return "\n".join(f"{pad(key, width)}  {value}".rstrip() for key, value in items)


def human_amount(value: float, unit: str = "") -> str:
    """大数字按 万 / 亿 折算，便于一眼读数。"""
    if value >= 1e8:
        return f"{value / 1e8:.2f}亿{unit}"
    if value >= 1e4:
        return f"{value / 1e4:.2f}万{unit}"
    return f"{value:.0f}{unit}"


# ---------------------------------------------------------------- 配色


class Palette:
    """涨跌配色。默认红涨绿跌，`green_up` 时反转。"""

    def __init__(self, enabled: bool = True, green_up: bool = False) -> None:
        self.enabled = enabled
        self.green_up = green_up

    def style(self, text: str, change: float) -> str:
        """按涨跌给文本上色；零涨跌用暗色，避免被误读成方向。"""
        if not self.enabled:
            return text
        if change > 0:
            return (_GREEN if self.green_up else _RED) + text + _RESET
        if change < 0:
            return (_RED if self.green_up else _GREEN) + text + _RESET
        return _DIM + text + _RESET

    def bold(self, text: str) -> str:
        """加粗。关闭配色时原样返回 —— 否则管道输出里会漏出转义序列，坏掉 grep/awk。"""
        return _BOLD + text + _RESET if self.enabled else text


def color_enabled(stream, no_color: bool = False) -> bool:
    """是否上色。遵循 NO_COLOR 约定，且非 TTY（管道 / 重定向）时自动关闭。"""
    if no_color or os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


# ---------------------------------------------------------------- JSON


def emit_json(data: object) -> None:
    """把服务端的 `data` 原样输出，不重新包装 —— 方便脚本消费。"""
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


# ---------------------------------------------------------------- 各命令的视图


def status_block(data: dict, api_base: str, source: str, version: str) -> str:
    server = (data or {}).get("server") or {}
    mode = str((data or {}).get("mode") or "")
    description = str((data or {}).get("description") or "")
    connected = (data or {}).get("connected")
    error = (data or {}).get("error")

    if connected is True:
        state = "已连接"
    elif connected is False:
        state = f"未连接{f'（{error}）' if error else ''}"
    else:
        state = "未知"

    pairs = [
        ("服务地址", api_base),
        ("数据源", f"{source or mode} · {description}".rstrip(" ·")),
        ("连接状态", state),
        ("标的数量", (data or {}).get("stockCount")),
        ("服务版本", version or "-"),
        ("服务启动", server.get("startedAt")),
        ("已运行", _human_uptime(server.get("uptimeMs"))),
        ("监听地址", f"{server.get('host', '')}:{server.get('port', '')}"),
        ("请求计数", server.get("requestCount")),
    ]
    if (data or {}).get("reconnecting"):
        pairs.insert(3, ("重连中", "是"))
    return key_value_block(pairs)


def _human_uptime(milliseconds: object) -> str:
    try:
        total = int(milliseconds or 0) // 1000
    except (TypeError, ValueError):
        return "-"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分"


def endpoints_block(data: dict) -> str:
    """接口索引：服务端自描述，不硬编码文档。"""
    table = Table(["路径", "说明", "参数", "阶段"])
    for item in (data or {}).get("endpoints") or []:
        params: list[str] = []
        for param in item.get("params") or []:
            name = str(param.get("name") or "")
            params.append(f"{name}*" if param.get("required") else name)
        table.add(
            item.get("path", ""),
            item.get("desc", ""),
            ", ".join(params) or "-",
            item.get("stage", ""),
        )

    head = key_value_block(
        [
            ("版本", (data or {}).get("version", "")),
            ("数据源", (data or {}).get("source", "")),
            ("需要 token", "是" if (data or {}).get("tokenRequired") else "否"),
            ("局域网地址", ", ".join((data or {}).get("addrs") or []) or "-"),
        ]
    )
    return head + "\n\n" + table.render() + "\n\n（参数列带 * 为必填）"


def quotes_table(quotes: Sequence, palette: Palette) -> str:
    table = Table(
        ["代码", "名称", "最新价", "涨跌额", "涨跌幅"],
        aligns=["<", "<", ">", ">", ">"],
    )
    for quote in quotes:
        if quote.missing:
            table.add(quote.code, "取不到", "--", "--", "--")
            continue
        table.add(
            quote.code,
            quote.name,
            f"{quote.price:.2f}",
            palette.style(f"{quote.change:+.2f}", quote.change),
            palette.style(f"{quote.change_percent:+.2f}%", quote.change),
        )
    return table.render()


def quote_detail(quote, palette: Palette) -> str:
    """单只快照的详情块，比表格多给几个字段。"""
    if quote.missing:
        return f"{quote.code}  取不到行情（代码可能已失效，或数据源缺这条数据）"

    title = f"{palette.bold(quote.name)}  {quote.code}"
    if quote.industry:
        title += f"  ·  {quote.industry}"
    if quote.market_name:
        title += f"  ·  {quote.market_name}"

    delta = palette.style(
        f"{quote.change:+.2f}  {quote.change_percent:+.2f}%", quote.change
    )
    body = key_value_block(
        [
            ("最新价", f"{palette.bold(f'{quote.price:.2f}')}    {delta}"),
            ("昨收", f"{quote.prev_close:.2f}"),
            ("今开", f"{quote.open:.2f}"),
            ("最高", f"{quote.high:.2f}"),
            ("最低", f"{quote.low:.2f}"),
            ("成交量", human_amount(quote.volume, "股")),
            ("成交额", human_amount(quote.turnover, "元")),
            ("时间", quote.timestamp or "-"),
        ]
    )
    return title + "\n" + body


def search_table(items: Sequence) -> str:
    table = Table(["代码", "名称", "行业"])
    for item in items:
        table.add(item.code, item.name, item.industry or "-")
    return table.render()


def stocks_block(data: dict, items: Sequence) -> str:
    table = Table(
        ["代码", "名称", "交易所", "类型"],
        aligns=["<", "<", "<", "<"],
    )
    for item in items:
        table.add(item.code, item.name, item.exchange, item.type)

    shown_from = (data or {}).get("offset", 0)
    shown = shown_from + len(items)
    head = (
        f"共 {(data or {}).get('total', 0)} 只，"
        f"本次返回 {len(items)} 条（第 {shown_from}–{max(shown - 1, shown_from)} 条）"
    )
    return head + "\n\n" + table.render()


def history_block(data: dict, bars: Sequence, palette: Palette) -> str:
    head = (
        f"{(data or {}).get('code', '')}  周期 {(data or {}).get('period', '')}  "
        f"共 {len(bars)} 根"
    )
    if not bars:
        return head + "\n\n（该区间没有 K 线）"

    table = Table(
        ["时间", "开盘", "最高", "最低", "收盘", "成交量"],
        aligns=["<", ">", ">", ">", ">", ">"],
    )
    for bar in bars:
        table.add(
            bar.date or bar.time,
            f"{bar.open:.2f}",
            f"{bar.high:.2f}",
            f"{bar.low:.2f}",
            palette.style(f"{bar.close:.2f}", bar.close - bar.open),
            human_amount(bar.volume, ""),
        )
    return head + "\n\n" + table.render()


def discover_block(target, elapsed_ms: int | None = None) -> str:
    # 信标的 host 是服务端监听地址（常见 0.0.0.0），对用户没意义；
    # 真正可达的地址从 api_base 里取。
    reachable = urlparse(target.api_base).hostname or target.host
    host = f"{target.hostname}（{reachable}）" if target.hostname else reachable

    pairs = [
        ("服务地址", target.api_base),
        ("主机", host),
        ("数据源", target.source or "-"),
        ("版本", target.version or "-"),
        ("需要 token", "是" if target.token_required else "否"),
    ]
    if elapsed_ms is not None:
        pairs.append(("耗时", f"{elapsed_ms} ms"))
    return key_value_block(pairs)


def config_block(cfg, path) -> str:
    token = cfg.token
    token_text = f"已设置（{len(token)} 字符）" if token else "未设置"
    schemes = {"red_up": "红涨绿跌", "green_up": "绿涨红跌"}
    return key_value_block(
        [
            ("配置文件", path),
            ("服务地址", cfg.api_base or "未设置"),
            ("token", token_text),
            ("涨跌配色", schemes.get(cfg.color_scheme, cfg.color_scheme)),
            ("自选", ", ".join(cfg.watchlist) or "（空）"),
        ]
    )
