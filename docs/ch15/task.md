# NewCode ch15 - AgentTeam 与 Coordinator Mode Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `newcode/team/__init__.py` | 包导出 |
| 新建 | `newcode/team/types.py` | Team / TeammateInfo / BackendType / 异常家族 |
| 新建 | `newcode/team/persistence.py` | sanitize + config.json 原子写 + read_json |
| 新建 | `newcode/team/filelock.py` | 共享文件锁（mailbox/tasks 共用） |
| 新建 | `newcode/team/manager.py` | Manager（create/get/delete/scan/member_of/poll_lead_mailboxes/handle_task_done + 跨进程 reload） |
| 新建 | `newcode/team/spawn.py` | spawn_teammate（TeamHook 结构实现 + 闭包注入） |
| 新建 | `newcode/team/notices.py` | team-context / 附录 / incoming-messages / team-update 文案 |
| 新建 | `newcode/team/notify.py` | build_team_notification（含 usage） |
| 新建 | `newcode/team/cli_team_member.py` | run_team_member（--team-member 自治循环） |
| 新建 | `newcode/team/mailbox/__init__.py` | Box（read/write/mark_read/read_unread/write_broadcast） |
| 新建 | `newcode/team/mailbox/message.py` | Message / MessageType |
| 新建 | `newcode/team/registry/__init__.py` | AgentNameRegistry |
| 新建 | `newcode/team/tasks/__init__.py` | Task / Store |
| 新建 | `newcode/team/tasks/filter.py` | Filter / Patch / is_ready |
| 新建 | `newcode/team/backend/__init__.py` | Backend Protocol / SpawnRequest / new_backend |
| 新建 | `newcode/team/backend/detect.py` | detect() |
| 新建 | `newcode/team/backend/tmux.py` | TmuxBackend |
| 新建 | `newcode/team/backend/iterm2.py` | Iterm2Backend（骨架，待人工验证） |
| 新建 | `newcode/team/backend/inprocess.py` | InProcessBackend |
| 新建 | `newcode/team/tools/__init__.py` | 工具工厂导出 |
| 新建 | `newcode/team/tools/team_create.py` / `team_delete.py` | TeamCreate / TeamDelete |
| 新建 | `newcode/team/tools/task_create.py` / `task_get.py` / `task_list.py` / `task_update.py` | 任务四工具 |
| 新建 | `newcode/team/tools/send_message.py` | SendMessage（含续写检测） |
| 新建 | `newcode/team/tools/teammate_filter.py` | TEAMMATE_EXTRA_TOOLS 白名单 |
| 新建 | `newcode/coordinator/__init__.py` | is_enabled / allowed_tools / system_prompt_suffix |
| 新建 | `newcode/agent/team_hook.py` | TeamHook / TeamSpawnRequest / IncomingMessage / TeammateContext（闭包） |
| 新建 | `newcode/agent/team_mailbox.py` | 成员 Loop 头部邮箱注入 + Plan 审批切换 |
| 修改 | `newcode/agent/agent.py` | incoming 注入 / set_allowed_tools / raw reminders / 收窄强制 |
| 修改 | `newcode/agent/agent_tool.py` | team_name 参数 + team_hook 委托 |
| 修改 | `newcode/task/manager.py` | name_reg 委托（保留 _by_name 兜底）+ on_task_done 多回调 |
| 修改 | `newcode/session/runtime.py` | raw reminder 通道 + open_at |
| 修改 | `newcode/subagent/types.py` + `parser.py` | plan_mode_required 字段 |
| 修改 | `newcode/subagent/launcher.py` | make_sub_agent / build_sub_registry 扩展参数 |
| 修改 | `newcode/tools/filter.py` | TEAMMATE_EXTRA_TOOLS + ALL_AGENT_DISALLOWED_TOOLS |
| 修改 | `newcode/permission/sandbox.py` | /tmp + /private/tmp 白名单（N14） |
| 修改 | `newcode/config/`（新 `features.py` 或并入 loader） | FeaturesConfig（三层 features 段） |
| 修改 | `newcode/tui/app.py` | team_mgr / coordinator 标签 / lead mail 消费 / 自动续推 / 团队通知 |
| 修改 | `newcode/tui/tasks.py` | consume_lead_mail / wait_for_lead_mail |
| 新建 | `newcode/slash/commands/team.py` | /team 四子命令 |
| 修改 | `newcode/slash/commands/__init__.py` | COMMAND_MODULES 加 team |
| 修改 | `newcode/main.py` | 装配 + on_task_done 注册 + --team-member CLI + coordinator 激活 |
| 修改 | `newcode/__init__.py` + `pyproject.toml` | 版本 0.15.0 |
| 新建 | `tests/test_team_*.py`（11 个） | 团队全量测试批 |

