# MewCode Agent Loop — 技术设计 (plan.md)

## 架构概览

ch04 不新增包，在 ch03 的 `agent / provider / tools / conversation / prompt / tui` 六个包之上**扩展**，依赖方向不变、无环：

```
tool → provider
conversation → provider
agent → {provider, tools, conversation}
tui → {agent, tools, conversation, provider, prompt}
provider → {config, prompt}
```

各包变更：

- **`mewcode.agent`（重写 run）**：把 ch03 的「请求#1 → 执行 → 请求#2 → 停」改为真正的 ReAct 循环——`while` 迭代直到自然完成 / 上限 / 取消 / 连续未知工具 / 出错。新增保序分批并发执行、迭代进度与用量事件、终止时的历史一致性收尾、Plan/Normal 两种模式。
- **`mewcode.provider`（扩展）**：`StreamEvent` 增 `usage` 字段；`Provider.stream` 增 `system_suffix: str` 形参（Plan Mode 系统提示后缀）；两适配器在流结束后上抛本轮 token 用量、把 `system_suffix` 拼到内置系统提示后；OpenAI 适配器打开 `stream_options={"include_usage": True}`。
- **`mewcode.tools`（扩展）**：`Tool` Protocol 增 `read_only: bool` 属性；6 个工具各实现；`Registry` 增 `read_only_definitions()` 与 `is_read_only(name)`。
- **`mewcode.conversation`（扩展）**：增 `last_role()`（终止收尾时判断角色尾巴是否合法）。
- **`mewcode.prompt`（扩展）**：增 `PLAN_MODE_REMINDER`（计划态系统后缀）与 `EXECUTE_DIRECTIVE`（`/do` 触发执行时的用户消息）；`SYSTEM_PROMPT` 增补「持续工作直到任务完成」的 Agent 循环约定。
- **`mewcode.tui`（扩展）**：`submit` 识别 `/plan`、`/do`；引入 per-turn 取消事件；事件泵处理用量 / 进度 / 通知 / 多个并发工具；按键处理拆分 Esc / Ctrl+C；状态栏显示模式与累计用量、动态区显示迭代轮次。

---

## 核心数据结构

### 1. StopReason（新增，`agent/events.py`）

```python
from enum import Enum

class StopReason(Enum):
    NATURAL = "natural"                          # 自然终止（模型不再要工具）
    MAX_TURNS = "max_turns"                      # 达到迭代上限
    CANCELLED = "cancelled"                       # 用户取消（ESC/Ctrl+C）
    CONSECUTIVE_UNKNOWN_TOOLS = "unknown_tools"   # 连续未知工具
    STREAM_ERROR = "stream_error"                 # Provider 流式错误
```

### 2. TokenUsage（新增，`agent/events.py`）

```python
from dataclasses import dataclass

@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int
```

### 3. TurnEnd（新增，`agent/events.py`）

```python
@dataclass
class TurnEnd:
    turn: int
    tool_call_count: int
    token_usage: TokenUsage
```

### 4. Event 扩展（修改，`agent/events.py`）

```python
class EventType(Enum):
    TEXT = "text"                  # 不变
    TOOL_CALL = "tool_call"        # 不变
    TOOL_RESULT = "tool_result"    # 不变
    TOKEN_USAGE = "token_usage"    # 新增：每次 API 调用后推送
    TURN_START = "turn_start"      # 新增：每轮迭代开始
    TURN_END = "turn_end"          # 新增：每轮迭代结束
    DONE = "done"                  # 不变，payload 改为 StopReason
    ERROR = "error"                # 不变

@dataclass
class Event:
    type: EventType
    payload: str | ToolCall | ToolResult | TokenUsage | TurnEnd | StopReason | Exception
```

### 5. StreamEvent 扩展（修改，`provider/base.py`）

```python
@dataclass
class StreamEvent:
    text: str = ""
    tool_call: ToolCall | None = None
    done: bool = False
    err: Exception | None = None
    usage: TokenUsage | None = None   # 新增：流结束时的 token 用量
```

### 6. Provider 协议扩展（修改，`provider/base.py`）

```python
class Provider(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...

    def stream(
        self,
        msgs: list[Message],
        tools: list[ToolDefinition] | None = None,
        system_suffix: str = "",                 # 新增：Plan Mode 系统提示后缀
    ) -> AsyncIterator[StreamEvent]: ...
```

