# MewCode ch12 - Hook 生命周期挂钩系统 Plan

## 技术栈

- 语言：Python 3.10+（项目 requires-python，非模板的 3.12）
- TUI：prompt_toolkit + Rich（MewCode 实际底座，**不是** Textual——模板 TUI 结构仅思想借鉴）
- 配置：YAML（`yaml.safe_load`）
- HTTP 客户端：`httpx`（原生 async，已随 mcp 传递依赖存在 0.28.1）
- 异步进程：`asyncio.create_subprocess_shell` + `asyncio.wait_for` 超时
- 模板：标准库 `str.format_map`（不开放函数调用；裸 `{}` 容错）
- 测试：`pytest` + `pytest-asyncio`、`tmp_path`、`pytest-httpserver`（http 桩）

## 架构概览

Hook 系统由两个层次构成，分层清晰、单向依赖：

1. **权限匹配器升级层（`mewcode/permission/` 包内改造）**——把 Pattern 形态从字符串升级到结构化 `Matcher` Protocol：新增 exact / not / regex 三种实现，glob 保留作为缺省类型。对外仅暴露语法升级和 stderr 错误回退，运行时 Allow/Deny 语义不变（spec F1，前置基础）。
2. **Hook 主体层（新建 `mewcode/hooks/` 包）**——加载 YAML 规则、事件分派引擎、四类动作执行器；通过 18 个事件 emit 点接入 agent / tui / main。

核心设计：**Hook 引擎是"被注入的观察者"。** Agent / REPL / main 在事件节点调用 `Engine.dispatch()`，引擎同步（或后台）执行匹配的 Hook，返回拦截判定与待注入 prompt；**Hook 永远不反向感知调用方**，错误一律内部消化（stderr 日志），保证错误隔离与无侵入（N1/N10）。

```
┌──────────────────────────────────────────────────────┐
│           调用方（装配层）：main / REPL / slash / Agent │
└────────────────────────┬─────────────────────────────┘
                         │ 构造注入 / 事件分派
                         ▼
┌──────────────────────────────────────────────────────┐
│               mewcode/hooks/ 包（Hook 引擎）          │
│  loader.py    三层配置加载 + 合并 + 校验(fail-soft)   │
│  engine.py    Engine：统一 dispatch + once 集合       │
│  conditions.py  eval_condition / get_by_path          │
│  executor.py  四类执行器 + {field} 模板渲染           │
│  types.py     Event/Action/Hook/Payload/常量          │
└────────────────────────┬─────────────────────────────┘
                         │ 复用（共享匹配器）
                         ▼
┌──────────────────────────────────────────────────────┐
│      mewcode/permission/matcher.py（前置基础 F1）      │
│   Matcher Protocol：Exact/Glob/Regex/Not 四实现       │
│   权限规则(rules.py) 与 Hook 条件共用                 │
└──────────────────────────────────────────────────────┘
```

## 核心数据结构与接口

### mewcode/permission/matcher.py —— 共享匹配器（前置基础 F1）

