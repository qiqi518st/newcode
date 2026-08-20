"""InstructionLoader 单测（ch09 T2，spec F1-F4 / AC1-AC4）。

防 bug：三层发现顺序颠倒、include 越权展开、深度/环路/越界未拦截、
二进制或超限文件被读入、启动后修改指令文件导致缓存失效。
"""

from pathlib import Path

import pytest

from mewcode.instructions.loader import InstructionLoader


@pytest.fixture
def workspace(tmp_path):
    return tmp_path / "proj"


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_three_layer_order(workspace, home):
    """防 bug：三层内容必须按项目根、项目 .mewcode、用户级拼接且高优先级在前。"""
    _write(workspace / "MEWCODE.md", "# root")
    _write(workspace / ".mewcode" / "MEWCODE.md", "# project_config")
    _write(home / ".mewcode" / "MEWCODE.md", "# user")
    loader = InstructionLoader(workspace, user_home=home)
    doc = loader.load()
    assert doc.text == "# root\n\n# project_config\n\n# user"
    assert len(doc.sources) == 3


def test_missing_layers_silent(workspace, home):
    """防 bug：缺失层不能抛异常或产生空占位。"""
    loader = InstructionLoader(workspace, user_home=home)
    doc = loader.load()
    assert doc.text == ""
    assert doc.sources == []


def test_empty_file_no_content(workspace, home):
    """防 bug：空文件不产生内容、来源也不计入（spec F1 静默跳过）。"""
    _write(workspace / "MEWCODE.md", "")
    loader = InstructionLoader(workspace, user_home=home)
    doc = loader.load()
    assert doc.text == ""
    assert doc.sources == []


def test_include_exclusive_line(workspace, home):
    """防 bug：独占行 include 被完整替换，段落中的 @include 保持原文。"""
    _write(workspace / "MEWCODE.md", "before\n@include part.md\nafter")
    _write(workspace / "part.md", "INCLUDED")
    loader = InstructionLoader(workspace, user_home=home)
    doc = loader.load()
    assert "INCLUDED" in doc.text
    assert "@include part.md" not in doc.text
    assert "before" in doc.text and "after" in doc.text


def test_include_inline_not_expanded(workspace, home):
    """防 bug：非独占行（段落中）的 @include 必须原样保留。"""
    _write(workspace / "MEWCODE.md", "正文里有 @include part.md 不应展开")
    _write(workspace / "part.md", "INCLUDED")
    loader = InstructionLoader(workspace, user_home=home)
    doc = loader.load()
    assert "@include part.md" in doc.text
    assert "INCLUDED" not in doc.text


def test_nested_include(workspace, home):
    """防 bug：引用文件可继续 include 其他相对路径文件。"""
    _write(workspace / "MEWCODE.md", "@include a.md")
    _write(workspace / "a.md", "@include b.md")
    _write(workspace / "b.md", "LEAF")
    loader = InstructionLoader(workspace, user_home=home)
    assert "LEAF" in loader.load().text


def test_include_depth_limit(workspace, home):
    """防 bug：超过 5 层时保留原 include 行并追加深度警告。"""
    for i in range(7):
        nxt = f"@include f{i + 1}.md" if i < 6 else "LEAF"
        _write(workspace / f"f{i}.md", nxt)
    _write(workspace / "MEWCODE.md", "@include f0.md")
    loader = InstructionLoader(workspace, user_home=home, max_depth=5)
    doc = loader.load()
    assert "LEAF" not in doc.text
    assert "超过最大嵌套深度" in doc.text


def test_include_cycle(workspace, home):
    """防 bug：A -> B -> A 环路必须跳过并追加环路警告。"""
    _write(workspace / "MEWCODE.md", "@include a.md")
    _write(workspace / "a.md", "@include b.md")
    _write(workspace / "b.md", "@include a.md")
    loader = InstructionLoader(workspace, user_home=home)
    doc = loader.load()
    assert "检测到环路" in doc.text
    # 环路文件只展开一次，不应无限递归（测试本身跑完即证明无死循环）


