# MewCode ch13 - 多 Agent 分发架构 Spec

## 背景

ch12 给 MewCode 装上了 Hook 生命周期钩子系统，Agent 在关键节点上有了可编程的扩展能力。但不管挂了多少 Hook，干活的还是同一个 Agent：所有任务都塞进同一个对话上下文，上下文越来越长、噪声越来越多、Token 越烧越快。这一章解决的是「从单 Agent 进化到能分发任务的多 Agent 架构」：主 Agent 可以把子任务委派给独立的子 Agent，每个子 Agent 有自己的上下文、工具集和权限边界，干完活把结果交回来就行。

现状里已有几处铺垫可复用，不是从零开始：

- **skills/executor.py 已有一份「fork 子 Agent」雏形**——隔离内存会话 + 临时 Agent + `registry.filtered()` 收窄工具集 + `make_provider(model)` 模型覆盖 + 结果/Token 写回主对话。ch13 的 Fork 模式是对这套机制的泛化，并把它统一到同一套底座。
- **`Registry.filtered()` / `definitions_filtered()`** 已存在，注释即写「仅 fork 模式子 Agent 用」。
- **ch12 的 hook `agent` 动作是占位**——`hooks/executor.py` 的 `_run_agent` 只打 `agent not yet implemented, skipped`，ch12 spec 承诺「后续章节对接 SubAgent 系统」。
- **缓存命中前提已具备**——Anthropic Provider 的缓存断点在 stable_prompt（首条 user 消息）+ tools；Fork 继承父对话历史逐字节原样、沿用父 stable_prompt 即可命中 prompt cache。

## 目标

- G1：提供统一的 Agent 工具，主 Agent 通过 `subagent_type` 参数选择预定义角色、留空走 Fork 路径；工具列表对模型始终稳定（不因角色定义增减而变化）
- G2：子 Agent 拥有独立的运行时状态——消息历史、权限模式、文件读缓存、token 计数；共享基础设施——LLM 客户端、Hook 引擎、文件系统、工具注册表、权限规则层
- G3：两种创建模式——定义式（空白对话 + 固定角色，可指定独立模型）；Fork 式（继承父对话历史 + 复用父工具集，让首次 LLM 请求命中 prompt cache，强制后台）
- G4：角色定义为 Markdown + YAML frontmatter 文件；多来源加载，优先级：项目级 > 用户级 > 内置 > 插件；同名定义按优先级覆盖
- G5：子 Agent 以「跑到底」模式非交互执行——任务从参数注入不等用户输入，LLM 不再调任何工具即视为完成，返回最后一条 assistant 文本；工具调用链与主循环一致（pre_tool_use Hook → 执行 → post_tool_use Hook），Hook 在子工作者中仍然生效
- G6：权限由角色 `permissionMode` 决定——新增子 Agent 专属 `dontAsk` 模式（所有通过过滤与规则的工具自动批准，无审批弹窗）；能力边界由 `disallowedTools` 锁死、权限模式由 `permissionMode` 控制，两者配合实现全自动运行；**子 Agent 永不升级到主 TUI 弹审批框**
- G7：后台运行两种进入路径——调用时显式 `run_in_background:true`、前台超时自动切；Fork 路径无条件后台；前台→后台移交运行中实例不杀掉重来（本期不做 ESC 手动切换，后续章节补）
- G8：后台任务跑完通过 `<task-notification>` 注入主对话（不打断当前对话）；主 Agent 可用 TaskList / TaskGet / TaskStop / SendMessage 工具查询、操控、续派；用户可用 `/tasks` 斜杠命令族做同样的事
- G9：工具过滤多层防线阻断子 Agent 无限嵌套——全局禁止列表（定义式子 Agent 永远看不到 Agent 工具）、后台白名单（ASYNC_AGENT_ALLOWED_TOOLS 不含 agent 工具，后台/Fork 天然不能 spawn）、Fork 标记运行时检查兜底
- G10：复用统一底座改造 Skill fork——`skills/executor.py` 的 `_execute_fork` 改走 SubAgent 公共启动路径，两条路径同一段 agent 构造与 `run_to_completion` 逻辑
- G11：内置 3 个角色——`general-purpose`（全工具）、`explore`（只读探索，haiku）、`plan`（只读规划，plan 权限模式）；验证角色 `verifier` 用配置开关按需启用、默认不启动；插件级接口保留但本期不实现真插件加载（恒为空）

## 范围

