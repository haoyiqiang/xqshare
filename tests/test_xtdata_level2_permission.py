"""
xtquant Token 模式 Level2 行情权限集成测试。

运行方式：
    uv run pytest tests/test_xtdata_level2_permission.py -q -s

运行前准备：
    1. 安装支持 xtdatacenter 的 xtquant 版本。
    2. 在 .env 中设置 XT_TOKEN。

可选环境变量：
    XT_LEVEL2_TEST_CODE=000001.SZ
    XT_LEVEL2_TEST_MARKETS=SH;SZ
    XT_LEVEL2_TEST_TIMEOUT=180
    XT_DATA_HOME=.xtdata-cache

说明：
    本测试会在子进程中导入真实 xtquant，避免被 tests/conftest.py 的 xtquant mock 影响。
    测试通过表示检测到指定市场 Level2 权限；测试失败通常表示当前 Token 没有这些市场的 Level2 权限。
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


VIP_QUOTE_SERVERS = [
    "115.231.218.73:55310",
    "115.231.218.79:55310",
    "42.228.16.211:55300",
    "42.228.16.210:55300",
    "36.99.48.20:55300",
    "36.99.48.21:55300",
]

@pytest.mark.integration
def test_xtdata_token_level2_permission():
    """使用 XT_TOKEN 直连行情中心，并检测指定市场是否具备 Level2 行情权限。"""
    if load_dotenv is not None:
        load_dotenv(".env")

    token = os.environ.get("XT_TOKEN")
    if not token:
        pytest.skip("未设置 XT_TOKEN，跳过 xtquant Level2 权限真实连接测试")

    env = os.environ.copy()
    env["XT_VIP_SERVERS"] = ";".join(VIP_QUOTE_SERVERS)

    if not env.get("XT_LEVEL2_TEST_MARKETS"):
        # 默认检查沪深两市股票 Level2；如只购买单市场，可用 XT_LEVEL2_TEST_MARKETS 覆盖。
        env["XT_LEVEL2_TEST_MARKETS"] = "SH;SZ"
    timeout = int(env.get("XT_LEVEL2_TEST_TIMEOUT", env.get("XT_VIP_TEST_TIMEOUT", "180")))

    script = textwrap.dedent(
        r'''
        import json
        import os
        import pathlib
        import sys
        import tempfile
        import time
        import traceback


        def emit(payload, exit_code):
            print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
            sys.stdout.flush()
            os._exit(exit_code)


        def fail(message, **extra):
            emit({"ok": False, "message": message, **extra}, 1)


        def normalize_list(value):
            if value is None:
                return []
            if isinstance(value, str):
                return [item.strip().upper() for item in value.replace(",", ";").split(";") if item.strip()]
            return [str(item).strip().upper() for item in value if str(item).strip()]


        def decode_bson(value, bson_module):
            if value is None:
                return None
            if isinstance(value, (bytes, bytearray)):
                return bson_module.decode(value)
            return value


        def simplify_init_result(info):
            if not isinstance(info, dict):
                return {"repr": repr(info)}
            boresp = info.get("boresp") if isinstance(info.get("boresp"), dict) else {}
            return {
                "done": info.get("done"),
                "errorcode": info.get("errorcode"),
                "boerror": info.get("boerror"),
                "resultcode": info.get("resultcode"),
                "resultdesc": info.get("resultdesc"),
                "reason": info.get("reason"),
                "boresp_result": boresp.get("result"),
                "boresp_type": boresp.get("type"),
                "boresp_level2": boresp.get("level2"),
                "boresp_isVip": boresp.get("isVip"),
                "boresp_vipLevel": boresp.get("vipLevel"),
                "boresp_markets": boresp.get("markets"),
                "boresp_rawPermission": boresp.get("rawPermission"),
            }


        def is_init_success(info):
            if not isinstance(info, dict):
                return False
            boresp = info.get("boresp") if isinstance(info.get("boresp"), dict) else {}
            if info.get("done") != 1 or info.get("errorcode") not in (0, None):
                return False
            if info.get("resultcode") not in (0, None):
                return False
            return boresp.get("result", 0) in (0, None)


        def object_summary(obj):
            summary = {"type": type(obj).__name__, "repr": repr(obj)[:500]}
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
                    return all(int(x) > 0 for x in obj.shape[:1])
                except Exception:
                    pass
            if hasattr(obj, "__len__"):
                try:
                    return len(obj) > 0
                except Exception:
                    pass
            return False


        def call_l2_api(xtdata, name, code):
            func = getattr(xtdata, name, None)
            if not callable(func):
                return {"available": False, "error": f"xtdata 缺少接口 {name}"}
            try:
                value = func(stock_code=code, count=1)
                return {
                    "available": has_non_empty_data(value),
                    "summary": object_summary(value),
                }
            except TypeError:
                try:
                    value = func([], code, "", "", 1)
                    return {
                        "available": has_non_empty_data(value),
                        "summary": object_summary(value),
                    }
                except Exception as exc:
                    return {"available": False, "error": repr(exc)}
            except Exception as exc:
                return {"available": False, "error": repr(exc)}


        token = os.environ.get("XT_TOKEN")
        if not token:
            fail("子进程未读取到 XT_TOKEN")

        code = os.environ.get("XT_LEVEL2_TEST_CODE", os.environ.get("XT_VIP_TEST_CODE", "000001.SZ"))
        markets = normalize_list(os.environ.get("XT_LEVEL2_TEST_MARKETS", "SH;SZ"))
        data_home = os.environ.get("XT_DATA_HOME") or os.environ.get("XT_LEVEL2_DATA_HOME")
        if not data_home:
            data_home = tempfile.mkdtemp(prefix="xqshare-level2-")
        pathlib.Path(data_home).mkdir(parents=True, exist_ok=True)

        try:
            from xtquant import xtbson
            from xtquant import xtdatacenter as xtdc
            from xtquant import xtdata
            from xqshare.market_data import MarketDataRuntime

            os.environ["XQSHARE_MARKET_MODE"] = "token"
            os.environ["XT_DATA_HOME"] = data_home
            os.environ["XT_INIT_MARKETS"] = ",".join(markets)
            if os.environ.get("XT_LEVEL2_TEST_PORT_START"):
                os.environ["XTDC_PORT_START"] = os.environ["XT_LEVEL2_TEST_PORT_START"]
            if os.environ.get("XT_LEVEL2_TEST_PORT_END"):
                os.environ["XTDC_PORT_END"] = os.environ["XT_LEVEL2_TEST_PORT_END"]

            print("MarketDataRuntime.initialize start", flush=True)
            runtime = MarketDataRuntime(xtdc, xtdata)
            runtime_status = runtime.initialize()
            print("MarketDataRuntime.initialize done: " + json.dumps(runtime_status, ensure_ascii=False), flush=True)

            try:
                import xtquant.datacenter as datacenter
            except Exception as exc:
                datacenter = None
                datacenter_import_error = repr(exc)
            else:
                datacenter_import_error = None

            auth_markets = []
            l1_results = {}
            l2_results = {}
            l2_success_markets = []
            l2_marker_markets = []

            if datacenter is not None:
                auth_info = datacenter.fetch_auth_markets()
                auth_markets = normalize_list(auth_info.get("markets", [])) if isinstance(auth_info, dict) else []
                init_keys = []
                for market in markets:
                    init_keys.extend([f"0_{market}_L1", f"0_{market}_L2"])
                raw_init_results = datacenter.fetch_init_result(init_keys)
                for market in markets:
                    l1_info = decode_bson(raw_init_results.get(f"0_{market}_L1"), xtbson)
                    l2_info = decode_bson(raw_init_results.get(f"0_{market}_L2"), xtbson)
                    l1_results[market] = simplify_init_result(l1_info)
                    l2_results[market] = simplify_init_result(l2_info)
                    if is_init_success(l2_info):
                        l2_success_markets.append(market)
                    l1_markets = normalize_list(l1_results[market].get("boresp_markets", []))
                    if f"{market}:L2" in auth_markets or f"{market}:L2" in l1_markets:
                        l2_marker_markets.append(market)

            # 实盘数据接口再探测一次；非交易时间可能没有数据，所以仅作为辅助诊断。
            time.sleep(float(os.environ.get("XT_LEVEL2_TEST_WARMUP_SECONDS", "1")))
            api_results = {
                name: call_l2_api(xtdata, name, code)
                for name in ("get_l2_quote", "get_l2_order", "get_l2_transaction")
            }
            api_available = [name for name, result in api_results.items() if result.get("available")]

            has_level2_permission = bool(l2_success_markets or l2_marker_markets or api_available)
            diagnostics = {
                "code": code,
                "markets_checked": markets,
                "data_home": data_home,
                "runtime_status": runtime_status,
                "auth_markets": auth_markets,
                "l1_results": l1_results,
                "l2_results": l2_results,
                "l2_success_markets": l2_success_markets,
                "l2_marker_markets": l2_marker_markets,
                "l2_api_available": api_available,
                "l2_api_results": api_results,
                "datacenter_import_error": datacenter_import_error,
            }
            print("level2_diagnostics=" + json.dumps(diagnostics, ensure_ascii=False, default=str), flush=True)

            if not has_level2_permission:
                fail(
                    "未检测到指定市场的 Level2 行情权限；如果只购买了其他市场，请设置 XT_LEVEL2_TEST_MARKETS 后重试",
                    **diagnostics,
                )

            emit({"ok": True, "message": "检测到 Level2 行情权限", **diagnostics}, 0)
        except SystemExit:
            raise
        except Exception as exc:
            fail("Level2 权限测试异常", error=repr(exc), traceback=traceback.format_exc())
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
