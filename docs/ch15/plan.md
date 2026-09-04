# NewCode ch15 - AgentTeam 与 Coordinator Mode Plan

## 架构概览

本章引入 `newcode.team` 顶层包，把 ch13 SubAgent 的「子 Agent」扩展为「Team 队员」。整体分**四层**：

1. **数据模型层**（`team/types.py` + `team/manager.py` + `team/persistence.py`）——Team、TeammateInfo 数据结构与持久化
2. **后端层**（`team/backend/`）——`Backend` Protocol 与三种实现 tmux / iterm2 / inprocess，屏蔽 spawn 差异
3. **协作层**（`team/mailbox/`、`team/registry/`、`team/tasks/`）——邮箱（含文件锁）、AgentNameRegistry、共享任务列表
4. **工具与集成层**（`team/tools/` + `agent` 包扩展 + `coordinator` 包 + `tui`/`cli`/`slash` 装配）——5 个协作工具 + TeamCreate/TeamDelete + `Agent` 工具 `team_name` 分支 + Coordinator Mode

Lead 仍是 `tui.REPL` 驱动的主 Agent——本期 Lead 不引入独立类型，通过 `coordinator.is_enabled()` 在启动时收窄其工具集。

依赖方向（单向）：
```
tui  ──→  agent  ──→  team  ──→  team/{backend,mailbox,registry,tasks,tools}
                       └──→  worktree(ch14)、task(ch13)、session(ch12)、subagent(ch13)
```
- `team` 不反向依赖 `agent` 包（避免环）——`agent` 通过新增的 `TeamHook` Protocol 注入 team 行为，实现由 cli 装配时注入（TD-12）
- 例外：`team/backend/inprocess.py` 子模块允许依赖 `agent` 包（`agent` 在更低层，无环）（TD-15）
- `task.Manager` 经 `on_task_done` 回调注册接口反向通知 team（依赖反转，TD-13）

## 核心数据结构

### `Team`（team/types.py）
```python
from dataclasses import dataclass, field
from datetime import datetime
import asyncio

@dataclass
class Team:
    name: str                       # 用户给的原始名
    sanitized_name: str             # 经 sanitize 后用于路径，Team 主键
    lead_agent_id: str              # 固定 "lead"（本期 Lead = 主 Agent）
    backend: "BackendType"          # 全 team 默认后端；可被 member 覆盖
    description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    members: list["TeammateInfo"] = field(default_factory=list)

    # 派生路径（不持久化）
    config_dir: str = ""            # <home_dir>/.newcode/teams/<sanitized_name>/
    config_path: str = ""           # <config_dir>/config.json
    tasks_path: str = ""            # <config_dir>/tasks.json
    mailbox_dir: str = ""           # <config_dir>/mailbox/

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
```

### `TeammateInfo`（team/types.py）
```python
@dataclass
class TeammateInfo:
    name: str                       # Team 内唯一，SendMessage 寻址
    agent_id: str                   # agent-a<hex> 或 "lead"
    agent_type: str = ""            # subagent 定义名；"" 表 Fork
    model: str = ""                 # 覆盖，"" 表 inherit
    worktree_path: str = ""         # 绝对路径
    branch: str = ""
    backend_type: "BackendType" = "in-process"
    pane_id: str = ""               # tmux pane / iterm2 split id；in-process 空
    is_active: bool | None = None   # None/True 活跃；False 空闲；不存在视为终止
    plan_mode_required: bool = False
    session_dir: str = ""           # 绝对路径
```
序列化用手写 `to_dict` / `from_dict`（F19c 的 reload 流程需要细粒度控制 `is_active` 的 None 语义）。

### `Manager`（team/manager.py）
```python
@dataclass
class Manager:
    teams: dict[str, Team] = field(default_factory=dict)   # 按 sanitized_name 索引
    home_dir: str = ""
    wt_mgr: "worktree.Manager" = None
    task_mgr: "task.Manager" = None
    registry: "AgentNameRegistry" = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
```

