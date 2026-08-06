# MewCode 多轮对话 — 任务拆分 (tasks.md)

## 任务总览

共 9 个任务，每个任务可在一次专注会话内完成。按依赖关系排序，先基础设施后功能接入。

---

### 任务 1：项目脚手架搭建

**影响文件**：`pyproject.toml`、`mewcode/__init__.py`、`README.md`

**依赖**：无

**内容**：
- 创建 `pyproject.toml`，声明项目元信息、Python 版本要求（≥3.10）
- 添加依赖：`anthropic`、`openai`、`pyyaml`
- 创建 `mewcode/` 包目录及 `__init__.py`
- 创建 `README.md` 简要说明项目

**验收**：`pip install -e .` 成功后 `import mewcode` 不报错

---

### 任务 2：配置模块

**影响文件**：`mewcode/config.py`、`mewcode/config.yaml.example`

**依赖**：任务 1

**内容**：
- 定义配置数据类：`Config`（provider、max_turns、system_prompt）、`ProviderConfig`（name、protocol、base_url、api_key、model、thinking）
- 实现 `load_config()` 函数：从 `~/.mewcode/config.yaml` 读取 YAML，解析为 `Config` 对象
- 配置文件不存在时返回内置默认值（默认 provider 为 `anthropic-official`，包含火山引擎 API 的默认配置）
- 支持 `${ENV_VAR}` 语法在 api_key 字段中引用环境变量
- 创建 `config.yaml.example` 示例文件

**参考资料**：
- `pyyaml` 文档：`yaml.safe_load()`
- `pathlib.Path.home()` 获取用户主目录
- `os.path.expandvars()` 或 `re.sub()` 处理 `${VAR}` 环境变量替换

**验收**：手动创建/删除 `~/.mewcode/config.yaml`，验证默认值和自定义值均可正确加载

---

### 任务 3：Provider 抽象层

**影响文件**：`mewcode/providers/__init__.py`、`mewcode/providers/base.py`

**依赖**：任务 2

**内容**：
- 定义抽象基类 `BaseProvider`：
  - `__init__(self, config: ProviderConfig)` — 初始化 provider 配置
  - `chat(self, messages: list[dict], system_prompt: str) -> Iterator[str]` — 流式对话，返回 token 迭代器
- 在 `__init__.py` 中暴露 `create_provider(config: ProviderConfig) -> BaseProvider` 工厂函数，根据 `protocol` 字段路由到具体实现

**参考资料**：
- `abc.ABC`、`abc.abstractmethod`
- `collections.abc.Iterator`

**验收**：`create_provider()` 对 `protocol: "anthropic"` 返回 AnthropicProvider 实例，对 `protocol: "openai"` 返回 OpenAIProvider 实例

---

### 任务 4：Anthropic Protocol Provider

**影响文件**：`mewcode/providers/anthropic_provider.py`

**依赖**：任务 3

**内容**：
- 实现 `AnthropicProvider(BaseProvider)`：
  - 使用 `anthropic.Anthropic(base_url=..., api_key=...)` 初始化客户端
  - 实现 `chat()` 方法：
    - 调用 `client.messages.create(model=..., max_tokens=..., system=system_prompt, messages=messages, stream=True)`
    - 遍历 SSE 事件流，yield `TextDelta.text` 
    - 处理 `thinking` 块（如果 `config.thinking=True`，在 stream 中设置 `thinking={"type": "adaptive"}`）
  - 错误处理：捕获 `anthropic.APIStatusError` 等异常，转为 `ProviderError` 抛出

**参考资料**：
- Anthropic Python SDK 文档（见 `python/claude-api/streaming.md`）：
  ```python
  with client.messages.stream(model=..., max_tokens=..., messages=...) as stream:
      for text in stream.text_stream:
          yield text
  ```
- 或使用低级别事件循环：
  ```python
  for event in client.messages.create(..., stream=True):
      if event.type == "content_block_delta" and event.delta.type == "text_delta":
          yield event.delta.text
  ```
- 支持 `thinking: {"type": "adaptive"}` 用于开启扩展思考

**验收**：用真实 API key 调用，验证可流式收到回复文本

---

### 任务 5：OpenAI Protocol Provider

**影响文件**：`mewcode/providers/openai_provider.py`

**依赖**：任务 3

**内容**：
- 实现 `OpenAIProvider(BaseProvider)`：
  - 使用 `openai.OpenAI(base_url=..., api_key=...)` 初始化客户端
  - 实现 `chat()` 方法：
    - 拼接 system prompt 到 messages 列表头部
    - 调用 `client.chat.completions.create(model=..., messages=messages, stream=True)`
    - 遍历 `stream`，yield `chunk.choices[0].delta.content`（跳过 None）
  - 错误处理：捕获 `openai.APIError` 等异常，转为 `ProviderError` 抛出

