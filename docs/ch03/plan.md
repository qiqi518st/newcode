# MewCode 工具系统 — 技术设计 (plan.md)

> 基于已批准的 spec.md。本文档与语言相关（Python 3.12+）。SDK 调用方式已对 `anthropic`（`AsyncAnthropic`，支持 tool_use streaming）、`openai`（`AsyncOpenAI`，支持 chat.completions tool_calls streaming）实测核对。

## 架构概览

在 ch02「provider → conversation → tui」三件套之上，新增两个包并扩展三处：

- **`mewcode.tool`（新建）**：统一工具抽象 `Tool`、执行结果 `Result`、注册中心 `Registry`、6 个核心工具。零外部依赖，不感知 LLM 协议。
- **`mewcode.agent`（新建）**：承载「单轮闭环」编排——请求#1（带工具）→ 收集工具调用 → 注册中心执行 → 结果回灌进 `Conversation` → 请求#2（续答）→ 最终文本 → 停。对外吐出一条 `Event` async generator 供 TUI 渲染。只依赖 `llm`、`tool`、`conversation`，不 import anthropic/openai，保持协议无关。
- **`mewcode.llm`（扩展）**：`Message`/`StreamEvent` 增加工具字段；新增协议无关类型 `ToolCall`/`ToolResult`/`ToolDefinition` 与 `ROLE_TOOL` 常量；`Provider.stream` 增加 `tools` 参数；两个适配器注入工具定义、解析流式工具调用、回灌工具结果。
- **`mewcode.conversation`（扩展）**：新增「assistant 工具调用回合」与「工具结果回合」的追加方法。
- **`mewcode.prompt`（扩展）**：`SYSTEM_PROMPT` 增补 Agent 角色与工具使用约定。
- **`mewcode.tui`（扩展）**：`submit` 改走 `Agent.run`；事件消费 task 处理工具事件；渲染 Claude Code 风格工具行与执行指示。
- **`mewcode/main.py`（扩展）**：构造 `tool.new_default_registry()` 并注入 `REPL`。

依赖方向（无环）：
```
tool → llm
conversation → llm
agent → {llm, tool, conversation}
tui → {agent, tool, conversation, llm, prompt}
llm → {config, prompt}
```

> **包名映射说明**：`mewcode.llm` 对应现有代码中的 `mewcode/provider/` 目录，本次不强制重命名，新增类型与扩展逻辑仍放在 `mewcode/provider/` 中；`mewcode.tool` 对应新建目录 `mewcode/tools/`；`mewcode.agent` 对应新建目录 `mewcode/agent/`。

---

## 核心数据结构

### `llm.Message`

```python
@dataclass
class Message:
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    tool_call_id: str | None = None   # OpenAI 回灌需要；Anthropic 用 tool_use_id
    name: str | None = None           # OpenAI tool 角色需要 tool name
```

- `role="tool"` 用于承载工具结果回灌。
- `tool_call_id` / `name` 仅在 `role="tool"` 时由 Provider 适配器填充，Conversation 层透传。

### `llm.StreamEvent`

```python
@dataclass
class StreamEvent:
    text: str = ""
    tool_call: ToolCall | None = None   # 流中出现工具调用时填充
    done: bool = False
    err: Exception | None = None
```

- `text` 与 `tool_call` 互斥，同一事件不会同时出现。
- 工具调用的 JSON 参数在 Provider 适配器内部拼接完整后才组装为 `ToolCall` 吐出。

### `llm.ToolCall`

```python
@dataclass
class ToolCall:
    tool_name: str
    arguments: dict   # 已解析的 JSON 参数字典
```

### `llm.ToolDefinition`

```python
@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict   # JSON Schema object
```

用于 Registry 向 Provider 导出工具定义，协议无关。

### `llm.ToolResult`

```python
@dataclass
class ToolResult:
    status: Literal["ok", "error"]
    output: str = ""
    error: str = ""
    truncated: bool = False
```

