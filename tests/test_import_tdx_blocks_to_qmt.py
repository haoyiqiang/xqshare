"""将通达信 .blk 自定义板块导入 QMT 自定义板块。

默认读取：
    C:\\new_tdx\\T0002\\blocknew\\323.blk ~ 331.blk
板块名称按日期生成，例如 323.blk -> 2026-03-23，328.blk -> 2026-03-28。

真实写入 QMT（有副作用）前，需要启动并登录 QMT/MiniQMT，并配置其
``userdata_mini`` 路径，然后执行：

PowerShell::
    $env:RUN_QMT_BLOCK_IMPORT="1"
    $env:QMT_USERDATA_PATH="C:\\你的QMT目录\\userdata_mini"
    uv run pytest tests/test_import_tdx_blocks_to_qmt.py -q -s
可选环境变量：
    TDX_BLOCK_DIR=C:\\new_tdx\\T0002\\blocknew
    TDX_BLOCK_IDS=323-331
    TDX_BLOCK_YEAR=2026
    QMT_SECTOR_PREFIX=
    QMT_XTDATA_PORT=58610
    TDX_BLOCK_STRICT_FILES=false
    QMT_REMOVE_LEGACY_NUMERIC_SECTORS=false
    QMT_SECTOR_VERIFY_TIMEOUT=10
    QMT_BLOCK_IMPORT_TIMEOUT=180

注意：导入会覆盖同名日期板块的现有成分股；兼容旧券商 QMT 的底层板块接口。
"""

from __future__ import annotations

from datetime import date

import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Sequence, Tuple

import pytest

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


DEFAULT_BLOCK_DIR = Path(r"C:\new_tdx\T0002\blocknew")
DEFAULT_BLOCK_IDS = "323-331"
TDX_MARKET_MAP = {
    "0": "SZ",
    "1": "SH",
    "2": "BJ",
}


def parse_block_id_spec(value: str) -> List[str]:
    """解析 ``323-331,335`` 形式的板块编号。"""
    result: List[str] = []
    for part in value.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            result.append(part)
            continue

        start_text, end_text = (item.strip() for item in part.split("-", 1))
        start = int(start_text)
        end = int(end_text)
        step = 1 if start <= end else -1
        result.extend(str(value) for value in range(start, end + step, step))

    # 去重并保持原顺序。
    return list(dict.fromkeys(result))


def block_id_to_sector_date(block_id: str, year: int) -> str:
    """将 323、328、1231 这类 MMDD 编号转换成 YYYY-MM-dd。"""
    if not block_id.isdigit() or len(block_id) not in {3, 4}:
        raise ValueError(f"板块编号必须是 MMDD 格式: {block_id!r}")

    month = int(block_id[:-2])
    day = int(block_id[-2:])
    try:
        return date(year, month, day).isoformat()
    except ValueError as exc:
        raise ValueError(f"无效的板块日期编号: {block_id!r}") from exc


def parse_tdx_block_file(path: Path) -> List[str]:
    """将通达信 7 位市场代码转换成 xtquant 的 ``code.market`` 格式。

    通达信 .blk 常见格式：
        0002310 -> 002310.SZ
        1600683 -> 600683.SH
        2830799 -> 830799.BJ
    """
    stock_codes: List[str] = []
    invalid_lines: List[Tuple[int, str]] = []

    text = path.read_text(encoding="ascii", errors="strict")
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        value = raw_line.strip().lstrip("\ufeff")
        if not value:
            continue

        if len(value) != 7 or not value.isdigit() or value[0] not in TDX_MARKET_MAP:
            invalid_lines.append((line_number, value))
            continue

        stock_codes.append(f"{value[1:]}.{TDX_MARKET_MAP[value[0]]}")

    if invalid_lines:
        preview = ", ".join(f"第{line}行={value!r}" for line, value in invalid_lines[:5])
        raise ValueError(f"{path} 包含无法识别的通达信代码: {preview}")

    # QMT 板块无需重复代码，保持通达信原始顺序去重。
    return list(dict.fromkeys(stock_codes))


