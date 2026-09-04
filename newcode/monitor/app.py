"""Standalone terminal monitor for NewCode provider request history."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame

from .protocol import MonitorLease


def _workspace_records(workspace: str) -> list[dict[str, Any]]:
    root = Path(workspace) / ".newcode" / "sessions"
    records: list[dict[str, Any]] = []
    if not root.exists():
        return records
    for path in root.glob("*/requests/request-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            record["_path"] = str(path)
            records.append(record)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
    return sorted(
        records,
        key=lambda item: float(item.get("recorded_at", 0)),
        reverse=True,
    )


def _workspace_groups(workspace: str) -> list[list[dict[str, Any]]]:
    """Group provider calls belonging to one user submission."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in _workspace_records(workspace):
        run_id = record.get("run_id")
        if not run_id:
            run_id = f"legacy:{record.get('request_kind', '')}:{record.get('user_input', '')}"
        key = (str(record.get("session_id", "unknown")), str(run_id))
        grouped.setdefault(key, []).append(record)
    return sorted(
        grouped.values(),
        key=lambda group: float(group[0].get("recorded_at", 0)),
        reverse=True,
    )


def _time_label(value: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(value)))
    except (TypeError, ValueError, OverflowError):
        return "unknown time"


class MonitorApp:
    def __init__(self, workspace: str) -> None:
        self.workspace = os.path.abspath(workspace)
        self.lease = MonitorLease(self.workspace)
        self.groups: list[list[dict[str, Any]]] = []
        self.selected = 0
        self.selected_request = 0
        self.follow_latest = True
        self.detail_offset = 0

        self._left = FormattedTextControl(self._left_content)
        self._right = FormattedTextControl(self._right_content)
        self._app: Application | None = None

    def _build_app(self) -> Application:
        return Application(
            layout=Layout(
                VSplit(
                    [
                        Frame(
                            Window(self._left, wrap_lines=False),
                            title="Sessions / Requests",
                            width=Dimension(min=42, preferred=48, max=58),
                        ),
                        Frame(
                            Window(self._right, wrap_lines=False),
                            title="Latest Provider History",
                        ),
                    ]
                )
            ),
            key_bindings=self._bindings(),
            full_screen=True,
            mouse_support=False,
            refresh_interval=0.5,
        )

    def _refresh_records(self) -> None:
        previous_path = (
            self._current_record().get("_path")
            if self.groups and self._current_record() is not None
            else None
        )
        previous_group_key = (
            self._group_key(self.groups[self.selected]) if self.groups else None
        )
        self.groups = _workspace_groups(self.workspace)
        if not self.groups:
            self.selected = 0
            self.selected_request = 0
            return
        if self.follow_latest:
            latest_path = self.groups[0][0].get("_path")
            self.selected = 0
            self.selected_request = 0
            if latest_path != previous_path:
                self.detail_offset = 0
            return
        if previous_group_key:
            for index, group in enumerate(self.groups):
                if self._group_key(group) == previous_group_key:
                    self.selected = index
                    break
        self.selected = min(self.selected, len(self.groups) - 1)
        current = self.groups[self.selected]
        for index, record in enumerate(current):
            if record.get("_path") == previous_path:
                self.selected_request = index
                break
        self.selected_request = min(self.selected_request, len(current) - 1)

    @staticmethod
    def _group_key(group: list[dict[str, Any]]) -> tuple[str, str]:
        record = group[0]
        run_id = record.get("run_id")
        if not run_id:
            run_id = f"legacy:{record.get('request_kind', '')}:{record.get('user_input', '')}"
        return (str(record.get("session_id", "unknown")), str(run_id))

    def _current_record(self) -> dict[str, Any] | None:
        if not self.groups or not 0 <= self.selected < len(self.groups):
            return None
        group = self.groups[self.selected]
        if not group:
            return None
        index = min(self.selected_request, len(group) - 1)
        return group[index]

    def _left_content(self):
        self._refresh_records()
        fragments = [
            ("bold", f"Workspace: {self.workspace}\n"),
            (
                "",
                (
                    "默认跟随最新请求；上下键选择用户请求，左右键选择其中一次调用，"
                    "PageUp/PageDown 翻页，L 回到最新，Q 退出\n\n"
                ),
            ),
        ]
        if not self.groups:
            fragments.append(("", "等待 NewCode 请求..."))
            return fragments
        for index, group in enumerate(self.groups):
            record = group[0]
            session = record.get("session_id", "unknown")
            pid = record.get("pid", "?")
            user_input = record.get("user_input") or "（内部上下文摘要）"
            marker = "○" if index == self.selected else " "
            style = "reverse bold" if index == self.selected else ""
            fragments.append(
                (
                    style,
                    (
                        f"{marker} {_time_label(record.get('recorded_at'))} "
                        f"pid={pid} {session} "
                        f"请求数={len(group)}\n"
                        f"  {user_input[:52]}\n"
                    ),
                )
            )
        return fragments

    def _right_content(self):
        self._refresh_records()
        record = self._current_record()
        if record is None:
            return "等待监控记录..."
        user_input = record.get("user_input") or "（内部上下文摘要请求）"
        group = self.groups[self.selected]
        header = (
            f"发送时间: {_time_label(record.get('recorded_at'))}\n"
            f"进程: {record.get('pid', '?')}  会话: {record.get('session_id', '?')}\n"
            f"类型: {record.get('request_kind', 'conversation')}\n"
            f"请求: {self.selected_request + 1}/{len(group)}\n"
            f"最近用户输入: {user_input}\n"
            f"\n实际 provider 请求:\n"
        )
        body = json.dumps(
            record.get("provider_request", {}), ensure_ascii=False, indent=2
        )
        lines = (header + body).splitlines()
        visible = lines[self.detail_offset : self.detail_offset + 200]
        return "\n".join(visible)

    def _bindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("up")
        def _up(event) -> None:
            self._refresh_records()
            if self.groups:
                self.follow_latest = False
                self.selected = max(self.selected - 1, 0)
                self.selected_request = 0
                self.detail_offset = 0
                event.app.invalidate()

        @bindings.add("down")
        def _down(event) -> None:
            self._refresh_records()
            if self.groups:
                self.follow_latest = False
                self.selected = min(self.selected + 1, len(self.groups) - 1)
                self.selected_request = 0
                self.detail_offset = 0
                event.app.invalidate()

        @bindings.add("left")
        def _older_request(event) -> None:
            self._refresh_records()
            if self.groups:
                self.follow_latest = False
                self.selected_request = min(
                    self.selected_request + 1, len(self.groups[self.selected]) - 1
                )
                self.detail_offset = 0
                event.app.invalidate()

        @bindings.add("right")
        def _newer_request(event) -> None:
            self._refresh_records()
            if self.groups:
                self.follow_latest = False
                self.selected_request = max(self.selected_request - 1, 0)
                self.detail_offset = 0
                event.app.invalidate()

        @bindings.add("l")
        def _latest(event) -> None:
            self.follow_latest = True
            self.detail_offset = 0
            event.app.invalidate()

        @bindings.add("pageup")
        def _page_up(event) -> None:
            self.detail_offset = max(0, self.detail_offset - 50)
            event.app.invalidate()

        @bindings.add("pagedown")
        def _page_down(event) -> None:
            self.detail_offset += 50
            event.app.invalidate()

        @bindings.add("q")
        @bindings.add("c-c")
        def _quit(event) -> None:
            event.app.exit()

        return bindings

    async def _heartbeat(self) -> None:
        while True:
            self.lease.heartbeat()
            await asyncio.sleep(1)

    async def run(self) -> None:
        self.lease.start()
        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            self._app = self._build_app()
            await self._app.run_async()
        finally:
            heartbeat.cancel()
            self.lease.close()
            await asyncio.gather(heartbeat, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="newcode-monitor",
        description="Monitor NewCode requests sent to LLM providers.",
    )
    parser.add_argument(
        "-w",
        "--workspace",
        default=os.getcwd(),
        help="workspace containing .newcode/sessions",
    )
    args = parser.parse_args()
    asyncio.run(MonitorApp(args.workspace).run())
