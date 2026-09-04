# NewCode TUI 多轮对话 — 验收清单 (checklist.md)

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] 项目骨架完整：`pip install -e .` 成功安装，`newcode --help` 显示帮助信息（验证：运行命令）
- [ ] 配置数据类定义正确：`ProviderConfig`（含 Optional base_url、Literal protocol）和 `Config` 所有字段可正常实例化（验证：导入测试）
- [ ] 配置加载器正确解析：合法的 config.yaml 返回正确 Config 对象，`${ENV_VAR}` 正确解析（验证：用示例 YAML 测试）
- [ ] 配置加载器正确校验：缺少必填字段时抛出 `ConfigError`；`protocol` 非法时抛出 `ConfigError`（验证：用无效 YAML 测试）
- [ ] 提示词模块完整：`SYSTEM_PROMPT`、`DOG_BANNER`、`render_banner()` 均可正常调用（验证：导入并打印）
- [ ] 对话管理器正确维护：添加 user/assistant 消息后 `get_context()` 返回 system + 窗口内消息（验证：单元测试）
- [ ] 滑动窗口正确裁剪：超过 max_turns 后最早消息对被丢弃（验证：构造 10 轮对话，max_turns=3，验证只保留 3 轮）
- [ ] StreamEvent 和 Provider Protocol 定义正确：`new_provider()` 根据 protocol 返回正确实例（验证：导入测试）
- [ ] Anthropic Provider 流式正常：`stream()` 返回 `StreamEvent` 序列，thinking 增量不出现在 text 中（验证：真实 API 测试）
- [ ] OpenAI Provider 流式正常：`stream()` 返回 `StreamEvent` 序列（验证：真实 API 测试）
- [ ] Rich 渲染器流式输出 Markdown：模拟 `StreamEvent` 序列，终端看到逐步更新的格式化输出（验证：运行模拟测试）
- [ ] TUI REPL 状态机正常：IDLE 状态接受输入，STREAMING 状态锁定输入并可响应 ESC（验证：启动 newcode 交互）
- [ ] CLI 入口正确分发：`-c` 触发单次调用，无参数启动 TUI 并打印 banner（验证：两种模式分别运行）

## 集成

- [ ] 配置层 → Provider 层正确串联：`load()` 返回的 Config 传给 `new_provider()` 创建正确的 Provider 实例（验证：端到端启动）
- [ ] Provider 层 → TUI 层正确串联：REPL 消费 `StreamEvent` 序列，text 被渲染，done 被处理，err 被捕获（验证：启动 TUI 输入问题）
- [ ] REPL 流式消费接线正确（自动）：用 mock provider 产出 `StreamEvent.text` 序列，驱动 `REPL._consume_stream()`，用 `rich.console` 的 capture 断言输出包含渲染后的文本（验证：自动，mock 测试）
- [ ] ConversationManager → Provider 上下文正确传递：`get_context()` 返回的 system + 历史消息正确传入 `Provider.stream()`（验证：多轮对话，AI 能引用之前的内容）
- [ ] 所有模块导入无循环依赖（验证：`python -c "import newcode"` 成功）

## 编译与测试

- [ ] `pip install -e .` 安装无错误
- [ ] `python -m newcode --help` 正常运行
- [ ] 所有模块导入无错误

## 端到端场景

> 验证方式说明：`自动`=脚本/mock 可验证；`真实 API`=需有效 API key；`真实终端`=需真实 Windows 终端（cmd/PowerShell，prompt_toolkit 无法在 Git Bash 运行）。标注后，验收时不得跳过「真实终端」场景。

- [ ] **场景 1：TUI 启动与界面布局**（验证：真实终端）
  - 启动 `newcode`
  - 终端显示 ASCII 小狗横幅 + 应用名与版本号 + 当前工作目录
  - 底部输入框含 `❯` 提示符与占位文字
  - 底部状态栏显示活动 provider 名称和模型名

