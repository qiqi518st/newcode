# MewCode 工具系统 — 任务拆解 (task.md)

> 基于已批准的 spec.md 和 plan.md。Python 3.12+。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `mewcode/provider/base.py` | 扩展 Message/StreamEvent；新增 ToolCall、ToolDefinition、ToolResult |
| 修改 | `mewcode/utils/error.py` | 新增 ToolError、CommandNotAllowedError |
| 新建 | `mewcode/tools/base.py` | Tool Protocol |
| 新建 | `mewcode/tools/registry.py` | Registry 类、default() 工厂 |
| 新建 | `mewcode/tools/file_ops.py` | ReadFileTool、WriteFileTool、EditFileTool |
| 新建 | `mewcode/tools/shell.py` | ExecuteCommandTool（含白名单） |
| 新建 | `mewcode/tools/search.py` | ListFilesTool、SearchCodeTool |
| 新建 | `mewcode/tools/__init__.py` | 包导出 |
| 修改 | `mewcode/conversation/manager.py` | 新增 add_tool_call、add_tool_result |
| 修改 | `mewcode/prompt/resources.py` | SYSTEM_PROMPT 增补 Agent 角色与工具约定 |
| 修改 | `mewcode/provider/anthropic.py` | 注入 tools、解析 tool_use streaming、回灌 tool_result |
| 修改 | `mewcode/provider/openai.py` | 注入 tools、解析 function_call streaming、回灌 function 结果 |
| 新建 | `mewcode/agent/events.py` | EventType、Event |
| 新建 | `mewcode/agent/agent.py` | Agent 类、单轮闭环编排 |
| 新建 | `mewcode/agent/__init__.py` | 包导出 |
| 修改 | `mewcode/tui/app.py` | _process_input 改走 agent.run、消费 AgentEvent |
| 修改 | `mewcode/main.py` | 构造 Registry.default() 注入 REPL/oneshot |
| 新建 | `tests/test_tools.py` | 工具单元测试 |
| 新建 | `tests/test_agent.py` | Agent 闭环测试（mock provider + mock tool） |
| 新建 | `tests/test_provider_tools.py` | Provider 工具解析测试 |
| 新建 | `tests/test_conversation_tools.py` | ConversationManager 工具消息测试 |

---

## T1: Provider 基础类型扩展

**文件：** `mewcode/provider/base.py`
**依赖：** 无
**步骤：**
1. `Message` 增加 `role="tool"`，新增字段 `tool_call_id: str | None = None`、`name: str | None = None`
2. `StreamEvent` 新增字段 `tool_call: ToolCall | None = None`
3. 新增 `ToolCall` dataclass：`tool_name: str`, `arguments: dict`
4. 新增 `ToolDefinition` dataclass：`name: str`, `description: str`, `parameters: dict`
5. 新增 `ToolResult` dataclass：`status: Literal["ok","error"]`, `output: str`, `error: str`, `truncated: bool`
6. `Provider.stream` 签名扩展为 `stream(self, msgs, tools=None)`，现有调用方不受影响（新增参数有默认值）

**验证：** `python -c "from mewcode.provider.base import Message, StreamEvent, ToolCall, ToolDefinition, ToolResult; print('OK')"`

---

## T2: 工具相关异常类型

**文件：** `mewcode/utils/error.py`
**依赖：** 无
**步骤：**
1. 新增 `ToolError(Exception)`：工具执行失败的基类
2. 新增 `CommandNotAllowedError(ToolError)`：命令不在白名单
3. 新增 `PathTraversalError(ToolError)`：路径越界
4. 新增 `ToolTimeoutError(ToolError)`：工具执行超时

**验证：** `python -c "from mewcode.utils.error import ToolError, CommandNotAllowedError; print('OK')"`

---

## T3: Tool 协议定义

**文件：** `mewcode/tools/base.py`
**依赖：** T1（需要 `ToolResult`）
**步骤：**
1. `from mewcode.provider.base import ToolResult`
2. 定义 `Tool` Protocol：`name`、`description`、`parameters`、`execute(self, arguments) -> ToolResult`
3. 所有属性均为 `property`，`execute` 为 `async`

**验证：** `python -c "from mewcode.tools.base import Tool; print('OK')"`

---

## T4: 工具注册中心

