"""ch11 集成测试（T26）：两阶段加载 / 意图触发 load_skill / fork 工具集收窄 / /clear / 热重载。

用 mock provider 驱动真实 Agent.run 路径（无真实 API/终端，CLAUDE.md 接线测试规范）。

防的 bug：阶段一 env 泄漏完整 SOP（F4.1）；load_skill 触发后 body 未进 env（F4.2）；
意图触发时 load_skill 未被模型执行（工具调用未落地）；fork 工具集未收窄（F3.7）；
/clear 未清 activeSkills（F5.5）；热重载后旧 body 残留（N7）。
"""

from pathlib import Path

import pytest

from mewcode.agent.agent import Agent
from mewcode.agent.events import EventType
from mewcode.conversation.manager import ConversationManager
from mewcode.provider.base import StreamEvent, ToolCall
from mewcode.skills import ActiveSkills, Catalog
from mewcode.tools import Registry
from mewcode.tools.load_skill import LoadSkillTool

pytestmark = pytest.mark.anyio


def _skill_md(name: str, body: str = "BODY") -> str:
    return f"---\nname: {name}\ndescription: {name} desc\n---\n{body}"


def _catalog(tmp_path: Path) -> Catalog:
    user = tmp_path / "user"
    user.mkdir(exist_ok=True)
    (user / "commit.md").write_text(_skill_md("commit", "COMMIT SOP"), encoding="utf-8")
    (user / "review.md").write_text(
        "---\nname: review\ndescription: review desc\nmode: fork\n---\nREVIEW SOP",
        encoding="utf-8",
    )
    return Catalog.load(
        project_dir=tmp_path / "proj",
        user_skills_dir=user,
        builtin_dir=tmp_path / "builtin",
    )


class RecordingProvider:
    """记录每次请求 payload，按调用顺序弹事件队列。"""

    model = "fake-model"
    name = "fake"

    def __init__(self, queues):
        self._queues = list(queues)
        self.payloads: list = []
        self.stream_calls = 0

    def stream(self, payload):
        self.payloads.append(payload)
        self.stream_calls += 1
        q = self._queues.pop(0) if self._queues else []

        async def gen():
            for se in q:
                yield se

        return gen()


def _setup(tmp_path: Path):
    catalog = _catalog(tmp_path)
    store = ActiveSkills()
    registry = Registry()
    tool = LoadSkillTool(catalog, store, registry)
    registry.register(tool)
    return catalog, store, registry


def _make_agent(provider, registry, store, catalog, env_segment: str = "BASE ENV"):
    conv = ConversationManager(20)
    agent = Agent(
        provider,
        conv,
        registry,
        stable_prompt="STABLE",
        env_segment=env_segment,
        active_skills=store,
        is_interactive=False,
    )
    agent.with_catalog(catalog)
    return agent, conv


def _done_queue(text: str, in_t: int = 0, out_t: int = 0):
    return [StreamEvent(text=text), StreamEvent(done=True)]


# ── 两阶段加载（F4.1/F4.2）─────────────────────────────────


async def test_stage1_env_has_catalog_no_body(tmp_path):
    """防 bug：启动 env 含 Available Skills 摘要（name+description）但不含完整 SOP。"""
    catalog, store, registry = _setup(tmp_path)
    provider = RecordingProvider([_done_queue("hello")])
    agent, _ = _make_agent(provider, registry, store, catalog)
    async for _ in agent.run("hi"):
        pass
    env = provider.payloads[0].env_segment
    assert "Available Skills" in env
    assert "commit: commit desc" in env
    assert "COMMIT SOP" not in env  # 阶段一不含完整 body


async def test_load_skill_pins_full_body_to_env(tmp_path):
    """防 bug：load_skill 激活后下一轮 env 含完整 SOP，且完整 SOP 不在消息历史（AC6b）。"""
    catalog, store, registry = _setup(tmp_path)
    # 第一轮：Agent 调 load_skill；第二轮：继续
    tool_call_queue = [
        StreamEvent(text="I'll use the commit skill"),
        StreamEvent(
            tool_call=ToolCall(tool_name="load_skill", arguments={"name": "commit"})
        ),
        StreamEvent(done=True),
    ]
    provider = RecordingProvider([tool_call_queue, _done_queue("committed!")])
    agent, conv = _make_agent(provider, registry, store, catalog)
    events = [e async for e in agent.run("帮我提交一下")]
    # 工具被执行 → Skill 进入激活态
    assert store.names() == ["commit"]
    # 第二轮请求 env 含完整 SOP
    assert provider.stream_calls >= 2
    env2 = provider.payloads[1].env_segment
    assert "COMMIT SOP" in env2
    # 完整 SOP 不在消息历史（F4.4）
    history = "".join(m.content or "" for m in conv.get_context())
    assert "COMMIT SOP" not in history
    # 会话正常结束
    assert any(e.type == EventType.DONE for e in events)


