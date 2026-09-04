# NewCode ch15 - AgentTeam 与 Coordinator Mode Spec

## 背景

ch13 SubAgent 把任务从单 Agent 委派给子 Agent，实现了消息、权限账本、文件读缓存与 token 计数的隔离；ch14 Worktree 给每个子 Agent 配上独立工作目录，文件系统层并发也安全。但这两章合起来仍是**星型**拓扑——所有子 Agent 只能与主 Agent 通信，子 Agent 之间没有横向通道；主 Agent 既要决策、又要中转，**既是大脑也是邮局**。对「同时重构四个模块」「三个角度查同一个 bug」这类持续性、需要互相交流的工作，星型结构的瓶颈很明显。

本章把 newcode 从星型升级到**网状**：

- 主 Agent 创建 **Team** 后升任 **Lead**——Team 是长期存在的小组对象，记名称、负责人、成员花名册、持久化位置；
- 每个**队员**（Teammate）是独立 Agent 实例，有独立 Conversation 与独立 Worktree；
- 三种执行后端 `tmux` / `iterm2` / `in-process` 覆盖不同环境，按优先级一次性检测，启动后不静默回退；
- 队员之间经**共享任务列表**与**邮箱**直接通信，不必经 Lead 中转；协作工具仅在团队上下文出现；
- 队员可暂停可续写——自然停下后 session 留盘，Lead 调 `SendMessage` 会从磁盘恢复后继续指派；
- Lead 可选启用 **Coordinator Mode**（独立于 Team，但典型场景一起用），双锁机制下剥夺 `write_file`/`edit_file`，只保留调度、读类操作与 shell（用于 git merge）；
- 收敛阶段由 Lead 用 Bash 跑 `git merge` 逐个合各队员的 worktree 分支，冲突由 LLM 推理解决，搞不定就 `git merge --abort` 保留 worktree 上报用户。

现有相关基础设施（已核实）：
- ch13 `task.Manager` 已支持后台任务管理 + `send_message` 续派 + `by_name` 名称映射（name → id）；本章扩展为多 Team 寻址
- ch13 `AgentTool.execute` 已是子 Agent 启动入口，本章新增 `team_name` 参数走 Team spawn 分支
- ch13 工具过滤 `apply_agent_tool_filter` 已支持多层防线（`GLOBAL_DENY` 禁 `agent`、后台白名单、系统工具豁免）；本章新增团队专属白名单（协作工具）与 Coordinator Mode 白名单
- ch14 `worktree.Manager` 已支持嵌套 slug（`team/alice` → `.newcode/worktrees/team+alice/`），本章复用做队员 worktree（slug 形式 `team-<team_name>/<member>`）
- ch12 session 持久化（`.newcode/sessions/<id>/conversation.jsonl`）按对话粒度落盘；本章给每个队员单独申请一个 session，队员 stop 不删 session，SendMessage 续派时经 session 反序列化 Conversation
- ch10 slash 命令系统，本章新增 `/team` 系列
- ch07 permission 已支持 `plan` 模式，本章 `plan_mode_required` 的 Plan 提交-Lead 审批工作流套用同一引擎

环境约束（已实测）：开发环境为 **WSL2**（Linux），`tmux 3.4` 已安装可实测；**iTerm2 是 macOS 专属**，本环境完全无法验证 → iterm2 后端本期只做**探测/配置/报错骨架**，实装标「待人工验证」。`~/.newcode/teams/` 目录已存在（空）。

本章**只做**「Lead 多人协作 + Plan 审批 + Coordinator 收敛」。跨进程跨机器分布式团队、队员之间实时流式通信、复杂任务依赖约束（优先级 / deadline）、Windows 平台 iterm2 适配均不在范围内。

## 目标