### 7. Tool 协议扩展（修改，`tools/base.py`）

```python
class Tool(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def parameters(self) -> dict: ...
    @property
    def read_only(self) -> bool: ...    # 新增：只读工具可并发
    async def execute(self, arguments: dict) -> ToolResult: ...
```

### 8. Registry 扩展（修改，`tools/registry.py`）

```python
class Registry:
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool | None: ...
    def to_definitions(self) -> list[ToolDefinition]: ...
    def read_only_definitions(self) -> list[ToolDefinition]: ...  # 新增
    def is_read_only(self, name: str) -> bool: ...                # 新增
    async def execute(self, name: str, arguments: dict) -> ToolResult: ...
```

### 9. ConversationManager 扩展（修改，`conversation/manager.py`）

```python
class ConversationManager:
    # ... 现有方法不变 ...
    def last_role(self) -> str | None: ...  # 新增：返回最后一条消息的 role
```

### 10. Config 扩展（修改，`config/schema.py`）

```python
@dataclass
class Config:
    provider: str
    max_turns: int
    system_prompt: str
    providers: list[ProviderConfig]
    plan_file: str = "plan.md"           # 新增
    default_mode: str = "normal"         # 新增："normal" | "plan"
```

### 11. Prompt 扩展（修改，`prompt/resources.py`）

```python
# 新增：Plan Mode 系统提示后缀
PLAN_MODE_REMINDER = """..."""

# 新增：/do 执行指令的用户消息模板
EXECUTE_DIRECTIVE = """..."""
```

---

## 模块设计

### 模块 A: Agent 循环引擎（`agent/agent.py` — 重写）

**职责：** ReAct 循环编排，**替代** ch03 的单轮闭环

**对外接口：** `Agent.run(user_input: str, mode: str = "normal", plan_content: str = "") → AsyncIterator[Event]`

**核心逻辑：**

```
1. conversation.add_user(user_input)
   （如果 mode == "execute" 且有 plan_content，先注入 EXECUTE_DIRECTIVE 格式的 user 消息）

2. _unknown_streak = 0     # 连续未知工具计数
   _cancelled = asyncio.Event()  # 取消信号

3. 构建 tool_defs：
   - mode == "plan" → registry.read_only_definitions()
   - mode == "normal" / "execute" → registry.to_definitions()

4. 构建 system_suffix：
   - mode == "plan" → PLAN_MODE_REMINDER
   - 否则 → ""

5. for turn in range(MAX_AGENT_TURNS):        # MAX_AGENT_TURNS = 10（内置常量）
   a. yield TURN_START(turn)
   b. 调用 provider.stream(context, tools=tool_defs, system_suffix=system_suffix)
   c. 消费 StreamEvent 流：
      - text 增量 → yield TEXT → 追加 _buffer
      - tool_call → 追加 _tool_calls
      - err → yield ERROR → yield DONE(STREAM_ERROR) → return
      - done/stream 结束 → 从 usage 提取 token 用量
   d. yield TOKEN_USAGE(token_usage)
   e. 如果 _tool_calls 为空（自然终止）：
      - conversation.add_assistant(_buffer)
      - yield DONE(NATURAL) → return
   f. 分类 tool_calls：
      - 已知工具 + 未知工具分开
      - 未知工具逐个 yield TOOL_RESULT(error)
      - 如果全部未知：_unknown_streak += 1
      - 如果 _unknown_streak >= 2：yield DONE(CONSECUTIVE_UNKNOWN_TOOLS) → return
      - 如果有已知工具：_unknown_streak = 0
   g. 已知工具写入 conversation：
      - conversation.add_assistant_with_tool_calls(_buffer, _tool_calls)
   h. 执行已知工具（保序分批并发）：
      - 读取 registry.is_read_only() 分组
      - 并发组：asyncio.gather 并行执行
      - 串行组：按原始顺序逐个执行
      - 每个 yield TOOL_CALL → yield TOOL_RESULT
   i. 收集结果（成功 + 失败），按原始顺序写入 conversation
   j. yield TURN_END(turn, tool_call_count, token_usage)
   k. 检查取消信号：
      - 如果 _cancelled.is_set() → 补「已取消」结果给未完成的工具
      - yield DONE(CANCELLED) → return

6. 达到上限：
   - conversation.add_assistant(_buffer + "（达到迭代上限）")
   - yield DONE(MAX_TURNS) → return
```