**文件：** `mewcode/tools/registry.py`
**依赖：** T3
**步骤：**
1. `from mewcode.provider.base import ToolDefinition`
2. 实现 `Registry` 类：
   - `_tools: dict[str, Tool]` 存储注册的工具
   - `register(tool)`：按 `tool.name` 注册
   - `get(name)`：按名查找，无则返回 `None`
   - `to_definitions()`：将每个 Tool 转为 `ToolDefinition`（name/description/parameters），返回列表
   - `execute(name, arguments)`：查找工具并 `await tool.execute(arguments)`
   - `@staticmethod default()`：返回预装六个核心工具的 Registry（此时六个工具类尚未实现，先用 `pass` 占位或 TODO 注释）

**验证：** `python -c "from mewcode.tools.registry import Registry; r = Registry(); print(type(r))"`

---

## T5: 文件操作工具 — read_file

**文件：** `mewcode/tools/file_ops.py`
**依赖：** T2, T3
**步骤：**
1. 实现 `ReadFileTool`：
   - `name = "read_file"`
   - `parameters` JSON Schema：`path`(str, required)，`offset`(int)，`limit`(int)
   - `execute`：路径安全检查（abspath + cwd 前缀检查）→ 读文件 → 可选 offset/limit 切片 → 超过 500 行截断（`truncated=True`）→ 返回 `ToolResult`
   - 异常：文件不存在 → `status="error", error="文件不存在: {path}"`；路径越界 → `PathTraversalError`

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "
import asyncio
from mewcode.tools.file_ops import ReadFileTool
async def test():
    t = ReadFileTool()
    r = await t.execute({'path': 'mewcode/main.py'})
    print(r.status, len(r.output))
asyncio.run(test())
"`

---

## T6: 文件操作工具 — write_file

**文件：** `mewcode/tools/file_ops.py`
**依赖：** T5（同文件追加）
**步骤：**
1. 在 `file_ops.py` 中追加 `WriteFileTool`：
   - `name = "write_file"`
   - `parameters`：`path`(str, required)，`content`(str, required)
   - `execute`：路径安全检查 → `os.makedirs(..., exist_ok=True)` → 写入 → 返回确认信息
   - 异常：路径越界、权限不足 → `status="error"`

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "
import asyncio, os
from mewcode.tools.file_ops import WriteFileTool
async def test():
    t = WriteFileTool()
    r = await t.execute({'path': '/tmp/mew_test_write.txt', 'content': 'hello'})
    print(r.status, os.path.exists('/tmp/mew_test_write.txt'))
asyncio.run(test())
"`

---

## T7: 文件操作工具 — edit_file

**文件：** `mewcode/tools/file_ops.py`
**依赖：** T6（同文件追加）
**步骤：**
1. 在 `file_ops.py` 中追加 `EditFileTool`：
   - `name = "edit_file"`
   - `parameters`：`path`(str)，`old_string`(str)，`new_string`(str)
   - `execute`：路径安全检查 → 读文件 → `content.count(old_string)` → 
     - `== 0` → `error="old_string 在文件中未找到"`
     - `> 1` → `error="old_string 在文件中找到 {n} 处，无法确定替换哪一处"`
     - `== 1` → `content.replace(old_string, new_string, 1)` → 写回 → 返回确认

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "
import asyncio
from mewcode.tools.file_ops import EditFileTool
async def test():
    t = EditFileTool()
    # 测试唯一匹配成功
    r = await t.execute({'path': '/tmp/mew_test_write.txt', 'old_string': 'hello', 'new_string': 'world'})
    print('ok:', r.status)
    # 测试匹配多次
    r2 = await t.execute({'path': '/tmp/mew_test_write.txt', 'old_string': 'x', 'new_string': 'y'})
    print('not_found:', r2.status, r2.error)
asyncio.run(test())
"`

---

## T8: Shell 命令工具

**文件：** `mewcode/tools/shell.py`
**依赖：** T2, T3
**步骤：**
1. 实现 `ExecuteCommandTool`：
   - `name = "execute_command"`
   - `parameters`：`command`(str)，`cwd`(str, 默认当前目录)
   - 白名单集合：`{"ls", "cat", "grep", "find", "python", "pytest", "git", "pwd", "echo", "head", "tail", "wc", "mkdir", "touch"}`
   - `execute`：解析命令第一个 token → 白名单检查 → `asyncio.create_subprocess_exec` + `asyncio.wait_for(60s)` → 收集 stdout/stderr → 超过 10KB 截断
   - 异常：不在白名单 → `CommandNotAllowedError`；超时 → `ToolTimeoutError`；exit_code ≠ 0 → `status="error"` 但返回 stdout/stderr/exit_code（让模型自行判断）

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "
import asyncio
from mewcode.tools.shell import ExecuteCommandTool
async def test():
    t = ExecuteCommandTool()
    r = await t.execute({'command': 'python --version'})
    print('ok:', r.status, r.output.strip())
    r2 = await t.execute({'command': 'rm -rf /'})
    print('deny:', r2.status, r2.error)
