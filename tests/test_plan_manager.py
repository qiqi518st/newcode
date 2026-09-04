"""PlanManager 单元测试

背景：Plan 文件管理功能引入 .meta.json 索引和独立 slug 命名。
测试覆盖：slug 提取/回退/清洗、CRUD、执行标记、清理、自愈、损坏容错。
"""

import json
from datetime import datetime, timedelta, timezone

from newcode.plans import PlanManager, PlanMeta

CONTENT_WITH_SLUG = "<!-- slug: my-plan -->\n# 我的计划\n- [ ] 步骤一\n- [ ] 步骤二\n"

CONTENT_NO_SLUG = "# 我的计划\n- [ ] 步骤一\n"


# ── slug 提取 ──


def test_extract_slug_from_comment():
    m = PlanManager("/tmp/p")
    assert m._extract_slug("<!-- slug: my-plan -->\n# 计划") == "my-plan"


def test_extract_slug_sanitize():
    """特殊字符应被替换为连字符，大写转小写"""
    m = PlanManager("/tmp/p")
    assert m._extract_slug("<!-- slug:  Hello_World! -->") == "hello-world"


def test_extract_slug_fallback():
    """无 slug 注释时回退日期格式"""
    m = PlanManager("/tmp/p")
    slug = m._extract_slug("# 无 slug 计划")
    assert slug.startswith("plan-")


def test_extract_slug_empty_comment():
    """slug 注释为空时回退日期格式"""
    m = PlanManager("/tmp/p")
    slug = m._extract_slug("<!-- slug: -->\n# 计划")
    assert slug.startswith("plan-")


# ── task 提取 ──


def test_extract_task_from_heading():
    m = PlanManager("/tmp/p")
    assert m._extract_task("# 创建登录页\n- [ ] 步骤") == "创建登录页"


def test_extract_task_fallback():
    """无标题时取首行非注释内容"""
    m = PlanManager("/tmp/p")
    assert m._extract_task("<!-- slug: x -->\n首行内容\n更多") == "首行内容"


# ── 创建与读取 ──


def test_create_plan_writes_file_and_meta(tmp_path):
    m = PlanManager(str(tmp_path))
    slug = m.create_plan("", CONTENT_WITH_SLUG)

    assert slug == "my-plan"
    assert (tmp_path / "my-plan.md").exists()
    meta = json.loads((tmp_path / ".meta.json").read_text(encoding="utf-8"))
    assert meta["my-plan"]["file"] == "my-plan.md"
    assert meta["my-plan"]["task"] == "我的计划"
    assert meta["my-plan"]["executed_at"] is None


def test_create_plan_fallback_filename(tmp_path):
    """无 slug 时用日期文件名"""
    m = PlanManager(str(tmp_path))
    slug = m.create_plan("", CONTENT_NO_SLUG)
    assert (tmp_path / f"{slug}.md").exists()