```python
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Protocol

class Matcher(Protocol):
    """规则匹配统一接口；四种实现：Exact / Glob / Regex / Not。"""
    def match(self, s: str) -> bool: ...
    def __str__(self) -> str: ...   # 调试 / /hooks 输出用

@dataclass(frozen=True)
class ExactMatcher:
    value: str
    def match(self, s: str) -> bool: return s == self.value
    def __str__(self) -> str: return f"={self.value}"

@dataclass(frozen=True)
class GlobMatcher:
    pattern: str
    is_command: bool          # True=整串通配(Bash)；False=match_path(/** 递归)
    def match(self, s: str) -> bool:
        if self.is_command:
            return match_command(self.pattern, s)   # fnmatch 整串
        return match_path(self.pattern, s)          # 现有 ** 递归逻辑
    def __str__(self) -> str: return self.pattern

@dataclass(frozen=True)
class RegexMatcher:
    src: str
    compiled: re.Pattern[str]   # 加载期编译并缓存
    def match(self, s: str) -> bool: return self.compiled.search(s) is not None
    def __str__(self) -> str: return f"~{self.src}"

@dataclass(frozen=True)
class NotMatcher:
    inner: Matcher             # 一元取反，支持嵌套
    def match(self, s: str) -> bool: return not self.inner.match(s)
    def __str__(self) -> str: return f"!{self.inner}"

def compile_matcher(pattern: str, *, is_command: bool = False) -> Matcher:
    """解析单条匹配描述串，失败抛 ValueError（F1.2/F1.3）。
      "=value"  -> ExactMatcher
      "~regex"  -> RegexMatcher（编译失败 -> ValueError）
      "!inner"  -> NotMatcher(compile_matcher(inner))  # 支持 !=value / !~re / !glob
      "value"   -> GlobMatcher（缺省，向后兼容）"""

def matcher_from_spec(d: dict) -> Matcher:
    """Hook 条件 YAML -> Matcher（F4.4）：
      {type: exact|glob|regex, value} / {type: not, inner: {...}}
    glob 复用现有 match_pattern 语义（无 / -> fnmatch 整串通配、有 / 或 ** ->
    路径分段递归），与权限规则一致（spec 场景 2 的 `rm -rf *` 匹配命令串依赖此行为）；
    不合法抛 ValueError。"""

def evaluate(spec: Matcher, target: str) -> bool:
    """等价 spec.match(target)，保持调用点语义统一。"""
```

技术说明：`match_command`（fnmatch 整串）与 `match_path`（`**` 递归，现 `_match_parts`）当前在 `permission/rules.py`。为避免 matcher 反向依赖 rules，把 glob 求值逻辑移入 `matcher.py`；`rules.py` 从 matcher import 并保留 `match_pattern` 名称 re-export（兼容已有调用）。

### mewcode/permission/rules.py（修改）

`Rule` 由 `pattern: str` 改为 `matcher: Matcher | None`（None = 该工具全匹配，等价 `Bash(*)`）+ 保留 `raw: str` 原文供错误日志。`Rule.parse` 在正则提取 `(tool_name, pattern)` 后调 `compile_matcher(pattern, is_command=(tool_name=="Bash"))`；`Rule.match_target` 改用 `evaluate(matcher, target)`。`build_rule_set` 解析失败由静默跳过改为 stderr 打印 `rule "<raw>" parse failed: <原因>` 并跳过（F1.4）。向后兼容（F1.5）：`Bash(git *)` 无前缀 → GlobMatcher → 行为与现状一致，现有 ch08 测试不改。

### mewcode/hooks/types.py —— Hook 规则与上下文

