"""队员 spawn 主流程（ch15 F10/F25）：TeamHook 结构实现（TD-12）。

- `TeamHookImpl` 由 cli 装配时注入 AgentTool（agent 包不 import team）
- spawn_teammate：校验 → 角色解析 → worktree → session → 构造子 Agent（in-process）
  或 SpawnRequest（Pane）→ mailbox 预写 initial_prompt（Pane，F2.6）→ backend.spawn
  → registry 注册 → add_member（reload-before-modify）
- 权限：
  - 成员一律 dont_ask=True（F6.3，覆盖角色 permission_mode）
  - plan_mode_required 成员：初始 allowed_tools 收窄去写工具（硬门控，F7.4）+ plan 提示词
  - in-process 队员禁再 spawn（TD-14）；Pane 队员 team_name 屏蔽
"""

from __future__ import annotations

import json
import secrets
from dataclasses import replace

from ..agent.team_hook import (
    IncomingMessage,
    TeammateContext,
    TeamSpawnRequest,
    current_teammate,
)
from ..permission.modes import PermissionMode
from ..session.runtime import SessionRuntime
from ..subagent.types import AgentDefinition
from ..tools.filter import TEAMMATE_EXTRA_TOOLS
from .backend import SpawnRequest, new_backend
from .mailbox import Box, Message, MessageType
from .manager import Manager
from .notices import (
    TEAMMATE_PROMPT_APPENDIX,
    build_plan_mode_reminder,
    build_team_context,
)
from .types import BackendType, InProcessTeammateNoSpawnError, TeammateInfo

# plan 门控成员的初始白名单：读类 + 协作工具（不含 write_file/edit_file，F7.4 硬门控）
_PLAN_GATED_READ_ONLY = (
    "read_file",
    "list_files",
    "search_code",
    "execute_command",
) + tuple(TEAMMATE_EXTRA_TOOLS)


def _truncate_summary(prompt: str, max_words: int = 10) -> str:
    """给 mailbox 初始任务消息生成 5-10 词 summary（F2.6）。"""
    words = " ".join(prompt.split()).split()
    summary = " ".join(words[:max_words])
    return summary if summary else "new task"