**包含：**
- Agent 工具（统一入口，subagent_type 分流定义式/Fork 式）
- AgentDefinition 数据结构与 Markdown + YAML frontmatter 解析
- 四层加载（项目 > 用户 > 内置 > 插件）与同名优先级覆盖
- 内置角色（general-purpose / explore / plan）+ 配置开关启用的 verifier
- 定义式与 Fork 式两种创建模式（含 Fork Boilerplate 与消息深拷贝装填）
- 隔离与共享边界（运行时状态隔离 / 基础设施共享）
- 「跑到底」非交互执行（`run_to_completion` 复用主循环）+ `dontAsk` 权限模式
- 工具过滤多层防线（全局禁止 / 自定义限制 / 后台白名单 / 定义层 tools/disallowedTools）
- 后台任务管理器（状态、结果、token、起止时间、工具计数、保留/续派/排队/清理策略）
- 前台→后台移交（超时自动，不杀运行中实例；ESC 手动切换本期不做、字段预留）
- Task 工具组（TaskList / TaskGet / TaskStop / SendMessage）+ `/tasks` 斜杠命令族
- hook `agent` 动作接通（ch12 占位兑现）
- Skill fork 底座统一改造
- `.mewcode/config.yaml` 的 `agents:` 配置段（含模型分层、后台总闸、保留/续派上限）

**不包含（留给后续章节或明确不做）：**
- Worktree 级文件系统隔离（下一章）
- AgentTeam 多 Agent 协作编排（后续章节）
- 后台任务的跨会话持久化（任务不落盘，进程退出全局清空）
- 子 Agent 的 L1/L2 上下文压缩（轻量方案：maxTurns 上限 + 滑动窗口；超长/PTL 按任务失败处理）
- 角色文件热更新（启动期一次性加载，编辑后需重启）
- 子 Agent 升级到主 TUI 的 HITL 审批弹窗（子 Agent 永不暂停等用户批准，B1 决策）
- 真正的插件系统（SourcePlugin 占位，加载顺序里插件来源恒为空）
- `TaskCreate` 工具（本期仅 List/Get/Stop/SendMessage）
- ESC 手动切换后台（本期不做——前台→后台仅由超时自动触发，`foreground_sub_agent` 跟踪字段预留，后续章节补）
- 子 Agent 输出 schema 强制结构化（返回纯文本即可；Fork 报告格式经 Boilerplate 约束）
- 跨 SubAgent token 用量汇总到 /status（只在 Manager 内部记录）

## 功能需求

### F1 Agent 工具（统一入口）

- F1.1：子 Agent 包装成一个工具，内部名 `agent`，注册进主 Registry（与 load_skill / 记忆工具同层级，main.py 装配时注册）。工具定义（description + parameters JSON Schema）固定，不随任何角色变化
- F1.2：工具参数（JSON Schema）：
  - `prompt`（必填字符串）：交给子 Agent 的任务指令
  - `description`（可选字符串）：一句话描述任务，供 UI 展示；缺省取角色 description 或 prompt 前 80 字
  - `subagent_type`（可选字符串）：角色名；**缺省时不启用定义式，走 Fork 路径**（F3.2）
  - `model`（可选字符串）：模型覆盖，取值 `haiku` / `sonnet` / `opus` / `inherit`；留空沿用角色定义的 model（F2.1）
  - `run_in_background`（可选布尔，缺省 false）：true 时强制后台启动；Fork 路径忽略此字段（无条件后台，F3.3）
  - `name`（可选字符串）：给本次启动的子 Agent 命名，供 SendMessage 续派寻址（F7.10）；同名后启动的覆盖前面的弱引用
- F1.3：`subagent_type` 有值 → 定义式：按名查已加载的 AgentDefinition（F2）；无值 → Fork 式：走 `build_forked_messages`（F3.2）
- F1.4：主 Agent 的工具列表保持稳定——不因角色存在/加载/被调用而变化（防 prompt cache 抖动）
- F1.5：`subagent_type` 引用不存在的角色 → 工具返回结构化错误 `未知 subagent_type: <name>`，主 Agent 主流程不受影响

### F2 AgentDefinition 定义与加载

- F2.1：角色文件为 Markdown，`---` frontmatter 块开头、紧跟正文（= 子 Agent 系统提示，定义身份/职责/工作风格，伴随整个生命周期）。frontmatter YAML 字段：
  - `name`（可选）：角色名，小写字母 / 数字 / 连字符，长度 1-32；缺省取文件名基名
  - `description`（必填）：一句话描述，用于 Agent 工具 `subagent_type` 文档与 UI 列表
  - `tools`（可选 list[str]）：工具白名单（内部工具名）；空 = 不限制
  - `disallowedTools`（可选 list[str]）：工具黑名单（内部工具名）
  - `model`（可选）：`haiku` / `sonnet` / `opus` / `inherit`，缺省 `inherit`；命名分层经配置映射到实际模型串（F10.1）
  - `maxTurns`（可选 int）：最大迭代轮数；缺省 0 = 未设置 → 回落全局 `agents.max_turns`（缺省 15，F11.1）
  - `permissionMode`（可选）：`default` / `acceptEdits` / `plan` / `bypassPermissions` / `dontAsk`，缺省 `default`；`dontAsk` 是子 Agent 专属（主 Agent 不可用）——自动批准所有规则未命中的工具（F5.3）
  - `background`（可选 bool，缺省 false）：true 时角色强制后台——Agent 工具忽略 `run_in_background` 参数
  - `enabled`（可选 bool，缺省 true）：false 时不加载该角色（内置 verifier 用）
  - 解析复用 skills 的 frontmatter 分离机制与失败隔离模式