```python
class Event(str, enum.Enum):
    """18 个事件（spec F3.1），snake_case，YAML 字面量直接对应"""
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SESSION_RESUME = "session_resume"
    USER_PROMPT_SUBMIT = "user_prompt_submit"     # 拦截
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    PRE_TOOL_USE = "pre_tool_use"                 # 拦截
    POST_TOOL_USE = "post_tool_use"
    PRE_SEND = "pre_send"
    POST_RECEIVE = "post_receive"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    ERROR = "error"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    PERMISSION_REQUEST = "permission_request"
    FILE_CHANGE = "file_change"
    COMMAND_EXECUTE = "command_execute"

BLOCKING_EVENTS: frozenset[Event] = frozenset(
    {Event.PRE_TOOL_USE, Event.USER_PROMPT_SUBMIT}
)

def is_blocking(e: Event) -> bool: return e in BLOCKING_EVENTS

class CombineMode(str, enum.Enum):
    ALL_OF = "all_of"; ANY_OF = "any_of"

class ActionType(str, enum.Enum):
    COMMAND = "command"; PROMPT = "prompt"; HTTP = "http"; AGENT = "agent"

@dataclass
class AtomCondition:
    field: str          # 点分路径，如 tool_input.path（F4.3，缺失 -> ""）
    matcher: Matcher    # 复用 permission.Matcher（F4.4）

@dataclass
class Condition:
    mode: CombineMode   # all_of / any_of 二选一，不混用（F4.1）
    atoms: list[AtomCondition]

@dataclass
class ShellAction:
    command: str                        # command 必填

@dataclass
class PromptAction:
    text: str                           # prompt 必填

@dataclass
class HttpAction:
    url: str                            # http 必填
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None             # 模板串；None=payload JSON

@dataclass
class AgentAction:
    agent_name: str                     # agent 必填
    prompt: str                         # agent 必填

@dataclass
class Action:
    type: ActionType
    shell: ShellAction | None = None
    prompt: PromptAction | None = None
    http: HttpAction | None = None
    agent: AgentAction | None = None

@dataclass
class Hook:
    name: str                           # 必填；日志/once/冲突检测
    event: Event                        # 必填
    action: Action                      # 必填
    condition: Condition | None = None  # if；None=无条件
    once: bool = False
    asyncio_mode: bool = False          # YAML 写 async；内部避关键字（Loader 映射）
    timeout_s: float = 30.0             # command/http 用
    source: str = ""                    # 来源文件路径，/hooks 显示用

# Payload：事件分派携带的上下文数据，条件求值与动作输入都用它。
# 序列化 JSON 用 json.dumps(payload, sort_keys=True)（N5 稳定顺序）。
Payload = dict[str, Any]

@dataclass
class DispatchResult:
    blocked: bool = False
    reason: str = ""
    blocking_hook_name: str = ""
    injected_prompts: list[str] = field(default_factory=list)

@dataclass
class ExecutionResult:
    blocked: bool = False
    reason: str = ""
    prompt: str = ""                    # 仅 prompt 动作非空
    err: Exception | None = None        # hook 自身失败（不拦截）
```

### mewcode/hooks/conditions.py

```python
def eval_condition(cond: Condition | None, payload: Payload) -> bool:
    """cond=None -> True；否则 atoms 逐一 evaluate(atom.matcher,
    get_by_path(payload, atom.field))，按 mode 做 all/any 组合（F4.6）。"""

def get_by_path(payload: Payload, path: str) -> str:
    """点分路径取值（F4.3）：tool_input.path 遍历嵌套 dict；
    路径不存在 -> ""；值非 str 时 bool 转小写（"true"/"false"，与 YAML 直觉及
    spec 场景 1 的 is_error: false 一致）、int/float -> str()、嵌套 dict/list ->
    json.dumps(sort_keys=True)。"""
```

### mewcode/hooks/engine.py

```python
class Engine:
    def __init__(self, rules: list[Hook], sources: list[str]) -> None:
        self._rules = rules            # 按加载顺序（优先级高者在前）
        self._sources = sources        # 来源文件列表，/hooks 用
        self._once_fired: set[str] = set()
        self._lock = asyncio.Lock()
        self._executor = Executor()

    async def dispatch(self, event: Event, payload: Payload) -> DispatchResult:
        """统一分派接口（blocking 由 is_blocking(event) 内部判定）：
        1) 过滤匹配 event 的 hook（按声明顺序）
        2) once 过滤（_once_fired 命中跳过）
        3) 串行求值条件
        4) asyncio_mode -> create_task 后台执行，不等结果（不参与 block/inject）；
           once 命中即标记（任务已提交，视为已执行）
        5) 同步执行：Executor.run(rule, payload, blocking=is_blocking(event))
           - err -> stderr `[hook <name>] <event> failed: <err>`，continue（F9.1）
           - prompt -> result.injected_prompts.append
           - blocked 且 is_blocking -> 设 blocked/reason/blocking_hook_name，break（F7.3）
        6) 同步执行成功后 add(_once_fired)"""

    async def reset_for_new_session(self) -> None:
        """清空 once 集合（/clear、/resume、/session_new 调，F2.2/N8）"""

    def set_context_providers(
        self, get_session_id: Callable[[], str], get_mode: Callable[[], str]
    ) -> None:
        """main.py 装配后注入（loader 构造 Engine 时为空 provider）；
        dispatch 时用它们补通用字段 session_id / mode（hooks 不反向依赖 session/permission）"""

    @property
    def sources(self) -> list[str]: return list(self._sources)
    @property
    def rules(self) -> list[Hook]: return list(self._rules)

    async def close(self) -> None:
        """shutdown 收尾：记录未完成后台任务，不强制等待（F9.5）"""
```

