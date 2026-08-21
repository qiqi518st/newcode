"""内置命令 handler 单测（T4 RecordingUI + T7/T8/T9）：NopUI/RecordingUI 驱动真实 handler 路径。

防的 bug：命令把 TUI 内部属性外泄、状态查询读错数据源（token/工具数/记忆数/模型/目录）、
/permission_add 写错层或立即生效失败、/memory_add 文件名非法、/clear 未走原子重置、
/do 执行标记丢失导致 plan 不被 mark_executed（AC8/AC9/AC16）。
"""

import asyncio
import tempfile
from pathlib import Path

from mewcode.memory import MemoryManager
from mewcode.permission.checker import PermissionChecker
from mewcode.permission.modes import PermissionMode
from mewcode.permission.rules import RuleLayers
from mewcode.plans.manager import PlanManager
from mewcode.slash import CommandContext, CommandRegistry, NopUI, RecordingUI
from mewcode.slash.commands import register_all
from mewcode.slash.commands.memory import _slugify


def _registered() -> CommandRegistry:
    reg = CommandRegistry()
    register_all(reg)
    return reg


def _ctx(
    ui, plan_mgr=None, mem=None, perm=None, runtime=None, archive=None
) -> CommandContext:
    return CommandContext(
        registry=_registered(),
        ui=ui,
        agent=None,
        conversation=None,
        plan_manager=plan_mgr or PlanManager(tempfile.mkdtemp()),
        session_runtime=runtime,
        session_archive=archive,
        memory_manager=mem,
        permission=perm,
        version="0.10.0",
        cwd=".",
    )


async def _run(reg, name, ctx, args=""):
    cmd = reg.get(name)
    assert cmd is not None, f"命令 {name} 未注册"
    await cmd.handler(ctx, args)


class PickUI(RecordingUI):
    """RecordingUI 变体：choose/choose_multi 返回预设值（模拟用户选择）。"""

    def __init__(self, pick: str | None = None, picks: list[str] | None = None):
        super().__init__()
        self._pick = pick
        self._picks = list(picks or [])

    async def choose(self, question, options, default_index=0):
        self._record("choose", question, *[o[0] for o in options])
        return self._pick

    async def choose_multi(self, question, options):
        self._record("choose_multi", question, *[o[0] for o in options])
        return list(self._picks)


# ── 注册装配（T6）──────────────────────────────────────────


def test_register_builtins_all_registered():
    reg = _registered()
    names = [c.name for c in reg.list()]
    for expected in [
        "help",
        "status",
        "memory",
        "memory_list",
        "memory_add",
        "memory_clear",
        "permission",
        "permission_rules",
        "permission_add",
        "permission_reset",
        "session",
        "session_list",
        "session_resume",
        "session_new",
        "plan",
        "normal",
        "do",
        "clear",
        "compact",
        "skill",
        "exit",
        "delete-plan",
    ]:
        assert expected in names, f"缺少命令 {expected}"
    # /quit 别名与 /resume 隐藏命令仍可命中
    assert reg.get("quit").name == "exit"
    assert reg.get("resume").hidden is True


def test_register_builtins_no_collision():
    _registered()  # register_all 不抛即无冲突


def test_all_handlers_run_on_nop_ui():
    """NopUI 下逐个 handler 可 await 不抛（含无参退化调用）。"""
    reg = _registered()
    ui = NopUI()
    ctx = _ctx(ui)
    for name in [c.name for c in reg.list(include_hidden=True)]:
        asyncio.run(_run(reg, name, ctx, args=""))


# ── /help（F8.8/AC1）───────────────────────────────────────


def test_help_lists_all_visible_commands_aligned():
    ui = RecordingUI()
    asyncio.run(_run(_registered(), "help", _ctx(ui)))
    assert len(ui.messages) == 1
    text = ui.messages[0]
    for name in ["clear", "help", "status", "delete-plan"]:
        assert f"/{name}" in text, f"/help 缺 {name}"
    assert "/resume" not in text  # hidden 不出现


# ── /status（F8.9/AC4/N6）──────────────────────────────────