- F2.2：四层加载来源与优先级（离使用场景越近越有话语权，同名定义前者覆盖后者）：

| 优先级 | 来源 | 目录 |
|--------|------|------|
| 1（最高） | 项目级 | `<projectRoot>/.mewcode/agents/*.md` |
| 2 | 用户级 | `~/.mewcode/agents/*.md` |
| 3 | 内置 | 随包发布的 `mewcode/subagent/builtin/*.md`（经 importlib.resources 读取） |
| 4（最低） | 插件级 | 插件目录下的 `agents/*.md`（第三方把 Agent 定义打包进插件分发；**本期不实现真插件加载**，此层恒为空，SourcePlugin 常量占位） |

- F2.3：同名定义按优先级覆盖——`resolve(name)` 返回优先级最高的版本；低优先级未被覆盖的角色仍可用
- F2.4：解析失败——内置级失败（代码 bug）启动期 fail-fast 直接 raise；用户/项目级单个文件失败（frontmatter 不合法、未知 model/permissionMode 等字段错）→ stderr 定位到文件与字段并跳过该角色，非法枚举值 warning 降级为缺省（model→inherit、permissionMode→default），不阻断启动
- F2.5：内置角色（frontmatter + 正文均为真实内容，正文参照 Claude Code 内置 subagent 模板写法——身份/职责/工作风格 + 工具使用纪律）：
  - `general-purpose`——通用全能：无 disallowedTools，model=inherit，maxTurns=25，permissionMode=default
  - `explore`——代码探索（只读为主）：disallowedTools=[write_file, edit_file]，model=haiku，maxTurns=30，permissionMode=default
  - `plan`——计划制定（只读 + 产出计划）：disallowedTools=[write_file, edit_file]，model=inherit，maxTurns=15，permissionMode=plan
  - `verifier`——验证角色，**默认 `enabled: false`**，经 `.mewcode/config.yaml` 的 `agents.enable_verifier` 开关启用（F10.1）
  - （maxTurns / model 为建议初值，实现时可调）

### F3 两种创建模式

- F3.1 **定义式**：空白对话（独立 ConversationManager）+ 角色正文作为子 Agent 的 stable_prompt + 角色的 maxTurns / permissionMode / 工具过滤。模型按角色 `model`：`inherit` → 复用父 Provider；`haiku/sonnet/opus` → 经配置映射解析为实际模型串，经 provider 工厂建独立 Provider（复用 main.py `make_provider` 模式）
- F3.2 **Fork 式**：`build_forked_messages(parent_conv)` 做三件事：
  1. **深拷贝**父对话全部消息（逐字节原样，复用父 stable_prompt → 首次请求命中 prompt cache）
  2. 把末尾 assistant 中未完成的 tool_use（无对应 ToolResult）包装为 placeholder ToolResult，使消息格式合法
  3. 末尾追加一条 user 消息，内容 = **Fork Boilerplate + 任务文本**（Boilerplate 见 F3.4）
  - Fork 复用父工具集（受 F6 过滤约束）与父 Provider（含父模型，无独立模型覆盖）
- F3.3：Fork 式**强制后台执行**（`run_in_background` 字段被代码 override 视为 true，保证并行）
- F3.4：**Fork Boilerplate** 是一段 `<fork_boilerplate>` 包裹的固定指令（代码内置常量），覆盖父工作者的默认行为：
  - 不能再 Fork（嵌套阻断见 F6.5）
  - 不要对话 / 提问 / 请求确认
  - 直接使用工具干活
  - 严格限制在分配的任务范围内
  - 最终报告以 `Scope:` 开头，500 字以内

### F4 隔离与共享边界

- F4.1 **运行时状态隔离**（每个子 Agent 独立一份）：
  - 消息历史：独立 ConversationManager（maxTurns 由角色决定；Fork 沿用父窗口上限）
  - 权限模式：独立 permissionMode（F5.3），子 Agent 自身的模式不改变主 Agent 模式
  - 文件读缓存：独立 FileTracker
  - token 计数：任务级独立累计