- 放在 `llm` 包而非 `tool` 包，因为 Provider 回灌时需要知道如何序列化它。
- `tool` 包的工具执行方法返回此类型，但构造过程在工具内部完成。

### `tool.Tool` Protocol

```python
class Tool(Protocol):
    @property
    def name(self) -> str: ...
    
    @property
    def description(self) -> str: ...
    
    @property
    def parameters(self) -> dict: ...   # JSON Schema
    
    async def execute(self, arguments: dict) -> ToolResult:
        ...
```

### `agent.Event`

```python
class EventType(Enum):
    TEXT = "text"              # 文本增量（来自模型的文本或最终答复）
    TOOL_CALL = "tool_call"    # 工具调用请求（展示工具行）
    TOOL_RESULT = "tool_result" # 工具执行结果（展示结果摘要）
    DONE = "done"              # 本轮结束
    ERROR = "error"            # 出错

@dataclass
class Event:
    type: EventType
    payload: str | ToolCall | ToolResult | Exception
```

- TUI 只理解 `agent.Event`，不直接消费 `llm.StreamEvent`。
- `Agent.run()` 消化 Provider 的流式差异和闭环逻辑，向 TUI 输出统一事件流。

---

## 模块设计

### `mewcode.tool` 包

**职责**：定义工具协议、实现六个核心工具、提供注册中心。零外部依赖，不感知 LLM 协议。

#### `tool.base`

- `Tool` Protocol
- 不定义 `ToolResult`（该类型在 `llm` 包，tool 包只 import 使用）

#### `tool.registry`

```python
class Registry:
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool | None: ...
    def to_definitions(self) -> list[ToolDefinition]: ...
    
    @staticmethod
    def default() -> Registry: ...   # 预装六个核心工具
```

- `to_definitions()` 将注册的工具批量导出为 `list[ToolDefinition]`，供 Provider 转译为 API 格式。

#### `tool.file_ops`

- `ReadFileTool`
  - 参数：`path` (str)，可选 `offset` (int)，`limit` (int)
  - 正常：文件内容字符串；大文件按 `limit` 截断，`truncated=True`
  - 异常：文件不存在、路径越界、非文本文件 → `status="error"`
  - 超时：10 秒（由 Registry 外层控制，或工具内部 `asyncio.wait_for`）

- `WriteFileTool`
  - 参数：`path` (str)，`content` (str)
  - 正常：确认写入
  - 异常：路径越界、目录创建失败、权限不足 → `status="error"`
  - 超时：10 秒

- `EditFileTool`
  - 参数：`path` (str)，`old_string` (str)，`new_string` (str)
  - 正常：`old_string` 恰好出现 1 次，替换成功
  - 异常：
    - 未找到 → `error="old_string 在文件中未找到"`
    - 找到 N 处（N>1）→ `error="old_string 在文件中找到 N 处，无法确定替换哪一处"`
    - 文件不存在、权限不足 → 相应错误
  - 超时：10 秒

#### `tool.shell`

- `ExecuteCommandTool`
  - 参数：`command` (str)，可选 `cwd` (str，默认项目根目录)
  - 白名单检查：解析命令第一个 token，不在白名单则直接返回 `status="error", error="命令 'xxx' 不在白名单"`
  - 白名单（初版）：`ls`, `cat`, `grep`, `find`, `python`, `pytest`, `git`, `pwd`, `echo`, `head`, `tail`, `wc`, `mkdir`, `touch`
  - 正常：stdout、stderr、exit_code 包装为字符串
  - 异常：超时（60 秒，返回已收集输出 + 超时标记）、命令不存在 → `status="error"`
  - 体量控制：stdout+stderr 超过 10KB 截断，`truncated=True`

#### `tool.search`

- `ListFilesTool`
  - 参数：`pattern` (str，glob 模式)，可选 `cwd` (str)
  - 正常：匹配的文件路径列表（空列表不视为错误）
  - 异常：模式非法、目录不存在 → `status="error"`
  - 体量控制：最多返回 100 条

