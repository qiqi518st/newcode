# MewCode Agent Loop — 验收清单 (checklist.md)

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] `TokenUsage`、`TurnEnd`、`StopReason` 已定义且可导入（验证：`python -c "from mewcode.agent.events import TokenUsage, TurnEnd, StopReason"` 无报错）
- [ ] `EventType` 包含 `TOKEN_USAGE`、`TURN_START`、`TURN_END`（验证：同上，检查枚举值）
- [ ] `Tool` Protocol 包含 `read_only` 属性（验证：`python -c "from mewcode.tools.base import Tool; print(hasattr(Tool, 'read_only'))"` 输出 True）
- [ ] 六个工具 `read_only` 属性值正确：3 个 True（read_file, list_files, search_code），3 个 False（write_file, edit_file, execute_command）（验证：遍历 Registry.default() 检查）
- [ ] `Registry.read_only_definitions()` 返回 3 个工具定义（验证：调用后检查长度为 3）
- [ ] `Registry.is_read_only(name)` 正确查询（验证：`True` for read_file, `False` for write_file, `False` for 不存在工具）
- [ ] `StreamEvent` 包含 `usage` 字段（验证：`python -c "from mewcode.provider.base import StreamEvent; print(hasattr(StreamEvent, 'usage'))"` 输出 True）
- [ ] `Provider.stream()` 签名包含 `system_suffix` 形参（验证：检查方法签名）
- [ ] Anthropic 适配器流结束后 `StreamEvent.usage` 非空（验证：需要真实 API key 的集成测试，或 mock 验证）
- [ ] OpenAI 适配器请求包含 `stream_options={"include_usage": True}`（验证：mock 检查请求参数）
- [ ] `ConversationManager.last_role()` 返回正确角色（验证：空列表返回 None，有消息返回最后一条的 role）
- [ ] `ConversationManager.add_assistant_with_tool_calls()` 正确写入多 tool_calls（验证：写入后 `last_role() == "assistant"`，消息包含多个 tool_calls）
- [ ] `ConversationManager.add_cancelled_tool_result()` 补入「已取消」结果（验证：写入后配对完整）
- [ ] `PLAN_MODE_REMINDER` 和 `EXECUTE_DIRECTIVE` 已定义（验证：`python -c "from mewcode.prompt.resources import PLAN_MODE_REMINDER, EXECUTE_DIRECTIVE"` 无报错）
- [ ] `SYSTEM_PROMPT` 包含 Agent 循环行为约定（验证：检查字符串包含"持续工作"等关键词）
- [ ] `Config` 包含 `plan_file` 和 `default_mode` 字段（验证：构造 Config 时传入这两个字段不报错）
- [ ] `ToolScheduler` 已实现（验证：`python -c "from mewcode.agent.scheduler import ToolScheduler"` 无报错）
- [ ] `Agent.run()` 支持 `mode` 和 `plan_content` 参数（验证：检查方法签名）
- [ ] `Agent` 有 `cancel()` 方法（验证：`hasattr(agent, 'cancel')`）
- [ ] `Agent.__init__.py` 导出所有新类型（验证：可导入 StopReason, TokenUsage, TurnEnd, ToolScheduler）
- [ ] `main.py` 支持 `-p`/`--plan` 参数（验证：`python -m mewcode --help` 显示）

## 集成

- [ ] Agent 正确调用 Provider.stream() 并传入 `system_suffix`（验证：mock provider 检查调用参数）
- [ ] Agent 在 Plan Mode 下使用 `registry.read_only_definitions()`（验证：mock registry 检查调用）
- [ ] Agent 在 Normal Mode 下使用 `registry.to_definitions()`（验证：mock registry 检查调用）
- [ ] Agent 正确调用 ToolScheduler.schedule()（验证：mock scheduler 检查调用）
- [ ] Agent 正确调用 ConversationManager 的批量写入方法（验证：集成测试检查对话历史）
- [ ] TUI 正确调用 `agent.cancel()` 响应 ESC/Ctrl+C（验证：mock agent 检查 cancel 被调用）
- [ ] TUI 正确消费 TOKEN_USAGE 事件并累加（验证：mock 事件流推入 TUI，检查状态栏）
- [ ] TUI 正确消费 TURN_START 事件并更新动态区（验证：mock 事件流检查显示）
- [ ] TUI 正确识别 `/plan` 和 `/do` 命令（验证：mock 输入检查 agent.run 参数）
- [ ] main.py 正确传递 `config.default_mode` 到 TUI/Agent（验证：检查启动流程）

## 编译与测试