class TeamHookImpl:
    """TeamHook 结构实现（TD-12）：由 cli 构造，注入 AgentTool。"""

    def __init__(self, mgr: Manager, catalog, launcher, features_cfg) -> None:
        self._mgr = mgr
        self._catalog = catalog
        self._launcher = launcher
        self._features = features_cfg

    # ── TeamHook Protocol ────────────────────────────────
    def is_teammate_context(self, ctx=None) -> tuple[str, str, bool]:
        """当前任务上下文是否在某队员内（TD-14）。"""
        tc = current_teammate()
        if tc is None:
            return ("", "", False)
        return (
            tc.team_name,
            tc.member_name,
            tc.backend_type == BackendType.IN_PROCESS.value,
        )

    async def spawn_teammate(self, req: TeamSpawnRequest) -> str:
        """spawn 一个队员（F10）；返回 JSON 文本。"""
        team = self._mgr.get(req.team_name)
        if team is None:
            return self._err(f"团队不存在: {req.team_name}")
        # 调用者身份拦截（TD-14）：成员不能往团队加人
        _ct, _cm, is_inproc = self.is_teammate_context(None)
        if _cm:
            if is_inproc:
                raise InProcessTeammateNoSpawnError("in-process 队员不能再 spawn 队员")
            return self._err("队员不能往团队加人（team_name 屏蔽）")

        role = self._resolve_role(req)
        if isinstance(role, str):
            return self._err(role)
        assert role is not None

        member_name = (req.name or "").strip() or f"member-{secrets.token_hex(3)}"
        agent_id = f"agent-{secrets.token_hex(4)}"

        # worktree（F10.3）
        if self._mgr.wt_mgr is None:
            return self._err("worktree 不可用（非 git 仓库或未启用）")
        wt = await self._mgr.wt_mgr.create(
            f"team-{team.sanitized_name}/{member_name}", "HEAD", False
        )

        # 独立 session（F10.4，F6.1 transcript 持久化）
        runtime = SessionRuntime(self._mgr.project_root)
        runtime.create_new()
        session_dir = str(runtime.session_dir)

        backend_type = team.backend
        plan_gated = req.plan_mode_required or role.plan_mode_required
        box = Box(team.mailbox_dir)

        # 角色 body 拼队员系统提示词附录（F10.9）
        eff_role: AgentDefinition = replace(
            role, body=(role.body + "\n\n" + TEAMMATE_PROMPT_APPENDIX)
        )

        sub = None
        holder: dict = {}
        if backend_type == BackendType.IN_PROCESS:
            # 闭包构造（TD-12：team 注入，agent 包不 import team）
            async def _read_unread():
                idx, msgs = await box.read_unread(agent_id)
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
                await box.mark_read(agent_id, indices)

            def _set_perm(mode: str) -> None:
                agent = holder.get("agent")
                if agent is None:
                    return
                if agent.permission is not None:
                    pm = PermissionMode.parse(mode) or PermissionMode.DEFAULT
                    agent.permission.set_mode(pm)
                if mode == "default":
                    agent.set_allowed_tools(None)  # 解除 plan 硬门控（F13.4）

            tc = TeammateContext(
                team_name=team.sanitized_name,
                member_name=member_name,
                agent_id=agent_id,
                backend_type=backend_type.value,
                read_unread=_read_unread,
                mark_read=_mark_read,
                set_permission=_set_perm,
            )
            sub, _conv = self._launcher.make_sub_agent(
                eff_role,
                is_background=True,
                permission_mode=PermissionMode.PLAN if plan_gated else None,
                sandbox_root=wt.path,
                dont_ask=True,  # F6.3
                runtime=runtime,
                teammate=tc,
                extra_tools=TEAMMATE_EXTRA_TOOLS,
            )
            holder["agent"] = sub
            if plan_gated:
                # 硬门控：初始 allowed_tools 不含写工具（F7.4）；approve 后经 _set_perm 解锁
                sub.set_allowed_tools(list(_PLAN_GATED_READ_ONLY))
            # <team-context> + plan 提示注入任务文本（F10.10）
            task = build_team_context(
                team.sanitized_name,
                member_name,
                agent_id,
                wt.path,
                [m.name for m in team.members],
            )
            if plan_gated:
                task += "\n\n" + build_plan_mode_reminder()
            initial_prompt = task + "\n\n" + req.prompt
        else:
            # Pane 后端：子进程自己构造 Agent；这里只预写 mailbox + spawn（F2.6）
            initial_prompt = req.prompt
            if plan_gated:
                initial_prompt = build_plan_mode_reminder() + "\n\n" + req.prompt

        # Pane 后端预写 initial_prompt 到 mailbox（F2.6，不走命令行）
        if backend_type != BackendType.IN_PROCESS:
            await box.write(
                agent_id,
                Message(
                    from_="lead",
                    to=agent_id,
                    type=MessageType.TEXT,
                    summary=_truncate_summary(initial_prompt),
                    content=initial_prompt,
                ),
            )

        # backend.spawn（F10.7）
        backend = new_backend(backend_type, task_mgr=self._mgr.task_mgr)
        spawn_req = SpawnRequest(
            team_name=team.sanitized_name,
            member_name=member_name,
            agent_id=agent_id,
            worktree_path=wt.path,
            session_dir=session_dir,
            agent_type=role.name,
            model=req.model,
            initial_prompt=initial_prompt,
            plan_mode_required=plan_gated,
            sub_agent=sub,
            task_mgr=self._mgr.task_mgr,
        )
        pane_id, aid = await backend.spawn(spawn_req)

        # 注册 + 入花名册（F10.7/F1.7 reload-before-modify）
        self._mgr.registry.register(member_name, aid)
        info = TeammateInfo(
            name=member_name,
            agent_id=aid,
            agent_type=role.name,
            model=req.model,
            worktree_path=wt.path,
            branch=wt.branch,
            backend_type=backend_type,
            pane_id=pane_id,
            is_active=True,
            plan_mode_required=plan_gated,
            session_dir=session_dir,
        )
        await self._mgr.add_member(team, info)

        return json.dumps(
            {
                "member_name": member_name,
                "agent_id": aid,
                "worktree": wt.path,
                "backend": backend_type.value,
                "pane_id": pane_id,
            },
            ensure_ascii=False,
        )

    # ── 内部 ─────────────────────────────────────────────
    def _resolve_role(self, req: TeamSpawnRequest) -> AgentDefinition | str:
        """角色解析（F10.2）：subagent_type → catalog；留空 + FORK_TEAMMATE → fork；否则 general-purpose。"""
        if req.subagent_type:
            role = self._catalog.resolve(req.subagent_type)
            if role is None:
                return f"未知 subagent_type: {req.subagent_type}"
            return role
        if self._features.fork_teammate:
            return self._catalog.fork_definition()
        role = self._catalog.resolve("general-purpose")
        if role is None:
            return "缺少 general-purpose 角色定义"
        return role

    @staticmethod
    def _err(msg: str) -> str:
        return json.dumps({"status": "error", "error": msg}, ensure_ascii=False)
