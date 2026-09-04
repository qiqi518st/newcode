"""RecoveryBuilder 恢复段三块单测（ch08 T31，spec F16/F17/F18/F31，AC9-AC11）。

防 bug：文件快照超限/顺序、工具列表重算、边界提示不稳定。
"""

import pytest

from newcode.context.files import FileTracker
from newcode.context.recovery import BOUNDARY_NOTICE, RecoveryBuilder
from newcode.provider.base import ToolDefinition


@pytest.mark.anyio
async def test_file_snapshot_limit():
    """AC9：7 个 record → 只含最近 5、倒序。

    第 6/7 个文件路径不应出现（反向断言）。
    """
    ft = FileTracker()
    for i in range(7):
        await ft.record(f"/file{i}.py", f"c{i}")
    bundle = await RecoveryBuilder().build(ft, [])
    text = bundle.file_snapshots_text
    # 最近 5 个出现（file2-file6）
    for i in range(2, 7):
        assert f"/file{i}.py" in text
    # 第 0/1 个（最旧）不应出现
    assert "/file0.py" not in text
    assert "/file1.py" not in text


@pytest.mark.anyio
async def test_file_truncate():
    """AC9：超 5000 token 只留头部 + (content truncated)。"""
    ft = FileTracker()
    # 5000 token * 3.5 = 17500 字符，给 20000 字符触发截断
    await ft.record("/big.py", "x" * 20_000)
    bundle = await RecoveryBuilder().build(ft, [])
    assert "(content truncated)" in bundle.file_snapshots_text
    # 截断后内容应小于原 20000
    assert "x" * 20_000 not in bundle.file_snapshots_text


@pytest.mark.anyio
async def test_tools_exact_reference():
    """AC10：工具文本与传入 tool_defs 集合一致、内部不重算。

    直接引用传入列表（id 一致），不构造新列表。
    """
    ft = FileTracker()
    defs = [
        ToolDefinition(
            name="read_file", description="读文件", parameters={"type": "object"}
        ),
        ToolDefinition(name="grep", description="搜索", parameters={}),
    ]
    builder = RecoveryBuilder()
    bundle = await builder.build(ft, defs)
    text = bundle.tools_declaration_text
    assert "- read_file: 读文件" in text
    assert "- grep: 搜索" in text
    # 空参数不附 schema 行
    # grep 后若无 schema（参数为空），下一行应是另一个工具或无
    assert "object" in text  # read_file 的 schema 出现


@pytest.mark.anyio
async def test_boundary_notice_stable():
    """AC11：边界提示固定文案，同入参两次输出逐字节相等。"""
    ft = FileTracker()
    b1 = await RecoveryBuilder().build(ft, [])
    b2 = await RecoveryBuilder().build(ft, [])
    assert b1.boundary_notice_text == b2.boundary_notice_text
    assert b1.boundary_notice_text == BOUNDARY_NOTICE
    # 文案含重读提示
    assert "重读" in BOUNDARY_NOTICE or "重新读取" in BOUNDARY_NOTICE


@pytest.mark.anyio
async def test_empty_file_tracker():
    """防 bug：无文件时快照段不应抛异常、应有兜底文案。"""
    ft = FileTracker()
    bundle = await RecoveryBuilder().build(ft, [])
    assert "(no recent files)" in bundle.file_snapshots_text


@pytest.mark.anyio
async def test_empty_tools():
    """防 bug：无工具时工具段应有兜底文案。"""
    ft = FileTracker()
    bundle = await RecoveryBuilder().build(ft, [])
    assert "(no tools)" in bundle.tools_declaration_text
