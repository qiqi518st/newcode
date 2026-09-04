# NewCode 多轮对话 — 验收清单 (checklist.md)

> 每一项必须可勾选、可观测。完成后在 `[ ]` 中填 `x`。

---

## 一、项目搭建

- [ ] `pip install -e .` 执行成功，`import newcode` 无报错
- [ ] `newcode --help` 输出帮助信息，包含 `-c` 参数说明

## 二、配置加载

- [ ] 不创建 `~/.newcode/config.yaml` 时，启动 `newcode` 不报错，使用默认配置
- [ ] 创建 `~/.newcode/config.yaml` 写入 `max_turns: 10`，启动后 `max_turns` 生效为 10
- [ ] 配置文件中 `api_key: "${MY_API_KEY}"` 引用环境变量，export 对应变量后 API key 正确解析
- [ ] 配置文件中 `api_key` 直接填写明文 key 时，正确使用该 key
- [ ] 配置 `provider: openai` 切换到 OpenAI provider，请求走 OpenAI API

## 三、REPL 交互

- [ ] 启动 `newcode` 后显示欢迎信息和 `> ` 提示符
- [ ] 输入一行文本按回车，AI 开始流式回复
- [ ] 输入 `/exit` 或 `/quit`，REPL 正常退出，返回码为 0
- [ ] 按 `Ctrl+D`（EOF），REPL 正常退出
- [ ] 按 `Ctrl+C` 在等待回复时，中断当前回复但不退出 REPL，回到 `> ` 提示符
- [ ] 按 `Ctrl+C` 在空闲输入时，退出 REPL

## 四、多行输入

- [ ] 输入 `def foo():` 后按回车，自动进入续行模式，提示符变为 `... `（或类似续行提示）
- [ ] 在续行模式下输入 `    return 42` 后按回车，再按一次空行回车，输入结束
- [ ] 输入未闭合的 `(` 或 `[` 或 `{` 后按回车，自动续行
- [ ] 输入 `"""` 或 `'''` 后按回车，自动续行直到闭合
- [ ] 输入 `\` 结尾的行，自动续行

## 五、单次调用模式

- [ ] `newcode -c "1+1等于几"` 输出 AI 回复并退出，返回码为 0
- [ ] 单次调用模式不进入 REPL，输出完毕即退出
- [ ] 单次调用模式也支持流式输出

## 六、滑动窗口

- [ ] 设置 `max_turns: 2`，连续对话 5 轮，检查只保留最近 2 轮上下文
- [ ] 验证：第 1 轮说"我叫张三"，第 3 轮问"我叫什么？"，AI 不应记得第 1 轮的内容

## 七、流式输出

- [ ] AI 回复内容逐 token 出现在终端，不是一次性弹出
- [ ] 流式输出过程中 `Ctrl+C` 中断，已输出的部分保留在终端

## 八、System Prompt

- [ ] 不配置 system_prompt 时，AI 行为体现默认编程助手角色
- [ ] 配置 `system_prompt: "用中文回答，每句话不超过10个字"` 后，AI 回复风格符合该约束
- [ ] 配置 `system_prompt: /path/to/prompt.md`（文件路径），AI 使用文件内容作为 system prompt

## 九、错误处理

- [ ] 使用无效 API key 时，REPL 显示错误信息（如 "401 Unauthorized"），不退出，回到 `> ` 提示符
- [ ] API 返回 500 错误时，REPL 显示错误信息，不退出，保留对话历史
- [ ] 网络不可达时，REPL 显示连接错误，不退出
- [ ] 配置文件中 `protocol` 填写不存在的值（如 `"unknown"`），启动时报错并退出

## 十、端到端验收

- [ ] 从零启动：`pip install -e .` → 配置 API key → `newcode` → 输入"你好，介绍一下你自己" → 看到流式回复 → `/exit` 退出
- [ ] 完整对话流：启动 → 输入问题 A → 收到回复 → 输入问题 B（引用问题 A 的上下文）→ 收到回复 → 退出
- [ ] Provider 切换：修改配置 `provider: openai` → 启动 → 输入问题 → 确认走 OpenAI API → 退出
- [ ] 多行输入：启动 → 粘贴多行代码块 → 正常输入 → 收到回复 → 退出
- [ ] `grep -r "import" newcode/` 返回预期文件列表（main.py, config.py, conversation.py, repl.py, providers/*.py 等）
- [ ] `newcode --help` 输出包含 `-c, --command` 和 `--help` 两个选项
- [ ] 对话超过 max_turns 后，`len(conversation.messages) <= max_turns * 2` 成立

## 配置文件默认值

以下默认值需在验收时确认：

| 配置项 | 默认值 |
|--------|--------|
| `provider` | `anthropic-official` |
| `max_turns` | `20` |
| `system_prompt` | 内置默认编程助手 prompt |
| providers[0].name | `anthropic-official` |
| providers[0].protocol | `anthropic` |
| providers[0].base_url | `https://ark.cn-beijing.volces.com/api/coding` |
| providers[0].model | `deepseek-v4-pro-260425` |
| providers[0].thinking | `true` |
| providers[1].name | `openai` |
| providers[1].protocol | `openai` |
| providers[1].base_url | `https://api.openai.com/v1` |
| providers[1].model | `gpt-4o` |

## 错误信息文本

以下错误信息文本需在验收时确认：

| 场景 | 预期输出包含 |
|------|-------------|
| 配置文件解析失败 | `配置文件解析错误:` |
| Provider 不支持 | `不支持的 provider 协议:` |
| API key 无效 | `API 认证失败` |
| 网络错误 | `网络连接失败` |
| API 服务端错误 | `API 服务错误` |
| 未知 Provider | `未知的 provider:` |
| 配置文件不存在 | 静默使用默认配置，不报错 |