- `SearchCodeTool`
  - 参数：`pattern` (str，正则表达式)，可选 `cwd` (str)，`glob` (str，文件过滤)
  - 正常：匹配结果列表（文件路径、行号、片段），空列表不视为错误
  - 异常：正则非法、目录不存在 → `status="error"`
  - 体量控制：最多返回 50 条匹配，每条片段最多 200 字符

#### 安全机制（所有工具共用）

- **路径遍历防护**：所有涉及文件路径的工具，先 `os.path.abspath()` 解析，再检查是否在项目工作目录（`os.getcwd()`）内。越界则返回 `status="error", error="路径超出项目范围"`。
- **超时**：`asyncio.wait_for()` 包裹执行体，超时后取消并返回超时错误。

### `mewcode.agent` 包

**职责**：单轮闭环编排。接收用户问题，输出统一 `Event` async generator。

#### `agent.Agent`

```python
class Agent:
    def __init__(
        self,
        provider: Provider,
        conversation: ConversationManager,
        registry: Registry,
    ) -> None: ...

    async def run(self, user_input: str) -> AsyncIterator[Event]:
        ...
```

**内部流程**：

```
run(user_input):
  1. conversation.add_user(user_input)
  2. tool_defs = registry.to_definitions()
  3. 第 1 次请求：provider.stream(conversation.get_context(), tools=tool_defs)
  4. 消费流（请求#1）：
     buffer = ""
     tool_call_buffer = None   # 用于拼接分片 JSON
     async for event in stream:
       if event.text:
         buffer += event.text
         yield Event(EventType.TEXT, event.text)
       elif event.tool_call:
         # 流已完整吐出 ToolCall，JSON 拼接在 Provider 内部完成
         yield Event(EventType.TOOL_CALL, event.tool_call)
         # 执行工具
         tool = registry.get(event.tool_call.tool_name)
         if tool is None:
           result = ToolResult(status="error", error=f"未知工具: {event.tool_call.tool_name}")
         else:
           result = await tool.execute(event.tool_call.arguments)
         yield Event(EventType.TOOL_RESULT, result)
         # 回灌
         conversation.add_tool_call(event.tool_call)   # assistant 的工具调用消息
         conversation.add_tool_result(result)          # tool 角色的结果消息
         break  # 请求#1 结束，进入请求#2
       elif event.done:
         conversation.add_assistant(buffer)
         yield Event(EventType.DONE, "")
         return
       elif event.err:
         yield Event(EventType.ERROR, event.err)
         return
  5. 第 2 次请求：provider.stream(conversation.get_context(), tools=None)
  6. 消费流（请求#2，续答）：
     buffer = ""
     async for event in stream:
       if event.text:
         buffer += event.text
         yield Event(EventType.TEXT, event.text)
       elif event.done:
         conversation.add_assistant(buffer)
         yield Event(EventType.DONE, "")
         return
       elif event.err:
         yield Event(EventType.ERROR, event.err)
         return
  7. 防御：请求#2 中若出现 tool_call（理论上不应出现，因未传 tools），忽略并继续消费文本。
```

**关键约束**：
- 单轮闭环：最多两次 Provider 调用。
- 不循环：请求#2 中即使有 tool_call 也不处理（spec 明确不做 Agent Loop）。
- TUI 和单次调用共用同一 `Agent.run()`，各自消费 `Event` 流即可。

### `mewcode.llm` 包（扩展现有 `mewcode/provider/`）

#### `llm.base` 扩展

- `Message` 新增 `role="tool"`，新增 `tool_call_id`、`name` 字段。
- `StreamEvent` 新增 `tool_call: ToolCall | None`。
- 新增 `ToolCall`、`ToolDefinition`、`ToolResult`（协议无关）。
- `Provider.stream` 签名扩展：
  ```python
  def stream(self, msgs: list[Message], tools: list[ToolDefinition] | None = None) -> AsyncIterator[StreamEvent]:
      ...
  ```

