# MewCode ch10 - SlashCommand 内置命令框架 Spec

## 背景

MewCode TUI 的输入框是单一入口：所有回车提交都作为 user message 送进 Agent 走 LLM。代码里已有一个最小的斜杠分发器，支持 11 条命令（/exit、/quit、/plan、/do、/normal、/compact、/resume、/session、/memory、/delete-plan、/exit-plan），但存在几处明显的工程缺口：

- 命令分发是 `if/elif` 硬编码阶梯，没有"名字 → handler"之外的元数据（描述、别名、执行类型等）
- 没有启动期冲突检测，名字撞了会在运行时静默失效
- 没有 /help；可用命令清单硬编码在两处（未知命令提示文本 + 启动引导文本），互不同步会随版本漂移
- 没有自动补全，用户必须凭记忆敲完整命令
- handler 函数签名直接接收 TUI App 引用，没有 UI 抽象层，所有命令实现紧耦合在 TUI 包内
- 缺少"纯本地查询"类命令（看状态/记忆/权限/会话/帮助），这些非对话操作目前都要让 LLM 去做，浪费 token、行为不确定
- 存量 bug：/memory 出现在 `_is_known_command` 白名单但无 handler，输入会被静默透传给 Agent 消耗 token

ch10 为 MewCode 装上 **SlashCommand 内置命令框架**：一套命令注册与分发机制，让以 `/` 开头的输入**绕过 AgentLoop 直接在本地执行**——常用操作响应快、省 Token、行为确定。

## 目标

- G1：把现有硬编码命令收编进新的注册中心，新增常用本地操作命令
- G2：让常用本地操作绕过 LLM——响应即时、不消耗 token、行为完全可预测
- G3：提供一层 UI 控制抽象，handler 不再直接持有 TUI App 引用，为后续扩展（Skill 系统等）解耦
- G4：注册中心驱动 /help 与未命中提示，单一信源；启动期检测名字与别名冲突直接立即终止启动并报错
- G5：为下一章 Skill 系统预留运行时动态注册能力（注册/查找并发安全）

## 范围

**包含：**
- 命令框架：注册中心、解析器、CommandContext、UIController 接口
- 12 条内置命令（见 F12-F25）+ 存量命令收编
- Tab 补全（简单形式：单匹配直接补全、多匹配弹列表）
- 状态栏联动、未知命令引导
- 对应单元测试与集成测试

**不包含（留给 Skill 系统）：**
- 用户自定义命令、命令热加载、动态 prompt 生成
- 命令级权限控制
- 外部贡献命令（如 Claude Code 的 `mcp__<server>__<prompt>` 模式）
- 完整补全菜单（上下键切换高亮等全套键位，本期采用简单 Tab 补全）

## 功能需求

### F1 命令注册中心

- F1.1：每条命令登记：名字、别名集合、一句描述、执行类型、处理函数；元数据形态对所有命令统一
- F1.2：可选字段：参数提示（argPrompt）、是否隐藏
- F1.3：注册中心在进程启动期对名字与别名做冲突检测，发现任一名字或别名重复立即终止启动并报错，不进入运行时；报错信息必须包含冲突的具体名字或别名（N4）
- F1.4：读写锁保护注册/查找/列举/补全的并发安全（为 Skill 系统运行时动态注册预留）
- F1.5：提供按名字或别名查找、列举（含/不含隐藏）、补全候选的接口

### F2 命令解析器

- F2.1：识别 `/` 前缀输入；第一个空格之前是命令名、之后是参数
- F2.2：命令名转小写，大小写不敏感
- F2.3：空输入与仅含空白字符的输入早返回，不参与分发
- F2.4：未命中任何已注册命令名/别名时，向用户输出引导使用 /help 的提示（提示文本来自注册中心查询，不硬编码命令列表）

### F3 命令执行类型

- F3.1：每条命令必须显式声明属于三类之一——纯本地（KindLocal）、影响界面（KindUI）、提示词（KindPrompt）
- F3.2：纯本地类命令只向用户输出信息，不修改对话历史、不改变界面运行模式、不消耗 token
- F3.3：影响界面类命令可以修改运行时模式、清空或替换会话、退出进程、触发会话恢复，但不向对话历史追加 user 消息
- F3.4：提示词类命令向对话历史追加一条用户消息（含命令产生的固定文本），随后立即触发一次 LLM 回合；该消息按和真实用户消息相同的方式被持久化、对 LLM 可见

