"""本地配置读写。

对应 Android 端 `Prefs`（SharedPreferences）的角色：记住服务地址、token、
涨跌配色与自选列表，让「发现一次、之后免配」成为默认体验。

文件位置遵循 XDG 规范，权限 0600 —— 里面可能存 token。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .models import normalize_code

# 涨跌配色。中国市场习惯默认红涨绿跌（与桌面端、Android 端一致）
RED_UP = "red_up"
GREEN_UP = "green_up"
COLOR_SCHEMES = (RED_UP, GREEN_UP)

# 服务端 `/quotes` 单请求上限，源自 config/default.json 的 maxBatchCodes
MAX_BATCH_CODES = 50

DEFAULT_WATCHLIST = ["sh600519", "sz000001", "sh601318"]

_DEFAULTS: dict[str, Any] = {
    "api_base": "",
    "token": "",
    "color_scheme": RED_UP,
    "watchlist": list(DEFAULT_WATCHLIST),
}


def default_path() -> Path:
    """配置文件位置。

    优先级：`$MOOQUANT_CONFIG` > `$XDG_CONFIG_HOME/mooquant/config.json` > `~/.config/mooquant/config.json`。
    """
    override = os.environ.get("MOOQUANT_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base).expanduser() / "mooquant" / "config.json"


class Config:
    """配置对象的全部改动都只落在内存，需显式 `save()` 才落盘。

    这样只读类命令（`status`、`quotes`…）绝不会意外写文件。
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_path()
        self._data = self._load()

    # ---- 读 ----

    @property
    def api_base(self) -> str:
        return self._data["api_base"]

    @property
    def token(self) -> str:
        return self._data["token"]

    @property
    def color_scheme(self) -> str:
        return self._data["color_scheme"]

    @property
    def watchlist(self) -> list[str]:
        return list(self._data["watchlist"])

    def as_dict(self) -> dict:
        return dict(self._data)

    # ---- 写 ----

    def set_api_base(self, value: str) -> None:
        # 末位斜杠会让后续拼接出 `//quotes`，这里顺手抹掉
        self._data["api_base"] = str(value or "").strip().rstrip("/")

    def set_token(self, value: str) -> None:
        self._data["token"] = str(value or "").strip()

    def set_color_scheme(self, value: str) -> None:
        if value not in COLOR_SCHEMES:
            raise ConfigError(
                f"未知配色 {value!r}，可选：{' / '.join(COLOR_SCHEMES)}"
            )
        self._data["color_scheme"] = value

    def add_watch(self, code: str) -> bool:
        """加入自选；已存在则返回 False。"""
        code = normalize_code(code)
        if not code:
            raise ConfigError("代码不能为空")
        if code in self._data["watchlist"]:
            return False
        self._data["watchlist"].append(code)
        return True

    def remove_watch(self, code: str) -> bool:
        """移出自选；本来就不在则返回 False。"""
        code = normalize_code(code)
        if code not in self._data["watchlist"]:
            return False
        self._data["watchlist"].remove(code)
        return True

    def reset_watchlist(self) -> None:
        self._data["watchlist"] = list(DEFAULT_WATCHLIST)

    # ---- 落盘 ----

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            # 里面可能有 token，不给同机其他用户可读
            os.chmod(self.path, 0o600)
        except OSError as exc:
            raise ConfigError(f"配置写入失败：{self.path}（{exc.strerror}）") from exc

    # ---- 内部 ----

    def _load(self) -> dict:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return dict(_DEFAULTS)
        except OSError as exc:
            raise ConfigError(f"配置读取失败：{self.path}（{exc.strerror}）") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"配置文件不是合法 JSON：{self.path}（{exc}）") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"配置文件应为 JSON 对象：{self.path}")

        return {
            "api_base": str(data.get("api_base") or "").strip().rstrip("/"),
            "token": str(data.get("token") or "").strip(),
            # 配色是装饰性的，手工改坏了就退回默认，不必让整个工具罢工
            "color_scheme": (
                data.get("color_scheme")
                if data.get("color_scheme") in COLOR_SCHEMES
                else RED_UP
            ),
            "watchlist": self._normalize_watchlist(data.get("watchlist")),
        }

    @staticmethod
    def _normalize_watchlist(value: Any) -> list[str]:
        if not isinstance(value, list):
            return list(DEFAULT_WATCHLIST)
        seen: list[str] = []
        for item in value:
            code = normalize_code(item)
            if code and code not in seen:
                seen.append(code)
        return seen
