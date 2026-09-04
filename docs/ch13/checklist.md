# NewCode ch13 - 多 Agent 分发架构 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。逐项执行并记录证据（命令输出、观察到的行为）；受限项单独列「待人工验证」，不混入「通过」。
>
> 范围决策：本期**不做 ESC 手动切换后台**（B 决策，spec「不做的事」），相关场景跳过。

## 实现完整性（对应 spec AC）

### 工具与定义加载（F1/F2）
- [ ] AC1：`agent` 工具注册成功，且主 Agent 工具定义列表不随角色加载/调用而变化（验证：`registry.to_definitions()` 在加载角色前后数量与 schema 一致）
- [ ] AC2：`agent(subagent_type="explore")` 定义式前台执行，tool_result 为子 Agent 最后一条 assistant 文本（验证：integration mock 驱动真实代码路径）
- [ ] AC3：`subagent_type` 引用不存在角色 → 结构化错误「未知 subagent_type: X」，主 Agent 继续（验证：test_ch13_tools）
- [ ] AC4：项目级 `.newcode/agents/explore.md` 覆盖内置 explore；用户级与项目级同名 → 项目级生效；未覆盖名字正常可用（验证：test_ch13_catalog）
- [ ] AC5：用户/项目级 frontmatter 非法（未知 model/mode、缺 description）→ stderr 定位并降级缺省/跳过，不阻断启动；内置级失败直接 raise（验证：test_ch13_parser / test_ch13_catalog capsys）
- [ ] AC6：`verifier` 默认 resolve 不到；`agents.enable_verifier: true` 后可用（验证：test_ch13_catalog）

### 两种创建模式（F3）
- [ ] AC7：Fork 路径子 Agent 首条 user 消息以 `<fork_boilerplate>` 起头，且消息列表前缀与父对话逐字节一致（验证：test_ch13_fork 断言）
- [ ] AC8：Fork 调用不传 `run_in_background` 也立即返回 `{task_id, status:"async_launched"}`（强制后台）（验证：test_ch13_launcher / integration）
- [ ] AC9：`run_to_completion` 模型不再调工具 → 返回累积文本；触达 maxTurns → 抛 `MaxTurnsReached`（带文本/usage/tool_count）（验证：test_ch13_agent）

### 权限（F5）
- [ ] AC10：角色 `permissionMode: dontAsk` → bash 等 Ask 类工具自动放行，无审批；未设 dontAsk 且 default → write_file ASK→DENY，结果含拒绝原因（验证：test_ch13_agent / test_ch13_permission）
- [ ] AC11：子 Agent 运行中**永不**弹主 TUI 审批框（无 HITL 升级、无 approval_upgrader）——mock 断言无 HITL_REQUEST 流向主对话（验证：test_ch13_permission）
- [ ] AC12：子 Agent 工具调用链 = pre_tool_use Hook → 执行 → post_tool_use Hook；pre_tool_use 拦截 hook 生效（结果含 `[hook <name>]` 拒绝原因）（验证：test_ch13_hooks / test_ch13_agent）

### 工具过滤多层防线（F6）
- [ ] AC13：定义式子 Agent 工具集不含 `agent`（验证：test_ch13_filter / test_ch13_launcher 断言子 Registry 可见名）
- [ ] AC14：后台工作者（含全部 Fork）工具集与 ASYNC_AGENT_ALLOWED_TOOLS 交集后不含 `agent`；角色白名单写 `agent` 也不可见（验证：test_ch13_filter）
- [ ] AC15：Fork 子 Agent 对话历史含 `<fork_boilerplate>` 时调 agent 工具 → `is_error=True`「Fork 子 Agent 不能再启动 Agent」（模拟工具列表残留 agent 的场景）（验证：test_ch13_launcher / test_ch13_tools）
- [ ] AC16：角色 `disallowedTools: [execute_command]` → 子 Agent 工具集不含 execute_command；`tools: [read_file, list_files]` → 仅白名单 + 系统工具（验证：test_ch13_filter）