**新增属性：**
- `_cancelled: asyncio.Event` — TUI 在 ESC/Ctrl+C 时 set
- `cancel()` 方法 — 供 TUI 调用

**ch03 兼容性：** ch03 的 `Agent` 类被完全替换，但对外接口 `run(user_input) → AsyncIterator[Event]` 保持兼容（`mode` 默认 `"normal"` 行为与 ch03 的升级版一致）。

### 模块 B: 工具调度器（`agent/scheduler.py` — 新建）

**职责：** 按 `read_only` 属性分批执行，保序返回结果

**对外接口：**

```python
@dataclass
class ScheduledResult:
    """单个工具调度结果"""
    tool_call: ToolCall
    result: ToolResult

class ToolScheduler:
    def __init__(self, registry: Registry): ...

    async def schedule(
        self,
        tool_calls: list[ToolCall],
    ) -> list[ScheduledResult]:
        """按原始顺序返回结果；并发组 asyncio.gather，串行组逐个执行"""
```

**内部逻辑：**

```
1. 遍历 tool_calls，按 registry.is_read_only(name) 分组：
   - concurrent_items: list[(index, ToolCall)]
   - serial_items: list[(index, ToolCall)]
   （保留原始下标，用于最终排序）

2. 并发执行 concurrent_items：
   - 预分配 results 数组（长度 = len(tool_calls)）
   - asyncio.gather(*(execute(tc) for idx, tc in concurrent_items))
   - 每个结果写入 results[idx]

3. 串行执行 serial_items：
   - 按原始顺序 for tc in serial_items: results[idx] = await execute(tc)

4. 过滤 None 并返回 results
```

### 模块 C: Provider 适配器扩展（`provider/anthropic.py`、`provider/openai.py` — 修改）

**职责：** 上抛 token 用量、支持 `system_suffix`

**变更：**

**Anthropic 适配器：**
- `stream()` 方法新增 `system_suffix` 形参
- 构建 system 消息时：`content = base_system_prompt + system_suffix`
- 流结束时从 `message_stop` 事件的 `usage` 提取 `input_tokens`/`output_tokens`
- 最后一个 `StreamEvent` 设置 `usage = TokenUsage(input_tokens, output_tokens)`

**OpenAI 适配器：**
- `stream()` 方法新增 `system_suffix` 形参
- 构建 system 消息时：`content = base_system_prompt + system_suffix`
- 请求参数增加 `stream_options={"include_usage": True}`
- 流结束时从最后一个 chunk 的 `usage` 提取 `input_tokens`/`output_tokens`
- 最后一个 `StreamEvent` 设置 `usage = TokenUsage(input_tokens, output_tokens)`

### 模块 D: Tool 实现扩展（`tools/file_ops.py`、`tools/shell.py`、`tools/search.py` — 修改）

**变更：** 每个工具类新增 `read_only` 属性：

| 工具 | `read_only` |
|------|------------|
| `ReadFileTool` | `True` |
| `ListFilesTool` | `True` |
| `SearchCodeTool` | `True` |
| `WriteFileTool` | `False` |
| `EditFileTool` | `False` |
| `ExecuteCommandTool` | `False` |

### 模块 E: Registry 扩展（`tools/registry.py` — 修改）

**新增方法：**
- `read_only_definitions() → list[ToolDefinition]` — 仅导出 `read_only=True` 的工具定义
- `is_read_only(name: str) → bool` — 查询指定工具是否为只读

### 模块 F: ConversationManager 扩展（`conversation/manager.py` — 修改）

**新增方法：**
- `last_role() → str | None` — 返回 `_messages[-1].role` 或 `None`（空列表时）。用于 Agent 终止收尾时判断角色尾巴是否合法（如有 `assistant` 无对应的 `tool` 结果，需补「已取消」）。

