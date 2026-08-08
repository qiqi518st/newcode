# MewCode 工具系统 — 验收检查 (checklist.md)

> 基于已批准的 spec.md + plan.md + task.md。每一项通过运行代码或观察行为来验证，聚焦系统行为。

---

## 实现完整性

- [ ] **Tool 协议与六个工具已实现**（验证：`python -m pytest tests/test_tools.py -v`，全部通过）
- [ ] **Registry 预装六个工具并可导出 API 格式**（验证：`python -c "from mewcode.tools import Registry; r=Registry.default(); print(len(r.to_definitions()))"` 输出 `6`）
- [ ] **Agent 单轮闭环编排已实现**（验证：`python -m pytest tests/test_agent.py -v`，纯文本和工具调用两条路径均通过）
- [ ] **Anthropic Provider 支持 tool_use 流式解析与回灌**（验证：`python -m pytest tests/test_provider_tools.py -v`，Anthropic 分片解析用例通过）
- [ ] **OpenAI Provider 支持 function_call 流式解析与回灌**（验证：`python -m pytest tests/test_provider_tools.py -v`，OpenAI 分片解析用例通过）
- [ ] **ConversationManager 支持 tool 角色消息**（验证：`python -m pytest tests/test_conversation_tools.py -v`）
- [ ] **TUI 消费 AgentEvent 流并渲染工具行**（验证：启动 TUI 输入问题，观察到工具行以 `●` 前缀展示）
- [ ] **单次调用模式支持工具闭环**（验证：`mewcode -c "读取 README.md"` 完整输出工具行和最终回复后退出）

---

## 功能行为检查

- [ ] **read_file 正常读取**（验证：TUI 中请求"读取 main.py"，最终回复包含 main.py 内容）
- [ ] **read_file 行范围切片**（验证：请求"读取 main.py 前 5 行"，回复仅含前 5 行）
- [ ] **read_file 大文件截断**（验证：读取超过 500 行的文件，结果尾部含"...（已截断）"提示，`truncated=True`）
- [ ] **read_file 文件不存在**（验证：请求"读取 not_exist.txt"，TUI 展示错误工具行，结构化错误回灌模型，模型给出"文件不存在"的解释）
- [ ] **write_file 创建文件**（验证：请求"创建 hello.py 写入 print('hello')"，文件被创建，TUI 展示 `● Write` 工具行）
- [ ] **write_file 自动创建目录**（验证：请求"写入 foo/bar.txt"，foo/ 目录不存在时自动创建）
- [ ] **edit_file 唯一匹配成功**（验证：请求替换文件中恰好出现一次的字符串，替换成功，TUI 展示成功摘要）
- [ ] **edit_file 未找到报错**（验证：请求替换不存在的字符串，TUI 以红色展示"old_string 在文件中未找到"，结构化错误回灌，模型可重试）
- [ ] **edit_file 多处匹配报错**（验证：请求替换在文件中出现多次的字符串，TUI 展示"找到 N 处，无法确定替换哪一处"，结构化错误回灌）
- [ ] **execute_command 白名单允许**（验证：请求"运行 python --version"，TUI 展示 `● Bash(cmd="python --version")`，结果含版本号）
- [ ] **execute_command 白名单拒绝**（验证：请求"运行 rm -rf /"，TUI 展示拒绝信息"命令 'rm' 不在白名单"，结构化错误回灌）
- [ ] **execute_command 超时处理**（验证：请求"运行 sleep 100"，约 60 秒后展示超时提示，返回已收集输出（如有）+ 超时标记，不残留 sleep 进程）
- [ ] **execute_command exit_code 非 0**（验证：请求"运行 python -c 'exit(1)'"，TUI 展示错误样式摘要，结构化结果含 exit_code=1 回灌模型）
- [ ] **list_files 正常列出**（验证：请求"列出所有 py 文件"，返回项目内 `.py` 文件路径列表）
- [ ] **list_files 空结果**（验证：请求"列出所有 .xyz 文件"，返回空列表，不报错）
- [ ] **search_code 正常搜索**（验证：请求"搜索项目里所有 TODO"，返回含 TODO 的文件路径、行号、片段）
- [ ] **search_code 空结果**（验证：搜索不存在的模式，返回空列表，不报错）
- [ ] **路径遍历防护**（验证：请求"读取 ../../etc/passwd"，所有文件工具均返回"路径超出项目范围"错误）