asyncio.run(test())
"`

---

## T9: 搜索工具

**文件：** `mewcode/tools/search.py`
**依赖：** T3
**步骤：**
1. 实现 `ListFilesTool`：
   - `name = "list_files"`
   - `parameters`：`pattern`(str)，`cwd`(str)
   - `execute`：`glob.glob(pattern)` → 最多 100 条 → 空列表也返回 `status="ok"`
2. 实现 `SearchCodeTool`：
   - `name = "search_code"`
   - `parameters`：`pattern`(str)，`cwd`(str)，`glob`(str)
   - `execute`：`re.compile(pattern)` → 遍历匹配文件 → 逐行搜索 → 最多 50 条，每条片段 200 字符

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "
import asyncio
from mewcode.tools.search import ListFilesTool, SearchCodeTool
async def test():
    t1 = ListFilesTool()
    r1 = await t1.execute({'pattern': 'mewcode/**/*.py'})
    print('list:', r1.status, len(r1.output.split()))
    t2 = SearchCodeTool()
    r2 = await t2.execute({'pattern': 'class.*Tool', 'glob': 'mewcode/tools/*.py'})
    print('search:', r2.status, 'Tool' in r2.output)
asyncio.run(test())
"`

---

## T10: tools 包导出 + Registry.default() 完成

**文件：** `mewcode/tools/__init__.py`、`mewcode/tools/registry.py`
**依赖：** T4, T5~T9
**步骤：**
1. `__init__.py` 导出 `Tool`、`ToolResult`（从 provider.base 再导出）、`Registry`、`ReadFileTool` 等六个工具类
2. `registry.py` 中完成 `Registry.default()`：实例化并注册六个工具

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "
from mewcode.tools import Registry
r = Registry.default()
print('tools:', [d.name for d in r.to_definitions()])
"`

---

## T11: ConversationManager 扩展工具消息

**文件：** `mewcode/conversation/manager.py`
**依赖：** T1
**步骤：**
1. 新增 `add_tool_call(self, tool_call: ToolCall)`：
   - 将工具调用包装为 assistant 消息，content 存 JSON 描述（如 `调用工具: read_file(...)`），内部保存 `tool_calls` 元数据
2. 新增 `add_tool_result(self, result: ToolResult)`：
   - 追加 `role="tool"` 的消息，content 为 `result.output` 或 `result.error`
3. `get_context()` 保持透传所有消息（含 `role="tool"`）

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "
from mewcode.conversation.manager import ConversationManager
from mewcode.provider.base import ToolCall, ToolResult
cm = ConversationManager('', 20)
cm.add_user('test')
cm.add_tool_call(ToolCall('read_file', {'path': 'x'}))
cm.add_tool_result(ToolResult('ok', 'content'))
msgs = cm.get_context()
print('roles:', [m.role for m in msgs])
"`

---

## T12: System Prompt 增补

**文件：** `mewcode/prompt/resources.py`
**依赖：** 无
**步骤：**
1. 在现有 `SYSTEM_PROMPT` 末尾追加 Agent 角色与工具使用约定：
   - 你是一个编程助手，可以使用以下工具...
   - 列出六个工具名和用途摘要
   - 强调 `edit_file` 的 old_string 必须恰好出现一次
   - 强调一次只调用一个工具
   - 如果不需要工具，直接回答

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "from mewcode.prompt.resources import SYSTEM_PROMPT; print('tool' in SYSTEM_PROMPT.lower())"`

---

## T13: Anthropic Provider 工具扩展

**文件：** `mewcode/provider/anthropic.py`
**依赖：** T1, T11
**步骤：**
1. `stream(self, msgs, tools=None)`：
   - 发送时若 `tools` 不为空，注入 `tools=[{"name":..., "description":..., "input_schema":...}]`
2. 消费流时：
   - 维护 `_current_tool_name` 和 `_partial_json` 缓冲区
   - `content_block_start`（type=tool_use）→ 保存 name，初始化 input
   - `content_block_delta`（type=input_json_delta）→ 拼接 `partial_json`
   - `content_block_stop` → `json.loads()` 组装 `ToolCall`，yield `StreamEvent(tool_call=...)`
   - 普通文本仍 yield `StreamEvent(text=...)`
3. 内部保存 `tool_use_id`，需要在某处传递给 Conversation 用于回灌（通过 ToolCall 扩展或 Agent 层处理）

**验证：** 编写 mock 测试（T22），先保证代码能编译通过：`export PYTHONIOENCODING=utf-8 && python -c "from mewcode.provider.anthropic import AnthropicProvider; print('OK')"`

---

## T14: OpenAI Provider 工具扩展

**文件：** `mewcode/provider/openai.py`
**依赖：** T1, T11
**步骤：**
1. `stream(self, msgs, tools=None)`：
   - 发送时若 `tools` 不为空，注入 `tools=[{"type":"function", "function":{...}}]`
2. 消费流时：
   - 维护 `tool_call_buffers: dict[int, dict]` 按 index 分桶
   - `delta.tool_calls` 每个元素：保存 `id`、`function.name`、`function.arguments` 增量
   - 当某个 index 的 `arguments` 拼接完成 → `json.loads()` 组装 `ToolCall`，yield `StreamEvent(tool_call=...)`
3. 回灌时构造 `{"role":"tool", "tool_call_id":..., "name":..., "content":...}`

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "from mewcode.provider.openai import OpenAIProvider; print('OK')"`

