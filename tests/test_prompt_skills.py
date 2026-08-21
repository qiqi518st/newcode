"""prompt/skills_block 渲染单测（T23）：catalog 块 / active 块 / 空块。

防的 bug：空列表时输出非空串（装配层会错误拼进 env，向后兼容 N10 被破坏）；
active 块漏 body；多 Skill 并存顺序错乱。
"""

from mewcode.prompt.skills_block import (
    ActiveSkillEntry,
    SkillCatalogItem,
    render_active_skills_block,
    render_skills_catalog,
)


def test_catalog_block_empty_returns_empty_string():
    """防 bug：无 Skill 时返回空串，装配层跳过该段（N10 与 ch10 行为一致）。"""
    assert render_skills_catalog([]) == ""


def test_active_block_empty_returns_empty_string():
    assert render_active_skills_block([]) == ""


def test_catalog_block_lists_name_desc_and_guide():
    items = [
        SkillCatalogItem(name="commit", description="Commit changes"),
        SkillCatalogItem(name="review", description="Review code"),
    ]
    out = render_skills_catalog(items)
    assert "## Available Skills" in out
    assert "- commit: Commit changes" in out
    assert "- review: Review code" in out
    assert "load_skill" in out  # F4.1 调用指引


def test_active_block_contains_full_body():
    """防 bug：激活块必须含完整 SOP body（F5.2），否则阶段二注入失效。"""
    entries = [
        ActiveSkillEntry(name="review", body="Review all the code\nin five dims"),
        ActiveSkillEntry(name="test", body="Run pytest"),
    ]
    out = render_active_skills_block(entries)
    assert "## Active Skills" in out
    assert "### Skill: review" in out
    assert "Review all the code\nin five dims" in out
    assert "### Skill: test" in out
    # 顺序保持激活顺序
    assert out.index("### Skill: review") < out.index("### Skill: test")