## T1: team 包骨架 + 核心类型

**文件：** `newcode/team/__init__.py`、`newcode/team/types.py`
**依赖：** 无
**步骤：**
1. 建 `newcode/team/` 包；`types.py` 定义 `Team`（name/sanitized_name/lead_agent_id/backend/description/created_at/members + 派生路径 config_dir/config_path/tasks_path/mailbox_dir（序列化跳过）+ `_lock: asyncio.Lock`）
2. 定义 `TeammateInfo`（name/agent_id/agent_type/model/worktree_path/branch/backend_type/pane_id/is_active(保留 None 语义)/plan_mode_required/session_dir）+ 手写 `to_dict`/`from_dict`
3. 定义 `BackendType(StrEnum)`：`tmux`/`iterm2`/`in-process`
4. 异常家族：`TeamError` → `TeamNotFoundError`/`TeamHasActiveMembersError`/`MemberExistsError`/`MemberNotFoundError`/`InProcessTeammateNoSpawnError`/`BackendUnavailableError`/`SendMessageValidationError`
5. `__init__.py` 导出

**验证：** `python -c "from newcode.team import Team, TeammateInfo, BackendType"` 通过；`ruff check newcode/team/types.py`

## T2: persistence

**文件：** `newcode/team/persistence.py`
**依赖：** T1
**步骤：**
1. `sanitize(name)`——只保留 `[a-zA-Z0-9._-]`，其余替换 `-`，首尾去 `-`，空串返回 `""`（用 `re.sub`）
2. `atomic_write_json(path, value)`——`json.dumps(indent=2)` → 写 `<path>.tmp` → `os.replace`
3. `read_json(path)`——`Path.read_text()` + `json.loads`；不存在抛 `FileNotFoundError`
4. 持久化侧 `reload_members_from_disk(team)`——调用方持锁；从 config_path 重读 members 覆盖内存（失败静默回退内存现状）

**验证：** 单测 `sanitize("foo bar/baz")=="foo-bar-baz"`；atomic 写入后 read_json 取回相等

## T3: 共享文件锁 team/filelock.py

**文件：** `newcode/team/filelock.py`
**依赖：** 无
**步骤：**
1. `async def acquire(lock_path) -> AsyncContextManager[None]`——`os.open(O_CREAT|O_EXCL|O_WRONLY, 0o644)` 抢锁；失败 5-100ms 随机抖动重试 ≤10 次；持锁超 10s（`st_mtime`）视为 stale 删锁重试；退出 `os.unlink`
2. 常量 `LOCK_MAX_RETRIES=10` / `LOCK_STALE_AFTER=10.0` / `LOCK_BACKOFF_MIN=0.005` / `LOCK_BACKOFF_MAX=0.1`
3. **mailbox 与 tasks 共用此模块**（避免跨子包耦合）

**验证：** 单测 `test_acquire_serial`（两次抢锁中间 release）、`test_acquire_stale`（`os.utime` 伪造 11s 前锁，断言能拿到）

## T4: mailbox

**文件：** `newcode/team/mailbox/message.py`、`newcode/team/mailbox/__init__.py`
**依赖：** T3
**步骤：**
1. `message.py`：`MessageType(StrEnum)`（text/shutdown_request/shutdown_response/plan_approval_response）+ `Message` dataclass（`from_`/to/type/summary/content/payload/timestamp/read）+ to_dict/from_dict（`from_` ↔ json key `from`）
2. `Box`：`__init__(dir_)` mkdir；`write(agent_id, msg)`——`async with acquire(<dir>/<agent_id>.lock)` 内 read-modify-write + `atomic_write_json`（timestamp=0 时补 `int(time.time())`）；`read` / `read_unread -> (indices, msgs)` / `mark_read(agent_id, indices)`
3. `write_broadcast(sender, msg, member_ids)`——对除 sender 外每个成员各 write 一次（F8.5）

**验证：** 单测 write/read/mark_read；并发 10 task 同写一个邮箱断言 10 条无丢失

## T5: registry

**文件：** `newcode/team/registry/__init__.py`
**依赖：** 无
**步骤：**
1. `AgentNameRegistry`：`threading.Lock` + `by_name`/`by_id`
2. `register`（同名覆盖：删旧 by_id 映射；同 agent_id 换名：先反向 unregister）、`unregister`、`unregister_by_agent_id`、`resolve(name_or_id)`（name 优先再 agent_id）、`name_of`、`list_`

