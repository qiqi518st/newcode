# MewCode TUI 多轮对话 — 技术设计 (plan.md)

## 技术栈

- 语言：Python 3.10+
- TUI：prompt_toolkit（REPL 输入处理）+ Rich（Markdown 渲染，含 `Live`、`Markdown`、`Console` 等）
- 配置：YAML 解析（`pyyaml`，import 名 `yaml`）
- LLM 通信：官方 Python SDK —— `anthropic`（`AsyncAnthropic`）、`openai`（`AsyncOpenAI`），均原生支持 async 流式（SDK 内部已处理 SSE）

## 架构概览

1. **入口层** `mewcode.main` — 加载配置、打印启动横幅、分发到 TUI 或单次调用模式。
2. **配置层** `mewcode.config` — 读取并校验项目根目录的 `.mewcode.yaml`，解析 `${ENV_VAR}` 占位符，提供 providers 列表和全局配置。
3. **LLM 协议层** `mewcode.provider` — 定义协议无关的 `Provider` 接口与统一流式事件类型；Anthropic、OpenAI 两个适配器各自封装官方 SDK，统一吐出 `StreamEvent`（思考增量内部丢弃）。
4. **会话层** `mewcode.conversation` — 进程内维护多轮对话历史，提供完整上下文（system prompt + 滑动窗口内消息对）。
5. **提示词/资源层** `mewcode.prompt` — 内置 system prompt 与启动横幅（ASCII 小狗）。
6. **终端层** `mewcode.tui` — prompt_toolkit REPL 循环，含状态机（空闲/流式）、输入框（Alt+Enter 多行编辑）、Rich 流式 Markdown 渲染、响应计时、ESC 流式中断、错误反馈与重试展示；以 async task 消费 `Provider.stream()` 的 `StreamEvent` 生成器，实时写入终端。

**数据流：**

```
用户输入 → prompt_toolkit 读取（Alt+Enter 多行编辑）
  → ConversationManager.add_user(content)
  → Provider.stream(manager.get_context())
  → async for StreamEvent:
      text 增量 → Rich Renderer 流式渲染
      done      → 定型 Markdown 渲染 + 追加到对话历史
      err       → 自动重试（最多3次）→ 失败则显示错误样式
  → ConversationManager.add_assistant(full_text)
  → 滑动窗口裁剪（超出 max_turns 时丢弃最早消息对）
  → 等待下一次输入
```

---

## 核心数据结构与接口