def load_tdx_blocks(
    block_dir: Path,
    block_ids: Sequence[str],
    strict_files: bool = False,
) -> Tuple[Dict[str, List[str]], List[str]]:
    """读取指定板块文件，返回 ``{板块编号: 股票列表}`` 和缺失文件列表。"""
    blocks: Dict[str, List[str]] = {}
    missing: List[str] = []

    for block_id in block_ids:
        path = block_dir / f"{block_id}.blk"
        if not path.is_file():
            missing.append(str(path))
            continue

        stock_codes = parse_tdx_block_file(path)
        if not stock_codes:
            raise ValueError(f"板块文件为空: {path}")
        blocks[block_id] = stock_codes

    if strict_files and missing:
        raise FileNotFoundError("缺少板块文件: " + ", ".join(missing))
    if not blocks:
        raise FileNotFoundError(f"{block_dir} 下没有找到指定的 .blk 文件")

    return blocks, missing


def _is_function_not_realize(exc: BaseException) -> bool:
    return "function not realize" in str(exc).lower()


def _replace_sector_with_legacy_client(
    xtdata_module: Any,
    sector_name: str,
    stock_codes: Sequence[str],
) -> None:
    """兼容旧券商 QMT：绕过新版 commonControl，调用旧版底层板块接口。"""
    get_client = getattr(xtdata_module, "get_client", None)
    if not callable(get_client):
        raise RuntimeError("当前 xtquant 不提供 get_client，无法使用旧版板块兼容接口")

    client = get_client()
    add_sector = getattr(client, "add_sector", None)
    if not callable(add_sector):
        raise RuntimeError("当前 QMT 底层客户端不提供旧版 add_sector 接口")

    if sector_name in set(xtdata_module.get_sector_list() or []):
        # 旧版接口 mode=-1 删除板块，mode=1 新建/增加板块成分。
        add_sector(sector_name, [], -1)
    add_sector(sector_name, list(stock_codes), 1)


def _remove_sector_with_legacy_client(xtdata_module: Any, sector_name: str) -> None:
    get_client = getattr(xtdata_module, "get_client", None)
    if not callable(get_client):
        raise RuntimeError("当前 xtquant 不提供 get_client，无法删除旧版板块")

    add_sector = getattr(get_client(), "add_sector", None)
    if not callable(add_sector):
        raise RuntimeError("当前 QMT 底层客户端不提供旧版 add_sector 接口")
    add_sector(sector_name, [], -1)


def _remove_sector_compat(xtdata_module: Any, sector_name: str) -> None:
    """兼容新版 commonControl 和旧券商 QMT 的板块删除。"""
    if sector_name not in set(xtdata_module.get_sector_list() or []):
        return

    remove_sector = getattr(xtdata_module, "remove_sector", None)
    try:
        if not callable(remove_sector):
            raise AttributeError("当前 xtquant 缺少 remove_sector")
        remove_sector(sector_name)
    except AttributeError:
        _remove_sector_with_legacy_client(xtdata_module, sector_name)
    except RuntimeError as exc:
        if not _is_function_not_realize(exc):
            raise
        _remove_sector_with_legacy_client(xtdata_module, sector_name)

    if sector_name in set(xtdata_module.get_sector_list() or []):
        raise AssertionError(f"旧板块删除失败: {sector_name}")