### 后端抽象（team/backend/__init__.py）
```python
class BackendType(StrEnum):
    TMUX = "tmux"; ITERM2 = "iterm2"; IN_PROCESS = "in-process"

@dataclass
class SpawnRequest:
    team_name: str; member_name: str; agent_id: str
    worktree_path: str; session_dir: str
    agent_type: str; model: str; initial_prompt: str
    plan_mode_required: bool = False
    sub_agent: Any = None       # in-process：agent.Agent（由 team 包预先构造）
    conv: Any = None            # in-process：conversation.Conversation
    task_mgr: Any = None        # in-process：task.Manager

class Backend(Protocol):
    def type(self) -> BackendType: ...
    async def spawn(self, req: SpawnRequest) -> tuple[str, str]: ...   # (pane_id, agent_id)
    async def wake(self, pane_id: str, agent_id: str) -> None: ...
    async def kill(self, pane_id: str, agent_id: str) -> None: ...
```
`SpawnRequest.sub_agent` 由调用方（team 包）预先构造好——`backend` 包只做调度，不知道 `agent.Agent` 类型（解环的关键）。

### 邮箱（team/mailbox/）
```python
class MessageType(StrEnum):
    TEXT = "text"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"

@dataclass
class Message:
    from_: str; to: str; type: MessageType; summary: str; content: str
    payload: dict | None = None; timestamp: int = 0; read: bool = False

class Box:
    def __init__(self, dir_: str) -> None: ...       # <team_config_dir>/mailbox/
    async def write(self, agent_id: str, msg: Message) -> None: ...
    async def read(self, agent_id: str) -> list[Message]: ...
    async def read_unread(self, agent_id: str) -> tuple[list[int], list[Message]]: ...
    async def mark_read(self, agent_id: str, indices: list[int]) -> None: ...
```
文件锁内置在 `Box` 内（`lock.py`），所有公开方法都走锁。

### 名称注册表（team/registry/__init__.py）
```python
class AgentNameRegistry:
    def __init__(self) -> None: ...
    def register(self, name: str, agent_id: str) -> None: ...   # 后注册覆盖前（弱引用）
    def unregister(self, name: str) -> None: ...
    def resolve(self, name_or_id: str) -> str | None: ...
    def name_of(self, agent_id: str) -> str | None: ...
```
本章把 `task.Manager._by_name` 替换为委托——`task.Manager` 持一个 `AgentNameRegistry` 引用。

### 共享任务列表（team/tasks/）
```python
class Status(StrEnum):
    PENDING = "pending"; IN_PROGRESS = "in_progress"; COMPLETED = "completed"; BLOCKED = "blocked"

@dataclass
class Task:
    id: str; title: str; description: str = ""; status: Status = Status.PENDING
    assignee: str = ""
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    created_at: int = 0; updated_at: int = 0

class Store:
    def __init__(self, path: str) -> None: ...        # <config_dir>/tasks.json + tasks.lock
    async def create(self, t: Task) -> str: ...
    async def get(self, id_: str) -> Task: ...
    async def list_(self, filter_: "Filter") -> list[Task]: ...
    async def update(self, id_: str, patch: "Patch") -> None: ...
```
`filter.py`：`Filter`（status 过滤）/ `Patch`（含 add_blocks/remove_blocks/add_blocked_by/remove_blocked_by 双向维护）/ `is_ready`（无未完成 blocker）。

### TeamHook（agent/team_hook.py，agent 包内，无 team 依赖）
```python
@dataclass
class TeamSpawnRequest:
    team_name: str; prompt: str; subagent_type: str = ""; model: str = ""
    name: str = ""; plan_mode_required: bool = False

class TeamHook(Protocol):
    # 委托 Team Manager 处理 team_name 分支；返回 final_text（立即返回 task_id JSON）
    async def spawn_teammate(self, req: TeamSpawnRequest) -> str: ...
    # 判断当前上下文是否在某队员执行上下文中（嵌套 spawn 拦截）
    def is_teammate_context(self, ctx) -> tuple[str, str, bool]: ...  # (team, member, is_inprocess)

class IncomingMailbox(Protocol):   # agent.run 每轮调用（队员邮箱注入）
    async def inject_incoming(self) -> list[str]: ...   # 读未读 → <incoming-messages> reminder，mark_read
```
`team` 包实现这两个 Protocol 的结构类型（spawn.py / context.py），cli 装配时注入 `AgentTool.team_hook`。