**验证：** 单测覆盖注册/解析/反查/同名覆盖/同名指同 agent_id 边界

## T6: tasks Store

**文件：** `newcode/team/tasks/filter.py`、`newcode/team/tasks/__init__.py`
**依赖：** T3
**步骤：**
1. `filter.py`：`Status(StrEnum)`（pending/in_progress/completed/blocked）；`Filter`（status 过滤）；`Patch`（title/description/status/assignee + add_blocks/remove_blocks/add_blocked_by/remove_blocked_by）；`is_ready(task, tasks)`（无未完成 blocker）
2. `Store`：`<config_dir>/tasks.json` 单文件 + `tasks.lock`（`team/filelock`）；`create`（id=`task_<6位hex>`，`secrets.token_hex(3)`）/`get`/`list_(Filter)`（附加 is_ready，不存盘）/`update(id, Patch)`（**双向维护** blocked_by/blocks）；原子写

**验证：** 单测 create/get/update；`add_blocked_by` 双向更新断言

## T7: Manager 生命周期 + 成员操作 + 跨进程 reload

**文件：** `newcode/team/manager.py` + `newcode/team/persistence.py`
**依赖：** T1-T6
**步骤：**
1. `Manager(home_dir, project_root, wt_mgr, task_mgr, reg)`：建 `~/.newcode/teams/`（存在校验可写）；扫描子目录还原 `teams` dict（坏 JSON stderr 警告跳过，F17.2）
2. `get(name)` / `list_()` / `member_of(agent_id)` / `is_teammate(agent_id)`
3. `async create(name, description)`：sanitize → 同名后缀 `-2/-3` → 建 config_dir + mailbox/ → `detect()` → 写 config.json（Lead 成员 `name="lead", agent_id="lead", is_active=None`）→ 入 dict
4. `async delete(name, force)`：持锁 → 非 force 有活跃成员抛 `TeamHasActiveMembersError` → 逐成员 `new_backend(...).kill`（未注入 backend deps 的测试场景跳过 kill，fallback 只清磁盘）→ 删 session/worktree（best-effort）→ `shutil.rmtree(config_dir)` → 移除
5. `Team.add_member` / `set_member_active` / `remove_member` / `member_by_name` / `member_by_agent_id`：**加锁后先 `reload_members_from_disk` 再改再原子 save**（F1.7 跨进程丢更新防护）
6. `async poll_lead_mailboxes() -> list[LeadMessage]`：遍历各 Team 读 `mailbox/lead.json` 未读 → 标 read → 返回（F11.3）
7. `async handle_task_done(agent_id)`：`registry.name_of` → 找所属 Team → `set_member_active(name, False)` + `box.write(lead, Message(type=text, summary=f"{name} idle"))`（**全成员统一写 idle**，对齐 F12.1/AC17；in-process 的 done-queue `<task-notification>` 为额外实时路径）

**验证：** 单测 create sanitize/后缀/Lead 成员；delete 活跃校验；delete force 清理

## T7b: 跨进程 reload 时序测试（专项）

**文件：** `tests/test_team_manager.py`（新增用例）
**依赖：** T7
**步骤：**
1. 构造时序：t1 = read_json 得到无 alice 的 Team A 快照；t2 在 disk 上写带 alice 的 Team B；t3 调 `await team.set_member_active("alice", False)`——应经 reload 路径成功而非静默 no-op
2. 断言 disk 最终 `is_active=false`

**验证：** 该用例通过；若无 reload 会失败（证明 F1.7 修复必要）

## T8: FeaturesConfig

**文件：** `newcode/config/`（新 `features.py`，镜像 worktree/config.py 三层模式）
**依赖：** 无
**步骤：**
1. 三层合并（local > project > user）读 `features:` 段
2. `FeaturesConfig`：`enable: bool = True`（团队总开关）、`coordinator_mode: bool = False`、`fork_teammate: bool = False`
3. `load_features_config(project_root)` 函数

**验证：** 单测缺省值 + 三层覆盖 + 非法 YAML 不阻断

## T9: backend 抽象 + detect

**文件：** `newcode/team/backend/__init__.py`、`newcode/team/backend/detect.py`
**依赖：** T1
**步骤：**
1. `backend/__init__.py`：`SpawnRequest` dataclass（含 `sub_agent`/`conv`/`task_mgr` 三个 Any 可选字段——backend 不反向依赖 agent）；`Backend` Protocol（`type()`/`spawn(req)->(pane_id,agent_id)`/`wake(pane_id,agent_id)`/`kill(pane_id,agent_id)`）；`new_backend(t, **deps)` 工厂（懒 import 各子模块）
2. `detect.py`：`detect()`——`$TMUX` → tmux；`$TERM_PROGRAM==iTerm.app` 且 `it2` 可执行 → iterm2；`shutil.which("tmux")` → tmux；否则 in-process（F2.4，一次性决定）