### F4 状态机约束

- F4.1：影响界面类（KindUI）与提示词类（KindPrompt）命令仅在 idle 状态可执行，非 idle 时拒绝并提示"请等待当前任务完成"
- F4.2：纯本地类（KindLocal）命令在任何状态下都可执行（不改 App、只读查询）

### F5 CommandContext

- F5.1：打包命令执行所需的全部资源：Agent、对话、UI（UIController）、注册表、PlanManager、SessionRuntime、SessionArchive、MemoryManager、PermissionChecker、版本号、工作目录
- F5.2：Handler 统一签名 `async def handler(ctx: CommandContext, args: str) -> None`，命令实现不需要关心依赖如何获取

### F6 UIController 接口

- F6.1：抽象 UI 操作：向用户打印消息、注入并发送 user 消息、切换权限模式、查询累计 token 用量、查询当前模式/模型/目录/记忆文件名清单、关闭进程、打开会话列表、触发压缩、清空并新建会话等
- F6.2：抽象接口只暴露命令实际需要的方法，不把 TUI App 内部属性直接外露
- F6.3：命令实现不依赖具体终端渲染框架（不 import prompt_toolkit / rich）
- F6.4：TUI 提供真实实现，测试使用 mock 驱动

### F7 参数处理

- F7.1：分发器对命令名后的参数尾巴做最小切分：第一个空格前为命令名、之后为参数串
- F7.2：本期除明确声明的命令外，不解析参数结构；对未声明参数的命令，携带空白外尾随字符的输入按未命中处理
- F7.3：argPrompt 字段存储参数格式提示，作为 Tab 补全的补充（当用户输入命令不带参数时，UI 显示该提示）

### F8 内置命令

#### 影响界面类（KindUI）

- F8.1 `/exit`：关闭 TUI 进程。在通知 TUI 退出前必须取消 TUI 持有的主 asyncio cancel scope，让 Agent.run、memory.update_async、MCP 等后台任务收到 CancelledError；具体进程资源（会话存档 writer 等）由 TUI shutdown 钩子关闭（N12）
- F8.2 `/plan`：切换运行时权限模式到计划模式（存量迁移）
- F8.3 `/normal`：退出计划模式、切回普通模式（存量迁移，替代 /exit-plan）
- F8.4 `/do`：**执行计划**（存量迁移，语义不变）——`/do <slug>` 执行指定 plan，无参弹出计划列表选择
- F8.5 `/compact`：手动触发上下文压缩（存量迁移，通过同一事件流推送压缩进度）
- F8.6 `/resume`：打开历史会话列表，选中后从该会话最后一个 compact 标记之后恢复；沿用 ch09 的所有约束；仅 idle 状态可用（存量迁移）
- F8.7 `/clear`：关闭当前会话存档文件、开新会话存档文件、清空当前内存中的消息列表、把会话累计 token 计数与回合数归零、AppMode 重置回 NORMAL；执行后向用户输出一条 notice，提示已结束当前会话并开启新会话

#### 纯本地类（KindLocal）

- F8.8 `/help`：按命令名字典序排序输出每条已注册命令的"名字 + 一句描述"两列对齐列表；列表来自注册中心单一查询，不硬编码
- F8.9 `/status`：输出当前权限模式、累计 token 输入/输出、可用工具数量、已加载记忆条目数、当前模型名、当前工作目录六项信息，渲染顺序固定一致、key/value 按列对齐
- F8.10 `/memory`：只读。输出当前已加载的项目层与用户层记忆文件名列表（只列文件名，不展开内容、无编辑入口、不触发重载）
- F8.11 `/permission`：只读。输出当前权限模式的字符串名称（与 /status 中相同的字符串形式）；不能修改权限模式、不能编辑持久化规则
- F8.12 `/session`：只读。输出当前会话的标识信息——至少包含当前会话存档文件路径与 session 标识；不能切换或操作会话

#### 提示词类（KindPrompt）

