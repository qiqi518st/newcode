"""上下文管理窄入口（spec F7/F23-F28）：编排 L1 落盘 + L2 摘要 + 自动闸 + 三路径收尾。

- manage_context（自动，每轮组装前）：L1 全量扫 + L2 阈值检查（受自动闸约束）。
- compact_now（手动 /compact）：无条件摘要，成功解除自动闸。
- force_compact（紧急 ForceCompact）：先强制 L1 挪走 50K+（F25）→ 摘要 → F25a 重估。
- 锚点：update_anchor 仅主对话路径调用（Agent 流成功后）；摘要路径不调（防污染）。
"""

import asyncio
import logging
import os
from collections.abc import Callable

from ..context.autogate import AutoCompactGate
from ..context.constants import (
    AUTO_SAFETY_MARGIN,
    CONTEXT_WINDOW_FLOOR,
    MANUAL_SAFETY_MARGIN,
)
from ..context.files import FileTracker
from ..context.offload import offload_and_snip
from ..context.recovery import RecoveryBuilder
from ..context.replacement import ContentReplacementState
from ..context.session import SessionPaths, new_session_context
from ..context.summarize import CompactOutcome, SummarizeConfig, Summarizer
from ..context.tokens import estimate_tokens, usage_to_anchor
from ..context.window import get_context_window_for_model
from ..monitor.protocol import is_monitor_active
from ..provider.base import Provider, TokenUsage, ToolDefinition

logger = logging.getLogger(__name__)

# emit_event 契约：kind ∈ {"context_compacting", "compact_failed"}，payload 见下。
# T20 将 kind 映射为 agent.events 的 CONTEXT_COMPACTING / COMPACT_FAILED。
EMIT_COMPACTING = "context_compacting"  # payload: str（"auto"/"force"）
EMIT_FAILED = "compact_failed"  # payload: CompactOutcome
EMIT_OFFLOADED = "context_offloaded"  # payload: dict
EMIT_COMPACTED = "context_compacted"  # payload: CompactOutcome


