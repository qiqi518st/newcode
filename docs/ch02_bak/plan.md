# MewCode TUI 多轮对话 — 技术设计 (plan.md)

## 架构概览

```
main.py                        — 入口：解析 CLI → 加载配置 → 创建 Provider → 分发到 TUI / 单次调用
├── config/
│   ├── schema.py              — 配置数据类 (ProviderConfig, Config)
│   └── loader.py             — YAML 加载、校验、${ENV_VAR} 解析
├── provider/
│   ├── base.py               — 抽象 Provider 接口 (BaseProvider)
│   ├── anthropic.py          — Anthropic 实现 (SSE 流式 + extended thinking)
│   └── openai.py             — OpenAI 实现 (SSE 流式)
├── conversation/
│   └── manager.py            — 消息列表维护、滑动窗口裁剪、system prompt 管理
├── tui/
│   ├── app.py                — prompt_toolkit REPL 循环、多行检测、退出处理
│   └── renderer.py           — Rich 库 Markdown 流式渲染
└── utils/
    └── error.py              — 自定义异常类型、错误信息格式化
```

**数据流：**

```
用户输入 → prompt_toolkit 读取（多行检测）
  → ConversationManager.append_user(content)
  → Provider.stream(manager.get_context())
  → Rich Renderer 流式渲染 Markdown 并逐 token 输出
  → ConversationManager.append_assistant(content)
  → 滑动窗口裁剪（超出 max_turns 时丢弃最早消息对）
  → 等待下一次输入
```

---

## 核心数据结构

### config/schema.py

```python
from dataclasses import dataclass, field

@dataclass
class ProviderConfig:
    """单个 LLM 供应商的配置"""
    name: str           # 供应商标识名，如 "anthropic-official"
    protocol: str       # 协议类型: "anthropic" | "openai"
    model: str          # 模型名，如 "claude-sonnet-4-20250514"
    base_url: str       # API 基础地址
    api_key: str        # 认证密钥（已解析 ${ENV_VAR} 后的明文）
    thinking: bool = False  # 是否启用 extended thinking（仅 Anthropic 有效）

@dataclass
class Config:
    """MewCode 全局配置"""
    provider: str                     # 当前激活的 provider name
    max_turns: int = 20               # 滑动窗口保留轮数
    system_prompt: str = ""           # 自定义 system prompt，空则用内置默认值
    providers: list[ProviderConfig] = field(default_factory=list)
```

### conversation/manager.py

```python
from dataclasses import dataclass

@dataclass
class Message:
    """单条对话消息"""
    role: str       # "system" | "user" | "assistant"
    content: str

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
```

### provider/base.py

```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

class BaseProvider(ABC):
    """LLM Provider 抽象接口，所有后端通过此接口统一调用"""

    def __init__(self, config: ProviderConfig) -> None: ...

    @abstractmethod
    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        """流式返回 LLM 回复 token 序列，每个 token 是一个字符串"""
        ...
```

---

## 模块设计

### 模块 A: CLI 入口 (main.py)

**职责：** 解析命令行参数，加载配置，创建 Provider 和 ConversationManager，根据模式分发到 TUI 或单次调用。

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
2. 调用 ConfigLoader 加载 `~/.mewcode/config.yaml`
3. 根据 `config.provider` 找到对应的 ProviderConfig
4. 调用 `create_provider()` 工厂函数创建 Provider 实例
5. 创建 ConversationManager 实例
6. 若 `-c` 存在：调用 `oneshot()` 单次调用流程
7. 否则：启动 TUI REPL 循环

**依赖：** config 层、provider 层、conversation 层、tui 层

---

### 模块 B: 配置层 (config/)

**职责：** 加载 YAML 配置文件，校验字段完整性，解析 `${ENV_VAR}` 占位符，返回 Config 对象。

**子模块：**

#### config/schema.py
- `ProviderConfig` — provider 配置数据类
- `Config` — 全局配置数据类

#### config/loader.py
- `ConfigLoader` 类

**对外接口：**
- `ConfigLoader.load(path: str) -> Config` — 加载并校验配置文件
- `ConfigLoader._resolve_env(value: str) -> str` — 解析 `${ENV_VAR}` 占位符
- `ConfigLoader._validate(config: Config) -> None` — 校验字段完整性