- F4.2 **基础设施共享**（子 Agent 与主 Agent 共用同一实例）：
  - LLM 客户端：Provider / API 客户端共享（模型覆盖时经工厂建新 Provider，底层 SDK 连接模式一致）
  - 权限规则层：同一套 local/project/user 规则——父对话 `persist_local_allow` 过的精确规则，子 Agent 同样命中（用户已批准过的不再重问）
  - Hook 引擎：同一 Engine——子 Agent 的 turn / 工具级事件走同一引擎，Hook 在子工作者中仍然生效
  - 文件系统：同一 cwd，受既有权限沙箱约束（子 Agent 读写仍限定项目目录内）
- F4.3：**Hook 引擎共享的副作用治理**——子 Agent 事件的 payload 必须带 `agent_id` 字段（`agent-<hex>`），供 Hook 条件区分主/子来源（防用户写的 `turn_end` http 通知 hook 在每个子 Agent 轮次重复触发）；主 Agent 事件的 `agent_id` 缺省为空串

### F5 「跑到底」非交互执行

- F5.1：子 Agent 以 `is_interactive=False` 构造；任务从 `prompt` 参数注入，**永不等待用户输入**
- F5.2：子 Agent 以「跑到底」方式执行——任务注入为 user 消息后进入 ReAct 循环（maxTurns 由 `Agent.max_turns` 决定，子 Agent 用 frontmatter，主 Agent 保持现有 10）；模型不再调工具即自然终止，返回最后一条 assistant 文本；触达 maxTurns 返回最后文本并报 `MaxTurnsReached`；任务已注入时（Fork）不重复注入；**与主对话 `run` 共用同一段循环代码，不重复实现**（方法签名见 plan/task）
- F5.3 **权限决策（无 HITL 升级）**：工具调用权限由角色 `permissionMode` 决定，`is_interactive=False` 下永不弹主 TUI 审批框：
  - `dontAsk`（子 Agent 专属）：所有通过工具过滤与规则（黑名单/沙箱/deny 规则仍拦）的工具**自动批准**
  - 其余模式沿用非交互语义：default 下写文件/命令 ASK→DENY；acceptEdits 放行写；bypassPermissions 全 Allow（黑名单/沙箱仍拦）；plan 同 default
  - 能力边界由 `disallowedTools` 锁死 + 权限模式由 `permissionMode` 控制 = 全自动运行
- F5.4：工具调用链与主循环完全一致——`pre_tool_use Hook → 执行工具 → post_tool_use Hook`，Hook 在子 Agent 中仍然生效（含拦截类：pre_tool_use 拦截 → 该工具被拒并回灌原因）
- F5.5：终止路径（maxTurns 达上限 / 连续未知工具 / 流式错误 / 取消）→ 任务结果带终止原因，不阻塞主流程

### F6 工具过滤多层防线

- F6.1 **全局禁止（GLOBAL_DENY）**：硬编码集合 `{"agent"}`——Agent 工具排除于**定义式**子 Agent（无论前台/后台），不可被配置覆盖
- F6.2 **自定义限制（定义层过滤）**：用户/项目在定义 Agent 时做的额外禁止与收窄——frontmatter 的 `disallowedTools`（黑名单）与 `tools`（白名单）；`tools` 为空 = 不限制；黑名单命中即移除（即使在白名单内）；系统工具（load_skill）豁免过滤恒可见（沿用既有 `is_system_tool` 语义）
- F6.3 **后台白名单（ASYNC_AGENT_ALLOWED_TOOLS）**：硬编码、**不受 Agent 定义文件配置影响**；后台工作者（含全部 Fork、run_in_background、hook 触发的）工具集与该白名单求交集。MewCode 内部名单：
  - 核心六件套：`read_file` / `write_file` / `edit_file` / `list_files` / `search_code` / `execute_command`
  - 记忆工具：`read_memory` / `write_memory`
  - 系统工具：`load_skill`（豁免恒可见）
  - MCP 工具：`mcp__*` 前缀（对应参考里的 WebSearch/WebFetch 网络能力）
  - **不含 `agent` 自身**（B2 层 2）——后台 Agent 天然不能 spawn，即使先前带了也被 4 重过滤剔除
- F6.4：有效工具可见集 = Registry 全部工具 − 全局禁止 →（定义层 `disallowedTools` 黑名单剔除）→（定义层 `tools` 白名单非空则取交集）→（后台工作者再与 ASYNC_AGENT_ALLOWED_TOOLS 取交集）→ + 系统工具豁免
- F6.5 **Fork 嵌套运行时阻断（B2 层 1）**：子 Agent 的对话历史含 `<fork_boilerplate>` 标记时（QuerySource 检测 / 标记扫描兜底），调用 agent 工具直接返回 `is_error=True`「Fork 子 Agent 不能再启动 Agent」——即使工具列表因某种原因残留 agent 工具也拦截
- F6.6：前台→后台移交**不改变已创建的工具集**（子 Agent 创建时即固定，移交途中不重算过滤）