```python
# ───────── config 层 ─────────
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class ProviderConfig:
    """单个 LLM 供应商的配置"""
    name: str                                  # 状态栏左侧显示
    protocol: Literal["anthropic", "openai"]   # 协议类型
    model: str                                 # 状态栏右侧显示
    api_key: str                               # 认证密钥（已解析 ${ENV_VAR}）
    base_url: str | None = None                # None 则用 SDK 默认端点
    thinking: bool = False                     # 仅 anthropic 生效

@dataclass
class Config:
    """MewCode 全局配置"""
    provider: str                              # 当前激活的 provider name
    max_turns: int = 20                        # 滑动窗口保留轮数
    system_prompt: str = ""                    # 自定义 system prompt，空则用内置默认值
    providers: list[ProviderConfig] = field(default_factory=list)

def load(path: str) -> Config: ...             # 加载 + 校验 + ${ENV_VAR} 解析

# ───────── llm 层（协议无关）─────────
from typing import Protocol, AsyncIterator
from dataclasses import dataclass

@dataclass
class Message:
    """单条对话消息"""
    role: Literal["user", "assistant"]
    content: str

@dataclass
class StreamEvent:
    """流式事件：text / done / err 三者互斥"""
    text: str = ""                             # 文本增量
    done: bool = False                         # 本轮正常结束
    err: Exception | None = None               # 出错（与 done 互斥）

class Provider(Protocol):
    """LLM Provider 协议，所有后端通过此接口统一调用"""
    @property
    def name(self) -> str: ...                 # → 状态栏左
    @property
    def model(self) -> str: ...                # → 状态栏右
    # 发起一轮流式对话；内部注入内置 system prompt 与 thinking 配置；
    # 思考增量内部丢弃；以 async generator 吐出 StreamEvent；
    # 调用方 cancel() 该 task 即终止。
    def stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]: ...

def new_provider(cfg: ProviderConfig) -> Provider: ...   # 按 protocol 构造适配器

# ───────── conversation 层 ─────────
class ConversationManager:
    """对话上下文管理器，维护消息列表并实现滑动窗口"""
    def __init__(self, system_prompt: str, max_turns: int) -> None: ...
    def add_user(self, content: str) -> None: ...
    def add_assistant(self, content: str) -> None: ...
    def get_context(self) -> list[Message]:
        """返回完整上下文: system prompt + 窗口内消息"""
        ...
    def _trim(self) -> None:
        """超出 max_turns 时，丢弃最早的一对 user/assistant"""
        ...

# ───────── prompt 层 ─────────
SYSTEM_PROMPT: str = "你是一个 AI 编程助手 MewCode，运行在终端中。请用中文回复，回答简洁清晰。"
DOG_BANNER: str = "..."                        # ASCII 小狗图案
def render_banner(version: str, cwd: str) -> str:
    """返回拼接后的启动横幅：ASCII 小狗 + 版本号 + 工作目录"""
    ...

# ───────── tui 层 ─────────
from enum import Enum

class SessionState(Enum):
    IDLE = "idle"                              # 等待用户输入
    STREAMING = "streaming"                    # 等待/接收模型流（loading + 计时）

class REPL:
    """prompt_toolkit REPL 循环"""
    state: SessionState
    provider: Provider
    conv: ConversationManager
    cur_reply: str                             # 本轮 assistant 增量缓冲
    turn_start: float                          # time.monotonic() 计时起点
    _stream_task: asyncio.Task | None          # 当前流式消费 task
    _retry_count: int                          # 当前重试次数

    async def run(self) -> None: ...           # 主循环
    async def _process_input(self, text: str) -> None: ...  # 提交 → 切换状态 → 流式消费
    async def _consume_stream(self) -> None:   # async for event in provider.stream(...)
    def _cancel_stream(self) -> None: ...      # ESC 取消当前流式 task
    def _show_timer(self) -> str: ...          # "Imagining… (5s)" 格式
```

---

## 模块设计

### 模块 A: CLI 入口 (main.py)

**职责：** 解析命令行参数，加载配置，打印启动横幅，创建 Provider 和 ConversationManager，根据模式分发到 TUI 或单次调用。

**对外接口：**
- `argparse` 子命令解析
- `async_main()` — 异步主函数

**参数定义：**
```
mewcode                  # 启动 TUI 多轮对话
mewcode -c "问题"        # 单次调用，输出后退出
mewcode --help           # 显示帮助
```

**流程：**
1. 解析命令行参数
2. 调用 `config.load()` 加载项目根目录的 `.mewcode.yaml`
3. 根据 `config.provider` 找到对应的 ProviderConfig
4. 调用 `new_provider()` 工厂函数创建 Provider 实例
5. 创建 ConversationManager 实例
6. 若 `-c` 存在：调用 `oneshot()` 单次调用流程，输出后退出
7. 否则：打印 `render_banner()`，创建 REPL，`asyncio.run(repl.run())`

**依赖：** config 层、provider 层、conversation 层、prompt 层、tui 层

---

### 模块 B: 配置层 (config/)

**职责：** 加载 YAML 配置文件，校验字段完整性，解析 `${ENV_VAR}` 占位符，返回 Config 对象。

**子模块：**

#### config/schema.py
- `ProviderConfig` — provider 配置数据类，`base_url` 为 Optional
- `Config` — 全局配置数据类

#### config/loader.py
- `load(path: str) -> Config` — 加载并校验配置文件

**校验规则：**
- 配置文件必须存在，否则抛出 `ConfigError` 提示用户创建
- `provider` 字段必须在 `providers` 列表中存在
- 每个 provider 的 `name`、`protocol`、`model`、`api_key` 必填
- `protocol` 只能为 `"anthropic"` 或 `"openai"`
- `api_key` 支持 `${ENV_VAR}` 语法，从环境变量读取
- `base_url` 可选，不填则使用 SDK 默认端点

**依赖：** pyyaml、os.environ

---

### 模块 C: Provider 层 (provider/)

**职责：** 封装 Anthropic SDK 和 OpenAI SDK 的差异，提供统一的 `Provider` 协议接口。

**子模块：**

