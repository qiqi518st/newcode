# MewCode ch15 收尾 - 团队清理引导与孤儿 Worktree 自动清扫 Spec

## 背景

ch15 已实现 `TeamCreate`/`TeamDelete` 工具 + `/team delete` slash 命令（F16.3），`Manager.delete` 内部按正确顺序清理（`backend.kill` → `git worktree remove` → `git branch -D` → 删 `~/.mewcode/teams/<name>/`）。

但真实会话实测暴露三个问题：

1. **Lead 走手动 git 而非 TeamDelete**——清理团队时 Lead 选择逐条执行 `git worktree remove`/`git branch -D` 而不是调用 TeamDelete 工具。后果：DEFAULT 权限模式下每个 command 类工具调用都弹确认，turn 被吃光直到迭代上限；且 `git branch -D` 对**仍被 worktree 检出的分支**直接失败（exit 1——git 规则：必须先 `git worktree remove` 解除检出才能删分支），命令失败后 agent 重述计划、继续弹窗，10 个 turn 耗尽。
2. **DEFAULT 模式重复弹窗**——command 类工具每次调用都要 ASK 确认（ch06 设计如此），多步手动 git = 多次弹窗。
3. **孤儿 team worktree 无人收**——团队配置被外部删除、或删队时 worktree 清理 best-effort 失败，都会留下 `team-*` worktree。ch14 `sweep_stale` 的 `EPHEMERAL_PATTERN`（`agent-a[0-9a-f]{7}`/`wf-` 前缀）**不覆盖 `team-*`**，孤儿会永久滞留（实测已出现 `team-demo+alice` 孤儿）。

三层对策：**层0 用户习惯**（`/team delete <name> --force` 直接敲，不让 agent 碰）、**层1 Lead 提示词纪律**、**层2a execute_command 守卫拦截**、**层2b 孤儿自动清扫**。

## 目标

- G1（层1）：主 Agent 系统提示词追加团队清理纪律——删除/清理团队必须用 `TeamDelete` 或提示用户 `/team delete`，**禁止**手动 git worktree/branch 清理团队
- G2（层2a）：`execute_command` 对「团队仍存在」的 team worktree 手动 git 清理命令返回**结构化引导错误**（不静默执行、不弹权限确认）
- G3（层2b）：孤儿 team worktree（团队配置已不存在的 `team-*`）**启动 + 周期 + TeamDelete 后**自动清扫
- G4：不误伤正常 git 操作（`git status`/`merge`/`diff`——F15 收敛合并依赖）；不拦非团队 worktree（`agent-*`/`wf-` 走既有 ch14 sweep + `/worktree` 命令）

## 功能需求

### F1 Lead 清理纪律提示（层1）

- F1.1 团队功能启用时，主 Agent `stable_prompt` 追加固定纪律段：「删除/清理团队必须调用 TeamDelete 工具，或提示用户使用 `/team delete <name> [--force]`；**禁止**手动执行 git worktree / git branch 命令清理团队成员 worktree」
- F1.2 纪律段**通用生效**（非 coordinator 的 Lead 也遵守，不只 coordinator 模式）

### F2 execute_command 守卫（层2a）

- F2.1 team 包提供 `guard_team_git_cleanup(mgr, command) -> str | None`：`None`=放行；`str`=拦截提示文案
- F2.2 命中条件：command 匹配 git 清理类操作（`git worktree remove` / `git branch -D` / `git worktree prune`）**且**目标指向 `team-*`（路径含 `.mewcode/worktrees/team-`，或分支名 `worktree-team-`）**且该团队配置仍存在**（`mgr.get(prefix)` 命中）
- F2.3 命中返回：`ToolResult(status="error", error="请改用 /team delete <name> --force 或 TeamDelete 工具清理团队（自动按正确顺序：kill → worktree remove → branch -D → 删配置）")`——**不执行、不弹权限确认**
- F2.4 团队配置已不存在（孤儿）→ **不拦截**（放行正常 git 清理；孤儿本体由层2b 自动清扫接管）
- F2.5 非清理类 git 操作（`status`/`merge`/`diff`/`log`/`add`/`commit` 等）一律不拦截（F15 收敛依赖 `git merge`）
- F2.6 非团队 worktree（`agent-*`/`wf-`）清理不拦截（走 ch14 sweep 或 `/worktree remove`）
- F2.7 守卫经 `ExecuteCommandTool` **可选注入**（`guard: Callable | None`）；团队功能未启用时为 None，工具行为与现状完全一致（N6）