- G1：**`team.Team` 与 `team.Manager`**——Team 封装小组生命周期（name、lead_agent_id、members、config_path）；Manager 在单 newcode 进程内管理多个 Team（典型场景同时只有一个活跃 Team）
- G2：**`TeamCreate` 工具**——主 Agent 调用即创建 Team、调 `detect_backend` 确定后端、写 `~/.newcode/teams/<sanitized_name>/config.json`、把 Lead 注册成第一个成员；同名团队自动后缀 `-2`/`-3` 避免冲突
- G3：**扩展 `Agent` 工具**——增加 `team_name` 可选参数，非空时走 Team spawn 分支：加载定义 → 创建队员 Worktree → 注入协作工具 → 按后端分流 spawn → 注册到名称注册表 → 写入 `team.members`
- G4：**`TeamDelete` 工具**——确认所有成员空闲后删队员 worktree + 删 team 目录、Lead 退出团队；有活跃成员时拒绝删除
- G5：**三种执行后端 `tmux`/`iterm2`/`in-process`**，统一抽象 `team.Backend` Protocol；`detect_backend` 按 `$TMUX → $TERM_PROGRAM==iTerm.app && which it2 → which tmux → in-process` 优先级一次性决定，不做运行时回退
- G6：**队员注入 5 个协作工具**（TaskCreate/TaskGet/TaskList/TaskUpdate/SendMessage）；未建团队的主入口与普通子 Agent 看不到这些工具
- G7：**`SendMessage` 寻址**支持 `to="<name>"`、`to="<agent_id>"`、`to="*"` 广播三种；经名称注册表解析，写邮箱；tmux/iterm2 后端额外经 `send-keys` 唤醒目标 pane
- G8：**邮箱文件并发安全**——收件人独占 lock 文件抢锁、随机抖动重试、stale 判定、read-modify-write + 原子替换
- G9：**三种结构化消息**——纯文本（必带 summary）、`shutdown_request`/`shutdown_response`（优雅退出协商）、`plan_approval_response`（Plan 审批回复，仅 Lead 可发）；同一 SendMessage 入口以 `type` 字段分流
- G10：**队员未读消息注入**——下一轮 Agent Loop 开头读出，以 `<incoming-messages>` system reminder 注入 LLM 输入，读后批量标记 read
- G11：**队员 spawn 两条路径**——指定 `subagent_type` 走定义式（空白对话起步）、留空走 Fork 路径（继承 Lead 对话历史，受 `FORK_TEAMMATE` feature flag 控制，默认关闭）
- G12：**队员完成通知 Lead**——团队 config 标记 `is_active=False`、Lead 邮箱收到 `idle_notification`；队员 Conversation 已经 ch12 Writer 实时写入 session 文件
- G13：**队员续写**——Lead 调 `SendMessage` 时系统检测队员已停，从 session 反序列化 Conversation、新建 asyncio task 续派，Conv 沿用历史，不重头 spawn
- G14：**`plan_mode_required` 审批工作流**——队员以 plan 模式起步，生成计划后发给 Lead，Lead 用 `plan_approval_response` approve/reject；approve 时权限切到 Lead 当前模式继续执行
- G15：**Coordinator Mode**——独立于 Team；`is_coordinator_mode() = feature(COORDINATOR_MODE) && env_truthy(NEWCODE_COORDINATOR_MODE)` 双锁全开才生效；工具集收窄 + 四阶段工作流提示词 + 「派完停手」纪律
- G16：**收敛全由 LLM 推理驱动**——Lead 用 Bash 逐个 `git merge` 队员 worktree 分支，冲突自行解决，搞不定 `--abort` 保留 worktree 上报
- G17：**TUI slash 命令** `/team list` / `/team info <name>` / `/team delete <name>` / `/team kill <member>`，辅助人工介入
- G18：**与 ch04~ch14 协同**——主 Agent 平时（未 TeamCreate）工具列表不变；协作工具仅团队上下文出现；ch13 后台任务 / 超时移交 / SendMessage 续派路径保留，队员续派复用同一套底层 `task.Manager`

## 功能需求

### F1 Team 数据结构与 Manager

- F1.1 `Team` 字段：`name`（原始名）、`sanitized_name`（经 sanitize 用于路径）、`lead_agent_id`（本期固定 `"lead"`）、`members: list[TeammateInfo]`、`config_dir`（`<home_dir>/.newcode/teams/<sanitized_name>/`）、`config_path`、`created_at`、`backend`。
- F1.2 `TeammateInfo` 字段：`name`（Lead 分配的队员名，Team 内唯一）、`agent_id`、`agent_type`（使用的 subagent 定义名；Fork 路径下为空）、`model`（覆盖，空表 inherit）、`worktree_path`（绝对路径）、`branch`（对应 worktree 分支名）、`backend_type`（可 per-member 不同）、`pane_id`（tmux/iterm2 pane id，in-process 为空）、`is_active: bool | None`（None 或 True 表活跃，False 表空闲；终止后从 members 移除）、`plan_mode_required`、`session_dir`（队员独立 session 目录绝对路径）。
- F1.3 `Manager` 构造：校验 `<home_dir>/.newcode/teams/` 可写；扫描该目录还原 `teams` dict（每个子目录读一次 config.json，解析失败的跳过并 stderr 警告）；`Manager._lock` 仅保护 `teams` dict。
- F1.4 `Manager.create(name, agent_type)`：
  1. `sanitize(name)`——只保留 `[a-zA-Z0-9._-]`，其余替换为 `-`，首尾去 `-`，空字符串拒绝；
  2. 同名冲突时在 sanitized 后追加 `-2`/`-3` 直到唯一；
  3. 创建 config_dir，落 config.json（原子写）；
  4. 调 `detect_backend()` 写入 `team.backend`；
  5. 取当前 Lead Agent ID（本期固定 `"lead"`）；
  6. 把 Lead 注册成第一个成员（`TeammateInfo(name="lead", agent_id="lead", is_active=None)`）；
  7. 加入 `teams` dict，返回 Team。
- F1.5 `Manager.delete(name, force)`：
  1. 取 Team；不存在抛 `TeamNotFoundError`；
  2. 非 force 时若有成员 `is_active != False`（含 None 和 True）抛 `TeamHasActiveMembersError`；
  3. 逐个删队员 Worktree（失败只警告不中断）；
  4. 删队员 session 目录；
  5. 删 config_dir；
  6. 从 `teams` dict 移除。
- F1.6 `Team.add_member` / `set_member_active` / `remove_member`：name 在 Team 内唯一校验；更新后持久化 config.json（原子写）。
- F1.7 **跨进程写并发**：Pane 后端下 Lead 与子进程是不同进程、各持一份内存 Team 对象。`add_member` 与 `set_member_active` 在加锁后**先从磁盘 reload `members` 字段**再修改 + 原子 save（`_reload_from_disk_locked`），否则会出现「子进程内存看不到自己、set_member_active 静默 no-op」的丢更新。

### F2 后端检测与抽象