#### provider/base.py
- `Provider` — Protocol 定义，含 `name`、`model` 属性和 `stream()` 方法
- `StreamEvent` — 流式事件：text（增量）/ done（结束）/ err（错误）
- `Message` — 消息数据类
- `new_provider(cfg: ProviderConfig) -> Provider` — 工厂函数

#### provider/anthropic.py
- `AnthropicProvider` — 实现 Provider 协议
  - 使用 `anthropic.AsyncAnthropic` 客户端
  - 配置 `base_url`（若为 None 则用 SDK 默认）和 `api_key`
  - 调用 `messages.stream()` 获取 SSE 事件流
  - 处理 `thinking` 参数：仅当 `config.thinking=True` 时启用
  - 思考增量内部丢弃，仅 yield `StreamEvent(text=...)` 文本增量
  - 流正常结束时 yield `StreamEvent(done=True)`
  - 捕获 `anthropic.APIError`，yield `StreamEvent(err=...)`

#### provider/openai.py
- `OpenAIProvider` — 实现 Provider 协议
  - 使用 `openai.AsyncOpenAI` 客户端
  - 配置 `base_url`（若为 None 则用 SDK 默认）和 `api_key`
  - 调用 `chat.completions.create(stream=True)` 获取 SSE 事件流
  - 逐 token yield `StreamEvent(text=delta.content)`
  - 流正常结束时 yield `StreamEvent(done=True)`
  - 捕获 `openai.APIError`，yield `StreamEvent(err=...)`

**依赖：** anthropic SDK、openai SDK

---

### 模块 D: 会话层 (conversation/)

**职责：** 维护消息列表，实现滑动窗口上下文裁剪，管理 system prompt。

**子模块：**

#### conversation/manager.py
- `ConversationManager` — 对话管理器

**关键逻辑：**
- `get_context()` 返回 `[system_prompt_message] + 窗口内消息`
- `_trim()` 在消息数超过 `max_turns * 2`（每轮 2 条 user/assistant）时，丢弃最早的一对
- system prompt 为空时，使用 `prompt.SYSTEM_PROMPT` 内置默认值

**依赖：** prompt 层

---

### 模块 E: 提示词/资源层 (prompt/)

**职责：** 提供内置 system prompt 和启动横幅资源。

**子模块：**

#### prompt/resources.py
- `SYSTEM_PROMPT` — 内置默认 system prompt
- `DOG_BANNER` — ASCII 小狗图案
- `render_banner(version: str, cwd: str) -> str` — 拼接启动横幅

**依赖：** 无外部依赖

---

### 模块 F: 终端层 (tui/)

**职责：** 基于 prompt_toolkit 提供 Claude Code 风格的 REPL 交互界面，支持状态机、多行输入、流式 Markdown 渲染、响应计时、ESC 流式中断、错误反馈与重试。

**子模块：**

#### tui/app.py
- `SessionState` — 状态枚举：IDLE / STREAMING
- `REPL` 类 — TUI 主循环

**关键实现：**

1. **状态机**：
   - `IDLE`：等待用户输入，可接受新提交
   - `STREAMING`：正在接收模型流，输入框锁定，不接受新提交，可响应 ESC

2. **REPL 循环**：
   - 使用 `asyncio` 事件循环
   - 每次循环调用 `prompt_toolkit.prompt_async("❯ ")` 获取用户输入
   - 空输入跳过
   - 检测 `/exit`、`/quit` → break 退出
   - 检测 `Ctrl+C`（KeyboardInterrupt）→ 若在 STREAMING 则取消流，否则 break 退出
   - 检测 `Ctrl+D`（EOFError）→ break 退出
   - 非空输入 → 调用 `_process_input(text)`

3. **多行输入**：
   - 使用 `prompt_toolkit` 的 `key_bindings` 绑定 `Alt+Enter` 插入换行
   - 输入框底部提示 "Alt+Enter 换行，Enter 发送"

4. **流式消费**（`_consume_stream`）：
   - 切换状态为 STREAMING，记录 `turn_start = time.monotonic()`
   - 创建 `_stream_task = asyncio.create_task()` 消费 `provider.stream()`
   - `async for event in provider.stream(...)`：
     - `event.text` → 追加到 `cur_reply`，Rich Live 流式渲染 Markdown
     - `event.done` → 切换状态为 IDLE，定型 Markdown 渲染，追加到对话历史
     - `event.err` → 若 `_retry_count < 3`，等待 3 秒后重试（显示重试次数和状态）；否则显示错误信息（红色/可区分样式），切换状态为 IDLE

