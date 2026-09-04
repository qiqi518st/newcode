# NewCode ch15 收尾 - 团队清理引导与孤儿 Worktree 自动清扫 Checklist

> 每一项通过运行代码或观察行为验证，聚焦系统行为。

## 实现完整性

- [ ] `TEAM_CLEANUP_DISCIPLINE` 含「TeamDelete」「/team delete」「禁止」关键词（验证：单测断言）
- [ ] `guard_team_git_cleanup` 对 `git branch -D worktree-team-demo+alice`（团队 demo 存在）返回含「请改用 /team delete」的提示（验证：单测）
- [ ] 同命令团队已删（孤儿）→ 返回 None（放行）（验证：单测）
- [ ] `git worktree remove .newcode/worktrees/team-demo+alice`（团队存在）→ 拦截（验证：单测）
- [ ] `git merge worktree-team-demo+alice --no-ff` / `git status` / `git diff` → 不拦截（F15 收敛不受影响）（验证：单测）
- [ ] `git branch -D worktree-agent-a1b2c3d`（非团队）→ 不拦截（验证：单测）
- [ ] `ExecuteCommandTool` 注入 guard 时，命中命令返回 error、不执行、不弹权限确认（验证：单测断言 execute 返回 error）
- [ ] `ExecuteCommandTool` guard 默认 None → 行为与现状一致（验证：存量 shell 测试全绿）
- [ ] `sweep_orphan_worktrees` 对「配置不存在的 team-* worktree」删除目录+分支（验证：mock wt_mgr 单测）
- [ ] 团队配置仍存在 → 保留；孤儿有未提交变更/未推送 commit → 保留（fail-closed）（验证：单测）
- [ ] TeamDelete 成功后孤儿补扫被触发（验证：mock 单测）
- [ ] 启动时 + 周期任务触发孤儿清扫（验证：main 装配单测/冒烟）

## 集成

- [ ] 团队功能启用时主 Agent stable_prompt 含纪律段；未启用时不追加（验证：装配单测）
- [ ] 纪律段在 coordinator 之外通用生效（非 coordinator 的 Lead 也遵守）（验证：stable_prompt 内容断言）
- [ ] 周期清扫任务在 main finally 被取消（验证：装配冒烟）

## 编译与测试

- [ ] 版本 0.15.1 在 `newcode/__init__.py` 与 `pyproject.toml` 两处一致（验证：grep）
- [ ] `ruff check .` 无警告；`ruff format --check .` 无未格式化（验证：退出码 0）
- [ ] `pytest tests/` 全部通过（含 ch04~ch15 存量零回归）（验证：退出码 0）
- [ ] 跑批后 `docs/` 未被改动（除 ch15-cleanup 四份 new-spec 文档）（验证：git status）

## 端到端场景（验收参考）

- [ ] 场景1（拦截引导）：newcode 会话中 agent 尝试 `git branch -D worktree-team-demo+alice` → 收到「请改用 /team delete」错误 → 改调 TeamDelete 一次清理成功
- [ ] 场景2（孤儿清扫）：手动删 `~/.newcode/teams/<name>/` 配置 → 残留 `team-*` worktree → 下次启动/周期清扫自动移除
- [ ] 场景3（正常收敛不误伤）：F15 的 `git merge worktree-team-*` 在守卫下正常执行
