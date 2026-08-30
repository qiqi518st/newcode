# MewCode 结构化 System Prompt 与 Prompt Cache 优化 — 技术设计 (plan.md)

## 架构概览

本方案把「系统提示拼装」从单块字符串升级为**模块化组装 + 三通道分发**。核心思路：稳定内容（七个模块拼装结果 + 工具定义）在会话内**跨轮逐字节一致**，可缓存；变化内容（环境信息、对话历史、按轮次注入的补充消息）走消息通道，不缓存。

引入一条**组装管线**：`assemble_payload()` 把稳定系统提示（段1）、环境信息（段2）、会话历史、轮次级补充消息、工具定义分类路由，产出协议无关的 `PromptPayload`。两个 Provider 适配器把 `PromptPayload` 翻译成各自协议格式，Anthropic 显式打缓存断点、OpenAI 靠前缀自动缓存，并解析 usage 中的缓存字段。

**关键位置变化**：稳定系统提示物理上放在**首条 user 消息段1**（非 `system` 参数），环境信息为段2，均不在 system 参数出现；`system_suffix` 机制整体移除，Plan Mode 提醒改为按轮次注入 system-reminder。

```
main.py 启动
  config.system_prompt ─→ PromptBuilder ─→ build_system_prompt() ─→ stable_prompt(段1，跨轮不变)
  collect_env(cwd, version, provider, model) ─→ format_env() ─→ env_segment(段2，会话不变)
  Agent(builder, env_segment, provider, conv, registry)

每轮请求 (Agent.run 循环)
  Agent 按 mode + turn 构造 reminders（PlanMode 完整/精简版）
  assemble_payload(stable, env, conv.get_context(), reminders, tools) → PromptPayload
  provider.stream(payload)
    ├─ Anthropic: 首条 user 消息 content=[段1(cache_control), 段2] + 历史 + reminders + tools(cache_control)
    └─ OpenAI:    user(段1) + user(段2) + 历史 + reminders + tools（无缓存标记）
  StreamEvent(usage 含缓存字段) → Agent 消费、执行工具、写历史、循环
```

## 核心数据结构

### Section（系统提示模块）
```python
@dataclass
class Section:
    name: str  # 模块名，如 "identity"、"behavior"
    content: str  # 模块指令文本
    priority: int  # 优先级，数字越小越靠前（固定模块 1-7，可选模块 10+）
```

### PromptPayload（组装管线输出，协议无关）
```python
@dataclass
class PromptPayload:
    stable_prompt: str  # 段1：七个固定模块 + 可选模块拼装结果（跨轮逐字节一致，可缓存）
    env_segment: str  # 段2：环境信息文本（不缓存）
    messages: list[Message]  # 会话历史（不含 system，由 ConversationManager 提供）
    reminders: list[Message]  # 轮次级 system-reminder（瞬时不持久）
    tools: list[ToolDefinition] | None = None
```

### TokenUsage（扩展，F8）
```python
@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0  # 缓存写入（首次创建）
    cache_read_input_tokens: int = 0  # 缓存读取（命中复用）
```
Provider 映射：Anthropic 读 usage 的 `cache_creation_input_tokens` / `cache_read_input_tokens`；OpenAI 读 `prompt_tokens_details.cached_tokens`（写入字段 OpenAI 不暴露，恒 0）。兼容端点缺字段按 0 处理（N1 健壮解析）。

### EnvContext（环境信息，F3）
```python
@dataclass
class EnvContext:
    cwd: str
    platform: str
    datetime: str  # 当前日期/时间
    timezone: str
    git_branch: str | None  # git 状态，取不到为 None（N12 降级）
    git_dirty: bool | None
    version: str  # 应用版本
    provider: str  # 激活 provider 名
    model: str  # 模型名
```

### 补充消息（F5）
`reminders.py` 提供 `system_reminder(content) -> Message`，产出 `role=user`、内容以 `<system-reminder>...</system-reminder>` 包裹的消息。

## 模块设计

### prompt 模块（新建 builder / sections / assembler / env / reminders）