### mewcode/hooks/executor.py

```python
def render_template(text: str, payload: Payload) -> str:
    """{field} 点分路径替换（http body F5.9 + 全动作通用）。
    映射表（与原始 $VAR 语义一致）：{event}≡$EVENT、{tool_name}≡$TOOL_NAME、
    {file_path}≡$FILE_PATH、{message}≡$MESSAGE、{error}≡$ERROR、
    {tool_input.xxx}≡$TOOL_ARGS.xxx。
    容错：format_map 抛 KeyError/IndexError/ValueError（裸 {} 等）时返回原文，
    未知字段 -> ""，绝不抛给调用方。"""

class Executor:
    def __init__(self) -> None:
        self._http_client = httpx.AsyncClient(timeout=30.0)

    async def run(self, hook: Hook, payload: Payload, *, blocking: bool) -> ExecutionResult:
        # 按 action.type 分发到 _run_shell / _run_prompt / _run_http / _run_agent

    async def _run_shell(self, sa, payload, blocking, timeout_s) -> ExecutionResult:
        # command = render_template(sa.command, payload)（可选内嵌，主通道仍 stdin JSON）
        # asyncio.create_subprocess_shell(command, stdin/stdout/stderr=PIPE)
        # payload_json = json.dumps(payload, sort_keys=True).encode() 写 stdin
        # asyncio.wait_for(proc.communicate(...), timeout) 超时 kill 子进程 -> err
        # blocking and rc==2 -> blocked=True，reason=(stderr or stdout).decode().rstrip("\n")
        # rc==0 -> 放行；其它非零 -> err=RuntimeError(f"exit {rc}: {stderr}")（F5.4/F9.1）

    def _run_prompt(self, pa, payload) -> ExecutionResult:
        # ExecutionResult(prompt=render_template(pa.text, payload))，永不 blocked（F5.8）

    async def _run_http(self, ha, payload, blocking, timeout_s) -> ExecutionResult:
        # body = render_template(ha.body, payload) if ha.body else json.dumps(payload, sort_keys=True)
        # httpx request(method, url, content=body, headers, timeout)
        # 2xx 且 json {"decision":"block","reason"} -> blocked（F5.11）
        # 其它 -> 放行；网络/超时/JSON 解析错 -> err（不拦截）

    def _run_agent(self, aa, hook) -> ExecutionResult:
        # stderr 固定格式 `[hook <name>] agent not yet implemented, skipped`（N9）
        # 不 blocked 不 err
```

### mewcode/hooks/loader.py

```python
# 文件位置（F6.1，三层）：本地 > 项目 > 用户
HOOK_FILE_LOCAL = ".mewcode/config.local.yaml"     # 与权限 permissions.local.yaml 命名对齐
HOOK_FILE_PROJECT = ".mewcode/config.yaml"
HOOK_FILE_USER = os.path.expanduser("~/.mewcode/config.yaml")

def load(project_root: str | Path) -> Engine:
    """本地 -> 项目 -> 用户 依次加载（F6.2 追加合并，优先级高者在前）。
    返回 Engine（内部含来源文件列表）；所有错误走 stderr 不抛异常（N1/N2）。
    逐条校验（F6.5/F6.6）：name 必填+跨文件冲突、event 枚举、action.type 枚举与
    子字段必填、if 顶层 all_of/any_of 互斥、matcher 编译失败、async+拦截事件冲突、
    timeout 格式合法 —— 任一失败 stderr 定位
    `hook "<name>" (in <file>): <原因>, skipped` 并跳过，其余正常加载（N3）。"""
```

