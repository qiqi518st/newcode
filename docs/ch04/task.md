# NewCode Agent Loop — 任务拆解 (task.md)

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `newcode/agent/events.py` | 新增 EventType、TokenUsage、TurnEnd、StopReason |
| 修改 | `newcode/agent/__init__.py` | 导出新类型 |
| 新建 | `newcode/agent/scheduler.py` | ToolScheduler 保序分批并发执行 |
| 重写 | `newcode/agent/agent.py` | ReAct 循环引擎替代单轮闭环 |
| 修改 | `newcode/provider/base.py` | StreamEvent 增 usage；Provider 增 system_suffix |
| 修改 | `newcode/provider/anthropic.py` | 流结束上抛 usage；system_suffix 拼接 |
| 修改 | `newcode/provider/openai.py` | stream_options include_usage；usage 上抛；system_suffix |
| 修改 | `newcode/tools/base.py` | Tool Protocol 增 read_only |
| 修改 | `newcode/tools/registry.py` | 增 read_only_definitions()、is_read_only() |
| 修改 | `newcode/tools/file_ops.py` | 三个工具 read_only 属性 |
| 修改 | `newcode/tools/shell.py` | ExecuteCommandTool read_only 属性 |
| 修改 | `newcode/tools/search.py` | 两个工具 read_only 属性 |
| 修改 | `newcode/conversation/manager.py` | 增 last_role() 和批量写入、取消补位方法 |
| 修改 | `newcode/prompt/resources.py` | 增 PLAN_MODE_REMINDER、EXECUTE_DIRECTIVE；SYSTEM_PROMPT 增补 |
| 修改 | `newcode/config/schema.py` | Config 增 plan_file、default_mode |
| 修改 | `newcode/config/loader.py` | 解析新配置字段 |
| 修改 | `newcode/tui/app.py` | 识别 /plan /do；ESC/Ctrl+C 拆分；新事件消费；状态栏/动态区 |
| 修改 | `newcode/main.py` | -p/--plan CLI；_oneshot 新事件处理 |
| 修改 | `tests/*.py` | 更新现有测试，新增 Agent Loop / ToolScheduler / 取消 / Plan Mode 测试 |

---

## T1: 扩展事件系统

**文件：** `newcode/agent/events.py`
**依赖：** 无

**步骤：**
1. 新增 `TokenUsage` dataclass，字段 `input_tokens: int`、`output_tokens: int`
2. 新增 `TurnEnd` dataclass，字段 `turn: int`、`tool_call_count: int`、`token_usage: TokenUsage`
3. 新增 `StopReason` 枚举：NATURAL、MAX_TURNS、CANCELLED、CONSECUTIVE_UNKNOWN_TOOLS、STREAM_ERROR
4. 在 `EventType` 枚举中新增 `TOKEN_USAGE`、`TURN_START`、`TURN_END`
5. 扩展 `Event.payload` 类型标注为 `str | ToolCall | ToolResult | TokenUsage | TurnEnd | StopReason | Exception`

**验证：** `python -c "from newcode.agent.events import TokenUsage, TurnEnd, StopReason, EventType; print(EventType.TOKEN_USAGE)"` 无报错

---

## T2: Tool 协议扩展 + 六个工具实现

**文件：** `newcode/tools/base.py`、`newcode/tools/file_ops.py`、`newcode/tools/shell.py`、`newcode/tools/search.py`
**依赖：** 无

**步骤：**
1. 在 `tools/base.py` 的 `Tool` Protocol 中新增 `read_only: bool` 属性
2. 在 `file_ops.py`：`ReadFileTool.read_only = True`，`WriteFileTool.read_only = False`，`EditFileTool.read_only = False`
3. 在 `shell.py`：`ExecuteCommandTool.read_only = False`
4. 在 `search.py`：`ListFilesTool.read_only = True`，`SearchCodeTool.read_only = True`

**验证：** `python -c "from newcode.tools import Registry; r = Registry.default(); print([(t.name, t.read_only) for t in [r.get(n) for n in ['read_file','write_file','list_files','execute_command']]])"` 输出正确的 read_only 值

---

## T3: Registry 扩展

**文件：** `newcode/tools/registry.py`
**依赖：** T2

**步骤：**
1. 新增 `read_only_definitions() → list[ToolDefinition]` 方法，仅返回 `read_only=True` 的工具定义
2. 新增 `is_read_only(name: str) → bool` 方法，查询指定工具是否只读；工具不存在时返回 `False`

**验证：**
```python
r = Registry.default()
assert len(r.read_only_definitions()) == 3  # read_file, list_files, search_code
assert r.is_read_only("read_file") is True
assert r.is_read_only("write_file") is False
```