**验证：** `python -c "from newcode.team.backend import Backend, SpawnRequest, new_backend"` 通过；detect 四分支 monkeypatch 单测

## T10: backend/tmux.py

**文件：** `newcode/team/backend/tmux.py`
**依赖：** T9
**步骤：**
1. `spawn(req)`：`$TMUX` 内 `tmux split-window -h -P -F "#{pane_id}" -- <cmd>`；`$TMUX` 外但 tmux 可用 `tmux new-session -d` detached（失败抛 `BackendUnavailableError`，不回落 in-process，F2.5）
2. `cmd` 构造：`python -m newcode --team-member --team <t> --member <m> --agent-id <预生成> --session-dir <sd> --worktree <wt> [--agent-type][--model][--plan-mode]`，用 `shlex.quote` 转义；`--agent-id` 必传（F3.2）
3. `initial_prompt` **不走命令行**（由 spawn_teammate 在 spawn 前预写 mailbox，F2.6）
4. `wake`：`tmux send-keys -t <pane_id> "" Enter`；`kill`：`tmux kill-pane -t <pane_id>` 忽略不存在
5. `asyncio.create_subprocess_exec` 跑 tmux，捕获 stdout 作 pane_id

**验证：** mock `create_subprocess_exec` 断言命令拼接含 `--agent-id`；wake/kill 命令形参正确

## T11: backend/iterm2.py（骨架）

**文件：** `newcode/team/backend/iterm2.py`
**依赖：** T9
**步骤：**
1. 接口按 `it2 split --new-pane --command "<cmd>"` / `it2 send-text --pane <pane_id> ""` / `it2 close-pane --pane <pane_id>` 约定实现（cmd 同 T10 格式含 `--agent-id`）
2. docstring 标注「待人工验证：macOS 专属，需 iTerm2 + it2 实机」

**验证：** 单测 `new_backend("iterm2")` 返回实例且 type() 正确（不实跑 it2）

## T12: backend/inprocess.py

**文件：** `newcode/team/backend/inprocess.py`
**依赖：** T9
**步骤：**
1. `spawn(req)`：`task_mgr.launch(req.sub_agent, req.initial_prompt, name=req.member_name)` 返回 task_id 作 agent_id；`pane_id=""`（F5.1）
2. `wake`：no-op；`kill`：`await task_mgr.stop(agent_id)`（F5.2/F5.3）
3. 本模块允许依赖 `agent`/`task`/`conversation`（低层，TD-15）

**验证：** mock task_mgr 单测 spawn 返回 `("", "agent-xxx")`、kill 调 stop

## T13: agent/team_hook.py

**文件：** `newcode/agent/team_hook.py`
**依赖：** 无
**步骤：**
1. `TeamSpawnRequest` dataclass（team_name/prompt/subagent_type/model/name/plan_mode_required）
2. `TeamHook` Protocol：`spawn_teammate(req) -> str`、`is_teammate_context(ctx) -> tuple[str,str,bool]`（TD-14）
3. `IncomingMessage` 轻量 dataclass（独立于 mailbox.Message，agent 包内定义）
4. `TeammateContext` dataclass（agent 包持有）：team_name/member_name/agent_id + **闭包** `read_unread: Callable[[], Awaitable[tuple[list[int], list[IncomingMessage]]]]`、`mark_read: Callable[[list[int]], Awaitable[None]]`、`set_permission: Callable[[str], None] | None`（Plan 审批切换用）——由 team 包在 spawn 时注入，agent 包不 import team/mailbox（TD-12，闭包彻底解环）

**验证：** `python -c "from newcode.agent.team_hook import TeamHook, TeammateContext, TeamSpawnRequest"` 通过

## T14: agent.py 改造

**文件：** `newcode/agent/agent.py`
**依赖：** T13
**步骤：**
1. `__init__` 加 `teammate: TeammateContext | None = None`、`allowed_tools: list[str] | None = None`
2. `run()` 每轮组装 reminders 处：`if self._teammate: reminders.extend(await self._teammate.inject_incoming())`（inject_incoming 逻辑在 team_mailbox.py，F11.1）
3. `run()` raw reminder 消费：`for p in runtime.take_raw_reminders(): reminders.append(p)`（TD-3，不经 hook_notification）
4. `set_allowed_tools(allowed)`（**新增方法**）：`run()` tool_defs 用 `registry.definitions_filtered(allowed)`；known_calls 按 allowed 硬过滤（不在集合 → TOOL_RESULT(error)，TD-11）
5. 存量路径行为不变