---

## T15: Agent 事件类型

**文件：** `mewcode/agent/events.py`
**依赖：** T1
**步骤：**
1. `EventType` Enum：`TEXT`、`TOOL_CALL`、`TOOL_RESULT`、`DONE`、`ERROR`
2. `Event` dataclass：`type: EventType`, `payload: str | ToolCall | ToolResult | Exception`

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "from mewcode.agent.events import EventType, Event; print([e.value for e in EventType])"`

---

## T16: Agent 单轮闭环编排

**文件：** `mewcode/agent/agent.py`
**依赖：** T1, T3, T4, T10, T11, T15
**步骤：**
1. `Agent.__init__(provider, conversation, registry)`
2. `async def run(self, user_input)`：
   - `conversation.add_user(user_input)`
   - 请求#1：`provider.stream(..., tools=registry.to_definitions())`
   - 消费流：text → yield Event(TEXT)；tool_call → yield Event(TOOL_CALL) → 执行工具 → yield Event(TOOL_RESULT) → 回灌 conversation → break 进入请求#2；done → yield Event(DONE) → return；err → yield Event(ERROR) → return
   - 请求#2：`provider.stream(..., tools=None)`
   - 消费流：text → yield Event(TEXT)；done → yield Event(DONE)；err → yield Event(ERROR)
   - 防御：请求#2 中出现 tool_call 则忽略日志警告，继续消费文本

**验证：** 先编译通过：`export PYTHONIOENCODING=utf-8 && python -c "from mewcode.agent.agent import Agent; print('OK')"`。详细测试在 T21。

---

## T17: agent 包导出

**文件：** `mewcode/agent/__init__.py`
**依赖：** T15, T16
**步骤：**
1. 导出 `Agent`、`EventType`、`Event`

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "from mewcode.agent import Agent, EventType; print('OK')"`

---

## T18: TUI 改走 Agent.run

**文件：** `mewcode/tui/app.py`
**依赖：** T15, T16, T17
**步骤：**
1. `REPL.__init__` 改为接收 `Agent` 而非 `Provider`
2. `_process_input` 不再直接操作 `conv.add_user`，改调 `self.agent.run(text)`
3. 新增 `_consume_agent_events(self, user_input)` 替代 `_consume_stream`：
   - `TEXT` → Rich Live 渲染 Markdown（同 ch02）
   - `TOOL_CALL` → `console.print(f"● {tc.tool_name}(...)", style="bold green")`
   - `TOOL_RESULT` → 展示摘要（成功 dim，错误 red）
   - `DONE` → 定型展示耗时
   - `ERROR` → 红色展示错误
