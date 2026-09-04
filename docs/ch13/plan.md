# NewCode ch13 - 多 Agent 分发架构 Plan

## 架构概览

ch13 的核心是一个新的 `newcode/subagent/` 包 + `newcode/tools/` 的扩展，把「子 Agent 的启动、过滤、运行、后台管理」从零散的既有代码（skills fork、hook 占位）收拢成一套统一底座，主 Agent 只通过一组工具与之交互。

```
                ┌────────────────────────── 主对话（TUI / main） ──────────────────────────┐
                │  Agent.run()  ReAct 循环                                              │
                │     │  调 agent / Task* / SendMessage 工具（经 ToolScheduler）          │
                │     ▼                                                                 │
                │  Registry  ── agent 工具 ── TaskList/TaskGet/TaskStop/SendMessage     │
                └───────┬───────────────────────────────────────────────────────────────┘
                        │ 工具 execute
                        ▼
   ┌───────────────────── subagent 包（统一底座）──────────────────────┐
   │  Catalog（4 层加载+优先级+fork_definition）→ AgentDefinition       │
   │  Launcher（build 子 Registry → 构造子 Agent → 前台/后台分派）      │
   │  Manager（BackgroundTask 生命周期/续派/排队/保留/清理/完成通知队列） │
   │  fork.py（build_forked_messages + Boilerplate）                   │
   └──────┬───────────────────────────────────────────────────────────┘
          │ 子 Agent 实例（Agent.run_to_completion 复用主循环）
          ▼
   子对话（独立 ConversationManager）＋ 子 Provider ＋ 子权限（共享规则层）
          │ 共享：Hook 引擎（事件带 agent_id）／ 文件系统 ／ 工具实例
          ▼
   完成 → Manager 推 done 队列 → TUI 空闲时以 user 角色 <task-notification> 注入主对话
```

设计原则（对应 spec G 系列）：
- **复用主循环**——`Agent.run_to_completion` 与 `run` 共用同一段 ReAct 循环代码（spec F5.2），不复制实现
- **统一底座**——定义式 / Fork 式 / Skill fork / hook agent 动作四路径都走 `Launcher`（Fork 经 `Catalog.fork_definition()` 伪定义统一建模），消灭并行实现（spec F10）
- **管理器持有任务**——子 Agent 的 run 始终是 Manager 名下 asyncio 任务（前台=工具 await 它，后台=工具立即返回 task_id，移交=任务所有权从工具转给后台，不杀重来）；**状态/计数全程在 Manager，移交无需中途接力**（spec F7.3）。本期仅超时自动移交（无 ESC 手动切换，spec「不做的事」）
- **过滤在构造时一次完成**——子 Agent 的工具集创建时固定，移交途中不重算（spec F6.6）
- **续派复用同 task_id**——状态语义=「同一 worker 继续」，`/tasks` 查询一个条目（spec F7.10，用户已确认）

## 核心数据结构

### AgentDefinition（`subagent/types.py`）

```python
@dataclass
class AgentDefinition:
    name: str                    # 角色名（subagent_type 取值），^[a-z][a-z0-9-]*$，1-32
    description: str             # 用途说明（必填）
    body: str                    # 正文 = 子 Agent 系统提示
    tools: list[str]             # 工具白名单（空 = 不限制）
    disallowed_tools: list[str]  # 工具黑名单
    model: str                   # "inherit" | "haiku" | "sonnet" | "opus"（缺省 inherit）
    max_turns: int               # 缺省 10（全局 Agent 上限）
    permission_mode: PermissionMode  # 仅四档（default/acceptEdits/plan/bypassPermissions）
    dont_ask: bool               # frontmatter permissionMode: dontAsk → mode=DEFAULT + dont_ask=True
    background: bool             # 角色强制后台（缺省 False）
    enabled: bool                # 缺省 True；False 不加载（verifier）
    source: Source               # PROJECT / USER / BUILTIN / PLUGIN
    source_path: str             # 来源文件路径（诊断用）

    def is_fork(self) -> bool:   # self.name == "__fork__"（fork_definition 的伪定义）
        ...
```

### Source（`subagent/types.py`）

```python
class Source(IntEnum):
    BUILTIN = 0
    USER = 1
    PROJECT = 2
    PLUGIN = 3   # 本期占位，恒为空
```

### BackgroundTask（`subagent/manager.py`）

```python
@dataclass
class BackgroundTask:
    id: str                    # agent-<hex>（续派复用同 id）
    name: str | None           # spawn 时 name（SendMessage 寻址）
    sub_agent: Agent           # 子 Agent 实例（含其 conv/provider/权限）
    task_text: str             # 当前轮任务文本
    status: str                # running / completed / failed / cancelled
    result: str                # 本轮跑完后填（最后一条 assistant 文本；续派覆盖）
    err: BaseException | None
    start_time: float          # monotonic（首轮启动）
    end_time: float | None     # 每轮结束更新
    usage: TokenUsage           # 本轮 token 用量
    total_usage: TokenUsage     # 该 Agent 全部轮次累计（上限/诊断用）
    tool_count: int             # 本轮工具调用次数
    last_activity: str          # 最近一次工具名
    round: int                  # 已执行轮数（首轮=1，续派+1，上限 10）
    queue: deque[str]           # 排队续派任务（≤2）
    idle_since: float | None    # completed 后时间戳（空闲清理用）
    cancel_event: asyncio.Event # 取消信号
    run_task: asyncio.Task | None  # 当前轮 run 任务（Manager 持有）
```