#### `llm.anthropic` 扩展

**发送时**：
- 若 `tools` 不为空，注入 `tools` 参数：
  ```python
  tools=[{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
  ```

**消费流时**：
- Anthropic 流式 tool_use 的 SDK 行为：
  - `content_block_start` 事件标记 tool_use 块开始，含 `name` 和空的 `input`。
  - `content_block_delta` 事件携带 `partial_json` 增量。
  - 需要内部维护 `tool_name` 和 `partial_json` 缓冲区，拼接完成后 `json.loads()` 得到参数字典，组装 `ToolCall` 吐出。
- 普通文本增量仍走 `StreamEvent(text=...)`。
- tool_use 块完成后吐 `StreamEvent(tool_call=ToolCall(...))`，然后继续消费直到 `message_stop` 吐 `StreamEvent(done=True)`。

**回灌工具结果**：
- 构造 Anthropic 格式的 tool_result 消息：
  ```python
  {
      "role": "user",
      "content": [{
          "type": "tool_result",
          "tool_use_id": tool_use_id,   # 需要从 content_block_start 保存
          "content": result.output if result.status == "ok" else result.error,
          "is_error": result.status == "error",
      }]
  }
  ```
- 需要在内部保存 `tool_use_id`，与 `ToolCall` 一起传给 Agent，Agent 再回传给 Provider 回灌。

#### `llm.openai` 扩展

**发送时**：
- 若 `tools` 不为空，注入 `tools` 参数：
  ```python
  tools=[{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
  ```

**消费流时**：
- OpenAI 流式 tool_calls 的 SDK 行为：
  - `delta.tool_calls` 列表，每个元素含 `index`、`id`、`function.name`、`function.arguments`。
  - `function.arguments` 是 JSON 字符串增量，需要按 `index` 分桶拼接。
  - 拼接完成后 `json.loads()` 得到参数字典，组装 `ToolCall` 吐出。
- 注意：OpenAI 可能在同一个 chunk 中同时出现 `content`（文本）和 `tool_calls`，但 tool_calls 出现时 `content` 通常为空。实现上优先处理 `tool_calls`，非空时忽略 `content`。

**回灌工具结果**：
- 构造 OpenAI 格式的 tool 消息：
  ```python
  {
      "role": "tool",
      "tool_call_id": tool_call_id,
      "name": tool_name,
      "content": result.output if result.status == "ok" else result.error,
  }
  ```

### `mewcode.conversation` 包（扩展）

#### `conversation.manager` 扩展

新增方法：

```python
def add_tool_call(self, tool_call: ToolCall) -> None:
    """追加 assistant 的工具调用回合。"""
    # 需要以 assistant 角色消息存储工具调用描述
    # Anthropic: assistant content 含 tool_use 块
    # OpenAI: assistant message 含 tool_calls 列表
    # 为协议无关，ConversationManager 只保存简化文本描述，
    # 具体格式由 Provider 适配器在 get_context() 时构造？
    # 或 ConversationManager 保存通用结构，Provider 转译？
    ...

def add_tool_result(self, result: ToolResult) -> None:
    """追加 tool 角色的结果消息。"""
    ...
```

**设计决策**：`ConversationManager` 保持协议无关，不直接存储 Anthropic/OpenAI 特有的消息格式。

- `add_tool_call` 存储 assistant 的消息，内容为工具调用的 JSON 表示（如 `{"tool_calls": [{"name": "read_file", "arguments": {...}}]}`），并附带 `tool_call_id`。
- `add_tool_result` 存储 `role="tool"` 的消息，内容为结果字符串，附带 `tool_call_id` 和 `name`。
- `get_context()` 返回 `list[Message]`，Provider 适配器在发送前将 Message 转译为各自 SDK 所需的字典格式。

