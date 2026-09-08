"""
xtquant QMT/MiniQMT Level2 行情权限集成测试。

运行方式：
    uv run pytest tests/test_xtdata_qmt_level2_permission.py -q -s

运行前准备：
    1. 启动并登录 QMT/MiniQMT。
    2. 确认 QMT 本机 xtquant 服务可连接。

可选环境变量：
    XT_QMT_LEVEL2_TEST_CODE=000001.SZ
    XT_QMT_LEVEL2_TEST_TIMEOUT=120
    XT_QMT_TEST_IP=127.0.0.1
    XT_QMT_TEST_PORT=58610

说明：
    本测试会在子进程中导入真实 xtquant，避免被 tests/conftest.py 的 xtquant mock 影响。
    测试通过表示通过 QMT/MiniQMT 连接拿到了 Level2 数据；测试失败会打印连接和接口诊断信息。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv 是项目依赖，这里只是兜底
    load_dotenv = None


@pytest.mark.integration
def test_xtdata_qmt_level2_permission():
    """连接本机 QMT/MiniQMT，并检测 Level2 行情接口是否能返回数据。"""
    if load_dotenv is not None:
        load_dotenv(".env")

    env = os.environ.copy()
    timeout = int(env.get("XT_QMT_LEVEL2_TEST_TIMEOUT", "120"))

    script = textwrap.dedent(
        r'''
        import json
        import os
        import sys
        import time
        import traceback


        def emit(payload, exit_code):
            print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
            sys.stdout.flush()
            os._exit(exit_code)


        def fail(message, **extra):
            emit({"ok": False, "message": message, **extra}, 1)


        def object_summary(obj):
            summary = {"type": type(obj).__name__, "repr": repr(obj)[:1000]}
            for attr in ("shape", "dtype"):
                if hasattr(obj, attr):
                    try:
                        summary[attr] = repr(getattr(obj, attr))
                    except Exception:
                        pass
            if hasattr(obj, "__len__"):
                try:
                    summary["len"] = len(obj)
                except Exception:
                    pass
            if hasattr(obj, "empty"):
                try:
                    summary["empty"] = bool(obj.empty)
                except Exception:
                    pass
            if isinstance(obj, dict):
                summary["keys"] = [str(k) for k in list(obj.keys())[:20]]
            return summary


        def has_non_empty_data(obj):
            if obj is None:
                return False
            if hasattr(obj, "empty"):
                try:
                    return not bool(obj.empty)
                except Exception:
                    pass
            if hasattr(obj, "shape"):
                try:
                    return int(obj.shape[0]) > 0
                except Exception:
                    pass
            if hasattr(obj, "__len__"):
                try:
                    return len(obj) > 0
                except Exception:
                    pass
            return False


        def call_api(name, caller):
            try:
                value = caller()
                return {
                    "available": has_non_empty_data(value),
                    "summary": object_summary(value),
                }
            except Exception as exc:
                return {"available": False, "error": repr(exc)}


        def decode_server_tag(client):
            try:
                raw = client.get_server_tag()
            except Exception as exc:
                return {"error": repr(exc)}
            try:
                from xtquant import xtbson
                return xtbson.BSON.decode(raw)
            except Exception:
                try:
                    from xtquant import xtbson
                    return xtbson.decode(raw)
                except Exception as exc:
                    return {"raw_repr": repr(raw), "decode_error": repr(exc)}


        code = os.environ.get("XT_QMT_LEVEL2_TEST_CODE", os.environ.get("XT_LEVEL2_TEST_CODE", "000001.SZ"))
        connect_ip = os.environ.get("XT_QMT_TEST_IP", "")
        connect_port = os.environ.get("XT_QMT_TEST_PORT", "").strip()

        try:
            from xtquant import xtdata

            # QMT 方案：不初始化 xtdatacenter，不设置 XT_TOKEN，直接连接本机 QMT/MiniQMT xtdata 服务。
            print("xtdata.connect(QMT) start", flush=True)
            if connect_port:
                client = xtdata.connect(connect_ip, int(connect_port))
            else:
                client = xtdata.connect()
            print("xtdata.connect(QMT) done", flush=True)

            connection = {
                "peer_addr": None,
                "data_dir": None,
                "server_tag": decode_server_tag(client),
            }
            for key, method_name in (("peer_addr", "get_peer_addr"), ("data_dir", "get_data_dir")):
                method = getattr(client, method_name, None)
                if callable(method):
                    try:
                        connection[key] = method()
                    except Exception as exc:
                        connection[key] = {"error": repr(exc)}

            # L1 作为连接健康检查；Level2 用三个接口分别探测。
            l1_result = call_api("get_full_tick", lambda: xtdata.get_full_tick([code]))
            l2_results = {
                "get_l2_quote": call_api("get_l2_quote", lambda: xtdata.get_l2_quote(stock_code=code, count=1)),
                "get_l2_order": call_api("get_l2_order", lambda: xtdata.get_l2_order(stock_code=code, count=1)),
                "get_l2_transaction": call_api("get_l2_transaction", lambda: xtdata.get_l2_transaction(stock_code=code, count=1)),
            }

            # 部分 QMT/xtquant 版本连接后需要一点时间同步实时数据。
            if not any(item.get("available") for item in l2_results.values()):
                deadline = time.time() + float(os.environ.get("XT_QMT_LEVEL2_WARMUP_TIMEOUT", "10"))
                while time.time() < deadline:
                    time.sleep(1)
                    l2_results = {
                        "get_l2_quote": call_api("get_l2_quote", lambda: xtdata.get_l2_quote(stock_code=code, count=1)),
                        "get_l2_order": call_api("get_l2_order", lambda: xtdata.get_l2_order(stock_code=code, count=1)),
                        "get_l2_transaction": call_api("get_l2_transaction", lambda: xtdata.get_l2_transaction(stock_code=code, count=1)),
                    }
                    if any(item.get("available") for item in l2_results.values()):
                        break

            l2_available = [name for name, item in l2_results.items() if item.get("available")]
            diagnostics = {
                "code": code,
                "connection": connection,
                "l1_result": l1_result,
                "l2_available": l2_available,
                "l2_results": l2_results,
            }
            print("qmt_level2_diagnostics=" + json.dumps(diagnostics, ensure_ascii=False, default=str), flush=True)

            if not l2_available:
                fail(
                    "通过 QMT/MiniQMT 未拿到 Level2 数据；请确认 QMT 已登录、已购买 Level2，且当前时段/本地缓存有可查询数据",
                    **diagnostics,
                )

            emit({"ok": True, "message": "通过 QMT/MiniQMT 拿到了 Level2 数据", **diagnostics}, 0)
        except SystemExit:
            raise
        except Exception as exc:
            fail("QMT Level2 测试异常", error=repr(exc), traceback=traceback.format_exc())
        finally:
            try:
                from xtquant import xtdata
                disconnect = getattr(xtdata, "disconnect", None)
                if callable(disconnect):
                    disconnect()
            except Exception:
                pass
        '''
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout
    assert '"ok": true' in completed.stdout, completed.stdout