### F7 后台任务管理与续派

- F7.1：两种进入后台的路径：
  1. 调用时显式 `run_in_background: true`
  2. 前台执行超过阈值自动切（`agents.async_timeout_s`，缺省 120s）
  （ESC 手动切换本期不做，见「不做的事」）
- F7.2：Fork 模式强制后台（F3.3）；角色 `background: true` 也强制后台
- F7.3：**前台→后台移交（超时自动）**：移交运行中的实例（**不杀掉重来**）——实现用 `asyncio.wait` 竞速（**非 wait_for**，后者超时会 cancel 内部协程），`manager.adopt_running(...)` 只转移任务所有权继续后台跑；主 Agent 收到形如「任务已转入后台，task_id=agent-xxx」的工具结果，主对话继续
- F7.4：后台任务管理器（单事件循环内 asyncio 任务）记录每个任务：`task_id`（`agent-<hex>`）、`name`（可选）、角色或 fork 标记、`status`、`result`、`err`、token 用量（in/out）、`start_time` / `end_time`、耗时、`tool_count`、`last_activity`、`round`（已执行轮数，首轮=1，续派递增）
- F7.5：任务状态机：`running` → `completed` / `failed` / `cancelled`
- F7.6：**完成通知注入主对话**（不打断当前对话——主 Agent 流式中先排队，空闲时注入）：
  - 以 **user 角色消息**写入主对话历史（XML 标签包裹，让主 LLM 识别为系统注入而非用户输入），并打印到界面
  - 统一结构（`<task-notification>`）：
    ```xml
    <task-notification>
      <task-id>agent-a1b2c3d4</task-id>
      <status>completed</status>
      <summary>Agent "explore" completed</summary>
      <result>...（≤800 字截断）...</result>
    </task-notification>
    ```
  - `status` 取 `completed` / `failed`（带回错误/终止原因）/ `cancelled`
  - `<result>` 字数上限（缺省 800 字）截断，完整结果经 `/tasks show <id>` 或 TaskGet 查看
- F7.7 **子 Agent 生命周期与保留**：
  - `status=completed` 的子 Agent 保留在内存等待续派（不立即销毁）
  - **清理机制**：① 用户主动清理（/tasks kill 等）；② 进程退出全局清空；③ **空闲超时**——`completed` 后 15 分钟无新任务自动清理（`agents.idle_cleanup_minutes`，缺省 15，可配置）
  - **保留上限**：最多保留 `max_idle_agents` 个空闲子 Agent（缺省 10，可配置），超出关闭最旧
  - **任务数上限**：每个子 Agent 总共最多执行 `max_tasks_per_agent` 个任务（缺省 10，首轮 + 续派合计），超限拒绝续派——防上下文过长、失去开子 Agent 的意义
- F7.8 **续派排队**：续派只发给**空闲（completed）**子 Agent；若子 Agent 数达上限且全在执行任务，续派任务可排队，**每 Agent 最多 `max_queue_per_agent` 个排队任务**（缺省 2）；一般情况下不出现排队（空闲才增派）
- F7.9：生命周期——`/clear`、`/resume`、`/session_new`、进程退出时取消所有运行中后台任务、清空所有保留子 Agent 与排队（跨会话持久化不做，不落盘）
- F7.10 **SendMessage 续派**（工具，主 Agent 调用）：参数 `task_id` 或 `name`（至少一个，name 命中最新同名）+ `message`；把 `message` 作为新 user 消息追加到目标子 Agent 的 conv（保留此前所有轮次上下文）→ 重新 `launch` 一轮 `run_to_completion` → 返回 `{status:"accepted", task_id}`；目标不存在 / 已取消 / 任务数达上限 → 结构化错误；运行中 → 入队（F7.8）。**续派复用同 task_id**（状态语义=同一 worker 继续，`/tasks` 查询一个条目）——status 从 completed 重置为 running，`result` 覆盖为本轮文本，`round` 递增；新一轮完成 → 同 task_id 的 `<task-notification>`（status=completed，result=新一轮文本）注入主对话

### F8 Task 工具组与斜杠命令

- F8.1：新增 4 个内置工具（注册进主 Registry，同层级）：
  - `TaskList`（无参）：返回所有非 cancelled/清理 任务的简要列表（id、name、status、tool_count、last_activity）
  - `TaskGet`（`{task_id}`）：返回指定任务完整状态（含 result / err / usage / 起止时间）
  - `TaskStop`（`{task_id}`）：触发取消，返回 `{status:"cancellation_requested"}`；已结束任务返回当前状态
  - `SendMessage`（F7.10）