或更简洁的方案：`ConversationManager` 只追加普通文本消息（工具调用描述和结果描述），但这样 Provider 无法构造正确的 tool_result 消息格式。**必须保存结构化信息。**

最终方案：
- `Message` 增加可选字段 `tool_calls: list[dict] | None`（assistant 消息携带）和 `tool_call_id` / `name`（tool 消息携带）。
- `ConversationManager.add_tool_call` 构造 assistant 消息，`tool_calls` 字段存 `[{"id": ..., "name": ..., "arguments": ...}]`。
- `ConversationManager.add_tool_result` 构造 tool 消息，`tool_call_id` 和 `name` 填充。
- `get_context()` 透传这些字段。
- Provider 适配器的 `stream()` 方法内部将 `Message` 列表转译为 SDK 所需的字典格式时，识别 `tool_calls` 和 `tool_call_id` 字段并正确构造。

### `mewcode.prompt` 包（扩展）

#### `prompt.resources` 扩展

`SYSTEM_PROMPT` 增补以下内容：

```
你是一个编程助手，可以使用以下工具来观察项目状态和完成用户请求：

- read_file: 读取文件内容
- write_file: 写入文件内容
- edit_file: 在文件中进行原文替换（old_string 必须恰好出现一次）
- execute_command: 执行 shell 命令（仅白名单内的命令可用）
- list_files: 按 glob 模式列出文件
- search_code: 按正则表达式搜索代码内容

当你需要使用工具时，调用一次即可。如果不需要工具，直接回答用户。
```

- 具体措辞在实现时微调，确保模型理解每个工具的用途和参数规范。
- 特别需要强调 `edit_file` 的"唯一匹配"约束，减少模型因重复匹配而失败。

### `mewcode.tui` 包（扩展）

#### `tui.app` 扩展

**REPL 修改**：

1. `__init__` 接收 `Agent` 而非 `Provider`，或同时接收两者但 `_process_input` 改走 Agent。
   - 建议：`REPL.__init__(agent, renderer)`，`Agent` 内部已持有 Provider 和 Conversation。
   - 但 `REPL` 需要 `ConversationManager` 做一些操作吗？ch02 中 `REPL` 直接操作 `conv.add_user` 等，改走 Agent 后这些由 Agent 内部处理。

2. `_process_input` 流程：
   ```python
   async def _process_input(self, text: str) -> None:
       self.state = SessionState.STREAMING
       self.cur_reply = ""
       self.turn_start = time.monotonic()
       
       self._stream_task = asyncio.create_task(self._consume_agent_events(text))
       try:
           await self._stream_task
       except asyncio.CancelledError:
           self._show_cancelled()
       finally:
           self.state = SessionState.IDLE
   ```

3. `_consume_agent_events` 替代 `_consume_stream`：
   ```python
   async def _consume_agent_events(self, user_input: str) -> None:
       buffer = ""
       async for event in self.agent.run(user_input):
           if event.type == EventType.TEXT:
               buffer += event.payload
               live.update(Markdown(buffer))
           elif event.type == EventType.TOOL_CALL:
               tc = event.payload  # ToolCall
               self._console.print(f"● {tc.tool_name}(...)", style="bold green")
           elif event.type == EventType.TOOL_RESULT:
               tr = event.payload  # ToolResult
               summary = tr.output[:200] + "..." if len(tr.output) > 200 else tr.output
               if tr.status == "error":
                   self._console.print(f"  ✗ {tr.error}", style="red")
               else:
                   self._console.print(f"  → {summary}", style="dim")
           elif event.type == EventType.DONE:
               self.cur_reply = buffer
               elapsed = time.monotonic() - self.turn_start
               self._show_done(elapsed)
               return
           elif event.type == EventType.ERROR:
               self._show_error(event.payload)
               return
   ```

4. **单次调用模式** `_oneshot` 同样消费 `AgentEvent`，纯文本输出。

#### `tui.renderer` 扩展

