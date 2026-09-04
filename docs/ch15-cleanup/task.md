# NewCode ch15 收尾 - 团队清理引导与孤儿 Worktree 自动清扫 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `newcode/team/cleanup.py` | TEAM_CLEANUP_DISCIPLINE + guard_team_git_cleanup |
| 修改 | `newcode/tools/shell.py` | ExecuteCommandTool guard 注入 |
| 修改 | `newcode/team/manager.py` | sweep_orphan_worktrees + run(interval) |
| 修改 | `newcode/team/tools/team_delete.py` | 删队成功后补扫 |
| 修改 | `newcode/main.py` | 提示词 + guard 装配 + 启动/周期清扫 |
| 修改 | `newcode/__init__.py` + `pyproject.toml` | 版本 0.15.1 |
| 新建 | `tests/test_team_cleanup.py` | 守卫 + 清扫测试 |

## T1: team/cleanup.py（纪律常量 + 守卫）

**文件：** `newcode/team/cleanup.py`
**依赖：** 无
**步骤：**
1. `TEAM_CLEANUP_DISCIPLINE` 常量（F1.1，中文：必须用 TeamDelete 或 /team delete，禁止手动 git worktree/branch 清理团队）
2. `guard_team_git_cleanup(mgr, command) -> str | None`（F2.1-F2.4）：
   - 非 `git branch -D`/`-d`/`git worktree remove` → None
   - 提取候选团队：`branch -D <worktree-team-<s>+<m>>`（去 worktree- 前缀、team- 后到 + 前）；`worktree remove <...worktrees/team-<s>+<m>...>`（worktrees/team- 后到 + 前）
   - 任一候选 `mgr.get(s)` 命中 → 返回拦截提示（含「请改用 /team delete <s> --force 或 TeamDelete」+ 正确顺序 kill→worktree remove→branch -D→删配置）
   - 候选团队配置不存在 → None（放行）

**验证：** 单测：`git branch -D worktree-team-demo+alice`（团队在）→ 拦截；`git merge worktree-team-demo+alice` → None；`git status` → None；团队已删 → None；非团队 `worktree-agent-a1b2c3d` → None

## T2: tools/shell.py guard 注入

**文件：** `newcode/tools/shell.py`
**依赖：** T1
**步骤：**
1. `ExecuteCommandTool.__init__` 加 `guard: Callable[[str], str | None] | None = None`
2. `execute` 解析出 command 后：`if self._guard: hint = self._guard(command); if hint: return ToolResult(status="error", error=hint)`（不执行、不弹权限确认，F2.3）
3. guard 默认 None → 行为与现状一致（N6）

**验证：** 单测：注入 guard 返回 hint → execute 返回 error 不执行；guard=None → 正常路径

## T3: team/manager.py 孤儿清扫

**文件：** `newcode/team/manager.py`
**依赖：** 无
**步骤：**
1. `sweep_orphan_worktrees() -> list[str]`（F3.1-F3.3）：遍历 `wt_mgr.list()` 名字前缀 `team-` → 提取 sanitized（team- 后到第一个 /）→ `teams_root/<s>/config.json` 不存在 → `wt_mgr.remove(wt.name, ExitOptions(discard_changes=True))`（ch14 内部变更保护 fail-closed）→ 记录 removed；单目录失败 continue
2. `async run(interval_seconds)`：`while True: sweep_orphan_worktrees(); await sleep(interval)`，单轮异常 stderr 后继续（仿 worktree sweep）

**验证：** 单测（mock wt_mgr）：配置存在 → 保留；配置删 → remove 被调；有变更抛错 → 保留；run 循环 sleep 后重扫

## T4: team_delete.py 补扫

**文件：** `newcode/team/tools/team_delete.py`
**依赖：** T3
**步骤：**
1. `execute` 成功删除后 `await self._mgr.sweep_orphan_worktrees()`（F3.4 删队后补扫）；失败仅 stderr 不阻断返回（N2）

**验证：** 单测：TeamDelete 成功后 orphan sweep 被调用（mock）

## T5: main.py 装配

**文件：** `newcode/main.py`
**依赖：** T1-T4
**步骤：**
1. 团队启用时：构造 Agent 前 `stable_prompt += "\n\n" + TEAM_CLEANUP_DISCIPLINE`（先纪律后 coordinator suffix）
2. guard 装配：`registry.register(ExecuteCommandTool(guard=lambda cmd: guard_team_git_cleanup(team_mgr, cmd)))` 覆盖默认 shell 工具（team_mgr None 时不覆盖）
3. 启动时 `await team_mgr.sweep_orphan_worktrees()`（best-effort try/except）
4. 起周期任务 `asyncio.create_task(team_mgr.run(worktrees_cfg.cleanup_interval_minutes*60))`；finally 取消（仿 sweep_task）

**验证：** `python -m newcode --version` 正常；启动不崩；团队未启用时行为不变

## T6: 版本 bump 0.15.1

**文件：** `newcode/__init__.py`、`pyproject.toml`
**依赖：** 无
**步骤：**
1. 两处 `0.15.0` → `0.15.1`

**验证：** `grep -r "0.15.1" newcode/__init__.py pyproject.toml` 两处一致

## T7: 测试批 + 回归

**文件：** `tests/test_team_cleanup.py`
**依赖：** T1-T6
**步骤：**
1. `test_team_cleanup.py`：守卫匹配（拦截/放行矩阵，F2）、孤儿清扫（配置在/删/有变更，F3）、纪律常量内容
2. `ruff format` + `ruff check`；确认 docs/ 未改（N6/文档保护）
3. 全量 `pytest` 通过（ch04~ch15 存量零回归）

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && .venv/bin/python -m pytest tests/ -q
export PYTHONIOENCODING=utf-8 && .venv/bin/python -m ruff check .
export PYTHONIOENCODING=utf-8 && .venv/bin/python -m newcode --version
```

## 执行顺序

```
T1 → T2 → T3 → T4（T3 先于 T4）→ T5 → T6（可并行）→ T7（全部完成后）
```