---

## T4: Provider 基类扩展

**文件：** `newcode/provider/base.py`
**依赖：** T1

**步骤：**
1. 在 `StreamEvent` dataclass 中新增 `usage: TokenUsage | None = None` 字段
2. 在 `Provider` Protocol 的 `stream()` 方法签名中新增 `system_suffix: str = ""` 形参
3. 更新 `new_provider()` 无影响（仅签名变更，调用方暂不传 system_suffix）

**验证：** `python -c "from newcode.provider.base import StreamEvent, Provider; print('ok')"` 无报错

---

## T5: Anthropic Provider 适配器扩展

**文件：** `newcode/provider/anthropic.py`
**依赖：** T4

**步骤：**
1. `stream()` 方法新增 `system_suffix` 形参
2. 构建 system 消息时：`content = base_system_prompt + system_suffix`（系统提示在 `ConversationManager.get_context()` 中已包含，此处处理 `system_suffix` 拼接到 system 消息末尾）
3. 在流式消费中捕获 `message_stop` 事件的 `usage` 字段，提取 `input_tokens` 和 `output_tokens`
4. 在流结束前最后一个 `StreamEvent` 设置 `usage = TokenUsage(input_tokens, output_tokens)`

**验证：** 现有测试通过（`pytest tests/ -k anthropic`，如有）

---

## T6: OpenAI Provider 适配器扩展

**文件：** `newcode/provider/openai.py`
**依赖：** T4

**步骤：**
1. `stream()` 方法新增 `system_suffix` 形参
2. 构建 system 消息时：`content = base_system_prompt + system_suffix`
3. 请求参数增加 `stream_options={"include_usage": True}`
4. 在流式消费的最后一个 chunk 中提取 `usage` 字段，设置 `StreamEvent.usage`

**验证：** 现有测试通过（`pytest tests/ -k openai`，如有）

---

## T7: ConversationManager 扩展

**文件：** `newcode/conversation/manager.py`
**依赖：** T1

**步骤：**
1. 新增 `last_role() → str | None` 方法：返回 `_messages[-1].role` 或 `None`（空列表时）
2. 新增 `add_assistant_with_tool_calls(text: str, tool_calls: list[ToolCall])` 方法：
   - 构造 `tool_calls` 列表（协议无关格式），写入 assistant 消息
   - 同时写入 `tool_call_id`、`tool_use_id` 等回灌所需字段
3. 新增 `add_tool_results(results: list[tuple[ToolCall, ToolResult]])` 方法：
   - 按序追加 tool 角色结果消息
4. 新增 `add_cancelled_tool_result(tool_call: ToolCall)` 方法：
   - 写入 `output="已取消"` 的 tool 结果消息，确保配对

**验证：**
```python
from newcode.conversation.manager import ConversationManager
from newcode.provider.base import ToolCall, ToolResult

cm = ConversationManager("test", 10)
cm.add_user("hi")
cm.add_assistant("hello")
assert cm.last_role() == "assistant"
tc = ToolCall(tool_name="read_file", arguments={"path": "x"}, tool_use_id="id1")
cm.add_assistant_with_tool_calls("let me check", [tc])
cm.add_cancelled_tool_result(tc)
assert cm.last_role() == "tool"
```

---

## T8: Prompt 扩展

**文件：** `newcode/prompt/resources.py`
**依赖：** 无

**步骤：**
1. 新增 `PLAN_MODE_REMINDER` 常量：计划模式系统提示后缀，告知模型当前处于计划模式，只能使用只读工具探查代码，产出可执行计划文档，不要修改文件或执行命令
2. 新增 `EXECUTE_DIRECTIVE` 模板字符串：包含 `{plan}` 占位符，指示模型按照计划执行
3. 在 `SYSTEM_PROMPT` 中增补 Agent 循环行为约定："持续工作直到任务完成。你可以连续调用多个工具，无需等待用户确认。如果一次需要多个信息，可以同时调用多个只读工具。"

**验证：** `python -c "from newcode.prompt.resources import PLAN_MODE_REMINDER, EXECUTE_DIRECTIVE; print('ok')"` 无报错

---

## T9: Config 扩展

**文件：** `newcode/config/schema.py`、`newcode/config/loader.py`
**依赖：** 无

**步骤：**
1. 在 `schema.py` 的 `Config` dataclass 中新增：
   - `plan_file: str = "plan.md"`
   - `default_mode: str = "normal"`