**新增辅助方法：**
- `add_assistant_with_tool_calls(text: str, tool_calls: list[ToolCall])` — 一次写入含文本和多个 `tool_calls` 声明的 assistant 消息
- `add_tool_results(results: list[tuple[ToolCall, ToolResult]])` — 按序写入 tool 结果消息
- `add_cancelled_tool_result(tool_call: ToolCall)` — 为单个未完成的工具调用补 `output="已取消"` 的 tool 结果

**滑动窗口逻辑不变：** 按 user/assistant 对数裁剪，tool 消息不计入对数统计。

### 模块 G: Prompt 扩展（`prompt/resources.py` — 修改）

**新增内容：**

- `PLAN_MODE_REMINDER`：计划模式系统提示后缀，注入到 system prompt 末尾。告知模型当前处于计划模式，只能使用只读工具探查代码，最终产出应是一份可执行的计划文档，不要尝试修改文件或执行命令。

- `EXECUTE_DIRECTIVE`：`/do` 触发执行时的用户消息模板。包含计划文件内容，指示模型按照计划执行。

- `SYSTEM_PROMPT` 增补：在现有系统提示中增加 Agent 循环的行为约定——"持续工作直到任务完成，可连续调用多个工具，无需等待用户确认"。

### 模块 H: TUI 扩展（`tui/app.py` — 修改）

**职责：** 消费新事件类型，支持 ESC/Ctrl+C 取消，展示进度和 Token 用量，识别 `/plan`/`/do`

**变更：**

1. **斜杠命令识别：**
   - `submit` 时检查输入是否以 `/plan` 或 `/do` 开头
   - `/plan` → 以 `mode="plan"` 启动 Agent
   - `/do` → 读取 `plan.md`，以 `mode="execute"` + `plan_content` 启动 Agent
   - 普通输入 → 以 `mode="normal"` 启动 Agent

2. **按键处理拆分：**
   - 流式态：ESC 或 Ctrl+C → 调用 `agent.cancel()`（设置 `_cancelled` 事件），等待 `DONE(CANCELLED)` 后回到空闲态
   - 空闲态：Ctrl+C 或 ESC → 退出程序

3. **事件泵处理：**
   - `TOKEN_USAGE` → 累加 `_session_input_tokens` / `_session_output_tokens`，更新状态栏
   - `TURN_START` → 更新动态区显示 "Turn N/10"
   - `TURN_END` → 更新动态区进度
   - `TOOL_CALL` → 渲染工具行（`● tool_name(params)`），多个工具调用几乎同时出现（并发）
   - `TOOL_RESULT` → 渲染结果摘要，错误时红色区分
   - `DONE(StopReason)` → 根据终止原因展示不同提示
   - `ERROR` → 红色错误提示，不崩溃

4. **状态栏：**
   - 左侧：当前模式（`[plan]` 或 `[normal]`）+ provider 名称
   - 右侧：累计 token 用量（`Σ in:1.2k out:800`）

5. **动态区：**
   - 流式态显示当前迭代轮次（如 `Turn 2/10 · Imagining… (3s)`）

### 模块 I: 单次调用模式扩展（`main.py` — 修改）

**变更：**
- `_oneshot` 处理新增事件类型：`TOKEN_USAGE`（忽略或简洁打印）、`TURN_START`/`TURN_END`（忽略）
- 新增 `-p`/`--plan` CLI 参数：以计划模式运行单次调用
- 保持向后兼容

---

## 模块交互

### 正常路径：多轮工具调用

