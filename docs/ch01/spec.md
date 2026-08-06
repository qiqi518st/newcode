# MewCode 多轮对话 — 需求规格 (spec.md)

## 背景

MewCode 是一个终端 AI 编程助手（类似 Claude Code），使用 Python 实现。本规格定义其第一版核心能力：**多轮对话**。

用户可以启动 MewCode，在终端中与 AI 进行持续的多轮对话，AI 能记住上下文，流式输出回复。第一版不涉及工具调用、文件操作等高级能力，专注于对话体验本身。

## 目标用户

- 使用终端的开发者
- 希望通过命令行与 LLM 快速交互，而不离开终端环境

## 能力清单

### 对话能力

1. **REPL 多轮对话**：启动 `mewcode` 后进入交互式 REPL，用户可连续输入，AI 逐轮流式回复，直到用户退出
2. **单次调用模式**：`mewcode -c "问题"` 直接输出 AI 回复后退出，不进入 REPL
3. **滑动窗口上下文管理**：保留最近 N 轮对话历史（N 可配置，默认 20），超过上限时丢弃最早的消息对
4. **流式输出**：AI 回复逐 token 实时打印到终端，用户无需等待完整回复
5. **多行输入**：自动检测未闭合的括号/引号，像 Python REPL 一样允许换行继续输入

### 多 Provider 支持

6. **通过配置文件切换 LLM 提供商**：支持 Anthropic 协议和 OpenAI 协议，通过 YAML 配置文件指定使用哪个 provider
7. **Provider 可配置项**：每个 provider 可配置 name、protocol、base_url、api_key、model、thinking 等参数
8. **API Key 双来源**：优先从环境变量读取，配置文件中也可直接填写

### 交互体验

9. **用户提示符**：`> ` 作为输入提示符
10. **退出方式**：`/exit`、`/quit`、`Ctrl+C`、`Ctrl+D` 四种方式均可退出 REPL
11. **System Prompt 可定制**：默认内置 system prompt，用户可通过配置文件覆盖
12. **优雅降级**：API 调用失败时显示错误信息，保留对话历史，用户可重试当前轮，不丢失上下文

## 非功能要求

- 使用 Python 实现，依赖 `anthropic` SDK 和 `openai` SDK
- 配置文件为 YAML 格式，位于 `~/.mewcode/config.yaml`
- 配置文件不存在时使用内置默认值正常启动
- 流式输出到 stdout，错误信息输出到 stderr

## 设计骨架

### 命令行接口

```
mewcode                    # 启动 REPL 多轮对话
mewcode -c "问题"          # 单次调用，输出后退出
mewcode --help             # 显示帮助
```

### 配置文件结构 (~/.mewcode/config.yaml)

```yaml
provider: anthropic-official
max_turns: 20
system_prompt: ""           # 留空用默认

providers:
  - name: anthropic-official
    protocol: anthropic
    base_url: https://ark.cn-beijing.volces.com/api/coding
    api_key: "${ANTHROPIC_API_KEY}"
    model: deepseek-v4-pro-260425
    thinking: true

  - name: openai
    protocol: openai
    base_url: https://api.openai.com/v1
    api_key: "${OPENAI_API_KEY}"
    model: gpt-4o
```

### 架构分层

- **CLI 层**：解析命令行参数，决定进入 REPL 还是单次调用模式
- **配置层**：加载 YAML 配置，合并默认值，解析 provider 配置
- **Provider 层**：抽象 LLM 调用接口，封装 Anthropic SDK 和 OpenAI SDK 的差异
- **对话管理**：维护消息列表（system + user/assistant 交替），实现滑动窗口
- **REPL 层**：终端交互循环，读取用户输入，调用对话管理，流式输出回复

### 数据流

```
用户输入 → REPL 读取（处理多行） → 对话管理（追加 user 消息）
  → Provider 调用（流式） → 逐 token 打印 → 对话管理（追加 assistant 消息）
  → 滑动窗口裁剪 → 等待下一次输入
```

## Out of Scope（第一版不做）

- ❌ 工具调用（Tool Use / Function Calling）
- ❌ 对话历史持久化（退出即丢失）
- ❌ Markdown 渲染 / 代码高亮 / 语法着色
- ❌ 多会话管理
- ❌ 上下文智能压缩（仅做简单的滑动窗口）
- ❌ MCP / 插件系统
- ❌ 文件读写 / Shell 命令执行
- ❌ 会话导出 / 导入
- ❌ 自动补全
- ❌ 历史命令搜索