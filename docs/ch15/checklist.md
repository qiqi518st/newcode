# MewCode ch15 - AgentTeam 与 Coordinator Mode Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为而非实现细节。
> 标注「实跑（待人工验证）」的条目需要真实终端交互（tmux/TUI），本环境无法自动执行；其代码路径已由对应的 mock 单测/集成测试覆盖，实跑由用户人工补验。

## 实现完整性

- [ ] `team.Manager` 可实例化且构造时 `~/.mewcode/teams/` 不存在会自动创建（验证：`python -m mewcode` 启动冒烟 + `test_team_manager`）
- [ ] `Manager.create("demo", "")` 在 `~/.mewcode/teams/demo/config.json` 落地，`backend` 字段反映 `detect()` 结果（验证：单测 + 检查文件）
- [ ] `Manager.create("foo bar/baz", "")` sanitize 后路径为 `~/.mewcode/teams/foo-bar-baz/`（验证：单测）
- [ ] 同名 Team 二次 create 自动后缀 `-2`，目录与 sanitized_name 都生效（验证：单测）
- [ ] `BackendType` 三值齐全 `tmux`/`iterm2`/`in-process`；`detect()` 在 `$TMUX` 已设返回 tmux、全清空返回 in-process（验证：`monkeypatch.setenv` 单测四分支）
- [ ] `Box.write` + `Box.read` 一进一出消息字段一致（from/to/type/summary/content/timestamp/read=false）（验证：单测）
- [ ] mailbox 文件锁持锁超 10s 视为 stale，新 writer 能清锁抢占（验证：单测制造 11s 前锁文件，断言能拿到）
- [ ] `AgentNameRegistry.register("alice","agent-123")` → `resolve("alice")` 返 `agent-123`、`name_of("agent-123")` 返 `alice`；后注册覆盖前（验证：单测）
- [ ] `Store.create` 返回 task id 形如 `task_<6位hex>`；`Store.update(id, add_blocked_by=[other])` 双向维护 `blocked_by`/`blocks`（验证：单测断言双向）
- [ ] `Store.list_(status=pending)` 返回带 `is_ready` 字段，反映 blocked_by 是否全 completed（验证：单测）
- [ ] `coordinator.is_enabled` 双锁：feature 关 + env 开 → False；feature 开 + env 开 → True（验证：单测 4 组合）
- [ ] `coordinator.allowed_tools()` 含 `bash`，不含 `write_file`/`edit_file`（验证：单测）
- [ ] 队员工具池（spawn 后）含 `task_create`/`task_get`/`task_list`/`task_update`/`send_message` 五个协作工具（验证：spawn 后检查成员 registry）
- [ ] 未建团队的主 Agent 工具列表、普通子 Agent 工具池**不含**这 5 个协作工具（验证：`build_sub_registry` 单测 + 主 registry 检查）
- [ ] `Team.add_member`/`set_member_active` 加锁后先 `reload_from_disk_locked` 重读 disk 再改写再原子 save——跨进程时序下不丢更新（验证：单测制造「Lead 已 add_member、alice 子进程读完旧 config 后 set_member_active(False)」的时序，断言 disk 最终 `is_active=false`）

## 集成