- F2.1 `BackendType`：`tmux` / `iterm2` / `in-process`（字符串枚举）。
- F2.2 `Backend` Protocol：`type()` / `spawn(req)`（返回 pane_id、agent_id）/ `wake(pane_id, agent_id)`（消息到达时唤醒目标 pane；in-process 为 no-op）/ `kill(pane_id, agent_id)`。
- F2.3 `SpawnRequest` 字段：`team_name`、`member_name`、`agent_id`、`worktree_path`、`session_dir`、`agent_type`、`model`、`initial_prompt`、`plan_mode_required`、`sub_agent`/`conv`/`task_mgr`（in-process 用）。
- F2.4 `detect_backend()` 按以下优先级**一次性决定**，启动后不运行时回退：
  1. `$TMUX` 已设 → `tmux`（已在 tmux 会话内）；
  2. `$TERM_PROGRAM == "iTerm.app"` 且 `it2` 可执行 → `iterm2`；
  3. `tmux` 二进制在 PATH → `tmux`（外部 spawn 新 session）；
  4. 否则 → `in-process`。
- F2.5 当前在 tmux 会话外但本机有 tmux：spawn 走 `tmux new-session -d`（detached 新会话）；**失败回落到错误而非 in-process**（不静默回退）。
- F2.6 **Pane 后端 `initial_prompt` 不走命令行**——在 `Backend.spawn` 前由 spawn 流程预写入队员 mailbox（`type=text`，`from=lead`），子进程启动后读 mailbox 自然拿到，避免长 prompt 在命令行 shell-quote 的边界问题。
- F2.7 队员**强制 worktree 隔离**（并行安全 + 收敛合并基础）；队员写操作只落自己 worktree，主工作树对应文件不变。

### F3 tmux 后端

- F3.1 `spawn`：`tmux split-window`（横向分屏，`-P` 打印 pane id，`-F` 指定格式）；命令为 `python -m newcode --team-member --team <team_name> --member <member_name> --agent-id <agent_id> --session-dir <session_dir> --worktree <worktree_path> [--agent-type <type>] [--model <model>] [--plan-mode]`。用 `asyncio.create_subprocess_exec` 跑 tmux，捕获 stdout 作 pane_id。
- F3.2 `--agent-id` 预生成是关键：Lead spawn 时已生成的 agent_id 直接传给子进程，子进程无需读 Lead 尚未写完的 config.json 找自己。
- F3.3 `wake`：`tmux send-keys -t <pane_id> "" Enter`——回车触发子进程 stdin reader 读到一行，立即去 mailbox 轮询。
- F3.4 `kill`：`tmux kill-pane -t <pane_id>`（忽略 pane 不存在错误）。

### F4 iterm2 后端

- F4.1 本期仅**骨架**：探测（F2.4）、配置、结构化报错；`spawn`/`wake`/`kill` 接口约定为 `it2 split --new-pane --command "<cmd>"`（含 `--agent-id`）、`it2 send-text --pane <pane_id> ""`、`it2 close-pane --pane <pane_id>`；实装标「待人工验证」（本环境 WSL 无法验证 macOS 专属）。

### F5 in-process 后端

- F5.1 `spawn`：复用 `task.Manager.launch`——创建带 `cwd=worktree_path` 的子 Agent，在 asyncio task 里跑 `run_to_completion`；返回 `(pane_id="", agent_id=<task_id>)`，内部用 `BackgroundTask.id` 关联。
- F5.2 `wake`：no-op（同进程，下一轮 Loop 自动读邮箱）。
- F5.3 `kill`：调 `task.Manager.stop(agent_id)`。
- F5.4 **in-process 队员只允许同步子 Agent**——其 `Agent` 工具看不到 `team_name` 参数（被拦截）；后台子 Agent 禁用（过滤 `run_in_background=True`）。

### F6 Pane 子进程 team-member 模式

- F6.1 `python -m newcode --team-member` 子进程**不启动 TUI**，跑自治协程：
  1. 解析 `--team / --member / --agent-id / --session-dir / --worktree / --agent-type / --model / --plan-mode`；
  2. `os.chdir(--worktree)`，让该进程的 `Path.cwd()` 与权限沙箱根都指到 worktree；
  3. 构造单独的 Manager、provider、registry、permission engine、hook engine（完整复用 Lead wire 代码，但不构造 TUI）；
  4. 构造队员 Agent，设 `dont_ask=True`、注入 `<team-context>` reminder、注入 TeammateContext（含 mailbox client）；
  5. 启动 stdin reader asyncio task：任何来自 tmux send-keys 的回车都推到 `wake_event`（`asyncio.Event`），触发立即去 mailbox 轮询（0~2s 内响应）；
  6. 进入主循环：读未读邮箱 → 空则 `await asyncio.wait_for(wake_event.wait(), timeout=2.0)` 兜底轮询；有未读时按消息类型处理（`text` 拼成 task、`plan_approval_response(approve=True)` 切换权限 + 续派、`shutdown_request` 触发优雅退出）；`run_to_completion` 跑到底；完成后写 `summary="<name> idle"` 到 Lead mailbox 并 `set_member_active(name, False)`；检测到 mailbox 目录已删除（Lead 调 `/team delete`）→ 优雅退出。