def test_status_prints_all_keys():
    ui = RecordingUI()
    ui._tokens = (100, 50)
    ui._memory_files = ["a.md"]
    asyncio.run(_run(_registered(), "status", _ctx(ui)))
    text = ui.messages[-1]
    for key in ["Mode", "Tokens", "Tools", "Memories", "Model", "Directory"]:
        assert f"{key}" in text, f"缺 key {key}"
    assert "100 in / 50 out" in text
    assert "1 files" in text  # Memories 值取自 query_memory_files


# ── /permission（F8.11）────────────────────────────────────


def test_permission_prints_mode():
    ui = RecordingUI()
    ui.set_permission_mode("acceptEdits")
    asyncio.run(_run(_registered(), "permission", _ctx(ui)))
    assert ui.messages[-1] == "acceptEdits"


# ── /permission_rules / _add / _reset（F8.17-F8.19）────────


def _perm(tmp: str) -> PermissionChecker:
    return PermissionChecker(
        project_root=tmp,
        mode=PermissionMode.DEFAULT,
        layers=RuleLayers(),
    )


def test_permission_add_and_rules(tmp_path):
    checker = _perm(str(tmp_path))
    ui = RecordingUI()
    ctx = _ctx(ui, perm=checker)
    asyncio.run(_run(_registered(), "permission_add", ctx, "Bash(git *) allow"))
    assert checker.count_rules() == 1
    asyncio.run(_run(_registered(), "permission_rules", ctx))
    assert "local allow Bash(git *)" in ui.messages[-1]
    # 立即生效：check() 命中新规则
    from mewcode.provider.base import ToolCall

    result = checker.check(ToolCall("execute_command", {"command": "git status"}))
    assert result.decision.value == "allow"


def test_permission_add_immediate_effect_on_checker():
    """add_rule 后同进程 check() 立即命中（防"重启才生效"的静默失效 bug）。"""
    checker = _perm(tempfile.mkdtemp())
    checker.add_rule("Bash(git *)", "allow")
    from mewcode.provider.base import ToolCall

    r = checker.check(ToolCall("execute_command", {"command": "git pull"}))
    assert r.decision.value == "allow"
    # 无关命令不受新规则影响（不会误 allow）
    r2 = checker.check(ToolCall("execute_command", {"command": "echo hi"}))
    assert r2.decision.value != "allow"


def test_permission_reset_clears(tmp_path):
    checker = _perm(str(tmp_path))
    checker.add_rule("Bash(git *)", "allow")
    checker.add_rule("Read", "deny")
    ui = RecordingUI()
    ctx = _ctx(ui, perm=checker)
    asyncio.run(_run(_registered(), "permission_reset", ctx))
    assert checker.count_rules() == 0
    assert "2" in ui.messages[-1]  # 返回删除条数


def test_permission_add_bad_effect():
    ui = RecordingUI()
    ctx = _ctx(ui, perm=_perm(tempfile.mkdtemp()))
    asyncio.run(_run(_registered(), "permission_add", ctx, "Read maybe"))
    assert "allow 或 deny" in ui.messages[-1]


# ── /memory 家族（F8.10/F8.14-F8.16）───────────────────────


def _mem(tmp_path: Path) -> MemoryManager:
    return MemoryManager(tmp_path / "proj", tmp_path / "user")


def test_memory_empty(tmp_path):
    ui = RecordingUI()
    asyncio.run(_run(_registered(), "memory", _ctx(ui, mem=_mem(tmp_path))))
    assert "无已加载的记忆文件" in ui.messages[-1]


def test_memory_add_list_clear_roundtrip(tmp_path):
    mem = _mem(tmp_path)
    ui = RecordingUI()
    ctx = _ctx(ui, mem=mem)
    reg = _registered()
    asyncio.run(_run(reg, "memory_add", ctx, "user_preference 记住 tea"))
    assert "已添加记忆" in ui.messages[-1]
    assert len(mem.list_notes()) == 1
    asyncio.run(_run(reg, "memory_list", ctx))
    assert "user_preference" in ui.messages[-1]
    asyncio.run(_run(reg, "memory_clear", ctx))
    assert mem.list_notes() == []
    assert "已清空" in ui.messages[-1]