### mewcode/session/runtime.py（修改）

`SessionRuntime` 增加：
- `pending_reminders: list[str]` + `_reminders_lock`（threading.Lock）
- `append_reminders(prompts)` / `take_reminders()`：加锁追加 / 取出并清空
- `hook_engine: Engine | None = None`（TYPE_CHECKING 标注；TUI 装配时设置）
- `reset_for_new_session()`：**集中重置点**——清空 pending_reminders + 调 `hook_engine.reset_for_new_session()`（调用方只调这一个）
- `create_new` / `resume` 同样清空（与 ActiveSkills 同生命周期，N8）

模板式决策：**reminder 注入放 SessionRuntime 而非 Engine**——与现有 plan reminder 同一注入点、reset 一并清空、不污染 Engine；`/clear`、`/resume`、`/session_new` 的 once 重置也走 runtime 这一个入口。

## 模块设计

### 模块 A：permission.matcher（新建，前置基础）
**职责：** 四种匹配类型的统一接口（F1）。**对外接口：** `Matcher` Protocol、`compile_matcher`、`matcher_from_spec`、`evaluate`、四个实现类。**依赖：** 标准库 `re`、`fnmatch`。**改动文件：** `permission/rules.py`（Rule 持有 matcher、build_rule_set stderr）。

### 模块 B：hooks.types
**职责：** Event/Action/Hook/Payload/DispatchResult/ExecutionResult 数据结构 + 常量。**对外接口：** 各 dataclass + `is_blocking`。**依赖：** `permission.matcher`、`enum`。

### 模块 C：hooks.conditions
**职责：** 条件解析与求值（F4）。**对外接口：** `eval_condition`、`get_by_path`。**依赖：** 模块 A/B。

### 模块 D：hooks.loader
**职责：** 三层 YAML 加载、合并、逐条校验（fail-soft）（F6）。**对外接口：** `load(project_root) -> Engine`、三个路径常量。**依赖：** A/B/C、`yaml`、Engine。

### 模块 E：hooks.executor
**职责：** 四类动作执行 + 模板渲染（F5）。**对外接口：** `Executor.run(hook, payload, *, blocking) -> ExecutionResult`、`render_template`。**依赖：** `asyncio`、`httpx`、`json`、`str.format_map`。

### 模块 F：hooks.engine
**职责：** 统一 dispatch 编排、once 集合、后台任务跟踪（F2.2/F7/F8.2/F9）。**对外接口：** `dispatch`、`reset_for_new_session`、`sources`、`rules`、`close`。**依赖：** B/C/E。

### 模块 G：agent 接入
**职责：** Agent 内部 **11 个**事件节点 emit（turn_start / turn_end / pre_tool_use / post_tool_use / pre_send / post_receive / error / pre_compact / post_compact / permission_request / file_change；TUI 层 5 个、main 层 2 个，见模块 H/J）+ reminder 注入 + 拦截整合（F8.1/F7.4）。**对外接口：** `Agent.__init__(..., hooks: Engine | None = None, runtime: SessionRuntime | None = None)`（runtime 用于 `take_reminders`/`append_reminders`）；私有 `_dispatch_hook(event, payload) -> DispatchResult`。**依赖：** 模块 F、`session/runtime.py`。**改动文件：** `agent/agent.py`、`session/runtime.py`（pending_reminders）。

### 模块 H：tui 接入
**职责：** user_prompt_submit 拦截（F7.5）、command_execute、会话生命周期事件（session_start/end/resume）+ reset_for_new_session（F2.2）。**对外接口：** REPL 私有方法。**依赖：** 模块 F。**改动文件：** `tui/app.py`、`slash/commands/clear.py`、`slash/commands/session.py`。

### 模块 I：/hooks 命令
**职责：** 输出已加载 hook 列表 + 来源（F10）。**对外接口：** `slash/commands/hooks.py`，注册进 `register_all`。**依赖：** `CommandContext.hooks`。