**验证：** 单测 teammate 注入进 reminders；set_allowed_tools 收窄 defs + 硬过滤 write_file；存量 agent 测试全绿

## T15: agent/team_mailbox.py（成员注入 + Plan 审批切换）

**文件：** `newcode/agent/team_mailbox.py`
**依赖：** T13
**步骤：**
1. `inject_incoming(agent, teammate)`：调 `teammate.read_unread()` → 有未读组 `<incoming-messages>` reminder（notices.py 文案）→ `teammate.mark_read(indices)` → 返回 reminder 列表
2. **Plan 审批集中处理**（双后端统一）：未读中若有 `plan_approval_response(approve=True)` → `teammate.set_permission("default")` + reminder 加「Lead 已批准，权限已切到 default，可执行计划」；`approve=False` → reminder 加「Lead 驳回，反馈：<feedback>，请调整重提」（F13.4/TD-合并）
3. 收到 `shutdown_request`：提示队员可自主选择回复 shutdown_response（LLM 决策不强制，F43）

**验证：** fake mailbox 写 1 条消息，启动子 Agent.run，断言 reminder 含 `<incoming-messages>`；plan_approval approve 后 set_permission 被调

## T16: session/runtime.py

**文件：** `newcode/session/runtime.py`
**依赖：** 无
**步骤：**
1. raw reminder 通道：`append_raw_reminders(list[str])` / `take_raw_reminders()`（线程安全，镜像 pending_reminders 模式）
2. `open_at(abs_session_dir, model)` classmethod：构造指向绝对 session 目录的 SessionContext + `SessionWriter.open_existing` + 全新 ConversationManager（Pane 子进程恢复用，F6.1）

**验证：** 单测 raw 通道存取；open_at 建出可 append 的 conv 且 writer 落盘

## T17: subagent plan_mode_required

**文件：** `newcode/subagent/types.py`、`newcode/subagent/parser.py`
**依赖：** 无
**步骤：**
1. `AgentDefinition` 加 `plan_mode_required: bool = False`
2. parser 解析 frontmatter `planModeRequired`（bool 容错，非法警告回落 False）

**验证：** 单测 frontmatter 含 `planModeRequired: true` 解析出 True

## T18: subagent/launcher.py 扩展

**文件：** `newcode/subagent/launcher.py`
**依赖：** T17
**步骤：**
1. `build_sub_registry(role, is_background, extra_tools=())`：过滤后 `visible.extend(extra_tools)` 再 view（队员 collab 工具注入点，TD-7）
2. `make_sub_agent(..., runtime=None, teammate=None, extra_tools=(), permission_mode=None, dont_ask=None)`：runtime 给定 → `conv = runtime.conversation`（writer 已接线）；teammate/extra_tools 透传 Agent；dont_ask 覆盖角色（F6.3）

**验证：** 单测 extra_tools 出现在子 registry；runtime conv 被采用；存量启动测试全绿

## T19: filter 扩展

**文件：** `newcode/tools/filter.py`
**依赖：** 无
**步骤：**
1. `ALL_AGENT_DISALLOWED_TOOLS` 加 `TEAMMATE_EXTRA_TOOLS = frozenset({"task_create","task_get","task_list","task_update","send_message"})`——普通子 Agent 经过滤天然不可见（N2）
2. 团队成员经 `build_sub_registry(extra_tools=TEAMMATE_EXTRA_TOOLS)` 在过滤后显式注入（主 Agent 可见性走 TD-2 动态注册，不由 filter 控制）

**验证：** 单测普通子 Agent 过滤结果不含 5 工具；extra_tools 注入后含

## T20: task/manager.py 改造

**文件：** `newcode/task/manager.py`
**依赖：** T5
**步骤：**
1. `TaskManager` 加 `name_reg: AgentNameRegistry | None = None` + `set_name_registry(reg)` setter；`get_by_name`/`continue_agent` 优先 `name_reg.resolve`，**保留本地 `_by_name` 兜底**（ch13 兼容）
2. `launch` 时 name 非空且 name_reg 非 None → `name_reg.register(name, id_)`（同时维持 `_by_name`）
3. 新增 `on_task_done(fn: Callable[[str], Awaitable[None]])` **多回调注册**；`_drive` 的 finally（终态）逐个 `await` 回调（包 try/except 不崩循环）

**验证：** 单测 on_task_done 完成/失败触发；存量 subagent 测试全绿

## T21: team/spawn.py