### RunOutcome / MaxTurnsReached（`agent/agent.py` / `subagent/errors.py`）

```python
@dataclass
class RunOutcome:               # run_to_completion 的正常返回
    text: str
    usage: TokenUsage
    tool_count: int
    stop_reason: StopReason     # NATURAL

class MaxTurnsReached(Exception):  # 触达 maxTurns（spec F5.2）
    text: str                   # 最后一条 assistant 文本
    usage: TokenUsage
    tool_count: int
```

### AgentConfig（`subagent/config.py`）

```python
@dataclass
class AgentConfig:
    enable_verifier: bool = False
    enable_subagent_background: bool = True   # 后台总闸（N7）
    async_timeout_s: float = 120.0            # 前台自动转后台阈值
    idle_cleanup_minutes: float = 15.0        # 空闲清理超时
    max_idle_agents: int = 10                 # 保留上限
    max_tasks_per_agent: int = 10             # 每 Agent 任务总数上限
    max_queue_per_agent: int = 2              # 每 Agent 排队上限
    model_tiers: dict[str, str] = {}          # haiku/sonnet/opus → 模型串

    def effective_enable_subagent_background(self) -> bool:
        """后台总闸生效值（字段本身缺省 True；显式方法供调用点语义清晰）。"""
        return self.enable_subagent_background
```

### 过滤常量与纯函数（`tools/filter.py`）

```python
GLOBAL_DENY: frozenset[str] = frozenset({"agent"})   # F6.1：任何子 Agent 永不可用
ASYNC_AGENT_ALLOWED_TOOLS: frozenset[str] = frozenset({
    "read_file", "write_file", "edit_file", "list_files", "search_code",
    "execute_command", "read_memory", "write_memory",
    # mcp__* 前缀工具按前缀匹配；load_skill 经 is_system_tool 豁免恒可见
})                                                    # F6.3：不含 agent

@dataclass
class FilterParams:
    all: list[str]                    # registry 全部工具名（按注册顺序）
    background: bool                  # 是否后台工作者
    role_tools: list[str]             # 定义 tools 白名单（空=不限制）
    role_disallowed: list[str]        # 定义 disallowedTools 黑名单

def apply_agent_tool_filter(p: FilterParams) -> list[str]:
    """spec F6.4：visible = 全部 − GLOBAL_DENY → 黑名单剔除 → 白名单交集 →
    后台再与 ASYNC_AGENT_ALLOWED_TOOLS(+mcp__*) 交集 → + 系统工具豁免。"""
```

## 模块设计

### subagent/types.py

`AgentDefinition` / `Source` / 错误（`DefinitionParseError`）与文案常量（`<task-notification>` 模板、任务文案）。导出给 catalog / launcher / tools 使用。

### subagent/parser.py —— 定义文件解析

**职责：** frontmatter + 正文分离、字段校验、名字归一化。
**对外接口：** `parse_definition(path, source) -> AgentDefinition`（复用 `skills/parser.py` 的 `parse_frontmatter_and_body`，不重复实现 frontmatter 切分）
**校验规则（spec F2.4）：** `name`（缺省取文件基名，归一化 `^[a-z][a-z0-9-]*$`）、`description` 必填；`model` 非法 → 降级 `inherit` + warning；`permissionMode` 非法 → 降级 `default` + warning，**`dontAsk` → mode=DEFAULT + dont_ask=True**；`tools/disallowedTools` 非 list → 记 warning 置空。解析失败抛 `DefinitionParseError`（Catalog 决定 skip 或 raise）。

### subagent/catalog.py —— 四层加载与优先级

**职责：** 启动期一次性加载角色，维护 `name → AgentDefinition` 的按优先级覆盖映射。
**对外接口：**
- `load_catalog(project_root, agents_cfg: AgentConfig) -> Catalog`
- `Catalog.resolve(name) -> AgentDefinition | None`
- `Catalog.list() -> list[AgentDefinition]`（按 name 排序；供 Agent 工具 description 渲染）
- `Catalog.fork_definition() -> AgentDefinition`——返回 Fork 路径伪定义（`name="__fork__"`、`body=""`（继承父系统提示）、`background=True`、`permission_mode=DEFAULT`、`dont_ask=False`、`tools/disallowed_tools` 空）：定义式与 Fork 走同一 `resolve`/构造路径