class ContextManager:
    """窄入口：编排 L1/L2、手动、紧急与熔断收尾（session 级 asyncio.Lock 互斥，F34）。

    注：__init__ 补可选 workspace 参数——SessionPaths 落盘目录需要工作区路径，
    task/plan 签名均遗漏，默认取 os.getcwd()，main.py 装配时可显式传入。
    """

    def __init__(
        self,
        provider: Provider,
        conversation: object,  # ConversationManager（延迟导入避循环：conversation.manager → context.dropper → context）
        model: str,
        protocol: str,
        file_tracker: FileTracker,
        active_skills: object
        | None = None,  # ch11: ActiveSkills | None（压缩预算淘汰）
        emit_event: Callable[[str, object], None] | None = None,
        workspace: str | None = None,
    ) -> None:
        self._provider = provider
        self._conv = conversation
        self._file_tracker = file_tracker
        self._emit_event = emit_event
        self._lock = asyncio.Lock()  # 会话级互斥（F34），管 context 三方法
        self._state = ContentReplacementState()
        self._auto_gate = AutoCompactGate()
        self._workspace = os.path.abspath(workspace or os.getcwd())
        self._session = SessionPaths(new_session_context(self._workspace))
        self._recovery_builder = RecoveryBuilder(active_skills)
        self._summarizer = Summarizer(
            provider,
            self._recovery_builder,
            file_tracker,
            trace_factory=self._prepare_summary_trace,
        )
        self._context_window = get_context_window_for_model(model, protocol)
        # 外部锚点状态（纯函数估算用，由 Agent 主对话路径维护）
        self._usage_anchor = 0
        self._anchor_msg_len = 0

    @property
    def context_window(self) -> int:
        return self._context_window

    @property
    def usage_anchor(self) -> int:
        return self._usage_anchor

    @property
    def anchor_msg_len(self) -> int:
        return self._anchor_msg_len

    # ── 自动路径 ──────────────────────────────────────────────
    async def manage_context(self, tool_defs: list[ToolDefinition]) -> None:
        """Agent 每轮组装请求前调用：L1 全量扫 + L2 自动阈值检查（受自动闸约束）。"""
        try:
            # 入口 sanity check（F7）：窗口过小 → 自动摘要会死循环，仅跑 L1
            if self._context_window <= CONTEXT_WINDOW_FLOOR:
                logger.warning(
                    "context_window %d too small for auto-compact, skipping layer2 (L1 only)",
                    self._context_window,
                )
                async with self._lock:
                    replaced = await offload_and_snip(
                        self._conv.get_messages_ref(), self._state, self._session
                    )
                    self._emit_offloaded(replaced)
                return
            # 自动闸（F28）：连续失败达上限 → 静默跳过 L2，仅 L1，不弹菜单
            if self._auto_gate.auto_disabled():
                async with self._lock:
                    replaced = await offload_and_snip(
                        self._conv.get_messages_ref(), self._state, self._session
                    )
                    self._emit_offloaded(replaced)
                return
            async with self._lock:
                replaced = await offload_and_snip(
                    self._conv.get_messages_ref(), self._state, self._session
                )
                self._emit_offloaded(replaced)
                estimate = estimate_tokens(
                    self._usage_anchor,
                    self._conv.get_messages_ref(),
                    self._anchor_msg_len,
                )
                if estimate < self._context_window - CONTEXT_WINDOW_FLOOR:
                    return  # 未达阈值
                self._emit(EMIT_COMPACTING, "auto")
                outcome = await self._summarizer.summarize(
                    self._conv.get_messages_ref(),
                    SummarizeConfig(
                        safety_margin=AUTO_SAFETY_MARGIN, keep_recent_turns=6
                    ),
                    self._context_window,
                    tool_defs,
                )
                outcome.replaced_results = replaced
                if outcome.success:
                    self._conv.replace_history(outcome.messages or [])
                    self.reset_anchor()
                    self._auto_gate.record_auto_success()
                    self._emit(EMIT_COMPACTED, outcome)
                    logger.info(
                        "auto-compact ok: %d → %d tokens, L1 replaced %d results",
                        outcome.before_tokens,
                        outcome.after_tokens,
                        replaced,
                    )
                else:
                    self._auto_gate.record_auto_failure()
                    self._emit(EMIT_FAILED, outcome)
        except Exception:  # N11：单次失败不崩进程
            logger.exception("manage_context failed")

    # ── 手动路径 ──────────────────────────────────────────────
    async def compact_now(self, tool_defs: list[ToolDefinition]) -> CompactOutcome:
        """手动 /compact：无条件摘要（F23 跳阈值/跳自动闸/跳 L1），成功解除自动闸。"""
        try:
            async with self._lock:
                outcome = await self._summarizer.summarize(
                    self._conv.get_messages_ref(),
                    SummarizeConfig(
                        safety_margin=MANUAL_SAFETY_MARGIN, keep_recent_turns=6
                    ),
                    self._context_window,
                    tool_defs,
                )
                if outcome.success:
                    self._conv.replace_history(outcome.messages or [])
                    self.reset_anchor()
                    self._auto_gate.reset_on_manual_success()  # 手动成功解除自动闸
                    return outcome
                self._emit(EMIT_FAILED, outcome)
                return outcome
        except Exception:  # N11
            logger.exception("compact_now failed")
            return CompactOutcome(True, 0, 0, 0, False, "exception", None)

    # ── 紧急路径 ──────────────────────────────────────────────
    async def force_compact(self, tool_defs: list[ToolDefinition]) -> CompactOutcome:
        """紧急压缩（F25）：先强制 L1 挪走 50K+ → 摘要 → F25a 重估决定可否重试。"""
        try:
            async with self._lock:
                self._emit(EMIT_COMPACTING, "force")
                replaced = await offload_and_snip(
                    self._conv.get_messages_ref(), self._state, self._session
                )
                outcome = await self._summarizer.summarize(
                    self._conv.get_messages_ref(),
                    SummarizeConfig(
                        safety_margin=MANUAL_SAFETY_MARGIN, keep_recent_turns=3
                    ),
                    self._context_window,
                    tool_defs,
                )
                outcome.replaced_results = replaced
                if not outcome.success:
                    self._emit(EMIT_FAILED, outcome)
                    return outcome
                self._conv.replace_history(outcome.messages or [])
                self.reset_anchor()
                # F25a：重估 < 窗口-3000 才允许 Agent 重试原请求，否则不可恢复
                estimate = estimate_tokens(0, self._conv.get_messages_ref(), 0)
                if estimate >= self._context_window - MANUAL_SAFETY_MARGIN:
                    outcome.success = False
                    outcome.failure_reason = "irrecoverable"
                    outcome.messages = None
                    self._emit(EMIT_FAILED, outcome)
                    return outcome
                self._emit(EMIT_COMPACTED, outcome)
                return outcome
        except Exception:  # N11
            logger.exception("force_compact failed")
            return CompactOutcome(True, 0, 0, 0, False, "exception", None)

    # ── 锚点（外部状态，纯函数估算用）────────────────────────
    def update_anchor(self, usage: TokenUsage, conv_len: int) -> None:
        """主对话路径每轮请求成功后调（替换不累加，F14）；摘要路径不调此方法。"""
        self._usage_anchor = usage_to_anchor(usage)
        self._anchor_msg_len = conv_len

    def reset_anchor(self) -> None:
        """摘要/紧急成功后清零锚点（强制纯字符重估）。"""
        self._usage_anchor = 0
        self._anchor_msg_len = 0

    def reset_for_new_session(self, conversation: object | None = None) -> None:
        """（ch10 T0b）/clear 用：原子重置本管理器到新会话初态。

        重建 ContentReplacementState 账本与 AutoCompactGate 计数、归零外部锚点
        （单事件循环无并发顾虑，比给两个类各加 reset 方法省事）；若传入新
        ConversationManager（/clear 重建会话后），同时把 _conv 指向它，避免
        后续上下文压缩仍操作旧会话导致不一致。
        """
        self._state = ContentReplacementState()
        self._auto_gate = AutoCompactGate()
        self._usage_anchor = 0
        self._anchor_msg_len = 0
        if conversation is not None:
            self._conv = conversation

    def _emit(self, kind: str, payload: object) -> None:
        """emit_event 透传（None 时静默；回调异常不打断压缩流程）。"""
        if self._emit_event is None:
            return
        try:
            self._emit_event(kind, payload)
        except Exception:
            logger.exception("emit_event(%s) failed", kind)

    def _emit_offloaded(self, replaced: int) -> None:
        if replaced:
            self._emit(
                EMIT_OFFLOADED,
                {"count": replaced, "spill_dir": str(self._session.spill_dir)},
            )

    def prepare_request_trace(
        self,
        user_input: str,
        turn: int,
        run_id: str | None = None,
        request_kind: str = "conversation",
    ) -> dict[str, object] | None:
        """Reserve a trace file only while the standalone monitor is active."""
        if not is_monitor_active(self._workspace):
            return None
        return {
            "path": str(self._session.request_trace_path()),
            "session_id": self._session.session_id,
            "pid": os.getpid(),
            "workspace": self._workspace,
            "request_kind": request_kind,
            "user_input": user_input,
            "turn": turn,
            "run_id": run_id,
        }

    def _prepare_summary_trace(
        self, messages: list[object]
    ) -> dict[str, object] | None:
        return self.prepare_request_trace("", -1, request_kind="context_summary")