def import_blocks_to_qmt(
    xtdata_module: Any,
    block_dir: Path,
    block_ids: Sequence[str],
    sector_prefix: str = "",
    sector_year: int = date.today().year,
    remove_legacy_numeric_sectors: bool = False,
    strict_files: bool = False,
    verify_timeout: float = 10.0,
) -> Dict[str, Any]:
    """创建/覆盖 QMT 自定义板块，并回读校验股票列表。"""
    blocks, missing_files = load_tdx_blocks(block_dir, block_ids, strict_files=strict_files)
    existing_sectors = set(xtdata_module.get_sector_list() or [])
    imported: Dict[str, Dict[str, Any]] = {}
    removed_legacy_sectors: List[str] = []

    for block_id, expected_codes in blocks.items():
        dated_name = block_id_to_sector_date(block_id, sector_year)
        sector_name = f"{sector_prefix}{dated_name}"
        legacy_sector_name = f"{sector_prefix}{block_id}"

        if remove_legacy_numeric_sectors and legacy_sector_name in existing_sectors:
            _remove_sector_compat(xtdata_module, legacy_sector_name)
            existing_sectors.discard(legacy_sector_name)
            removed_legacy_sectors.append(legacy_sector_name)
        created = sector_name not in existing_sectors

        write_method = "commonControl"
        create_sector = getattr(xtdata_module, "create_sector", None)
        reset_sector = getattr(xtdata_module, "reset_sector", None)
        try:
            if not callable(create_sector) or not callable(reset_sector):
                raise AttributeError("当前 xtquant 缺少新版板块写入接口")

            if created:
                created_name = create_sector("", sector_name, overwrite=True)
                if isinstance(created_name, str) and created_name:
                    sector_name = created_name

            reset_result = reset_sector(sector_name, expected_codes)
            if reset_result is False:
                raise RuntimeError(f"QMT reset_sector({sector_name!r}) 返回 False")
        except AttributeError:
            _replace_sector_with_legacy_client(xtdata_module, sector_name, expected_codes)
            write_method = "legacy_client.add_sector"
        except RuntimeError as exc:
            if not _is_function_not_realize(exc):
                raise
            _replace_sector_with_legacy_client(xtdata_module, sector_name, expected_codes)
            write_method = "legacy_client.add_sector"

        expected_set = set(expected_codes)
        verify_deadline = time.monotonic() + verify_timeout
        actual_codes: List[str] = []
        while True:
            actual_codes = list(xtdata_module.get_stock_list_in_sector(sector_name) or [])
            actual_set = set(actual_codes)
            missing_codes = sorted(expected_set - actual_set)
            extra_codes = sorted(actual_set - expected_set)
            if not missing_codes and not extra_codes:
                break
            if time.monotonic() >= verify_deadline:
                raise AssertionError(
                    f"板块 {sector_name} 回读校验失败: "
                    f"缺少 {missing_codes[:10]}，多出 {extra_codes[:10]}"
                )
            time.sleep(0.5)

        imported[block_id] = {
            "sector_name": sector_name,
            "created": created,
            "stock_count": len(expected_codes),
            "write_method": write_method,
        }
        existing_sectors.add(sector_name)

    return {
        "block_dir": str(block_dir),
        "imported": imported,
        "missing_files": missing_files,
        "removed_legacy_sectors": removed_legacy_sectors,
        "total_blocks": len(imported),
        "total_stocks": sum(item["stock_count"] for item in imported.values()),
    }


def _is_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def validate_qmt_connection(xtdata_module: Any, qmt_userdata_path: Path) -> str:
    """确认 xtdata 连接的是指定 QMT，而不是 xqshare 的 Token xtdatacenter。"""
    get_data_dir = getattr(xtdata_module, "get_data_dir", None)
    actual_data_dir = get_data_dir() if callable(get_data_dir) else getattr(xtdata_module, "data_dir", "")
    if not actual_data_dir:
        raise RuntimeError("无法获取 xtdata 当前连接的数据目录")

    expected = os.path.normcase(os.path.abspath(str(qmt_userdata_path)))
    actual = os.path.normcase(os.path.abspath(str(actual_data_dir)))
    try:
        belongs_to_qmt = os.path.commonpath([expected, actual]) == expected
    except ValueError:
        belongs_to_qmt = False

    if not belongs_to_qmt:
        raise RuntimeError(
            "xtdata 未连接到目标 QMT："
            f"期望 userdata_mini={qmt_userdata_path}，实际 data_dir={actual_data_dir}。"
            "请启动并登录 QMT，必要时设置 QMT_XTDATA_PORT；"
            "不要连接 xqshare Token xtdatacenter 端口。"
        )
    return str(actual_data_dir)


def test_parse_tdx_block_file_format(tmp_path):
    block_file = tmp_path / "sample.blk"
    block_file.write_bytes(b"\r\n0002310\r\n1600683\r\n2830799\r\n0002310\r\n")

    assert parse_tdx_block_file(block_file) == [
        "002310.SZ",
        "600683.SH",
        "830799.BJ",
    ]


def test_block_id_to_sector_date():
    assert block_id_to_sector_date("323", 2026) == "2026-03-23"
    assert block_id_to_sector_date("328", 2026) == "2026-03-28"
    assert block_id_to_sector_date("1231", 2026) == "2026-12-31"
    with pytest.raises(ValueError):
        block_id_to_sector_date("332", 2026)


def test_import_blocks_to_qmt_with_fake_xtdata(tmp_path):
    class FakeXtData:
        def __init__(self):
            self.sectors = {}

        def get_sector_list(self):
            return list(self.sectors)

        def create_sector(self, parent_node, sector_name, overwrite=True):
            assert parent_node == ""
            self.sectors.setdefault(sector_name, [])
            return sector_name

        def reset_sector(self, sector_name, stock_list):
            self.sectors[sector_name] = list(stock_list)
            return True

        def get_stock_list_in_sector(self, sector_name):
            return self.sectors.get(sector_name, [])

    (tmp_path / "323.blk").write_bytes(b"0002310\r\n1600683\r\n")
    (tmp_path / "324.blk").write_bytes(b"0300385\r\n")
    fake_xtdata = FakeXtData()

    result = import_blocks_to_qmt(
        xtdata_module=fake_xtdata,
        block_dir=tmp_path,
        block_ids=["323", "324"],
        sector_year=2026,
    )

    assert result["total_blocks"] == 2
    assert result["total_stocks"] == 3
    assert fake_xtdata.sectors["2026-03-23"] == ["002310.SZ", "600683.SH"]
    assert fake_xtdata.sectors["2026-03-24"] == ["300385.SZ"]