### coordinator 包（顶层，3 个纯函数，无状态）
```python
def is_enabled(cfg) -> bool: ...            # feature(COORDINATOR_MODE) and env_truthy(NEWCODE_COORDINATOR_MODE)
def allowed_tools() -> list[str]: ...        # COORDINATOR_ALLOWED_TOOLS 常量
def system_prompt_suffix() -> str: ...       # 四阶段 + 派完停手纪律
```

## 模块设计

### 层 1：数据模型层（team/types + manager + persistence）

- **`types.py`**：Team / TeammateInfo / BackendType / 异常家族（`TeamError` → `TeamNotFoundError` / `TeamHasActiveMembersError` / `MemberNameConflictError` / `InProcessTeammateNoSpawnError` / `BackendUnavailableError` / `SendMessageValidationError`）
- **`manager.py`（Manager）**：
  - 构造：校验 `~/.newcode/teams/` 可写；扫描子目录还原 `teams` dict（坏 JSON 跳过 + stderr 警告，F17.2）；`task_mgr.on_task_done(self._on_task_done)` 注册回调（TD-13）
  - `async create(name, agent_type) -> Team`：sanitize → 同名后缀 `-2/-3` → 建目录 + mailbox/ → detect_backend → 写 config.json（Lead 首个成员）→ 入 dict
  - `get(name)` / `async delete(name, force)`（顺序：kill → 删 session/worktree → rmtree(config_dir)，F17.4）
  - `member_of(agent_id) -> (Team, TeammateInfo) | None` / `is_teammate(agent_id) -> bool`（TUI 通知选型用）
  - `async poll_lead_mailboxes() -> list[LeadMessage]`（F11.3）
  - `_on_task_done(agent_id)`：in-process 成员自然结束 → `set_member_active(False)`（TD-4；Pane 由子进程自己做）
- **`persistence.py`**：`sanitize(name)`（F1.4 防路径遍历）；`save_config`（`.tmp` + `os.replace` 原子写）；`load_config`；`Team.add_member/set_member_active/remove_member` 的持久化侧 `_reload_from_disk_locked()`（加锁后重读 disk members 再改写再 save，F1.7 跨进程丢更新防护）

### 层 2：后端层（team/backend/）

- **`__init__.py`**：`Backend` Protocol / `SpawnRequest` / `new_backend(t: BackendType, **deps) -> Backend` 工厂
- **`detect.py`**：`detect() -> BackendType`——`$TMUX` → tmux；`$TERM_PROGRAM==iTerm.app` 且 `it2` 可执行 → iterm2；`shutil.which("tmux")` → tmux；否则 in-process（F2.4，一次性决定，启动后不运行时回退）
- **`tmux.py`（TmuxBackend）**：spawn = `asyncio.create_subprocess_exec("tmux","split-window","-h","-P","-F","#{pane_id}","--",cmd)` 捕获 pane_id；cmd = `python -m newcode --team-member ...`（含预生成 `--agent-id`，F3.2）；会话外 `tmux new-session -d` detached、失败抛 `BackendUnavailableError`（F2.5）；wake = `tmux send-keys -t <pane_id> "" Enter`；kill = `tmux kill-pane` 忽略不存在
- **`iterm2.py`（骨架）**：接口约定 `it2 split --new-pane --command` / `it2 send-text --pane` / `it2 close-pane --pane`；本环境 WSL 不可验证，实装标「待人工验证」（F4.1）
- **`inprocess.py`**（允许依赖 agent，TD-15）：spawn = `task_mgr.launch(sub, initial_prompt, name=member_name)` 返回 task_id 作 agent_id；wake no-op；kill = `task_mgr.stop(agent_id)`