**加载顺序（spec F2.2）：** ① 项目级 `<root>/.newcode/agents/*.md` → ② 用户级 `~/.newcode/agents/*.md` → ③ 内置 `newcode/subagent/builtin/*.md`（`importlib.resources.files`）→ ④ 插件级（本期跳过，SourcePlugin 占位）。
**合并规则：** 按 1→4 顺序扫描，后扫到的同名字典写入即被高优先级覆盖（高优先级层先写，低优先级只写入 key 尚未存在的）；verifier 在 `agents_cfg.enable_verifier=False` 时跳过内置 verifier.md（spec F2.5）。
**失败策略：** 内置级解析失败 → raise（代码 bug，N4 fail-fast）；用户/项目级单文件失败 → stderr 定位（文件+字段）并跳过，其余正常加载。

### subagent/config.py —— agents: 配置段

**职责：** 读取 `.newcode/config.yaml` 的 `agents:` 段（local > project > user 三层合并，局部优先），解析为 `AgentConfig`。
**对外接口：** `load_agent_config(project_root) -> AgentConfig`
**实现：** 复用 hook loader 的目录探测方式；YAML `agents:` 键缺失 → 全缺省（spec F11.1/F11.2）；`model_tiers` 某 tier 缺配置 → 解析为 `inherit` 并记 warning。

### subagent/fork.py —— Fork 消息装填

**职责：** 构造 Fork 子对话的初始消息（spec F3.2）。
**对外接口：** `build_forked_messages(parent_conv: ConversationManager, task: str) -> list[Message]`、`is_fork_context(msgs: list[Message]) -> bool`
**步骤（build_forked_messages）：**
1. 深拷贝 `parent_conv.get_context()` 全部消息（Message 为 dataclass，内部 `tool_calls`/`content` 需深拷贝）
2. 扫描末尾 assistant 的 tool_calls：无对应 tool 结果（下一消息不是该 tool_use_id 的 tool 消息）的 tool_use，就地追加 `role="tool"` 的 placeholder ToolResult（content="（继承上下文，未完成）"），保证消息配对合法（spec F3.2.2）
3. 末尾追加 `Message(role="user", content=FORK_BOILERPLATE + "\n" + task)`

**FORK_BOILERPLATE**（spec F3.4，`<fork_boilerplate>` 包裹）：不得再调用 agent 工具；不要对话/提问/请求确认；直接使用工具；严格限制在任务范围内；最终报告以 `Scope:` 开头、≤500 字。
**`is_fork_context`**：扫描历史全部 user 消息内容寻找 `<fork_boilerplate>`（B2 层 1 的兜底检测）。

### subagent/launcher.py —— 统一启动器

**职责：** 计算有效工具集 → 构造子 Registry → 构造子 Agent → 分派前台/后台/移交。四路径唯一入口。
**构造依赖（main.py 注入）：**
```python
SubAgentLauncher(
    provider,                 # 父 Provider（fork/inherit 复用）
    make_provider,            # (model: str) -> Provider（模型分层解析用）
    parent_permission,        # 父 PermissionChecker（共享规则层，A2）
    hooks,                    # Hook 引擎（共享，子 Agent 事件带 agent_id）
    catalog,                  # Catalog
    manager,                  # TaskManager
    cfg,                      # AgentConfig
    get_main_agent,           # () -> Agent（fork 取父历史/stable_prompt/registry）
)
```
**对外接口：**
- `build_sub_registry(role: AgentDefinition, is_background: bool) -> Registry`——调 `tools/filter.py` 的 `apply_agent_tool_filter` 得到可见名集合，用 `Registry.view(visible)` 构造子 Registry（共享 Tool 实例，保留系统工具）
- `resolve_model(tier: str) -> Provider`——`inherit/空` → 父 provider；`haiku/sonnet/opus` → `cfg.model_tiers.get(tier)`，缺配置 → warning 降级父 provider，有配置 → `make_provider(model_id)`
- `make_sub_agent(role, *, fork_history: list[Message] | None) -> Agent`：
  ```python
  Agent(
      provider=resolve_model(role.model),
      conversation=ConversationManager(role.max_turns or DEFAULT_MAX_TURNS,
                                       messages=fork_history or []),
      registry=build_sub_registry(role, is_background),
      stable_prompt=role.body if not role.is_fork() else parent_stable_prompt,
      env_segment=parent_env_segment,
      permission=parent_permission.for_subagent(role.permission_mode),
      dont_ask=role.dont_ask,          # Agent 层 ASK→ALLOW 短路
      is_interactive=False,            # 永不 HITL（B1）
      hooks=engine,                    # 共享；runtime=None（prompt 注入对子 Agent 暂缺省）
      max_turns=role.max_turns or DEFAULT_MAX_TURNS,
  )
  ```
- `launch_defined(role_name, prompt, *, name=None, background=False) -> LaunchResult`
- `launch_fork(prompt, *, name=None) -> LaunchResult`（`Catalog.fork_definition()` + 强制 background）
- `launch_hook_agent(agent_name, prompt) -> str | None`（hook 动作用，后台，失败返回 None 记日志）
- `LaunchResult = {task_id, status: "async_launched" | "completed", text}`

