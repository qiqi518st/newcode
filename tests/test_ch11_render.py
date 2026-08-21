"""Skill 正文渲染单测（T23）：$ARGUMENTS 替换、## User Request 兜底、allowed_tools 提示。

防的 bug：$ARGUMENTS 只替换第一处（要全部替换）；有占位符时不加 User Request 兜底
（兜底仅限无占位符）；allowed_tools 提示插错位置（应在顶部）或未插入。
"""

from pathlib import Path

from mewcode.skills.render import render_body
from mewcode.skills.types import Skill, SkillMeta, SkillSource

_DIR = Path(".")


def _skill(body: str, allowed: list[str] | None = None) -> Skill:
    return Skill(
        meta=SkillMeta(name="t", description="d", allowed_tools=list(allowed or [])),
        prompt_body=body,
        source_dir=_DIR,
        source=SkillSource.BUILTIN,
    )


def test_substitute_single_placeholder():
    sk = _skill("Review this: $ARGUMENTS")
    assert render_body(sk, "src/main.py") == "Review this: src/main.py"


def test_substitute_multiple_placeholders():
    """防 bug：body 多处 $ARGUMENTS 必须全部替换（不能只换第一处）。"""
    sk = _skill("A: $ARGUMENTS\nB: $ARGUMENTS")
    out = render_body(sk, "x")
    assert out == "A: x\nB: x"


def test_substitute_empty_args():
    """自然语言触发（load_skill 仅传 name）时 $ARGUMENTS 替换为空串（F3.3）。"""
    sk = _skill("do: $ARGUMENTS")
    assert render_body(sk, "") == "do: "


def test_no_placeholder_with_args_appends_user_request():
    """防 bug：无占位符但调用带 args → 末尾兜底追加 ## User Request（显式 /name args）。"""
    sk = _skill("Follow the steps.")
    out = render_body(sk, "focus on auth")
    assert out == "Follow the steps.\n\n## User Request\n\nfocus on auth"


def test_no_placeholder_no_args_returns_body_unchanged():
    sk = _skill("Follow the steps.")
    assert render_body(sk, "") == "Follow the steps."


def test_allowed_tools_hint_inserted_at_top():
    """防 bug：allowed_tools 提示段在 body 顶部（渐进式声明，F3.4），不真过滤。"""
    sk = _skill("Do the work", allowed=["read_file", "search_code"])
    out = render_body(sk, "")
    assert out.startswith(
        "This skill is designed to use only these tools: read_file, search_code."
    )
    assert "Prefer them" in out
    assert out.endswith("Do the work")


def test_hint_then_placeholder_both_applied():
    sk = _skill("handle: $ARGUMENTS", allowed=["read_file"])
    out = render_body(sk, "abc")
    assert out.startswith("This skill is designed to use only these tools: read_file.")
    assert out.endswith("handle: abc")
    assert "## User Request" not in out  # 有占位符 → 不追加兜底