### 后台任务与移交（F7/F8）
- [ ] AC17：前台子 Agent 跑超 `async_timeout_s` → 自动转后台，tool_result 含「已转后台 + task_id」，主对话恢复，任务随后正常 completed；**移交未杀实例**（同一 run 继续，mock 计数未归零，asyncio.wait 非 wait_for）（验证：test_ch13_launcher / integration）
- [ ] AC18：任务记录含 `agent-<hex>` id、状态机 running→completed/failed/cancelled、起止时间、token、tool_count、round（验证：test_ch13_manager）
- [ ] AC19：完成通知 = user 角色 `<task-notification>` XML（task-id/status/summary/result）写入主对话历史 + 界面打印；`<result>` 超 800 字截断；主 Agent 流式中完成 → 排队不打断，空闲后注入（验证：test_ch13_tui mock）
- [ ] AC20：`completed` 后 15 分钟无续派 → 自动清理；保留超 `max_idle_agents` → 关最旧；任务达 `max_tasks_per_agent` → 续派拒绝（验证：test_ch13_manager）
- [ ] AC21：续派给运行中任务 → 入队（≤`max_queue_per_agent`）；空闲任务立即执行（验证：test_ch13_manager）
- [ ] AC22：`SendMessage({name,message})` 与 `/tasks send <id|name> <message>` 走同一条 `continue_agent`——**同 task_id 复用**（round+1、result 覆盖），跑完注入同 id 通知；目标不存在 / 达上限 → 结构化错误（验证：test_ch13_manager / test_ch13_tools / integration）
- [ ] AC23：`/clear`、`/resume`、`/session_new`、进程退出 → 运行中任务取消、保留子 Agent 与排队清空（验证：test_ch13_manager clear_all）
- [ ] AC24：`TaskList` 返回任务摘要（id/name/status/tool_count/round）；`TaskGet` 返回含 result 完整状态；`TaskStop` 触发取消（状态变 cancelled）（验证：test_ch13_tools）

### hook 动作与 Skill 统一（F9/F10）
- [ ] AC25：hook `agent` 动作接通——`agent_name=explore` 触发定义式后台子 Agent，完成通知注入主对话；`agent_name` 无效 → stderr 失败日志、主流程不受影响（验证：test_ch13_hooks）
- [ ] AC26：Skill fork 走 SubAgent 底座——`skills/executor.py` `_execute_fork` 只装饰参数后调 `launcher.launch_fork`，行为不变（结果/token 写回主对话）（验证：test_ch13_skills + 存量 ch11 测试全绿）

### 配置与总闸（F11）
- [ ] AC27：`enable_subagent_background: false` → `run_in_background:true` / 超时自动切全部失效强制前台；Fork 调用返回「后台禁用，无法 Fork」（验证：test_ch13_launcher / test_ch13_tools）

### 隔离/共享与错误隔离（F4/N）
- [ ] AC28：子 Agent 事件 hook payload 含 `agent_id`（`agent-<hex>`），主 Agent 事件不含；按 `agent_id` 为空的 hook 条件不匹配子 Agent 事件（验证：test_ch13_agent / test_ch13_hooks）
- [ ] AC29：子 Agent 流式错误 / maxTurns 达上限 → 任务 failed 带原因，主 Agent 与 TUI 不崩（验证：test_ch13_manager failed 路径 / integration）
- [ ] AC30：未加载任何角色 / 无后台任务时，主 Agent 行为与 ch12 一致（验证：全量存量测试通过）

## 集成（plan 层验证点）