4. 重试逻辑：若请求#1 或请求#2 出错，沿用 ch02 的 3 次重试

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "from mewcode.tui.app import REPL; print('OK')"`

---

## T19: main.py 扩展注入 Registry

**文件：** `mewcode/main.py`
**依赖：** T10, T17, T18
**步骤：**
1. `from mewcode.tools import Registry`
2. `from mewcode.agent import Agent`
3. `main()` 中构造 `registry = Registry.default()`
4. `agent = Agent(provider, conversation, registry)`
5. `REPL` 改为接收 `agent`
6. `_oneshot` 同样构造 `Agent` 并消费 `Event` 流

**验证：** `export PYTHONIOENCODING=utf-8 && python -m mewcode --version` 正常输出版本号

---

## T20: 工具单元测试

**文件：** `tests/test_tools.py`
**依赖：** T5~T10
**步骤：**
1. `test_read_file_success`：读取已知文件，断言内容正确
2. `test_read_file_not_found`：读取不存在的文件，断言 `status="error"`
3. `test_read_file_path_traversal`：传入 `../../etc/passwd`，断言被拒绝
4. `test_write_file_and_read_back`：写文件后读回，内容一致
5. `test_edit_file_unique_match`：唯一匹配替换成功
6. `test_edit_file_no_match`：未找到报错
7. `test_edit_file_multiple_match`：多处匹配报错
8. `test_execute_command_whitelist_allowed`：`python --version` 成功
9. `test_execute_command_whitelist_denied`：`rm -rf /` 被拒绝
10. `test_execute_command_timeout`：用 `sleep 100` 触发超时
11. `test_list_files`：glob 匹配
12. `test_search_code`：正则搜索

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_tools.py -v`

---

## T21: Agent 闭环测试

**文件：** `tests/test_agent.py`
**依赖：** T16, T17
**步骤：**
1. Mock Provider：
   - 第 1 次 stream 返回 `StreamEvent(tool_call=ToolCall(...))` 然后 `done=True`
   - 第 2 次 stream 返回文本增量然后 `done=True`
2. Mock Tool：`execute` 返回 `ToolResult("ok", "mock_result")`
3. 测试 `Agent.run`：
   - 消费 Event 流，断言事件顺序：`TOOL_CALL` → `TOOL_RESULT` → `TEXT` ... → `DONE`
   - 断言 Conversation 中消息序列正确：user → assistant(tool_call) → tool → assistant(text)
4. 测试无工具调用场景：纯文本回复，事件流为 `TEXT` ... → `DONE`

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_agent.py -v`

---

## T22: Provider 工具解析测试

**文件：** `tests/test_provider_tools.py`
**依赖：** T13, T14
**步骤：**
1. Mock Anthropic SDK 流：模拟 `content_block_start` + `content_block_delta` + `content_block_stop` 序列，断言解析出正确的 `ToolCall`
2. Mock OpenAI SDK 流：模拟 `delta.tool_calls` 分片，断言拼接后解析出正确的 `ToolCall`
3. 测试 JSON 分片拼接：参数分 3 个 chunk 到达，断言最终 `json.loads` 成功

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_provider_tools.py -v`

---

## T23: Conversation 工具消息测试

**文件：** `tests/test_conversation_tools.py`
**依赖：** T11
**步骤：**
1. 测试 `add_tool_call` + `add_tool_result` 后，`get_context()` 返回的消息序列 role 正确
2. 测试滑动窗口：加入 tool 消息后，超过 max_turns 时正确裁剪
3. 测试上下文完整性：user → assistant(tool) → tool → assistant(text) 的顺序在 `get_context()` 中保持

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_conversation_tools.py -v`

---

## 执行顺序

```
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10
  └─────────────────────────────────────────────────┘
                      ↓
              T11 → T12 → T13 → T14
                      ↓
              T15 → T16 → T17 → T18 → T19
                      ↓
              T20 → T21 → T22 → T23（可并行）
```

- 基础层：T1~T2（类型/异常）
- 工具层：T3~T10（Tool 协议 + 6 个工具 + Registry）
- 扩展层：T11~T14（Conversation + Prompt + Provider 适配器）
- 编排层：T15~T19（Agent + TUI + main）
- 测试层：T20~T23（可并行执行）

---

## 自检

- [x] **plan 覆盖**：plan.md 的每个组件至少有一个任务（tool 层 T3~T10、agent 层 T15~T17、provider 层 T1,T13,T14、conversation T11、prompt T12、tui T18、main T19）
- [x] **占位符扫描**：无模糊步骤或"类似 TX"引用
- [x] **依赖链**：存在合法执行顺序，无循环依赖
- [x] **验证完整性**：每个任务都有具体的验证命令
- [x] **类型一致性**：函数名/类型名与 plan.md 一致

---

**task.md 已生成，共 23 个任务。请 review：**
- 任务粒度是否合适？（每个 2-5 分钟可完成）
- 依赖关系是否正确？
- 有没有遗漏的实现步骤？（如 provider 回灌 tool_use_id / tool_call_id 的处理是否需要在 task 中体现？）
- 测试覆盖是否充分？

确认后进入 **checklist.md** 验收设计阶段。