- F8.2：本期不实现 `TaskCreate`（主要给 Hook 用，Hook 暂未需要独立创建任务）
- F8.3：新增 `/tasks` 斜杠命令族（注册进现有 CommandRegistry，用户侧，与 Task 工具组同一底层）：
  - `/tasks`（零参）：列出所有后台任务，一行一个：`<task_id>  <status>  <角色|fork|name>  <耗时>  in:<n> out:<n>`
  - `/tasks show <task_id>`：详情——状态、角色/name、起止时间、耗时、token 用量、完整结果、终止原因
  - `/tasks kill <task_id>`：终止运行中任务（status→cancelled）；已结束提示当前状态
  - `/tasks send <task_id|name> <message>`：续派——走 F7.10 同一 `manager.continue_agent()` 路径，message 取命令参数剩余部分
- F8.4：无任务时 `/tasks` 输出 `No background tasks.`

### F9 hook `agent` 动作接通

- F9.1：接通 ch12 占位——`hooks/executor.py` 的 `_run_agent` 改为真实实现：按 `agent_name` 查 AgentDefinition，`prompt` 经既有 `{field}` 模板渲染后作为任务注入，触发一个**定义式**子 Agent，**后台**执行
- F9.2：`agent_name` 不存在 / 触发失败 → 记一行 stderr 日志（`[hook <name>] agent ... failed: <reason>`），不中断主流程（沿用 ch12 F9.1 错误隔离）
- F9.3：hook 触发的子 Agent 属于后台工作者，受 F6 全部过滤防线约束（含 ASYNC_AGENT_ALLOWED_TOOLS），结果走 F7.6 完成通知
- F9.4：hook agent 动作执行期间不表达拦截信号（沿用 ch12「agent 动作不 blocked 不 err」语义，通知型副作用）

### F10 Skill fork 底座统一

- F10.1：改造 `skills/executor.py` 的 `_execute_fork`——不再自建临时 Agent 构造路径，改为调 SubAgent 公共启动函数（构造临时 Definition 按 Fork 路径走），复用 `run_to_completion` 与统一的消息装填、工具过滤路径；对外行为不变（结果经 `append_assistant_message` 写回主对话、token 写回主统计），消灭两套子 Agent 构造代码并存

### F11 配置

- F11.1：`.mewcode/config.yaml` 新增 `agents:` 段（缺省全部可用，段缺失不报错不阻断）：
  ```yaml
  agents:
    enable_verifier: false          # 启用内置 verifier 角色（F2.5）
    enable_subagent_background: true # 后台总闸；false 时显式/超时后台全部失效，Fork 报错「后台禁用，无法 Fork」
    max_turns: 15                   # 子 Agent 全局缺省最大轮次（角色未设 maxTurns 时，F2.1）
    async_timeout_s: 120            # 前台子 Agent 自动转后台阈值（F7.1）
    idle_cleanup_minutes: 15        # 空闲子 Agent 清理超时（F7.7）
    max_idle_agents: 10             # 空闲子 Agent 保留上限（F7.7）
    max_tasks_per_agent: 10         # 每子 Agent 任务总数上限（F7.7）
    max_queue_per_agent: 2          # 每子 Agent 排队任务上限（F7.8）
    model_tiers:                    # 模型分层 → 实际模型串（F2.1/F3.1）
      haiku: "<model-id>"
      sonnet: "<model-id>"
      opus: "<model-id>"
  ```
- F11.2：`model_tiers` 中某 tier 未配置 → 解析为 `inherit`（降级父模型）并记 warning；`haiku/sonnet/opus` 之外的取值 → 加载期按 F2.4 降级为 `inherit`

## 非功能需求

