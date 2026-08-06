# MewCode TUI 多轮对话 — 任务拆分 (task.md)

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `pyproject.toml` | 项目元数据、依赖声明、入口点 |
| 新建 | `mewcode/__init__.py` | 包初始化 |
| 新建 | `mewcode/__main__.py` | `python -m mewcode` 入口 |
| 新建 | `mewcode/main.py` | CLI 入口：argparse、流程编排 |
| 新建 | `mewcode/config/__init__.py` | config 包初始化 |
| 新建 | `mewcode/config/schema.py` | ProviderConfig、Config 数据类 |
| 新建 | `mewcode/config/loader.py` | ConfigLoader：YAML 加载校验 |
| 新建 | `mewcode/provider/__init__.py` | create_provider() 工厂函数 |
| 新建 | `mewcode/provider/base.py` | BaseProvider 抽象基类 |
| 新建 | `mewcode/provider/anthropic.py` | AnthropicProvider 实现 |
| 新建 | `mewcode/provider/openai.py` | OpenAIProvider 实现 |
| 新建 | `mewcode/conversation/__init__.py` | conversation 包初始化 |
| 新建 | `mewcode/conversation/manager.py` | Message、ConversationManager |
| 新建 | `mewcode/tui/__init__.py` | tui 包初始化 |
| 新建 | `mewcode/tui/app.py` | REPL 类：prompt_toolkit 循环 |
| 新建 | `mewcode/tui/renderer.py` | RichRenderer：流式 Markdown 渲染 |
| 新建 | `mewcode/utils/__init__.py` | utils 包初始化 |
| 新建 | `mewcode/utils/error.py` | MewCodeError、ConfigError、ProviderError |

---

## T1: 项目骨架搭建

**文件：** `pyproject.toml`、`mewcode/__init__.py`、`mewcode/__main__.py`

**依赖：** 无

**步骤：**
1. 创建 `pyproject.toml`，声明项目名 `mewcode`，Python 版本 `>=3.10`
2. 声明依赖：`prompt_toolkit>=3.0`、`rich>=13.0`、`anthropic>=0.30`、`openai>=1.0`、`pyyaml>=6.0`
3. 配置 `[project.scripts]` 入口点：`mewcode = "mewcode.main:main"`
4. 创建所有 `__init__.py` 文件（空文件）
5. 创建 `__main__.py`，内容：`from mewcode.main import main; main()`

**验证：** `pip install -e .` 成功，`mewcode --help` 不报错（此时 main 尚未实现）

---

## T2: 错误类型定义

**文件：** `mewcode/utils/error.py`

**依赖：** 无

**步骤：**
1. 定义 `MewCodeError(Exception)` 基类
2. 定义 `ConfigError(MewCodeError)` — 配置相关错误（文件不存在、字段缺失、格式错误）
3. 定义 `ProviderError(MewCodeError)` — Provider 调用错误（API 错误、网络错误）

**验证：** `python -c "from mewcode.utils.error import ConfigError; raise ConfigError('test')"` 正常抛出

---

## T3: 配置数据类

**文件：** `mewcode/config/schema.py`

**依赖：** 无

**步骤：**
1. 定义 `ProviderConfig` 数据类，字段：`name: str`、`protocol: str`、`model: str`、`base_url: str`、`api_key: str`、`thinking: bool = False`
2. 定义 `Config` 数据类，字段：`provider: str`、`max_turns: int = 20`、`system_prompt: str = ""`、`providers: list[ProviderConfig]`

**验证：** `python -c "from mewcode.config.schema import Config, ProviderConfig; c = Config(provider='test', providers=[ProviderConfig(name='test', protocol='anthropic', model='x', base_url='http://x', api_key='x')])"` 无报错

---

## T4: 配置加载器

**文件：** `mewcode/config/loader.py`

**依赖：** T3

**步骤：**
1. 实现 `ConfigLoader` 类
2. `load(path: str) -> Config`：
   - 检查文件是否存在，不存在抛出 `ConfigError("配置文件不存在，请在 ~/.mewcode/config.yaml 创建配置")`
   - 用 `yaml.safe_load` 读取 YAML
   - 调用 `_resolve_env()` 解析 api_key 中的 `${ENV_VAR}` 占位符
   - 构造 `Config` 和 `ProviderConfig` 对象
   - 调用 `_validate()` 校验