def test_list_plans_sorted(tmp_path):
    m = PlanManager(str(tmp_path))
    m.create_plan("", "<!-- slug: b-plan -->\n# B 计划")
    m.create_plan("", "<!-- slug: a-plan -->\n# A 计划")

    # 手动调整 created_at 模拟时间差
    meta = json.loads((tmp_path / ".meta.json").read_text(encoding="utf-8"))
    meta["a-plan"]["created_at"] = "2020-01-01T00:00:00"
    (tmp_path / ".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )

    plans = m.list_plans()
    assert [p.slug for p in plans] == ["b-plan", "a-plan"]  # 新→旧


def test_get_plan_found(tmp_path):
    m = PlanManager(str(tmp_path))
    m.create_plan("", CONTENT_WITH_SLUG)
    p = m.get_plan("my-plan")
    assert p is not None
    assert p.file == "my-plan.md"
    assert p.task == "我的计划"
    assert p.executed is False


def test_get_plan_not_found(tmp_path):
    m = PlanManager(str(tmp_path))
    assert m.get_plan("nonexistent") is None


def test_read_plan_content(tmp_path):
    m = PlanManager(str(tmp_path))
    m.create_plan("", CONTENT_WITH_SLUG)
    content = m.read_plan_content("my-plan")
    assert "步骤一" in content


def test_read_plan_content_missing(tmp_path):
    m = PlanManager(str(tmp_path))
    assert m.read_plan_content("nonexistent") == ""


# ── 执行标记 ──


def test_mark_executed(tmp_path):
    m = PlanManager(str(tmp_path))
    m.create_plan("", CONTENT_WITH_SLUG)
    m.mark_executed("my-plan")
    p = m.get_plan("my-plan")
    assert p.executed is True
    assert p.executed_at is not None


def test_mark_executed_twice_refreshes(tmp_path):
    """重复执行刷新 executed_at"""
    m = PlanManager(str(tmp_path))
    m.create_plan("", CONTENT_WITH_SLUG)
    m.mark_executed("my-plan")
    first = m.get_plan("my-plan").executed_at
    m.mark_executed("my-plan")
    second = m.get_plan("my-plan").executed_at
    assert second >= first


# ── 删除 ──


def test_delete_plans(tmp_path):
    m = PlanManager(str(tmp_path))
    m.create_plan("", "<!-- slug: a-plan -->\n# A")
    m.create_plan("", "<!-- slug: b-plan -->\n# B")

    m.delete_plans(["a-plan"])

    assert not (tmp_path / "a-plan.md").exists()
    assert (tmp_path / "b-plan.md").exists()
    meta = json.loads((tmp_path / ".meta.json").read_text(encoding="utf-8"))
    assert "a-plan" not in meta
    assert "b-plan" in meta


def test_delete_plans_missing_file(tmp_path):
    """元数据存在但文件已被删，删除不崩溃"""
    m = PlanManager(str(tmp_path))
    m.create_plan("", CONTENT_WITH_SLUG)
    (tmp_path / "my-plan.md").unlink()
    m.delete_plans(["my-plan"])  # 不应抛异常
    assert m.get_plan("my-plan") is None


# ── 清理 ──


def test_cleanup_old(tmp_path):
    m = PlanManager(str(tmp_path))
    m.create_plan("", "<!-- slug: old-plan -->\n# 旧")
    m.create_plan("", "<!-- slug: new-plan -->\n# 新")

    # 把 old-plan 的 created_at 改到 40 天前
    old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(
        timespec="seconds"
    )
    meta = json.loads((tmp_path / ".meta.json").read_text(encoding="utf-8"))
    meta["old-plan"]["created_at"] = old_ts
    (tmp_path / ".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )

    deleted = m.cleanup_old(days=30)

    assert deleted == 1
    assert not (tmp_path / "old-plan.md").exists()
    assert (tmp_path / "new-plan.md").exists()


def test_cleanup_old_zero(tmp_path):
    m = PlanManager(str(tmp_path))
    m.create_plan("", CONTENT_WITH_SLUG)
    assert m.cleanup_old(days=0) == 0
    assert m.get_plan("my-plan") is not None


def test_cleanup_old_no_expired(tmp_path):
    m = PlanManager(str(tmp_path))
    m.create_plan("", CONTENT_WITH_SLUG)
    assert m.cleanup_old(days=30) == 0


# ── 容错与自愈 ──


def test_meta_corrupted(tmp_path):
    """.meta.json 损坏时降级为空列表，不崩溃"""
    (tmp_path / ".meta.json").write_text("{{{invalid json", encoding="utf-8")
    m = PlanManager(str(tmp_path))
    assert m.list_plans() == []


def test_meta_corrupted_write_self_heals(tmp_path):
    """损坏的 .meta.json 在下次写入时被重建"""
    (tmp_path / ".meta.json").write_text("{{{invalid", encoding="utf-8")
    m = PlanManager(str(tmp_path))
    m.create_plan("", CONTENT_WITH_SLUG)
    # 重建后的 meta 应只包含新 plan
    assert m.get_plan("my-plan") is not None


def test_self_heal_removes_stale_entry(tmp_path):
    """.md 文件被手动删除后，list_plans 自动清除对应条目"""
    m = PlanManager(str(tmp_path))
    m.create_plan("", "<!-- slug: a-plan -->\n# A")
    m.create_plan("", "<!-- slug: b-plan -->\n# B")

    # 手动删除 a-plan 的 .md 文件
    (tmp_path / "a-plan.md").unlink()

    plans = m.list_plans()
    assert [p.slug for p in plans] == ["b-plan"]
    meta = json.loads((tmp_path / ".meta.json").read_text(encoding="utf-8"))
    assert "a-plan" not in meta


def test_plan_meta_dataclass():
    """PlanMeta.executed 属性"""
    p = PlanMeta(slug="s", file="s.md", task="t", created_at="2026")
    assert p.executed is False
    p.executed_at = "2026-08-08T00:00:00"
    assert p.executed is True