- F6.2 **pane UX 是只读日志流**：`Text` 直接 print、`ToolEvent` 打 `● tool(args)`、`Done` 打分隔横线、错误打 stderr；不接受用户输入（任何回车都被 stdin reader 消费做 Wake 信号）。
- F6.3 **队员一律 `dont_ask=True`，覆盖角色定义的 `permission_mode`**。理由：队员没有可交互 TUI 接 ApprovalRequest（in-process 走 `task.Manager` 聚合事件不响应、Pane 子进程更没有 TUI），Ask 工具会无人应答地永远阻塞。队员安全边界由 allowed 工具集 + Worktree 隔离 + Plan 模式控制，不靠逐次 ask 弹窗。

### F7 协作工具（团队上下文）

- F7.1 **可见性**：协作工具仅团队上下文出现——队员工具池（spawn 时注入）与 Lead 团队态可见；**未建团队的主入口与普通子 Agent 不可见**（不注册进全局默认集合）。
- F7.2 **名称策略**：`task_list`/`task_get`/`send_message` 复用 ch13 同名，团队上下文内指向团队实现（基于共享任务列表与 Mailbox）；`task_create`/`task_update` 本期新增；`task_stop` 沿用 ch13（in-process 队员终止用）。
- F7.3 `TaskCreate`：`title` 必填，`description`/`assignee`（队员名）/`blocked_by`（可选 list[task_id]）可选；返回新建 `task_id`（`task_<6位hex>`）；写入团队 `tasks.json`（原子）。
- F7.4 `TaskGet`：按 `task_id` 返回任务详情。
- F7.5 `TaskList`：可选 `status` 过滤（`pending`/`in_progress`/`completed`/`blocked`）；返回任务数组，带依赖关系标注（`blocked_by`、`blocks`、`is_ready`=无未完成 blocker）。
- F7.6 `TaskUpdate`：`task_id` 必填，`title`/`description`/`status`/`assignee`/`add_blocks`/`add_blocked_by`/`remove_blocks`/`remove_blocked_by` 可选；更新后持久化，**双向维护依赖关系**。
- F7.7 `SendMessage`：`to`（name / agent_id / `"*"` 广播）必填；`summary`（纯文本消息必填，5-10 词）；`message`（可选，纯文本消息体）；`type`（可选，默认 `"text"`：`text`/`shutdown_request`/`shutdown_response`/`plan_approval_response`）；`payload`（可选，结构化消息载荷，如 `{approve, feedback}`）。
- F7.8 协作工具经 `apply_agent_tool_filter` 在 spawn 时注入队员工具池；主 Agent 工具集与 schema 不变（N1/N2）。

### F8 Mailbox 邮箱系统

- F8.1 **两段式寻址**：先经名称注册表（name → agent_id）解析，再定位该 agent_id 的邮箱文件。
- F8.2 存储：`<team_config_dir>/mailbox/<agent_id>.json`；消息字段 `from`/`to`/`type`/`summary`/`content`/`payload`/`timestamp`/`read`（默认 false）。
- F8.3 `Box` 接口：`write(agent_id, msg)` / `read(agent_id)` / `mark_read(agent_id, indices)`。
- F8.4 **锁文件并发安全**：写前抢 `<agent_id>.lock`（`os.open(O_CREAT|O_EXCL)`）；抢锁失败按 5-100ms 随机抖动重试，最多 10 次；持锁超过 10 秒视为 stale（`Path.stat().st_mtime` 判定）直接清掉重试；成功后 read-modify-write，`os.replace` 原子替换。
- F8.5 **广播**：`to="*"` 时对 Team 内**除发件人外**所有活跃成员 mailbox 各 write 一次。
- F8.6 **结构化消息规则**：`plan_approval_response` 仅 Lead 可发（否则抛错）；`shutdown_response` 只能发给 Lead（否则抛错）；队员收到 `shutdown_request` 时自主决定 approve/reject（LLM 决策，不强制）。
- F8.7 **Pane 后端唤醒**：写邮箱后取目标 `backend_type` 与 `pane_id`，Pane 后端调 `backend.wake(pane_id, agent_id)`。

### F9 名称注册表

- F9.1 字段：`by_name: dict[str, str]`（name → agent_id）、`by_id: dict[str, str]`（agent_id → name，反查），受锁保护。
- F9.2 接口：`register(name, agent_id)`、`unregister(name)`、`resolve(name_or_id)`、`name_of(agent_id)`。
- F9.3 注册时机：`Agent` 工具 spawn 队员时；`AgentTool` 的 `name` 参数非空时（ch13 已有）；统一这套 registry，替换 `task.Manager.by_name` 的内部 dict。
- F9.4 命名冲突：后注册的覆盖先注册的（弱引用语义，后启动覆盖前）。

### F10 队员 spawn 流程（Agent 工具 team_name 分支）

