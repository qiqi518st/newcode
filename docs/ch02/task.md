# NewCode TUI 多轮对话 — 任务拆分 (task.md)

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `pyproject.toml` | 项目元数据、依赖声明、入口点 |
| 新建 | `newcode/__init__.py` | 包初始化 |
| 新建 | `newcode/__main__.py` | `python -m newcode` 入口 |
| 新建 | `newcode/main.py` | CLI 入口：argparse、流程编排、banner 打印 |
| 新建 | `newcode/config/__init__.py` | config 包初始化 |
| 新建 | `newcode/config/schema.py` | ProviderConfig、Config 数据类（Literal、Optional） |
| 新建 | `newcode/config/loader.py` | load()：YAML 加载、校验、${ENV_VAR} 解析 |
| 新建 | `newcode/provider/__init__.py` | 空（工厂函数定义在 base.py） |
| 新建 | `newcode/provider/base.py` | Provider Protocol、StreamEvent、Message、new_provider() |
| 新建 | `newcode/provider/anthropic.py` | AnthropicProvider：SSE 流式、thinking 丢弃、StreamEvent |
| 新建 | `newcode/provider/openai.py` | OpenAIProvider：SSE 流式、StreamEvent |
| 新建 | `newcode/conversation/__init__.py` | conversation 包初始化 |
| 新建 | `newcode/conversation/manager.py` | ConversationManager：滑动窗口、上下文管理 |
| 新建 | `newcode/prompt/__init__.py` | prompt 包初始化 |
| 新建 | `newcode/prompt/resources.py` | SYSTEM_PROMPT、DOG_BANNER、render_banner() |
| 新建 | `newcode/tui/__init__.py` | tui 包初始化 |
| 新建 | `newcode/tui/app.py` | REPL 类：状态机、prompt_toolkit 循环、流式消费、计时、ESC 中断、重试 |
| 新建 | `newcode/tui/renderer.py` | RichRenderer：流式 Markdown 渲染 |
| 新建 | `newcode/utils/__init__.py` | utils 包初始化 |
| 新建 | `newcode/utils/error.py` | NewCodeError、ConfigError、ProviderError |

---

## T1: 初始化 Python 项目骨架与依赖

**文件：** `pyproject.toml`、`newcode/__init__.py`、`newcode/__main__.py`、`newcode/main.py`（临时占位）

**依赖：** 无

**步骤：**
1. 创建 `pyproject.toml`，关键字段：
   ```toml
   [project]
   name = "newcode"
   version = "0.1.0"
   requires-python = ">=3.10"
   dependencies = [
     "prompt_toolkit>=3.0",
     "rich>=13",
     "anthropic>=0.40",
     "openai>=1.50",
     "pyyaml>=6",
   ]

   [project.scripts]
   newcode = "newcode.main:main"

   [build-system]
   requires = ["hatchling"]
   build-backend = "hatchling.build"

   [dependency-groups]
   dev = ["pytest>=8", "ruff>=0.6", "mypy>=1.10"]
   ```
2. `newcode/__init__.py`：定义 `__version__ = "0.1.0"`。
3. `newcode/__main__.py`：`from .main import main; main()`。
4. `newcode/main.py` 写一个临时 `main()`，打印 `f"newcode {__version__}"` 并退出，确保可启动。
5. 创建所有子包的 `__init__.py` 文件（空文件）。
6. 安装依赖：`pip install -e ".[dev]"`。

**验证：** `python -m newcode` 能打印版本号；`newcode` 同样可用；`pip list` 能看到上述依赖。

---

## T2: 错误类型定义

**文件：** `newcode/utils/error.py`

**依赖：** 无

**步骤：**
1. 定义 `NewCodeError(Exception)` 基类
2. 定义 `ConfigError(NewCodeError)` — 配置相关错误（文件不存在、字段缺失、格式错误、protocol 非法）
3. 定义 `ProviderError(NewCodeError)` — Provider 调用错误（API 错误、网络错误）

**验证：** `python -c "from newcode.utils.error import ConfigError, ProviderError; raise ConfigError('test')"` 正常抛出

---

## T3: 配置数据类

**文件：** `newcode/config/schema.py`

**依赖：** 无

**步骤：**
1. 定义 `ProviderConfig` 数据类：
   - `name: str`
   - `protocol: Literal["anthropic", "openai"]`
   - `model: str`
   - `api_key: str`
   - `base_url: str | None = None`（None 表示用 SDK 默认端点）
   - `thinking: bool = False`
2. 定义 `Config` 数据类：
   - `provider: str`（当前激活的 provider name）
   - `max_turns: int = 20`
   - `system_prompt: str = ""`
   - `providers: list[ProviderConfig]`

