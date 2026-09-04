# NewCode 结构化 System Prompt 与 Prompt Cache — 任务拆解 (task.md)

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `newcode/prompt/sections.py` | 七个固定模块 + 可选模块（含优先级） |
| 新建 | `newcode/prompt/builder.py` | Section、PromptBuilder、build() |
| 新建 | `newcode/prompt/assembler.py` | PromptPayload、assemble_payload（三通道分发） |
| 新建 | `newcode/prompt/env.py` | collect_env、format_env |
| 新建 | `newcode/prompt/reminders.py` | system_reminder、PLAN_MODE_FULL/LEAN、plan_mode_reminder |
| 修改 | `newcode/prompt/resources.py` | 迁出 SYSTEM_PROMPT/PLAN_MODE_REMINDER；保留 banner/EXECUTE_DIRECTIVE |
| 修改 | `newcode/provider/base.py` | TokenUsage 扩展缓存字段；Provider.stream 改收 PromptPayload |
| 修改 | `newcode/provider/anthropic.py` | PromptPayload 翻译 + cache_control + 缓存字段解析 |
| 修改 | `newcode/provider/openai.py` | PromptPayload 翻译 + cached_tokens 解析 |
| 修改 | `newcode/agent/agent.py` | 用 assembler 组装；按轮注入 reminders；删 system_suffix |
| 修改 | `newcode/conversation/manager.py` | 删 system_prompt 参数；get_context 返回纯历史 |
| 修改 | `newcode/tools/file_ops.py` | read/write/edit description 强化 |
| 修改 | `newcode/tools/search.py` | list/search description 强化 |
| 修改 | `newcode/tools/shell.py` | execute_command description 强化 |
| 修改 | `newcode/main.py` | 构建 PromptBuilder、collect_env、改 Agent/ConversationManager 构造 |
| 修改 | `newcode/__init__.py` | 版本 0.4.5 → 0.5.0 |
| 修改 | `pyproject.toml` | 版本 0.4.5 → 0.5.0 |
| 新建 | `tests/test_builder.py` | 优先级拼装、可选模块追加 |
| 新建 | `tests/test_assembler.py` | 三通道分发、缓存一致性校验 |
| 新建 | `tests/test_env.py` | 采集、git 降级、格式化 |
| 新建 | `tests/test_reminders.py` | system-reminder 格式、PlanMode 注入频率 |
| 新建 | `tests/test_cache_usage.py` | 缓存字段解析（Anthropic/OpenAI mock usage） |
| 新建 | `scripts/eval_scenarios.py` | F7 五个典型场景评估脚本 |
| 修改 | `tests/test_agent.py` | MockProvider 适配新签名；保留 ch04 全部用例 |
| 修改 | `tests/test_conversation_tools.py` | 适配 ConversationManager 新签名 |
| 修改 | `tests/test_tui_wiring.py` | 适配 Agent 构造签名 |

## T1: 版本号升级到 0.5.0

**文件：** `newcode/__init__.py`、`pyproject.toml`
**依赖：** 无
**步骤：**
1. `newcode/__init__.py` 的 `__version__` 改为 `"0.5.0"`
2. `pyproject.toml` 的 `version` 改为 `0.5.0`

**验证：** `python -c "import newcode; print(newcode.__version__)"` 输出 0.5.0

## T2: TokenUsage 扩展缓存字段

**文件：** `newcode/provider/base.py`
**依赖：** 无
**步骤：**
1. `TokenUsage` 增加字段 `cache_creation_input_tokens: int = 0` 和 `cache_read_input_tokens: int = 0`
2. 确认不破坏现有 `TokenUsage(input_tokens, output_tokens)` 调用（默认值保证向后兼容）

**验证：** `TokenUsage(10, 5)` 仍可用，`TokenUsage(10, 5).cache_read_input_tokens == 0`

## T3: 六个工具 description 强化

**文件：** `newcode/tools/file_ops.py`、`newcode/tools/search.py`、`newcode/tools/shell.py`
**依赖：** 无
**步骤：**
1. `ReadFileTool.description`：强调定位后用 read 精读；配合 search_code
2. `EditFileTool.description`：强调先读后改（改前必须已 read）
3. `WriteFileTool.description`：强调新建/整体覆盖，目录自动创建
4. `ExecuteCommandTool.description`：强调优先专用工具而非 shell，白名单限制
5. `ListFilesTool.description`：强调优于 shell 手写 ls
6. `SearchCodeTool.description`：强调优于 shell 手写 grep
7. 只改 description 字符串与 parameters 辅助描述，**不改 execute 逻辑**

**验证：** 每个工具 `description` 属性含用法 + 配合关系关键词（如"优先""先读""配合"）

## T4: sections.py 七个固定模块 + 可选模块