5. **响应计时**（`_show_timer`）：
   - 自 `turn_start` 起计算已用秒数
   - 在 STREAMING 期间实时显示 "Imagining… (Ns)"（N 随时间递增）
   - 回复结束后定型显示 "Done (Ns)"

6. **流式中断**（`_cancel_stream`）：
   - 在 STREAMING 状态下按 ESC → `_stream_task.cancel()`
   - 对话区显示取消提示，切换状态为 IDLE，对话历史保留，用户可继续输入

7. **退出清理**：
   - 退出时恢复终端状态，清理 raw mode，不残留损坏的终端环境

#### tui/renderer.py
- `RichRenderer` 类 — 流式 Markdown 渲染

**关键实现：**
- 使用 `rich.console.Console` 作为输出目标
- 使用 `rich.live.Live` 配合 `rich.markdown.Markdown` 实现流式渲染
- 每收到一个 `StreamEvent.text`，追加到缓冲区，重新渲染完整 Markdown
- 流式结束后，保留最终渲染结果

**依赖：** prompt_toolkit、rich、asyncio、time

---

### 模块 G: 工具层 (utils/)

**职责：** 自定义异常类型。

**子模块：**

#### utils/error.py
```python
class MewCodeError(Exception): ...


class ConfigError(MewCodeError): ...  # 配置相关的错误


class ProviderError(MewCodeError): ...  # Provider 调用相关的错误
```

**依赖：** 无

---

## 模块交互

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────────┐
│   main.py    │────▶│  config.load()  │────▶│  .mewcode.yaml   │
│  (入口)      │     │                 │     │  (项目根目录)    │
└──────┬───────┘     └─────────────────┘     └──────────────────┘
       │
       │ Config
       ▼
┌──────────────────┐     ┌──────────────────┐
│  new_provider()  │────▶│  ProviderFactory │
│  (工厂函数)      │     │  (协议分发)      │
└──────┬───────────┘     └────────┬─────────┘
       │                          │
       │              ┌───────────┴──────────┐
       │              ▼                      ▼
       │     ┌──────────────┐      ┌──────────────┐
       │     │ Anthropic    │      │   OpenAI     │
       │     │ Provider     │      │  Provider    │
       │     └──────┬───────┘      └──────┬───────┘
       │            │                      │
       ▼            └──────────┬───────────┘
┌──────────────┐              │
│ Conversation │◀─────────────┘ stream(messages) → StreamEvent
│  Manager     │
└──────┬───────┘
       │
       │ get_context() / add_assistant()
       ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   REPL       │────▶│ RichRenderer │     │   prompt     │
│  (TUI 循环)  │     │  (流式渲染)  │     │  (资源)      │
└──────────────┘     └──────────────┘     └──────────────┘
```

**典型调用链（一次用户输入）：**

1. `main.py` → 打印 `render_banner()` → `REPL.run()` 启动循环
2. `REPL` → `prompt_toolkit.prompt_async("❯ ")` 获取用户输入
3. `REPL` → `ConversationManager.add_user(input)`
4. `REPL` → `ConversationManager.get_context()` 获取上下文
5. `REPL` → 状态切换为 STREAMING，启动计时
6. `REPL` → `Provider.stream(context)` 异步迭代 `StreamEvent`：
   - `text` → RichRenderer 流式渲染
   - `done` → 定型渲染，追加到对话历史，显示耗时
   - `err` → 自动重试（最多 3 次）或显示错误样式
7. 流结束后 → `ConversationManager.add_assistant(full_response)`
8. 状态切换为 IDLE，回到步骤 2

---

## 文件组织

```
mewcode/
├── __init__.py
├── __main__.py              # python -m mewcode 入口
├── main.py                  # CLI 入口，argparse 解析，流程编排
├── config/
│   ├── __init__.py
│   ├── schema.py            # ProviderConfig, Config 数据类
│   └── loader.py            # load(): YAML 加载、校验、env 解析
├── provider/
│   ├── __init__.py          # new_provider() 工厂函数
│   ├── base.py              # Provider Protocol, StreamEvent, Message
│   ├── anthropic.py         # AnthropicProvider 实现
│   └── openai.py            # OpenAIProvider 实现
├── conversation/
│   ├── __init__.py
│   └── manager.py           # ConversationManager
├── prompt/
│   ├── __init__.py
│   └── resources.py         # SYSTEM_PROMPT, DOG_BANNER, render_banner()
├── tui/
│   ├── __init__.py
│   ├── app.py               # REPL 类：状态机、prompt_toolkit 循环、流式消费
│   └── renderer.py          # RichRenderer：流式 Markdown 渲染
└── utils/
    ├── __init__.py
    └── error.py             # MewCodeError, ConfigError, ProviderError