- F10.1 校验：`team_name` 对应 Team 存在（否则抛错）；调用者权限——主 Agent/Lead 允许；in-process 队员调 Team spawn 拒绝（`InProcessTeammateNoSpawnError`）；Pane 队员可调（拥有完整 Agent 工具）但 `team_name` 参数被屏蔽（队员不能往 Team 加人）。
- F10.2 加载定义：指定 `subagent_type` 走 Catalog；留空且 `FORK_TEAMMATE` feature flag 开启走 Fork 定义（继承 Lead 对话历史）；留空且 flag 关闭用 `general-purpose`。
- F10.3 创建队员 Worktree（`wt_mgr.create(f"team-{sanitized}/{member_name}", "HEAD", False)`，复用 ch14）。
- F10.4 申请新 session 目录（复用 session 包接口）作 `session_dir`。
- F10.5 构造子 Agent（in-process）或仅构造 SpawnRequest（Pane 后端）；把协作工具注入到子 Agent 的 allowed tools 集合；注入队员系统提示词附录（F10.9）与 `<team-context>` initial system reminder。
- F10.6 **Pane 后端**：`backend.spawn` 前把 `initial_prompt` 作为 `text` 消息（`from=lead, summary=initial task`）预写入队员 mailbox（F2.6）；**in-process 后端**不需要，`initial_prompt` 直接作为 `task.Manager.launch` 的 task 参数。
- F10.7 `backend.spawn(req)` 记 `pane_id` → 注册名称注册表（`member_name → agent_id`）→ 构造 `TeammateInfo` 加入 `team.members` 持久化（F1.7 reload-before-modify 兜底）。
- F10.8 返回 JSON：`{member_name, agent_id, worktree, backend, pane_id}`。
- F10.9 **队员系统提示词附录**（spawn 进 Team 时追加）：告知队员「仅文本回复队友不可见，必须用 SendMessage；用户主要与 Lead 交互；工作经任务系统与队友消息协调」（具体文案 plan.md 定稿）。
- F10.10 **`<team-context>` initial reminder**（注入子 Conv 首条 system reminder）：含 team、成员名、agent_id、worktree 目录、当前团队成员列表（文案结构 plan.md 定稿）。

### F11 邮箱读取与消息注入

- F11.1 **队员侧**：子 Agent 每轮请求 LLM **之前**调 `mailbox.read(agent_id)`；有未读 → 构造 `<incoming-messages>` system reminder 追加到本轮 system_reminders → 调 `mark_read`。
- F11.2 `<incoming-messages>` 格式：`收到 N 条新消息`，逐条 `[i] 来自 <from>(type=<type>,ts=<时间>): <summary>` + `<content 前 200 字>`（文案 plan 定稿）。
- F11.3 **Lead 侧**：Lead 无 TeammateContext，由 TUI 在 `on_mount` 启动后台 asyncio task `consume_lead_mail`——每秒遍历所有 Team 的 `mailbox/lead.json` 读未读消息、标 read；渲染成 `<team-update>` reminder（截断上限 8000 字符，允许队员完整报告透传）推入 `pending_reminders`；同时往 `lead_mail_event`（`asyncio.Event`）set 信号。Lead 下一轮 Run 迭代头部取出——**Lead 即便在长 Run 中也能中途惊醒**（下一个 LLM 调用前看到队员更新）。这是 Pane 队员通知 Lead 的关键路径（in-process 队员另有 `task-notification` 路径）。
- F11.4 **Lead idle 自动续推**：TUI 阻塞在 `lead_mail_event` 上，收到信号后：
  - `app.state == IDLE` → `begin_autonomous_turn`：合成一条 user 消息 `"[team-update] 队员发来新消息，请按 Coordinator 流程处理…"` 加入对话历史（用户在 RichLog scrollback 可见，清楚是系统通知触发而非自己输入），然后启 Run；
  - 非 idle（STREAMING/APPROVING）→ reminder 已在 pending，当前 Run 下一轮迭代头部自然取出，不需主动 wake；
  - 末尾 `event.clear()` 让后续信号接住。
  - 这避免了「队员全 idle、Lead 在 idle 等用户输入、reminder 静默积累没人取」的协作卡死。

### F12 队员空闲与续写

- F12.1 队员 `run_to_completion` 自然结束：`set_member_active(member_name, False)`；给 Lead 邮箱写 `idle_notification`（`type=text, summary="<member> idle"`）。
- F12.2 **续写（in-process）**：`SendMessage` 检测目标已 stop（`BackgroundTask.status` 非 Running）→ 从 `session_dir` 反序列化 Conversation → 复用 ch13 `task.Manager` 续派接口（重置 Running、起新 asyncio task 跑 `run_to_completion(new_message)`、同 id、Conv 沿用历史）→ 续派前 `set_member_active(member_name, True)`。
- F12.3 **续写（Pane）**：写邮箱 + `backend.wake`；pane 内实例下一轮 Loop 自然读到消息；pane 已死（`tmux list-panes` 查不到 pane_id）→ 报错让 Lead 决定是否重新 spawn。
- F12.4 **核心循环**：spawn 队员 → 收结果（`<task-notification>`，含 usage）→ Lead 阅读理解（Synthesis）→ `SendMessage(to="<agent_id>", message=…)` 续写 → 循环，直至收敛。

### F13 Plan 审批工作流

- F13.1 spawn 时 `plan_mode_required=True`（来自 subagent 定义新字段或 spawn 参数）→ 队员初始 permission mode 设为 `plan`。
- F13.2 队员在 plan 模式生成 Plan 后，经 `SendMessage(to="lead", type="text", summary="plan ready", content="<plan text>")` 发给 Lead——本期不强制结构化 Plan 类型（Lead 自行识别）。
- F13.3 Lead 用 `SendMessage(to="<member>", type="plan_approval_response", payload={"approve": bool, "feedback": "…"})` 回复。
- F13.4 队员收到 `plan_approval_response`：`approve=True` → 从 Team config 读 Lead 当前 permission mode（本期 `default`）切换后继续执行 plan；`approve=False` → 把 `feedback` 当作新用户消息加入对话，重新进入 plan 模式。
- F13.5 门控：批准前队员修改类工具调用被拒绝（结构化错误提示「先等 Lead 批准」）；批准后本次任务内可执行修改。