3. `_resolve_env(value: str) -> str`：正则匹配 `${VAR_NAME}`，用 `os.environ.get()` 替换
4. `_validate(config: Config) -> None`：
   - `provider` 必须在 `providers` 列表中存在
   - 每个 provider 的 `name`、`protocol`、`model`、`base_url`、`api_key` 非空
   - `protocol` 只能是 `"anthropic"` 或 `"openai"`
   - 校验失败抛出 `ConfigError` 说明具体原因

**验证：** 创建临时 YAML 文件，调用 `ConfigLoader.load()` 验证返回正确 Config 对象；传入无效 YAML 验证抛出 ConfigError

---

## T5: 对话管理器

**文件：** `mewcode/conversation/manager.py`

**依赖：** 无

**步骤：**
1. 定义 `Message` 数据类，字段：`role: str`、`content: str`
2. 实现 `ConversationManager` 类：
   - `__init__(self, system_prompt: str, max_turns: int)`：保存 system_prompt 和 max_turns，初始化空消息列表
   - `add_user(self, content: str)`：追加 `Message(role="user", content=content)`
   - `add_assistant(self, content: str)`：追加 `Message(role="assistant", content=content)`，调用 `_trim()`
   - `get_context(self) -> list[Message]`：构造 `[system_message] + 窗口内消息` 列表
   - `_trim(self)`：统计 user/assistant 消息对数，超过 max_turns 时删除最早的一对
3. 默认 system prompt：`"你是一个 AI 编程助手 MewCode，运行在终端中。请用中文回复，回答简洁清晰。"`

**验证：** 创建 `ConversationManager("test", 3)`，添加 5 轮对话，验证 `get_context()` 只返回最近 3 轮

---

## T6: Provider 抽象接口

**文件：** `mewcode/provider/base.py`

**依赖：** T5（Message 类型）

**步骤：**
1. 定义 `BaseProvider(ABC)` 抽象基类
2. `__init__(self, config: ProviderConfig)`：保存 ProviderConfig 并初始化客户端
3. 定义抽象方法 `async stream(self, messages: list[Message]) -> AsyncIterator[str]`

**验证：** `python -c "from mewcode.provider.base import BaseProvider"` 导入成功

---

## T7: Anthropic Provider

**文件：** `mewcode/provider/anthropic.py`

**依赖：** T6

**步骤：**
1. 实现 `AnthropicProvider(BaseProvider)`：
   - `__init__`：创建 `anthropic.AsyncAnthropic(api_key=config.api_key, base_url=config.base_url)`
   - `stream(messages)`：
     - 将 `Message` 列表转换为 Anthropic SDK 的消息格式（不含 system 消息）
     - 提取 system prompt（role="system" 的第一条消息）
     - 调用 `client.messages.create(model=config.model, system=system_prompt, messages=api_messages, max_tokens=4096, stream=True, thinking=... 如果 config.thinking 为 True)`
     - 异步迭代 SSE 事件流，yield 每个 `text_delta` 中的文本
2. 处理 extended thinking：当 `thinking=True` 时，将 `thinking` 参数传入 SDK
3. 错误处理：捕获 `anthropic.APIError`，转换为 `ProviderError`

**验证：** 使用有效配置调用 `stream()`，验证能收到流式 token 输出

---

## T8: OpenAI Provider

**文件：** `mewcode/provider/openai.py`

**依赖：** T6

**步骤：**
1. 实现 `OpenAIProvider(BaseProvider)`：
   - `__init__`：创建 `openai.AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)`
   - `stream(messages)`：
     - 将 `Message` 列表转换为 OpenAI SDK 的消息格式
     - 调用 `client.chat.completions.create(model=config.model, messages=api_messages, stream=True)`
     - 异步迭代 SSE 事件流，yield 每个 `delta.content` 中的文本
2. 错误处理：捕获 `openai.APIError`，转换为 `ProviderError`

**验证：** 使用有效配置调用 `stream()`，验证能收到流式 token 输出

---

## T9: Provider 工厂函数

**文件：** `mewcode/provider/__init__.py`

**依赖：** T7、T8

**步骤：**
1. 实现 `create_provider(config: ProviderConfig) -> BaseProvider`：
   - `protocol == "anthropic"` → `AnthropicProvider(config)`
   - `protocol == "openai"` → `OpenAIProvider(config)`
   - 其他 → 抛出 `ConfigError`

**验证：** `python -c "from mewcode.provider import create_provider; from mewcode.config.schema import ProviderConfig; p = create_provider(ProviderConfig(name='t', protocol='anthropic', model='x', base_url='http://x', api_key='x'))"` 返回 AnthropicProvider 实例

---

## T10: Rich 流式渲染器

**文件：** `mewcode/tui/renderer.py`

**依赖：** 无