def test_memory_add_bad_type(tmp_path):
    ui = RecordingUI()
    ctx = _ctx(ui, mem=_mem(tmp_path))
    asyncio.run(_run(_registered(), "memory_add", ctx, "badtype 内容"))
    assert "未知记忆类型" in ui.messages[-1]


def test_slugify():
    assert _slugify("记住 tea") == "note"  # 首词为中文 → 无 [a-z0-9] → 回退 note
    assert _slugify("hello world") == "hello"
    assert _slugify("") == "note"
    assert _slugify("  ") == "note"


# ── /session 家族（F8.12/F8.20-F8.22）──────────────────────


class _FakeSessionContext:
    def __init__(self, sid, conv_path):
        self.session_id = sid
        self.conversation_path = conv_path


class _FakeWriter:
    def __init__(self, path):
        self.path = path


class _FakeRuntime:
    def __init__(self, sid="20260820-120000-abcd", path="/tmp/x/conversation.jsonl"):
        self.context = _FakeSessionContext(sid, path)
        self.writer = _FakeWriter(Path(path))
        self.conversation = None

    async def resume(self, session_id):
        return None

    def create_new(self):
        return None


class _FakeArchive:
    def __init__(self, rows=None):
        self._rows = rows or []

    def list(self):
        return list(self._rows)


class _FakeSummary:
    def __init__(self, session_id, title=""):
        self.session_id = session_id
        self.title = title
        self.model = ""
        self.message_count = 0


def test_session_prints_path_and_id():
    runtime = _FakeRuntime()
    ui = RecordingUI()
    asyncio.run(_run(_registered(), "session", _ctx(ui, runtime=runtime)))
    assert "20260820-120000-abcd" in ui.messages[-1]
    assert "conversation.jsonl" in ui.messages[-1]


def test_session_no_runtime():
    ui = RecordingUI()
    asyncio.run(_run(_registered(), "session", _ctx(ui)))
    assert "未启用" in ui.messages[-1]


def test_session_resume_calls_ui():
    ui = RecordingUI()
    runtime = _FakeRuntime()
    archive = _FakeArchive([_FakeSummary("20260820-120000-abcd")])
    asyncio.run(
        _run(
            _registered(),
            "session_resume",
            _ctx(ui, runtime=runtime, archive=archive),
            "20260820-120000-abcd",
        )
    )
    assert ("resume_session", "20260820-120000-abcd") in ui.calls
    assert "已恢复会话" in ui.messages[-1]


def test_session_resume_missing_id():
    ui = RecordingUI()
    archive = _FakeArchive([_FakeSummary("20260820-120000-zzzz")])
    asyncio.run(
        _run(_registered(), "session_resume", _ctx(ui, archive=archive), "nope")
    )
    assert "未找到会话" in ui.messages[-1]


def test_session_new_calls_ui():
    ui = RecordingUI()
    asyncio.run(_run(_registered(), "session_new", _ctx(ui, runtime=_FakeRuntime())))
    assert ("new_session",) in ui.calls
    assert "已创建新会话" in ui.messages[-1]


# ── /plan / /normal（F8.2/F8.3）────────────────────────────


def test_plan_sets_modes():
    ui = RecordingUI()
    asyncio.run(_run(_registered(), "plan", _ctx(ui)))
    assert ("set_app_mode", "plan") in ui.calls
    assert ("set_permission_mode", "plan") in ui.calls


def test_plan_with_args_runs_agent():
    ui = RecordingUI()
    asyncio.run(_run(_registered(), "plan", _ctx(ui), "创建 hello.txt"))
    assert ("run_agent", "创建 hello.txt", "plan", "", "") in ui.calls


def test_normal_sets_app_mode():
    ui = RecordingUI()
    asyncio.run(_run(_registered(), "normal", _ctx(ui)))
    assert ("set_app_mode", "normal") in ui.calls


# ── /do（F8.4）─────────────────────────────────────────────


