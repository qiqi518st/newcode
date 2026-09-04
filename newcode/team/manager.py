"""Team Manager（ch15 F3-F10）：Team 生命周期 + 成员操作 + Lead 邮箱轮询。

- 构造：校验 `~/.newcode/teams/` 可写；扫描子目录还原 teams dict（坏 JSON 跳过 + 警告，F17.2）
- create：sanitize → 同名后缀 -2/-3 → detect_backend → config.json（Lead 首个成员）
- delete：非 force 有活跃成员拒绝（F7）；force 逐成员 kill → 清 session/worktree → rmtree
- 成员操作：add_member / set_member_active / remove_member——**加锁后先 reload disk members
  再改再原子 save**（F1.7 跨进程丢更新防护）
- handle_task_done：in-process 成员自然结束 → is_active=False + Lead mailbox idle 通知（F12.1）
- poll_lead_mailboxes：Lead 侧邮箱消费（F11.3，TUI 后台任务调用）
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .mailbox import Box, Message, MessageType
from .persistence import (
    atomic_write_json,
    read_json,
    reload_members_from_disk,
    sanitize,
)
from .registry import AgentNameRegistry
from .types import (
    BackendType,
    MemberExistsError,
    MemberNotFoundError,
    Team,
    TeamHasActiveMembersError,
    TeammateInfo,
    TeamNotFoundError,
)


# 检测函数惰性导入（backend 包在 T9 实现；避免模块加载期顺序依赖）
def _detect_backend() -> BackendType:
    from .backend.detect import detect

    return detect()


def _new_backend(t: BackendType, **deps):
    from .backend import new_backend

    return new_backend(t, **deps)


@dataclass
class LeadMessage:
    """Lead 邮箱未读消息（F11.3 消费单元）。"""

    team_name: str
    from_: str
    type: str
    summary: str
    content: str
    payload: dict | None = None
    timestamp: int = 0


class Manager:
    """单 newcode 进程内的多个 Team 管理（F3）。"""

    def __init__(
        self,
        home_dir: str,
        project_root: str,
        wt_mgr: object | None,
        task_mgr: object | None,
        registry: AgentNameRegistry | None = None,
    ) -> None:
        self.home_dir = str(Path(home_dir))
        self.project_root = str(Path(project_root))
        self.wt_mgr = wt_mgr
        self.task_mgr = task_mgr
        self.registry = registry or AgentNameRegistry()
        self.teams_root = Path(self.home_dir) / ".newcode" / "teams"
        self._lock = asyncio.Lock()
        self.teams: dict[str, Team] = {}
        self._active: str | None = None  # Lead 侧协作工具解析的「当前团队」
        self.teams_root.mkdir(parents=True, exist_ok=True)
        self._scan()
        if self.teams:
            self._active = min(self.teams)

    # ── 构造期扫描（F17.2）────────────────────────────────
    def _scan(self) -> None:
        """扫描 teams 目录还原；坏 config.json 跳过 + stderr 警告（F1.4/N7）。"""
        if not self.teams_root.is_dir():
            return
        for child in self.teams_root.iterdir():
            if not child.is_dir():
                continue
            cfg = child / "config.json"
            if not cfg.is_file():
                continue
            try:
                raw = read_json(cfg)
                if not isinstance(raw, dict):
                    raise TypeError("config.json 非对象")
                team = Team.from_dict(raw)
            except (OSError, ValueError, TypeError) as exc:
                print(
                    f"team: {child.name} config.json 解析失败，跳过: {exc}",
                    file=sys.stderr,
                )
                continue
            self._fill_derived(team)
            self.teams[team.sanitized_name] = team

    def _fill_derived(self, team: Team) -> None:
        """填充派生路径字段（F1.1）。"""
        team.config_dir = str(self.teams_root / team.sanitized_name)
        team.config_path = str(Path(team.config_dir) / "config.json")
        team.tasks_path = str(Path(team.config_dir) / "tasks.json")
        team.mailbox_dir = str(Path(team.config_dir) / "mailbox")

    # ── 查询 ──────────────────────────────────────────────
    def get(self, name: str) -> Team | None:
        return self.teams.get(name)

    def list_(self) -> list[Team]:
        return sorted(self.teams.values(), key=lambda t: t.created_at)

    def member_of(self, agent_id: str) -> tuple[Team, TeammateInfo] | None:
        """按 agent_id 反查所属 Team 与成员。"""
        for team in self.teams.values():
            m = team.member_by_agent_id(agent_id)
            if m is not None:
                return team, m
        return None

    def is_teammate(self, agent_id: str) -> bool:
        """agent_id 是否为某团队的非 lead 成员（TUI 通知选型用）。"""
        found = self.member_of(agent_id)
        return found is not None and found[1].name != "lead"

    def active_team(self) -> Team | None:
        """Lead 侧协作工具解析的当前团队（F7：典型场景单活跃 Team）。"""
        if self._active and self._active in self.teams:
            return self.teams[self._active]
        if self.teams:
            self._active = min(self.teams)
            return self.teams[self._active]
        return None

    # ── 创建 / 删除 ───────────────────────────────────────
    async def create(self, name: str, description: str = "") -> Team:
        """创建 Team（F5）：sanitize → 同名后缀 → 建目录 → detect → Lead 成员。"""
        base = sanitize(name)
        sanitized = base
        suffix = 2
        async with self._lock:
            while sanitized in self.teams:
                sanitized = f"{base}-{suffix}"
                suffix += 1
            backend = _detect_backend()
            config_dir = self.teams_root / sanitized
            config_dir.mkdir(parents=True, exist_ok=False)
            (config_dir / "mailbox").mkdir(parents=True, exist_ok=True)
            team = Team(
                name=name,
                sanitized_name=sanitized,
                lead_agent_id="lead",
                backend=backend,
                description=description,
            )
            self._fill_derived(team)
            team.members = [TeammateInfo(name="lead", agent_id="lead", is_active=None)]
            atomic_write_json(team.config_path, team.to_dict())
            self.teams[sanitized] = team
            self._active = sanitized
            return team

    async def delete(self, name: str, force: bool = False) -> None:
        """删除 Team（F7/F17.4）：非 force 有活跃成员拒绝；force 清理成员资源 + rmtree。"""
        async with self._lock:
            team = self.teams.get(name)
            if team is None:
                raise TeamNotFoundError(f"团队不存在: {name}")
            if not force:
                for m in team.members:
                    if m.name == "lead":
                        continue
                    if m.is_active is not False:  # None 或 True 均视为活跃
                        raise TeamHasActiveMembersError(
                            f"团队 {name} 有活跃成员 {m.name}（is_active={m.is_active}），"
                            "如需强制删除请用 force=True"
                        )
            # 逐个杀成员（backend.kill）
            for m in team.members:
                if m.name == "lead":
                    continue
                try:
                    backend = _new_backend(m.backend_type, task_mgr=self.task_mgr)
                    await backend.kill(m.pane_id, m.agent_id)
                except Exception as exc:  # noqa: BLE001 - kill 失败只警告不中断
                    print(
                        f"team: kill 成员 {m.name} 失败（{exc}），继续清理",
                        file=sys.stderr,
                    )
            # 清 session 目录 + worktree（best-effort）
            self._cleanup_member_resources(team)
            shutil.rmtree(team.config_dir, ignore_errors=True)
            self.teams.pop(name, None)
            if self._active == name:
                self._active = min(self.teams) if self.teams else None

    def _cleanup_member_resources(self, team: Team) -> None:
        """删成员 session 目录与 worktree（best-effort，F7/F17.3）。"""
        for m in team.members:
            if m.name == "lead":
                continue
            if m.session_dir and Path(m.session_dir).is_dir():
                shutil.rmtree(m.session_dir, ignore_errors=True)
        if self.wt_mgr is not None:
            from ..worktree.types import ExitOptions

            for m in team.members:
                if m.name == "lead" or not m.worktree_path:
                    continue
                try:
                    wt = next(
                        (
                            w
                            for w in self.wt_mgr.list()
                            if w.path == str(Path(m.worktree_path).resolve())
                        ),
                        None,
                    )
                    if wt is not None:
                        self.wt_mgr.remove(wt.name, ExitOptions(discard_changes=True))
                except Exception as exc:  # noqa: BLE001 - 清理失败仅警告
                    print(
                        f"team: 清理成员 {m.name} worktree 失败（{exc}）",
                        file=sys.stderr,
                    )

    # ── 成员操作（跨进程 reload-before-modify，F1.7）────────
    async def add_member(self, team: Team, info: TeammateInfo) -> None:
        async with team._lock:
            reload_members_from_disk(team)
            if team.member_by_name(info.name) is not None:
                raise MemberExistsError(f"成员名已存在: {info.name}")
            team.members.append(info)
            atomic_write_json(team.config_path, team.to_dict())

    async def set_member_active(self, team: Team, name: str, active: bool) -> None:
        async with team._lock:
            reload_members_from_disk(team)
            m = team.member_by_name(name)
            if m is None:
                raise MemberNotFoundError(f"成员不存在: {name}")
            m.is_active = active
            atomic_write_json(team.config_path, team.to_dict())

    async def remove_member(self, team: Team, name: str) -> None:
        async with team._lock:
            reload_members_from_disk(team)
            m = team.member_by_name(name)
            if m is None:
                raise MemberNotFoundError(f"成员不存在: {name}")
            team.members.remove(m)
            atomic_write_json(team.config_path, team.to_dict())

    # ── 成员完成通知（F12.1）──────────────────────────────
    async def handle_task_done(self, agent_id: str) -> None:
        """in-process 成员自然结束：is_active=False + Lead mailbox idle 通知（F12.1）。"""
        name = self.registry.name_of(agent_id)
        if not name:
            return
        found = self.member_of(agent_id)
        if found is None:
            return
        team, member = found
        try:
            await self.set_member_active(team, member.name, False)
        except MemberNotFoundError:
            return
        box = Box(team.mailbox_dir)
        await box.write(
            team.lead_agent_id,
            Message(
                from_=member.name,
                to=team.lead_agent_id,
                type=MessageType.TEXT,
                summary=f"{member.name} idle",
                content=f"agent {member.agent_id} finished work, available for new tasks",
            ),
        )

    # ── Lead 邮箱轮询（F11.3）────────────────────────────
    async def poll_lead_mailboxes(self) -> list[LeadMessage]:
        """遍历各 Team 读 lead.json 未读 → 标 read → 返回（TUI 后台任务调用）。"""
        result: list[LeadMessage] = []
        for team in self.list_():
            box = Box(team.mailbox_dir)
            indices, msgs = await box.read_unread(team.lead_agent_id)
            if not msgs:
                continue
            await box.mark_read(team.lead_agent_id, indices)
            for m in msgs:
                result.append(
                    LeadMessage(
                        team_name=team.sanitized_name,
                        from_=m.from_,
                        type=m.type.value,
                        summary=m.summary,
                        content=m.content,
                        payload=m.payload,
                        timestamp=m.timestamp,
                    )
                )
        return result

    # ── ch15 收尾 F3：孤儿 team worktree 自动清扫 ──────────
    def _team_of_worktree(self, name: str) -> str | None:
        """`team-<sanitized>/<member>`（嵌套 slug）→ sanitized；非 team worktree 返回 None。"""
        if not name.startswith("team-"):
            return None
        return name[len("team-") :].split("/", 1)[0]

    async def sweep_orphan_worktrees(self) -> list[str]:
        """清扫团队配置已不存在的 team-* worktree（F3.1-F3.3，fail-closed）。

        - 配置存在（含损坏但文件在）→ 保留（F3.2）
        - 配置不存在 → `wt_mgr.remove(discard_changes=True)`（ch14 内部 fail-closed：
          有未提交变更/未推送 commit 抛错 → 保留）
        - 单目录失败 continue（F3.5/N2）；返回已移除名字列表
        """
        if self.wt_mgr is None:
            return []
        from ..worktree.types import ExitOptions

        removed: list[str] = []
        for wt in self.wt_mgr.list():
            sanitized = self._team_of_worktree(wt.name)
            if sanitized is None:
                continue
            if (self.teams_root / sanitized / "config.json").exists():
                continue  # 团队仍存在（fail-closed）
            try:
                await self.wt_mgr.remove(wt.name, ExitOptions(discard_changes=True))
                removed.append(wt.name)
            except Exception as exc:  # noqa: BLE001 —— 有变更/失败 → 保留
                print(
                    f"team: 孤儿 worktree {wt.name} 清扫失败（保留）: {exc}",
                    file=sys.stderr,
                )
        return removed

    async def run(self, interval_seconds: float) -> None:
        """周期孤儿清扫循环（F3.4，仿 worktree sweep F6.5；单轮失败不退出）。"""
        while True:
            try:
                await self.sweep_orphan_worktrees()
            except Exception as exc:  # noqa: BLE001 —— 单轮失败不终止循环
                print(f"team: 孤儿清扫周期失败: {exc}", file=sys.stderr)
            await asyncio.sleep(interval_seconds)
