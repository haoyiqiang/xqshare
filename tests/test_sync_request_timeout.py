"""同步 RPC 超时配置回归测试。"""

from unittest.mock import Mock, patch

from xqshare.client import XtQuantRemote


def _connection_mock():
    connection = Mock()
    connection.root.ping = Mock(return_value="pong")
    return connection


def test_configurable_sync_request_timeout():
    with patch("xqshare.client.rpyc.connect") as connect:
        connect.return_value = _connection_mock()
        client = XtQuantRemote(
            host="localhost",
            port=18812,
            client_secret="",
            auto_reconnect=False,
            heartbeat_interval=0,
            sync_request_timeout=7200,
        )

        assert connect.call_args.kwargs["config"]["sync_request_timeout"] == 7200
        client.close()


def test_sync_request_timeout_from_environment(monkeypatch):
    monkeypatch.setenv("XQSHARE_SYNC_REQUEST_TIMEOUT", "5400")
    with patch("xqshare.client.rpyc.connect") as connect:
        connect.return_value = _connection_mock()
        client = XtQuantRemote(
            host="localhost",
            port=18812,
            client_secret="",
            auto_reconnect=False,
            heartbeat_interval=0,
        )

        assert connect.call_args.kwargs["config"]["sync_request_timeout"] == 5400
        client.close()


def test_default_sync_request_timeout_is_one_hour(monkeypatch):
    monkeypatch.delenv("XQSHARE_SYNC_REQUEST_TIMEOUT", raising=False)
    with patch("xqshare.client.rpyc.connect") as connect:
        connect.return_value = _connection_mock()
        client = XtQuantRemote(
            host="localhost",
            port=18812,
            client_secret="",
            auto_reconnect=False,
            heartbeat_interval=0,
        )

        assert connect.call_args.kwargs["config"]["sync_request_timeout"] == 3600
        client.close()