- F8.13 `/review`：向对话注入一条固定文本的"代码审查请求"消息，并立即触发回合；**不读 git diff、不收集任何外部上下文**

#### 衍生扁平命令（纯本地类）

- F8.14 `/memory_list`：列出记忆条目详情（项目层与用户层）
- F8.15 `/memory_add <类型> <内容>`：手动添加一条记忆（如 /memory_add user_preference xxx）
- F8.16 `/memory_clear`：清空该作用域（user/project）全部记忆，沿用现有 MemoryStore.clear()
- F8.17 `/permission_rules`：列出当前生效的权限规则
- F8.18 `/permission_add <规则> <效果>`：新增一条权限规则
- F8.19 `/permission_reset`：重置权限规则（清空本地规则）
- F8.20 `/session_list`：列出可恢复的会话
- F8.21 `/session_resume <id>`：恢复指定会话
- F8.22 `/session_new`：新建会话

#### 存量收编

- F8.23 `/delete-plan`：删除计划（存量迁移，影响界面类）
- F8.24 `/quit`：`/exit` 的别名
- F8.25 `/resume` 同时作为 `/session_resume` 的隐藏别名（ch09 已发布行为向后兼容）
- F8.26 移除 `/exit-plan`（冗余，被 /normal 替代）

### F9 Tab 补全

- F9.1：用户在输入框输入 `/` 开头时，补全激活并实时显示候选；继续输入按当前输入对所有已注册命令名做前缀匹配过滤
- F9.2：补全不参与别名匹配、不参与描述匹配；仅按命令名前缀过滤
- F9.3：当前输入不再以 `/` 开头时（包括删空），补全立即关闭
- F9.4：补全候选显示"命令名 + 一句描述"两列对齐
- F9.5：隐藏命令不参与补全
- F9.6：单匹配时 Tab 直接补全；多匹配时弹列表选择
- F9.7：命中命令后，按 argPrompt 显示参数提示

### F10 隐藏命令

- F10.1：标记 hidden 的命令不出现在 /help 输出中，也不出现在补全菜单中，但 dispatcher 仍能命中（为未来 Skill 系统预留）

### F11 状态栏联动

- F11.1：显示当前权限模式标记 [DEFAULT]/[ACCEPT EDITS]/[PLAN]/[BYPASS] + AppMode 标记 [plan]/[normal]
- F11.2：高频命令提示从注册表派生（不手写死）
- F11.3：命令执行后状态栏自动刷新
- F11.4：状态栏的左右两侧字段、宽度、高度、模式可视标记渲染保持现状不动

## 非功能需求

- N1：所有纯本地命令在用户回车后必须在主线程（asyncio event loop）上完成输出，无可观察延迟
- N2：影响界面类命令的状态变更对用户立即可见（模式切换的彩色徽章在下一帧渲染中生效）
- N3：提示词类命令的注入消息对 LLM 不可与真实用户消息区分；走相同的对话历史追加、相同的会话存档持久化路径
- N3a：KindUI 与 KindPrompt 命令仅 idle 态可执行，非 idle 时拒绝并提示；KindLocal 命令任何状态都可执行
- N4：注册期冲突检测报错必须包含冲突的具体名字或别名，便于排查
- N5：/help 与未命中提示的命令列表必须来自同一注册中心查询，不允许任何硬编码字符串；ReadyHint 文案中允许且仅允许出现 /help 作为入口引导，其他命令名不得硬编码出现
- N6：/status 的 6 个字段渲染顺序固定一致；每行 key 与 value 之间按列对齐
- N7：/clear 写入新会话存档文件后，/resume（或 /session_list）能看到旧会话作为可恢复条目
- N8：收编后，现有命令的外部行为与本 spec 实施前一致（包括 /resume 仅 idle 态可用、/compact 通过同一事件流推送压缩进度、/do 执行计划语义不变等）
- N9：注册中心并发安全（读写锁）
- N10：命令名大小写不敏感
- N11：可测试性：命令不依赖真实终端；UIController 用 mock 驱动真实代码路径
- N12：/exit 必须先取消主 asyncio cancel scope，让后台任务收 CancelledError；资源由 shutdown 钩子关闭
- N13：文档保护：docs/ 下除本流程四份文档外不改