**文件：** `newcode/team/spawn.py`
**依赖：** T7-T20 + T10-T12
**步骤：**
1. `async spawn_teammate(mgr, team, req: TeamSpawnRequest) -> str`（结构实现 TeamHook.spawn_teammate）：
   - 校验 team 存在；`is_teammate_context(ctx)` 拦截（in-process 队员抛 `InProcessTeammateNoSpawnError`；Pane 队员 team_name 屏蔽——filter 层 + execute 双保险）
   - 定义：subagent_type → catalog.resolve；留空 + `features.fork_teammate` → fork 定义（build_forked_messages 继承 Lead 历史）；留空 → `general-purpose`
   - 预生成 `agent_id = "agent-" + secrets.token_hex(4)`（对齐 ch13 task id 形态）
   - `wt_mgr.create(f"team-{sanitized}/{member_name}", "HEAD", False)`（worktree 不可用 → 结构化错误）
   - `SessionRuntime(project_root).create_new()` → session_dir
   - `launcher.make_sub_agent(role, is_background=(backend==in-process), permission_mode=(PLAN if plan_mode_required else DEFAULT), sandbox_root=wt.path, dont_ask=True, runtime=member_runtime, teammate=TeammateContext(...闭包注入), extra_tools=TEAMMATE_EXTRA_TOOLS)`（F10.5）
   - Pane：`box.write(agent_id, Message(from="lead", type=text, summary=truncate(prompt), content=initial_prompt))` 预写（F2.6）；in-process：initial_prompt 作 task
   - `backend.spawn(req)` → (pane_id, agent_id)；`reg.register(member_name, agent_id)`；`team.add_member(TeammateInfo(...))`（reload-before-modify）
   - 返回 `{member_name, agent_id, worktree, backend, pane_id}` JSON
2. 闭包构造：`read_unread`/`mark_read` 绑定 Box+agent_id；`set_permission` 绑定队员 permission checker
3. helper：`build_team_context_reminder(team, member, agent_id)`（F10.10）、`team_system_prompt_suffix()`（F10.9）、`truncate_for_summary(prompt)`（5-10 词 summary）

**验证：** mock backend 单测完整 spawn 流程（worktree/mailbox 预写/registry/members 落盘）+ 权限拦截

## T22: notices + notify

**文件：** `newcode/team/notices.py`、`newcode/team/notify.py`
**依赖：** T21
**步骤：**
1. `notices.py`：`build_team_context`（F10.10）、`teammate_prompt_appendix`（F10.9 固定文本）、`incoming_messages(msgs)`（F11.2 格式）、`team_update(msgs)`（F11.3，8000 截断、完整报告透传）
2. `notify.py`：`build_team_notification(bt)`——`<task-notification>` 含 `<usage>`（total_tokens/tool_uses/duration_ms），task-id=agent_id（F9.1）

**验证：** 单测 incoming 格式、team_update 截断、build_team_notification 五段齐全

## T23: coordinator 包

**文件：** `newcode/coordinator/__init__.py`
**依赖：** T8
**步骤：**
1. `is_enabled(cfg)`：`cfg.enable and cfg.coordinator_mode and env_truthy(os.environ.get("NEWCODE_COORDINATOR_MODE",""))`；`env_truthy` 接受 `1/true/yes` 大小写不敏感
2. `allowed_tools()`：`COORDINATOR_ALLOWED_TOOLS = [Agent, TeamCreate, TeamDelete, TaskCreate, TaskGet, TaskList, TaskUpdate, SendMessage, read_file, glob, grep, bash]`
3. `system_prompt_suffix()`：四阶段（Research 队员并行 → Synthesis Lead 不委托理解 → Implementation 队员 → Verification 队员）+ **派完停手纪律**（派完禁止立刻自读探索/轮询凑时间，唯一该做发一行总结；允许自读仅限 Research 首次定位 / Synthesis 读队员报告 / Verification git diff 收敛）（F14.5/F14.6）

**验证：** 单测双锁 4 组合（仅 11 → True）+ allowed_tools 含 bash 不含 write_file/edit_file + suffix 含四阶段与纪律关键词

## T24: team/tools/ 七工具