### 模块 J：cli wiring
**职责：** main.py 构造 Engine、load、注入 agent 与 CommandContext；startup/shutdown/session_start/session_end 事件。**改动文件：** `main.py`、`slash/context.py`（hooks 字段）。

## 模块交互

### 数据流

```
YAML(hooks.yaml×3) ──loader.load──▶ Engine(rules, sources)
                                        │（compile_matcher / matcher_from_spec / 校验）
  事件节点 ──dispatch(event,payload)──▶ 过滤匹配 → once 过滤 → 条件求值
                                    ├─ asyncio_mode → create_task（不等）
                                    ├─ command  → subprocess + stdin JSON + rc==2 拦截
                                    ├─ prompt   → injected_prompts
                                    ├─ http     → httpx + decision:block
                                    └─ agent    → 占位日志
                                    ▼
                    DispatchResult(blocked/reason/blocking_hook_name/injected_prompts)
                    ↓ 调用方把 injected_prompts append 到 runtime.pending_reminders
```

### 事件节点时序（Agent 内部）

```
agent.run(user_input)
  │ ① _dispatch_hook("turn_start", {prompt: user_input})   # 收集 prompt→runtime
  │ ② conv.add_user(user_input)
  └─ for turn in range(MAX):
       │ ③ manage_context → _dispatch_hook("pre_compact"/"post_compact", {trigger:auto})
       │ ④ _dispatch_hook("pre_send", {prompt, last_user_message})  # 发送前
       │ ⑤ reminders = [plan_reminder] + runtime.pending_reminders（join 并清空，F8.3）
       │ ⑥ assemble → provider.stream(payload)
       │ ⑦ _dispatch_hook("post_receive", {message})              # 接收后
       │    └─ for tc in known_calls:
       │         ├─ ⑧ _dispatch_hook("pre_tool_use", {tool_name, tool_input})  # 拦截
       │         │     blocked → ToolResult(error="[hook <name>] <reason>")
       │         │               yield TOOL_CALL+TOOL_RESULT, conv.add_tool_result, continue
       │         ├─ ⑨ permission.check（原有，hook 放行后才走，F7.6）
       │         │     ASK → _dispatch_hook("permission_request", {...}) + HITL
       │         │     DENY → 产 TOOL_RESULT(error) 后也 _dispatch_hook("post_tool_use",
       │         │            is_error=True)（spec F3.1：被权限 Deny 的也触发）
       │         └─ ⑩ 执行 → _dispatch_hook("post_tool_use", {tool_name, tool_input,
       │               tool_result, is_error})；write/edit 成功 → _dispatch_hook("file_change")
       └─ ⑪ 结束：NATURAL/MAX_TURNS → _dispatch_hook("turn_end", {iter})
            STREAM_ERROR → _dispatch_hook("error", {error}) + DONE(STREAM_ERROR)
            CANCELLED → 不触发 turn_end（F3.1）
```

### 拦截整合（F7.4/F7.5）

- **pre_tool_use 拦截**：`dispatch` 返回 `blocked=True` → 构造 `ToolResult(status="error", error=f"[hook {name}] {reason}")` → yield `TOOL_CALL`（用户仍看到工具被尝试）+ `TOOL_RESULT(error)` → `conv.add_tool_result` → 跳过权限检查与真实执行 → 进入下一 tc。**与现有权限 Deny 路径完全同构**，`TOOL_RESULT(status=error)` 即 PhaseEnd is_error=True 语义。
- **user_prompt_submit 拦截**：REPL `_process_input` 先 `dispatch(USER_PROMPT_SUBMIT, {prompt: text})` → blocked 则 `self._console.print(f"[hook {name}] {reason}", style="red")` 后 `return`（不启动 agent.run、消息不写历史）→ 焦点天然回输入框（F7.5）。

### 会话生命周期事件（TUI 侧）

