# NewCode ch14 - Git Worktree 文件系统隔离 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。对应 spec AC1-AC25 与 plan 集成点。

## 实现完整性

- [ ] **AC1 / slug 校验**：`validate_slug` 对 `"feature/a"` / `"alice"` / `"v1.0"` / `"a_b"` 通过；对 `""` / `"../etc"` / `".."` / `"a//b"` / `"a/b "` / `"/x"` / `"a/"` / `"a;b"` 抛 `ValueError` 且带原因（验证：`pytest tests/test_worktree_slug.py -v`）
- [ ] **AC2 / AC3 目录与分支命名**：`create("alice", ...)` → `.newcode/worktrees/alice/` + 分支 `worktree-alice`；`create("team/alice", ...)` → `.newcode/worktrees/team+alice/` + 分支 `worktree-team+alice`；`git branch` 输出可见（验证：`pytest tests/test_worktree_manager.py`）
- [ ] **AC4 / 快速恢复**：目录已存在的合法 worktree 再 create → 不调 `git worktree add`（monkeypatch `_run_git` 断言未被调用），毫秒级返回（验证：`pytest tests/test_worktree_create.py`）
- [ ] **AC5 / 配置复制**：主仓库存在 `.newcode/config.local.yaml` → worktree 内同位置出现该文件（验证：`pytest tests/test_worktree_create.py`）
- [ ] **AC6 / hooks**：主仓库有 `.husky/` 或 `core.hooksPath` → worktree `.git/config` 含 `core.hooksPath`（验证：`pytest tests/test_worktree_create.py`）
- [ ] **AC7 / 软链大目录**：主仓库有 `node_modules/` → worktree 内是 symlink（`Path.is_symlink()` True）（验证：`pytest tests/test_worktree_create.py`）
- [ ] **AC8 / .worktreeinclude**：主仓库有 `.worktreeinclude` 含 `*.env` 且存在被忽略 `.env` → worktree 内出现 `.env`（验证：`pytest tests/test_worktree_create.py`）
- [ ] **AC9 / enter 不 chdir**：`enter(name)` 不改变进程 `Path.cwd()`；返回 session 字段正确（验证：`pytest tests/test_worktree_lifecycle.py`）
- [ ] **AC10 / exit 变更保护**：`exit(name, REMOVE, ExitOptions())` 遇未提交修改 → 抛 `WorktreeHasChangesError`，worktree 目录仍在（验证：`pytest tests/test_worktree_lifecycle.py`）
- [ ] **AC11 / discard 删除**：`exit(name, REMOVE, ExitOptions(discard_changes=True))` → 目录删、分支删（验证：`pytest tests/test_worktree_lifecycle.py`）
- [ ] **AC12 / auto_cleanup**：`manual=True` 直接 keep；`manual=False` 且无变更 remove（验证：`pytest tests/test_worktree_lifecycle.py`）
- [ ] **AC13 / 工具 ctx cwd**：`read_file`/`write_file`/`edit_file`/`list_files`/`search_code`/`execute_command` 在 ctx cwd 注入下以 cwd 为基准解析相对路径（验证：`pytest tests/test_tools_cwd.py`）
- [ ] **AC14 / bash 子进程 cwd**：`execute_command` 在 ctx cwd 注入下子进程 `cwd=` 参数为 ctx cwd（验证：`pytest tests/test_tools_cwd.py`）
- [ ] **AC15 / isolation 链路**：`Definition.isolation == "worktree"` → AgentTool 创建临时 worktree、注入 notice、传 ctx cwd、跑完 auto_cleanup（验证：`pytest tests/test_agent_worktree.py tests/test_agent_tool.py`）
- [ ] **AC16 / 文件隔离**：隔离子 Agent 写文件 → 主工作目录对应文件未变，worktree 副本已变（验证：`pytest tests/test_agent_worktree.py`；tmux 场景 8）
- [ ] **AC17 / /worktree create+list**：`/worktree create alice` 落地，`/worktree list` 输出含 alice（验证：`pytest tests/test_worktree_command.py`）
- [ ] **AC18 / /worktree exit**：`/worktree exit --remove` 遇未提交修改报错；加 `--discard` 删除成功（验证：`pytest tests/test_worktree_command.py`）
- [ ] **AC19 / sweep_stale 三层**：只删名字匹配 `agent-a[0-9a-f]{7}`/`wf-`、跳过当前 session、跳过有变更/未推送 commit 的目录（验证：`pytest tests/test_worktree_sweep.py`）
- [ ] **AC20 / session 持久化**：session 持久化到 `.newcode/worktree_session.json`；worktree 目录被外部删除后启动清空 + stderr 警告（验证：`pytest tests/test_worktree_manager.py`）
- [ ] **AC21 / .gitignore 只警告**：根 `.gitignore` 缺两行时启动 stderr 警告、**不修改**（验证：`pytest tests/test_worktree_manager.py`；临时仓库造缺两行的 .gitignore 观察启动输出）
- [ ] **AC22 / ctx 机制新建 + schema 不变**：cwd ContextVar 机制从无到有（新建）；主 Agent 工具 schema 与参数与 ch13 完全一致（验证：`pytest tests/test_tool_ctx.py`；对比工具 parameters 输出）
- [ ] **AC23 / enable=false 降级**：`worktrees.enable=false` → `isolation: worktree` 角色退化为不隔离，不建目录（验证：`pytest tests/test_worktree_config.py`；mock 装配断言）
- [ ] **AC24 / 非 git 优雅降级**：非 git 仓库 / 无 commit / git 缺失 → worktree 命令返回结构化错误或降级警告，主流程不崩（验证：`pytest tests/test_worktree_manager.py`；在非 git 目录 `python -m newcode` 启动观察）