**文件：** `newcode/team/tools/`（9 个 py）
**依赖：** T7-T9 + T5-T6
**步骤：**
1. `teammate_filter.py`：`TEAMMATE_EXTRA_TOOLS` 常量（与 filter.py 同源或 import）
2. 各工具 `new_xxx_tool(mgr) -> Tool`（复用 `newcode/tools/base.py`）：
   - `task_create.py`（title 必填 + description/assignee/blocked_by）→ Store.create
   - `task_get.py` / `task_list.py`（status 过滤 + is_ready） / `task_update.py`（含 add/remove blocks 双向）
   - `send_message.py`：校验调用者在 Team 内 → resolve（`*` 广播）→ box.write → Pane `backend.wake` → **in-process 已停目标**：`set_member_active(True)` + mark_read 刚写消息 + 恢复 conv（`session.recover_session_async`）+ `task_mgr.continue_agent` + （TD-8 去重）→ 返回 `{delivered_to, timestamp}`；`plan_approval_response` 仅 Lead 可发、`shutdown_response` 仅发 Lead（F8.6）
   - `team_create.py`：manager.create → **非 coordinator 建队后把 collab 工具注册进主 registry**（TD-2）
   - `team_delete.py`：manager.delete → 无活跃团队注销 collab 工具
3. `read_only`：TaskGet/TaskList True；其余 False

**验证：** 单测各工具（mock Manager）+ 消息类型权限规则 + 广播 + 续写检测

## T25: agent_tool.py

**文件：** `newcode/tools/agent_tool.py`
**依赖：** T21 + T13
**步骤：**
1. `AgentTool.__init__` 加 `team_hook: TeamHook | None = None`；schema 加 `team_name` 字段（F24）
2. `execute`：`team_name` 非空 → 校验 team_hook 存在（否则「团队功能未启用」）→ `is_teammate_context(ctx)` 校验（in-process 队员抛 `InProcessTeammateNoSpawnError`）→ `await team_hook.spawn_teammate(TeamSpawnRequest(...))`（F25）
3. 无 team_name 维持 ch13 行为

**验证：** 单测 team_name 分支委托 mock team_hook；无 hook 报错；无 team_name 走原路径

## T26: cli_team_member.py

**文件：** `newcode/team/cli_team_member.py`
**依赖：** T21-T22 + T16
**步骤：**
1. `run_team_member(args)`：`os.chdir(worktree)` → 加载配置 + provider（ccswitch 兜底）→ 构造 Manager（扫 teams 还原）→ 开 team → TeammateContext（闭包注入 Box）
2. 构造 Registry（default + 成员工具池覆盖：collab 工具 + Pane 成员含 agent 工具但 team_name 屏蔽）+ PermissionChecker（sandbox_root=worktree、plan 时 PLAN）+ Agent（`dont_ask=True`、`is_interactive=False`、`teammate=ctx`、`runtime=SessionRuntime.open_at(session_dir)`）
3. 注入 `<team-context>` initial reminder
4. stdin reader task：`asyncio.to_thread(sys.stdin.readline)` 非空 → `wake_event.set()`
5. 主循环：`read_unread(agent_id)` → 空 `await asyncio.wait_for(wake_event.wait(), 2.0)`；有未读按 type 分流（text → task；plan_approval_response → 由 team_mailbox 注入已切权限 + 续派 prompt；shutdown_request → 优雅退出）→ `run_to_completion(task)` → 完成写 Lead mailbox idle + `set_member_active(False)`；mailbox 目录消失 → 退出
6. 事件转只读日志流（Text print / `● tool(args)` / Done 横线 / 错误 stderr）

**验证：** mock mailbox + wake_event 单测主循环分流；mailbox 目录删除触发退出

## T27: main.py 装配

**文件：** `newcode/main.py`
**依赖：** T24-T26 + T7 + T20
**步骤：**
1. argparse 加 `--team-member` + `--team/--member/--agent-id/--session-dir/--worktree/--agent-type/--model/--plan-mode`；`main()` 顶部检测 `--team-member` → **先 chdir(worktree) 再** `asyncio.run(run_team_member(args))` 返回
2. `_amain`：`features_cfg = load_features_config(cwd)`；构造 `name_reg` → `task_mgr.set_name_registry(name_reg)` → 构造 `team.Manager`（worktree_mgr None → 团队功能结构化降级警告）
3. **cli 装配注册 on_task_done**（非 Manager 自注册）：`task_mgr.on_task_done(lambda tid: team_mgr.handle_task_done(tid))`
4. 注册 TeamCreate/TeamDelete（恒）；coordinator 启用时注册 collab 工具（TD-2）
5. `AgentTool` 注入 `team_hook`（spawn_teammate + is_teammate_context）
6. coordinator 激活：构造 Agent 前 `stable_prompt += coordinator.system_prompt_suffix()`；构造后 `agent.set_allowed_tools(coordinator.allowed_tools())`；REPL 传 coordinator 标记
7. REPL 注入 team_mgr；`register_all` 已含 team 模块

**验证：** `python -m newcode --version` 正常；`NEWCODE_COORDINATOR_MODE=1` + config 开 → 启动不崩、主 Agent 工具集收窄；无 config 不进入

