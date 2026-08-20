from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import TYPE_SCOPE, MemoryNote, MemoryOperation

_SAFE = re.compile(r"^[a-z0-9][a-z0-9_-]*\.md$")


class MemoryStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser().resolve()
        self._lock = threading.RLock()

    @property
    def index_path(self) -> Path:
        return self.directory / "MEMORY.md"

    def ensure_dir(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def _safe(self, filename: str) -> Path:
        if not _SAFE.fullmatch(filename) or Path(filename).name != filename:
            raise ValueError("invalid memory filename")
        p = (self.directory / filename).resolve()
        if not p.is_relative_to(self.directory):
            raise ValueError("memory path escapes scope")
        return p

    def load_index(self) -> str:
        try:
            text = self.index_path.read_text(encoding="utf-8")
        except OSError:
            return ""
        if len(text.encode("utf-8")) <= 25 * 1024 and len(text.splitlines()) <= 200:
            return text
        out = []
        size = 0
        for line in text.splitlines(True):
            if size + len(line.encode("utf-8")) > 25 * 1024:
                break
            out.append(line)
            size += len(line.encode("utf-8"))
        return "".join(out).rstrip() + "\n(index truncated)"

    def list_notes(self) -> list[MemoryNote]:
        self.ensure_dir()
        result = []
        for p in self.directory.glob("*.md"):
            if p.name == "MEMORY.md":
                continue
            try:
                result.append(self._parse(p))
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda n: n.updated, reverse=True)

    def _parse(self, path: Path) -> MemoryNote:
        text = path.read_text(encoding="utf-8")
        meta = {}
        body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                for line in parts[1].splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
                body = parts[2].lstrip("\n")
        return MemoryNote(
            filename=path.name,
            type=meta.get("type", ""),
            title=meta.get("title", path.stem),
            content=body,
            scope=meta.get("scope", ""),
            created=meta.get("created", ""),
            updated=meta.get("updated", ""),
            source_session=meta.get("source_session", ""),
            status=meta.get("status", "active"),
        )

    def _write_index(self) -> None:
        # 索引行携带文件名：Agent 需要详情时可按文件名用 read_memory 工具读取；
        # 提取 LLM 也能据此引用已有文件做 update/delete（而非靠 slug 猜）。
        lines = [
            f"- [{n.type}] {n.title} ({n.filename}) - {n.content.strip().splitlines()[0][:120] if n.content.strip() else ''}"
            for n in self.list_notes()
        ]
        if len(lines) > 200:
            raise ValueError("memory index exceeds 200 lines")
        content = "\n".join(lines) + ("\n" if lines else "")
        if len(content.encode("utf-8")) > 25 * 1024:
            raise ValueError("memory index exceeds 25KB")
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, self.index_path)

    def apply(
        self, operation: MemoryOperation | dict, *, source_session: str = ""
    ) -> MemoryNote | None:
        op = (
            operation
            if isinstance(operation, MemoryOperation)
            else MemoryOperation(**operation)
        )
        if op.action not in {"create", "update", "delete"} or op.level not in {
            "user",
            "project",
        }:
            raise ValueError("invalid memory operation")
        if op.type and TYPE_SCOPE.get(op.type) != op.level:
            raise ValueError("memory type/scope mismatch")
        with self._lock:
            self.ensure_dir()
            filename = op.filename or f"{op.type}_{op.slug}.md"
            path = self._safe(filename)
            if op.action == "delete":
                if path.exists():
                    path.unlink()
                self._write_index()
                return None
            now = datetime.now(timezone.utc).isoformat()
            old = self._parse(path) if path.exists() else None
            note = MemoryNote(
                op.type or (old.type if old else ""),
                op.title or (old.title if old else path.stem),
                op.content or "",
                op.level,
                old.created if old else now,
                now,
                source_session,
                "active",
                path.name,
            )
            if note.type not in TYPE_SCOPE or TYPE_SCOPE[note.type] != op.level:
                raise ValueError("invalid memory type")
            fm = {
                "type": note.type,
                "title": note.title,
                "created": note.created,
                "updated": note.updated,
                "scope": note.scope,
                "source_session": note.source_session,
                "status": note.status,
            }
            text = (
                "---\n"
                + "\n".join(f"{k}: {v}" for k, v in fm.items())
                + "\n---\n\n"
                + note.content.rstrip()
                + "\n"
            )
            created_new = not path.exists()
            old_text = path.read_text(encoding="utf-8") if old is not None else None
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            try:
                self._write_index()
            except Exception:
                # 原子性：索引失败时回滚刚写入的笔记，保留旧文件和旧索引（spec F13）
                if created_new:
                    path.unlink(missing_ok=True)
                elif old_text is not None:
                    rollback = path.with_suffix(".rollback")
                    rollback.write_text(old_text, encoding="utf-8")
                    os.replace(rollback, path)
                raise
            return note

    def clear(self) -> int:
        with self._lock:
            count = 0
            for n in self.list_notes():
                try:
                    self._safe(n.filename or "").unlink()
                    count += 1
                except OSError:
                    pass
            self._write_index()
            return count