## 集成

- [ ] **WorktreeAccessor 协议解耦**：`newcode/slash/` 不 import `newcode/worktree`（协议定义在 slash/ui.py，适配器在 tui）（验证：grep 确认无反向 import）
- [ ] **slash 注册无冲突**：`register_all` 无命令名/别名冲突，`/worktree` 可注册（验证：`pytest` 启动注册路径；`python -m newcode` 启动无冲突报错）
- [ ] **AgentTool 分支顺序**：`isolation: worktree` 且 worktree 可用 → create→notice→with_cwd→run_to_completion→auto_cleanup 顺序正确；`worktree_mgr=None` 或 `enable=false` → 回落原 launch_defined（验证：`pytest tests/test_agent_tool.py`）
- [ ] **主 Agent enter 后工具落 worktree**：`/worktree enter <name>` 后主 Agent 相对路径写文件落在该 worktree；`exit` 后回主工作树（验证：`pytest tests/test_worktree_tui.py`；tmux 场景 2）
- [ ] **隔离执行强制前台**：`isolation: worktree` 且 `run_in_background=true` → 仍同步执行返回最终文本（验证：`pytest tests/test_agent_tool.py`）

## 编译与测试

- [ ] **全量存量测试通过**：`pytest -q` 全绿（ch13 行为零回归，AC25）（验证：跑全量）
- [ ] **新增测试通过**：`pytest tests/test_worktree_*.py tests/test_tool_ctx.py tests/test_tools_cwd.py tests/test_agent_worktree.py tests/test_agent_tool.py tests/test_subagent_parser.py tests/test_worktree_command.py tests/test_worktree_tui.py -q` 全绿
- [ ] **lint 通过**：`ruff check newcode tests` 无错误；`ruff format --check` 通过（如有配置）
- [ ] **可启动**：`python -m newcode --version` 正常；非 git 目录启动降级警告不崩（验证：运行）
- [ ] **docs/ 不可变**：跑完全部验证后 `git status` 确认 docs/ 下仅 ch14 四份文档（N14）（验证：git status）

## 端到端场景

- [ ] **场景 1（并行隔离）**：两个 `isolation: worktree` 子 Agent 同时各写各自文件 → 互不覆盖，各落各 worktree（验证：tmux 或集成测试）
- [ ] **场景 2（保留→review→接着改）**：子 Agent 写了代码 → worktree 保留 → `/worktree list` 看到 → `/worktree enter` 接着改（相对路径写文件落 worktree）→ `/worktree exit` 回主工作树（验证：tmux 场景 8）
- [ ] **场景 3（无价值自动清理）**：子 Agent 只读分析无改动 → 完成即自动清理（目录+分支消失，`git worktree list` 无残留）（验证：tmux 或集成）
- [ ] **场景 4（手动不清理）**：`/worktree create review-fix` 手动创建 → 子 Agent 完成后 / 后台清理均不删它（验证：`/worktree list` 持续可见）
- [ ] **场景 5（异常残留清理）**：模拟异常退出残留 `agent-xxx` worktree → 过期+干净 → 被后台清理；过期但脏/有 commit → 保留（验证：`pytest tests/test_worktree_sweep.py`；手动造目录跑 sweep）
- [ ] **场景 6（--resume）**：`/worktree enter x` 后退出，`python -m newcode --resume` 重启 → session 恢复、`/worktree list` 可见、可 enter（验证：tmux 或 CLI）
- [ ] **场景 7（优雅降级）**：在非 git 目录运行 → `/worktree list`/`create` 返回结构化错误或「Worktree 功能未启用」，不崩（验证：tmux）
- [ ] **场景 8（tmux 实跑）**：临时 git 仓库 + 项目级角色 `.newcode/agents/worktree-writer.md`（`isolation: worktree`）→ 触发子 Agent 改 `server.py` → 主目录 `server.py` 未变、worktree 副本已变；留盘/清理符合预期（验证：tmux 手动）

## 验收报告格式（开发完成后）

```
## 验收报告

### 通过（N/M）
- [x] 条目 — 证据：...

### 未通过（如有）
- [ ] 条目 — 预期：X，实际：Y，修复方案：...

### 待人工验证（如有）
- [ ] 条目 — 原因：环境限制（说明），替代验证：...，风险：...，补验：由谁何时

### 端到端
- [x] 场景 N — 结果：...
```