**步骤：**
1. 实现 `RichRenderer` 类：
   - `__init__`：创建 `rich.console.Console()` 实例
   - `render_stream(self, token_stream: AsyncIterator[str]) -> str`：
     - 使用 `rich.live.Live` 上下文
     - 维护缓冲区字符串
     - 异步迭代 token_stream，每个 token 追加到缓冲区
     - 用 `Markdown(buffer)` 构造 Rich 对象，调用 `live.update()`
     - 流结束后返回完整响应文本
2. 处理非 Markdown 文本（纯文本也可正常渲染）

**验证：** 构造模拟 token 流，调用 `render_stream()`，验证终端能看到流式更新的 Markdown 输出

---

## T11: TUI REPL 循环

**文件：** `mewcode/tui/app.py`

**依赖：** T4、T5、T9、T10

**步骤：**
1. 实现 `REPL` 类：
   - `__init__(self, provider: BaseProvider, conversation: ConversationManager, renderer: RichRenderer)`
   - `async run(self)`：主循环
2. 主循环逻辑：
   - 循环调用 `await prompt_toolkit.prompt_async("> "` 获取输入
   - 空输入跳过
   - 检测 `/exit`、`/quit` → break 退出
   - 检测 `Ctrl+C`（KeyboardInterrupt）→ break 退出
   - 检测 `Ctrl+D`（EOFError）→ break 退出
   - 非空输入 → 调用 `_process_input(text)`
3. `_process_input(text)`：
   - `conversation.add_user(text)`
   - 调用 `provider.stream(conversation.get_context())` 获取 token 流
   - 调用 `renderer.render_stream(token_stream)` 流式渲染
   - 将完整响应传入 `conversation.add_assistant()`
   - 捕获 `ProviderError` → 打印错误信息到 stderr，保留对话历史
4. 多行输入：使用 `prompt_toolkit` 的 `validator` 检测未闭合括号/引号

**验证：** 启动 `mewcode`，输入问题，观察流式回复和 Markdown 渲染

---

## T12: CLI 入口

**文件：** `mewcode/main.py`

**依赖：** T4、T5、T9、T11

**步骤：**
1. 实现 `main()` 函数：
   - 使用 `argparse` 定义参数：`-c/--command`（可选字符串）
   - 解析参数
2. 加载配置：`ConfigLoader.load(os.path.expanduser("~/.mewcode/config.yaml"))`
3. 找到激活的 provider config：`next(p for p in config.providers if p.name == config.provider)`
4. 创建 provider：`create_provider(provider_config)`
5. 创建 conversation manager：`ConversationManager(config.system_prompt, config.max_turns)`
6. 创建 renderer：`RichRenderer()`
7. 若 `-c` 存在：
   - `conversation.add_user(args.command)`
   - `provider.stream()` → `renderer.render_stream()` 流式输出
   - 退出
8. 否则：
   - 创建 `REPL(provider, conversation, renderer)`
   - `asyncio.run(repl.run())`

**验证：** `mewcode -c "你好"` 输出回复后退出；`mewcode` 进入 TUI 对话

---

## 执行顺序

```
T1（项目骨架）
 │
 ├──▶ T2（错误类型）────────────────────────────┐
 ├──▶ T3（配置数据类）──▶ T4（配置加载器）──────┤
 │                                              │
 ├──▶ T5（对话管理器）──────────────────────────┤
 │                                              │
 └──▶ T6（抽象接口）──┬──▶ T7（Anthropic）─────┤
                      └──▶ T8（OpenAI）────────┤
                                                │
                      T7 + T8 ──▶ T9（工厂）───┤
                                                │
                      T10（渲染器）─────────────┤
                                                │
                      T4 + T5 + T9 + T10 ──▶ T11（REPL）
                                                │
                      T4 + T5 + T9 + T11 ──▶ T12（入口）
```

T2、T3、T5、T10 可并行开发。T7 和 T8 可并行开发。

---

## 任务粒度汇总

| 任务 | 预估工作量 | 依赖数 |
|------|-----------|--------|
| T1 | 项目骨架 | 0 |
| T2 | 错误类型 | 0 |
| T3 | 配置数据类 | 0 |
| T4 | 配置加载器 | 1 |
| T5 | 对话管理器 | 0 |
| T6 | 抽象接口 | 1 |
| T7 | Anthropic Provider | 1 |
| T8 | OpenAI Provider | 1 |
| T9 | Provider 工厂 | 2 |
| T10 | Rich 渲染器 | 0 |
| T11 | TUI REPL | 4 |
| T12 | CLI 入口 | 4 |