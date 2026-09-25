"""局域网发现：配合桌面端的 UDP 广播信标（`main/server/discovery-beacon.js`）。

两阶段，与 Android 端策略一致：

1. **主动探测** —— 向 `255.255.255.255` 与每个网卡的**定向广播地址**发探测包。
   信标是**单播回探测包的源端口**的，所以这里用临时端口就够，不必常驻绑定 12679。
2. **被动监听** —— 绑定 12679 等一条周期广播。

绑定 12679 属于非特权端口，**不需要 root**。

报文里**永远没有 token 明文**，只有 `tokenRequired` 布尔。
"""

from __future__ import annotations

import errno
import json
import socket
import struct
import time

from .errors import DiscoveryError
from .models import ServerTarget

MAGIC = "MOOQUANT-DISCOVERY"
PROTOCOL_VERSION = 1
DEFAULT_PORT = 12679

MAX_PACKET = 4096
PROBE_PAYLOAD = json.dumps(
    {"magic": MAGIC, "v": PROTOCOL_VERSION, "probe": True}, separators=(",", ":")
).encode("utf-8")

DEFAULT_PROBE_TIMEOUT = 3.0
DEFAULT_LISTEN_TIMEOUT = 6.0

_BROADCAST_ALL = "255.255.255.255"

# Linux ioctl 请求码（net/if.h）
_SIOCGIFADDR = 0x8915
_SIOCGIFNETMASK = 0x891B


# ---------------------------------------------------------------- 对外接口


def discover(
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
    listen_timeout: float = DEFAULT_LISTEN_TIMEOUT,
) -> tuple[ServerTarget, str]:
    """先主动探测，失败再被动监听。

    返回 `(目标, 命中方式)`；两个时长都传 0 或都没命中则抛 `DiscoveryError`。
    """
    if probe_timeout > 0:
        target = probe(probe_timeout)
        if target is not None:
            return target, "主动探测"

    if listen_timeout > 0:
        target = listen(listen_timeout)
        if target is not None:
            return target, "被动监听"

    raise DiscoveryError(
        "没找到服务端。确认桌面端已启动、设置里已开启本地服务与发现信标，"
        "两台机器在同一局域网（Windows 防火墙需放行「专用网络」）；"
        "也可以直接指定地址：mooquant config set-url http://<主机>:12678/mooquant/api"
    )


def probe(timeout: float = DEFAULT_PROBE_TIMEOUT) -> ServerTarget | None:
    """主动探测：发广播探测包，等任意一个应答。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))

        for address in broadcast_targets():
            try:
                sock.sendto(PROBE_PAYLOAD, (address, DEFAULT_PORT))
            except OSError:
                # 某个网卡发不出去（已 down、无权限）不该拖垮其它网卡
                continue

        return _await_reply(sock, timeout)


def listen(timeout: float = DEFAULT_LISTEN_TIMEOUT) -> ServerTarget | None:
    """被动监听：绑定 12679 等周期广播。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", DEFAULT_PORT))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise DiscoveryError(
                    f"本机 {DEFAULT_PORT} 端口被占用，无法被动监听。"
                    "改用主动探测（不要加 --listen）即可。"
                ) from exc
            raise

        return _await_reply(sock, timeout)


def broadcast_targets() -> list[str]:
    """广播目标：全局广播 + 每个网卡的定向广播。"""
    targets = [_BROADCAST_ALL]
    for address in directed_broadcasts():
        if address not in targets:
            targets.append(address)
    return targets


def directed_broadcasts() -> list[str]:
    """按每个网卡的 IPv4 与掩码算出定向广播地址。

    `fcntl` 是 Unix 专有：Windows 上直接返回空列表，只剩全局广播 ——
    本次的目标平台是 Linux，Windows 走这条降级路径只为便于本机调试。
    """
    try:
        import fcntl
    except ImportError:
        return []

    results: list[str] = []
    try:
        interfaces = socket.if_nameindex()
    except OSError:
        return []

    for _, name in interfaces:
        address = _ioctl_addr(fcntl, name, _SIOCGIFADDR)
        mask = _ioctl_addr(fcntl, name, _SIOCGIFNETMASK)
        if address is None or mask is None or address.startswith("127."):
            continue
        ip_int = struct.unpack("!I", socket.inet_aton(address))[0]
        mask_int = struct.unpack("!I", socket.inet_aton(mask))[0]
        broadcast = (ip_int & mask_int) | (~mask_int & 0xFFFFFFFF)
        results.append(socket.inet_ntoa(struct.pack("!I", broadcast)))
    return results


# ---------------------------------------------------------------- 内部


def _ioctl_addr(fcntl, name: str, request: int) -> str | None:
    """取网卡的 IPv4 / 掩码。没有该地址（如 IPv6-only、已 down）时返回 None。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # ifreq 里 sockaddr_in 的 sin_addr 落在偏移 20..24
            packed = fcntl.ioctl(
                sock.fileno(), request, struct.pack("256s", name[:15].encode())
            )
        return socket.inet_ntoa(packed[20:24])
    except OSError:
        return None


def _await_reply(sock: socket.socket, timeout: float) -> ServerTarget | None:
    """在 socket 上等到一条合法信标，或超时。"""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        sock.settimeout(remaining)
        try:
            payload, _ = sock.recvfrom(MAX_PACKET)
        except TimeoutError:
            return None
        except OSError:
            return None
        target = parse_beacon(payload)
        if target is not None:
            return target


def parse_beacon(payload: bytes) -> ServerTarget | None:
    """校验并解析一条信标报文；不是本协议的一律返回 None。"""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("magic") != MAGIC:
        return None
    return ServerTarget.from_beacon(data)