**校验规则：**
- 配置文件必须存在，否则抛出 `ConfigError` 提示用户创建
- `provider` 字段必须在 `providers` 列表中存在
- 每个 provider 的 `name`、`protocol`、`model`、`base_url`、`api_key` 必填
- `protocol` 只能为 `"anthropic"` 或 `"openai"`
- `api_key` 支持 `${ENV_VAR}` 语法，从环境变量读取

**依赖：** pyyaml、os.environ

---

### 模块 C: Provider 层 (provider/)

**职责：** 封装 Anthropic SDK 和 OpenAI SDK 的差异，提供统一的流式调用接口。

**子模块：**

#### provider/base.py
- `BaseProvider` — 抽象基类，定义 `stream()` 接口

#### provider/anthropic.py
- `AnthropicProvider` — 实现 Anthropic SSE 流式调用
  - 使用 `anthropic.AsyncAnthropic` 客户端
  - 配置 `base_url` 和 `api_key`
  - 调用 `messages.stream()` 获取 SSE 事件流
  - 处理 `thinking` 参数：仅当 `config.thinking=True` 时启用
  - 逐 token yield `text_delta` 事件中的文本

#### provider/openai.py
- `OpenAIProvider` — 实现 OpenAI SSE 流式调用
  - 使用 `openai.AsyncOpenAI` 客户端
  - 配置 `base_url` 和 `api_key`
  - 调用 `chat.completions.create(stream=True)` 获取 SSE 事件流
  - 逐 token yield `delta.content` 中的文本

**工厂函数：**
```python
def create_provider(config: ProviderConfig) -> BaseProvider:
    if config.protocol == "anthropic":
        return AnthropicProvider(config)
    elif config.protocol == "openai":
        return OpenAIProvider(config)
    else:
        raise ConfigError(f"Unknown protocol: {config.protocol}")
```

**依赖：** anthropic SDK、openai SDK

---

### 模块 D: 对话管理 (conversation/)

**职责：** 维护消息列表，实现滑动窗口上下文裁剪，管理 system prompt。

**子模块：**

#### conversation/manager.py
- `Message` — 消息数据类
- `ConversationManager` — 对话管理器

**关键逻辑：**
- `get_context()` 返回 `[system_prompt_message] + 窗口内消息`
- `_trim()` 在消息数超过 `max_turns * 2`（每轮 2 条 user/assistant）时，丢弃最早的一对
- system prompt 为空时，使用内置默认值（见下方）

**默认 System Prompt：**
```
"你是一个 AI 编程助手 MewCode，运行在终端中。请用中文回复，回答简洁清晰。"
```

**依赖：** 无外部依赖

---

### 模块 E: TUI 层 (tui/)

**职责：** 基于 prompt_toolkit 提供 Claude Code 风格的 REPL 交互界面，支持多行输入、流式 Markdown 渲染、多种退出方式。

**子模块：**

#### tui/app.py
- `REPL` 类 — TUI 主循环

**关键实现：**

1. **REPL 循环**：
   - 使用 `asyncio` 事件循环
   - 每次循环调用 `prompt_toolkit.prompt()` 获取用户输入
   - 处理 `/exit`、`/quit` 指令（立即退出）
   - 将输入传给 ConversationManager，调用 Provider 流式获取回复
   - 重复直到退出

2. **多行输入检测**：
   - 使用 `prompt_toolkit` 的 `validator` 检测未闭合括号/引号
   - 未闭合时自动换行，不发送请求

3. **退出处理**：
   - `/exit`、`/quit` — 在输入处理中检测
   - `Ctrl+C` — 触发 `KeyboardInterrupt`，退出循环
   - `Ctrl+D` — 触发 `EOFError`，退出循环

4. **流式输出**：
   - 调用 `Provider.stream()` 异步迭代 token
   - 使用 `RichRenderer` 渲染每个 token 并输出

#### tui/renderer.py
- `RichRenderer` 类 — 流式 Markdown 渲染

**关键实现：**
- 使用 `rich.console.Console` 作为输出目标
- 使用 `rich.live.Live` 配合 `rich.markdown.Markdown` 实现流式渲染
- 每收到一个 token，追加到缓冲区，重新渲染完整 Markdown
- 流式结束后，保留最终渲染结果

**依赖：** prompt_toolkit、rich、asyncio

---

### 模块 F: 工具层 (utils/)

**职责：** 自定义异常类型和错误信息格式化。

**子模块：**

#### utils/error.py
```python
class MewCodeError(Exception): ...
class ConfigError(MewCodeError): ...    # 配置相关的错误
class ProviderError(MewCodeError): ...  # Provider 调用相关的错误
```