### 层 3：协作层（team/mailbox/ + registry/ + tasks/）

- **`mailbox/lock.py`**：`FileLock`——`os.open(O_CREAT|O_EXCL|O_WRONLY)` 抢锁；失败 5-100ms 随机抖动重试 ≤10 次；持锁超 10s（`st_mtime`）视为 stale 删锁重试（F8.4）
- **`mailbox/message.py`**：Message / MessageType / 序列化
- **`mailbox/__init__.py`（Box）**：`<config_dir>/mailbox/<agent_id>.json` 单文件 `{"messages":[...]}`（F32）；write = 抢锁 + read-modify-write + `os.replace`；广播辅助 `write_broadcast(sender, msg)`（除 sender 外所有活跃成员，F8.5）
- **`registry/__init__.py`**：AgentNameRegistry（threading.Lock + by_name/by_id）
- **`tasks/__init__.py`（Store）** + **`filter.py`**：tasks.json 单文件 + `tasks.lock`（同 mailbox 锁机制）；create/get/list_/update 双向依赖维护（F26-F30）

### 层 4：工具与集成层

**`team/tools/`**（各工具一个 `new_xxx_tool(mgr) -> Tool` 工厂）：
- `team_create.py`（TeamCreate）：team_name 必填 + description/agent_type；execute → manager.create → 返回 `{team_name, backend, config_path}`；**非 coordinator 下建队后把 collab 工具注册进主 registry**（团队上下文开始，TD-2）
- `team_delete.py`（TeamDelete）：team_name + force；execute → manager.delete → 无活跃团队时从主 registry 注销 collab 工具
- `task_create.py` / `task_get.py` / `task_list.py` / `task_update.py`（内部名 task_create/task_get/task_list/task_update）：薄封装 Store（F26-F29）
- `send_message.py`（内部名 send_message）：校验调用者在 Team 内 → resolve to（`*` 广播）→ `box.write` → Pane 目标 `backend.wake` → in-process 已停目标续写（TD-8）→ 返回 `{delivered_to, timestamp}`（F31/F34）
- `teammate_filter.py`：`TEAM_COLLAB_TOOLS = frozenset({"task_create","task_get","task_list","task_update","send_message"})`——队员专属工具白名单，注入 `apply_agent_tool_filter`（N2）

**`agent` 包扩展**：
- `team_hook.py`（新建）：TeamHook Protocol / TeamSpawnRequest / IncomingMailbox Protocol（TD-12）
- `team_mailbox.py`（新建）：成员 Loop 头部邮箱注入——agent.run 每轮经 `IncomingMailbox.inject_incoming()` 把 `<incoming-messages>` reminder 拼入 reminders（F11.1）
- `agent_tool.py`（修改）：schema 加 `team_name`（F24）；execute 中 team_name 非空 → `self._team_hook.spawn_teammate(TeamSpawnRequest(...))`（F25）；`team_hook` 为 None 时降级 ch13 行为
- `agent.py`（修改）：`__init__` 加 `incoming: IncomingMailbox | None`、`allowed_tools: list[str] | None`；`run()` 每轮 `reminders.extend(await incoming.inject_incoming())`；`run()` raw reminder 消费（TD-3）；`set_allowed_tools`（**新增方法**）收窄 tool_defs + 硬过滤 known_calls（TD-11）

**`task` 包扩展（task/manager.py）**：
- 持 `name_reg: AgentNameRegistry` 引用（`_by_name` 字段废弃，改委托）
- 新增 `on_task_done(fn: Callable[[str], Awaitable[None]])` 回调注册；`_drive` finally（终态）调 `await self._on_task_done(bt.id)`（TD-13）
- `continue_agent` 复用（in-process 已停队员续派直接调）

**`coordinator` 包（独立顶层）**：`is_enabled`（双锁）/ `allowed_tools`（`COORDINATOR_ALLOWED_TOOLS = [Agent, TeamCreate, TeamDelete, TaskCreate, TaskGet, TaskList, TaskUpdate, SendMessage, read_file, glob, grep, bash]`）/ `system_prompt_suffix`（四阶段 + 派完停手纪律文案）