2. 在 `loader.py` 的 `load()` 函数中解析 `.newcode.yaml` 的 `plan_file` 和 `default_mode` 字段（可选，有默认值）
3. 可选：`load_ccswitch()` 也支持 `default_mode`（CC Switch 配置中如有 `defaultMode` 字段则读取）

**验证：** 创建临时 `.newcode.yaml` 含 `plan_file: "my_plan.md"` 和 `default_mode: "plan"`，加载后 `config.plan_file == "my_plan.md"` 且 `config.default_mode == "plan"`

---

## T10: ToolScheduler 实现

**文件：** `newcode/agent/scheduler.py`（新建）
**依赖：** T2、T3

**步骤：**
1. 定义 `ScheduledResult` dataclass：`tool_call: ToolCall`、`result: ToolResult`
2. 实现 `ToolScheduler` 类：
   - `__init__(self, registry: Registry)`
   - `async schedule(self, tool_calls: list[ToolCall]) → list[ScheduledResult]`
3. 调度逻辑：
   - 遍历 tool_calls，按 `registry.is_read_only(name)` 分组，保留原始下标
   - 预分配 `results = [None] * len(tool_calls)`
   - 并发组：`asyncio.gather(*(_execute_one(tc, idx) for idx, tc in concurrent))`
   - 串行组：`for idx, tc in serial: results[idx] = await _execute_one(tc, idx)`
   - `_execute_one` 内部调用 `registry.execute()`，返回 `ScheduledResult`
   - 过滤 None 并返回
4. 错误处理：单个工具失败不影响同组其他工具

**验证：**
```python
import asyncio
from newcode.tools import Registry
from newcode.agent.scheduler import ToolScheduler
from newcode.provider.base import ToolCall


async def test():
    r = Registry.default()
    s = ToolScheduler(r)
    calls = [
        ToolCall(tool_name="read_file", arguments={"path": "setup.py"}),
        ToolCall(tool_name="list_files", arguments={"pattern": "*.py"}),
    ]
    results = await s.schedule(calls)
    assert len(results) == 2
    assert results[0].tool_call == calls[0]
    assert results[0].result.status == "ok"


asyncio.run(test())
```

---

## T11: Agent 循环引擎重写

**文件：** `newcode/agent/agent.py`
**依赖：** T1、T3、T4、T7、T10

**步骤：**
1. 定义内置常量 `MAX_AGENT_TURNS = 10`
2. 重写 `Agent` 类：
   - `__init__(self, provider, conversation, registry)` — 不变
   - 新增 `_cancelled: asyncio.Event` 属性
   - 新增 `cancel()` 方法 — `self._cancelled.set()`
3. 重写 `run(self, user_input, mode="normal", plan_content="")` 方法：
   - 保留 ch03 的 `conversation.add_user(user_input)` 开头
   - 如果 `mode == "execute"` 且有 `plan_content`：注入 `EXECUTE_DIRECTIVE` 格式的 user 消息
   - 根据 mode 选择 tool_defs（`"plan"` → `read_only_definitions()`，否则 → `to_definitions()`）
   - 根据 mode 设置 system_suffix（`"plan"` → `PLAN_MODE_REMINDER`）
   - `for turn in range(MAX_AGENT_TURNS):` 循环体
   - 消费 `provider.stream()` 时：TEXT 实时 yield，收集到 buffer；tool_call 收集到列表；err 则 yield ERROR + DONE(STREAM_ERROR)
   - 流结束后 yield TOKEN_USAGE
   - tool_calls 为空 → yield DONE(NATURAL)
   - 分类已知/未知工具；连续未知计数逻辑
   - 调用 `ToolScheduler.schedule()` 执行已知工具
   - 每个工具 yield TOOL_CALL + TOOL_RESULT
   - 写入 conversation：`add_assistant_with_tool_calls()` + `add_tool_results()`
   - yield TURN_END
   - 检查 `_cancelled`：补 `add_cancelled_tool_result()`，yield DONE(CANCELLED)
   - 达到上限：yield DONE(MAX_TURNS)
4. **保持向后兼容：** `run(user_input)` 默认 mode="normal"，行为与 ch03 语义一致（只是现在可以循环多轮）

**验证：** 现有测试 `tests/test_agent.py` 通过（如有），`python -c "from newcode.agent import Agent; print('ok')"` 无报错

---

## T12: Agent 包导出更新

**文件：** `newcode/agent/__init__.py`
**依赖：** T1、T10、T11

**步骤：**
1. 更新导出列表：`Agent`、`Event`、`EventType`、`StopReason`、`TokenUsage`、`TurnEnd`、`ToolScheduler`

**验证：** `python -c "from newcode.agent import Agent, Event, EventType, StopReason, TokenUsage, TurnEnd, ToolScheduler; print('ok')"` 无报错

