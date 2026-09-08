"""Token 行情运行时单元测试。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from xqshare.market_data import (
    DEFAULT_VIP_QUOTE_SERVERS,
    MarketDataInitializationError,
    MarketDataRuntime,
)


def make_xt_modules(listen_result=("0.0.0.0", 58623)):
    xtdc = SimpleNamespace(
        set_token=Mock(),
        set_data_home_dir=Mock(),
        set_allow_optmize_address=Mock(),
        set_kline_mirror_enabled=Mock(),
        set_kline_mirror_markets=Mock(),
        set_init_markets=Mock(),
        init=Mock(),
        listen=Mock(return_value=listen_result),
    )
    xtdata = SimpleNamespace(connect=Mock())
    return xtdc, xtdata


def test_token_mode_initializes_xtdatacenter_without_qmt(tmp_path):
    xtdc, xtdata = make_xt_modules()
    environ = {
        "XQSHARE_MARKET_MODE": "token",
        "XT_TOKEN": "secret-token",
        "XT_DATA_HOME": str(tmp_path / "data"),
        "XT_INIT_MARKETS": "SH,SZ",
        "XT_KLINE_MIRROR_MARKETS": "SH;SZ",
        "XTDC_PORT_START": "58620",
        "XTDC_PORT_END": "58650",
    }

    runtime = MarketDataRuntime(xtdc, xtdata, environ=environ)
    status = runtime.initialize()

    xtdc.set_token.assert_called_once_with("secret-token")
    xtdc.set_data_home_dir.assert_called_once_with(str(tmp_path / "data"))
    xtdc.set_allow_optmize_address.assert_called_once_with(list(DEFAULT_VIP_QUOTE_SERVERS))
    xtdc.set_kline_mirror_enabled.assert_called_once_with(True)
    xtdc.set_kline_mirror_markets.assert_called_once_with(["SH", "SZ"])
    xtdc.set_init_markets.assert_called_once_with(["SH", "SZ"])
    xtdc.init.assert_called_once_with(start_local_service=False)
    xtdc.listen.assert_called_once_with(port=(58620, 58650))
    xtdata.connect.assert_called_once_with(port=58623)

    assert status["ready"] is True
    assert status["mode"] == "token"
    assert status["qmt_required"] is False
    assert status["listen_port"] == 58623
    assert status["kline_mirror_enabled"] is True
    assert "secret-token" not in repr(status)


def test_token_mode_is_initialized_only_once(tmp_path):
    xtdc, xtdata = make_xt_modules(58624)
    runtime = MarketDataRuntime(
        xtdc,
        xtdata,
        environ={
            "XT_TOKEN": "secret-token",
            "XT_DATA_HOME": str(tmp_path),
            "XTDC_PORT_START": "58624",
            "XTDC_PORT_END": "58624",
        },
    )

    first = runtime.initialize()
    second = runtime.initialize()

    assert first == second
    xtdc.init.assert_called_once()
    xtdc.listen.assert_called_once_with(port=58624)
    xtdata.connect.assert_called_once_with(port=58624)


def test_qmt_compatibility_mode_does_not_start_xtdatacenter():
    xtdc, xtdata = make_xt_modules()
    runtime = MarketDataRuntime(
        xtdc,
        xtdata,
        environ={"XQSHARE_MARKET_MODE": "qmt"},
    )

    status = runtime.initialize()

    assert status["ready"] is True
    assert status["mode"] == "qmt"
    assert status["qmt_required"] is True
    xtdc.init.assert_not_called()
    xtdc.listen.assert_not_called()
    xtdata.connect.assert_not_called()


def test_token_mode_requires_token():
    xtdc, xtdata = make_xt_modules()
    runtime = MarketDataRuntime(xtdc, xtdata, environ={})

    with pytest.raises(MarketDataInitializationError, match="XT_TOKEN"):
        runtime.initialize()


def test_token_mode_requires_xtdatacenter():
    _, xtdata = make_xt_modules()
    runtime = MarketDataRuntime(None, xtdata, environ={"XT_TOKEN": "secret-token"})

    with pytest.raises(MarketDataInitializationError, match="xtdatacenter"):
        runtime.initialize()


def test_invalid_market_mode_is_rejected():
    xtdc, xtdata = make_xt_modules()
    runtime = MarketDataRuntime(
        xtdc,
        xtdata,
        environ={"XQSHARE_MARKET_MODE": "invalid"},
    )

    with pytest.raises(MarketDataInitializationError, match="token 或 qmt"):
        runtime.initialize()


def test_old_xtdatacenter_init_signature_is_supported(tmp_path):
    xtdc, xtdata = make_xt_modules()
    xtdc.init.side_effect = [TypeError("unexpected keyword"), None]
    runtime = MarketDataRuntime(
        xtdc,
        xtdata,
        environ={
            "XT_TOKEN": "secret-token",
            "XT_DATA_HOME": str(tmp_path),
        },
    )

    status = runtime.initialize()

    assert status["ready"] is True
    assert xtdc.init.call_count == 2
    assert xtdc.init.call_args_list[1].args == ()
    assert xtdc.init.call_args_list[1].kwargs == {}
