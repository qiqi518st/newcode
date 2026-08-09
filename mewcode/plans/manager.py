"""PlanManager：plan 文件 CRUD + .meta.json 索引 + 清理 + 自愈"""

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

_SLUG_PATTERN = re.compile(r"<!--\s*slug:\s*([^>]+?)\s*-->", re.IGNORECASE)


@dataclass
class PlanMeta:
    """单个 plan 的元数据"""
    slug: str                    # 唯一标识，如 "create-hello-world"
    file: str                    # 文件名，如 "create-hello-world.md"
    task: str                    # 任务描述（从 plan 内容提取）
    created_at: str              # ISO 时间戳，如 "2026-08-08T15:30:00"
    executed_at: str | None = None  # 最近执行时间，None 表示未执行

    @property
    def executed(self) -> bool:
        """是否已执行过"""
        return self.executed_at is not None


class PlanManager:
    """所有 plan 文件操作的统一入口"""

    def __init__(self, plans_dir: str) -> None:
        self._plans_dir = plans_dir
        os.makedirs(plans_dir, exist_ok=True)
        self._meta_path = os.path.join(plans_dir, ".meta.json")

    # ── 元数据读写 ──

    def _load_meta(self) -> dict:
        """加载 .meta.json，损坏或不存在返回空 dict"""
        if not os.path.exists(self._meta_path):
            return {}
        try:
            with open(self._meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_meta(self, data: dict) -> None:
        """保存 .meta.json"""
        os.makedirs(self._plans_dir, exist_ok=True)
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── 内容解析 ──

    def _extract_slug(self, content: str) -> str:
        """从 plan 内容中提取 slug，未声明则回退日期格式"""
        m = _SLUG_PATTERN.search(content or "")
        if m:
            slug = m.group(1).strip()
            # 只保留小写字母、数字、连字符；大写转小写
            slug = re.sub(r"[^a-zA-Z0-9\-]", "-", slug).strip("-").lower()
            if slug:
                return slug
        return datetime.now().strftime("plan-%Y%m%d-%H%M%S")

    def _extract_task(self, content: str) -> str:
        """从 plan 内容中提取任务描述：首个 # 标题，或首个非注释行"""
        for line in (content or "").strip().split("\n"):
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
            if line and not line.startswith("<!--"):
                return line[:80]
        return "未命名计划"

    # ── 对外 API ──

    def create_plan(self, task: str, content: str) -> str:
        """创建 plan 文件和元数据，返回 slug"""
        slug = self._extract_slug(content)
        filename = f"{slug}.md"
        filepath = os.path.join(self._plans_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        meta = self._load_meta()
        meta[slug] = {
            "file": filename,
            "task": self._extract_task(content) or task,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "executed_at": None,
        }
        self._save_meta(meta)
        return slug

    def list_plans(self) -> list[PlanMeta]:
        """列出所有 plan（含自愈校验），按创建时间倒序"""
        meta = self._load_meta()
        stale = []
        plans: list[PlanMeta] = []

        for slug, data in meta.items():
            filename = data.get("file", f"{slug}.md")
            filepath = os.path.join(self._plans_dir, filename)
            if not os.path.exists(filepath):
                # 自愈：.md 文件被手动删除，清除对应元数据条目
                stale.append(slug)
                continue
            plans.append(PlanMeta(
                slug=slug,
                file=filename,
                task=data.get("task", ""),
                created_at=data.get("created_at", ""),
                executed_at=data.get("executed_at"),
            ))

        if stale:
            for slug in stale:
                meta.pop(slug, None)
            self._save_meta(meta)

        plans.sort(key=lambda p: p.created_at, reverse=True)
        return plans

    def get_plan(self, slug: str) -> PlanMeta | None:
        """获取单个 plan 元数据"""
        meta = self._load_meta()
        data = meta.get(slug)
        if data is None:
            return None
        return PlanMeta(
            slug=slug,
            file=data.get("file", f"{slug}.md"),
            task=data.get("task", ""),
            created_at=data.get("created_at", ""),
            executed_at=data.get("executed_at"),
        )

    def read_plan_content(self, slug: str) -> str:
        """读取 plan 文件内容"""
        meta = self._load_meta()
        data = meta.get(slug)
        if data is None:
            return ""
        filename = data.get("file", f"{slug}.md")
        filepath = os.path.join(self._plans_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def mark_executed(self, slug: str) -> None:
        """标记 plan 已执行，刷新 executed_at"""
        meta = self._load_meta()
        if slug in meta:
            meta[slug]["executed_at"] = datetime.now().isoformat(timespec="seconds")
            self._save_meta(meta)

    def delete_plans(self, slugs: list[str]) -> None:
        """删除指定 plan 的文件和元数据"""
        meta = self._load_meta()
        for slug in slugs:
            data = meta.pop(slug, None)
            if data:
                filename = data.get("file", f"{slug}.md")
                filepath = os.path.join(self._plans_dir, filename)
                try:
                    os.remove(filepath)
                except OSError:
                    pass
        self._save_meta(meta)

    def cleanup_old(self, days: int) -> int:
        """清理超过 N 天的 plan，返回删除数量。days<=0 时不清理"""
        if days <= 0:
            return 0
        cutoff = datetime.now() - timedelta(days=days)
        meta = self._load_meta()
        to_delete: list[str] = []

        for slug, data in meta.items():
            created_at = data.get("created_at", "")
            try:
                created = datetime.fromisoformat(created_at)
            except (ValueError, TypeError):
                continue
            if created < cutoff:
                to_delete.append(slug)

        if to_delete:
            self.delete_plans(to_delete)
        return len(to_delete)