- [ ] `Agent` 工具不带 `team_name` 时走 ch13 原路径，行为不变（验证：存量 `test_agent_tool` 全绿）
- [ ] `Agent` 工具带 `team_name="demo"` 时委托 `team_hook.spawn_teammate`；team_hook 为 None 时报「团队功能未启用」（验证：mock team_hook 单测断言被调用 + None 报错）
- [ ] `spawn_teammate` 创建 worktree `.mewcode/worktrees/team-demo+alice`、`team.members` 含 alice 并持久化到 config.json（验证：单测 + 集成）
- [ ] in-process 队员调用 `Agent(team_name=...)` 被拒并抛 `InProcessTeammateNoSpawnError`（验证：集成测试）
- [ ] 队员 `Agent.run` 每轮头部读 mailbox 未读 → `<incoming-messages>` reminder 注入 LLM 输入 → 读后 mark_read（验证：fake mailbox 写消息，捕获 Agent 构造的 payload 断言 reminder，再断言 read=true）
- [ ] 队员 `run_to_completion` 自然结束触发 `on_task_done` → config 中该成员 `is_active=false`（验证：单测注册回调 + launch noop task）
- [ ] in-process 队员完成经 done 队列 → Lead conv 收到 `<task-notification>` 且含 `<usage>`（total_tokens/tool_uses/duration_ms）五段齐全（验证：`build_team_notification` 单测）
- [ ] `SendMessage(to="alice")` 目标为 in-process 已停队员时：恢复 session Conv → `task_mgr.continue_agent` 续派（task status 回 Running）→ 刚写 mailbox 消息 mark_read 防重复（验证：集成测试断言状态 + 不重复注入）
- [ ] `SendMessage(to="*")` 广播给除发件人外所有活跃成员，每人邮箱各一条（验证：单测）
- [ ] `plan_approval_response` 仅 Lead 可发（队员发 → 抛错）；`shutdown_response` 只能发给 Lead（验证：单测）
- [ ] 所有队员一律 `dont_ask=True` 覆盖角色 `permission_mode`——派一个 `permission_mode: default` 的角色让她调 bash，断言正常完成不卡 Ask（验证：单测/集成）
- [ ] Pane 后端 spawn 前 `initial_prompt` 预写入队员 mailbox（type=text, from=lead），不走 CLI 参数（验证：spawn 后检查 mailbox 已有一条 from=lead 的初始任务）
- [ ] Pane 后端 `python -m mewcode --team-member` 子进程**不构造 TUI/REPL**，跑自治协程：读 mailbox → run_to_completion → 通知 Lead idle → stdin 回车做 wake_event 等下一轮（验证：`build_member_cmd` 含 `--agent-id` 单测 + tmux 实跑看 pane 是纯文本日志流）
- [ ] Lead mailbox watcher 每秒轮询所有 Team 的 lead.json → 未读转 `<team-update>` reminder（8000 截断、完整报告透传）推 pending → `lead_mail_event.set()`（验证：mock team_mgr 单测 + tmux 实跑后 unread 归零）
- [ ] Lead 在 IDLE 态收到 `lead_mail_event` → `begin_autonomous_turn` 合成 `[team-update]…` user 消息自动开新轮（用户在 RichLog 可见）；STREAMING 态不主动 wake（验证：mock 单测 + tmux 实跑）
- [ ] 沙箱开放 `/tmp` 与 `/private/tmp` 白名单——`write_file`/`edit_file` 可写 `/tmp/foo.txt`，`/etc/passwd` 仍拒（验证：`test_sandbox` 两组用例 + 存量 sandbox 测试全绿）
- [ ] `/team list` 输出含所有 Team；`/team delete demo --force` 调 backend.kill + 清 worktree + 清 team 目录（验证：mock manager 单测 + 实跑）

## 编译与测试

- [ ] `python -m mewcode --help` 退出码 0、打印帮助
- [ ] 版本号 0.15.0 在 `mewcode/__init__.py` 与 `pyproject.toml` 两处一致（验证：`grep -r "0.15.0"` 两处）
- [ ] `ruff check .` 无警告；`ruff format --check .` 无未格式化文件（验证：退出码 0）
- [ ] `pytest` 全部通过（含 ch04~ch14 存量零回归）（验证：退出码 0）
- [ ] 跑批后确认 `docs/` 未被改动（N16，CLAUDE.md 文档保护；本流程四份文档除外）
- [ ] 可选：`mypy mewcode/team/` 全绿

## 端到端场景（tmux 实跑，待人工验证：需真实终端交互）

### 场景 1：tmux 后端，Team 全生命周期

环境：tmux 已装；`cd /path/to/mewcode`；开新 tmux 会话 `tmux new-session -s ch15-test`；`python -m mewcode` 启动。

- [ ] 输入「创建一个名为 demo 的团队」→ Agent 调 `TeamCreate(team_name="demo")` 返回 `{team_name, backend:"tmux", config_path}`；`ls ~/.mewcode/teams/demo/config.json` 存在且 `backend` 为 `tmux`
- [ ] 输入「派 alice 用 general-purpose，在 worktree 里 `echo hello > /tmp/test_alice.txt && pwd > /tmp/test_alice_pwd.txt`」→ Agent 调 `Agent(team_name="demo", name="alice", ...)`
  - `tmux list-panes` 见新 pane；pane 内是**只读日志流**（`[team-member]` 起始行 + 工具调用打印，非 TUI 框）
  - `ls .mewcode/worktrees/team-demo+alice/` 存在；30s 内 `/tmp/test_alice.txt` = `hello`、`/tmp/test_alice_pwd.txt` = worktree 路径
  - `config.json` members 含 alice、`backend_type="tmux"`、pane_id 非空；alice mailbox 已有一条 `from=lead` 的 text 消息（initial_prompt 预写入证据）