**错误处理策略（对应 F13）：**
- 捕获 `anthropic.APIError` / `openai.APIError` → 转换为 `ProviderError`
- 捕获网络异常 → 转换为 `ProviderError`
- TUI 层捕获后，显示错误信息到 stderr，保留对话历史，等待用户下一次输入

---

## 模块交互

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────────┐
│   main.py    │────▶│  ConfigLoader   │────▶│  ~/.mewcode/     │
│  (入口)      │     │  .load()        │     │  config.yaml     │
└──────┬───────┘     └─────────────────┘     └──────────────────┘
       │
       │ Config
       ▼
┌──────────────────┐     ┌──────────────────┐
│  create_provider │────▶│  ProviderFactory │
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
│ Conversation │◀─────────────┘ stream(messages)
│  Manager     │
└──────┬───────┘
       │
       │ get_context() / append_assistant()
       ▼
┌──────────────┐     ┌──────────────┐
│   REPL       │────▶│  RichRenderer│
│  (TUI 循环)  │     │  (流式渲染)  │
└──────────────┘     └──────────────┘
```

**典型调用链（一次用户输入）：**

1. `main.py` → `REPL.run()` 启动循环
2. `REPL` → `prompt_toolkit.prompt("> ")` 获取用户输入
3. `REPL` → `ConversationManager.add_user(input)`
4. `REPL` → `ConversationManager.get_context()` 获取上下文
5. `REPL` → `Provider.stream(context)` 异步迭代 token
6. 每个 token → `RichRenderer.render(token)` 流式输出
7. 流结束后 → `ConversationManager.add_assistant(full_response)`
8. 回到步骤 2

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
│   └── loader.py            # ConfigLoader: YAML 加载、校验、env 解析
├── provider/
│   ├── __init__.py          # create_provider() 工厂函数
│   ├── base.py              # BaseProvider 抽象类
│   ├── anthropic.py         # AnthropicProvider 实现
│   └── openai.py            # OpenAIProvider 实现
├── conversation/
│   ├── __init__.py
│   └── manager.py           # Message, ConversationManager
├── tui/
│   ├── __init__.py
│   ├── app.py               # REPL 类：prompt_toolkit 循环
│   └── renderer.py          # RichRenderer：流式 Markdown 渲染
└── utils/
    ├── __init__.py
    └── error.py             # MewCodeError, ConfigError, ProviderError
```

---

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| TUI 框架 | prompt_toolkit | 用户指定，Claude Code 同类方案，成熟的输入处理 |
| Markdown 渲染 | Rich | 用户指定，终端渲染效果好，支持 Live 流式更新 |
| API 调用 | 官方 SDK (anthropic + openai) | 用户指定，原生支持 SSE 流式和 extended thinking |
| 异步模型 | asyncio + async/await | 两个 SDK 均提供 AsyncClient，流式迭代天然适合 async |
| 配置格式 | YAML + pyyaml | 用户指定，可读性好，支持嵌套结构 |
| 流式渲染策略 | Rich Live + 缓冲区累积重渲染 | 逐 token 追加到缓冲区，用 Live 实时重渲染，兼顾流式体验和 Markdown 正确性 |
| 项目结构 | Python 包 + pyproject.toml | 标准 Python 项目结构，支持 pip install -e |
| API Key 安全 | `${ENV_VAR}` 占位符 | 避免密钥硬编码在配置文件中 |
| 配置缺失处理 | 提示用户并退出 | 用户指定，强制显式配置 |

---

## spec 需求覆盖

| spec 需求 | 对应实现 |
|-----------|---------|
| F1: TUI 多轮对话 | tui/app.py REPL 循环 |
| F2: 单次调用模式 | main.py `-c` 分支 |
| F3: 流式输出 (SSE) | provider/*.py 的 stream() 方法 |
| F4: 滑动窗口 | conversation/manager.py _trim() |
| F5: 多行输入 | tui/app.py prompt_toolkit validator |
| F6: 多种退出 | tui/app.py 退出处理 |
| F7: System Prompt | conversation/manager.py 默认值 + 配置覆盖 |
| F8: YAML 配置 | config/loader.py |
| F9: 双 Provider | provider/anthropic.py + provider/openai.py |
| F10: 抽象接口 | provider/base.py BaseProvider |
| F11: Extended Thinking | provider/anthropic.py thinking 参数 |
| F12: Markdown 渲染 | tui/renderer.py RichRenderer |
| F13: 优雅降级 | utils/error.py + tui/app.py 异常捕获 |