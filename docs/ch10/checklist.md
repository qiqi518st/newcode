# MewCode ch10 - SlashCommand 框架 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] 注册中心已实现且可被调用（验证：`pytest tests/test_ch10_registry.py` 通过）
- [ ] 注册阶段冲突检测：重复命令名/别名 → 抛错且消息含冲突名（验证：单测 AC15）
- [ ] 启动期冲突 → 进程立即终止启动并打印冲突名（验证：`test_ch10_integration.py` 反向用例）
- [ ] 12+ 条命令全部注册（验证：`/help` 输出含全部命令，来自 registry.list() 单一信源）
- [ ] /help 按字典序输出"命令名 + 一句描述"两列对齐（验证：单测 AC1）
- [ ] /status 输出六项 key:value 且顺序固定（验证：单测 AC4）
- [ ] /memory_clear 清空该作用域全部记忆（验证：单测 AC16）
- [ ] /review 触发 Agent 且携带含审查关键字的 user 消息、不读 git diff（验证：单测 AC9）

## 架构与集成

- [ ] 命令文件不 import prompt_toolkit / rich，只依赖 UIController（验证：grep 校验）
- [ ] 分流器：`/` 命令不触发 Agent.run（验证：集成测试 mock Agent 断言未调用，AC2）
- [ ] 普通输入仍走 AgentLoop（验证：集成测试 mock Agent 断言被调用）
- [ ] KindUI / KindPrompt 仅 idle 态执行，非 idle 提示"请等待当前任务完成"（验证：单测 N3a）
- [ ] KindLocal 任何状态可执行（验证：单测 N3a）
- [ ] hidden 命令不出现在 /help 与补全，但 dispatcher 仍命中（验证：单测 AC14/F10）
- [ ] /exit 先取消主 asyncio cancel scope 再退出（验证：集成测试断言后台任务收 CancelledError，N12）
- [ ] /help 与未命中提示的命令列表来自同一注册中心查询，无硬编码命令名（验证：grep 校验 N5）
- [ ] main.py 把 session_runtime / session_archive 传入 REPL（验证：`/session`、`/session_list` 可用，AC7/AC8）

## 编译与测试

- [ ] 项目编译无错误（验证：`python -c "import mewcode"`）
- [ ] 全部单元测试通过（验证：`pytest tests/ -v` 全绿）
- [ ] 存量测试适配后通过：test_tui_resume / test_tui_compact / test_tui_wiring（验证：AC10/N8）
- [ ] ruff check 通过（验证：`ruff check mewcode tests`）
- [ ] docs/ 未被 ruff/测试/git 操作改动（验证：git status 检查 docs/）

## 端到端场景

- [ ] 场景 1：`/help` → 显示全部命令及用法（验证：运行 mewcode 观察）
- [ ] 场景 2：`/status` → 显示模式/token/工具/记忆/模型/目录（验证：运行观察）
- [ ] 场景 3：`/memor<Tab>` → 补全 `/memory`；`/s<Tab>` → 弹列表（验证：运行观察 F9）
- [ ] 场景 4：`/clear` → 旧会话保存、AppMode 回 NORMAL、token 归零；随后 `/session_list` 见旧会话（验证：AC8）
- [ ] 场景 5：`/memory_add user_preference test` → `/memory_list` 可见 → `/memory_clear` 后清空（验证：AC16）
- [ ] 场景 6：`/permission mode acceptEdits` → 状态栏模式标记变化（验证：运行观察）
- [ ] 场景 7：`/review` → 状态栏进入流式、AI 回复、存档新增含审查关键字的 user 消息（验证：AC9）
- [ ] 场景 8：`/do` 无参 → 弹出计划列表；`/do <slug>` → 执行指定计划（验证：AC10）
- [ ] 场景 9：未知 `/foobar` → 引导 /help 且不触发 LLM（验证：AC2）
- [ ] 场景 10：`/resume` → 打开历史会话列表恢复（验证：AC10）

## 待人工验证

- [ ] 场景 11：Shift+Tab 与 `/permission mode` 双入口切换权限模式不冲突（原因：需真实终端交互确认；替代验证：单测覆盖 /permission mode 切换路径；补验：用户真实终端）
- [ ] 场景 12：补全菜单在真实 prompt_toolkit 渲染下的观感（原因：无真实终端；替代验证：test_ch10_tab_completion.py 断言候选逻辑；补验：用户真实终端）
- [ ] 场景 13：`/exit` 后台任务 CancelledError 真实行为（原因：需真实运行观察日志；替代验证：集成测试 mock cancel scope；补验：用户真实运行）