- `RichRenderer` 可能需要新增渲染工具行的方法，或直接在 `REPL` 中用 `Console.print` 处理。
- 工具行的样式：绿色前缀 `●`，参数摘要；结果用 `dim` 或 `red`（错误时）。

---

## 文件组织

```
mewcode/
├── __init__.py
├── __main__.py
├── main.py                    # 扩展：构造 Registry 注入 REPL/oneshot
├── config/
│   ├── __init__.py
│   ├── schema.py
│   └── loader.py
├── provider/                  # 对应 llm 包
│   ├── __init__.py
│   ├── base.py                # 扩展：ToolCall、ToolDefinition、ToolResult、Message(role="tool")、StreamEvent(tool_call)
│   ├── anthropic.py           # 扩展：注入 tools、解析 tool_use streaming、回灌 tool_result
│   └── openai.py              # 扩展：注入 tools、解析 function_call streaming、回灌 function 结果
├── conversation/
│   ├── __init__.py
│   └── manager.py             # 扩展：add_tool_call、add_tool_result、get_context 处理 tool 消息
├── prompt/
│   ├── __init__.py
│   └── resources.py           # 扩展：SYSTEM_PROMPT 增补 Agent 角色与工具约定
├── tools/                     # 新建（对应 tool 包）
│   ├── __init__.py
│   ├── base.py                # Tool Protocol
│   ├── registry.py            # Registry 类、default() 工厂
│   ├── file_ops.py            # ReadFileTool、WriteFileTool、EditFileTool
│   ├── shell.py               # ExecuteCommandTool（含白名单）
│   └── search.py              # ListFilesTool、SearchCodeTool
├── agent/                     # 新建
│   ├── __init__.py
│   ├── agent.py               # Agent 类、单轮闭环编排
│   └── events.py              # EventType、Event
├── tui/
│   ├── __init__.py
│   ├── app.py                 # 扩展：_process_input 改走 agent.run、消费 AgentEvent
│   └── renderer.py            # 扩展/复用：渲染逻辑
└── utils/
    ├── __init__.py
    └── error.py               # 扩展：新增 ToolError、TimeoutError 等
tests/
├── test_tui_wiring.py         # 已有，扩展工具事件测试
├── test_tools.py              # 新建：工具单元测试（覆盖正常/异常/超时/路径安全）
├── test_agent.py              # 新建：Agent 闭环测试（mock provider + mock tool）
├── test_provider_tools.py     # 新建：Provider 工具解析测试（mock SDK 流）
└── test_conversation_tools.py # 新建：ConversationManager 工具消息测试
```

---

## 模块交互

```
用户输入 → REPL._process_input(text)
                ↓
         Agent.run(text)
                ↓
         Conversation.add_user(text)
                ↓
         Provider.stream(msgs, tools=registry.to_definitions())
                ↓
         流式输出：text 增量 或 tool_call
                ↓
         若为 tool_call:
           → yield Event(TOOL_CALL)
           → Registry.execute(tool_name, args)
           → yield Event(TOOL_RESULT)
           → Conversation.add_tool_call + add_tool_result
           → Provider.stream(msgs, tools=None)  # 请求#2
           → 流式输出：text 增量 → yield Event(TEXT)
           → done → yield Event(DONE)
                ↓
         REPL 消费 Event 流：TEXT → Live 渲染；TOOL_CALL → 打印工具行；TOOL_RESULT → 打印摘要；DONE → 定型
```