- [ ] `python -m mewcode --version` 正常输出
- [ ] 所有单元测试通过（`pytest tests/ -v`）
- [ ] 无 import 循环依赖（验证：`python -c "import mewcode"` 无报错）

## 端到端场景

- [ ] **场景 1 — ReAct 多轮循环：** 用户输入"读取 main.py 和 mewcode/agent/agent.py，对比两个文件的导入部分，然后创建 analysis.md"，Agent 自动进行多轮工具调用（list_files 或 read_file × 2 → write_file），无需用户逐轮确认。TUI 中可见多轮工具调用行，Turn 计数器递增。

- [ ] **场景 2 — 自然终止：** 用户输入"1+1 等于几"，模型直接回答文本无工具调用，Agent 自然终止。`DONE(NATURAL)` 事件产出。

- [ ] **场景 3 — 迭代上限：** 内置上限 10 轮。用户输入一个需要大量步骤的任务，Agent 在第 10 轮后强制终止，`DONE(MAX_TURNS)` 产出。对话历史保持合法，用户可继续下一轮对话。

- [ ] **场景 4 — 用户取消（流式态）：** Agent 执行多轮工具调用时，用户按 ESC 或 Ctrl+C，Agent 停止后续迭代，`DONE(CANCELLED)` 产出，回到空闲态，不退出程序。

- [ ] **场景 5 — 用户取消（空闲态）：** 空闲态下按 Ctrl+C 或 ESC，程序退出。

- [ ] **场景 6 — 取消后历史合法：** 用户取消后，已发起但未完成的工具调用已补「已取消」结果。输入新问题可继续对话，不会因历史不完整而失败。

- [ ] **场景 7 — 多工具并发（只读）：** 模型一次返回多个只读工具调用（如同时读 3 个文件），TUI 中多个工具行几乎同时出现，而非逐个串行。

- [ ] **场景 8 — 多工具串行（读写）：** 模型同时返回 write_file 和 edit_file 调用，工具按顺序执行，TUI 工具行按顺序出现。

- [ ] **场景 9 — 连续未知工具停止：** 注册表中移除某工具，模型连续 2 次请求，Agent 以 `CONSECUTIVE_UNKNOWN_TOOLS` 终止。对话历史保持合法。

- [ ] **场景 10 — 流式错误不崩溃：** 模拟 Provider 流式错误，Agent 以 `STREAM_ERROR` 终止，TUI 显示错误提示，程序不崩溃，会话不中断。

- [ ] **场景 11 — Token 用量展示：** Agent 多轮运行中，状态栏显示累计 token 用量（如 `Σ in:1.2k out:800`），每次 API 调用后数值更新。

- [ ] **场景 12 — 迭代进度展示：** 多轮工具调用中，流式态动态区显示当前迭代轮次（如 `Turn 2/10`），每轮递增。

- [ ] **场景 13 — Plan Mode 计划产出：** 用户输入 `/plan 分析项目结构并给出重构建议`，Agent 只能使用 read_file/list_files/search_code，无法写文件或执行命令。最终文本写入 `plan.md`。

- [ ] **场景 14 — Plan Mode 执行：** 用户 review `plan.md` 后输入 `/do`，Agent 读取计划内容，恢复全工具集，执行计划中的操作。

- [ ] **场景 15 — 单次调用模式 Agent Loop：** `mewcode -c "读取所有 py 文件并统计行数"` 完整走通多轮工具调用，终端输出工具行和最终回复后退出。

- [ ] **场景 16 — 跨协议一致：** 使用 Anthropic 和 OpenAI 分别测试相同多工具任务，Agent Loop 行为一致（循环轮数、事件类型、终止条件均相同）。

- [ ] **场景 17 — 密钥安全：** API 密钥不出现在对话区、工具输出、状态栏或任何输出中。

- [ ] **场景 18 — 结果截断：** 工具返回大文件/长输出时，内容被截断并标记 `[truncated]`，多轮累积下界面不被撑爆。

- [ ] **场景 19 — 正常路径历史完整性：** 多轮工具调用后，对话历史中 assistant 消息与 tool 结果消息配对完整，角色交替正常，无脏数据。

---

## 自检

- [x] spec 对齐 — spec.md 的每条验收标准（AC1–AC18）都有对应的 checklist 条目
- [x] 可观测性 — 每项都是「运行 X，看到 Y」或「运行 X，期望 Y」，不依赖逐行读代码
- [x] 耦合测试 — 条目聚焦行为，不涉及具体文件名或行号
- [x] 端到端 — 包含 19 个端到端场景，覆盖正常路径、异常路径、Plan Mode、跨协议一致性