**`config` 包**：新增 `FeaturesConfig`——`features.coordinator_mode` / `features.fork_teammate`（三层合并，镜像 worktree/config.py 模式）（TD-1）

**`session/runtime.py`**：加 raw reminder 通道 `append_raw_reminders/take_raw_reminders`（TD-3）；加 `open_at(abs_session_dir, model)` 辅助（Pane 子进程以绝对 session_dir 恢复，F6.1）

**`subagent` 扩展**：`AgentDefinition` 加 `plan_mode_required: bool = False`（types + parser 解析 `planModeRequired`，F48/F13.1）；`launcher.make_sub_agent` 加 `runtime/teammate/extra_tools/permission_mode/dont_ask` 可选参；`build_sub_registry` 加 `extra_tools`（collab 工具注入成员工具池，TD-7）

**`permission/sandbox.py`**：`check_path` 对 `/tmp`、`/private/tmp` 前缀放行（N14，TD-10，file-class；bash 走 exec-class 不受影响）

**`tui` 扩展**：
- `tui/tasks.py`：`consume_lead_mail(repl)`——每 1s `manager.poll_lead_mailboxes()` → 组 `<team-update>`（8000 截断、完整报告透传）→ `runtime.append_raw_reminders` → `lead_mail_event.set()` → IDLE 时 `session.app.exit()` 打断 prompt（TD-5）；`wait_for_lead_mail(repl)`——IDLE 走 `begin_autonomous_turn`（合成 `[team-update] 队员发来新消息…` → `_run_stream`），非 idle 不主动 wake（raw reminder 已被当前 Run 下一轮取走）
- `tui/app.py`：注入 `team_mgr` / `coordinator_mode`；启动 lead mail 后台任务；`prompt_async` 返回 None 视作 wake 信号；`_toolbar()` 加 `[COORDINATOR]`（F14.4）与 `[team:<name>]`（活跃团队时）；`_drain_task_notifications` 对 `team_mgr.is_teammate(id)` 用 `build_team_notification`（含 usage）

**`cli` 扩展（main.py）**：
- argparse 加 `--team-member` + `--team/--member/--agent-id/--session-dir/--worktree/--agent-type/--model/--plan-mode`；`main()` 检测 `--team-member` → 短路走 `asyncio.run(cli_team_member.run_team_member(args))`
- `cli/team_member.py`（新建，F6）：chdir(worktree) → 加载配置（ccswitch 兜底）→ 构造 TeamManager/Box/TeammateContext → Registry（default + 成员工具池覆盖，Pane 成员含 agent 工具但 team_name 屏蔽）→ PermissionChecker（sandbox_root=worktree、plan 时 mode=PLAN）→ Agent（dont_ask=True、is_interactive=False、incoming=ctx、runtime=open_at(session_dir)）→ 注入 `<team-context>` reminder → stdin reader（回车 → wake_event）→ 主循环（mailbox.read → wait_for(wake_event, 2s) → text/plan_approval/shutdown 分流 → run_to_completion → 写 Lead mailbox idle + set_member_active(False) → mailbox 目录消失 → 优雅退出）→ 只读日志流（Text print / `● tool(args)` / Done 横线 / 错误 stderr）
- 装配：`load_features_config` → 构造 Manager（worktree_mgr 为 None 时团队功能结构化降级）→ 注册 TeamCreate/TeamDelete（恒）+ coordinator 时 collab 工具 → AgentTool 注入 team_hook → coordinator 激活（`set_allowed_tools` + stable_prompt 拼 suffix + REPL 标记）→ REPL 注入 team_mgr → `register_all` 加 team 模块

**`slash/commands/team.py`**（新建，F16）：`/team list` / `/team info <name>` / `/team delete <name> [--force]` / `/team kill <member>`；`CommandKind.LOCAL`，经 `CommandContext.team_mgr` 访问

