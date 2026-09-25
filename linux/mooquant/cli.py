"""命令行入口：参数解析、子命令分发、退出码。

分层约定：这里只做「解析参数 → 调 API → 交给 render 渲染」，不写格式化细节，
也不在 `main` 层拼 URL。

服务地址的优先级：**命令行参数 > 环境变量 > 配置文件 > 自动发现**。
走到「自动发现」时会把结果写回配置文件，之后免配（与 Android 端一致）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__, discovery
from .api import DEFAULT_TIMEOUT, DIVIDEND_TYPES, PERIODS, ApiClient
from .config import COLOR_SCHEMES, GREEN_UP, RED_UP, Config
from .errors import (
    ApiError,
    ConfigError,
    DiscoveryError,
    MooquantError,
    NotFoundError,
    TransportError,
)
from .models import normalize_code
from .render import (
    Palette,
    color_enabled,
    config_block,
    discover_block,
    emit_json,
    endpoints_block,
    history_block,
    quote_detail,
    quotes_table,
    search_table,
    status_block,
    stocks_block,
)

EXIT_OK = 0
EXIT_ERROR = 1  # 服务端 code != 0，或本地可预期失败
EXIT_USAGE = 2  # 用法错误（argparse 也用它）
EXIT_UNREACHABLE = 3  # 连不上服务端 / 发现失败

# 归到「够不着服务端」一类的异常
_UNREACHABLE = (DiscoveryError, TransportError, NotFoundError)

_COLOR_NAMES = {RED_UP: "红涨绿跌", GREEN_UP: "绿涨红跌"}


# ---------------------------------------------------------------- 入口


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "func", None) is None:
        parser.print_help()
        return EXIT_USAGE

    try:
        return args.func(args, Config())
    except MooquantError as exc:
        print(f"mooquant: {exc}", file=sys.stderr)
        return EXIT_UNREACHABLE if isinstance(exc, _UNREACHABLE) else EXIT_ERROR
    except KeyboardInterrupt:
        print("mooquant: 已中断", file=sys.stderr)
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mooquant",
        description="mookquant 桌面端只读 HTTP 服务的命令行客户端（局域网）。",
        epilog="例：mooquant discover && mooquant status && mooquant quotes sh600519,sz000001",
    )
    parser.add_argument(
        "--version", action="version", version=f"mooquant {__version__}"
    )
    _add_common(parser)
    # 写在子命令之前的全局参数靠 SUPPRESS 默认值保留，详见 _add_common
    parser.set_defaults(func=None)

    sub = parser.add_subparsers(dest="command", metavar="<命令>")

    # ---- 服务 ----
    _sub(sub, "endpoints", "列出服务端全部接口（由服务端自描述）", _cmd_endpoints)
    status = _sub(sub, "status", "服务与数据源状态", _cmd_status)
    status.add_argument(
        "--no-probe", action="store_true", help="只查桥进程存活，不做真实探测"
    )

    # ---- 行情 ----
    quote = _sub(sub, "quote", "单只行情快照", _cmd_quote)
    quote.add_argument("code", help="标的代码，如 sh600519")

    quotes = _sub(sub, "quotes", "批量行情（>50 只自动切片）", _cmd_quotes)
    quotes.add_argument(
        "codes",
        nargs="+",
        metavar="CODE[,CODE…]",
        help="一个或多个代码，逗号或空格分隔",
    )

    history = _sub(sub, "history", "K 线历史", _cmd_history)
    history.add_argument("code", help="标的代码，如 sh600519")
    history.add_argument(
        "-p", "--period", default="1d", choices=PERIODS, help="周期，默认 1d"
    )
    history.add_argument(
        "-n", "--count", type=int, default=60, help="根数，默认 60（服务端上限 5000）"
    )
    history.add_argument(
        "--dividend",
        default="front",
        choices=DIVIDEND_TYPES,
        help="复权方式，默认 front（前复权）",
    )

    search = _sub(sub, "search", "按代码 / 名称 / 拼音首字母搜索", _cmd_search)
    search.add_argument("keyword", metavar="KEYWORD", help="如 600519 / 茅台 / gzmt")
    search.add_argument(
        "-n", "--limit", type=int, default=20, help="条数，默认 20（服务端上限 50）"
    )

    stocks = _sub(sub, "stocks", "股票列表（分页）", _cmd_stocks)
    stocks.add_argument("--offset", type=int, default=0, help="起始偏移，默认 0")
    stocks.add_argument("--limit", type=int, default=100, help="条数，默认 100（上限 1000）")
    stocks.add_argument("--type", help="标的类型过滤")
    stocks.add_argument("--exchange", help="交易所过滤：SH / SZ / BJ")
    stocks.add_argument("-q", "--query", help="代码或名称模糊过滤")

    # ---- 发现与本地 ----
    disc = _sub(sub, "discover", "局域网发现服务端", _cmd_discover)
    disc.add_argument(
        "--probe", type=float, default=3.0, help="主动探测等待秒数，默认 3（0 表示不探测）"
    )
    disc.add_argument(
        "--listen", type=float, default=6.0, help="被动监听秒数，默认 6（0 表示不监听）"
    )

    config = _sub(sub, "config", "查看 / 修改本地配置", _cmd_config)
    config_sub = config.add_subparsers(
        dest="config_action", metavar="{show,set-url,set-token,set-color}"
    )
    _nested(config_sub, "show", "显示当前配置（默认动作）")
    set_url = _nested(config_sub, "set-url", "设置服务地址")
    set_url.add_argument("value", metavar="URL", help="如 http://192.168.1.5:12678/mooquant/api")
    set_token = _nested(config_sub, "set-token", "设置鉴权 token（传空串即清空）")
    set_token.add_argument("value", metavar="TOKEN")
    set_color = _nested(config_sub, "set-color", "设置涨跌配色")
    set_color.add_argument(
        "value",
        type=_color_scheme,
        choices=COLOR_SCHEMES,
        metavar="{red-up,green-up}",
    )

    watch = _sub(sub, "watch", "自选股：不带子命令即拉行情", _cmd_watch)
    watch_sub = watch.add_subparsers(
        dest="watch_action", metavar="{list,add,remove,reset}"
    )
    _nested(watch_sub, "list", "列出自选代码（不拉行情）")
    watch_add = _nested(watch_sub, "add", "加入自选")
    watch_add.add_argument("codes", nargs="+", metavar="CODE[,CODE…]")
    watch_rm = _nested(watch_sub, "remove", "移出自选")
    watch_rm.add_argument("codes", nargs="+", metavar="CODE[,CODE…]")
    _nested(watch_sub, "reset", "恢复默认自选")

    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    """面向服务端的全局参数（顶层子命令用）。

    默认值用 `argparse.SUPPRESS`：这样写在子命令**之前**的 `--json` 等不会被
    子命令解析器的默认值覆盖掉（argparse 的经典坑），取值一律走 `_opt()`。
    """
    _add_output(parser)
    parser.add_argument(
        "--url", metavar="URL", default=argparse.SUPPRESS, help="服务地址，覆盖配置文件"
    )
    parser.add_argument(
        "--token", metavar="TOKEN", default=argparse.SUPPRESS, help="鉴权 token，覆盖配置文件"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        metavar="SEC",
        default=argparse.SUPPRESS,
        help=f"单次 HTTP 超时秒数，默认 {DEFAULT_TIMEOUT:g}",
    )
    parser.add_argument(
        "--no-discover",
        action="store_true",
        default=argparse.SUPPRESS,
        help="未配置地址时不要自动发现，直接报错",
    )


def _add_output(parser: argparse.ArgumentParser) -> None:
    """只影响输出的参数。嵌套子命令（`config set-url`、`watch add`…）只用这几个，
    免得 `--url` 之类出现在本就没有服务端调用的位置。"""
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="输出服务端原始 JSON（data 字段），便于脚本消费",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="关闭配色（管道 / 重定向时本就会自动关闭）",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="把诊断信息打到 stderr",
    )


def _sub(subparsers, name: str, help_text: str, handler):
    parser = subparsers.add_parser(name, help=help_text, description=help_text)
    _add_common(parser)
    parser.set_defaults(func=handler)
    return parser


def _nested(subparsers, name: str, help_text: str):
    parser = subparsers.add_parser(name, help=help_text)
    _add_output(parser)
    return parser


# ---------------------------------------------------------------- 公共


def _opt(args, name: str, default=None):
    """取全局参数。`_add_common` 用了 SUPPRESS，未提供时属性不存在。"""
    value = getattr(args, name, None)
    return default if value is None else value


def _json(args) -> bool:
    return bool(_opt(args, "json", False))


def _debug(args, message: str) -> None:
    if _opt(args, "verbose", False):
        print(f"mooquant: {message}", file=sys.stderr)


def _notice(message: str) -> None:
    print(f"mooquant: {message}", file=sys.stderr)


def _palette(args, cfg: Config) -> Palette:
    return Palette(
        enabled=color_enabled(sys.stdout, bool(_opt(args, "no_color", False))),
        green_up=cfg.color_scheme == GREEN_UP,
    )


def _color_scheme(value: str) -> str:
    """子命令都写连字符（`set-url`），配置文件里存的是下划线，两种都收。"""
    return value.strip().lower().replace("-", "_")


def _split_codes(chunks: list[str]) -> list[str]:
    """把 `sh600519,sz000001` 与 `sh600519 sz000001` 两种写法都摊平。"""
    codes: list[str] = []
    for chunk in chunks:
        for code in chunk.replace(",", " ").split():
            if code:
                codes.append(code)
    return codes


def _resolve(args, cfg: Config) -> tuple[str, str, str]:
    """确定服务地址与 token，返回 `(地址, token, 来源)`。"""
    api_base = _opt(args, "url") or os.environ.get("MOOQUANT_API_URL") or cfg.api_base
    token = _opt(args, "token") or os.environ.get("MOOQUANT_API_TOKEN") or cfg.token

    if _opt(args, "url"):
        return api_base, token, "命令行参数"
    if os.environ.get("MOOQUANT_API_URL"):
        return api_base, token, "环境变量"
    if cfg.api_base:
        return api_base, token, "配置文件"

    if _opt(args, "no_discover", False):
        raise NotFoundError(
            "尚未配置服务地址，且指定了 --no-discover。"
            "用 `mooquant config set-url <地址>` 配置。"
        )

    _notice("尚未配置服务地址，正在搜索局域网内的服务…")
    target, phase = discovery.discover()
    if target.token_required and not token:
        _notice(
            f"服务端 {target.api_base} 需要 token，当前未设置："
            "mooquant config set-token <token>"
        )
    # 首次发现即回填，之后免配
    cfg.set_api_base(target.api_base)
    cfg.save()
    _notice(f"{phase}命中 {target.api_base}，已记入 {cfg.path}")
    return target.api_base, token, f"自动发现（{phase}）"


def _client(args, cfg: Config) -> ApiClient:
    api_base, token, how = _resolve(args, cfg)
    _debug(args, f"服务地址 {api_base}（{how}）")
    if token:
        _debug(args, "已带上 token")
    return ApiClient(api_base, token, timeout=float(_opt(args, "timeout", DEFAULT_TIMEOUT)))


def _quote_json(quotes: list) -> list[dict]:
    """`--json` 时缺数据的代码也要有个交代，不能凭空消失。"""
    return [
        quote.raw if quote.raw else {"code": quote.code, "error": "服务端未返回该代码的行情"}
        for quote in quotes
    ]


# ---------------------------------------------------------------- 子命令


def _cmd_endpoints(args, cfg: Config) -> int:
    data = _client(args, cfg).endpoints()
    if _json(args):
        emit_json(data)
    else:
        print(endpoints_block(data))
    return EXIT_OK


def _cmd_status(args, cfg: Config) -> int:
    client = _client(args, cfg)
    data = client.status(probe=not bool(_opt(args, "no_probe", False)))
    if _json(args):
        emit_json(data)
    else:
        print(status_block(data, client.api_base, client.last_source, client.last_version))
    return EXIT_OK


def _cmd_quote(args, cfg: Config) -> int:
    quote = _client(args, cfg).quote(args.code)
    if _json(args):
        emit_json(_quote_json([quote])[0])
    else:
        print(quote_detail(quote, _palette(args, cfg)))
    return EXIT_OK


def _cmd_quotes(args, cfg: Config) -> int:
    quotes = _client(args, cfg).quotes(_split_codes(args.codes))
    if _json(args):
        emit_json(_quote_json(quotes))
    else:
        print(quotes_table(quotes, _palette(args, cfg)))
    return EXIT_OK


def _cmd_history(args, cfg: Config) -> int:
    client = _client(args, cfg)
    data = client.history(
        args.code, period=args.period, count=args.count, dividend=args.dividend
    )
    if _json(args):
        emit_json(data)
    else:
        print(history_block(data, client.bars_of(data), _palette(args, cfg)))
    return EXIT_OK


def _cmd_search(args, cfg: Config) -> int:
    items = _client(args, cfg).search(args.keyword, limit=args.limit)
    if _json(args):
        emit_json([item.raw for item in items])
        return EXIT_OK
    if not items:
        print(f"没有匹配「{args.keyword}」的标的")
        return EXIT_OK
    print(search_table(items))
    return EXIT_OK


def _cmd_stocks(args, cfg: Config) -> int:
    client = _client(args, cfg)
    data = client.stocks(
        offset=args.offset,
        limit=args.limit,
        type=args.type,
        exchange=args.exchange,
        keyword=args.query,
    )
    if _json(args):
        emit_json(data)
    else:
        print(stocks_block(data, client.stocks_of(data)))
    return EXIT_OK


def _cmd_discover(args, cfg: Config) -> int:
    if args.probe <= 0 and args.listen <= 0:
        print(
            "mooquant: --probe 与 --listen 不能同时为 0，至少保留一种发现方式",
            file=sys.stderr,
        )
        return EXIT_USAGE

    start = time.monotonic()
    target, phase = discovery.discover(args.probe, args.listen)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    if _json(args):
        emit_json(
            {
                "apiBase": target.api_base,
                "host": target.host,
                "hostname": target.hostname,
                "source": target.source,
                "version": target.version,
                "tokenRequired": target.token_required,
                "phase": phase,
                "elapsedMs": elapsed_ms,
            }
        )
    else:
        print(discover_block(target, elapsed_ms))
        _notice(f"{phase}命中")
    return EXIT_OK


def _cmd_config(args, cfg: Config) -> int:
    action = getattr(args, "config_action", None)

    if action in (None, "show"):
        if _json(args):
            emit_json(cfg.as_dict())
        else:
            print(config_block(cfg, cfg.path))
        return EXIT_OK

    if action == "set-url":
        cfg.set_api_base(args.value)
        cfg.save()
        print(f"服务地址已设为 {cfg.api_base}")
    elif action == "set-token":
        cfg.set_token(args.value)
        cfg.save()
        print("token 已设置" if cfg.token else "token 已清空")
    elif action == "set-color":
        cfg.set_color_scheme(args.value)
        cfg.save()
        print(f"涨跌配色已设为 {_COLOR_NAMES[cfg.color_scheme]}")
    return EXIT_OK


def _cmd_watch(args, cfg: Config) -> int:
    action = getattr(args, "watch_action", None)

    if action == "reset":
        cfg.reset_watchlist()
        cfg.save()
        print(f"已恢复默认自选：{', '.join(cfg.watchlist)}")
        return EXIT_OK

    if action in ("add", "remove"):
        # 先归一，否则 reported 的「已存在」集合会和输入的大小写对不上
        codes = [normalize_code(c) for c in _split_codes(args.codes)]
        codes = [c for c in codes if c]
        mutate = cfg.add_watch if action == "add" else cfg.remove_watch
        changed = [c for c in codes if mutate(c)]
        unchanged = [c for c in codes if c not in changed]

        if action == "add":
            summary = f"已加入 {', '.join(changed)}" if changed else "没有新增"
            if unchanged:
                summary += f"；已在自选中：{', '.join(unchanged)}"
        else:
            summary = f"已移出 {', '.join(changed)}" if changed else "没有移出"
            if unchanged:
                summary += f"；本就不在自选中：{', '.join(unchanged)}"

        if changed:
            cfg.save()
        print(f"{summary}（当前 {len(cfg.watchlist)} 只）")
        return EXIT_OK

    if action == "list":
        if _json(args):
            emit_json({"watchlist": cfg.watchlist})
        else:
            print("\n".join(cfg.watchlist) or "（空）")
        return EXIT_OK

    # 不带动词 = 直接拉自选行情
    codes = cfg.watchlist
    if not codes:
        print("自选为空。用 `mooquant watch add sh600519` 添加。")
        return EXIT_OK

    quotes = _client(args, cfg).quotes(codes)
    if _json(args):
        emit_json(_quote_json(quotes))
    else:
        print(quotes_table(quotes, _palette(args, cfg)))
    return EXIT_OK
