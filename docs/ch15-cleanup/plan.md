# MewCode ch15 收尾 - 团队清理引导与孤儿 Worktree 自动清扫 Plan

## 架构概览

三个小改动，全部落在既有模块上（无新包）：

1. **层1 提示词纪律**——`team/cleanup.py` 定义常量 `TEAM_CLEANUP_DISCIPLINE`；`main.py` 在构造主 Agent 前（团队功能启用时）追加到 `stable_prompt`
2. **层2a execute_command 守卫**——`team/cleanup.py` 提供 `guard_team_git_cleanup(mgr, command)`；`tools/shell.py` 的 `ExecuteCommandTool` 加可选 `guard` 注入；`main.py` 装配时注入
3. **层2b 孤儿清扫**——`team/manager.py` 加 `sweep_orphan_worktrees()` + `run(interval)` 周期循环；`team/tools/team_delete.py` 删队成功后补扫；`main.py` 启动时一次 + 起周期任务

依赖方向不变：`team` 是叶子核心，`tools/shell` 只持有可选 `Callable` 守卫（不 import team，注入解耦）；`main.py` 做装配。

## 核心数据结构 / 接口

```python
# mewcode/team/cleanup.py（新）
TEAM_CLEANUP_DISCIPLINE: str = (
    "## 团队清理纪律\n"
    "删除/清理团队必须调用 TeamDelete 工具，或提示用户使用 /team delete <name> [--force]；\n"
    "禁止手动执行 git worktree / git branch 命令清理团队成员 worktree。"
)

def guard_team_git_cleanup(mgr, command: str) -> str | None:
    """命中「团队仍存在」的 team worktree 手动 git 清理 → 返回拦截提示；否则 None（放行）。"""

# mewcode/team/manager.py（修改）
def sweep_orphan_worktrees(self) -> list[str]:
    """清扫团队配置已不存在的 team-* worktree（fail-closed）；返回移除的名字列表。"""

async def run(self, interval_seconds: float) -> None:
    """周期孤儿清扫循环（仿 worktree sweep F6.5；单轮失败不退出）。"""

# mewcode/tools/shell.py（修改）
class ExecuteCommandTool:
    def __init__(self, guard: Callable[[str], str | None] | None = None): ...
    # execute 开头：if self._guard: hint = self._guard(command); if hint: return error(hint)
```

## 模块设计

### 1. team/cleanup.py（新）

- `TEAM_CLEANUP_DISCIPLINE` 常量（F1.1，中文，含「TeamDelete」「/team delete」「禁止」）
- `guard_team_git_cleanup(mgr, command)`（F2.1-F2.4）：
  1. 若 command 不含 `git branch -D` / `git branch -d` / `git worktree remove` → 返回 None（非清理类，F2.5 放行 merge/status/diff）
  2. 从 command 提取候选团队名：
     - `branch -D <worktree-team-<s>+<m> ...>` → 每个分支名去 `worktree-` 前缀、取 `team-` 后到 `+` 前 → sanitized
     - `worktree remove <...worktrees/team-<s>+<m>...>` → 路径段 `worktrees/team-` 后到 `+` 前 → sanitized
     - 无候选 → None（`git worktree prune` 无目标参数，天然不命中）
  3. 任一候选 `mgr.get(sanitized)` 命中（团队仍存在）→ 返回 F2.3 拦截提示（含 `/team delete` 与正确顺序说明）
  4. 团队配置已不存在（孤儿）→ None（放行，F2.4；本体由层2b 接管）
- 匹配用简单字符串/正则（command 是 shell 文本，tokenize 处理引号后匹配）

### 2. tools/shell.py（修改）

- `ExecuteCommandTool.__init__` 加 `guard: Callable[[str], str | None] | None = None`
- `execute` 开头：解析出 command 后，`if self._guard is not None: hint = self._guard(command); if hint: return ToolResult(status="error", error=hint)`——**不执行、不弹权限确认**（F2.3）
- guard 为 None 时行为与现状完全一致（N6）

### 3. team/manager.py（修改）

- `sweep_orphan_worktrees() -> list[str]`（F3.1-F3.3）：
  1. `wt_mgr` 为 None → 返回 []
  2. 遍历 `wt_mgr.list()`，名字前缀 `team-`：
     - 提取 team sanitized（`team-` 后到第一个 `/`——worktree name 是嵌套 slug `team-<s>/<member>`）
     - `Path(self.teams_root)/<s>/config.json` 存在 → 保留（F3.2 fail-closed）
     - 不存在 → 调 `wt_mgr.remove(wt.name, ExitOptions(discard_changes=True))`（ch14 内部会做变更保护 fail-closed：有变更/未推送 commit 抛错 → 保留）
  3. 单目录失败 continue，返回已移除列表（F3.5/N2）
- `async run(interval_seconds)`：`while True: sweep_orphan_worktrees(); await sleep(interval)`，单轮异常 stderr 记录后继续（仿 worktree sweep F6.5）