**sections.py — 七个固定模块内容 + 可选模块**
- `fixed_sections() -> list[Section]`：按 spec F1 优先级返回七个模块（Identity=1, Behavior=2, ToolUsage=3, CodeQuality=4, Security=5, TaskPattern=6, OutputStyle=7），每段内容为中文指令文本
- `optional_sections(system_prompt: str) -> list[Section]`：`config.system_prompt` 非空时生成「自定义指令」模块（priority=10，**追加语义**，见 F1）；Skill / 长期记忆预留位不产出内容
- 工具使用指南模块（ToolUsage）内容内包含「优先用专用工具而非 shell」「编辑前必先读」两条关键约定——与工具 description 双重表述（F4）

**builder.py — PromptBuilder**
```python
class PromptBuilder:
    def __init__(self, sections: list[Section]): ...
    def add(self, section: Section) -> None: ...
    def build(self) -> str:   # 按 priority 升序拼装，模块间空行分隔
```
- `build()` 结果跨轮不变（会话内只构建一次），是稳定前缀（段1）的来源

**assembler.py — assemble_payload（F2）**
```python
def assemble_payload(stable, env, history, reminders, tools) -> PromptPayload: ...
```
- 负责分类路由：stable→段1（可缓存）、env→段2（不缓存）、history→messages、reminders→messages、tools→tools
- 对 stable 做**跨轮逐字节一致**校验：记录上一次 stable 哈希，不一致时日志告警（N8 / 缓存通道规划）

**env.py — collect_env / format_env（F3, N12）**
- `collect_env(cwd, version, provider, model) -> EnvContext`：cwd / platform / datetime / timezone 同步取；git 状态用 `git status --porcelain -b` 子进程**带超时**（如 1s），失败或超时降级为 None，不抛异常
- `format_env(env) -> str`：把 EnvContext 拼成一段供模型感知环境的文本（每项缺失则省略该行）
- 启动时采集一次（会话内稳定），不是每轮采集

**reminders.py — system-reminder + PlanMode 注入（F5, F6）**
- `system_reminder(content) -> Message`：`<system-reminder>` 标签包裹，role=user
- `PLAN_MODE_FULL`：完整版提醒（沿用现 `PLAN_MODE_REMINDER` 内容：只读、产出结构化计划、格式要求、slug 声明）
- `PLAN_MODE_LEAN`：精简版一行（如「【计划模式】只读：仅用只读工具探查并产出计划文档，不修改文件、不执行命令。」）
- `plan_mode_reminder(turn: int) -> Message`：turn==0 或 turn%5==0 返回完整版，否则精简版（F6 默认间隔 5 轮）
- 提醒每轮动态构造、**不写入会话历史**（N5 / AC13）

### provider 适配层（base / anthropic / openai）

**base.py**
- `TokenUsage` 增缓存字段（见上）
- `Provider.stream` 签名改为 `stream(self, payload: PromptPayload) -> AsyncIterator[StreamEvent]`，**删除 `system_suffix` 形参**

**anthropic.py — PromptPayload → Anthropic 请求**
- 首条 user 消息：`content=[{type:text, text: stable_prompt, cache_control:{type:"ephemeral"}}, {type:text, text: env_segment}]`（段1 打缓存断点，段2 在断点之后不缓存；env 为空则只发单块）
- 历史 / reminders 依次翻译为 user/assistant/tool 消息（reminders 为 `<system-reminder>` 包裹的 user 消息）
- `tools` 数组每个定义加 `cache_control: {"type": "ephemeral"}`
- 保留 thinking / max_tokens 现有配置
- message_start / message_delta 解析 `cache_creation_input_tokens` / `cache_read_input_tokens` → TokenUsage

**openai.py — PromptPayload → OpenAI 请求**
- 段1 / 段2 各为一条 user 消息（段1 在前，保段1 消息逐字节稳定 → 前缀缓存受益）；env 为空则只发段1
- 历史 / reminders 翻译为 user/assistant/tool 消息
- tools **不设**缓存标记（OpenAI 无 cache_control）
- 从 `chunk.usage.prompt_tokens_details.cached_tokens` 解析缓存读取 → TokenUsage.cache_read_input_tokens