**文件：** `newcode/prompt/sections.py`（新建）
**依赖：** 无
**步骤：**
1. 定义 `fixed_sections()`，返回七个 `Section`（priority 1-7）：
   角色设定（你是谁）→ 行为准则（怎么跟用户交互）→ 工具使用指南（怎么选/用工具）→ 代码质量规范（代码该写成什么样）→ 安全边界（绝不能做）→ 任务执行模式（不同任务怎么处理）→ 输出风格（回复格式长度）
2. 内容参考原 `SYSTEM_PROMPT` 拆分：身份、工具指南（含"持续工作直到任务完成"）、运行模式说明分别归入对应模块
3. 工具使用指南模块内容包含「优先用专用工具而非 shell」「编辑前必先读」两条约定（与 T3 的 description 双重表述，F4）
4. 定义 `optional_sections(system_prompt: str)`：非空时生成「自定义指令」Section（priority=10，追加语义）；Skill/长期记忆为预留位不产出

**验证：** `fixed_sections()` 返回 7 个 Section，priority 依次 1-7；`optional_sections("")` 返回空，`optional_sections("自定义")` 返回 1 个 priority=10

## T5: builder.py Section + PromptBuilder

**文件：** `newcode/prompt/builder.py`（新建）
**依赖：** T4
**步骤：**
1. 定义 `@dataclass Section`：`name: str`、`content: str`、`priority: int`
2. 定义 `PromptBuilder`：`__init__(sections)`、`add(section)`、`build()`
3. `build()` 按 priority 升序排序拼装，模块间空行分隔，返回完整稳定系统提示

**验证：** `PromptBuilder([Section("a","A",2), Section("b","B",1)]).build()` 输出 "B\n\nA"

## T6: test_builder.py

**文件：** `tests/test_builder.py`（新建）
**依赖：** T5
**步骤：**
1. 测试优先级拼装顺序（乱序输入、升序输出）
2. 测试同优先级按注册顺序
3. 测试可选模块追加在固定模块之后（自定义指令 priority=10）

**验证：** `pytest tests/test_builder.py -q` 全绿

## T7: reminders.py system-reminder + PlanMode 提醒

**文件：** `newcode/prompt/reminders.py`（新建）
**依赖：** 无
**步骤：**
1. 定义 `system_reminder(content) -> Message`：`role="user"`，内容 `<system-reminder>{content}</system-reminder>`
2. 定义 `PLAN_MODE_FULL`（沿用现 `PLAN_MODE_REMINDER` 内容：只读、产出结构化计划、格式要求、slug 声明）
3. 定义 `PLAN_MODE_LEAN`（一行精简版：「【计划模式】只读：仅用只读工具探查并产出计划文档，不修改文件、不执行命令。」）
4. 定义 `plan_mode_reminder(turn: int) -> Message`：`turn==0 or turn%5==0` 返回完整版，否则精简版

**验证：** `plan_mode_reminder(0)` 与 `plan_mode_reminder(5)` 含完整版内容，`plan_mode_reminder(3)` 为精简版；消息 role=user 且含 `<system-reminder>` 标签

## T8: test_reminders.py

**文件：** `tests/test_reminders.py`（新建）
**依赖：** T7
**步骤：**
1. 测试 `<system-reminder>` 标签包裹格式
2. 测试注入频率：turn 0/5 完整，turn 1-4 精简
3. 测试消息不写入历史（Message 对象独立，调用方负责不持久）

**验证：** `pytest tests/test_reminders.py -q` 全绿

## T9: env.py 环境信息采集与格式化

**文件：** `newcode/prompt/env.py`（新建）
**依赖：** 无
**步骤：**
1. 定义 `@dataclass EnvContext`：cwd/platform/datetime/timezone/git_branch/git_dirty/version/provider/model（git 字段可为 None）
2. `collect_env(cwd, version, provider, model)`：同步取 cwd/platform/datetime/timezone；git 状态用子进程带超时（1s），失败/超时降级为 None（N12）
3. `format_env(env)`：拼成一段文本，缺失项省略该行

**验证：** 正常环境返回完整文本；模拟 git 命令失败/超时返回 None 且不抛异常；format_env 缺失项跳过

## T10: test_env.py

**文件：** `tests/test_env.py`（新建）
**依赖：** T9
**步骤：**
1. 测试 collect_env 字段齐全（mock platform/time）
2. 测试 git 失败降级（monkeypatch 子进程抛错）
3. 测试 format_env 缺失项省略

**验证：** `pytest tests/test_env.py -q` 全绿

## T11: assembler.py PromptPayload + assemble_payload

**文件：** `newcode/prompt/assembler.py`（新建）
**依赖：** T2、T5
**步骤：**
1. 定义 `@dataclass PromptPayload`：stable_prompt/env_segment/messages/reminders/tools
2. `assemble_payload(stable, env, history, reminders, tools) -> PromptPayload`
3. 加入**跨轮逐字节一致校验**：记录上次 stable 哈希，变化时日志告警（N8）