**版本**：`newcode/__init__.py` + `pyproject.toml` → 0.15.0

## 模块交互（数据流）

```
① TeamCreate：LLM → TeamCreateTool → manager.create（sanitize→detect→config.json→Lead 成员）
             → 主 registry 注册 collab 工具（团队上下文开始）

② Spawn：LLM 调 Agent(team_name=...) → agent.AgentTool.execute
   → self._team_hook.spawn_teammate(TeamSpawnRequest)（agent 不 import team）
   → team.spawn.spawn_teammate（结构类型实现 TeamHook）
     catalog.resolve(role) → wt_mgr.create("team-<sanitized>/<member>") → member session
     → launcher.make_sub_agent(plan 模式?/dont_ask/teammate/extra_tools)
     → Pane: box.write(initial_prompt 预写) → backend.spawn → pane_id
     → in-process: backend.spawn = task_mgr.launch(sub, prompt) → agent_id
     → reg.register(member) → team.add_member(TeammateInfo)（reload-before-modify）

③ 成员运行（in-process）：task_mgr._drive → run_to_completion
   每轮 run(): incoming.inject_incoming() → <incoming-messages> reminder
   终态 → on_task_done(agent_id) → team._on_task_done → set_member_active(False)
   → done 队列 → TUI _drain_task_notifications → build_team_notification（含 usage）注入 Lead conv

④ 成员运行（pane）：main.py --team-member → cli_team_member.run_team_member
   chdir(worktree) → 构造 agent(dont_ask/incoming/runtime) → <team-context>
   → 主循环 mailbox.read → wait_for(wake_event,2s) → run_to_completion
   → 完成写 Lead mailbox idle + set_member_active(False)（跨进程 reload-before-modify）

⑤ SendMessage：TeamSendMessageTool → 调用者身份（Lead active team / 队员 TeammateContext）
   → resolve(to)/广播 → box.write（锁文件）→ Pane: backend.wake(pane_id)
   → in-process 已停: mark_read + session 恢复 conv + task_mgr.continue_agent + set_member_active(True)

⑥ Lead 收队员更新：consume_lead_mail（1s）→ poll_lead_mailboxes → <team-update> raw reminder
   → lead_mail_event → IDLE 时 app.exit 打断 prompt → begin_autonomous_turn → _run_stream
   （STREAMING 中：raw reminder 被当前 Run 下一轮 take_raw_reminders 取走）

⑦ Coordinator：is_enabled(双锁) → set_allowed_tools(COORDINATOR_ALLOWED_TOOLS) + 提示词 + [COORDINATOR]
   → Lead 派完停手 → 收 <task-notification>/<team-update> → SendMessage 续写（核心循环）

⑧ 收敛：Lead 用 Bash git merge worktree-team-<sanitized>+<member> --no-ff → 冲突 edit_file/bash 解决
   → 搞不定 git merge --abort 保留 worktree 上报

⑨ 清理：TeamDelete → 非 force 活跃校验 → backend.kill 逐个 → 删 session/worktree → rmtree(config_dir)
```

## 文件组织