### agent 循环（agent.py）
- 构造注入 `PromptBuilder`、`env_segment`；移除 `system_suffix` 逻辑
- `run()` 内每轮：
  1. 按 `mode` 构造 reminders：`plan` 模式调 `plan_mode_reminder(turn)`，其余模式空
  2. 调 `assemble_payload(stable, env, conv.get_context(), reminders, tools)`（tools 按模式：plan 用只读集）
  3. `provider.stream(payload)` 消费事件（TEXT/TOOL_CALL/usage/DONE 逻辑沿用 ch04，usage 现在携带缓存字段）
- 循环、停止条件、未知工具、取消、历史写入等 ch04 逻辑**不退化**（N3）

### conversation 管理器（manager.py）
- 构造函数**删除 `system_prompt` 参数**（改为 `__init__(max_turns)`），`_system_prompt` 移除
- `get_context()` 返回**纯会话历史**（不再拼 system 消息）
- 其余 add_user / add_assistant / add_tool_result / 滑动窗口逻辑不变

### tools（file_ops / search / shell）
- 六个工具 `description` 补全**用法 + 优先级 + 配合关系**（F4）：
  - read_file：定位后用 search_code 精读；edit 前必须先读
  - write_file / edit_file：先读后改
  - execute_command：优先专用工具而非 shell，白名单限制
  - list_files / search_code：优于 shell 手写 ls / grep
- 只改 description 字符串与 `parameters` 里的辅助描述，不改 execute 语义

### config / main
- `config/schema.py`：`system_prompt` 字段**保留**（喂给 optional_sections 做自定义指令模块，追加语义）
- `main.py`：
  - `ConversationManager(config.max_turns)`（去掉 system_prompt 实参）
  - `PromptBuilder(fixed_sections() + optional_sections(config.system_prompt))` 构建一次
  - `collect_env(cwd, __version__, provider.name, provider.model)` 采集一次
  - `Agent(builder, env_segment, provider, conversation, registry)`

### 版本号（ch05 → 0.5.0）
- `mewcode/__init__.py` 与 `pyproject.toml` 的版本从 0.4.5 → 0.5.0（独立提交，见版本号管理规则）

## 模块交互

```
Agent.run(user_input, mode)
  │  reminders = plan_mode_reminder(turn) if mode=="plan" else []
  │  payload = assemble_payload(stable_prompt, env_segment, conv.get_context(), reminders, tool_defs)
  ▼
provider.stream(payload)
  ├─ Anthropic: 首条 user [段1(cache_control), 段2] + 历史 + reminders + tools(cache_control)
  ├─ OpenAI:    user(段1) + user(段2) + 历史 + reminders + tools
  └─ StreamEvent(text / tool_call / usage[含缓存字段] / done / err)
  ▼
Agent 消费 → 执行工具 → conv.add_assistant_with_tool_calls / conv.add_tool_result → 下一轮
```

## 文件组织