**验证：** `python -c "from newcode.config.schema import Config, ProviderConfig; c = Config(provider='test', providers=[ProviderConfig(name='test', protocol='anthropic', model='x', api_key='x')])"` 无报错

---

## T4: 配置加载器

**文件：** `newcode/config/loader.py`

**依赖：** T3

**步骤：**
1. 实现 `load(path: str) -> Config` 函数：
   - 检查文件是否存在，不存在抛出 `ConfigError("配置文件不存在，请复制 .newcode.yaml.example 为 .newcode.yaml")`
   - 用 `yaml.safe_load` 读取 YAML
   - 遍历所有 provider，解析 `api_key` 中的 `${ENV_VAR}` 占位符
   - 构造 `Config` 和 `ProviderConfig` 对象
   - 调用内部校验函数
2. `_resolve_env(value: str) -> str`：正则匹配 `${VAR_NAME}`，用 `os.environ.get()` 替换，若环境变量不存在则报错
3. `_validate(config: Config) -> None`：
   - `provider` 必须在 `providers` 列表中存在
   - 每个 provider 的 `name`、`protocol`、`model`、`api_key` 非空
   - `protocol` 只能是 `"anthropic"` 或 `"openai"`
   - 校验失败抛出 `ConfigError` 说明具体原因

**验证：** 创建临时 YAML 文件，调用 `load()` 验证返回正确 Config 对象；传入无效 YAML 验证抛出 ConfigError；设置环境变量后验证 `${ENV_VAR}` 正确解析

---

## T5: 提示词与资源

**文件：** `newcode/prompt/resources.py`

**依赖：** 无

**步骤：**
1. 定义 `SYSTEM_PROMPT` 常量：`"你是一个 AI 编程助手 NewCode，运行在终端中。请用中文回复，回答简洁清晰。"`
2. 定义 `DOG_BANNER` 常量：ASCII 小狗图案（多行字符串）
3. 实现 `render_banner(version: str, cwd: str) -> str`：
   - 拼接 `DOG_BANNER` + 应用名与版本号 + 当前工作目录
   - 返回完整启动横幅字符串

**验证：** `python -c "from newcode.prompt.resources import render_banner; print(render_banner('0.1.0', '/home/user'))"` 输出包含小狗、版本号、路径

---

## T6: 对话管理器

**文件：** `newcode/conversation/manager.py`

**依赖：** T5（SYSTEM_PROMPT）

**步骤：**
1. 实现 `ConversationManager` 类：
   - `__init__(self, system_prompt: str, max_turns: int)`：保存 system_prompt 和 max_turns，初始化空消息列表。若 system_prompt 为空，使用 `prompt.SYSTEM_PROMPT`
   - `add_user(self, content: str)`：追加 `Message(role="user", content=content)`
   - `add_assistant(self, content: str)`：追加 `Message(role="assistant", content=content)`，调用 `_trim()`
   - `get_context(self) -> list[Message]`：构造 `[system_message] + 窗口内消息` 列表
   - `_trim(self)`：统计 user/assistant 消息对数，超过 max_turns 时删除最早的一对（一条 user + 一条 assistant）

**验证：** 创建 `ConversationManager("test", 2)`，添加 4 轮对话，验证 `get_context()` 只返回最近 2 轮 + system prompt

---

## T7: LLM 基础类型与工厂

**文件：** `newcode/provider/base.py`

**依赖：** T3（ProviderConfig）

**步骤：**
1. 定义 `Message` 数据类：`role: Literal["user", "assistant"]`、`content: str`
2. 定义 `StreamEvent` 数据类：
   - `text: str = ""` — 文本增量
   - `done: bool = False` — 本轮正常结束
   - `err: Exception | None = None` — 出错（与 done 互斥）
3. 定义 `Provider` Protocol：
   - `name: str` 属性
   - `model: str` 属性
   - `stream(self, msgs: list[Message]) -> AsyncIterator[StreamEvent]` 方法
4. 实现 `new_provider(cfg: ProviderConfig) -> Provider` 工厂函数：
   - `protocol == "anthropic"` → `AnthropicProvider(cfg)`
   - `protocol == "openai"` → `OpenAIProvider(cfg)`
   - 其他 → 抛出 `ConfigError`

**验证：** 模块导入成功，`new_provider()` 根据 protocol 返回正确类型

---

## T8: Anthropic Provider

**文件：** `newcode/provider/anthropic.py`

**依赖：** T7