# ── 意图触发（AC5）─────────────────────────────────────────


async def test_intent_load_skill_tool_executed(tmp_path):
    """防 bug：模型输出 load_skill 工具调用时，真实工具路径被执行并激活 Skill。"""
    catalog, store, registry = _setup(tmp_path)
    q = [
        StreamEvent(
            tool_call=ToolCall(tool_name="load_skill", arguments={"name": "commit"})
        ),
        StreamEvent(done=True),
    ]
    provider = RecordingProvider([q, _done_queue("ok")])
    agent, _ = _make_agent(provider, registry, store, catalog)
    async for _ in agent.run("帮我提交"):
        pass
    assert store.names() == ["commit"]


# ── fork 工具集收窄（F3.7/A 决策）──────────────────────────


def test_fork_toolset_narrowed_by_allowedtools(tmp_path):
    """防 bug：fork 收窄只含系统工具 + 白名单（load_skill 豁免透传，其余不可见）。"""
    catalog, store, _ = _setup(tmp_path)
    registry = Registry.default()
    registry.register(LoadSkillTool(catalog, store, registry))
    # 加一个 allowedTools=[read_file] 的 fork skill
    user = Path(tmp_path) / "user"
    (user / "narrow.md").write_text(
        "---\nname: narrow\ndescription: narrow\nmode: fork\nallowedTools:\n  - read_file\n---\nNARROW",
        encoding="utf-8",
    )
    catalog.reload()
    skill = catalog.get("narrow")
    sub = registry.filtered(skill.meta.allowed_tools)
    defs = sub.to_definitions()
    names = {d.name for d in defs}
    assert "read_file" in names  # 白名单内
    assert "load_skill" in names  # 系统工具豁免（F3.5）
    assert "execute_command" not in names  # 白名单外不可见
    assert "write_file" not in names


def test_inline_toolset_stays_full(tmp_path):
    """防 bug：inline 模式不真过滤——模型可见工具集保持全量（F5.3/A 决策）。"""
    catalog, store, _ = _setup(tmp_path)
    registry = Registry.default()
    registry.register(LoadSkillTool(catalog, store, registry))
    skill = catalog.get("commit")  # inline，allowedTools 空
    sub = registry.filtered(skill.meta.allowed_tools)
    assert len(sub.to_definitions()) == len(registry.to_definitions())


# ── /clear（F5.5）──────────────────────────────────────────


async def test_clear_active_skills(tmp_path):
    """防 bug：/clear 清空 activeSkills，后续 env 无旧 SOP，只留阶段一摘要。"""
    catalog, store, registry = _setup(tmp_path)
    store.activate("commit", "COMMIT SOP")
    provider = RecordingProvider([_done_queue("x")])
    agent, _ = _make_agent(provider, registry, store, catalog)
    # 模拟 /clear → agent.clear_active_skills()
    agent.clear_active_skills()
    assert store.names() == []
    async for _ in agent.run("hi"):
        pass
    env = provider.payloads[0].env_segment
    assert "Available Skills" in env
    assert "COMMIT SOP" not in env


# ── 热重载（N7）────────────────────────────────────────────


async def test_hot_reload_new_body_used(tmp_path):
    """防 bug：改源文件后不重启，get() 返回新 body（热更新）。"""
    catalog, _, _ = _setup(tmp_path)
    path = Path(tmp_path) / "user" / "commit.md"
    path.write_text(_skill_md("commit", "UPDATED SOP"), encoding="utf-8")
    skill = catalog.get("commit")
    assert "UPDATED SOP" in skill.prompt_body


# ── E2E 场景（AC13）────────────────────────────────────────


def test_off_persists_after_restart_e2e(tmp_path):
    """防 bug：/skill off 后重启（重建 Catalog）仍禁用（E2E3/AC13）。"""
    catalog, _, _ = _setup(tmp_path)
    catalog.set_disabled("commit", True)
    # 重启模拟
    c2 = _catalog(tmp_path)
    assert c2.is_disabled("commit")
    assert "commit" not in c2.names()
    assert "review" in c2.names()  # 未禁用的仍可用
