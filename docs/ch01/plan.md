# NewCode 多轮对话 — 实施计划 (plan.md)

## 目标

在 Python 中实现 NewCode 终端 AI 编程助手的第一版核心能力：**多轮对话**。

## 技术选型

| 层面 | 选择 | 理由 |
|------|------|------|
| 语言 | Python ≥3.10 | AI/LLM SDK 生态最成熟 |
| LLM SDK | `anthropic` + `openai` | 分别对接 Anthropic 协议和 OpenAI 协议 |
| 配置 | YAML (`pyyaml`) | 可读性好，支持复杂结构 |
| CLI | `argparse` | 标准库，够用 |
| 包管理 | `pyproject.toml` + pip | 现代 Python 打包标准 |

## 项目结构

```
newcode/
├── pyproject.toml
├── README.md
├── config.yaml.example
└── newcode/
    ├── __init__.py
    ├── main.py              # CLI 入口，argparse
    ├── config.py            # 配置加载，YAML 解析
    ├── conversation.py      # 对话管理，滑动窗口
    ├── repl.py              # REPL 交互循环
    ├── streaming.py         # 流式输出工具
    └── providers/
        ├── __init__.py      # 工厂函数 create_provider()
        ├── base.py          # 抽象基类 BaseProvider
        ├── anthropic_provider.py
        └── openai_provider.py
```

## 实现顺序

```
任务1: 脚手架 ──→ 任务2: 配置模块 ──→ 任务3: Provider 抽象
                                            ├── 任务4: Anthropic Provider
                                            └── 任务5: OpenAI Provider
                                                      ↓
                                              任务6: 对话管理
                                                      ↓
                                              任务7: 流式输出
                                                      ↓
                                         任务8: REPL + CLI 入口
                                                      ↓
                                         任务9: 端到端验证
```

## 核心流程图

```
                    ┌─────────────┐
                    │   main.py   │
                    │  (argparse) │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  config.py  │  读取 ~/.newcode/config.yaml
                    │  load_config│
                    └──────┬──────┘
                           │
              ┌────────────▼────────────┐
              │   providers/__init__.py │
              │   create_provider()     │
              └────┬──────────────┬─────┘
                   │              │
         ┌─────────▼──┐    ┌─────▼────────┐
         │ Anthropic   │    │ OpenAI        │
         │ Provider    │    │ Provider      │
         └──────┬──────┘    └──────┬────────┘
                │                  │
                └────────┬─────────┘
                         │
                ┌────────▼────────┐
                │ Conversation    │
                │ Manager         │
                │ - messages[]    │
                │ - max_turns     │
                │ - sliding window│
                └────────┬────────┘
                         │
                ┌────────▼────────┐
                │ streaming.py    │
                │ stream_response │
                └────────┬────────┘
                         │
                ┌────────▼────────┐
                │ repl.py         │
                │ REPL.run()      │
                │ - 多行输入检测   │
                │ - 特殊命令处理   │
                │ - Ctrl+C/D 处理  │
                └─────────────────┘
```

## 关键设计决策

### 1. Provider 抽象

`BaseProvider` 定义统一接口 `chat(messages, system_prompt) -> Iterator[str]`。两个子类各自封装不同 SDK 的调用细节，对上层（对话管理、REPL）完全透明。

### 2. 滑动窗口

在 `ConversationManager.send()` 每次追加 assistant 回复后，检查消息列表长度。如果超过 `max_turns * 2`（每轮 = 1 user + 1 assistant），丢弃最前面的一对消息。保持 system prompt 不在 messages 列表中（由 provider 层单独传入）。

### 3. 多行输入检测

使用栈追踪未闭合的括号 `()` `[]` `{}`，以及未闭合的引号 `"""` `'''` `"` `'`。同时检测行末 `\` 续行符。实现方式类似 Python REPL 的 `codeop.compile_command()`。

### 4. 错误处理策略

- Provider 层：捕获 SDK 异常，转换为 `ProviderError`（含原始错误信息）
- REPL 层：捕获 `ProviderError`，显示错误信息，保留对话历史，回到 `> ` 提示符
- 配置加载失败：立即报错退出（无法恢复）

### 5. 流式输出

所有 provider 的 `chat()` 方法返回 `Iterator[str]`（token 生成器）。`stream_response()` 函数统一处理：逐 token 打印 + 收集完整文本 + 返回。

## 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 火山引擎 API 兼容性 | Anthropic SDK 的 `base_url` 覆盖可能不完全兼容 | 提前用 curl 验证 API 端点，必要时用 `httpx` 直接请求 |
| 流式输出中断 | 网络断开导致用户体验差 | 捕获异常，显示错误信息，保留对话历史 |
| 多行输入边界情况 | 复杂的嵌套括号/引号可能误判 | 使用 Python 内置的 `codeop` 模块辅助判断 |
| 配置文件格式错误 | 用户写错 YAML 导致启动失败 | 给出明确的错误提示，指出具体行和问题 |