- [ ] `/team info demo` 输出 alice 行（worktree/pane_id/is_active）
- [ ] 输入「给 alice 发消息让她再写一行 world 到 /tmp/test_alice.txt」→ Agent 调 `SendMessage(to="alice", ...)`；alice pane 被唤醒；30s 内文件多一行 `world`
- [ ] 等 alice 自然结束 → `config.json` 中 alice `is_active=false`（跨进程 reload 修复验证）；Lead mailbox 含 `summary` 带 `idle` 的消息且 1-2s 后 read=true；**Lead 无需输入自动出现 `[team-update]` + Synthesis 回复**
- [ ] `/team delete demo --force` → teams/demo 目录消失、worktrees 无 team-demo+alice、`tmux list-panes` 只剩 Lead

### 场景 2：in-process 后端实跑

环境：`unset TMUX TERM_PROGRAM`；非 tmux 终端。

- [ ] 创建 `inproc` 团队 → `backend` 为 `in-process`
- [ ] 派 bob `echo step1 > /tmp/bob.txt` → 无新 pane 出现；文件内容 `step1`；等 bob 结束 `/team info inproc` 看 `is_active=false`
- [ ] `SendMessage(to="bob", ...)` 续写 → 文件多一行；active → idle 反复变化
- [ ] `/team delete inproc --force` 清理

### 场景 3：Coordinator Mode 实跑

环境：`.mewcode/config.yaml` 加 `features: {coordinator_mode: true}`；`MEWCODE_COORDINATOR_MODE=1` 启动。

- [ ] 状态栏出现 `[COORDINATOR]`
- [ ] 输入「写 hello world 到 /tmp/coord_test.txt」→ `write_file` 不在 Lead 工具集，LLM 无 write_file 可用（验证 `/tmp/coord_test.txt` 不存在）
- [ ] 输入「跑 `git status`」→ `bash` 在 Coordinator 白名单中正常执行
- [ ] 输入「派几个队员探索 mewcode/agent 和 mewcode/team」→ Lead 派完队员**不立刻**自己 read_file/glob 探索，回复是「等待汇报中」类措辞；队员 idle 前 Lead 无新工具调用（派完停手纪律验证）

### 场景 4：Plan 审批工作流实跑

- [ ] 准备 `~/.mewcode/agents/planner.md`（frontmatter `permission_mode: plan`）；创建 team `plan-test`
- [ ] 派 planner 角色队员制定实现计划 → 以 plan 模式起步，计划经 SendMessage 发 Lead；Lead mailbox 含计划
- [ ] 输入「批准 planner 的计划」→ Lead 调 `SendMessage(type="plan_approval_response", payload={approve:True})`；planner 收到后权限切到 default 继续执行

## 失败回归

- [ ] 启动时 `~/.mewcode/teams/` 不存在 → 自动创建，不报错
- [ ] `~/.mewcode/teams/<name>/config.json` 内容损坏 → 启动只 stderr 警告、跳过该 Team、主流程不崩
- [ ] 创建 Team 时 disk 写失败（手动 chmod 模拟）→ 抛错、不留半成品目录
- [ ] mailbox 锁冲突 10 次仍失败 → SendMessage 抛错、不丢消息
- [ ] tmux 后端在非 tmux 会话 `split-window` 失败 → 抛 `BackendUnavailableError`（`new-session -d` 也失败时）、Team.members 不留半成品
- [ ] 协作工具被未建团队的主 Agent 误调用 → 工具自身抛错兜底（不依赖过滤层单点保护）

## 待人工验证

- [ ] **iterm2 后端实装**——本环境为 WSL，无法验证 macOS 专属；接口已按 `it2` 约定实现，需 macOS + iTerm2 + it2 实机补验（风险：`it2 split`/`send-text`/`close-pane` 命令形态未实测）
- [ ] **真实 tmux 交互**（场景 1/3/4 的 pane 唤醒、send-keys 触发）——本环境可跑但需人工在真实终端驱动 TUI；mock 子进程测试已覆盖命令构造与分流逻辑
- [ ] **macOS `/private/tmp` 真实路径**——本环境仅有 `/tmp`；`/private/tmp` 前缀判定逻辑已单测，真实路径需 macOS 补验