- [ ] **场景 2：多轮对话**（验证：真实终端 + 真实 API）
  - 启动 `newcode`
  - 输入 "你好，我叫小明"
  - 收到 AI 流式逐字回复
  - 输入 "我叫什么名字？"
  - AI 回复中包含 "小明"，证明记住了上下文
  - 输入 `/exit` 正常退出，终端恢复

- [ ] **场景 3：单次调用模式**（验证：真实 API）
  - 运行 `newcode -c "1+1 等于几"`
  - 收到 AI 回复
  - 程序自动退出，不进入 TUI

- [ ] **场景 4：配置文件缺失**（验证：自动，脚本可测）
  - 删除项目根目录的 `.newcode.yaml`
  - 运行 `newcode`
  - 看到提示信息 "配置文件不存在"
  - 程序退出

- [ ] **场景 5：多行输入**（验证：真实终端）
  - 启动 `newcode`
  - 按 `Alt+Enter` 插入换行，输入框内出现多行文本
  - 按 `Enter` 提交
  - 输入框清空，进入等待状态，收到 AI 回复

- [ ] **场景 6：流式输出与 Markdown 渲染**（验证：真实终端 + 真实 API）
  - 启动 `newcode`
  - 输入 "请用 Markdown 写一段包含代码块、加粗、列表的回复"
  - 观察回复是逐字实时打印的
  - 本轮结束后整段回复以 Markdown 形式重新渲染定型展示
  - 代码块有区分，加粗有视觉变化，列表有缩进

- [ ] **场景 7：滑动窗口**（验证：真实终端 + 真实 API）
  - 配置 `max_turns: 3`
  - 启动 `newcode`
  - 第一轮告诉 AI "我最喜欢的颜色是蓝色"
  - 连续进行 5 轮其他对话
  - 问 "我最喜欢的颜色是什么？"
  - AI 不再记得（已超出窗口），验证滑动窗口裁剪生效

- [ ] **场景 8：Extended Thinking**（验证：真实 API）
  - 配置 Anthropic provider 的 `thinking: true`
  - 启动 `newcode`
  - 输入一个复杂推理问题
  - 对话区中不出现思考内容，正文完整无混入

- [ ] **场景 9：优雅降级与错误反馈**（验证：真实终端 + 无效 key）
  - 配置一个无效的 API key
  - 启动 `newcode`
  - 输入问题
  - 看到以可区分样式（如红色）显示的错误信息
  - 对话历史保留，输入框恢复，可继续输入

- [ ] **场景 10：自动重试**（验证：真实终端 + 不稳定 endpoint）
  - 配置一个不稳定的 API endpoint
  - 启动 `newcode`
  - 输入问题
  - 观察界面显示重试次数和状态（如 "重试 1/3…"）
  - 3 次重试后仍失败则显示错误信息，恢复输入状态

- [ ] **场景 11：响应计时**（验证：真实终端 + 真实 API）
  - 启动 `newcode`
  - 输入一个需要较长回复的问题
  - 观察状态栏或对话区显示 "Imagining… (Ns)"，秒数随时间递增
  - 回复结束后定型显示该轮总耗时（如 "Done (12s)"）

- [ ] **场景 12：流式中断**（验证：真实终端 + 真实 API）
  - 启动 `newcode`
  - 输入一个需要较长回复的问题
  - AI 流式回复进行中时，按 `ESC` 键
  - 对话区显示取消提示
  - 恢复输入状态，对话历史保留，可继续输入

- [ ] **场景 13：System Prompt 定制**（验证：真实 API）
  - 配置 `system_prompt: "请用 JSON 格式回复"`
  - 启动 `newcode`
  - 输入任意问题
  - AI 回复为合法 JSON 格式

- [ ] **场景 14：四种退出方式**（验证：真实终端）
  - 分别验证 `/exit`、`/quit`、`Ctrl+C`、`Ctrl+D` 均能正常退出
  - 退出后终端状态恢复正常