---

## 集成

- [ ] **Agent 正确调用 Registry 执行工具**（验证：`test_agent.py` 中 mock provider 返回 tool_call，断言 Registry.execute 被调用且参数正确）
- [ ] **工具结果正确回灌 Conversation**（验证：`test_agent.py` 断言 conversation 消息序列为 user → assistant(tool_call) → tool → assistant(text)）
- [ ] **Provider 正确将工具定义注入 API 请求**（验证：mock SDK 调用，断言 `tools` 参数非空且含 6 个工具定义）
- [ ] **Anthropic 与 OpenAI 回灌格式不同但行为一致**（验证：分别用两个 provider 运行同一问题，TUI 展示、对话历史、最终回复均一致）
- [ ] **TUI 和单次调用共用 Agent 逻辑**（验证：同一问题在 TUI 和 `mewcode -c` 下均走通闭环，无代码重复）
- [ ] **新增工具无需改 Provider 或 TUI**（验证：新建一个 mock 工具实现 Tool 协议并注册到 Registry，TUI 和 Provider 无修改即可识别和调用）

---

## 编译与测试

- [ ] 项目编译无错误（验证：`python -m compileall mewcode/`）
- [ ] 所有单元测试通过（验证：`python -m pytest tests/ -v`，全部 green）
- [ ] lint 检查通过（验证：`ruff check mewcode/` 或 `flake8 mewcode/`）

---

## 端到端场景

- [ ] **场景 1：文件读写改完整流程**
  - 用户操作：TUI 中依次输入：
    1. "创建一个 test_ch03.py 写入 x = 1"
    2. "把 x = 1 改成 y = 2"
    3. "读取 test_ch03.py"
  - 可观测结果：
    1. 第 1 轮展示 `● Write(path="test_ch03.py")`，文件被创建
    2. 第 2 轮展示 `● Edit`，文件内容被修改
    3. 第 3 轮展示 `● Read`，最终回复含 `y = 2`
    4. 每轮工具行与最终答复均纳入 scrollback，可滚动回看

- [ ] **场景 2：命令执行 + 搜索组合**
  - 用户操作：TUI 中输入"运行 git status 然后列出所有 py 文件"
  - 可观测结果：
    1. 模型调用 `execute_command`，TUI 展示 `● Bash(cmd="git status")` 及结果摘要
    2. 最终回复包含 git 状态摘要和 py 文件列表
    3. 程序不崩溃，会话继续，用户可继续输入

- [ ] **场景 3：工具失败后的自愈**
  - 用户操作：TUI 中输入"把不存在的字符串 AAA 改成 BBB"
  - 可观测结果：
    1. 模型调用 `edit_file`，TUI 以红色展示"old_string 在文件中未找到"
    2. 结构化错误回灌模型
    3. 模型在最终回复中解释"找不到 AAA，请确认要替换的内容"
    4. 程序不退出，用户可继续输入修正后的请求

- [ ] **场景 4：单次调用模式走通工具**
  - 用户操作：终端运行 `mewcode -c "读取 README.md 前 3 行"`
  - 可观测结果：终端输出工具行 `● Read(path="README.md")` 和结果摘要，随后输出最终回复（含 README.md 前 3 行内容），程序退出

- [ ] **场景 5：跨协议一致体验**
  - 用户操作：分别用 Anthropic provider 和 OpenAI provider 运行同一问题"搜索 TODO"
  - 可观测结果：两种配置下 TUI 工具行样式、结果展示格式、错误反馈样式、对话历史结构完全一致

---

## 自检

- [x] **spec 对齐**：spec.md 的每条 AC 都有对应 checklist 条目
- [x] **可观测性**：每一项都是"做 X，看到 Y"或"运行 X，期望 Y"
- [x] **耦合测试**：重构文件路径或函数名后，checklist 依然适用（聚焦行为而非实现细节）
- [x] **端到端**：5 个场景覆盖完整用户可见流程

---

**checklist.md 已生成。请 review：**
- 是否完整覆盖了 spec 的 15 条验收标准？
- 每项是否都可以运行/观察验证？
- 端到端场景是否合理？有没有遗漏的典型使用场景？
- 集成检查项是否覆盖了 plan.md 的关键交互点？

**确认后四份文档全部通过审批，按 task.md 开始开发。**