**验证：** 返回的 PromptPayload 字段正确路由；连续两次同 stable 不告警，stable 变化时日志告警

## T12: test_assembler.py

**文件：** `tests/test_assembler.py`（新建）
**依赖：** T11
**步骤：**
1. 测试三通道分类（stable→段1、env→段2、history→messages、reminders→messages、tools）
2. 测试缓存一致性校验（同 stable 不告警 / 变化告警）

**验证：** `pytest tests/test_assembler.py -q` 全绿

## T13: resources.py 清理

**文件：** `newcode/prompt/resources.py`
**依赖：** T4、T7
**步骤：**
1. 删除 `SYSTEM_PROMPT`（内容已拆入 sections.py）与 `PLAN_MODE_REMINDER`（内容移入 reminders.py）
2. 保留 `EXECUTE_DIRECTIVE`、`render_banner`、`DOG_BANNER`
3. 检查无残留 import 引用 `SYSTEM_PROMPT` / `PLAN_MODE_REMINDER`

**验证：** `grep -rn "SYSTEM_PROMPT\|PLAN_MODE_REMINDER" newcode/` 无引用；`python -c "from newcode.prompt.resources import render_banner, EXECUTE_DIRECTIVE"` 成功

## T14: Provider.stream 签名改收 PromptPayload

**文件：** `newcode/provider/base.py`
**依赖：** T11
**步骤：**
1. `Provider.stream(self, payload: PromptPayload)` 替换 `stream(msgs, tools, system_suffix)` 签名
2. 更新协议文档字符串（说明 payload 结构、provider 负责协议翻译 + 缓存标记）
3. 删除 `system_suffix` 形参

**验证：** `python -c "from newcode.provider.base import Provider; import inspect; print('stream' in dir(Provider))"` 通过；无 `system_suffix` 残留

## T15: anthropic.py PromptPayload 翻译 + cache_control + 缓存解析

**文件：** `newcode/provider/anthropic.py`
**依赖：** T14
**步骤：**
1. 首条 user 消息 `content=[{type:text, text: stable_prompt, cache_control:{"type":"ephemeral"}}, {type:text, text: env_segment}]`（env 为空则单块）
2. 历史/reminders 翻译（reminders 为 `<system-reminder>` 包裹的 user 消息）
3. tools 每个定义加 `cache_control`
4. message_start / message_delta 解析 `cache_creation_input_tokens` / `cache_read_input_tokens` → TokenUsage

**验证：** 构造 payload 调 `stream()`（mock 客户端），断言请求体首条 user 消息含 cache_control 块、tools 含 cache_control；模拟 usage 事件断言 TokenUsage 缓存字段正确

## T16: openai.py PromptPayload 翻译 + cached_tokens 解析

**文件：** `newcode/provider/openai.py`
**依赖：** T14
**步骤：**
1. 段1/段2 各为一条 user 消息（段1 在前），env 为空则只发段1
2. 历史/reminders 翻译
3. tools 不设缓存标记
4. 从 `chunk.usage.prompt_tokens_details.cached_tokens` 解析 → TokenUsage.cache_read_input_tokens

**验证：** 构造 payload 调 `stream()`（mock 客户端），断言首条 user=段1、次条 user=段2、tools 无 cache_control；模拟 usage 断言 cached_tokens 解析正确

## T17: test_cache_usage.py

**文件：** `tests/test_cache_usage.py`（新建）
**依赖：** T15、T16
**步骤：**
1. Anthropic：mock usage（含 cache_creation/cache_read）→ TokenUsage 字段正确
2. OpenAI：mock usage（含 cached_tokens）→ TokenUsage.cache_read 正确
3. 兼容端点缺字段 → 按 0 处理不抛异常（N1 健壮解析）

**验证：** `pytest tests/test_cache_usage.py -q` 全绿

## T18: conversation/manager.py 去 system_prompt

**文件：** `newcode/conversation/manager.py`
**依赖：** 无
**步骤：**
1. `__init__(self, max_turns: int)`，删除 `system_prompt` 参数
2. 删除 `self._system_prompt`，移除对 `SYSTEM_PROMPT` 的 import
3. `get_context()` 返回 `list(self._messages)`（纯历史，不再拼 system 消息）

**验证：** `ConversationManager(20)` 可用；`get_context()` 不含 role="system" 消息

## T19: agent.py 组装管线改造

