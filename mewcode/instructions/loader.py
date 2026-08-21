"""Safe, cached loading of layered MEWCODE.md instruction files."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)
_INCLUDE = re.compile(r"^\s*@include\s+(.+?)\s*$")


@dataclass
class InstructionDiagnostic:
    path: str
    reason: str
    line: int | None = None


@dataclass
class InstructionDocument:
    text: str = ""
    sources: list[str] = field(default_factory=list)
    diagnostics: list[InstructionDiagnostic] = field(default_factory=list)


class InstructionLoader:
    def __init__(
        self,
        project_root: str | Path,
        user_home: str | Path | None = None,
        max_depth: int = 5,
        max_file_size: int = 1_000_000,
        max_total_size: int = 5_000_000,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.user_home = (
            Path(user_home).expanduser() if user_home else Path.home()
        ).resolve()
        self.max_depth, self.max_file_size, self.max_total_size = (
            max_depth,
            max_file_size,
            max_total_size,
        )
        self._cached: InstructionDocument | None = None

    def load(self, *, refresh: bool = False) -> InstructionDocument:
        if self._cached is not None and not refresh:
            return self._cached
        diagnostics: list[InstructionDiagnostic] = []
        chunks: list[str] = []
        sources: list[str] = []
        total = 0
        candidates = [
            self.project_root / "MEWCODE.md",
            self.project_root / ".mewcode" / "MEWCODE.md",
            self.user_home / ".mewcode" / "MEWCODE.md",
        ]
        for path in candidates:
            if not path.is_file():
                continue
            text, srcs = self._expand(path, set(), 1, diagnostics, total)
            remaining = self.max_total_size - total
            if len(text.encode("utf-8")) > remaining:
                text = text.encode("utf-8")[: max(0, remaining)].decode(
                    "utf-8", "ignore"
                )
                diagnostics.append(InstructionDiagnostic(str(path), "total size limit"))
            total += len(text.encode("utf-8", "ignore"))
            if text:
                chunks.append(text)
                sources.extend(srcs)
            if total >= self.max_total_size:
                diagnostics.append(
                    InstructionDiagnostic(str(path), "total size limit reached")
                )
                break
        self._cached = InstructionDocument("\n\n".join(chunks), sources, diagnostics)
        return self._cached

    def text(self) -> str:
        return self.load().text

    def _allowed_root(self, source: Path) -> Path:
        return (
            self.user_home / ".mewcode"
            if source.is_relative_to(self.user_home / ".mewcode")
            else self.project_root
        )

    def _expand(
        self,
        path: Path,
        visited: set[Path],
        depth: int,
        diagnostics: list[InstructionDiagnostic],
        total: int,
    ) -> tuple[str, list[str]]:
        try:
            canonical = path.resolve(strict=True)
        except OSError as exc:
            diagnostics.append(InstructionDiagnostic(str(path), f"unreadable: {exc}"))
            return "", []
        root = self._allowed_root(path)
        if not canonical.is_relative_to(root):
            diagnostics.append(
                InstructionDiagnostic(str(path), "path outside allowed scope")
            )
            return f"<!-- @include 路径超出允许范围，已跳过: {path} -->", [
                str(canonical)
            ]
        if canonical in visited:
            diagnostics.append(
                InstructionDiagnostic(str(path), f"include cycle: {path}")
            )
            return f"<!-- @include 检测到环路，已跳过: {path} -->", [str(canonical)]
        if not canonical.is_file():
            return "", []
        if depth > self.max_depth:
            diagnostics.append(
                InstructionDiagnostic(str(path), "include depth limit", None)
            )
            return f"<!-- @include 超过最大嵌套深度，已跳过: {path} -->", [
                str(canonical)
            ]
        try:
            raw = canonical.read_bytes()
        except OSError as exc:
            diagnostics.append(InstructionDiagnostic(str(path), f"read failed: {exc}"))
            return "", []
        if len(raw) > self.max_file_size:
            diagnostics.append(InstructionDiagnostic(str(path), "file size limit"))
            return "", []
        if b"\x00" in raw[:512]:
            diagnostics.append(InstructionDiagnostic(str(path), "binary file"))
            return "", []
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            diagnostics.append(InstructionDiagnostic(str(path), "invalid utf-8"))
            return "", []
        visited = visited | {canonical}
        out: list[str] = []
        sources = [str(canonical)]
        for lineno, line in enumerate(content.splitlines(keepends=True), 1):
            match = _INCLUDE.fullmatch(line.rstrip("\r\n"))
            if not match:
                out.append(line)
                continue
            target = match.group(1).strip()
            if Path(target).is_absolute() or "\\" in target or target.startswith("~"):
                diagnostics.append(
                    InstructionDiagnostic(
                        str(canonical), "include path must be relative", lineno
                    )
                )
                out.append(f"<!-- @include 路径超出允许范围，已跳过: {target} -->\n")
                continue
            child = canonical.parent / target
            expanded, child_sources = self._expand(
                child,
                visited,
                depth + 1,
                diagnostics,
                total + len("".join(out).encode()),
            )
            out.append(
                expanded
                if expanded
                else f"<!-- @include 未找到或已跳过: {target} -->\n"
            )
            sources.extend(child_sources)
        return "".join(out), sources