```
用户输入 "对比 main.py 和 agent.py 的导入部分"
  │
  ▼
tui/app.py → REPL._process_input()
  │ 识别为普通输入 → mode="normal"
  ▼
agent/agent.py → Agent.run(user_input, mode="normal")
  │
  ├─ conversation.add_user(user_input)
  ├─ tool_defs = registry.to_definitions()   # 全工具集
  ├─ system_suffix = ""
  │
  ├─ [Turn 0] ──────────────────────────────────────────
  │  yield TURN_START(0)                              → UI: "Turn 1/10"
  │  provider.stream(context, tools=6, system_suffix="")
  │    ├─ StreamEvent(text="我来对比")                  → yield TEXT("我来对比")
  │    ├─ StreamEvent(tool_call=read_file("main.py"))   → 收集到 _tool_calls
  │    ├─ StreamEvent(tool_call=read_file("agent.py"))  → 收集到 _tool_calls
  │    └─ StreamEvent(done=True, usage={in:500,out:200})
  │  yield TOKEN_USAGE(500, 200)                      → UI: 累加展示
  │  _tool_calls = [read_file("main.py"), read_file("agent.py")]
  │  → 分类：全部已知，_unknown_streak = 0
  │  → conversation.add_assistant_with_tool_calls("我来对比", tool_calls)
  │  → ToolScheduler.schedule(tool_calls):
  │      read_only 组 → asyncio.gather(读 main.py, 读 agent.py)
  │      yield TOOL_CALL(read_file) → UI: ● read_file(path="main.py")
  │      yield TOOL_RESULT(ok)      → UI:   → (内容摘要)
  │      yield TOOL_CALL(read_file) → UI: ● read_file(path="agent.py")
  │      yield TOOL_RESULT(ok)      → UI:   → (内容摘要)
  │  → conversation.add_tool_results(results)
  │  yield TURN_END(0, count=2, {in:500,out:200})
  │
  ├─ [Turn 1] ──────────────────────────────────────────
  │  yield TURN_START(1)                              → UI: "Turn 2/10"
  │  provider.stream(context, tools=6, system_suffix="")
  │    └─ StreamEvent(text="两个文件的导入部分对比如下...")
  │       └─ StreamEvent(done=True, usage={in:800,out:300})
  │  yield TOKEN_USAGE(800, 300)                      → UI: 累加
  │  _tool_calls = []  → 自然终止
  │  conversation.add_assistant("两个文件的导入部分对比如下...")
  │  yield DONE(NATURAL)                              → UI: "Done (12s)"
  │  return
```

### 异常路径：用户取消

```
用户按 ESC（流式态）
  │
  ▼
tui/app.py → REPL._cancel_stream()
  │ agent.cancel()  → agent._cancelled.set()
  ▼
agent/agent.py → 当前轮次工具执行完毕后检查
  │ _cancelled.is_set() 为 True
  │ 未完成的 tool_calls → conversation.add_cancelled_tool_result(tc)
  │ yield DONE(CANCELLED)                              → UI: "已取消"
  │ return
  │
  ▼
tui/app.py → 收到 DONE(CANCELLED)
  │ 回到空闲态，用户可继续对话
```

### 异常路径：连续未知工具

```
[Turn N] 模型请求 tool_xxx（注册表中不存在）
  │ 全部 unknown → _unknown_streak += 1 (=1)
  │ 每个未知工具 yield TOOL_RESULT(error="未知工具: tool_xxx")
  │ conversation.add_assistant_with_tool_calls(...)  + add_tool_result(error)
  │ 继续下一轮
[Turn N+1] 模型再次请求 tool_xxx
  │ 全部 unknown → _unknown_streak += 1 (=2)
  │ yield DONE(CONSECUTIVE_UNKNOWN_TOOLS)             → UI: "连续未知工具，已停止"
  │ return
```

### Plan Mode 路径

```
用户输入 "/plan 分析项目结构"
  │
  ▼
tui/app.py → 识别 "/plan"
  │ 提取任务文本："分析项目结构"
  ▼
agent/agent.py → Agent.run("分析项目结构", mode="plan")
  │
  ├─ tool_defs = registry.read_only_definitions()  # 仅 3 个只读工具
  ├─ system_suffix = PLAN_MODE_REMINDER
  │
  ├─ [ReAct 循环，模型只能用 read_file/list_files/search_code]
  │  ...
  └─ DONE(NATURAL)
       │
       ▼
  main/tui 将完整响应文本写入 plan_file（默认 plan.md）
```

```
用户输入 "/do"
  │
  ▼
tui/app.py → 识别 "/do"
  │ 读取 plan.md 内容
  ▼
agent/agent.py → Agent.run("", mode="execute", plan_content=plan_md)
  │
  ├─ conversation.add_user(EXECUTE_DIRECTIVE.format(plan=plan_md))
  ├─ tool_defs = registry.to_definitions()          # 全工具集
  ├─ system_suffix = ""
  │
  ├─ [ReAct 循环，全工具可用]
  │  ...
  └─ DONE(NATURAL)
```

---

## 文件组织