- `/clear`：dispatch `session_end`（旧会话）→ `create_new()` → **`runtime.reset_for_new_session()`**（集中重置：清 pending + 调 engine 的 once 清空）→ dispatch `session_start`（新会话）
- `/resume`：dispatch `session_end`（旧会话）→ `runtime.resume()` → **`runtime.reset_for_new_session()`** → dispatch `session_resume`
- `/session_new`：同 /clear
- 进程启动：main.py `engine.load()` → dispatch `startup` →（TUI 首条输入前）dispatch `session_start`
- 进程退出：main.py finally dispatch `shutdown` → `engine.close()`

## 文件组织

```
mewcode/
├── permission/
│   ├── matcher.py            — 新建：Matcher Protocol + Exact/Glob/Regex/Not 四实现 +
│   │                           compile_matcher / matcher_from_spec / evaluate / match_path
│   └── rules.py              — 修改：Rule.matcher 字段、match_target 用 evaluate、
│                               build_rule_set 解析失败 stderr（F1.4）
├── hooks/                    — 新建包
│   ├── __init__.py           — 导出 Engine / Event / load / Hook / DispatchResult
│   ├── types.py              — Event(str Enum)/Action/Hook/Payload/常量
│   ├── conditions.py         — Condition/AtomCondition/eval_condition/get_by_path
│   ├── loader.py             — 三层加载合并校验（HOOK_FILE_*）
│   ├── engine.py             — Engine（统一 dispatch/once/tasks/close）
│   └── executor.py           — Executor 四动作 + render_template + ExecutionResult
├── session/runtime.py        — 修改：pending_reminders（create_new/resume 清空）
├── agent/agent.py            — 修改：hooks 注入 + 11 节点 _dispatch_hook + reminder join
├── main.py                   — 修改：engine 装配/load + startup/shutdown/session_start
├── slash/
│   ├── context.py            — 修改：hooks 字段
│   ├── commands/hooks.py     — 新建：/hooks 命令
│   ├── commands/clear.py     — 修改：session_end/start + reset_for_new_session
│   ├── commands/session.py   — 修改：session_end/resume + reset_for_new_session
│   └── commands/__init__.py  — 修改：register_all 注册 /hooks
├── tui/app.py                — 修改：user_prompt_submit 拦截 / command_execute /
│                               clear/resume/new 会话事件
tests/                                  # 扁平布局 + ch12 前缀（项目惯例，ch11 同）
├── test_ch12_matcher.py          — 新建：四种 type × 边界（空串/转义/嵌套not/空path）
├── test_ch12_conditions.py       — 新建：all_of/any_of/点分路径/缺字段
├── test_ch12_executor.py         — 新建：shell exit2/超时/http block/占位日志（pytest-httpserver）
├── test_ch12_loader.py           — 新建：三层合并/冲突/非法定位/async拦截冲突
├── test_ch12_engine.py           — 新建：once/顺序短路/async 后台
├── test_ch12_runtime.py          — 新建：pending_reminders 生命周期 + 集中重置
├── test_ch12_agent.py            — 新建：Agent 各节点接线（真实 Engine + 合成 rules）
├── test_ch12_tui.py              — 新建：user_prompt_submit 拦截/会话事件
└── test_ch12_integration.py      — 新建：端到端断言（E2E1-E2E4 自动化版本）
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 匹配器实现 | `Matcher` Protocol + 四个 frozen dataclass | 模板借鉴：OO、不可变、Regex 持编译态；新增类型只实现 match/__str__；`__str__` 供 /hooks 展示 |
| 匹配前缀语法 | `=` 精确、`!` 反向、`~` 正则、无前缀=glob | 单字符前缀让既有 `Bash(git *)` 继续 work（F1.5）；反向一元嵌套 `!=v`/`!~re`/`!glob` 全合法 |
| glob 区分命令/路径 | 权限侧 `GlobMatcher.is_command`（Bash=True 整串通配）；**hook 条件侧 glob 复用 match_pattern 自动判断**（无 `/` → fnmatch 整串、有 `/` → 路径递归） | 模板借鉴 is_command 显式化仅用于权限侧；hook 侧自动判断与 spec F4.4「复用权限规则 glob/`**` 语义」一致，且 `rm -rf *` 匹配命令串（spec 场景 2）不失效 |
| Event 用 str 枚举 | `class Event(str, Enum)` | 模板借鉴：YAML 字面量与枚举值直接对应、`Event("session_start")` 反查、日志可读、类型安全 |
| Action 嵌套子结构 | `Action{type, shell?, prompt?, http?, agent?}` 各自 dataclass | 模板借鉴：字段隔离、按 type 校验必填字段直接 |
| 分派接口 | 统一 `dispatch(event, payload)`，内部 `is_blocking` 判定 | 模板借鉴：单一接口，blocking 语义由事件决定，避免双接口漂移 |
| 失败表示 | `ExecutionResult.err: Exception | None` | 模板借鉴：携带失败原因，日志 `[hook <name>] <event> failed: <err>` 具体 |
| 拦截信号 | 动作结果（shell exit 2 / http decision:block），无 reject 字段 | 已确认（用户决策 1） |
| 模板语法 | `{field}` 点分替换（render_template）统一，映射原始 $VAR 语义 | 单一语法；裸 `{}`/未知字段容错返回原文，绝不抛给调用方 |
| command 取数 | **主通道** payload JSON(sort_keys) 经 stdin（jq 取）；`{field}` 内嵌为可选增强 | 模板单通道为本体（命令含 `{}` 时 format_map 有风险），保留 `{field}` 满足原始需求但做容错 |
| prompt 注入 | 独立 `hook_notification()` 构造 `<hook-notification>` 标签 Message | 用户确认的注入形式；与 plan reminder 的 `<system-reminder>` 区分 |
| reminder 存放 | `SessionRuntime.pending_reminders`（create_new/resume 清空） | 模板借鉴：与 plan reminder 同注入点、会话生命周期内重置、不污染 Engine |
| http 客户端 | httpx（mcp 传递依赖，0.28.1） | 不新增依赖；AsyncClient 复用连接池 |
| http 默认 | POST + JSON body；`str.format_map` 不开放函数 | 事件通知语义 POST 更合理；`{field}` 覆盖插值，避免模板注入 |
| shell 用 sh -c | `asyncio.create_subprocess_shell` | 用户常写 `\|`、`>` 等 shell 语法，直接交 sh 解释 |
| 同步挂死防护 | 单条 hook timeout + CancelledError 传播（N4） | 模板 N2/N3：不设全局上限，单条 timeout 累加 |
| 拦截整合 | 复用权限 Deny 路径（TOOL_CALL+TOOL_RESULT(error)） | 与现有 HITL/Deny 代码同构，TUI 零新增事件类型 |
| turn_end 触发 | 仅 NATURAL / MAX_TURNS | spec F3.1「取消、出错路径不触发」；模板 STOP 语义支撑 |
| once 集合 | Engine 持有 `_once_fired: set[str]` + `reset_for_new_session` | 模板借鉴：重置方法放 Engine 上，REPL clear/resume/new 调用 |
| 字段名 `asyncio_mode` | YAML 写 `async`，Loader 映射到 `Rule.asyncio_mode` | 模板借鉴：避免与 Python 关键字冲突，dataclass 字段名合法 |
| 本地配置路径 | `.mewcode/config.local.yaml`（对齐权限 `permissions.local.yaml`） | 修正 spec F6.1 原文 `mewcode/config.local.yaml`（源码包目录语义混乱）；待用户确认后同步 spec |
| 空引擎开销 | `hooks=None` 时所有调用短路（`if self._hooks is not None`） | N10 无侵入，与 active_skills 同模式 |
| 事件 payload mode | main.py 注入 `get_mode`（读 permission.mode.value） | hooks 不反向 import permission/session |
| 测试框架 | pytest-asyncio / tmp_path / pytest-httpserver | 模板借鉴：http 桩、临时目录、async 测试 |