**前台/后台分派（agent 工具 execute 内）：**
```
is_bg = background or role.background or role.is_fork() or not cfg.enable_subagent_background(→强制前台/Fork 报错)
if is_bg:  manager.launch(...) → {task_id, status:"async_launched"}
else:      manager.launch_foreground(...) → 工具 await 该任务
           ├─ 完成 → 返回 {status:"completed", text}
           └─ 超时 async_timeout_s → manager.adopt_running → {task_id, "timed_out_to_background"}
           （实现注意：用 asyncio.wait 竞速而非 wait_for——超时不 cancel 子 Agent，
            adopt_running 只转移任务所有权，不杀重来（spec F7.3）；本期无 ESC 手动切换）
```

### subagent/manager.py —— 后台任务管理器

**职责：** 后台任务全生命周期：launch / 前台注册 / 移交 / 续派（同 id）/ 排队 / 保留 / 空闲清理 / 完成通知队列。
**对外接口：**
```python
class TaskManager:
    def launch(ctx, agent_builder, task_text, *, name=None) -> str          # 后台启动，返回 task_id
    def launch_foreground(ctx, agent_builder, task_text, *, name=None) -> ForegroundHandle
        # 前台启动：注册运行中任务；handle 供工具 await（超时移交用，无 ESC）
    def adopt_running(task_id) -> bool      # 前台→后台移交（不杀任务，F7.3）
    def get(task_id) -> BackgroundTask | None
    def get_by_name(name) -> BackgroundTask | None    # 最新同名
    def list() -> list[BackgroundTask]                # 按 start_time 升序
    def stop(task_id) -> bool               # 取消（set cancel_event / run_task.cancel）
    def continue_agent(task_id_or_name, message) -> str   # 续派，返回同 task_id（F7.10）
    def drain_done() -> list[str]           # 取完成 task_id（TUI 空闲时消费，F7.6）
    def clear_all() -> None                 # /clear /resume /session_new / 退出（F7.9）
    async def run() -> None                 # 常驻：空闲清理扫描（idle_cleanup_minutes）
```
**launch 内部**（spec F16）：`run_task = asyncio.create_task(self._drive(bt))`；`_drive` 把 `agent.run_to_completion(task_text, already_injected=is_fork, observer=count)` 包 `try/except BaseException`：正常 → completed；MaxTurnsReached → failed(带文本与原因)；其他异常 → failed；`cancel_event`/`run_task.cancel()` → cancelled。结束置 `end_time`、`idle_since`、push task_id 到 done 队列（`_done` 为 `asyncio.Queue(maxsize=32)`，满则 `put_nowait` 抛 `QueueFull` 时丢弃 + stderr 警告）。
**续派（同 id 复用）**（F7.7/F7.8/F7.10）：`continue_agent` 校验——目标存在且非 cancelled；`round < max_tasks_per_agent`；空闲 → 立即新轮；运行中 → 若 `len(queue) < max_queue_per_agent` 入队，否则报错。新轮**复用同 task_id**：`status` completed→running 重置、`round += 1`、`result` 覆盖为本轮文本、`total_usage` 累计。每轮结束 push 同 task_id 到 done 队列。
**清理**（F7.7）：`run()` 每 ~30s 扫描：`status=completed` 且 `now - idle_since > idle_cleanup_minutes` → 清理；`completed` 数量超 `max_idle_agents` → 清理最旧；到达 `max_tasks_per_agent` 的 Agent 清理更积极（无续派价值）；`clear_all` 全部清空。

### tools/filter.py —— 过滤纯函数

常量与 `apply_agent_tool_filter` 如上（核心数据结构节）。独立模块便于单测（N15）；skills 的 `Registry.filtered()` 保持不动。

### tools/agent_tool.py —— Agent 工具

**职责：** 主 Agent 的统一子 Agent 入口（spec F1）。`name="agent"`、`read_only=False`（子 Agent 可能做任何事）。
**构造：** `AgentTool(catalog, task_mgr, launcher, cfg, get_main_agent)`（parent 经 `get_main_agent` 惰性取用，main.py 用 `agent_ref` 闭包，与现有 `context_mgr` 的 `emit_event` 同模式）。
**`description`：** 启动期渲染 `catalog.list()` 的角色名列表（`可用 subagent_type: explore, plan, general-purpose, verifier, ...`），帮主 LLM 选择（会话内稳定，不伤 tools 缓存）。
**execute 流程：**
1. 解析 `AgentArgs(prompt, description, subagent_type, model, run_in_background, name)`；校验 `prompt` 非空
2. **防嵌套**（B2 层 1 兜底）：`is_fork_context(sub_conv 或主 conv)` 命中 → 返回 `is_error=True`「Fork 子 Agent 不能再启动 Agent」；正常情况下子 Agent 工具集已被过滤剔除 agent（F6.1/F6.3），此步是双保险
3. `subagent_type` 非空 → `catalog.resolve(name)`（不存在 → `ToolResult(error="未知 subagent_type: X")`）；空 → `catalog.fork_definition()`（`cfg.enable_subagent_background=False` 时 fork 直接 error「后台禁用，无法 Fork」）
4. 决定 background：`role.background or args.run_in_background or role.is_fork()`
5. `launcher.launch_defined / launch_fork` 分派（见 launcher 节）

### tools/task_tools.py —— Task 工具组