```
mewcode/
├── agent/
│   ├── __init__.py          — 导出 Agent, Event, EventType, StopReason, TokenUsage, TurnEnd
│   ├── agent.py             — 重写：ReAct 循环引擎（替代 ch03 单轮闭环）
│   ├── scheduler.py         — 新建：ToolScheduler（保序分批并发执行）
│   └── events.py            — 修改：新增 EventType 枚举值、TokenUsage、TurnEnd、StopReason
├── provider/
│   ├── base.py              — 修改：StreamEvent 增 usage；Provider Protocol 增 system_suffix
│   ├── anthropic.py         — 修改：流结束上抛 usage；system_suffix 拼接到系统提示后
│   └── openai.py            — 修改：stream_options include_usage；system_suffix 拼接；流结束上抛 usage
├── tools/
│   ├── base.py              — 修改：Tool Protocol 增 read_only: bool
│   ├── registry.py          — 修改：增 read_only_definitions()、is_read_only()
│   ├── file_ops.py          — 修改：ReadFileTool(read_only=True), WriteFileTool(read_only=False), EditFileTool(read_only=False)
│   ├── shell.py             — 修改：ExecuteCommandTool(read_only=False)
│   └── search.py            — 修改：ListFilesTool(read_only=True), SearchCodeTool(read_only=True)
├── conversation/
│   └── manager.py           — 修改：增 last_role()、add_assistant_with_tool_calls()、add_tool_results()、add_cancelled_tool_result()
├── prompt/
│   └── resources.py         — 修改：增 PLAN_MODE_REMINDER、EXECUTE_DIRECTIVE；SYSTEM_PROMPT 增补 Agent 循环约定
├── config/
│   ├── schema.py            — 修改：Config 增 plan_file、default_mode
│   └── loader.py            — 修改：解析 plan_file、default_mode 字段
├── tui/
│   ├── app.py               — 修改：识别 /plan /do；ESC/Ctrl+C 拆分；新事件消费；状态栏/动态区
│   └── renderer.py          — 可能修改：支持多个工具行渲染
└── main.py                  — 修改：-p/--plan CLI 参数；_oneshot 新事件处理
```

---

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 循环架构 | Agent 内部 while 循环 + 取消事件 | 核心循环简洁，取消信号通过 `asyncio.Event` 传递，零依赖，TUI 直接 set |
| 流式收集 | Agent 内联消费 StreamEvent（不新建独立类） | 逻辑简单，Agent 直接 yield Event 给上层，无需引入回调或中间收集器类 |
| 工具调度 | ToolScheduler 独立类 | 分批逻辑独立可测，Agent 保持简洁 |
| 并发执行 | `asyncio.gather` + 预分配 list 按下标写入 | 单线程天然无数据竞争，下标保证结果顺序 |
| 历史完整性 | 取消时 `add_cancelled_tool_result()` 补位 | 确保 tool_call 与 tool_result 配对，避免脏历史阻断后续对话 |
| 未知工具阈值 | 连续 2 次全部未知才停止 | 单次误判不终止，反复调用不存在工具是明确异常 |
| 迭代上限 | 内置常量 `MAX_AGENT_TURNS = 10`，不可配 | 用户决策：保守默认值，ch04 不暴露配置 |
| Plan Mode | 创建只读 Registry + system_suffix | 不引入 Agent 子类，通过 Registry 和 Prompt 控制是最小侵入方式 |
| 计划传递 | 文件传递（plan.md） | 用户选择：/plan 独立产出，/do 读取注入；两个阶段独立 |
| Token 用量 | 从 Provider 流末尾 `StreamEvent.usage` 提取 | 两适配器在流结束时上抛，Agent 转为 TOKEN_USAGE 事件 |
| Provider 接口 | `stream()` 增 `system_suffix` 可选形参 | 向后兼容（默认 `""`），Plan Mode 时注入后缀 |
| TUI 取消 | Esc/Ctrl+C 流式态取消 Loop 回空闲态，空闲态退出 | spec 明确要求：Loop 中断后不退出程序，可继续对话 |
| 状态栏 | 模式 + 累计用量 | 复用现有 status bar 机制，扩展展示内容 |
| import 依赖 | 无环，遵循现有方向 | tool → provider；conversation → provider；agent → {provider, tools, conversation}；tui → {agent, tools, conversation, provider, prompt} |