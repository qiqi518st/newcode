"""Pane 后端子进程的 team-member 模式（ch15 F6/F19a-F19c）。

`python -m newcode --team-member ...` 被 tmux/iterm2 后端 spawn：
- 不构造 TUI/REPL，跑自治协程 run_team_member（F19a）
- chdir(worktree) → 构造独立 Manager/provider/registry/permission/Agent（dont_ask=True）
- stdin reader：任何回车（tmux send-keys 触发）→ wake_event → 立即轮询 mailbox（F6.1）
- 主循环：读未读 → text 拼 task / plan_approval 切权限+续派 / shutdown 优雅退出 →
  run（日志流打印）→ 完成写 Lead mailbox idle + set_member_active(False)（F19a）
- mailbox 目录消失（Lead 调 /team delete）→ 优雅退出
- pane UX 是只读日志流（Text print / ● tool(args) / Done 横线 / 错误 stderr，F6.2）
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from ..agent.agent import Agent
from ..agent.events import EventType
from ..agent.team_hook import IncomingMessage, TeammateContext
from ..config.loader import load as load_config
from ..config.loader import load_ccswitch
from ..permission.checker import PermissionChecker
from ..permission.modes import PermissionMode
from ..provider.base import new_provider
from ..session.runtime import SessionRuntime
from ..subagent.catalog import load_catalog
from ..subagent.config import load_agent_config
from ..subagent.errors import MaxTurnsReached
from ..subagent.types import DEFAULT_MAX_TURNS
from ..tools.registry import Registry
from .mailbox import Box, Message, MessageType
from .manager import Manager
from .notices import (
    TEAMMATE_PROMPT_APPENDIX,
    build_plan_mode_reminder,
    build_team_context,
)
from .tools import (
    new_send_message_tool,
    new_task_create_tool,
    new_task_get_tool,
    new_task_list_tool,
    new_task_update_tool,
)


async def _run_and_log(agent: Agent, task: str) -> str:
    """驱动一轮 run，把事件转只读日志流（F6.2）；返回最终文本。"""
    final_text = ""
    try:
        async for event in agent.run(task, mode="normal"):
            if event.type == EventType.TEXT:
                final_text += event.payload
                print(event.payload, end="", flush=True)
            elif event.type == EventType.TOOL_CALL:
                tc = event.payload
                params = ", ".join(f"{k}={v!r}" for k, v in tc.arguments.items())
                print(f"\n● {tc.tool_name}({params})", flush=True)
            elif event.type == EventType.TOOL_RESULT:
                tr = event.payload
                if tr.status == "error":
                    print(f"  ✗ {tr.error}", flush=True)
                else:
                    summary = (
                        tr.output[:200] + "..." if len(tr.output) > 200 else tr.output
                    )
                    print(f"  -> {summary}", flush=True)
            elif event.type == EventType.ERROR:
                print(f"错误: {event.payload}", file=sys.stderr, flush=True)
    except MaxTurnsReached as exc:
        final_text = exc.final_text or ""
        print("\n[队员达到最大轮数]", flush=True)
    print("\n" + "─" * 40, flush=True)
    return final_text


def _compose_task(msgs: list[IncomingMessage], set_perm) -> str | None:
    """把未读消息转下一轮任务文本；返回 None 表示无需跑（如 shutdown 退出）。"""
    for m in msgs:
        if m.type == MessageType.SHUTDOWN_REQUEST.value:
            return _SHUTDOWN
        if m.type == MessageType.PLAN_APPROVAL_RESPONSE.value:
            payload = m.payload or {}
            if payload.get("approve"):
                if set_perm is not None:
                    set_perm("default")
                return "Lead 已批准你的计划，权限已切换到执行模式，可执行计划。"
            return (
                "Lead 驳回了你的计划，反馈："
                + str(payload.get("feedback", ""))
                + "。请调整后重新提交计划。"
            )
    # 取第一条 text 消息内容作为任务
    for m in msgs:
        if m.type == MessageType.TEXT.value and (m.content or m.summary):
            return m.content or m.summary
    return None


_SHUTDOWN = "__TEAM_SHUTDOWN__"


async def run_team_member(args) -> None:
    """team-member 自治协程入口（F19a）。args 为 argparse.Namespace。"""
    worktree = os.path.abspath(args.worktree)
    os.chdir(worktree)
    print(
        f"[team-member] {args.member} · team={args.team} · agent={args.agent_id} "
        f"· cwd={worktree}",
        flush=True,
    )

    # 配置 + provider（ccswitch 兜底）
    config = load_ccswitch()
    if config is None:
        try:
            config = load_config(os.path.join(worktree, ".newcode.yaml"))
        except Exception as exc:  # noqa: BLE001 —— 子进程配置失败给错误退出
            print(f"[team-member] 配置加载失败: {exc}", file=sys.stderr)
            return
    provider_cfg = next(
        (p for p in config.providers if p.name == config.provider), None
    )
    if provider_cfg is None:
        print("[team-member] provider 未配置", file=sys.stderr)
        return
    provider = new_provider(provider_cfg)

    # Team Manager（扫 teams 目录还原；跨进程 reload-before-modify 兜底）
    mgr = Manager(
        home_dir=str(Path.home()),
        project_root=worktree,
        wt_mgr=None,
        task_mgr=None,
    )
    team = mgr.get(args.team)
    if team is None:
        print(f"[team-member] 团队不存在: {args.team}", file=sys.stderr)
        return

    # 权限（沙箱根=worktree，--plan-mode 时 PLAN）
    permission = PermissionChecker.create(worktree)
    if getattr(args, "plan_mode", False):
        permission.set_mode(PermissionMode.PLAN)

    # Registry：核心 6 工具 + 协作工具（Pane 队员暂不含 agent 工具——子进程内再派生子
    # Agent 超出本章范围，team_name 屏蔽由「不注入 agent 工具」兜底）
    registry = Registry.default()
    registry.register(new_task_create_tool(mgr))
    registry.register(new_task_get_tool(mgr))
    registry.register(new_task_list_tool(mgr))
    registry.register(new_task_update_tool(mgr))
    registry.register(new_send_message_tool(mgr))

    # 角色 + session
    agents_cfg = load_agent_config(worktree)
    catalog = load_catalog(worktree, agents_cfg)
    role = (
        catalog.resolve(args.agent_type)
        if getattr(args, "agent_type", "")
        else catalog.resolve("general-purpose")
    )
    stable_prompt = ""
    max_turns = agents_cfg.max_turns or DEFAULT_MAX_TURNS
    if role is not None:
        stable_prompt = role.body + "\n\n" + TEAMMATE_PROMPT_APPENDIX
        max_turns = role.max_turns or max_turns

    runtime = SessionRuntime.open_at(
        args.session_dir, max_turns=max_turns, model=provider.model
    )
    conv = runtime.conversation

    # TeammateContext（闭包注入，TD-12）
    box = Box(team.mailbox_dir)
    agent_holder: dict = {}

    async def _read_unread():
        idx, msgs = await box.read_unread(args.agent_id)
        return idx, [
            IncomingMessage(
                from_=m.from_,
                type=m.type.value,
                summary=m.summary,
                content=m.content,
                payload=m.payload,
                timestamp=m.timestamp,
            )
            for m in msgs
        ]

    async def _mark_read(indices):
        await box.mark_read(args.agent_id, indices)

    def _set_perm(mode: str) -> None:
        agent = agent_holder.get("agent")
        if agent is None:
            return
        if agent.permission is not None:
            pm = PermissionMode.parse(mode) or PermissionMode.DEFAULT
            agent.permission.set_mode(pm)
        if mode == "default":
            agent.set_allowed_tools(None)

    tc = TeammateContext(
        team_name=team.sanitized_name,
        member_name=args.member,
        agent_id=args.agent_id,
        backend_type=getattr(args, "backend", "") or team.backend.value,
        read_unread=_read_unread,
        mark_read=_mark_read,
        set_permission=_set_perm,
    )
    agent = Agent(
        provider,
        conv,
        registry,
        stable_prompt=stable_prompt,
        env_segment="",
        permission=permission,
        is_interactive=False,
        max_turns=max_turns,
        dont_ask=True,  # F6.3：子进程无 TUI 接 ApprovalRequest
        teammate=tc,
        runtime=runtime,
    )
    agent_holder["agent"] = agent
    if getattr(args, "plan_mode", False):
        agent.set_allowed_tools(
            [
                "read_file",
                "list_files",
                "search_code",
                "execute_command",
                "task_create",
                "task_get",
                "task_list",
                "task_update",
                "send_message",
            ]
        )

    # <team-context> initial reminder（F10.10）
    conv.add_user(
        build_team_context(
            team.sanitized_name,
            args.member,
            args.agent_id,
            worktree,
            [m.name for m in team.members],
        )
    )
    if getattr(args, "plan_mode", False):
        conv.add_user(build_plan_mode_reminder())

    # stdin reader：回车 → wake_event（F6.1）
    wake_event = asyncio.Event()

    async def _stdin_reader():
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            wake_event.set()

    asyncio.create_task(_stdin_reader())

    # 主循环（F19a）
    while True:
        if not Path(team.mailbox_dir).is_dir():
            print("[team-member] mailbox 目录已删除，优雅退出", flush=True)
            break
        indices, msgs = await box.read_unread(args.agent_id)
        if not msgs:
            try:
                await asyncio.wait_for(wake_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            wake_event.clear()
            continue
        await box.mark_read(args.agent_id, indices)
        task = _compose_task(msgs, _set_perm)
        if task == _SHUTDOWN:
            print("[team-member] 收到 shutdown_request，退出", flush=True)
            break
        if task is None:
            continue
        try:
            await _run_and_log(agent, task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 一轮失败记错误不崩循环
            print(f"[team-member] 本轮失败: {exc}", file=sys.stderr, flush=True)
        # 完成：写 Lead mailbox idle + is_active=False（F19a；跨进程 reload 兜底）
        try:
            await mgr.set_member_active(team, args.member, False)
            await box.write(
                team.lead_agent_id,
                Message(
                    from_=args.member,
                    to=team.lead_agent_id,
                    type=MessageType.TEXT,
                    summary=f"{args.member} idle",
                    content=f"agent {args.agent_id} finished work, available for new tasks",
                ),
            )
        except Exception as exc:  # noqa: BLE001 —— 通知失败不崩循环
            print(f"[team-member] idle 通知失败: {exc}", file=sys.stderr, flush=True)