---

## T13: TUI 扩展

**文件：** `newcode/tui/app.py`
**依赖：** T11、T12

**步骤：**
1. **斜杠命令识别：** 在 `_process_input()` 或 `submit` 中检查 `text.startswith("/plan ")` 或 `text == "/do"`：
   - `/plan <任务>` → 提取任务文本，以 `mode="plan"` 调用 `agent.run()`
   - `/do` → 读取 `plan_file`（默认 `plan.md`），以 `mode="execute"` + `plan_content` 调用 `agent.run()`
2. **按键处理拆分：**
   - 流式态：ESC 或 Ctrl+C → 调用 `agent.cancel()`，等待 `DONE(CANCELLED)` 后回到空闲态
   - 空闲态：Ctrl+C 或 ESC → 退出程序
3. **事件泵处理：**
   - `TOKEN_USAGE` → 累加 `_session_in_tokens`、`_session_out_tokens`
   - `TURN_START` → 更新动态区显示 "Turn N/10"
   - `TURN_END` → 更新动态区
   - `TOOL_CALL` → 渲染 `● tool_name(params)` 工具行
   - `TOOL_RESULT` → 渲染结果摘要，`status=="error"` 时红色
   - `DONE` → 根据 `StopReason` 展示不同提示
   - `ERROR` → 红色错误
4. **状态栏：** 在 `bottom_toolbar` 中展示模式标识（`[plan]`/`[normal]`）和累计 token 用量
5. **动态区：** 流式态显示当前迭代轮次

**验证：** `python -c "from newcode.tui.app import REPL; print('ok')"` 无报错；现有 TUI 测试通过

---

## T14: main.py 扩展

**文件：** `newcode/main.py`
**依赖：** T11、T12

**步骤：**
1. 新增 `-p`/`--plan` CLI 参数：单次调用以计划模式运行
2. `_oneshot()` 函数处理新增事件类型：
   - `TOKEN_USAGE`：忽略或简洁打印
   - `TURN_START`/`TURN_END`：忽略
   - `DONE`：打印终止原因（非 NATURAL 时）
3. TUI 模式启动时，根据 `config.default_mode` 设置初始模式

**验证：** `python -m newcode --version` 正常输出；`python -m newcode --help` 显示 `-p` 参数

---

## T15: 测试更新

**文件：** `tests/test_agent.py`（修改）、`tests/test_tools.py`（修改）、`tests/test_tui_wiring.py`（修改）
**依赖：** T11、T12、T13

**步骤：**
1. 更新 `test_tools.py`：验证 `read_only` 属性值正确；验证 `Registry.read_only_definitions()` 返回 3 个工具；验证 `is_read_only()`
2. 新增 `test_scheduler.py`（或并入 `test_tools.py`）：
   - 测试并发执行（多个只读工具同时执行）
   - 测试串行执行（读写工具顺序执行）
   - 测试混合执行（只读并发 + 读写串行）
   - 测试工具失败时其他工具继续执行
3. 更新 `test_agent.py`：
   - 测试自然终止（无工具调用）
   - 测试多轮工具调用
   - 测试达到迭代上限
   - 测试取消（通过 `agent.cancel()`）
   - 测试取消后历史完整性（last_role 配对）
   - 测试连续未知工具停止
   - 测试流式错误
   - 测试 Plan Mode 只有只读工具
   - 测试 event 流包含 TOKEN_USAGE、TURN_START、TURN_END
4. 更新 `test_tui_wiring.py`：验证新事件类型被 TUI 正确消费

**验证：** `pytest tests/ -v` 全部通过

---

## 执行顺序

```
T1 (events) ─────────────────────────────┐
T2 (tool read_only) ──→ T3 (registry) ──┤
T4 (provider base) ──→ T5 (anthropic) ──┤
                       T6 (openai) ──────┤
T8 (prompt) ─────────────────────────────┤
T9 (config) ─────────────────────────────┤
T7 (conversation) ───────────────────────┤
                                          ├─→ T10 (scheduler) ──┐
                                          │                     │
                                          ├─→ T11 (agent loop) ─┤
                                          │                     │
                                          └─────────────────────┤
                                                                │
                                          T12 (agent exports) ──┤
                                                                │
                                                                ▼
                                          T13 (tui) ──→ T14 (main) ──→ T15 (tests)
```

T1–T9 可并行推进（各自独立）。T10 依赖 T2/T3。T11 依赖 T1/T3/T4/T7/T10。T12 依赖 T1/T10/T11。T13/T14 依赖 T11/T12。T15 最后。