## T28: TUI lead mail + 自动续推

**文件：** `newcode/tui/tasks.py`、`newcode/tui/app.py`
**依赖：** T27 + T7
**步骤：**
1. `tui/tasks.py`：`consume_lead_mail(repl)`——循环每 1s `team_mgr.poll_lead_mailboxes()` → 组 `<team-update>`（notices.team_update，8000 截断）→ `runtime.append_raw_reminders`（TD-3）→ `lead_mail_event.set()` → IDLE 时 `session.app.exit()` 打断 prompt（TD-5）；`wait_for_lead_mail(repl)`——等事件 → IDLE 走 `begin_autonomous_turn` → `event.clear()`
2. `app.py`：`__init__` 加 `team_mgr`、`coordinator_mode`、`_lead_mail_event`；`run()` 启动 lead mail 后台任务；`prompt_async` 返回 None 视作 wake 信号；`begin_autonomous_turn`（合成 `[team-update] 队员发来新消息…` user 消息 → `_run_stream`）
3. `_toolbar()`：coordinator 显示 `[COORDINATOR]`；活跃团队显示 `[team:<name>]`
4. `_drain_task_notifications`：`team_mgr.is_teammate(id)` → `build_team_notification`（含 usage）注入

**验证：** mock team_mgr 单测 consume_lead_mail 取回/标 read/reminder 注入；IDLE→autonomous turn；非 idle 不主动 wake；存量 TUI 测试全绿

## T29: slash /team

**文件：** `newcode/slash/commands/team.py`、`newcode/slash/commands/__init__.py`
**依赖：** T27
**步骤：**
1. `/team list`（`<name>  <backend>  <member_count> 成员  [active/total] 活跃`）
2. `/team info <name>`（配置路径 + 成员详情）
3. `/team delete <name> [--force]` → `manager.delete`
4. `/team kill <member>` → 查所属 Team → `backend.kill` + `remove_member`
5. `COMMAND_MODULES` 加 `team`；经 `CommandContext.team_mgr` 访问

**验证：** 单测四子命令分流（mock manager）；`register_all` 无冲突

## T30: 版本 bump

**文件：** `newcode/__init__.py`、`pyproject.toml`
**依赖：** 无
**步骤：**
1. 两处 `0.14.0` → `0.15.0`

**验证：** `grep -r "0.15.0" newcode/__init__.py pyproject.toml` 两处一致

## T31: 测试批 + 回归

**文件：** `tests/test_team_*.py` 11 个（见 plan 文件组织）
**依赖：** T1-T30
**步骤：**
1. 补齐：manager（生命周期/持久化/reload 时序/on_task_done 处理）、spawn（流程/同名覆盖/权限拦截）、mailbox（并发/stale 锁）、registry、tasks（双向依赖/is_ready）、backend_detect（四分支）、backend_tmux（mock 子进程）、backend_inprocess、tools（7 工具 + 可见性：主 Agent/队员/普通子 Agent + 广播 + 续写）、notify（含 usage）、lead_mail（consume/自动续推）、coordinator（双锁/收窄/文案）
2. 每个测试 docstring 标注防的 bug（CLAUDE.md 测试规范）；mock 驱动真实代码路径
3. `ruff format` + `ruff check` 全库；**确认 docs/ 未被改动**（N16）
4. 全量 `pytest`（含 ch04~ch14 存量零回归）

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && export LC_ALL=C.UTF-8 && python -m pytest tests/ -q
export PYTHONIOENCODING=utf-8 && export LC_ALL=C.UTF-8 && python -m ruff check .
export PYTHONIOENCODING=utf-8 && export LC_ALL=C.UTF-8 && python -m newcode --version
```

## 执行顺序

```
T1 → T2 → T3 → T4 → T5 → T6
         ↘                    （T5/T6 依赖 T3，可并行）
T7 → T7b（依赖 T7）→ T8（可并行）
T9 → T10 → T11 → T12（T10-T12 互不依赖，可并行）
T13 → T14（依赖 T13）；T15（依赖 T13）；T16 可并行
T17 → T18（依赖 T17）
T19（可并行）
T20（依赖 T5）
T21（依赖 T7-T20 + 后端 + launcher）
T22（依赖 T21）
T23（依赖 T8）；T30 可并行
T24（依赖 T7-T9 + T5-T6）
T25（依赖 T21 + T13）
T26（依赖 T21-T22 + T16）
T27（依赖 T24-T26 + T7 + T20）
T28（依赖 T27 + T7）→ T29（依赖 T27）
T31（测试批 + 回归，全部完成后）
```
