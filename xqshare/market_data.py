"""xqshare 行情运行时。

Token 模式通过 ``xtquant.xtdatacenter`` 直接连接迅投行情服务，使
``xtdata`` 不再依赖已启动并登录的 QMT/MiniQMT 客户端。交易相关的
``XtQuantTrader`` 不在本模块处理，仍可按需连接 QMT。
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


DEFAULT_VIP_QUOTE_SERVERS: Tuple[str, ...] = (
    "115.231.218.73:55310",
    "115.231.218.79:55310",
    "42.228.16.211:55300",
    "42.228.16.210:55300",
    "36.99.48.20:55300",
    "36.99.48.21:55300",
)


class MarketDataInitializationError(RuntimeError):
    """行情运行时初始化失败。"""


def _split_list(value: Optional[str], default: Sequence[str]) -> List[str]:
    """解析逗号、分号或换行分隔的环境变量列表。"""
    if value is None:
        return list(default)

    normalized = value.replace(";", ",").replace("\n", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"无法解析布尔值: {value!r}")


def _normalize_listen_address(value: Any) -> Tuple[str, int]:
    """兼容不同 xtquant 版本的 ``xtdc.listen`` 返回类型。"""
    if isinstance(value, int):
        return "0.0.0.0", value

    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return str(value[0]), int(value[1])

    if isinstance(value, dict):
        port = value.get("port", value.get("listen_port"))
        if port is not None:
            return str(value.get("ip", value.get("host", "0.0.0.0"))), int(port)

    return "0.0.0.0", int(value)


class MarketDataRuntime:
    """初始化并保存 xqshare 服务端的行情连接状态。

    支持两种模式：

    ``token``
        使用 xtdatacenter + XT_TOKEN 直连迅投行情，不依赖 QMT。

    ``qmt``
        保留旧行为，由 xtdata 自行连接 QMT/MiniQMT。该模式主要用于兼容。
    """

    def __init__(
        self,
        xtdc_module: Any,
        xtdata_module: Any,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._xtdc = xtdc_module
        self._xtdata = xtdata_module
        self._environ = environ if environ is not None else os.environ
        self._lock = threading.Lock()
        self._initialized = False
        self._mode: Optional[str] = None
        self._status: Dict[str, Any] = {
            "initialized": False,
            "ready": False,
            "mode": None,
        }

    def initialize(self, mode: Optional[str] = None) -> Dict[str, Any]:
        """根据环境变量初始化行情运行时，并返回不含 Token 的状态信息。"""
        requested_mode = (mode or self._environ.get("XQSHARE_MARKET_MODE", "token")).strip().lower()
        if requested_mode not in {"token", "qmt"}:
            raise MarketDataInitializationError(
                "XQSHARE_MARKET_MODE 仅支持 token 或 qmt，"
                f"当前值为 {requested_mode!r}"
            )

        with self._lock:
            if self._initialized:
                if requested_mode != self._mode:
                    raise MarketDataInitializationError(
                        f"行情已经按 {self._mode} 模式初始化，不能切换到 {requested_mode}"
                    )
                return self.get_status()

            if requested_mode == "qmt":
                self._mode = "qmt"
                self._initialized = True
                self._status = {
                    "initialized": True,
                    "ready": True,
                    "mode": "qmt",
                    "qmt_required": True,
                    "kline_mirror_enabled": False,
                    "message": "xtdata 使用 QMT/MiniQMT 行情连接",
                }
                return self.get_status()

            return self._initialize_token_mode()

    def _initialize_token_mode(self) -> Dict[str, Any]:
        if self._xtdc is None:
            raise MarketDataInitializationError(
                "当前 xtquant 不包含 xtdatacenter，请升级到支持 Token 模式的版本"
            )
        if self._xtdata is None:
            raise MarketDataInitializationError("xtquant.xtdata 不可用")

        token = self._environ.get("XT_TOKEN", "").strip()
        if not token:
            raise MarketDataInitializationError(
                "Token 行情模式需要在 .env 中设置 XT_TOKEN"
            )

        data_home = os.path.abspath(
            os.path.expanduser(self._environ.get("XT_DATA_HOME", ".xtdata-cache"))
        )
        os.makedirs(data_home, exist_ok=True)

        vip_servers = _split_list(
            self._environ.get("XT_VIP_SERVERS"),
            DEFAULT_VIP_QUOTE_SERVERS,
        )
        init_markets = _split_list(
            self._environ.get("XT_INIT_MARKETS"),
            ("SH", "SZ"),
        )
        mirror_markets = _split_list(
            self._environ.get("XT_KLINE_MIRROR_MARKETS"),
            init_markets,
        )
        kline_mirror_enabled = _parse_bool(
            self._environ.get("XT_KLINE_MIRROR"),
            True,
        )

        port_start = int(self._environ.get("XTDC_PORT_START", "58620"))
        port_end = int(self._environ.get("XTDC_PORT_END", "58650"))
        if not (1 <= port_start <= 65535 and 1 <= port_end <= 65535):
            raise MarketDataInitializationError("XTDC 监听端口必须在 1~65535 范围内")
        if port_start > port_end:
            raise MarketDataInitializationError("XTDC_PORT_START 不能大于 XTDC_PORT_END")

        try:
            self._require_callable(self._xtdc, "set_token")(token)
            self._require_callable(self._xtdc, "set_data_home_dir")(data_home)

            set_addresses = getattr(self._xtdc, "set_allow_optmize_address", None)
            if vip_servers and callable(set_addresses):
                set_addresses(vip_servers)

            if kline_mirror_enabled:
                self._require_callable(self._xtdc, "set_kline_mirror_enabled")(True)
                set_mirror_markets = getattr(self._xtdc, "set_kline_mirror_markets", None)
                if mirror_markets and callable(set_mirror_markets):
                    set_mirror_markets(mirror_markets)

            set_init_markets = getattr(self._xtdc, "set_init_markets", None)
            if init_markets and callable(set_init_markets):
                set_init_markets(init_markets)

            init = self._require_callable(self._xtdc, "init")
            try:
                # 自己调用 listen/connect，避免 xtdata 寻找 QMT 默认端口。
                init(start_local_service=False)
            except TypeError:
                # 兼容不支持 start_local_service 参数的旧版 xtquant。
                init()

            listen = self._require_callable(self._xtdc, "listen")
            listen_arg: Any
            if port_start == port_end:
                listen_arg = port_start
            else:
                listen_arg = (port_start, port_end)
            listen_result = listen(port=listen_arg)
            listen_host, listen_port = _normalize_listen_address(listen_result)

            connect = self._require_callable(self._xtdata, "connect")
            connect(port=listen_port)
        except Exception as exc:
            self._status = {
                "initialized": False,
                "ready": False,
                "mode": "token",
                "error": str(exc),
            }
            raise MarketDataInitializationError(
                f"Token 行情初始化失败: {exc}"
            ) from exc

        self._mode = "token"
        self._initialized = True
        self._status = {
            "initialized": True,
            "ready": True,
            "mode": "token",
            "qmt_required": False,
            "data_home": data_home,
            "listen_host": listen_host,
            "listen_port": listen_port,
            "vip_servers": vip_servers,
            "init_markets": init_markets,
            "kline_mirror_enabled": kline_mirror_enabled,
            "kline_mirror_markets": mirror_markets if kline_mirror_enabled else [],
        }
        return self.get_status()

    @staticmethod
    def _require_callable(module: Any, name: str) -> Any:
        func = getattr(module, name, None)
        if not callable(func):
            raise MarketDataInitializationError(
                f"当前 xtquant 版本缺少必要接口: {name}"
            )
        return func

    def get_status(self) -> Dict[str, Any]:
        """返回可安全对外展示的行情状态，不包含 XT_TOKEN。"""
        return dict(self._status)
