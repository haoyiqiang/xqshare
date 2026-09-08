"""
xtquant Token 模式 VIP 行情 + K线全推集成测试。

运行方式：
    uv run pytest tests/test_xtdata_vip_token_kline.py -q -s
运行前准备：
    1. 安装支持 xtdatacenter 的 xtquant 版本。
    2. 在 .env 中设置 XT_TOKEN。
    3. 可选环境变量：
       XT_VIP_TEST_CODE=000001.SZ
       XT_VIP_TEST_PERIOD=1m
       XT_VIP_TEST_TIMEOUT=180
       XT_DATA_HOME=.xtdata-cache

说明：
    本测试会在子进程中导入真实 xtquant，避免被 tests/conftest.py 的 xtquant mock 影响。
    未设置 XT_TOKEN 时自动跳过，避免普通单元测试误连真实行情服务。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
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
def test_xtdata_vip_token_kline_mirror_real_connection():
    """使用 XT_TOKEN 直连 VIP 行情，并验证 K线全推 get_full_kline 可用。"""
    if load_dotenv is not None:
        load_dotenv()

    token = os.environ.get("XT_TOKEN")
    if not token:
        pytest.skip("未设置 XT_TOKEN，跳过 xtquant VIP 行情真实连接测试")

    env = os.environ.copy()
    env["XT_VIP_SERVERS"] = ";".join(VIP_QUOTE_SERVERS)

    timeout = int(env.get("XT_VIP_TEST_TIMEOUT", "180"))

    script = textwrap.dedent(
        r'''
        import json
        import os
        import pathlib
        import sys
        import tempfile
        import time
        import traceback


        def fail(message, **extra):
            payload = {"ok": False, "message": message, **extra}
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            sys.stdout.flush()
            os._exit(1)


        def normalize_list(value):
            if value is None:
                return []
            if isinstance(value, str):
                return [item.strip() for item in value.split(";") if item.strip()]
            return list(value)


        def normalize_listen_port(listen_result):
            """兼容 xtdc.listen 返回 int、('0.0.0.0', port)、[host, port] 等形式。"""
            if isinstance(listen_result, int):
                return listen_result
            if isinstance(listen_result, (tuple, list)) and len(listen_result) >= 2:
                return int(listen_result[1])
            if isinstance(listen_result, dict):
                for key in ("port", "listen_port"):
                    if key in listen_result:
                        return int(listen_result[key])
            return int(listen_result)


        def simplify_quote_status(status):
            """把不同 xtquant 版本的连接状态对象转成便于打印的结构。"""
            result = {}
            if not isinstance(status, dict):
                return {"repr": repr(status)}

            for key, value in status.items():
                item = {"repr": repr(value)}
                raw_info = getattr(value, "info", None)
                if raw_info is not None:
                    item["info_repr"] = repr(raw_info)
                    if isinstance(raw_info, dict):
                        item.update({f"info_{k}": v for k, v in raw_info.items()})
                if isinstance(value, dict):
                    item.update(value)
                for attr in ("ip", "port", "status", "server_type", "type"):
                    if hasattr(value, attr):
                        try:
                            item[attr] = getattr(value, attr)
                        except Exception:
                            pass
                result[str(key)] = item
            return result


        def dataframe_summary(obj):
            summary = {
                "type": type(obj).__name__,
                "repr": repr(obj)[:500],
            }
            if hasattr(obj, "shape"):
                try:
                    summary["shape"] = tuple(int(x) for x in obj.shape)
                except Exception:
                    pass
            if hasattr(obj, "empty"):
                try:
                    summary["empty"] = bool(obj.empty)
                except Exception:
                    pass
            if hasattr(obj, "columns"):
                try:
                    summary["columns"] = [str(x) for x in list(obj.columns)[:20]]
                except Exception:
                    pass
            if hasattr(obj, "index"):
                try:
                    summary["index_tail"] = [str(x) for x in list(obj.index)[-5:]]
                except Exception:
                    pass
            return summary


        token = os.environ.get("XT_TOKEN")
        if not token:
            fail("子进程未读取到 XT_TOKEN")

        code = os.environ.get("XT_VIP_TEST_CODE", "000001.SZ")
        period = os.environ.get("XT_VIP_TEST_PERIOD", "1m")
        vip_servers = normalize_list(os.environ.get("XT_VIP_SERVERS"))
        markets = normalize_list(os.environ.get("XT_VIP_TEST_MARKETS", "SH;SZ"))
        data_home = os.environ.get("XT_DATA_HOME") or os.environ.get("XT_VIP_DATA_HOME")
        if not data_home:
            data_home = tempfile.mkdtemp(prefix="xqshare-xtdc-vip-")
        pathlib.Path(data_home).mkdir(parents=True, exist_ok=True)

        xtdata = None
        xtdc = None
        try:
            from xtquant import xtdatacenter as xtdc
            from xtquant import xtdata

            from xqshare.market_data import MarketDataRuntime

            # 直接调用 xqshare 生产代码的行情初始化路径。
            os.environ["XQSHARE_MARKET_MODE"] = "token"
            os.environ["XT_DATA_HOME"] = data_home
            os.environ["XT_INIT_MARKETS"] = ",".join(markets)
            os.environ["XT_KLINE_MIRROR_MARKETS"] = ",".join(markets)
            if os.environ.get("XT_VIP_TEST_PORT_START"):
                os.environ["XTDC_PORT_START"] = os.environ["XT_VIP_TEST_PORT_START"]
            if os.environ.get("XT_VIP_TEST_PORT_END"):
                os.environ["XTDC_PORT_END"] = os.environ["XT_VIP_TEST_PORT_END"]

            print("MarketDataRuntime.initialize start", flush=True)
            runtime = MarketDataRuntime(xtdc, xtdata)
            runtime_status = runtime.initialize()
            listen_port = runtime_status["listen_port"]
            print(
                "MarketDataRuntime.initialize done: "
                + json.dumps(runtime_status, ensure_ascii=False),
                flush=True,
            )

            # 给 K线全推连接一点加载时间。
            time.sleep(float(os.environ.get("XT_VIP_TEST_WARMUP_SECONDS", "3")))

            quote_status = xtdata.get_quote_server_status()
            simple_status = simplify_quote_status(quote_status)
            print("quote_status=" + json.dumps(simple_status, ensure_ascii=False), flush=True)

            dates = xtdata.get_trading_dates("SH")
            if not dates:
                fail("get_trading_dates('SH') 返回为空", quote_status=simple_status)

            instrument = xtdata.get_instrument_detail(code)
            if not instrument:
                fail(f"get_instrument_detail({code!r}) 返回为空", quote_status=simple_status)

            fields = ["time", "open", "high", "low", "close", "volume"]
            kline_deadline = time.time() + float(
                os.environ.get("XT_VIP_TEST_KLINE_TIMEOUT", "60")
            )
            kline = {}
            close_df = None
            while time.time() < kline_deadline:
                # 新版 xtquant 会在第一次 get_full_kline 时开始加载全推数据，
                # 初始化后短时间返回空 DataFrame 属于正常预热过程。
                kline = xtdata.get_full_kline(
                    field_list=fields,
                    stock_list=[code],
                    period=period,
                    count=1,
                )
                if isinstance(kline, dict) and kline:
                    close_df = kline.get("close")
                    shape = getattr(close_df, "shape", None)
                    if shape and shape[0] > 0 and shape[1] > 0:
                        break
                time.sleep(1)
            if not isinstance(kline, dict) or not kline:
                fail("get_full_kline 返回不是非空 dict", returned_type=type(kline).__name__, quote_status=simple_status)
            kline_summary = {str(field): dataframe_summary(value) for field, value in kline.items()}
            print("kline_summary=" + json.dumps(kline_summary, ensure_ascii=False), flush=True)
            if close_df is None:
                fail("get_full_kline 返回中没有 close 字段", kline_summary=kline_summary)
            close_summary = dataframe_summary(close_df)
            shape = close_summary.get("shape")
            if not shape or shape[0] <= 0 or shape[1] <= 0:
                fail("K线全推预热超时，close 字段仍没有有效行列", close_summary=close_summary, kline_summary=kline_summary)
            # 不同 xtquant 版本的 get_full_kline DataFrame 方向可能不同：
            #   1. index=时间, columns=股票代码
            #   2. index=股票代码, columns=时间
            try:
                close_columns = [str(x) for x in list(close_df.columns)]
                close_index = [str(x) for x in list(close_df.index)]
            except Exception:
                close_columns = close_summary.get("columns", [])
                close_index = close_summary.get("index_tail", [])
            if code not in close_columns and code not in close_index:
                fail(f"get_full_kline close 字段行/列中没有 {code}", close_summary=close_summary, kline_summary=kline_summary)

            print(json.dumps({
                "ok": True,
                "code": code,
                "period": period,
                "data_home": data_home,
                "listen_port": listen_port,
                "trading_dates_tail": dates[-5:],
                "instrument_name": instrument.get("InstrumentName") if isinstance(instrument, dict) else repr(instrument),
                "kline_summary": kline_summary,
                "quote_status": simple_status,
            }, ensure_ascii=False), flush=True)
            sys.stdout.flush()
            os._exit(0)
        except SystemExit:
            raise
        except Exception as exc:
            fail("xtquant VIP 行情 + K线全推测试异常", error=repr(exc), traceback=traceback.format_exc())
        finally:
            if xtdata is not None:
                for name in ("disconnect",):
                    func = getattr(xtdata, name, None)
                    if callable(func):
                        try:
                            func()
                        except Exception:
                            pass
            if xtdc is not None:
                for name in ("shutdown", "stop", "close", "exit"):
                    func = getattr(xtdc, name, None)
                    if callable(func):
                        try:
                            func()
                        except Exception:
                            pass
        '''
    )

    attempts = int(env.get("XT_VIP_TEST_ATTEMPTS", "3"))
    retry_delay = float(env.get("XT_VIP_TEST_RETRY_DELAY", "10"))
    attempt_outputs = []
    completed = None
    has_success_marker = False

    for attempt in range(1, attempts + 1):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        has_success_marker = '"ok": true' in completed.stdout
        attempt_outputs.append(f"===== attempt {attempt}/{attempts} =====\n{completed.stdout}")
        if has_success_marker:
            break
        if attempt < attempts:
            time.sleep(retry_delay)

    output = "\n".join(attempt_outputs)
    assert completed is not None
    # 部分 xtquant 版本在 Python 退出时后台线程/本地行情进程清理会返回 Windows
    # 异常码 0xC0000409，但测试主体已经输出 ok=true；此时仍视为连接与取数成功。
    assert completed.returncode == 0 or has_success_marker, output
    assert has_success_marker, output