- [ ] `for_subagent()` 共享规则层——父对话 `persist_local_allow` 过的精确规则，子 Agent 同样命中，不重复问（验证：test_ch13_permission；integration——父批准 `git status` 后子 Agent 直接放行）
- [ ] main.py 装配完整：agents_cfg → catalog → task_manager → launcher → tools（agent + Task 组）→ hook launcher → main Agent → REPL/tasks，退出 finally `clear_all()`（验证：`python -c "from newcode.main import main"` 无错 + T21 冒烟）
- [ ] `/tasks` 命令注册进 `register_all`，与 Task 工具组共用同一 `TaskManager` 底层（验证：test_ch13_tools + 冒烟 `/tasks`）
- [ ] 循环依赖规避：`subagent` 包 import 方向无环（`subagent.launcher → agent` 单向，agent 不反向 import subagent；`tools.agent_tool → subagent`）（验证：启动 import 无循环导入错误）
- [ ] 子 Agent 构造一次性过滤（F6.6）——前台→后台移交后工具集不变（验证：test_ch13_launcher 断言移交前后子 Registry 一致）

## 编译与测试

- [ ] `export PYTHONIOENCODING=utf-8 && ruff check newcode tests` 全绿（验证：T26）
- [ ] `ruff format newcode tests` 无差异（验证：T26；注意**禁止** `ruff format .`，防扫到 docs/）
- [ ] 全量 `python -m pytest tests/ -q` 全绿（含全部存量 ch01-ch12 测试）（验证：T26）
- [ ] `newcode --version` = 0.13.0，且 `newcode/__init__.py` 与 `pyproject.toml` 两处一致（验证：T1/T26）
- [ ] 文档保护：跑完批量命令后 `git status` 确认 docs/ 未被改动（验证：T26）

## 端到端场景

- [ ] 场景 1（定义式前台快速任务）：主 Agent 调 `agent(subagent_type="explore")` 查一个函数定义 → 子 Agent 前台执行并同步返回结果文本作为工具结果，主 Agent 据此继续回复（验证：integration mock 驱动真实代码路径）
- [ ] 场景 2（前台超时自动转后台）：主 Agent 调 explore 调研整个代码库 → 超过 `async_timeout_s` 自动转后台（不杀重来）→ 主 Agent 继续 → 完成后 `<task-notification>` 注入主对话，后续轮可引用（验证：integration mock 用延时假 provider；真实 API 行为待人工验证）
- [ ] 场景 3（ESC 手动移交）：**本期不做**（B 决策，对齐参考）——前台→后台仅由超时自动触发，ESC 手动切换留待后续章节（spec 场景 3）
- [ ] 场景 4（Fork 模式）：主 Agent 调 `agent(prompt)`（不带 subagent_type）→ 子 Agent 继承父历史（前缀逐字节一致）+ 强制后台 → 完成后结构化通知注入（验证：integration mock）
- [ ] 场景 5（续派闭环）：主 Agent 用 `name:"worker-1"` spawn 后台子 Agent → 完成后 `SendMessage(name:"worker-1", message:"接着做...")` 续派（同 task_id、round 递增）→ 新一轮完成注入同 id 通知 → 累计 10 个任务后续派被拒（验证：integration mock）
- [ ] 场景 6（hook 触发子 Agent）：配置 session_start hook 的 agent 动作 → 启动后 explore 子 Agent 后台运行，完成通知注入主对话（验证：test_ch13_hooks + integration）
- [ ] 场景 7（嵌套防护）：任一子 Agent 运行中，其工具定义均不含 `agent`；Fork 子 Agent 即使残留 agent 工具也被标记检查拦截（验证：integration）

## 待人工验证

- [ ] Fork 首次请求命中 prompt cache（`cache_read_input_tokens > 0`）——原因：需真实 API key / 网络；替代验证：test_ch13_fork 断言消息前缀逐字节一致（缓存命中的前提），真实命中由用户本地跑真实 API 补验
- [ ] `/tasks` 命令族在真实终端的显示效果（列表/详情渲染、`/tasks send` 交互）——原因：无真实终端；替代验证：test_ch13_tui / test_ch13_tools mock 断言底层逻辑，由用户本地终端补验
- [ ] 前台超时自动转后台的真实耗时行为（120s 阈值下观察主对话恢复与通知注入时序）——原因：需真实 LLM 慢响应；替代验证：integration mock 用延时假 provider 覆盖逻辑，由用户本地补验