`TaskListTool`（read_only=True）：无参 → manager 列表摘要（id/name/status/tool_count/last_activity/round）。
`TaskGetTool`（read_only=True）：`{task_id}` → 完整状态（含 result/err/usage/起止/round）。
`TaskStopTool`（read_only=False）：`{task_id}` → `manager.stop`，返回 `{status:"cancellation_requested"}`。
`SendMessageTool`（read_only=False）：`{task_id | name, message}` → `manager.continue_agent`，返回 `{status:"accepted", task_id}`（同 id）或结构化错误（不存在/已达上限/排队满）。

### agent/agent.py 改造

**改动点：**
1. `__init__` 增加 `max_turns: int = 10`、`dont_ask: bool = False`（`_MAX_AGENT_TURNS` 改为实例字段，主 Agent 不变）
2. 抽出 `_react(mode, plan_content, *, inject: bool)` 私有 async generator——ReAct 循环主体（**不含** `add_user`）；`run()` = `add_user`（execute 模式注入 EXECUTE_DIRECTIVE）+ `_react(inject=True)`（行为不变）
3. 新增 `async run_to_completion(task: str, *, already_injected=False, observer=None) -> str`：
   - `already_injected=False`（定义式）→ `self.conv.add_user(task)`；True（Fork，任务已在 fork 消息里）→ 跳过
   - 驱动 `_react("normal", "", inject=False)`，逐事件：TEXT 累积、TOKEN_USAGE/TOOL_CALL 计数（经 observer 回调）、DONE
   - `NATURAL` → 返回累积文本；`MAX_TURNS` → 抛 `MaxTurnsReached(text, usage, tool_count)`；`CANCELLED` → 按取消语义（manager 转 cancelled）；`STREAM_ERROR`/`CONSECUTIVE_UNKNOWN_TOOLS` → 抛对应错误
   - 与 `run` 共用同一段循环（N「不重复实现」）；不触发 memory update / compact reminder 等主对话专属逻辑
4. **dont_ask 短路**（`_run_guarded` / 权限分支处）：`if self.dont_ask and decision == ASK: decision = ALLOW`——规则（黑名单/沙箱/deny 规则）仍拦，只放行规则未命中的（spec F5.3）；子 Agent `is_interactive=False` 且**无 approval_upgrader**（B1：永不弹主 TUI 审批框）

### permission/checker.py 改造

- **不加 DONT_ASK 枚举成员**（避免污染主 Agent 共享的 PermissionMode——TUI Shift+Tab、CLI --mode 都要处理排除）；`dontAsk` 语义由 Agent 层 `dont_ask` 标志短路实现（见上）
- 增加 `for_subagent(mode) -> PermissionChecker`：复用父实例的 `_layers`（共享规则层，A2——父 persist_local_allow 的精确规则子 Agent 同样命中），mode 换为子角色模式

### hooks 接通（hook agent 动作）

- `hooks/executor.py` `_run_agent(aa, hook)`：占位日志改为——`prompt = render_template(aa.prompt, payload)`；调注入的 `launch_hook_agent(aa.agent_name, prompt)`；`agent_name` 未知 / 失败 → `ExecutionResult(err=...)`（引擎按 F9.1 记 stderr，不中断）；成功 → `ExecutionResult()`（不 blocked 不 err，F9.4）
- `hooks/engine.py` 增加 `set_agent_launcher(callable)`，Executor 持引用（main.py 注入 launcher；未注入时保持原占位日志，向后兼容）

### tui/app.py 集成

- REPL 构造增加 `task_manager`；`CommandContext` 增加 `task_manager` 字段
- **完成通知注入**（F7.6）：`_consume_agent_events` 结束后、`run()` 主循环每次回到提示符前，`drain_done()` 取完成 task_id → 组 `<task-notification>` user 消息 → `agent.conv.add_user(xml)` + `console.print`（流式中不消费，空闲才注入，N5）
- **ESC 手动切换本期不做**（B 决策，对齐参考）：`foreground_sub_agent` 跟踪字段在 AgentTool/REPL 预留供后续章节；Ctrl+C 保持现有取消逻辑不变
- `/clear` / `/resume` / `/session_new` / 退出路径调用 `manager.clear_all()`

### slash/commands/tasks.py —— /tasks 命令族

- `build() -> list[CommandDef]`：`CommandDef(name="tasks", kind=LOCAL, usage="[show|kill|send] ...", handler=_handler)`；handler 按子命令分发：`/tasks`（列表）`/tasks show <id>` `/tasks kill <id>` `/tasks send <id|name> <message>`（F8.3）；无任务 → `No background tasks.`；续派走 `manager.continue_agent`（同 id）
- 注册进 `slash/commands/__init__.py` 的 `register_all`

### skills/executor.py 改造（F10）

- `_execute_fork`：不再自行 `_make_fork_agent` + `_run_fork_agent`，改为构造临时 `AgentDefinition(name=f"skill-fork-{skill.name}", disallowed_tools=skill.allowed_tools 反推或等同, ...)` 走 `launcher.launch_fork`；返回的最终文本行为不变（`ui.append_assistant_message` + token 写回由 Executor 保留）
- `_make_fork_agent` / `_run_fork_agent` 中与 SubAgent 底座重叠的部分删除；`fork_context`（none/recent/full）的摘要逻辑保留（skill 独有语义，不走 build_forked_messages 的逐字节继承）