### 4. team/tools/team_delete.py（修改）

- `execute` 成功删除后：`await mgr.sweep_orphan_worktrees()` 补扫（F3.4），失败仅 stderr 记录不阻断返回（N2）

### 5. main.py（修改）

1. 团队功能启用（`team_mgr is not None`）时：构造 Agent 前 `stable_prompt += "\n\n" + TEAM_CLEANUP_DISCIPLINE`（F1.1；与 coordinator suffix 同位置，先拼纪律再拼 coordinator）
2. 构造 `ExecuteCommandTool(guard=lambda cmd: team_cleanup_guard(team_mgr, cmd))`（守卫注入；team_mgr None 时不注入，工具行为不变，F2.7）
   - 注意：`Registry.default()` 已构造 ExecuteCommandTool——需在装配处替换为带 guard 的实例（`registry.register(ExecuteCommandTool(guard=...))` 覆盖）
3. 启动时 `await team_mgr.sweep_orphan_worktrees()`（F3.4 启动一次，best-effort 不阻断）
4. 起周期任务 `asyncio.create_task(team_mgr.run(worktrees_cfg.cleanup_interval_minutes*60))`（F3.4 周期；finally 取消，仿 sweep_task）

### 6. 版本

- `mewcode/__init__.py` + `pyproject.toml`：`0.15.0` → `0.15.1`（N7）

## 模块交互

```
① 层1：main.py 装配 → stable_prompt += TEAM_CLEANUP_DISCIPLINE → Agent(cleanup 纪律在提示词)
② 层2a：agent 调 execute_command("git branch -D worktree-team-demo+alice")
        → ExecuteCommandTool.execute → guard_team_git_cleanup(mgr, cmd)
        → mgr.get("demo") 命中 → ToolResult(error="请改用 /team delete ...")
        → agent 看到引导，改调 TeamDelete
③ 层2b：启动 / 周期 / TeamDelete 后 → Manager.sweep_orphan_worktrees
        → 遍历 wt_mgr.list() 找 team-* 且配置不存在 → wt_mgr.remove（内部变更保护）
```

## 文件组织

```
mewcode/team/cleanup.py              新建：TEAM_CLEANUP_DISCIPLINE + guard_team_git_cleanup
mewcode/team/manager.py              修改：sweep_orphan_worktrees + run(interval)
mewcode/team/tools/team_delete.py    修改：删队成功后补扫
mewcode/tools/shell.py               修改：ExecuteCommandTool guard 注入
mewcode/main.py                      修改：提示词 + guard 装配 + 启动/周期清扫
mewcode/__init__.py + pyproject.toml 修改：0.15.1
tests/test_team_cleanup.py           新建：守卫匹配 + 清扫测试
```

## 技术决策

| # | 决策点 | 选择 | 理由 |
|---|--------|------|------|
| TD-1 | 守卫落点 | `execute_command` 工具守卫（`team/cleanup.py` + shell 注入），不依赖 hook Engine | 无需改 Engine（无程序化注册接口）；语义精确、改动最小（已确认） |
| TD-2 | 拦截范围 | 仅 `git branch -D` / `git worktree remove` 且目标 `team-*` 且团队配置仍存在 | F15 收敛（merge/status/diff）与孤儿手动清理不受影响；`git worktree prune` 无目标参数天然不命中 |
| TD-3 | 孤儿判定 | 团队配置 `config.json` 不存在（fail-closed：存在/损坏但文件在 → 保留） | 防误删活跃团队 worktree |
| TD-4 | 孤儿清理 | 复用 `wt_mgr.remove(..., discard_changes=True)`（ch14 内部 fail-closed：有变更/未推送 commit 抛错保留） | 不重复实现 git 操作；保护语义一致 |
| TD-5 | 清扫时机 | 启动一次 + 周期（复用 worktree cleanup_interval_minutes）+ TeamDelete 后补扫 | 覆盖最全（已确认） |
| TD-6 | 周期任务 | team `Manager.run(interval)` asyncio 循环（仿 worktree sweep），main.py 起 task、finally 取消 | 与 ch14 sweep 模式一致，错误隔离 |
| TD-7 | 守卫装配 | `Registry.default()` 后 `registry.register(ExecuteCommandTool(guard=...))` 覆盖同名 | 默认注册表已含 shell 工具，覆盖注入即可；team 未启用时仍用默认（无 guard） |
| TD-8 | 版本 | 0.15.1（patch，ch15 收尾） | 版本管理规则：同章节 bug/增强升 patch |

## spec 覆盖对照

- F1（提示词纪律）→ team/cleanup.py + main.py 装配
- F2（守卫）→ team/cleanup.py + tools/shell.py + main.py
- F3（孤儿清扫）→ team/manager.py + team/tools/team_delete.py + main.py
- N7（版本）→ 0.15.1 两处
