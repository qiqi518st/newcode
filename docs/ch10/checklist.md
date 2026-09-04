# NewCode ch10 - SlashCommand 框架 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。
> 本版依据修订后的 spec / plan / task.md（含新增 T13 端到端冒烟、registry 防御拷贝、/help 对齐与两条 dispatch 断言）重新生成。

## 实现完整性

- [ ] 注册中心已实现且可被调用（验证：`pytest tests/test_ch10_registry.py` 通过）
- [ ] 注册阶段冲突检测：重复命令名/别名 → 抛 `RuntimeError` 且消息含具体冲突名字（验证：单测 AC15）
- [ ] 启动期冲突 → 进程立即终止启动并打印冲突名字（验证：`test_ch10_integration.py` 反向用例 + T13 实跑，AC15）
- [ ] 全部 24 条命令注册成功：/help /status /memory /memory_list /memory_add /memory_clear /permission /permission_rules /permission_add /permission_reset /session /session_list /session_resume /session_new /plan /normal /do /clear /compact /review /exit /quit /resume /delete-plan（验证：`/help` 输出含全部命令名 + 注册单测）
- [ ] `/help` 输出含全部已注册命令名（来自 registry.list() 单一信源，不硬编码）（验证：单测断言，AC1/N5）
- [ ] 解析器边界完整：空输入 / 仅空白 / 非 `/` 开头早返回；`/`、`/ xx` 退化输入不出现 `"未知命令: /"` 悬空斜杠文案；命令名大小写不敏感（验证：`test_ch10_parser.py` 表驱动全绿，F2/N10）
- [ ] registry.list() 返回排序后新拷贝，不暴露内部 dict 引用（验证：单测断言外部改动不影响内部）
- [ ] 隐藏命令双约束：不进 /help 与补全，但 dispatcher 仍命中（验证：单测 AC14/AC17）

## 架构与集成

- [ ] 命令文件只依赖 UIController，不 import prompt_toolkit / rich（验证：grep 校验 F6.3）
- [ ] 分流器：`/` 命令不触发 Agent.run（验证：集成测试 mock Agent 断言未调用，AC2）
- [ ] 普通输入仍走 AgentLoop（验证：集成测试 mock Agent 断言被调用）
- [ ] KindUI / KindPrompt 仅 idle 态执行，非 idle 提示"请等待当前任务完成"；KindLocal 任何状态可执行（验证：单测 N3a）
- [ ] `/exit` 先取消主 asyncio cancel scope 再退出；后台任务收 CancelledError（验证：集成测试 + 真实运行观察，N12）
- [ ] `/help` 与未命中提示的命令列表来自同一注册中心查询；ReadyHint 文案中仅允许出现 /help，无其它硬编码命令名（验证：grep 校验 N5）
- [ ] KindPrompt 注入消息与真实用户消息同持久化路径（验证：集成测试断言存档新增 user 角色消息，N3/F3.4）
- [ ] main.py 把 session_runtime / session_archive 传入 REPL（验证：`/session`、`/session_list` 可用，AC7/AC8，补 ch09 缺口）
- [ ] 注册中心并发安全（验证：并发 register/lookup/list/complete 无竞态单测，N9/F1.4）

## 编译与测试

- [ ] 项目编译无错误（验证：`python -c "import newcode"`）
- [ ] 全部单元测试通过（验证：`pytest tests/ -v` 全绿）
- [ ] 存量测试适配后通过：test_tui_resume / test_tui_compact / test_tui_wiring（验证：AC10/N8，迁移后仍绿）
- [ ] 新增两条 dispatch 断言通过：`/Help` 与 `/help` 行为一致（AC3）；`/help` 输出含全部已注册命令名（AC1）（验证：test_ch10_tui.py 对应用例）
- [ ] ruff check 通过（验证：`ruff check newcode tests`）
- [ ] docs/ 未被 ruff/测试/git 操作改动（验证：git status 检查 docs/）

## 端到端场景

> 真实终端 / tmux 实跑，逐项记录观测；本清单即 task.md T13 的逐条走查表。

- [ ] 场景 1：`/help` → 按字典序输出全部命令名 + 一句描述，两列对齐（验证：运行观察，AC1）
- [ ] 场景 2：`/status` → 六行 key:value（模式/token 入出/工具数/记忆数/模型/目录）顺序固定、对齐（验证：运行观察，AC4/N6）
- [ ] 场景 3：键入 `/` 补全立即激活；`/s` 过滤为 /session*、/status* 等 /s 开头候选；多匹配 Tab 弹列表、单匹配直接补全（验证：运行观察，AC11/AC12/AC13）
- [ ] 场景 4：`/clear` → 旧会话保存、AppMode 回 NORMAL、token 与回合数归零；随后 `/session_list` 见旧会话为可恢复条目（验证：运行观察，AC8）
- [ ] 场景 5：`/memory` → 列项目层与用户层记忆文件名；`/memory_add user_preference test` → `/memory_list` 可见 → `/memory_clear` 后该作用域清空（验证：运行观察，AC5/AC16）
- [ ] 场景 6：`/permission` → 输出当前权限模式名；`/session` → 输出当前会话存档路径与 session 标识（验证：运行观察，AC6/AC7）；`/permission mode acceptEdits` → 状态栏模式徽章变化（验证：运行观察，F11.1）
- [ ] 场景 7：`/review` → 状态栏进入流式、AI 开始回复、会话存档新增含审查关键字的 user 消息、不读 git diff（验证：运行观察 + 查存档，AC9）
- [ ] 场景 8：`/do` 无参 → 弹出计划列表；`/do <slug>` → 执行指定计划（验证：运行观察，AC10/F8.4）
- [ ] 场景 9：未知 `/foobar` → 引导指向 /help 且不触发任何 LLM 调用（验证：运行观察，AC2）
- [ ] 场景 10：`/resume` → 打开历史会话列表，选中后从最近 compact 标记后恢复；`/session_list` 列出可恢复会话、`/session_resume <id>` 恢复（验证：运行观察，AC8/AC10）
- [ ] 场景 11：`/Help`（大小写混合）→ 行为与 `/help` 一致（验证：运行观察，AC3）
- [ ] 场景 12：启动期冲突检测实跑——临时给某条已注册命令再注册同名，启动后打印冲突名字并立即退出（验证：T13 步骤 3，AC15）
- [ ] 场景 13：`/plan` 进入计划模式后状态栏含 [plan] 标记、`/normal` 退出；`/quit` 与 `/exit` 行为一致；`/delete-plan` 删除计划；`/permission_rules` / `/permission_add` / `/permission_reset` 规则读写生效（验证：运行观察，AC10/AC16/N8）

## 待人工验证

- [ ] 场景 A：Shift+Tab 与 `/permission mode` 双入口切换权限模式不冲突（原因：需真实终端交互确认；替代验证：单测覆盖 /permission 切换路径；补验：用户真实终端）
- [ ] 场景 B：补全菜单在真实 prompt_toolkit 渲染下的观感（原因：无真实终端；替代验证：test_ch10_tab_completion.py 断言候选逻辑；补验：用户真实终端）
- [ ] 场景 C：`/exit` 后台任务 CancelledError 真实行为（原因：需真实运行观察日志；替代验证：集成测试 mock cancel scope；补验：用户真实运行）
- [ ] 场景 D：tmux 不可用环境下，端到端场景无法实跑的项目逐条在此登记原因与替代验证，不得混入「通过」（原因：环境限制；替代验证：对应单测/集成测试；补验：用户具备真实终端时）