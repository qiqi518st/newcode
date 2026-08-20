from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .models import TYPE_SCOPE, MemoryNote, MemoryOperation
from .prompts import build_memory_prompt
from .store import MemoryStore


class MemoryManager:
    def __init__(
        self,
        project_dir: str | Path,
        user_dir: str | Path,
        provider=None,
        model: str | None = None,
    ):
        self.project_store = MemoryStore(project_dir)
        self.user_store = MemoryStore(user_dir)
        self.provider = provider
        self.model = model
        self._tasks = {}

    def set_provider(self, provider, model: str | None = None):
        self.provider = provider
        self.model = model or getattr(provider, "model", None)

    def load_indexes(self) -> str:
        """注入用索引文本：无记忆时返回空串；有记忆时前置读取指引（spec F13 加载闭环）。"""
        p = self.project_store.load_index()
        u = self.user_store.load_index()
        joined = "\n\n".join(x for x in (p, u) if x)
        if not joined.strip():
            return ""
        return (
            "以下为跨会话长期记忆索引。每条记忆一个 Markdown 文件："
            "项目级位于 .mewcode/memory/，用户级位于 ~/.mewcode/memory/。"
            "索引行格式 `- [类型] 标题 (文件名) - 摘要`。"
            "若某条记忆与当前任务相关、需要完整内容，用 read_memory 工具按文件名读取对应文件。"
            "当用户要求记住某条信息时，用 write_memory 工具记录，"
            "禁止用 write_file/Bash 手动创建或修改记忆文件。\n\n"
            + joined
        )

    def indexes(self) -> tuple[str, str]:
        return self.project_store.load_index(), self.user_store.load_index()

    # ── ch09 F13：/memory 子命令支撑 ─────────────────────────────

    def list_notes(self, scope: str = "") -> list:
        """列出两级记忆的笔记；scope 为 project/user/空（全部）。"""
        notes = []
        if scope in ("", "project"):
            notes += self.project_store.list_notes()
        if scope in ("", "user"):
            notes += self.user_store.list_notes()
        return sorted(notes, key=lambda n: n.updated, reverse=True)

    def _find(self, filename: str) -> tuple | None:
        """按文件名定位笔记，返回 (scope, store, note) 或 None。"""
        for scope, store in (("project", self.project_store), ("user", self.user_store)):
            for note in store.list_notes():
                if note.filename == filename:
                    return scope, store, note
        return None

    def show(self, filename: str) -> str | None:
        """按文件名展示单条记忆全文。"""
        found = self._find(filename)
        if found is None:
            return None
        _, _, note = found
        return note.content or ""

    def edit(self, filename: str, content: str) -> MemoryNote | None:
        """就地编辑单条记忆的内容（重写文件与索引）。"""
        found = self._find(filename)
        if found is None:
            raise ValueError("memory not found")
        scope, store, _ = found
        return store.apply(
            MemoryOperation(
                action="update",
                level=scope,
                filename=filename,
                content=content,
            )
        )

    def clear(self, scope: str = "") -> int:
        """清空指定级（project/user/全部）记忆，返回删除条数。"""
        total = 0
        if scope in ("", "project"):
            total += self.project_store.clear()
        if scope in ("", "user"):
            total += self.user_store.clear()
        return total

    async def update_async(self, messages, session_id: str = ""):
        if self.provider is None:
            return []
        key = "memory"
        current = self._tasks.get(key)
        if current and not current.done():
            return None
        task = asyncio.create_task(self._update(messages, session_id))
        self._tasks[key] = task
        return await task

    async def _update(self, messages, session_id):
        try:
            prompt = build_memory_prompt(messages, *self.indexes())
            result = self.provider.stream(prompt)
            chunks = []
            async for event in result:
                if getattr(event, "text", ""):
                    chunks.append(event.text)
            raw = "".join(chunks).strip()
            ops = json.loads(raw) if raw else []
            if not isinstance(ops, list):
                raise TypeError("memory response must be array")
            out = []
            for item in ops:
                op = MemoryOperation(**item)
                store = self.project_store if op.level == "project" else self.user_store
                if op.action == "delete":
                    if not op.filename:
                        raise ValueError("delete requires filename")
                elif op.type not in TYPE_SCOPE or TYPE_SCOPE[op.type] != op.level:
                    raise ValueError("invalid memory scope")
                out.append(store.apply(op, source_session=session_id))
            return out
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            logging.getLogger(__name__).warning("memory update failed: %s", exc)
            return []