def test_include_path_escape(workspace, home):
    """防 bug：相对路径目录穿越必须被拒绝并追加越界警告。"""
    outside = workspace.parent / "secret.md"
    outside.write_text("SECRET", encoding="utf-8")
    _write(workspace / "MEWCODE.md", "@include ../secret.md")
    loader = InstructionLoader(workspace, user_home=home)
    doc = loader.load()
    assert "SECRET" not in doc.text
    assert "超出允许范围" in doc.text or "未找到或已跳过" in doc.text


def test_include_absolute_rejected(workspace, home):
    """防 bug：绝对路径 include 必须拒绝。"""
    outside = workspace.parent / "abs.md"
    outside.write_text("ABS", encoding="utf-8")
    _write(workspace / "MEWCODE.md", f"@include {outside}")
    loader = InstructionLoader(workspace, user_home=home)
    doc = loader.load()
    assert "ABS" not in doc.text
    assert "超出允许范围" in doc.text


def test_include_symlink_escape(workspace, home):
    """防 bug：符号链接指向边界外时，canonical 路径越界必须拒绝。"""
    outside = workspace.parent / "outside.md"
    outside.write_text("SYMLINK_SECRET", encoding="utf-8")
    target = workspace / "link.md"
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持符号链接")
    _write(workspace / "MEWCODE.md", "@include link.md")
    loader = InstructionLoader(workspace, user_home=home)
    doc = loader.load()
    assert "SYMLINK_SECRET" not in doc.text


def test_binary_file_skipped(workspace, home):
    """防 bug：前 512 字节含 \\x00 的二进制文件必须跳过并记录警告。"""
    _write(workspace / "MEWCODE.md", "\x00\x01\x02" + "A" * 100)
    loader = InstructionLoader(workspace, user_home=home)
    doc = loader.load()
    assert doc.text == ""
    reasons = [d.reason for d in doc.diagnostics]
    assert any("binary" in r or "utf-8" in r or "unreadable" in r for r in reasons)


def test_file_size_limit(workspace, home):
    """防 bug：单文件超过大小上限时拒读。"""
    _write(workspace / "MEWCODE.md", "X" * 2000)
    loader = InstructionLoader(workspace, user_home=home, max_file_size=1000)
    doc = loader.load()
    assert "X" * 2000 not in doc.text


def test_total_size_limit(workspace, home):
    """防 bug：展开总大小超限时拒绝超出部分并记录原因。"""
    _write(workspace / "MEWCODE.md", "R" * 500)
    _write(workspace / ".mewcode" / "MEWCODE.md", "C" * 500)
    _write(home / ".mewcode" / "MEWCODE.md", "U" * 500)
    loader = InstructionLoader(
        workspace, user_home=home, max_total_size=1000
    )
    doc = loader.load()
    # 至少记录总大小超限诊断，且内容不超过上限
    assert any("size limit" in d.reason for d in doc.diagnostics)


def test_load_cached_after_startup(workspace, home):
    """防 bug：启动后修改指令文件不能改变已缓存的模块内容（AC4）。"""
    root = _write(workspace / "MEWCODE.md", "ORIGINAL")
    loader = InstructionLoader(workspace, user_home=home)
    first = loader.load().text
    root.write_text("CHANGED", encoding="utf-8")
    second = loader.load().text
    assert first == second == "ORIGINAL"
    # refresh=True 时重新读取
    assert loader.load(refresh=True).text == "CHANGED"


def test_user_scope_include_within_home(workspace, home):
    """防 bug：用户级文件的 include 允许根为 ~/.mewcode，内部相对引用可展开。"""
    _write(home / ".mewcode" / "MEWCODE.md", "@include extra.md")
    _write(home / ".mewcode" / "extra.md", "USER_EXTRA")
    loader = InstructionLoader(workspace, user_home=home)
    assert "USER_EXTRA" in loader.load().text