**步骤：**
1. 实现 `AnthropicProvider` 类，满足 `Provider` Protocol：
   - `__init__`：创建 `anthropic.AsyncAnthropic(api_key=cfg.api_key, base_url=cfg.base_url)`（base_url 为 None 时用 SDK 默认）
   - `name` 属性 → 返回 `cfg.name`
   - `model` 属性 → 返回 `cfg.model`
   - `stream(messages)`：
     - 从 messages 中提取 system prompt（role="system" 的第一条）
     - 将剩余消息转换为 Anthropic SDK 格式
     - 调用 `client.messages.create(model=cfg.model, system=system_prompt, messages=api_messages, max_tokens=4096, stream=True)`，若 `cfg.thinking=True` 则传入 thinking 参数
     - 异步迭代 SSE 事件流：
       - 遇到 `text_delta` → yield `StreamEvent(text=...)`
       - 遇到 thinking 相关事件 → 丢弃，不 yield
       - 流正常结束 → yield `StreamEvent(done=True)`
     - 捕获 `anthropic.APIError` → yield `StreamEvent(err=ProviderError(...))`
     - 捕获其他异常 → yield `StreamEvent(err=ProviderError(...))`

**验证：** 使用有效配置调用 `stream()`，验证能收到 `StreamEvent` 序列，thinking 内容不出现在 text 中

---

## T9: OpenAI Provider

**文件：** `newcode/provider/openai.py`

**依赖：** T7

**步骤：**
1. 实现 `OpenAIProvider` 类，满足 `Provider` Protocol：
   - `__init__`：创建 `openai.AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url)`（base_url 为 None 时用 SDK 默认）
   - `name` 属性 → 返回 `cfg.name`
   - `model` 属性 → 返回 `cfg.model`
   - `stream(messages)`：
     - 将 messages 转换为 OpenAI SDK 格式（role、content 直接映射）
     - 调用 `client.chat.completions.create(model=cfg.model, messages=api_messages, stream=True)`
     - 异步迭代 SSE 事件流：
       - `delta.content` 非空 → yield `StreamEvent(text=delta.content)`
       - 流正常结束 → yield `StreamEvent(done=True)`
     - 捕获 `openai.APIError` → yield `StreamEvent(err=ProviderError(...))`
     - 捕获其他异常 → yield `StreamEvent(err=ProviderError(...))`

**验证：** 使用有效配置调用 `stream()`，验证能收到 `StreamEvent` 序列

---

## T10: Rich 流式渲染器

**文件：** `newcode/tui/renderer.py`

**依赖：** 无

**步骤：**
1. 实现 `RichRenderer` 类：
   - `__init__`：创建 `rich.console.Console()` 实例
   - `render_stream(self, token_stream: AsyncIterator[StreamEvent]) -> str`：
     - 使用 `rich.live.Live` 上下文
     - 维护缓冲区字符串
     - 异步迭代 token_stream：
       - `StreamEvent.text` → 追加到缓冲区，用 `Markdown(buffer)` 构造 Rich 对象，调用 `live.update()`
       - `StreamEvent.done` → 退出 Live，返回完整响应文本
       - `StreamEvent.err` → 退出 Live，抛出异常（由调用方处理）
2. `render_static(self, content: str) -> None`：静态渲染 Markdown（用于定型展示）

**验证：** 构造模拟 `StreamEvent` 序列，调用 `render_stream()`，验证终端看到流式更新的 Markdown 输出

---

## T11: TUI REPL 循环

**文件：** `newcode/tui/app.py`

**依赖：** T4、T6、T7、T8、T9、T10

**步骤：**
1. 定义 `SessionState` 枚举：`IDLE`、`STREAMING`
2. 实现 `REPL` 类：
   - `__init__(self, provider: Provider, conversation: ConversationManager, renderer: RichRenderer)`
   - 初始化 `state = SessionState.IDLE`、`cur_reply = ""`、`turn_start = 0.0`、`_stream_task = None`、`_retry_count = 0`

3. 实现 `async run(self)` 主循环：
   - 循环调用 `await prompt_toolkit.prompt_async("❯ ")` 获取输入
   - 空输入跳过
   - 检测 `/exit`、`/quit` → break 退出
   - 检测 `Ctrl+C`（KeyboardInterrupt）→ 若在 STREAMING 则 `_cancel_stream()`，否则 break 退出
   - 检测 `Ctrl+D`（EOFError）→ break 退出
   - 非空输入 → 调用 `await _process_input(text)`

4. 实现 `async _process_input(self, text: str)`：
   - 切换状态为 STREAMING
   - `conversation.add_user(text)`
   - 重置 `_retry_count = 0`，记录 `turn_start = time.monotonic()`
   - 创建 `_stream_task = asyncio.create_task(_consume_stream())`
   - 等待 `_stream_task` 完成