### F3 孤儿 team worktree 自动清扫（层2b）

- F3.1 team `Manager` 提供 `sweep_orphan_worktrees() -> list[str]`：遍历 `wt_mgr.list()`，名字前缀 `team-` 且对应团队配置（`~/.mewcode/teams/<prefix>/config.json`）不存在 → 视为孤儿
- F3.2 孤儿判定 **fail-closed**：配置存在（含损坏但文件在）→ 保留；仅「配置目录/文件不存在」才清
- F3.3 清理：调 `wt_mgr.remove(孤儿名, ExitOptions(discard_changes=True))`；有未提交变更/未推送 commit → 保留（ch14 保护语义，fail-closed）
- F3.4 触发时机：**启动时一次** + **周期后台任务**（间隔复用 worktree `cleanup_interval_minutes`）+ **TeamDelete 成功后立即补扫**
- F3.5 清扫失败不中断：单目录失败继续、周期循环不退出（错误隔离 N2）

## 非功能需求

- N1：**不改变正常 git 工作流**——F15 收敛合并（`git merge`）、日常 git 命令零影响
- N2：**错误隔离**——守卫/清扫失败不影响主流程与 TUI
- N3：**幂等**——重复清扫安全；启动扫描不重复建后台任务
- N4：**中文友好**——拦截提示、清扫日志全中文
- N5：**可诊断**——清扫/拦截有 stderr 记录（孤儿名、保留原因）
- N6：**兼容**——团队功能未启用（guard=None / 无 team worktree）时行为与现状一致；存量测试零回归
- N7：**版本号**——ch15 收尾属 0.15.x patch（`0.15.0` → `0.15.1`），两处一致
- N8：**测试规范**——接线测试自动跑、mock 驱动真实路径、每测试标注防的 bug

## 不做的事

- 拦截用户在**自己终端**里直接敲的 git 命令（mewcode 只能管自己的 agent 工具调用）
- 拦截非团队 worktree（`agent-*`/`wf-`）的手动 git 清理（走既有 `/worktree` + ch14 sweep）
- 引导式 UI（弹窗/菜单）——只用结构化错误 + 提示词
- 新增用户命令（如 `/team prune`）——层2b 全自动，不加新命令
- 改动已批准的 ch15 四份文档（本次为收尾增强，独立目录）

## 验收标准

- AC1（F1.1/F1.2）：团队功能启用时 stable_prompt 含纪律段（含「TeamDelete」「/team delete」「禁止」）；未启用时不追加
- AC2（F2.2/F2.3）：团队 demo 存在时，`git branch -D worktree-team-demo+alice` → ToolResult error 含「/team delete」，**不执行**
- AC3（F2.2/F2.3）：`git worktree remove .mewcode/worktrees/team-demo+alice` → 同上拦截
- AC4（F2.4）：团队配置已删 → 同类命令**不拦截**（放行）
- AC5（F2.5）：`git merge worktree-team-demo+alice --no-ff` / `git status` / `git diff` → **不拦截**（F15 收敛不受影响）
- AC6（F2.6）：`git branch -D worktree-agent-a1b2c3d`（非团队）→ **不拦截**
- AC7（F3.1/F3.3）：构造孤儿（删团队配置后残留 `team-*` worktree）→ `sweep_orphan_worktrees` 删除目录+分支
- AC8（F3.2/F3.3）：配置仍存在 → 不清；孤儿有未提交变更/未推送 commit → 保留（fail-closed）
- AC9（F3.4）：TeamDelete 成功后孤儿被补扫（单测断言）
- AC10（N6/N7）：全量存量测试通过、ruff 通过、版本 0.15.1 两处一致、`python -m mewcode --version` 正常

## 端到端场景（验收参考）

- 场景1（拦截引导）：mewcode 会话中 agent 尝试 `git branch -D worktree-team-demo+alice` → 收到「请改用 /team delete」错误 → 改调 TeamDelete 一次清理成功
- 场景2（孤儿清扫）：手动删除 `~/.mewcode/teams/demo-2/` 配置 → 残留 `team-*` worktree → 下次启动/周期清扫自动移除（目录+分支）
- 场景3（正常收敛不误伤）：F15 的 `git merge worktree-team-*` 在守卫下正常执行