```

---

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| TUI 框架 | prompt_toolkit + Rich | 用户指定，Claude Code 同类方案，Rich 提供 Markdown 渲染 |
| Provider 类型 | Protocol（结构化类型） | 比 ABC 更灵活，调用方不需要显式继承，只需满足接口即可 |
| 流式返回值 | `StreamEvent`（text/done/err） | 统一处理文本增量、正常结束、错误三种情况，避免裸 str 无法表达状态 |
| API 调用 | 官方 SDK (anthropic + openai) | 用户指定，原生支持 SSE 流式和 extended thinking |
| 异步模型 | asyncio + async/await | 两个 SDK 均提供 AsyncClient，流式迭代天然适合 async |
| 配置格式 | YAML + pyyaml | 用户指定，可读性好，支持嵌套结构 |
| 流式渲染策略 | Rich Live + 缓冲区累积重渲染 | 逐 token 追加到缓冲区，用 Live 实时重渲染，兼顾流式体验和 Markdown 正确性 |
| 项目结构 | Python 包 + pyproject.toml | 标准 Python 项目结构，支持 pip install -e |
| API Key 安全 | `${ENV_VAR}` 占位符 | 避免密钥硬编码在配置文件中 |
| 配置缺失处理 | 提示用户并退出 | 用户指定，强制显式配置 |
| base_url | Optional，None 用 SDK 默认 | 大多数用户只需改 endpoint，不填更友好 |
| 状态管理 | SessionState 枚举 | 明确区分 IDLE/STREAMING，防止状态混乱 |
| 流式中断 | asyncio.Task.cancel() | 标准异步取消机制，ESC 触发 |
| 自动重试 | 3 次，间隔 3 秒 | spec 要求，界面展示重试次数 |

---

## spec 需求覆盖

| spec 需求 | 对应实现 |
|-----------|---------|
| F1: TUI 多轮对话 | tui/app.py REPL 循环 + 定型 Markdown 渲染 |
| F2: 单次调用模式 | main.py `-c` 分支 |
| F3: 流式输出 | provider/*.py 的 stream() → StreamEvent.text |
| F4: 滑动窗口 | conversation/manager.py _trim() |
| F5: 多行输入 | tui/app.py Alt+Enter key_bindings |
| F6: 多种退出 | tui/app.py /exit、/quit、Ctrl+C、Ctrl+D |
| F7: System Prompt | prompt/resources.py + config 覆盖 |
| F8: YAML 配置 | config/loader.py |
| F9: 双 Provider | provider/anthropic.py + provider/openai.py |
| F10: 抽象接口 | provider/base.py Provider Protocol |
| F11: Extended Thinking | provider/anthropic.py thinking 参数 + 思考增量丢弃 |
| F12: Markdown 渲染 | tui/renderer.py RichRenderer |
| F13: 错误反馈 | tui/app.py 错误样式 + StreamEvent.err |
| F14: 发起对话请求 | ConversationManager.get_context() + Provider.stream() |
| F15: 终端界面布局 | prompt/resources.py banner + tui/app.py 布局 |
| F16: 响应计时 | tui/app.py _show_timer() |
| F17: 流式中断 | tui/app.py _cancel_stream() ESC |
| 自动重试 | tui/app.py _consume_stream() 重试逻辑 |
| N1: 界面不阻塞 | asyncio 异步架构 |
| N2: 流式实时性 | Rich Live + 计时器 |
| N3: 跨协议一致 | Provider Protocol 统一接口 |
| N4: 配置健壮性 | config/loader.py 校验 |
| N5: 密钥安全 | ${ENV_VAR} 解析 |
| N6: 终端兼容 | prompt_toolkit + Rich 自适应 |
| N7: 退出整洁 | tui/app.py 退出清理 |