- N1：**错误隔离**——任何子 Agent 失败、角色解析失败、任务执行出错都不影响主 Agent 主流程与 TUI（对应 F1.5 / F2.4 / F9.2）
- N2：**嵌套防护**——Agent 工具经全局禁止 + 后台白名单双重剔除，再加 Fork 标记运行时检查兜底，杜绝 A→B→C 无限嵌套（对应 F6.1 / F6.3 / F6.5）
- N3：**无侵入**——未配置任何角色 / 无后台任务时，主 Agent 行为与 ch12 一致，开销近零（空加载短路）
- N4：**缓存命中**——Fork 式继承父历史逐字节原样 + 复用父 stable_prompt，首次请求命中 prompt cache（对应 F3.2）；主 Agent 工具列表不随角色变化，防 tools 缓存抖动（N1/F1.4）
- N5：**通知不打断**——完成通知在流式中排队、空闲注入，绝不打断当前对话（对应 F7.6）
- N6：**结果可控**——注入主对话的 `<result>` 截断（缺省 800 字），完整结果经 `/tasks show` / TaskGet 查看（对应 F7.6）
- N7：**后台总闸**——`enable_subagent_background: false` 时所有后台路径失效、Fork 直接报错（对应 F11.1）
- N8：**并发安全**——多后台任务在单一事件循环内并发（asyncio task），共享 LLM 客户端 / Hook 引擎 / 文件系统无跨线程竞争
- N9：**内存有界**——空闲子 Agent 保留受 idle 超时 + 保留上限 + 任务数上限约束，防止无界增长（对应 F7.7/F7.8）
- N10：**复用既有机制**——frontmatter 解析复用 skills 解析器；fork 执行复用 skills executor 的隔离会话模式；模型工厂复用 main.py `make_provider` 模式（对应 F10）
- N11：**可诊断**——角色解析失败 / 任务失败定位到文件与原因，不静默
- N12：**Hook 可区分来源**——子 Agent 事件 payload 带 `agent_id`，Hook 条件可按来源筛选（对应 F4.3）
- N13：**文档保护**——docs/ 不可变；ch13 四份文档走 mew-spec 流程生成，逐份经用户审批
- N14：**版本号**——本章开发前 bump 到 0.13.0，`mewcode/__init__.py` 与 `pyproject.toml` 同步更新
- N15：**测试规范**——接线测试自动跑、不依赖真实终端与 API key；mock 驱动真实代码路径（复用 ch12 的 object.__new__ / mock provider 手法）；每个测试标注它防的 bug

## 验收标准

- AC1（F1.1/F1.4）：`agent` 工具注册成功，主 Agent 工具定义列表含且仅含它；加载任意角色前后主 Agent 工具定义列表数量与 schema 一致
- AC2（F1.2/F1.3）：`agent` 工具调用 `{prompt, subagent_type:"explore"}` 时，主 Agent 收到的 tool_result 是 explore 子 Agent 的最后一条 assistant 文本
- AC3（F1.5）：`subagent_type:"non-existent"` → 结构化错误 `未知 subagent_type`，主 Agent 继续
- AC4（F2.2/F2.3）：项目级 `.mewcode/agents/explore.md` 覆盖内置 explore，`resolve("explore")` 返回项目级版本；用户级与项目级同名 → 项目级生效；未覆盖的名字正常可用
- AC5（F2.4）：用户/项目级角色 frontmatter 写未知 `model` / `permissionMode` → 启动 stderr 定位并降级缺省（inherit / default），该角色仍可 resolve 与调用；内置级解析失败直接 raise
- AC6（F2.5/F11.1）：默认启动 `subagent_type:"verifier"` → 未知角色；配置 `enable_verifier: true` 后可用
- AC7（F3.2/N4）：`agent` 调用不传 `subagent_type` 时，子 Agent 收到首条 user 消息以 `<fork_boilerplate>` 起头，且消息列表前缀与父对话逐字节一致（测试断言）
- AC8（F3.3/F7.2）：Fork 调用不传 `run_in_background` 也直接后台，tool_result 立即返回 `{task_id, status:"async_launched"}`
- AC9（F5.2）：子 Agent 以 `run_to_completion` 执行，模型不再调工具即返回最后一条文本；maxTurns 达上限时返回最后文本并报 MaxTurnsReached
- AC10（F5.3）：角色 `permissionMode: dontAsk` 时，bash 等需 Ask 的工具直接放行，无审批弹窗；未设 dontAsk 时 default 模式下 write_file 被拒（ASK→DENY），结果含拒绝原因
- AC11（F5.3）：子 Agent 运行中**永不**弹主 TUI 审批框（无 HITL 升级）——mock 断言无 HITL_REQUEST 事件流向主对话
- AC12（F5.4）：子 Agent 工具调用链与主循环一致——pre_tool_use 拦截 hook 生效，结果含 `[hook <name>]` 拒绝原因；post_tool_use 也触发
- AC13（F6.1）：定义式子 Agent 的工具定义不含 `agent`
- AC14（F6.3/B2）：后台工作者（含 Fork）的工具定义与 ASYNC_AGENT_ALLOWED_TOOLS 求交集后**不含 `agent`**；即使角色 `tools` 白名单写了 `agent` 也不可见
- AC15（F6.5/B2 层1）：Fork 子 Agent 对话历史含 `<fork_boilerplate>`，其调 agent 工具 → 返回 `is_error=True`「Fork 子 Agent 不能再启动 Agent」（模拟工具列表残留 agent 的场景）
- AC16（F6.2）：角色 `disallowedTools: [execute_command]` → 子 Agent 工具集不含 execute_command；`tools: [read_file, list_files]` → 仅含白名单 + 系统工具
- AC17（F7.1/F7.3）：前台子 Agent 跑超 `async_timeout_s` → 自动转后台，tool_result 含「已转后台 + task_id」，主对话恢复，任务随后正常 completed；**移交未杀实例**（同一 run 继续，非重启——断言 mock 计数未归零）
- AC18（F7.4/F7.5）：任务完成 `/tasks` 显示状态 completed、起止时间、token、tool_count；task_id 为 `agent-<hex>` 格式
- AC19（F7.6/N5/N6）：任务完成主对话历史追加一条 user 消息为 `<task-notification>` 结构（task-id/status/summary/result），界面打印同内容；`<result>` 超过 800 字被截断；主 Agent 流式中完成 → 排队不打断，空闲后注入
- AC20（F7.7）：子 Agent `completed` 后 15 分钟无续派 → 自动清理；`max_idle_agents=10` 超出关最旧；`max_tasks_per_agent=10` 达到后 SendMessage 续派返回错误
- AC21（F7.8）：续派给运行中的子 Agent → 入队（≤2）；空闲子 Agent 收到续派立即执行；新一轮完成注入新 `<task-notification>`
- AC22（F7.10/F8.3）：`SendMessage({name,message})` 与 `/tasks send <id> 续...` 让仍存活的已完成子 Agent 接到新任务并重新跑动（**同 task_id**、round 递增、result 覆盖为本轮文本），跑完结果作为同 task_id 的 `<task-notification>` 注入；目标不存在 / 达任务上限 → 结构化错误
- AC23（F7.9）：执行 `/clear` / `/resume` / `/session_new` / 进程退出时，运行中后台任务被取消、保留子 Agent 与排队全部清空
- AC24（F8.1）：`TaskList` 返回后台任务列表（含 id/name/status/tool_count）；`TaskGet({task_id})` 返回含 result 的完整状态；`TaskStop({task_id})` 触发取消、状态变 cancelled
- AC25（F9.1/F9.2）：配置一条 `agent` 动作 hook（`agent_name=explore, prompt=...`），事件触发后 explore 子 Agent 后台运行，完成通知注入主对话；`agent_name` 无效时 stderr 记失败日志、主流程不受影响
- AC26（F10.1）：Skill fork 模式调用走 SubAgent 底座——`skills/executor.py` 的 `_execute_fork` 内部只装饰参数后调 SubAgent 公共启动函数，行为不变（结果/token 写回主对话）
- AC27（F11.1/N7）：`enable_subagent_background: false` 时，`run_in_background:true` / 超时自动切全部失效强制前台；Fork 调用返回结构化错误「后台禁用，无法 Fork」
- AC28（F4.3/N12）：子 Agent 事件的 hook payload 含 `agent_id`（`agent-<hex>`），主 Agent 事件不含；按 `agent_id` 为空的 hook 条件不匹配子 Agent 事件
- AC29（N1）：子 Agent 流式错误 / maxTurns 达上限 → 任务 failed 带原因，主 Agent 与 TUI 不崩
- AC30（N3）：未加载任何角色 / 无后台任务时，主 Agent 行为与 ch12 一致（全部存量测试通过）

