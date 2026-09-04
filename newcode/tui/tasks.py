"""TUI 后台任务（ch15 F11.3/F11.4）：Lead 邮箱消费 + idle 自动续推。

- consume_lead_mail：每秒轮询所有 Team 的 lead.json → `<team-update>` raw reminder →
  lead_mail_event.set()；IDLE 时经 session.app.exit() 打断 prompt（TD-5）
- wait_for_lead_mail：阻塞在 lead_mail_event 上，IDLE 时触发 begin_autonomous_turn
  （STREAMING 中 raw reminder 已被当前 Run 下一轮取走，不需主动 wake）
"""

from __future__ import annotations

import asyncio

from ..team.notices import team_update_message
from .app import SessionState


async def consume_lead_mail(repl) -> None:
    """Lead 邮箱后台消费（F11.3）：1s ticker → poll → raw reminder + 事件 + 打断 prompt。"""
    mgr = getattr(repl, "team_mgr", None)
    if mgr is None:
        return
    while True:
        await asyncio.sleep(1.0)
        try:
            msgs = await mgr.poll_lead_mailboxes()
        except Exception:  # noqa: BLE001, S112 —— 单轮失败不终止消费循环（N6）
            continue
        if not msgs:
            continue
        updates = [
            (
                f"[{m.from_}](team={m.team_name}): {m.summary}\n    "
                + (m.content[:300] if m.content else "")
            )
            for m in msgs
        ]
        runtime = getattr(repl, "session_runtime", None)
        if runtime is not None:
            runtime.append_raw_reminders(
                [team_update_message(msgs[0].team_name, updates)]
            )
        repl._lead_mail_event.set()
        # IDLE 时打断 prompt_async（TD-5）：None 返回在主循环视作 wake 信号
        if getattr(repl, "state", None) == SessionState.IDLE:
            session = getattr(repl, "_session", None)
            if session is not None:
                try:
                    session.app.exit()
                except Exception:  # noqa: BLE001, S110 —— 打断失败下轮再试
                    pass


async def wait_for_lead_mail(repl) -> None:
    """Lead idle 自动续推（F11.4）：事件触发 → IDLE 走 begin_autonomous_turn。"""
    while True:
        await repl._lead_mail_event.wait()
        repl._lead_mail_event.clear()
        if getattr(repl, "state", None) == SessionState.IDLE:
            try:
                await repl._begin_autonomous_turn()
            except Exception:  # noqa: BLE001, S110 —— 自动续推失败不崩后台任务
                pass