**参考资料**：
- OpenAI Python SDK 流式调用：
  ```python
  stream = client.chat.completions.create(
      model=config.model,
      messages=[{"role": "system", "content": system_prompt}] + messages,
      stream=True,
  )
  for chunk in stream:
      if chunk.choices[0].delta.content:
          yield chunk.choices[0].delta.content
  ```

**验收**：用真实 API key 调用，验证可流式收到回复文本

---

### 任务 6：对话管理模块

**影响文件**：`mewcode/conversation.py`

**依赖**：任务 3（需要 `BaseProvider` 类型）

**内容**：
- 实现 `ConversationManager` 类：
  - `__init__(self, provider: BaseProvider, system_prompt: str, max_turns: int)` — 初始化
  - `messages: list[dict]` — 内部消息列表，格式 `[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]`
  - `send(user_message: str) -> Iterator[str]` — 接受用户输入，调用 provider 流式对话，yield token，同时收集完整回复并追加到 messages
  - `_trim_history()` — 私有方法，当 messages 超过 `max_turns * 2` 条时，丢弃最前面的一对 user+assistant
  - `get_history() -> list[dict]` — 返回当前对话历史

**参考资料**：
- 消息格式：`[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]`
- 滑动窗口：保留最近 `max_turns` 轮，每轮 = 1 user + 1 assistant，共 `max_turns * 2` 条

**验收**：构造 mock provider，验证消息列表正确增长、滑动窗口正确裁剪

---

### 任务 7：流式输出处理

**影响文件**：`mewcode/streaming.py`

**依赖**：任务 6

**内容**：
- 实现 `stream_response(token_iterator: Iterator[str]) -> str` 函数：
  - 遍历 token 迭代器，逐个打印 token 到 stdout（`print(token, end="", flush=True)`）
  - 收集完整文本
  - 最后打印换行符
  - 返回完整回复文本
- 处理 `KeyboardInterrupt`（Ctrl+C 中断流式输出）：中断当前输出但不退出 REPL

**验收**：模拟 token 迭代器，验证逐字输出到终端

---

### 任务 8：REPL 循环 + CLI 入口

**影响文件**：`mewcode/repl.py`、`mewcode/main.py`

**依赖**：任务 6、任务 7

**内容**：

`mewcode/repl.py`：
- 实现 `REPL` 类：
  - `__init__(self, conversation: ConversationManager)` 
  - `run()` — 主循环：打印欢迎信息 → 显示 `> ` 提示符 → 读取用户输入 → 处理特殊命令 → 调用对话管理 → 流式输出 → 循环
  - `_read_input() -> str` — 读取用户输入，支持多行（检测未闭合括号/引号，像 Python REPL 一样）
  - 支持 `/exit`、`/quit` 退出
  - 支持 `Ctrl+C`（中断当前操作但不退出）、`Ctrl+D`（退出）

`mewcode/main.py`：
- 实现 `main()` 函数：
  - 使用 `argparse` 解析命令行：`-c/--command` 单次调用
  - 加载配置 → 创建 provider → 创建对话管理器 → 进入 REPL 或单次调用
  - 单次调用模式：发送消息，流式输出，打印换行，退出
- `pyproject.toml` 中注册 `mewcode` 命令入口点：`mewcode = mewcode.main:main`

**多行输入检测**：
- 跟踪括号栈：`(` `[` `{` 需要匹配的闭合符号
- 行末以 `\` 结尾时续行
- 检测字符串引号未闭合（`"""` 或 `'''` 多行字符串）

**参考资料**：
- `argparse` 添加 `-c` 可选参数
- `pyproject.toml` 中 `[project.scripts]` 注册 CLI 入口
- Python `code` 模块 / `codeop` 模块的 `compile_command()` 可用于检测代码是否完整

**验收**：`mewcode` 启动 REPL，`mewcode -c "你好"` 单次调用后退出

---

### 任务 9：端到端验证

**影响文件**：无（手动测试）

**依赖**：任务 8

**内容**：
1. 准备配置文件 `~/.mewcode/config.yaml`，填入有效的 API key
2. 启动 REPL：`mewcode`
3. 输入对话，观察流式输出
4. 测试多行输入（粘贴代码块）
5. 测试 `/exit` 退出
6. 测试 `mewcode -c "一句话问题"` 单次调用
7. 测试 `Ctrl+C` 中断流式输出
8. 测试 `Ctrl+D` 退出
9. 测试配置文件不存在时的默认行为
10. 测试 API key 错误时的错误提示
11. 对照 `checklist.md` 逐项验收

**验收**：所有 checklist 项通过