def test_do_with_slug_executes(tmp_path):
    pm = PlanManager(str(tmp_path))
    content = "<!-- slug: my-plan -->\n# 任务\n执行它"
    slug = pm.create_plan("任务", content)
    ui = RecordingUI()
    ctx = _ctx(ui, plan_mgr=pm)
    asyncio.run(_run(_registered(), "do", ctx, slug))
    assert ("run_agent", "", "execute", content, "my-plan") in ui.calls
    assert any("my-plan" in m for m in ui.messages)  # plan 信息打印


def test_do_missing_slug(tmp_path):
    ui = RecordingUI()
    asyncio.run(
        _run(_registered(), "do", _ctx(ui, plan_mgr=PlanManager(str(tmp_path))), "nope")
    )
    assert "未找到计划" in ui.messages[-1]


def test_do_no_arg_selection(tmp_path):
    pm = PlanManager(str(tmp_path))
    content = "<!-- slug: plan-a -->\n# A\n内容"
    pm.create_plan("任务A", content)
    ui = PickUI(pick="plan-a")
    ctx = _ctx(ui, plan_mgr=pm)
    asyncio.run(_run(_registered(), "do", ctx))
    assert ("run_agent", "", "execute", content, "plan-a") in ui.calls


def test_do_no_arg_cancel(tmp_path):
    pm = PlanManager(str(tmp_path))
    pm.create_plan("任务A", "<!-- slug: plan-a -->\n# A\n内容")
    ui = RecordingUI()
    asyncio.run(_run(_registered(), "do", _ctx(ui, plan_mgr=pm)))
    assert "已取消" in ui.messages[-1]


# ── /clear / /compact（F8.7/F8.5）──────────────────────────


def test_clear_request_and_notice():
    ui = RecordingUI()
    asyncio.run(_run(_registered(), "clear", _ctx(ui)))
    assert ("request_clear_session",) in ui.calls
    assert "已清空当前会话" in ui.messages[-1]


def test_compact_request():
    ui = RecordingUI()
    asyncio.run(_run(_registered(), "compact", _ctx(ui)))
    assert ("request_compact",) in ui.calls


# ── /review 迁移（ch11 F6.4）───────────────────────────────


def test_review_removed_from_builtin_commands():
    """ch11 F6.4：/review 从硬编码内置命令移除，由 review Skill（fork）自动注册接管。

    防 bug：旧的 send_user_message 注入式 /review 已被 Skill 框架取代，
    内置命令不再包含 review，新增 /skill 管理命令。
    """
    reg = _registered()
    assert reg.get("review") is None
    assert reg.get("skill") is not None


# ── /exit / /delete-plan（F8.1/F8.23）──────────────────────


def test_exit_requests():
    ui = RecordingUI()
    asyncio.run(_run(_registered(), "exit", _ctx(ui)))
    assert ("request_exit",) in ui.calls
    assert "再见" in ui.messages[-1]


def test_delete_plan_flow(tmp_path):
    pm = PlanManager(str(tmp_path))
    pm.create_plan("A", "<!-- slug: plan-a -->\n# A")
    pm.create_plan("B", "<!-- slug: plan-b -->\n# B")
    ui = PickUI(picks=["plan-a", "plan-b"])
    ctx = _ctx(ui, plan_mgr=pm)
    # choose(确认) 也返回 'yes' —— PickUI 单值 choose 返回 _pick
    ui._pick = "yes"
    asyncio.run(_run(_registered(), "delete-plan", ctx))
    remaining = {p.slug for p in pm.list_plans()}
    assert remaining == set()
    assert "已删除 2 个计划" in ui.messages[-1]


def test_delete_plan_cancel(tmp_path):
    pm = PlanManager(str(tmp_path))
    pm.create_plan("A", "<!-- slug: plan-a -->\n# A")
    ui = RecordingUI()  # choose_multi 返回 None → 取消
    asyncio.run(_run(_registered(), "delete-plan", _ctx(ui, plan_mgr=pm)))
    assert "已取消" in ui.messages[-1]
    assert len(pm.list_plans()) == 1