5. 实现 `async _consume_stream(self)`：
   - 循环（最多 4 次 = 1 次原始 + 3 次重试）：
     - `async for event in provider.stream(conversation.get_context())`：
       - `event.text` → 追加到 `cur_reply`，调用 `renderer.render_stream()` 更新
       - `event.done` → `conversation.add_assistant(cur_reply)`，显示耗时 "Done (Ns)"，return
       - `event.err` → 若 `_retry_count < 3`：`_retry_count += 1`，显示重试提示（可区分样式），`await asyncio.sleep(3)`，break 内层循环进入重试
       - 若重试次数耗尽 → 显示错误信息（红色/可区分样式），保留对话历史，return
   - 最终恢复状态为 IDLE

6. 实现 `_cancel_stream(self)`：
   - `_stream_task.cancel()`
   - 显示取消提示，`cur_reply` 清空，状态恢复 IDLE

7. 实现 `_show_timer(self) -> str`：
   - 返回 `"Imagining… ({:.0f}s)".format(time.monotonic() - turn_start)`

8. 多行输入配置：
   - 使用 `prompt_toolkit` 的 `key_bindings` 绑定 `Alt+Enter` 插入换行
   - 输入框底部提示 "Alt+Enter 换行，Enter 发送"

9. 退出清理：
   - 退出循环后恢复终端状态

**验证：**
- 自动（接线测试）：用 mock provider 产出 `StreamEvent.text` 序列，驱动 `_consume_stream()`，用 `rich.console` capture 断言渲染后的文本出现在输出中 — 确认 `_consume_stream` 真的调用了渲染，而非只累积文本。
- 交互（真实终端）：启动 `newcode`，输入问题，验证流式回复、Markdown 渲染、计时显示、ESC 中断、重试展示。此步在真实终端执行，无法自动跑。

---

## T12: CLI 入口

**文件：** `newcode/main.py`

**依赖：** T4、T5、T6、T7、T11

**步骤：**
1. 实现 `main()` 函数：
   - 使用 `argparse` 定义参数：`-c/--command`（可选字符串）
   - 解析参数
2. 加载配置：`config.load(os.path.join(os.getcwd(), ".newcode.yaml"))`
3. 找到激活的 provider config：`next(p for p in config.providers if p.name == config.provider)`
4. 创建 provider：`new_provider(provider_config)`
5. 创建 conversation manager：`ConversationManager(config.system_prompt, config.max_turns)`
6. 创建 renderer：`RichRenderer()`
7. 若 `-c` 存在：
   - `conversation.add_user(args.command)`
   - 调用 `provider.stream()` → `renderer.render_stream()` 流式输出
   - 退出
8. 否则：
   - 打印 `render_banner(VERSION, os.getcwd())`
   - 创建 `REPL(provider, conversation, renderer)`
   - `asyncio.run(repl.run())`

**验证：** `newcode -c "你好"` 输出回复后退出；`newcode` 打印 banner 后进入 TUI 对话

---

## 执行顺序

```
T1（项目骨架）
 │
 ├──▶ T2（错误类型）──────────────────────────────────┐
 ├──▶ T3（配置数据类）──▶ T4（配置加载器）───────────┤
 ├──▶ T5（提示词资源）──▶ T6（对话管理器）───────────┤
 │                                                      │
 └──▶ T3 ──▶ T7（LLM 基础类型）──┬──▶ T8（Anthropic）┤
                                  └──▶ T9（OpenAI）───┤
                                                      │
                        T10（Rich 渲染器）────────────┤
                                                      │
      T4 + T6 + T7 + T8 + T9 + T10 ──▶ T11（REPL）──┤
                                                      │
            T4 + T6 + T7 + T11 ──▶ T12（CLI 入口）───┘
```

T2、T3、T5、T10 可并行开发。T8 和 T9 可并行开发。

---

## 任务粒度汇总

| 任务 | 预估工作量 | 依赖数 |
|------|-----------|--------|
| T1 | 项目骨架 | 0 |
| T2 | 错误类型 | 0 |
| T3 | 配置数据类 | 0 |
| T4 | 配置加载器 | 1 |
| T5 | 提示词资源 | 0 |
| T6 | 对话管理器 | 1 |
| T7 | LLM 基础类型 | 1 |
| T8 | Anthropic Provider | 1 |
| T9 | OpenAI Provider | 1 |
| T10 | Rich 渲染器 | 0 |
| T11 | TUI REPL | 5 |
| T12 | CLI 入口 | 4 |