## 端到端场景（验收参考）

- 场景 1（定义式前台快速任务）：用户让主 Agent 查一个函数定义；主 Agent 调用 `agent(prompt, subagent_type="explore")`；子 Agent 前台执行并同步返回结果文本作为工具结果，主 Agent 据此继续回复
- 场景 2（前台超时自动转后台）：主 Agent 调用 `agent(prompt, subagent_type="explore")` 调研整个代码库；超过 120s 自动转后台，主 Agent 收到「已转后台 + task_id」并继续；用户继续对话；完成后 `<task-notification>` 注入主对话，后续轮主 Agent 可引用结果
- 场景 3（ESC 手动移交）：**本期不做**（B 决策，对齐参考）——前台→后台仅由超时自动触发，ESC 手动切换留待后续章节
- 场景 4（Fork 模式）：主 Agent 调用 `agent(prompt)`（不带 subagent_type）；子 Agent 继承父历史 + 命中缓存，强制后台执行，完成后结构化通知注入
- 场景 5（续派闭环）：主 Agent 用 `name:"worker-1"` spawn 后台子 Agent → 完成后 `SendMessage(name:"worker-1", message:"接着做...")` 续派（同 task_id 复用、round 递增）→ 新一轮完成注入同 task_id 的通知；子 Agent 累计 10 个任务后续派被拒
- 场景 6（hook 触发子 Agent）：配置 session_start hook 的 agent 动作；启动后 explore 子 Agent 后台运行，完成通知注入主对话
- 场景 7（嵌套防护）：任一子 Agent 运行中，其工具定义均不含 `agent`；Fork 子 Agent 即使残留 agent 工具也会被标记检查拦截