```
newcode/team/                      新建（顶层包，四层）
├── __init__.py                    包导出
├── types.py                       Team / TeammateInfo / BackendType / 异常家族
├── manager.py                     Manager（create/get/delete/member_of/is_teammate/poll_lead_mailboxes/on_task_done 处理）
├── persistence.py                 sanitize + config.json 原子写 + reload_from_disk_locked
├── spawn.py                       spawn_teammate（TeamHook 结构实现）
├── context.py                     TeammateContext（IncomingMailbox 实现）
├── notices.py                     team-context / 附录 / incoming-messages / team-update 文案
├── notify.py                      build_team_notification（含 usage）
├── cli_team_member.py             run_team_member（--team-member）
├── backend/
│   ├── __init__.py                Backend Protocol / SpawnRequest / new_backend
│   ├── detect.py                  detect()
│   ├── tmux.py                    TmuxBackend
│   ├── iterm2.py                  Iterm2Backend（骨架，待人工验证）
│   └── inprocess.py               InProcessBackend（依赖 agent，低层）
├── mailbox/
│   ├── __init__.py                Box
│   ├── lock.py                    FileLock（抢锁/重试/stale）
│   └── message.py                 Message / MessageType
├── registry/
│   └── __init__.py                AgentNameRegistry
├── tasks/
│   ├── __init__.py                Task / Store
│   └── filter.py                  Filter / Patch / is_ready
└── tools/
    ├── __init__.py
    ├── team_create.py / team_delete.py
    ├── task_create.py / task_get.py / task_list.py / task_update.py / send_message.py
    └── teammate_filter.py         TEAM_COLLAB_TOOLS 白名单

newcode/coordinator/               新建（独立顶层包，3 纯函数）
└── __init__.py                    is_enabled / allowed_tools / system_prompt_suffix

newcode/agent/
├── agent_tool.py                  修改：team_name 参数 + team_hook 委托
├── team_hook.py                   新建：TeamHook Protocol / TeamSpawnRequest / IncomingMailbox Protocol
├── team_mailbox.py                新建：成员 Loop 头部邮箱注入
└── agent.py                       修改：incoming 注入 / set_allowed_tools / raw reminders / 收窄强制

newcode/task/manager.py            修改：name_reg 委托（废弃 _by_name）+ on_task_done 回调
newcode/session/runtime.py         修改：raw reminder 通道 + open_at 辅助
newcode/subagent/
├── types.py / parser.py           修改：plan_mode_required 字段
└── launcher.py                    修改：make_sub_agent/build_sub_registry 扩展参数
newcode/permission/sandbox.py      修改：/tmp + /private/tmp 白名单（N14）
newcode/config/                    修改：FeaturesConfig（features.coordinator_mode / fork_teammate）
newcode/tui/
├── app.py                         修改：team_mgr / coordinator 标签 / lead mail 消费 / 自动续推
└── tasks.py                       修改：consume_lead_mail / wait_for_lead_mail
newcode/slash/commands/team.py     新建：/team 四子命令
newcode/slash/commands/__init__.py 修改：COMMAND_MODULES 加 team
newcode/main.py                    修改：装配 + --team-member CLI + coordinator 激活
newcode/__init__.py / pyproject.toml  修改：版本 0.15.0

tests/
├── test_team_manager.py           Team 生命周期 + 持久化 + reload
├── test_team_spawn.py             spawn 流程 + 同名覆盖 + 权限拦截
├── test_team_mailbox.py           并发与 stale 锁
├── test_team_registry.py
├── test_team_tasks.py             双向依赖 + is_ready
├── test_team_backend_detect.py    三后端检测优先级
├── test_team_backend_tmux.py      mock 子进程驱动真实路径
├── test_team_backend_inprocess.py
├── test_team_tools.py             7 个工具 + 可见性（主/队员/普通子 Agent）
├── test_team_notify.py            团队 <task-notification> 含 usage
├── test_team_lead_mail.py         consume_lead_mail / 自动续推
└── test_coordinator.py            双锁 + 收窄 + 派完停手文案
```

## 技术决策

