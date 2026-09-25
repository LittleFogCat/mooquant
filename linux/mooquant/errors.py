"""统一异常层级。

`cli.py` 只捕获 `MooquantError` 及其子类，并把它们映射成稳定的退出码；
未预期的异常则原样抛出（带栈），以免把程序 bug 伪装成「服务端坏了」。
"""


class MooquantError(Exception):
    """本工具所有预期内失败的基类。"""


class ConfigError(MooquantError):
    """配置文件读不懂、写不了，或取值非法。"""


class NotFoundError(MooquantError):
    """本地缺少必要前置条件，例如尚未配置服务地址。"""


class DiscoveryError(MooquantError):
    """局域网发现失败：无应答、超时，或应答报文不合法。"""


class TransportError(MooquantError):
    """到不了服务端：连接被拒、超时、DNS 失败。"""


class ApiError(MooquantError):
    """服务端返回了信封，但 code != 0。

    服务端在非 2xx 时同样返回完整信封（401/404/429），所以 `status` 只作辅助信息，
    真正的错误语义在 `code` 与 `msg` 上。
    """

    def __init__(self, code: int, msg: str, status: int | None = None) -> None:
        self.code = code
        self.msg = msg
        self.status = status
        super().__init__(f"服务端错误 [{code}] {msg}")


class ApiFormatError(MooquantError):
    """响应不是预期的 {code,msg,data} 信封（例如被中间的代理改写）。"""