## 用户可观察行为

- 输入 `/help` 看到按字典序排列的全部命令与描述
- 输入 `/status` 看到六项综合状态信息
- 输入 `/memor` 按 Tab 补全为 `/memory`
- 输入未知命令 → 输出引导信息指向 /help
- Shift+Tab 仍切换权限模式
- 状态栏显示模式标记与高频命令提示
- /memory /permission /session 只读；管理操作走 /memory_*、/permission_*、/session_* 衍生命令

## 验收标准

- AC1（F8.8）：键入 /help 回车，输出按字典序排列的命令列表，每行"命令名 + 一句描述"两列对齐
- AC2（F2.4）：键入未注册的 /foobar 回车，输出引导使用 /help 的提示，且不触发任何 LLM 调用
- AC3（F2.2）：键入 /Help（大小写混合）回车，行为与 /help 一致
- AC4（F8.9）：键入 /status 回车，输出六行 key:value，渲染顺序固定一致
- AC5（F8.10）：键入 /memory 回车，输出当前已加载的项目层与用户层记忆文件名列表
- AC6（F8.11）：键入 /permission 回车，输出当前权限模式名称
- AC7（F8.12）：键入 /session 回车，输出当前会话的会话存档路径与 session 标识
- AC8（F8.7）：键入 /clear 回车，对话区域清空、累计 token 计数归零、AppMode 重置 NORMAL；再触发一次 LLM 回合后，在 /session_list 里能看到上一次的旧会话条目
- AC9（F8.13）：键入 /review 回车，状态栏立即进入流式状态、AI 开始回复；会话存档中新增一条 user 角色的消息，文本含审查相关关键字
- AC10（N8）：现有 /exit、/plan、/do、/compact、/resume、/normal 的外部行为与本 spec 实施前完全一致
- AC11（F9.1）：输入框首个字符输入 / 时，补全立即激活并显示候选
- AC12（F9.1）：继续输入 s（输入框为 /s），补全仅显示以 /s 开头的候选
- AC13（F9.6）：多匹配时 Tab 弹出候选列表；单匹配时直接补全
- AC14（F9.5）：隐藏命令不出现补全候选，但 dispatcher 仍能命中
- AC15（F1.3）：在源码中给某条已注册命令名再注册一个同名命令，启动 mewcode，进程立即终止启动并报错并打印冲突名字
- AC16（F8.14-F8.22）：/memory_add 后 /memory_list 可见；/memory_clear 后该作用域清空；/permission_mode 切换生效；/session_list 列出可恢复会话
- AC17（F10.1）：隐藏命令不出现在 /help 输出中，但可正常执行

## 已确认的设计决策

| 决策点 | 选择 |
|---|---|
| /do 语义 | 保留「执行计划」：/do \<slug\> 执行指定 plan，无参弹列表；不做退模式改造 |
| /memory /permission /session 基础命令 | 只读查询（与模板一致）；管理操作走 /memory_* /permission_* /session_* 扁平衍生命令 |
| 计划模式切换 | /plan 进入、/normal 退出（/exit-plan 冗余移除）；Shift+Tab 继续切权限模式 |
| 命名规范 | 基础命令 + 下划线扁平衍生命令：/memory_list /memory_add /memory_clear /permission_rules /permission_add /permission_reset /session_list /session_resume /session_new |
| /review | 不读 git diff，只注入固定"代码审查请求"文本 |
| /memory_clear 范围 | 清空该作用域全部记忆，沿用现有 MemoryStore.clear() |
| /clear 模式重置 | 换新会话时 AppMode 重置回 NORMAL，token 与回合数归零 |
| 补全交互 | 简单 Tab 补全（单匹配直接补、多匹配弹列表），不采用全套键位菜单 |
| /resume | 保留为 /session_resume 的隐藏别名（ch09 遗留向后兼容） |
| 框架位置 | 独立 mewcode/slash/ 包，UI 通过 UIController 抽象 |
| 框架层借鉴 | 吸收模板：N3a 状态机细化、N5 单一信源、N12 cancel scope、F3.4 提示词持久化、F10 隐藏命令双向约束 |