def test_load_configured_tdx_block_files():
    """验证本机 323~331 通达信板块文件能够被正确解析。"""
    block_dir = Path(os.environ.get("TDX_BLOCK_DIR", str(DEFAULT_BLOCK_DIR)))
    if not block_dir.is_dir():
        pytest.skip(f"通达信板块目录不存在: {block_dir}")

    block_ids = parse_block_id_spec(os.environ.get("TDX_BLOCK_IDS", DEFAULT_BLOCK_IDS))
    blocks, missing_files = load_tdx_blocks(block_dir, block_ids, strict_files=False)

    assert blocks
    assert all(codes for codes in blocks.values())
    assert all(code.endswith((".SH", ".SZ", ".BJ")) for codes in blocks.values() for code in codes)

    print("解析到的通达信板块:")
    for block_id, codes in blocks.items():
        print(f"  {block_id}.blk -> {len(codes)} 只")
    if missing_files:
        print("缺失文件（已跳过）:")
        for path in missing_files:
            print(f"  {path}")


@pytest.mark.integration
def test_import_tdx_blocks_to_real_qmt():
    """真实写入 QMT 自定义板块；默认跳过，必须显式开启副作用测试。"""
    if load_dotenv is not None:
        load_dotenv()

    if not _is_true(os.environ.get("RUN_QMT_BLOCK_IMPORT", "0")):
        pytest.skip("设置 RUN_QMT_BLOCK_IMPORT=1 后才执行真实 QMT 板块写入")

    env = os.environ.copy()
    if not env.get("QMT_USERDATA_PATH", "").strip():
        pytest.fail("真实导入前必须设置 QMT_USERDATA_PATH，防止误连 Token xtdatacenter")
    timeout = int(env.get("QMT_BLOCK_IMPORT_TIMEOUT", "180"))
    script = r'''
import json
import os
from datetime import date
from pathlib import Path

from xtquant import xtdata
from tests.test_import_tdx_blocks_to_qmt import (
    DEFAULT_BLOCK_DIR,
    DEFAULT_BLOCK_IDS,
    _is_true,
    import_blocks_to_qmt,
    parse_block_id_spec,
    validate_qmt_connection,
)

qmt_userdata_path = Path(os.environ["QMT_USERDATA_PATH"])
if not qmt_userdata_path.is_dir():
    raise FileNotFoundError(f"QMT_USERDATA_PATH 不存在: {qmt_userdata_path}")

port = os.environ.get("QMT_XTDATA_PORT", "").strip()
if port:
    xtdata.connect(port=int(port))
else:
    # 不指定 xtdatacenter Token 端口，直接连接已启动并登录的 QMT/MiniQMT。
    xtdata.connect()

actual_data_dir = validate_qmt_connection(xtdata, qmt_userdata_path)

result = import_blocks_to_qmt(
    xtdata_module=xtdata,
    block_dir=Path(os.environ.get("TDX_BLOCK_DIR", str(DEFAULT_BLOCK_DIR))),
    block_ids=parse_block_id_spec(os.environ.get("TDX_BLOCK_IDS", DEFAULT_BLOCK_IDS)),
    sector_prefix=os.environ.get("QMT_SECTOR_PREFIX", ""),
    sector_year=int(os.environ.get("TDX_BLOCK_YEAR", str(date.today().year))),
    remove_legacy_numeric_sectors=_is_true(
        os.environ.get("QMT_REMOVE_LEGACY_NUMERIC_SECTORS", "false")
    ),
    strict_files=_is_true(os.environ.get("TDX_BLOCK_STRICT_FILES", "false")),
    verify_timeout=float(os.environ.get("QMT_SECTOR_VERIFY_TIMEOUT", "10")),
)
result["qmt_data_dir"] = actual_data_dir
print(json.dumps({"ok": True, **result}, ensure_ascii=False), flush=True)
'''

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout
    assert '"ok": true' in completed.stdout, completed.stdout
    print(completed.stdout)