### F14 Coordinator Mode

- F14.1 `is_enabled(cfg)`：`feature_has(cfg, "COORDINATOR_MODE")`（经配置读 `features.coordinator_mode`）且 `env_truthy(NEWCODE_COORDINATOR_MODE)`（接受 `"1"`/`"true"`/`"yes"`，大小写不敏感）→ 双锁全开才生效；缺一把启动时 stderr 提示缺哪把。
- F14.2 生效时机：启动时判定（环境变量进程级），会话内固定，不做运行时切换；**运行时不可解锁**（避免 LLM 被注入后自行解锁；唯一解除方式=退出 newcode 重启）。
- F14.3 **工具收窄**：`COORDINATOR_ALLOWED_TOOLS = [Agent, TeamCreate, TeamDelete, TaskCreate, TaskGet, TaskList, TaskUpdate, SendMessage, read_file, glob, grep, bash]`；剥夺 `write_file`/`edit_file`。
- F14.4 TUI 状态栏显示 `[COORDINATOR]` 模式标签。
- F14.5 **四阶段工作流提示词注入**（追加 system_prompt 末尾）：
  1. **Research** — 执行者：队员，可并行，调查代码库、定位文件、理解问题；
  2. **Synthesis** — 执行者：Lead（coordinator），阅读调查结果、理解问题、撰写实施规格；**不得把理解能力委托出去**；
  3. **Implementation** — 执行者：队员，按规格修改代码并提交；
  4. **Verification** — 执行者：队员，测试改动是否正确。
- F14.6 **「派完就停手等汇报」纪律**（关键约束）：
  - 派出 Agent / SendMessage 后**禁止**立刻调 read_file/glob/grep/bash 自己探索；**禁止**用 sleep / TaskList 轮询凑时间；`task.Manager` 完成时自然推送 `<task-notification>`，Lead 下一轮被唤醒后再继续；
  - 唯一该做：发一行总结「已派 N 名队员探索 X，等结果」，让本轮结束；
  - 允许自己读的场景仅限：Research 第一次目标定位；Synthesis 读队员产出的报告文件；Verification 的 git diff / git status 等收敛操作。
  - 纯 prompt 引导不强制（对抗「LLM 派完队员后等不及自己 glob 重复劳动」的常见行为）。

### F15 收敛合并

- F15.1 收敛由 LLM 推理驱动，**不提供专门 merge 工具**——Lead（无论是否 Coordinator Mode）在所有任务 `completed` 后自主用 Bash 跑：`git merge worktree-team-<sanitized_team>+<member> --no-ff -m "merge: <member>"` 逐个合。
- F15.2 冲突解决：Lead 用 `read_file` 看冲突文件、`edit_file`（非 Coordinator）/ `bash`（Coordinator）写解决方案、`bash` 跑 `git add` + `git commit`。
- F15.3 回滚：判断搞不定时自主 `git merge --abort`，给用户报告冲突文件 + 队员 worktree 路径；**不删队员 worktree**。

### F16 /team Slash 命令

- F16.1 `/team list`：遍历 `manager.teams`，每行 `<name>  <backend>  <member_count> 成员  [<active>/<total>] 活跃`。
- F16.2 `/team info <name>`：Team 详情——配置路径、各成员 name/agent_id/backend/worktree_path/is_active/任务计数。
- F16.3 `/team delete <name> [--force]`：调 `manager.delete(name, force)`。
- F16.4 `/team kill <member>`：查所属 Team，调对应 backend.kill，然后 `remove_member`。

### F17 持久化与恢复

- F17.1 `config.json` 结构：`name` / `sanitized_name` / `lead_agent_id` / `backend` / `description` / `created_at` / `members[]`（字段同 F1.2）。所有写操作原子（`.tmp` + `os.replace`），受 `Team._lock` 保护；跨进程 reload-before-modify（F1.7）。
- F17.2 启动扫描：解析失败的目录跳过并 stderr 警告；**不自动恢复 in-process 队员**（进程重启后 in-process 状态丢失，is_active 视为 False）；Pane 队员按 `pane_id` 探测后端是否仍在（`tmux has-session` / `it2 list-panes`），不在的 is_active 标 False。
- F17.3 队员 session 沿用 ch12：路径 `<project_root>/.newcode/sessions/<id>/conversation.jsonl`；Team 删除时一并删除。
- F17.4 `Manager.delete(name, force=True)` 顺序：持锁校验 → 对每个非 lead 成员用其 `backend_type` 解析 Backend 实例并 `backend.kill`（Pane 子进程检测到 mailbox 目录消失会自行优雅退出兜底）→ 删 session 目录与 worktree → `shutil.rmtree(config_dir)` → 从 `teams` dict 移除。

## 非功能需求