| # | 决策点 | 选择 | 理由 |
|---|--------|------|------|
| TD-1 | feature flag 落点 | `config` 包 `FeaturesConfig`：`features.coordinator_mode` / `features.fork_teammate`（三层合并） | Coordinator 独立于 Team（不建队也可用）；`feature_has` 语义忠实 |
| TD-2 | collab 工具注册时机 | TeamCreate/TeamDelete 启动恒注册；collab 工具建队时动态注册、删队注销；coordinator 启动即注册 | 匹配「协作工具仅团队上下文出现」；建/删队是离散动作，prompt cache 在其间失效可接受 |
| TD-3 | `<team-update>` raw 通道 | SessionRuntime 加 `append_raw_reminders/take_raw_reminders`，agent.run 直接拼入（不经 hook_notification 包装） | hook 标签语义是 Hook 注入，复用会误导模型 |
| TD-4 | 完成通知双路径去重 | in-process 走 done-queue `<task-notification>`（团队格式含 usage）+ on_task_done 置 is_active=False；mailbox idle_notification 仅 Pane 成员写 | F41a 明确 Pane 只能靠 mailbox；防 Lead 收两份 |
| TD-5 | Lead idle 自动续推打断 | `session.app.exit()` 打断 `prompt_async`（None 作 wake 信号） | prompt_toolkit 下后台任务无法直接注入输入 |
| TD-6 | 邮箱并发锁 | `O_CREAT\|O_EXCL` + 5-100ms 抖动重试 10 次 + stale(10s) 判定 + `os.replace` 原子替换 | F8.4 明文；EEXIST 抢占（Python 无跨平台 flock 语义统一） |
| TD-7 | 成员工具池同名覆盖 | view(visible+extra) 后 `register()` 覆盖 task_list/get/send_message + 新增 task_create/update | 「复用 ch13 同名，团队态覆盖」；view() 返回独立 Registry，主 registry 不受影响 |
| TD-8 | in-process 已停续写去重 | 先写 mailbox（统一真值）→ 续写路径 mark_read 刚写消息 → `continue_agent` 以消息为新一轮 user 消息 | 单点投递避免 mailbox 重复注入 |
| TD-9 | --team-member 配置 | chdir(worktree) 后走 main() 配置流程（ccswitch 兜底） | worktree 已含 `.newcode/` 配置副本（ch14 F4.1）；provider 全局可用 |
| TD-10 | 沙箱 /tmp 白名单 | `check_path` 对 `/tmp`、`/private/tmp` 前缀放行（file-class） | N14 明文；全局生效，验收单列影响面 |
| TD-11 | coordinator 收窄强制 | `set_allowed_tools` 既收窄 tool_defs 又硬过滤 known_calls | 只藏定义可被注入提示绕过；满足 N8「运行时不可解锁」 |
| TD-12 | agent↔team 解耦 | `TeamHook`/`IncomingMailbox` Protocol 定义在 agent 包；team 结构类型实现；cli 装配注入 | 无环、可测（mock hook）、依赖单向 `agent→team` |
| TD-13 | task 通知依赖反转 | `task.Manager.on_task_done(fn)` 回调注册；team 初始化时注册 | task 包不反向依赖 team；单次注册比每 task 挂字段干净 |
| TD-14 | 嵌套 spawn 拦截 | `TeamHook.is_teammate_context(ctx) -> (team, member, is_inprocess)` | 调用者身份结构化判定；in-process 队员禁 spawn，Pane 队员 team_name 屏蔽 |
| TD-15 | backend inprocess 依赖 | `backend/inprocess.py` 子模块允许依赖 agent（低层）；tmux/iterm2 不依赖 | 避免污染其他 backend；无环 |

## spec 覆盖对照

- F1（Team/Manager/持久化）→ team/types + manager + persistence
- F2（后端抽象/检测）→ team/backend + detect
- F3-F5（三后端）→ backend/tmux + iterm2 + inprocess
- F6（team-member 子进程）→ cli_team_member + session.open_at
- F7（协作工具）→ team/tools + teammate_filter + spawn 注入
- F8（邮箱）→ team/mailbox（lock + message）
- F9（名称注册表）→ team/registry（task.Manager 委托）
- F10（spawn 流程）→ spawn.py + team_hook + launcher 扩展
- F11（邮箱读取/注入）→ agent/team_mailbox + tui/tasks + runtime raw 通道
- F12（空闲/续写）→ task.manager.on_task_done + notify + 续写路径
- F13（Plan 审批）→ plan_mode_required + plan_approval_response 分流
- F14（Coordinator）→ coordinator 包 + set_allowed_tools + 装配
- F15（收敛）→ 无专门 merge 工具，Bash 驱动（spec 既有）
- F16（/team）→ slash/commands/team.py
- F17（持久化/恢复）→ persistence + manager.scan + 版本 bump 0.15.0
- N14（沙箱 /tmp）→ permission/sandbox.py
