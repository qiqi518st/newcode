# MewCode TUI 多轮对话 — 需求规格 (spec.md)

## 背景

MewCode 是一个终端 AI 编程助手（类似 Claude Code），使用 Python 实现。ch02 是其 TUI 版本，提供 Claude Code 风格的终端交互体验：用户可以在终端中与 AI 进行持续多轮对话，AI 流式回复并渲染 Markdown。

第一版聚焦纯对话体验，不做工具调用、文件操作等 Agent 能力。

## 目标

- 用户在终端获得接近 Claude Code 的对话体验
- 支持 Anthropic 和 OpenAI 两种 API 后端，通过 YAML 配置切换
- Provider 层抽象为统一接口，方便后续扩展新后端
- 流式输出使用 SSE 协议，支持 Claude extended thinking

## 功能需求

- F1: **TUI 多轮对话** — 启动后进入 Claude Code 风格 REPL，输入输出交织，流式逐字打印，`> ` 提示符
- F2: **单次调用模式** — `mewcode -c "问题"` 直接输出回复后退出，不进入 TUI
- F3: **流式输出 (SSE)** — AI 回复通过 SSE 协议逐 token 实时打印，不等完整生成
- F4: **滑动窗口上下文** — 保留最近 N 轮对话（默认 20），超出自动丢弃最早消息对
- F5: **多行输入检测** — 自动检测未闭合括号/引号，允许换行继续输入
- F6: **多种退出方式** — `/exit`、`/quit`、`Ctrl+C`、`Ctrl+D` 四种方式均可退出
- F7: **System Prompt 可定制** — 默认内置 system prompt，用户可通过配置文件覆盖
- F8: **YAML 配置管理** — 配置文件 `~/.mewcode/config.yaml`，不存在则提示创建并退出
- F9: **双 Provider 支持** — 支持 Anthropic 协议和 OpenAI 协议，通过配置切换
- F10: **Provider 抽象接口** — 统一的 LLM 调用接口，封装不同 API 协议差异，方便扩展新 provider
- F11: **Extended Thinking 支持** — 支持 Claude extended thinking，通过配置 `thinking: true` 启用
- F12: **Markdown 渲染** — AI 回复中的 Markdown 通过 Rich 库渲染后显示
- F13: **优雅降级** — API 调用失败时显示错误信息，保留对话历史，用户可重试

## 非功能需求

- N1: **Python 3.10+** — 依赖 `prompt_toolkit`（TUI）、`rich`（Markdown 渲染）、`anthropic`（Anthropic SDK）、`openai`（OpenAI SDK）、`pyyaml`（配置解析）
- N2: **配置文件路径** — 位于 `~/.mewcode/config.yaml`，不存在时提示用户创建并提供模板
- N3: **API Key 安全** — 支持 `${ENV_VAR}` 语法从环境变量读取，也可直接填写
- N4: **流式输出到 stdout** — 正常输出到 stdout，错误信息到 stderr
- N5: **使用官方 SDK** — 通过 `anthropic` 和 `openai` 官方 SDK 调用 API，不裸写 HTTP

## 不做的事

- 工具调用（Tool Use / Function Calling）
- 文件读写 / Shell 命令执行
- 对话历史持久化（退出即丢失）
- MCP / 插件系统
- 多会话管理
- 上下文智能压缩（仅简单滑动窗口）
- TUI 内代码高亮 / 语法着色
- 自动补全 / 历史命令搜索
- 会话导出 / 导入

## 验收标准

- AC1: 启动 `mewcode`，看到 `> ` 提示符，输入问题后收到流式逐字回复
- AC2: `mewcode -c "你好"` 输出回复后立即退出，不进入 TUI
- AC3: AI 回复逐 token 实时出现，用户在生成过程中即可看到内容
- AC4: 对话超过配置的 `max_turns` 轮后，AI 不再引用最早的消息
- AC5: 输入 `print("hello` 后按回车，自动换行等待补全，输入 `")` 关闭括号后按回车才发送
- AC6: `/exit`、`/quit`、`Ctrl+C`、`Ctrl+D` 四种方式均能正常退出
- AC7: 配置文件中设置 `system_prompt: "请用 JSON 格式回复"` 后，AI 回复为合法 JSON
- AC8: `~/.mewcode/config.yaml` 不存在时，提示用户创建配置文件并退出
- AC9: 修改 `provider` 字段从 `anthropic` 协议切到 `openai` 协议后，调用不同 API 后端
- AC10: 新增一个 provider 类型只需实现统一接口，无需修改核心逻辑
- AC11: Anthropic provider 配置 `thinking: true` 时，回复包含 thinking 内容
- AC12: 代码块、加粗、列表等 Markdown 语法在终端中正确渲染
- AC13: 断网时调用 API，显示错误信息，对话历史保留，用户可继续输入重试