- N1：**工具稳定性**——主 Agent 平时（未 TeamCreate）看到的工具列表稳定；`TeamCreate`/`TeamDelete` 总是可见；`Agent` 工具的 `team_name` 参数对模型可见但仅在调用时校验；prompt cache 不抖动
- N2：**工具可见性**——协作工具（TaskCreate 等）仅团队上下文出现（队员 + Lead 团队态）；未建团队的主入口与普通子 Agent 不可见；经 `apply_agent_tool_filter` 在 spawn 时注入
- N3：**邮箱并发安全统一机制**（文件锁）——三种后端共用；in-process 多 asyncio task 写同一 mailbox 也由文件锁串行
- N4：**锁粒度**——Team 状态变更受 `Team._lock`（`asyncio.Lock`）；Team 之间互不相干各自一把锁；`Manager._lock` 仅保护 `teams` dict
- N5：**不持长锁**——后端 spawn / kill 调用不持 `Team._lock`（避免长锁）；只在更新 members 时短暂持锁
- N6：**安全**——`sanitize` 严格限制字符集防路径遍历（团队目录、邮箱文件名）；tmux send-keys / 唤醒命令不拼接不可信文本
- N7：**不静默降级**——显式指定后端不可用 → 结构化错误；tmux 会话外 `new-session -d` 失败 → 报错而非回落 in-process；自动选择失败给明确理由
- N8：**优雅降级**——无终端复用工具（tmux/iterm2 均不可用）→ 自动 in-process，不崩
- N9：**错误隔离**——团队操作失败不影响主 Agent 主流程与 TUI
- N10：**持久化健壮**——config.json / 邮箱 / 任务列表 / session 损坏不阻断启动（警告并跳过/重置）
- N11：**数据保护**——收敛合并前不静默丢变更；TeamDelete 的 force 由用户显式选择；回滚保留队员 worktree
- N12：**可诊断**——后端选择、spawn、消息投递失败定位到具体步骤与原因
- N13：**中文友好**——工具描述、错误消息、TUI 输出、coordinator 提示词全中文；代码注释中文
- N14：**沙箱临时目录白名单**——权限沙箱允许写入项目根**之外**的 `/tmp` 与 macOS 真实路径 `/private/tmp` 作为系统临时目录白名单（file-class 工具生效；bash 走 exec-class 不受沙箱约束）。理由：工具脚本和队员经常需要 /tmp 中转文件，严格限定项目根内会误杀正常用法
- N15：**测试规范**——接线测试自动跑、mock 驱动真实代码路径、每测试标注防的 bug；无 API key / 无真实终端可执行（tmux 相关用 mock 子进程驱动真实代码路径）
- N16：**文档保护**——docs/ 不可变（本流程四份文档除外）
- N17：**版本号**——0.15.0（`newcode/__init__.py` 与 `pyproject.toml` 两处一致）
- N18：**兼容**——ch04~ch14 存量功能零回归；pytest 全绿、ruff 通过
- N19：**待人工验证标注**——iterm2 实装、真实 tmux 交互、macOS `/private/tmp` 等环境受限项标「待人工验证」，不混入通过

## 不做的事

- 跨 newcode 进程的 Team 共享（同一仓库同一时刻单实例操作活跃 Team）
- 跨机器分布式 Team
- 队员之间实时流式通信（走 mailbox 文件 + 轮询/Wake，不走 socket）
- 复杂任务依赖约束（优先级、deadline、SLA）
- 任务自动分配（Lead 与队员都靠 LLM 推理领任务，系统不做调度）
- 队员的细粒度资源限额（token 上限、超时硬限制）
- Plan 审批的结构化 Plan 类型（本期 Plan 文本就是 SendMessage content，Lead 自行识别）
- Windows 平台特殊适配（iterm2 仅 macOS；tmux 在 WSL 可用但不保证；本期以 macOS / Linux 为主）
- Coordinator Mode 的运行时解锁与重新进入
- 跨 Team 寻址（SendMessage 只能在同一 Team 内寻址）
- 插件来源的 Team 后端
- 队员互改对方 worktree（各自隔离，仅经共享任务列表与邮箱协作）

## 验收标准