### main.py 装配

```
（现有装配不变，以下按序新增）
1. agents_cfg = load_agent_config(project_root)          # subagent/config
2. catalog = load_catalog(project_root, agents_cfg)      # subagent/catalog
3. task_manager = TaskManager(cfg=agents_cfg)            # subagent/manager
4. launcher = SubAgentLauncher(provider, make_provider=..., parent_permission=permission,
                               hooks=hook_engine, catalog=catalog, manager=task_manager,
                               cfg=agents_cfg, get_main_agent=lambda: agent_ref[0])
5. registry.register(AgentTool(catalog, task_manager, launcher, cfg,
                               get_main_agent=lambda: agent_ref[0]))
   registry.register(TaskListTool/GetTool/StopTool/SendMessageTool(task_manager))
6. hook_engine.set_agent_launcher(launcher.launch_hook_agent)
7. agent_ref[0] = Agent(...)（构造顺序：tools 已注册 → 主 Agent 可见；get_main_agent 惰性取用）
8. REPL(..., task_manager=task_manager) + register_all(/tasks) + CommandContext.task_manager
9. task_manager.run() 常驻任务 + 退出 finally 时 task_manager.clear_all()
```

## 模块交互（调用链 / 数据流）

### 调用链 1：定义式前台工具调用
```
TUI._consume_agent_events
  → main_agent.run("...")
    → LLM 返回 tool_call(agent, {subagent_type:"explore", prompt})
    → 权限/Hook 检查放行 → ToolScheduler
    → AgentTool.execute
        → catalog.resolve("explore") → AgentDefinition
        → launcher.build_sub_registry(role, is_background=False) → 子 Registry（不含 agent）
        → manager.launch_foreground(...) → ForegroundHandle
        → 工具 await handle.run_task（子 Agent run_to_completion 复用主循环）
        → 子 Agent 内部：权限(共享规则层+explore 的 default 模式) → 工具(只读放行)
        → 自然终止 → RunOutcome.text
    → 返回 ToolResult(output=final_text) → 主 Agent 收到继续回复
```

### 调用链 2：前台超时移交后台
```
AgentTool.execute 前台分支
  → asyncio.wait({run_task, timeout(async_timeout_s)})   # 非 wait_for，超时不 cancel
  ├─ 完成 → 返回 final_text
  └─ 超时 → manager.adopt_running(task_id)   # 任务所有权转后台，不杀
           返回 {task_id, status:"timed_out_to_background"}
  → 主 ReAct 循环继续（工具结果含 task_id）
  → 子 Agent 后台跑完 → _drive 置 completed → done 队列 push task_id
```

### 调用链 3：完成通知注入
```
task_manager._drive 结束 → done_queue.put(task_id)
TUI 主循环空闲点 → drain_done() → [task_id]
  → 组 <task-notification> XML → agent.conv.add_user(xml) → console.print
  → 下一轮 run 的上下文天然包含该通知（user 角色，XML 标签）
```

### 调用链 4：续派（同 id 复用）
```
主 Agent 调 SendMessage({name:"worker-1", message})  （或用户 /tasks send）
  → TaskManager.continue_agent("worker-1", message)
      → get_by_name → round<10？ 空闲？运行中→入队(≤2)？
      → 新轮：同 task_id 复用，status completed→running，round+1，
        conv 追加 user(message) → run_to_completion → 完成 → done 队列
  → 同 task_id 的 <task-notification> 注入主对话
```

### 数据流：权限决策（子 Agent）
```
AgentTool.execute → launcher.make_sub_agent → permission=parent_permission.for_subagent(mode), dont_ask=role.dont_ask
子 Agent 工具调用 → PermissionChecker.check（is_interactive=False）
  ① 黑名单（COMMAND+非空）→ DENY
  ② 沙箱（文件类）→ DENY
  ③ 规则引擎（共享 local/project/user 层——父 persist_local_allow 的精确规则命中 → Allow，A2）
  ④ 模式兜底：default → ASK→DENY（非交互）；acceptEdits → 写 Allow / 命令 DENY
  ⑤ Agent 层 dont_ask 短路：decision==ASK → ALLOW（规则/黑名单/沙箱仍拦）
  （永不进入 HITL / 无 approval_upgrader，spec F5.3/B1）
```

## 文件组织