**文件：** `newcode/agent/agent.py`
**依赖：** T11、T18、T14
**步骤：**
1. 构造函数增 `stable_prompt: str`、`env_segment: str` 参数（或 PromptBuilder + env 组合对象）
2. `run()` 内每轮：调 `assemble_payload(stable, env, conv.get_context(), reminders, tools)` → `provider.stream(payload)`
3. 移除 `system_suffix` 相关逻辑与 `PLAN_MODE_REMINDER` import
4. tools 按模式：plan 用 `registry.read_only_definitions()`，其余 `to_definitions()`

**验证：** 现有 test_agent.py 改造前先记录失败基线，本任务后 `pytest tests/test_agent.py -q` 除签名适配外行为用例不回归（T21 统一适配签名）

## T20: agent.py PlanMode 按轮注入

**文件：** `newcode/agent/agent.py`
**依赖：** T19、T7
**步骤：**
1. `run()` 每轮开头：`reminders = [plan_mode_reminder(turn)] if mode == "plan" else []`
2. reminders 传入 `assemble_payload`（瞬时不写入 conv 历史）
3. `/do` 的 `EXECUTE_DIRECTIVE` 仍走 `conv.add_user`（沿用）

**验证：** plan 模式下第 0/5 轮 payload.reminders 含完整版、第 1-4 轮精简版；normal 模式 reminders 为空

## T21: test_agent.py 适配新签名

**文件：** `tests/test_agent.py`
**依赖：** T19、T20
**步骤：**
1. MockProvider.stream 改为收 `payload: PromptPayload`，内部按 payload 发事件
2. `ConversationManager("", 20)` → `ConversationManager(20)`
3. Agent 构造传入 stable_prompt/env_segment
4. **保留全部 ch04 用例**（自然终止、多工具、取消、未知工具、流式错误、Plan Mode 只读）

**验证：** `pytest tests/test_agent.py -q` 全绿，ch04 用例不删不改断言

## T22: test_conversation_tools.py 适配

**文件：** `tests/test_conversation_tools.py`
**依赖：** T18
**步骤：**
1. 适配 `ConversationManager` 新签名（去 system_prompt 实参）
2. 若断言依赖 `get_context()` 含 system 消息则改为纯历史断言

**验证：** `pytest tests/test_conversation_tools.py -q` 全绿

## T23: main.py 接线

**文件：** `newcode/main.py`
**依赖：** T19、T18、T5、T9
**步骤：**
1. `ConversationManager(config.max_turns)`（去 system_prompt 实参）
2. 构建 `PromptBuilder(fixed_sections() + optional_sections(config.system_prompt))`，调 `build()` 得 stable_prompt
3. `collect_env(cwd, __version__, provider.name, provider.model)` → `format_env()` 得 env_segment
4. `Agent(builder_stable, env_segment, provider, conversation, registry)` 传入新构造

**验证：** `python -c "import newcode.main"` 无 import 错误；`newcode --version` 输出 0.5.0

## T24: test_tui_wiring.py 适配

**文件：** `tests/test_tui_wiring.py`
**依赖：** T23
**步骤：**
1. 适配 Agent / ConversationManager 新构造签名
2. 保留原有"驱动真实代码路径"的断言

**验证：** `pytest tests/test_tui_wiring.py -q` 全绿

## T25: scripts/eval_scenarios.py 评估脚本

**文件：** `scripts/eval_scenarios.py`（新建）
**依赖：** T19、T20
**步骤：**
1. 支持 `--scenario 1..5`：
   场景1 工具优先级（grep/ls vs search_code/list_files）、场景2 先读后改、场景3 PlanMode 只读、场景4 多工具配合、场景5 缓存命中与成本
2. 每个场景：给定输入、跑一次 Agent、打印工具调用序列 + 每轮 TokenUsage（含缓存字段）+ 缓存命中标注
3. 接受 `--prompt-config` 对比不同配置

**验证：** `python scripts/eval_scenarios.py --scenario 5 --help` 可运行；带 mock provider 能打印工具序列与缓存字段

## T26: 全量验证

**文件：** 全部
**依赖：** 全部
**步骤：**
1. `pytest tests/ -q` 全绿
2. `ruff format --check . && ruff check .` 通过（N13）
3. `python -c "import newcode.main"` 无 import 错误
4. 手工核对：组装器输出稳定前缀跨轮字节一致；Anthropic 请求含 cache_control；OpenAI 无缓存标记

**验证：** 以上 4 项全部通过

## 执行顺序

```
T1 → T2 → T3
                ↘
T4 → T5 → T6     T7 → T8
                T9 → T10
T11 → T12
T13（依赖 T4/T7）
T14 → T15 → T17
   ↘ T16
T18 → T19 → T20 → T21
T22 ↘
T23 → T24
T25
T26（全量）
```

**关键路径：** T1 → T4 → T5 → T11 → T14 → T15 → T19 → T20 → T23 → T26
**可并行：** T2/T3/T7/T9/T18 在 T4 之后可与主线并行推进