---

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 闭环编排放在 agent 层还是 TUI 层？ | `agent` 层 | TUI 和单次调用共用，避免重复；TUI 只负责渲染 |
| Tool 层是否感知 LLM 协议？ | 不感知 | `tool` 零外部依赖，Registry 导出协议无关的 `ToolDefinition` |
| 工具结果回灌格式由谁处理？ | Provider 适配器 | Anthropic 和 OpenAI 的工具结果消息格式不同，各自处理 |
| `AgentEvent` 是否必要？ | 必要 | TUI 不直接消费 Provider 的 `StreamEvent`，而是消费 Agent 重组后的统一事件流，屏蔽协议差异和闭环细节 |
| 改文件用 AST 还是字符串匹配？ | 字符串匹配 | 简单直接，Python 源码的 AST 重构复杂度高；唯一匹配策略足够安全 |
| 白名单还是黑名单？ | 白名单 | spec 已确认；防止 Agent 误执行高危命令 |
| 超时实现方式 | `asyncio.wait_for()` | Python 标准库，简洁。命令执行配合 `asyncio.create_subprocess_exec` 实现进程级超时 |
| 命令执行用 subprocess 还是 asyncio.subprocess？ | `asyncio.subprocess` | 与 async 生态兼容，配合超时更自然 |
| ConversationManager 是否保存协议特定的 tool_call_id？ | 是，保存在 Message 字段中 | Provider 回灌时必须携带正确的 ID，Conversation 层透传 |
| tool_use / function_call 的 JSON 拼接在哪层完成？ | Provider 适配器内部 | Agent 层不需要知道 JSON 是分片到达的，只接收完整的 `ToolCall` |
| 请求#2 中出现意外 tool_call 如何处理？ | 忽略，继续消费文本 | spec 明确不做 Agent Loop，防御性编程 |
| 白名单命令列表是否可扩展？ | 本章硬编码，不做配置化 | 属于"不做的事" |
| 路径越界检查在 tool 层还是 agent 层？ | tool 层 | 每个文件工具自己负责，安全边界贴近操作 |
| 截断提示是否包含在 ToolResult.output 中？ | 是，拼接在内容尾部 | 模型需要知道"后面还有"，才能决定是否需要再次读取 |
| System Prompt 中是否列出完整参数 Schema？ | 否，只列工具名和用途摘要 | JSON Schema 通过 API 的 `tools` 参数正式传递，Prompt 中冗长描述反而可能干扰模型 |
| 工具行参数展示粒度 | 只展示关键参数（如 path、cmd），省略默认值和冗长内容 | 保持工具行简洁，结果摘要在下方展示 |

---

## 自检

- [x] **spec 覆盖**：F1~F11 每条在架构中都有归属
  - F1（Tool 接口）→ `tool.base.Tool`
  - F2（六个工具）→ `tool.file_ops`、`tool.shell`、`tool.search`
  - F3（注册中心）→ `tool.registry.Registry`
  - F4（超时）→ `tool` 层 `asyncio.wait_for`
  - F5（结构化返回+错误处理）→ `ToolResult`，Agent 中统一回灌
  - F6（流式解析）→ `provider/anthropic.py`、`provider/openai.py`
  - F7（单轮闭环）→ `agent.agent.Agent.run`
  - F8（双协议）→ 两个 Provider 适配器
  - F9（TUI 工具行）→ `tui.app.REPL._consume_agent_events`
  - F10（双模式支持）→ `main.py` 中 REPL 和 oneshot 共用 Agent
  - F11（唯一匹配）→ `tool.file_ops.EditFileTool`
- [x] **接口完整性**：光看接口描述，可以独立实现每个模块
- [x] **依赖清晰度**：`tool → llm`（只 import 类型），`agent → {llm,tool,conversation}`，`tui → agent`，无环
- [x] **矛盾检查**：白名单策略与 spec 一致；单轮闭环与"不做 Agent Loop"一致

---

**plan.md 已生成。请 review：**
- 架构划分是否合理？`agent` 层的引入是否解决了闭环复用问题？
- 核心接口定义（`Tool`、`AgentEvent`、`ToolCall`、`Message` 扩展）是否完整？
- `ConversationManager` 保存 `tool_call_id` 的方案是否可行？
- 文件组织是否清晰？
- 技术决策是否认同？有没有遗漏的关键决策？

确认后进入 **task.md** 任务拆解阶段。