```
newcode/
├── subagent/                      ★ 新包（统一底座）
│   ├── __init__.py                # 导出 AgentDefinition/Source/Catalog/TaskManager/...
│   ├── types.py                   # AgentDefinition / Source / DefinitionParseError / 文案常量
│   ├── config.py                  # AgentConfig + load_agent_config（agents: 段，local>project>user）
│   ├── parser.py                  # parse_definition（复用 skills frontmatter 分离）
│   ├── catalog.py                 # Catalog：4 层加载 + 优先级 + resolve/list + fork_definition
│   ├── builtin/                   # 内置角色（importlib.resources）
│   │   ├── general-purpose.md
│   │   ├── explore.md
│   │   ├── plan.md
│   │   └── verifier.md            # enabled: false
│   ├── fork.py                    # build_forked_messages + FORK_BOILERPLATE + is_fork_context
│   ├── launcher.py                # SubAgentLauncher：build_sub_registry / resolve_model / make_sub_agent / 前台后台分派
│   ├── manager.py                 # BackgroundTask + TaskManager（launch/adopt/continue(同id)/清理/done 队列）
│   └── errors.py                  # MaxTurnsReached（或放 agent/events）
├── tools/
│   ├── filter.py                  # ★ 新增：GLOBAL_DENY / ASYNC_AGENT_ALLOWED_TOOLS / apply_agent_tool_filter
│   ├── agent_tool.py              # ★ 新增：AgentTool（description 渲染角色列表 + execute 分派）
│   ├── task_tools.py              # ★ 新增：TaskListTool / TaskGetTool / TaskStopTool / SendMessageTool
│   └── ...（既有工具不动）
├── agent/
│   ├── agent.py                   # 修改：max_turns/dont_ask 参数、_react 抽取、run_to_completion、dont_ask 短路
│   └── events.py                  # 修改（如需）：MaxTurnsReached 或复用 RunOutcome
├── permission/
│   └── checker.py                 # 修改：for_subagent()（共享规则层；不加 DONT_ASK 枚举）
├── hooks/
│   ├── executor.py                # 修改：_run_agent 真实实现（launcher 回调）
│   └── engine.py                  # 修改：set_agent_launcher
├── skills/
│   └── executor.py                # 修改：_execute_fork 走 launcher（F10）
├── slash/
│   ├── context.py                 # 修改：task_manager 字段
│   └── commands/
│       ├── tasks.py               # ★ 新建：/tasks 命令族
│       └── __init__.py            # 修改：register_all 注册 /tasks
├── tui/
│   └── app.py                     # 修改：done 队列消费、clear_all、foreground_sub_agent 预留、/tasks 接线
├── main.py                        # 修改：装配（config/catalog/manager/launcher/tools/hooks/tui）
├── __init__.py                    # 修改：__version__ = "0.13.0"
├── pyproject.toml                 # 修改：version = "0.13.0"
tests/
├── test_ch13_types.py             # 常量/数据结构/MaxTurnsReached
├── test_ch13_parser.py            # 定义解析（含 frontmatter 非法降级、dontAsk→dont_ask）
├── test_ch13_catalog.py           # 4 层加载/优先级/fork_definition/verifier 开关/fail-fast vs skip
├── test_ch13_fork.py              # build_forked_messages 深拷贝/placeholder 补全/Boilerplate 注入/is_fork_context
├── test_ch13_filter.py            # apply_agent_tool_filter 多层过滤/后台交集/mcp__*/系统豁免
├── test_ch13_launcher.py          # build_sub_registry/模型分层/前台后台分派/超时移交不杀重来
├── test_ch13_manager.py           # launch/状态机/续派同id/排队/上限/空闲清理/clear_all
├── test_ch13_tools.py             # 5 工具参数/错误路径/结果形态/description 角色渲染
├── test_ch13_agent.py             # run_to_completion 共用循环/observer/MaxTurnsReached/dont_ask 短路
├── test_ch13_permission.py        # for_subagent 共享规则层/dont_ask 放行/无 HITL
├── test_ch13_hooks.py             # hook agent 动作接通/失败隔离
├── test_ch13_tui.py               # done 队列注入（mock 无真实终端）
├── test_ch13_skills.py            # skill fork 走底座行为不变
├── test_ch13_config.py            # agents: 段解析/缺省/非法降级
└── test_ch13_integration.py       # 端到端：定义式/Fork/超时移交/续派同id/嵌套防护
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 子 Agent 循环复用 | `Agent` 抽 `_react`，`run` 与 `run_to_completion` 共用 | 满足 spec F5.2「不重复实现」；避免两套循环漂移 |
| 任务所有权 | 子 Agent run 始终是 Manager 名下 asyncio 任务；前台=工具 await，后台=立即返回，移交=adopt_running 不杀；状态/计数全程在 Manager | 天然满足「前台→后台移交不杀重来」（F7.3）；无需参考的 PartialState 中途接力 |
| dontAsk 建模 | **不加枚举成员**——`Definition.dont_ask` + `Agent.dont_ask` 独立布尔，frontmatter `permissionMode: dontAsk` → mode=DEFAULT + dont_ask=True；Agent 层 ASK→ALLOW 短路 | 避免污染主 Agent 共享的 PermissionMode 枚举（TUI/CLI 都要排除）；规则/黑名单/沙箱仍生效（F5.3） |
| 无 HITL 升级 | 子 Agent `is_interactive=False`、无 approval_upgrader | B1 决策：子 Agent 永不弹主 TUI 审批框、不等待用户输入；dontAsk 之外的模式 ASK→DENY |
| 共享规则层 | `for_subagent()` 复用父 `_layers`，父 persist_local_allow 精确规则子 Agent 命中 | 用户已批准过的不再重问（A2）；与 F4.2「权限规则层共享」一致 |
| 子 Registry 构造 | `tools/filter.py` 纯函数 `apply_agent_tool_filter` + `Registry.view(visible)`（保留系统工具） | 过滤可独立单测；子 Agent 用同一 Registry 实例跑 defs 与执行 |
| 过滤常量归属 | 独立 `tools/filter.py` 模块 | 参考同款；过滤是工具注册表关注点，独立成模块便于扩展与测试 |
| Fork 建模 | `Catalog.fork_definition()` 伪定义（name="__fork__"），定义式/Fork 走同一构造路径 | 统一 launcher 逻辑（参考同款）；Fork 强制 background |
| Fork 缓存命中 | fork 消息逐字节继承父历史 + 复用父 stable_prompt；Boilerplate 追加在末尾 user 消息 | messages 前缀缓存命中（N4）；tools 差异只损小缓存、不损大缓存 |
| Fork 嵌套阻断 | 后台白名单剔除 agent（Fork 天然不可见）+ `<fork_boilerplate>` 标记运行时检查兜底 | B2 决策：工具列表层主防 + 标记扫描兜底；定义式靠 GLOBAL_DENY |
| 模型分层 | `agents.model_tiers` 配置 → 实际模型串 → `make_provider`；缺配置降级 inherit | 适配非 anthropic provider（deepseek/openai），不硬编码模型名 |
| 前台→后台移交 | **本期仅超时自动**（B 决策，无 ESC 手动切换）：`asyncio.wait` 竞速（非 wait_for），超时不 cancel，`adopt_running` 只转移所有权 | 参考 task 证明 ESC 可后置；wait_for 超时会 cancel 内部协程=杀重来，故用 wait |
| 通知注入 | user 角色 `<task-notification>` XML 写历史 + 界面打印；TUI 空闲点 drain_done | 用户 Q1 决策：持久可引用 + 不打断（流式中排队，N5） |
| 续派语义 | **同 task_id 复用**——status completed→running、round+1、result 覆盖；`/tasks` 一个条目 | 用户确认：查询体验连贯（参考同款） |
| 完成通知缓冲 | done 队列 `maxsize=32`，QueueFull 丢弃 + stderr 警告 | 正常场景不可能满；漏一条通知不致命 |
| 循环依赖规避 | `subagent/launcher.py` → import agent（单向）；`agent/agent.py` 不反向 import subagent；`tools/agent_tool.py` → import subagent | agent 包已 import skills.adapter / tools.registry，subagent 只依赖 permission/agent；无环（参考 T31 的洞察已内建） |
| 保留/续派上限 | `max_tasks_per_agent=10` / `max_idle_agents=10` / `idle_cleanup=15min` / `max_queue=2`，全部配置化 | 防上下文过长与内存无界（N9）；spec F7.7/F7.8 |
| 后台总闸 | `enable_subagent_background=false` 时显式/超时后台全部失效，Fork 报错 | kill-switch；安全兜底 |
| Agent 工具 description | 启动期渲染 catalog 角色名列表 | 帮主 LLM 选 `subagent_type`；会话内稳定，不伤 tools 缓存 |
| hook agent 动作 | Executor 注入 `launch_hook_agent` 回调；未注入保持原占位日志 | 向后兼容；F9.1 错误隔离不变 |
| Skill fork 统一 | `_execute_fork` 走 launcher；fork_context 摘要逻辑保留 | F10 消灭双实现；保留 skill 独有语义 |
| 内置角色命名 | `general-purpose` / `explore` / `plan` / `verifier`（小写 kebab） | 对齐参考与 Claude Code 命名；与 subagent_type 归一化规则一致 |
| 内置角色 fail-fast vs skip | 内置级解析失败 raise（代码 bug）；用户/项目级 skip + stderr | N4 语义：内置错误是我们写坏的，用户错误不阻断 |

## 风险与边界

- **ESC 手动切换本期不做**（B 决策，对齐参考）：前台→后台仅由超时自动触发，`foreground_sub_agent` 跟踪字段预留。剩余风险点 = 前台超时移交的正确性（`asyncio.wait` 不 cancel、不杀重来）与 done 队列注入时序，task.md 用 mock 先测
- **子 Agent 的 hook prompt 注入暂缺省**（子 Agent 无 SessionRuntime，`runtime=None` 时 injected_prompts 丢弃）——pre_tool_use 拦截等阻塞/通知类 Hook 仍生效；若后续需要「hook 向子 Agent 注入 reminder」，加一个子 Agent 专用 mini-runtime 即可（本期不做）
- **`<task-notification>` 注入历史会增长主上下文**：靠 800 字截断 + 保留/续派上限控制（N6/N9）；未来若需历史瘦身可把通知改为仅 reminder 注入（本期按用户 Q1 决策走历史）
- **同 id 续派覆盖 result**：每轮结束 result 覆盖为本轮文本，历史轮次结果不保留在任务记录里——需要历史时靠主对话的 `<task-notification>`（每轮一条，各自带同 id 不同时间戳）
