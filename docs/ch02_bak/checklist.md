# MewCode TUI 多轮对话 — 验收清单 (checklist.md)

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] 项目骨架完整：`pip install -e .` 成功安装，`mewcode --help` 显示帮助信息（验证：运行命令）
- [ ] 配置数据类定义正确：`Config` 和 `ProviderConfig` 所有字段可正常实例化（验证：导入测试）
- [ ] 配置加载器正确解析 YAML：合法的 config.yaml 返回正确 Config 对象（验证：用示例 YAML 测试）
- [ ] 配置加载器正确校验：缺少必填字段时抛出 `ConfigError`（验证：用无效 YAML 测试）
- [ ] `${ENV_VAR}` 占位符正确解析：配置文件中的 `${API_KEY}` 从环境变量读取（验证：设置环境变量后加载）
- [ ] 对话管理器消息列表正确维护：添加 user/assistant 消息后 get_context() 返回完整上下文（验证：单元测试）
- [ ] 滑动窗口正确裁剪：超过 max_turns 后最早消息对被丢弃（验证：构造 10 轮对话，max_turns=3，验证只保留 3 轮）
- [ ] Anthropic Provider 流式调用正常：调用 stream() 方法能收到逐 token 输出（验证：用真实 API key 测试）
- [ ] OpenAI Provider 流式调用正常：调用 stream() 方法能收到逐 token 输出（验证：用真实 API key 测试）
- [ ] Provider 工厂函数正确分发：protocol="anthropic" 返回 AnthropicProvider，protocol="openai" 返回 OpenAIProvider（验证：导入测试）
- [ ] Rich 渲染器流式输出 Markdown：模拟 token 流，终端看到逐步更新的格式化输出（验证：运行模拟测试）
- [ ] TUI REPL 循环正常运行：启动后显示 `> ` 提示符，输入后收到回复（验证：启动 mewcode 交互）
- [ ] CLI 入口正确分发：`-c` 参数触发单次调用，无参数启动 TUI（验证：两种模式分别运行）

## 集成

- [ ] 配置层 → Provider 层正确串联：ConfigLoader 加载的 ProviderConfig 传给 create_provider() 创建正确的 Provider 实例（验证：端到端启动）
- [ ] Provider 层 → TUI 层正确串联：REPL 调用 Provider.stream() 获取的 token 流经 RichRenderer 渲染输出（验证：启动 TUI 输入问题）
- [ ] ConversationManager → Provider 上下文正确传递：get_context() 返回的 system + 历史消息正确传入 Provider.stream()（验证：多轮对话，AI 能引用之前的内容）
- [ ] 所有公开接口至少被一个真实调用方使用（验证：编译 + 全部测试通过）

## 编译与测试

- [ ] `pip install -e .` 安装无错误
- [ ] `python -m mewcode --help` 正常运行
- [ ] 所有模块导入无错误：`python -c "import mewcode"` 成功

## 端到端场景

- [ ] **场景 1：TUI 多轮对话**
  - 启动 `mewcode`
  - 输入 "你好，我叫小明"
  - 收到 AI 流式回复
  - 输入 "我叫什么名字？"
  - AI 回复中包含 "小明"，证明记住了上下文
  - 输入 `/exit` 正常退出

- [ ] **场景 2：单次调用模式**
  - 运行 `mewcode -c "1+1 等于几"`
  - 收到 AI 回复
  - 程序自动退出，不进入 TUI

- [ ] **场景 3：配置文件缺失**
  - 删除 `~/.mewcode/config.yaml`
  - 运行 `mewcode`
  - 看到提示信息 "配置文件不存在"
  - 程序退出

- [ ] **场景 4：多行输入**
  - 启动 `mewcode`
  - 输入 `print("hello` 按回车
  - 自动换行，等待继续输入
  - 输入 `")` 按回车
  - 括号闭合，发送请求，收到 AI 回复

- [ ] **场景 5：滑动窗口**
  - 配置 `max_turns: 3`
  - 启动 `mewcode`
  - 第一轮告诉 AI "我最喜欢的颜色是蓝色"
  - 连续进行 5 轮其他对话
  - 问 "我最喜欢的颜色是什么？"
  - AI 不再记得（因为已超出窗口），验证滑动窗口裁剪生效

- [ ] **场景 6：流式输出**
  - 启动 `mewcode`
  - 输入一个需要较长回复的问题
  - 观察回复是逐字/token 实时打印的，不是等待后一次性出现

- [ ] **场景 7：Markdown 渲染**
  - 启动 `mewcode`
  - 输入 "请用 Markdown 写一段包含代码块、加粗、列表的回复"
  - 观察代码块有边框/背景色区分，加粗文字有视觉变化，列表有缩进

- [ ] **场景 8：Extended Thinking**
  - 配置 Anthropic provider 的 `thinking: true`
  - 启动 `mewcode`
  - 输入一个复杂推理问题
  - 观察回复中包含 thinking 内容

- [ ] **场景 9：优雅降级**
  - 配置一个无效的 API key
  - 启动 `mewcode`
  - 输入问题
  - 看到错误提示信息
  - 对话历史保留，可以继续输入（而不是程序崩溃）

- [ ] **场景 10：System Prompt 定制**
  - 配置 `system_prompt: "请用 JSON 格式回复"`
  - 启动 `mewcode`
  - 输入任意问题
  - AI 回复是合法 JSON 格式