```
mewcode/
├── prompt/
│   ├── sections.py     新建 — 七个固定模块 + 可选模块（含优先级）
│   ├── builder.py      新建 — Section、PromptBuilder、build()
│   ├── assembler.py    新建 — PromptPayload、assemble_payload
│   ├── env.py          新建 — collect_env、format_env
│   ├── reminders.py    新建 — system_reminder、PLAN_MODE_FULL/LEAN、plan_mode_reminder
│   └── resources.py    修改 — 保留 render_banner / EXECUTE_DIRECTIVE；SYSTEM_PROMPT / PLAN_MODE_REMINDER 迁出
├── provider/
│   ├── base.py         修改 — TokenUsage 扩展；Provider.stream 改收 PromptPayload，删 system_suffix
│   ├── anthropic.py    修改 — PromptPayload 翻译、cache_control、缓存字段解析
│   └── openai.py       修改 — PromptPayload 翻译、无缓存标记、cached_tokens 解析
├── agent/
│   └── agent.py        修改 — 用 assembler；按轮次注入 reminders；删 system_suffix
├── conversation/
│   └── manager.py      修改 — 删 system_prompt 参数；get_context 返回纯历史
├── tools/
│   ├── file_ops.py     修改 — read/write/edit description 强化
│   ├── search.py       修改 — list/search description 强化
│   └── shell.py        修改 — execute_command description 强化
├── config/schema.py    无改动（system_prompt 字段保留）
├── main.py             修改 — 构建 PromptBuilder、collect_env、改 Agent/ConversationManager 构造
├── __init__.py         修改 — 版本 0.5.0
└── pyproject.toml      修改 — 版本 0.5.0
tests/
├── test_builder.py     新建 — 优先级拼装、可选模块追加
├── test_assembler.py   新建 — 三通道分发、段1/段2、缓存一致性校验
├── test_env.py         新建 — 采集、git 降级、格式化
├── test_reminders.py   新建 — system-reminder 格式、PlanMode 注入频率
├── test_cache_usage.py 新建 — 缓存字段解析（Anthropic/OpenAI mock usage）
├── test_agent.py       修改 — MockProvider 适配新签名；保留 ch04 全部用例
├── test_conversation_tools.py 修改 — 适配 get_context 纯历史
└── test_tui_wiring.py  修改 — 适配 Agent 构造签名
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 稳定系统提示物理位置 | 首条 user 消息段1（非 system 参数） | spec F3 明确；兼容代理（CC Switch/DeepSeek 走 messages 更稳，ch04 已踩过 messages.stream 的坑）；匹配 Claude Code 实践 |
| Anthropic 段1/段2 布局 | 一条 user 消息两个 content block，段1 带 cache_control | 块级缓存断点，精确匹配 spec F3 图；断点之后（段2+历史）不缓存 |
| OpenAI 段1/段2 布局 | 两条 user 消息（段1 在前） | OpenAI 无块级 cache_control；拆开保证段1 消息逐字节稳定 → 前缀缓存受益最大 |
| Provider.stream 签名 | 接收 PromptPayload | 组装逻辑集中在 prompt 模块，provider 只做协议翻译，职责单一 |
| system_suffix 机制 | 移除 | 被 system-reminder 按轮注入替换（spec 不做的事）；改签名需同步更新所有 mock |
| ConversationManager | 不再持有 system_prompt，get_context 返回纯历史 | 稳定内容归属 prompt 模块，职责分离 |
| TokenUsage 扩展 | 增 cache_creation / cache_read，默认 0 | 跨协议统一；兼容端点缺字段按 0（N1） |
| PlanMode 提醒 | reminders.py 提供 full/lean，Agent 按 turn%5 注入 | 不污染 system 缓存，控制注入频率（F6） |
| 环境采集时机 | 启动采集一次 | 满足 N12 快速有界；会话内稳定 |
| 缓存一致性 | assemble_payload 对 stable 做跨轮字节一致校验 + 日志告警 | 防误改稳定内容导致缓存静默失效（N8） |
| plan/normal 工具集不同 | tools 通道随模式变化 | 安全约束（只读集）优先于缓存；缓存按模式各自生效 |
| 版本号 | 0.4.5 → 0.5.0 | 章节对应主版本号（版本号管理规则） |

## Spec 覆盖检查

| spec 需求 | 归属 |
|-----------|------|
| F1 模块化拼装 | sections.py + builder.py |
| F2 三通道分发 + 缓存通道规划 | assembler.py + provider 适配 |
| F3 环境信息构造与呈现 | env.py（段2 不缓存） |
| F4 关键约定双重强化 | sections.py（ToolUsage 内容）+ tools description |
| F5 system-reminder 注入 | reminders.py + provider 翻译（瞬时不持久） |
| F6 规划模式按轮注入 | reminders.py（plan_mode_reminder）+ agent.py |
| F7 评估脚本 | 阶段五新增 scripts/ 或 tools 下（见 task.md） |
| F8 缓存命中解析 | TokenUsage 扩展 + provider usage 解析 |
| N2 双协议一致 | anthropic.py / openai.py 同套 PromptPayload |
| N11 历史合法性 | provider 翻译保持角色交替 + test_assembler / test_cache_usage |
| N12 环境采集降级 | env.py git 子进程带超时、失败降级 |