- AC1（F1.3/F17.2）：`Manager` 构造时 `~/.newcode/teams/` 不存在自动创建；已有时正确扫描子目录还原 `teams` dict；坏 config.json 跳过 + stderr 警告、不崩
- AC2（F1.4）：`create("refactor auth")` → sanitize 为 `refactor-auth`，`~/.newcode/teams/refactor-auth/config.json` 落地，`backend` 字段反映 `detect_backend` 结果
- AC3（F1.4）：同名 Team 二次 create 自动后缀 `-2`，目录与 sanitized_name 都生效
- AC4（F1.5）：`delete(name, False)` 有 `is_active != False` 成员时抛 `TeamHasActiveMembersError`，目录仍在
- AC5（F1.5）：`delete(name, True)` 删 Worktree、删 session 目录、删 config_dir
- AC6（F2.4）：`detect_backend()`——`$TMUX` 已设返回 `tmux`；未设但 `$TERM_PROGRAM==iTerm.app` 且 `it2` 可执行返回 `iterm2`；都无但 `tmux` 在 PATH 返回 `tmux`；否则 `in-process`
- AC7（F10.3/F10.7）：`Agent(team_name="<existing>")` → `.newcode/worktrees/team-<sanitized>+<member>/` 落地、调 backend.spawn、`team.members` 出现该成员；不带 `team_name` 维持 ch13 原行为
- AC8（F5.4）：in-process 队员的 `Agent` 工具调 `team_name` 参数被拦截，抛 `InProcessTeammateNoSpawnError`
- AC9（F7.1/F7.8）：协作工具在未建团队的主 Agent 工具列表**不可见**；在 Team 队员工具列表**可见**
- AC10（F7.3/F7.6）：`TaskCreate` 落 `<team_config_dir>/tasks.json`；`TaskUpdate(task_id, add_blocked_by=[id])` 正确双向更新 `blocked_by`/`blocks`
- AC11（F7.5）：`TaskList(status="pending")` 返回任务带 `is_ready` 字段，反映其 `blocked_by` 是否全部 `completed`
- AC12（F8.2/F8.3）：`SendMessage(to="alice", summary="hi", message="hello")` 在 `mailbox/<alice_agent_id>.json` 追加一条 unread 消息
- AC13（F8.5）：`SendMessage(to="*")` 广播给 Team 内除发件人外所有成员，每人邮箱各得一条
- AC14（F8.4）：并发 10 个 asyncio task 同时向同一 mailbox `write`，最终 10 条全部落盘且无丢失/无截断（集成测试）
- AC15（F8.4）：mailbox lock 文件 `st_mtime` 超 10 秒时，新 write 清掉旧 lock 并继续（集成测试）
- AC16（F11.1/F11.2）：队员 LLM 调用前未读消息以 `<incoming-messages>` 注入 system_reminders；调用后标记 read（单测断言）
- AC17（F12.1）：队员 `run_to_completion` 自然结束 → `config.json` 该成员 `is_active=False`、Lead mailbox 收到 `summary="<member> idle"`
- AC18（F12.2）：`SendMessage(to="alice", message="new task")` 当 alice 已 stop → 从其 session_dir 恢复 Conv 续派（in-process，状态从 Completed 回到 Running）
- AC19（F13.1）：`Agent(team_name=…, subagent_type=…, plan_mode_required=True)` spawn 后该队员初始权限模式为 `plan`
- AC20（F13.3/F13.4）：Lead 发 `plan_approval_response({approve: True})` 后队员下一轮权限模式切回 `default`
- AC21（F14.1/F14.3/F14.4）：feature 开 + env 设 → Lead allowed tools 收窄为 `COORDINATOR_ALLOWED_TOOLS`，`write_file`/`edit_file` 不在其中；TUI 状态栏显示 `[COORDINATOR]`
- AC22（F14.1）：只开一把锁 → 不进入 coordinator 且 stderr 提示缺哪把
- AC23（F3.1）：tmux 后端 spawn 后 `tmux list-panes` 见新 pane，pane 内 newcode 实例启动并连接该 Team
- AC24（F3.3）：`wake(pane_id, agent_id)` 经 `tmux send-keys` 触发目标 pane 输入（集成测试可观察 pane 内容）
- AC25（F5.1）：in-process 队员与主 Agent 同进程运行、共享 `task.Manager`，但有独立 `cwd=worktree_path`
- AC26（F16）：`/team list` 输出含所有 Team 摘要；`/team info <name>` 输出成员详情；`/team delete <name>` 调 `manager.delete`
- AC27（N17/N18）：版本 0.15.0 两处一致；`python -m newcode` 正常启动；ruff check 通过；pytest 全绿
- AC28（tmux 端到端）：在 tmux 会话内启动 newcode → `TeamCreate("demo")` 落地 config.json → `Agent(team_name="demo", name="alice", …)` 观察新 pane + worktree 目录 + 队员写文件落在 worktree → `/team info demo` 显示 alice → `SendMessage(to="alice")` 观察 pane 被唤醒续写 → `/team delete demo --force` 清空（真实 tmux 交互标「待人工验证」）
- AC29（in-process 端到端）：`unset TMUX TERM_PROGRAM` 启动（自动 in-process）→ `TeamCreate("inproc")` → `Agent(team_name="inproc", name="bob", …)` 同进程启动 → bob 完成 `is_active=False` + Lead 收 idle → `SendMessage(to="bob")` 从 session 恢复续写
- AC30（Coordinator 实跑）：`NEWCODE_COORDINATOR_MODE=1` 启动 → 主 Agent 的 `write_file` 调用被拒（is_error=True）；`bash git merge` 调用允许

## 端到端场景（验收参考）

- 场景 1（并行调查 → 综合）：Lead 建团队，两个 in-process 队员并行 Research → 各自经 `task-notification`/idle 通知回 Lead → Lead Synthesis 撰写实施规格
- 场景 2（实现 + 验证 → 收敛）：队员按规格改代码（各落各 worktree）→ Verification 队员测试 → Lead git 合并各分支
- 场景 3（续写循环）：队员完成后 Lead `SendMessage(to="<agent_id>")` 续写 → 同 id 恢复上下文继续，不重头 spawn
- 场景 4（计划审批）：`plan_mode_required` 队员提交计划 → Lead 批准 → 队员动手；驳回 → 队员按 feedback 调整重提
- 场景 5（Coordinator 双锁）：config 开 + env 设 → Lead 工具收窄 + 四阶段提示词 → 派完停手等汇报 → 复杂任务跑完 Lead 全程不改代码
- 场景 6（tmux 后端）：tmux 可用 → 队员在独立 pane 运行、pane 内只读日志流、完成后通知 Lead；Lead 发消息唤醒 pane 续写（真实 tmux 交互标「待人工验证」）
- 场景 7（优雅降级）：无 tmux 且非 macOS 环境 → 自动 in-process，团队行为一致
- 场景 8（TeamDelete 数据保护）：队员 worktree 有未提交变更 → 非 force 拒绝删除提示；force 由用户显式确认后清理
