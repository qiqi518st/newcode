"""统一启动器（ch13 F3/F5/F7）：SubAgentLauncher。

定义式 / Fork 式 / hook agent 动作 / Skill fork 四路径唯一入口：
- build_sub_registry：F6.4 多层过滤 → Registry.view（一次性固定，移交不重算）
- resolve_model：模型分层 haiku/sonnet/opus → 配置映射 → make_provider（F3.1/F11.1）
- make_sub_agent：构造子 Agent（独立 conv / 共享规则层 / dont_ask / 非交互）
- 前台分派：**asyncio.wait 竞速（非 wait_for）**——超时不 cancel，adopt_running 只转移
  所有权，不杀重来（spec F7.3）
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..agent.agent import Agent
from ..conversation.manager import ConversationManager
from ..tools.filter import FilterParams, apply_agent_tool_filter
from ..tools.registry import Registry
from .config import AgentConfig
from .fork import build_forked_messages
from .manager import Status, TaskManager
from .types import DEFAULT_MAX_TURNS, AgentDefinition

if TYPE_CHECKING:
    from ..provider.base import Provider
    from .catalog import Catalog


@dataclass
class LaunchResult:
    """launch_defined / launch_fork 的返回（前台完成带 text，后台带 task_id）。"""

    task_id: str = ""
    status: str = ""  # async_launched / completed / timed_out_to_background
    text: str = ""  # 前台完成的最后一条 assistant 文本
    error: str = ""  # 失败原因（未知角色 / 后台禁用 / 子 Agent 失败）


class SubAgentLauncher:
    """构造并分派子 Agent（依赖全部由 main.py 装配注入）。"""

    def __init__(
        self,
        provider: Provider,
        make_provider: Callable[[str], Provider] | None,
        parent_permission: object,
        hooks: object | None,
        catalog: Catalog,
        manager: TaskManager,
        cfg: AgentConfig,
        get_main_agent: Callable[[], Agent],
    ) -> None:
        self._provider = provider
        self._make_provider = make_provider
        self._parent_permission = parent_permission
        self._hooks = hooks
        self._catalog = catalog
        self._manager = manager
        self._cfg = cfg
        self._get_main_agent = get_main_agent

    # ── 工具集 ────────────────────────────────────────────
    def build_sub_registry(self, role: AgentDefinition, is_background: bool) -> Registry:
        """F6.4 多层过滤 → 子 Registry（共享 Tool 实例，一次性固定）。"""
        parent = self._get_main_agent()
        visible = apply_agent_tool_filter(
            FilterParams(
                all=parent.registry.names(),
                background=is_background,
                role_tools=role.tools,
                role_disallowed=role.disallowed_tools,
            )
        )
        return parent.registry.view(set(visible))

    # ── 模型分层 ──────────────────────────────────────────
    def resolve_model(self, tier: str) -> Provider:
        """inherit/空 → 父 provider；haiku/sonnet/opus → model_tiers 映射（F3.1/F11.1）。"""
        if not tier or tier == "inherit":
            return self._provider
        model_id = self._cfg.model_tiers.get(tier)
        if not model_id:
            print(
                f"subagent: model tier {tier!r} 未配置（agents.model_tiers），降级用父模型",
                file=sys.stderr,
            )
            return self._provider
        if self._make_provider is None:
            return self._provider
        return self._make_provider(model_id)

    # ── 构造子 Agent ──────────────────────────────────────
    def make_sub_agent(
        self, role: AgentDefinition, *, fork_history: list | None = None,
        is_background: bool = False,
    ) -> tuple[Agent, ConversationManager]:
        """构造子 Agent（独立 conv；共享规则层 + 子模式；dont_ask；非交互，B1）。"""
        parent = self._get_main_agent()
        max_turns = role.max_turns or DEFAULT_MAX_TURNS
        conv = ConversationManager(max_turns, messages=list(fork_history or []))
        sub = Agent(
            provider=self.resolve_model(role.model),
            conversation=conv,
            registry=self.build_sub_registry(role, is_background),
            stable_prompt=(
                role.body if not role.is_fork() else getattr(parent, "_stable_prompt", "")
            ),
            env_segment=getattr(parent, "_env_segment", ""),
            permission=self._parent_permission.for_subagent(role.permission_mode),
            is_interactive=False,
            hooks=self._hooks,
            max_turns=max_turns,
            dont_ask=role.dont_ask,
        )
        return sub, conv

    # ── 分派：定义式 ──────────────────────────────────────
    async def launch_defined(
        self,
        role_name: str,
        prompt: str,
        *,
        name: str | None = None,
        background: bool = False,
    ) -> LaunchResult:
        """定义式启动：subagent_type 非空（spec F2/F3.1）。

        background（参数或角色强制）且后台总闸开启 → 后台；否则前台（超时自动移交）。
        """
        role = self._catalog.resolve(role_name)
        if role is None:
            return LaunchResult(error=f"未知 subagent_type: {role_name}")
        is_bg = (background or role.background) and (
            self._cfg.effective_enable_subagent_background()
        )
        sub, _ = self.make_sub_agent(role, is_background=is_bg)
        if is_bg:
            task_id = self._manager.launch(sub, prompt, name=name, role_name=role.name)
            return LaunchResult(task_id=task_id, status="async_launched")
        return await self._run_foreground(sub, role, prompt, name)

    # ── 分派：Fork 式 ─────────────────────────────────────
    async def launch_fork(
        self, prompt: str, *, name: str | None = None
    ) -> LaunchResult:
        """Fork 式启动：无 subagent_type（spec F3.2/F3.3），强制后台。

        - 后台总闸关闭 → 返回结构化错误「后台禁用，无法 Fork」（F11.1/AC27）
        - 继承父历史（build_forked_messages）+ 复用父 stable_prompt/tools
        """
        if not self._cfg.effective_enable_subagent_background():
            return LaunchResult(error="后台禁用，无法 Fork")
        role = self._catalog.fork_definition()
        parent = self._get_main_agent()
        forked = build_forked_messages(parent.conv, prompt)
        sub, _ = self.make_sub_agent(
            role, fork_history=forked, is_background=True
        )
        task_id = self._manager.launch(
            sub, prompt, name=name, role_name="fork", already_injected=True
        )
        return LaunchResult(task_id=task_id, status="async_launched")

    # ── 分派：hook agent 动作 ─────────────────────────────
    async def launch_hook_agent(self, agent_name: str, prompt: str) -> str | None:
        """hook `agent` 动作（F9.1）：定义式后台；失败返回 None（记日志不中断）。"""
        result = await self.launch_defined(agent_name, prompt, background=True)
        if result.error:
            return None
        return result.task_id

    # ── 前台执行 + 超时移交 ───────────────────────────────
    async def _run_foreground(
        self, sub: Agent, role: AgentDefinition, prompt: str, name: str | None
    ) -> LaunchResult:
        """前台执行：asyncio.wait 竞速（非 wait_for），超时 adopt_running 不杀（F7.3）。

        后台总闸关闭（F11.1）→ 无超时（强制前台同步，等待完成、不移交）。
        """
        handle = self._manager.launch_foreground(sub, prompt, name=name, role_name=role.name)
        timeout = (
            self._cfg.async_timeout_s
            if self._cfg.effective_enable_subagent_background()
            else None
        )
        done, _pending = await asyncio.wait({handle.run_task}, timeout=timeout)
        if handle.run_task in done:
            bt = handle.task
            if bt.status == Status.COMPLETED:
                return LaunchResult(status="completed", text=bt.result)
            err = str(bt.err) if bt.err else "subagent failed"
            return LaunchResult(error=err)
        # 超时：任务仍运行（asyncio.wait 不 cancel）→ 移交后台
        self._manager.adopt_running(handle.task_id)
        return LaunchResult(task_id=handle.task_id, status="timed_out_to_background")
