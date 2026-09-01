"""Skill 执行器（F3）：inline / fork 两模式分发。

- inline（F3.1）：共享当前对话上下文——重读 body → render_body → 目录型注册工具 →
  store.activate → 注入消息触发 Agent 回合，结果留在主对话历史里。
- fork（F3.1/F3.2/N3）：创建独立内存 ConversationManager + 临时 Agent（definitions_filtered
  收窄工具集，with_catalog 注入），按 fork_context 决定历史携带，跑完结果摘要回流主对话
  （ui.append_assistant_message），token 用量写回主统计（N13）。

任一步出错 → final_text 兜底为 "[skill <name> failed: <reason>]" 仍写回主对话（T13）。
"""

from __future__ import annotations

import logging

from ..provider.base import Message, TokenUsage
from ..skills.render import render_body

logger = logging.getLogger(__name__)

_FORK_CONV_MAX_TURNS = 20  # fork 独立会话短小，滑动窗口放宽防过早裁剪


class Executor:
    """Skill 执行器：依赖 catalog/store/registry/provider，engine 可选。

    engine：主对话 runtime（如 TUI REPL），实现 add_token_usage(in, out) 时接收
    fork token 写回主统计（N13）；缺失则仅文本报告，不阻断。
    make_provider：可调用 (model: str) -> Provider，fork 模式 model override 用；
    缺失且 model 与当前会话模型不同时降级为当前 provider 并记 warning。
    """

    def __init__(
        self,
        catalog,
        store,
        registry,
        provider,
        engine=None,
        version: str = "",
        make_provider=None,
    ) -> None:
        self._catalog = catalog
        self._store = store
        self._registry = registry
        self._provider = provider
        self._engine = engine
        self._version = version
        self._make_provider = make_provider

    # ── 入口 ─────────────────────────────────────────────
    async def execute(self, ctx, ui, name: str, args: str) -> None:
        """Slash handler / LoadSkill 共用的执行入口。

        ctx：CommandContext（fork 需要主对话 ConversationManager 与版本信息）。
        """
        skill = self._catalog.get(name)
        if skill is None:
            ui.show_message(
                f"未知 Skill: {name}（可用: {', '.join(self._catalog.names())}）",
                style="yellow",
            )
            return
        rendered = render_body(skill, args)
        if skill.meta.is_fork():
            await self._execute_fork(ctx, ui, skill, rendered)
        else:
            await self._execute_inline(ctx, ui, skill, rendered)

    # ── inline（F3.1/F4.2）────────────────────────────────
    async def _execute_inline(self, ctx, ui, skill, rendered: str) -> None:
        """inline：注册目录型工具 → 激活 → 注入消息触发回合（不立即调 LLM）。"""
        self._register_skill_tools(skill)
        self._store.activate(skill.name, rendered)
        # 注入渲染后的 SOP 作为用户消息触发 Agent 回合（等价 plan 的 inject_and_send）
        await ui.send_user_message(rendered)

    # ── fork（F3.1/F3.2/N3/N13）───────────────────────────
    async def _execute_fork(self, ctx, ui, skill, rendered: str) -> None:
        """fork：独立会话执行，结果摘要与 token 回流主对话。"""
        try:
            # 收窄子 Agent 工具集：系统工具豁免 + allowedTools 白名单（F3.7/A 决策）
            sub_registry = self._registry.filtered(skill.meta.allowed_tools)
            provider = self._resolve_fork_provider(skill)
            # fork_context=none 时初始消息为空是合法的（子 Agent run 追加 rendered）
            fork_messages = await self._build_fork_messages(ctx, skill, rendered)

            conv = _make_fork_conversation(fork_messages)
            agent = self._make_fork_agent(provider, conv, sub_registry, skill)
            final_text, in_tokens, out_tokens = await self._run_fork_agent(
                agent, rendered
            )

            if not final_text:
                final_text = f"[skill {skill.name} completed with no text output]"
            ui.append_assistant_message(final_text)
            # N13：fork token 写回主统计（engine/UI 支持时）；文本报告已含用量
            self._write_back_tokens(ui, in_tokens, out_tokens)
        except Exception as e:
            logger.exception("fork skill %s failed", skill.name)
            ui.append_assistant_message(f"[skill {skill.name} failed: {e}]")

    async def _build_fork_messages(self, ctx, skill, rendered: str) -> list[Message]:
        """按 fork_context 构造 fork 初始消息（不含 rendered——由子 Agent run 追加，防重复）。

        - none（缺省，F3.2）：完全隔离，空初始消息。
        - recent：主对话最近 N 条原样拷贝（缺省 5）+ 由 run 追加 rendered。
        - full：LLM 压缩主对话成摘要作单条 user + assistant 占位衔接，再追加 rendered。
        """
        context_mode = skill.meta.fork_context
        main_conv = getattr(ctx, "conversation", None)
        if context_mode == "none" or main_conv is None:
            return []
        if context_mode == "recent":
            n = _recent_n(skill)
            recent = main_conv.get_context()[-n:]
            return [Message(role=m.role, content=m.content) for m in recent]
        # full：LLM 压缩主对话成摘要（复用 summarize 模式，省 token，F3.2）
        summary = await self._summarize_main(main_conv)
        return [
            Message(role="user", content=f"[fork context summary]\n{summary}"),
            Message(role="assistant", content="(continuing)"),
        ]

    async def _summarize_main(self, main_conv) -> str:
        """把主对话经 LLM 压缩成一段摘要（context=full 用）。"""
        from ..context.summarize import (
            SUMMARY_INSTRUCTION,
            build_summary_prompt,
            extract_summary,
        )
        from ..prompt.assembler import PromptPayload

        payload = PromptPayload(
            stable_prompt=SUMMARY_INSTRUCTION,
            env_segment="",
            messages=build_summary_prompt(main_conv.get_context()),
            tools=None,
            max_output_tokens=8192,
        )
        buffer = ""
        stream = self._provider.stream(payload)
        async for se in stream:
            if se.text:
                buffer += se.text
            elif se.err:
                raise RuntimeError(f"fork summary failed: {se.err}")
            elif se.done:
                break
        return extract_summary(buffer)

    async def _run_fork_agent(self, agent, rendered: str) -> tuple[str, int, int]:
        """驱动子 Agent 至 DONE（ch13 F10：改用 run_to_completion 复用主循环，不手写事件消费）。

        observer 聚合 token 用量供写回主统计（N13）；达 maxTurns 保留部分文本（与旧行为一致）。
        """
        from ..agent.events import EventType
        from ..subagent.errors import MaxTurnsReached

        usage = TokenUsage(0, 0)

        def observer(event) -> None:
            nonlocal usage
            if event.type == EventType.TOKEN_USAGE:
                tu: TokenUsage = event.payload
                usage = TokenUsage(
                    usage.input_tokens + tu.input_tokens,
                    usage.output_tokens + tu.output_tokens,
                    usage.cache_creation_input_tokens + tu.cache_creation_input_tokens,
                    usage.cache_read_input_tokens + tu.cache_read_input_tokens,
                )

        try:
            final_text = await agent.run_to_completion(rendered, observer=observer)
        except MaxTurnsReached as exc:
            final_text = exc.text
        return final_text.strip(), usage.input_tokens, usage.output_tokens

    def _resolve_fork_provider(self, skill):
        """model override：skill.meta.model 非空且与当前会话模型不同时建新 provider。

        无 make_provider 工厂时降级为当前 provider 并 warning（不阻断，F1.2 model 可选）。
        """
        model = skill.meta.model
        if not model or model == getattr(self._provider, "model", None):
            return self._provider
        if self._make_provider is not None:
            try:
                return self._make_provider(model)
            except Exception as e:  # noqa: BLE001 - override 失败降级当前 provider
                logger.warning(
                    "fork model override to %s failed (%s), using session model",
                    model,
                    e,
                )
        else:
            logger.warning(
                "skill %s requests model %s but no provider factory wired, using session model",
                skill.name,
                model,
            )
        return self._provider

    def _make_fork_agent(self, provider, conv, sub_registry, skill):
        """构造 fork 临时 Agent（局部 import 避循环；with_catalog 注入摘要）。"""
        from ..agent.agent import Agent

        agent = Agent(
            provider,
            conv,
            sub_registry,
            stable_prompt="",
            env_segment="",
            is_interactive=False,
        )
        catalog = getattr(self, "_catalog", None)
        if catalog is not None and getattr(agent, "with_catalog", None) is not None:
            try:
                agent.with_catalog(catalog)
            except Exception:
                logger.warning("fork agent catalog injection failed", exc_info=True)
        return agent

    # ── 目录型工具注册（F9.2/F9.6）────────────────────────
    def _register_skill_tools(self, skill) -> None:
        """目录型 Skill：tool.json 声明的工具注册进主注册表（ScriptTool 子进程壳）。"""
        from ..skills.catalog import register_skill_tools

        register_skill_tools(self._registry, skill)

    # ── token 写回（N13）──────────────────────────────────
    def _write_back_tokens(self, ui, in_tokens: int, out_tokens: int) -> None:
        """fork token 写回主统计：优先 UI 的 add_token_usage，其次 engine，最后跳过。"""
        if in_tokens or out_tokens:
            try:
                ui.add_token_usage(in_tokens, out_tokens)
                return
            except (AttributeError, NotImplementedError):
                pass
        engine = self._engine
        if engine is not None:
            add = getattr(engine, "add_token_usage", None)
            if add is not None:
                try:
                    add(in_tokens, out_tokens)
                except Exception:
                    logger.warning("fork token write-back failed", exc_info=True)


def _recent_n(skill) -> int:
    """fork_context 的 recent 缺省条数 N=5（F3.2/RECENT_DEFAULT_N）。"""
    from ..skills.constants import RECENT_DEFAULT_N

    return RECENT_DEFAULT_N


def _make_fork_conversation(messages: list[Message]):
    """构造 fork 独立内存 ConversationManager（不落盘，N3）。"""
    from ..conversation.manager import ConversationManager

    return ConversationManager(_FORK_CONV_MAX_TURNS, messages=list(messages))
