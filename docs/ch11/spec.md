# MewCode ch11 - Skill 技能包系统 Spec

## 背景

ch10 为 MewCode 装上了 SlashCommand 内置命令框架，用户通过 `/` 前缀的本地命令快速操作（12 条命令，注册中心 + TUI 分流器 + 补全）。但这些命令是**编译进二进制的 Python 代码**：改一条命令的提示词要改源码、重新构建、重启进程。

可复用的 AI 操作（提交代码、审查代码、跑测试……）本质上是一套「SOP 指令 + 工具约束」的组合。当前它们散落在：
- 硬编码的 SlashCommand（如 `/review` 是 PROMPT 类型，注入一段固定文本触发 LLM 回合）
- Claude Code 宿主技能（如 `.claude/skills/github-upload`，属于宿主环境，不经 MewCode）

ch11 引入 **Skill 技能包系统**：把可复用的 SOP 装进**可编辑的 Markdown 文件**（带 frontmatter + 资源的能力包），随时可编辑、不需要编译。核心机制是**两阶段加载 + 渐进式披露**——Agent 平时只看到 Skill 的名字 + 一句说明（几十 token），只有当判断任务匹配某个 Skill 时，才通过内置 `load_skill` 工具把完整指令和专属工具加载进当前会话。`/` 显式触发与意图识别自动触发共用同一套执行器。`inline` 模式 SOP 在主对话内执行，`fork` 模式独立子 Agent 隔离执行后把结果回流。

同时解决三个痛点：(1) 可复用操作无法复用与分发；(2) 工具一多模型选错的概率上升；(3) 缺少任务级工具白名单（allowedTools 最小权限）与上下文隔离。

## 目标

- G1：把可复用 AI 操作沉淀为独立 Markdown 文件（YAML frontmatter 元信息 + Markdown 正文 SOP），无需编译、随时可编辑
- G2：两阶段加载——平时只注入摘要（名字 + 一句说明），按需才加载完整指令，控制 token 成本
- G3：三级搜索路径（项目级 > 用户级 > 内置级）加载 Skill，同名按优先级覆盖，支持热加载
- G4：两种执行模式（inline 共享上下文 / fork 独立子 Agent 隔离执行后回流），`$ARGUMENTS` 参数替换，allowedTools 工具白名单收窄（最小权限 + 提升模型选择准确率）
- G5：目录型 Skill 自包含能力包（SKILL.md + tool.json + references/），工具经子进程执行，不引入进程内第三方代码
- G6：三个内置 Skill 样板（commit / review / test）作为生产力工具兼参考模板；`/review` 从硬编码 SlashCommand 升级为 Skill
- G7：`/skill` 管理命令（list / info / reload / load / on / off / unload）控制 Skill 生命周期
- G8：失活三层机制——压缩自动淘汰（共享 token 预算）、会话自然失活、手动主动移除

## 范围

**包含：**
- Skill 定义与解析（frontmatter + body 分离与校验、名字自动归一化）
- Skill 加载器（三级路径、同名覆盖、内存 `_cache` 回退、热加载、自动注册为 `/名字` 命令）
- Skill 执行器（inline / fork 两模式、`$ARGUMENTS` 替换、allowedTools 过滤、fail-fast 依赖检查、目录型工具子进程执行）
- 两阶段加载（阶段一摘要进 env context；阶段二 `load_skill` 工具激活完整 body 到 env context + 注册 tool.json 工具 + 返回确认）
- Agent 侧集成（activeSkills 列表、env context 每轮重建、系统工具豁免 allowedTools、Skill 嵌套触发）
- 三个内置 Skill：commit（inline）、review（fork）、test（inline）
- `/skill` 管理命令：list / info / reload / load / on / off / unload
- 失活机制（压缩预算淘汰 / 会话自然失活 / 手动移除）
- 目录型 Skill：SKILL.md + tool.json + references/ 自包含能力包
- 对应单元测试与集成测试

**不包含（留给后续章节）：**
- Skill 市场与分发（远程安装的设计蓝图记录于此，见「未来蓝图」，ch11 不实现）
- Skill 版本管理（版本字段声明、依赖声明、迁移）
- 进程内动态 import 工具实现（第三方 Python 代码沙箱插件运行时）——ch11 工具实现一律子进程执行
- 命名参数占位符（如 `$ARGUMENT.1`、`$OUTPUT_PATH`）——ch11 仅支持单个 `$ARGUMENTS` 整段替换
- 模型 override 于 inline 模式（当前会话模型不能中途切换；fork 模式可指定模型）
- Skill 之间的显式依赖声明（嵌套触发经 `load_skill` 系统工具，不引入依赖图）

## 功能需求

### F1 Skill 定义与解析

- F1.1：单个 Skill 用「YAML frontmatter + Markdown 正文」描述；解析器负责分离两者并独立校验。两种磁盘布局：
  - 单文件：`<目录>/<name>.md`
  - 目录型：`<目录>/<name>/SKILL.md` + `tool.json` + `references/`
- F1.2：frontmatter 字段（元信息完整集合）：
  - `name`：唯一名字（必填）
  - `description`：一句话说明（必填；用于阶段一摘要与 `/skill list`）
  - `allowedTools`：可见工具白名单（可选；缺省 = 不限制，即主环境全部工具可见，与未激活行为一致）
  - `mode`：执行模式（可选；`inline` / `fork`，缺省 = `inline`）
  - `context`：fork 模式的历史携带策略（可选；`full` / `recent:N` / `none`，缺省 = `none`，仅 fork 模式生效）
  - `model`：指定模型（可选；缺省 = 当前会话模型；fork 模式按此建独立 provider，inline 模式忽略并告警）
  - `tools`：目录型 Skill 专用——指向同目录 `tool.json`（可选；缺省 = 无注册工具）
  - 补充说明：frontmatter 键名 `context` 解析后存内部字段 `fork_context`；`mode` / `context` 取值非法（不在枚举内）时 warning 降级为缺省值（mode→`inline`、context→`none`），不阻断加载（name 非法则抛错跳过，见 F1.5）
- F1.3：正文是发给模型的 SOP 指令，支持 `$ARGUMENTS` 占位符替换用户传入内容（整段替换）；没有占位符则原样返回
- F1.4：名字**自动归一化**——解析时转小写、非字母数字字符转 `-`，与 `/名字` 命令注册的合法性对齐；归一化后冲突按三级优先级处理
- F1.5：解析失败的单个 Skill 文件跳过并记日志（`warning` 级），不阻断整体加载（N1 失败隔离）；frontmatter 缺失或非法（非 YAML、必填字段缺失、值非法）按此处理

### F2 Skill 加载器

- F2.1：三级搜索路径，优先级 **项目级 > 用户级 > 内置级**，同名按优先级覆盖（高优先级层覆盖低优先级层）：
  - 项目级：`<cwd>/.mewcode/skills/`
  - 用户级：`~/.mewcode/skills/`
  - 内置级：`mewcode/skills/builtin/`（编译进二进制的默认 Skill 目录）
- F2.2：扫描三目录，区分单文件与目录型两种布局（F1.1），每个 Skill 解析出元数据对象（name / description / prompt_body / allowed_tools / mode / context / model / source_path / is_directory）
- F2.3：热加载 + 容错——`get(name)` 每次调用都重读源文件，文件修改即时生效；若本次解析失败，回退内存 `_cache` 中的旧版本并记 `warning`（内存 `_cache` = 最近一次成功解析的 Skill 对象，不落盘）
- F2.4：Skill 加载完成后自动注册成 `/<名字>` 短命令出现在 `/help` 与 Tab 补全，描述末尾标注 `[skill]` 以区分内置命令；执行时经 `get(name)` 重读源文件正文支持热更新
- F2.5：同名 Skill 与内置 SlashCommand 冲突时，**内置命令优先**，该 Skill 的 `/名字` 注册跳过并记日志（`/review` 除外，见 F6.4）
- F2.6：加载器对外提供：按名查询、列举、按名重扫单个、全量重扫、启用/禁用状态管理（disabled 集合跨会话持久，见 F7）
- F2.7：fail-fast 依赖检查——启动时经 `validate_tools` 校验所有 Skill 的 allowedTools 白名单，引用当前主环境不存在工具名的 Skill：打 warning 并从 catalog 移除（不阻断其它 Skill 加载）；激活时保留防御性复查，仍失败则报错，不静默过滤

### F3 Skill 执行器

- F3.1：两种执行模式
  - `inline`：共享当前对话上下文，渲染 body 后调用 `Agent.activate_skill(name, body)` 钉到 env context，结果留在主对话历史里
  - `fork`：创建**独立 `ConversationManager`**（主对话状态不被子 Agent 修改，N3），按 `context` 字段决定历史携带，临时 Agent 跑到完成后把结果摘要回流主对话
- F3.2：fork 的 context 三种历史携带策略：
  - `full`：主对话**经 LLM 压缩成一段摘要**再带进 fork
  - `recent:N`：带最近 N 条主对话历史（原样）；`recent` 不带 N 时 N 缺省 = **5**
  - `none`：完全隔离（缺省）
- F3.3：`$ARGUMENTS` 参数替换——Skill 正文中所有 `$ARGUMENTS` 占位符替换为调用时用户传入的内容。显式调用（`/name args`）时替换为 args；自然语言触发（`load_skill` 仅传 name）时替换为空，参数由 Agent 在激活后的对话轮次中获取；无占位符则原样返回
- F3.4：allowedTools 可见性——**inline 模式不真过滤**模型可见工具集：allowedTools 只做两件事——(a) 渲染时在 SOP 顶部插入「本 Skill 设计为只用这些工具，优先使用」提示（渐进式声明，引导模型选对工具）；(b) 参与 fail-fast 校验（F2.7）。**仅 fork 模式真过滤**：子 Agent 用 `definitions_filtered(allowed)` 构造收窄的工具定义集（系统工具豁免透传）
- F3.5：系统工具豁免——`load_skill` 等系统级工具（`is_system` 标记）不受 allowedTools 过滤约束，恒可见，支持 Skill 之间嵌套触发
- F3.6：多个 Skill 同时激活时，各 Skill 的指令并存于 env context；inline 模式工具集保持全量可见（不因多个白名单并集而收窄），fork 模式按对应 Skill 的 allowedTools 收窄
- F3.7：工具过滤经 `definitions_filtered(allowed)` 返回收窄的工具定义列表（系统工具豁免透传），**仅 fork 模式子 Agent 使用**；inline 模式不调用（N8）
- F3.8：fail-fast——启动时白名单校验（F2.7）或目录型 Skill 的 tool.json 无效时，该 Skill 从 catalog 移除并打 warning，不阻断整体加载

### F4 两阶段加载

- F4.1：**阶段一（摘要 Catalog）**——启动时把所有 Skill（排除 disabled）的名字 + 一句说明构建「Available Skills」段（`- name: description` 列表 + `load_skill` 调用指引），通过 `Agent.set_skill_catalog` 注入 **environment context**；env context 每轮重建，/clear 后仍在；摘要总量控制在几十 token 量级（N1）
- F4.2：**阶段二（按需加载）**——Agent 判断用户意图匹配某个 Skill 时调用内置 `load_skill` 工具，该工具做三件事：
  1. 把 SKILL.md 的完整 prompt body 激活到 Agent 的**环境上下文（env context）**（不塞进普通消息历史）
  2. 加载 `tool.json` 声明的专属工具并注册进当前会话（目录型 Skill）
  3. 返回一句简短确认信息（含激活的 Skill 名与可用工具摘要）；**不返回完整 SOP**，避免 tool_result 占用上下文空间
- F4.3：激活后的完整指令钉在 env context，每轮 Agent Loop 重新构建时它都在最显眼位置；同时激活多个 Skill 时各自的指令并存
- F4.4：激活后的完整指令**不进入**普通消息历史——不参与 ch08 压缩/裁剪的常规路径，避免被当作历史消息处理

### F5 Agent 侧集成

- F5.1：Agent 持有 `activeSkills` 状态（实现为独立 `ActiveSkills` 容器类），记录当前会话激活的 Skill（名字、来源层级、激活时间戳、allowedTools、注册的专属工具）
- F5.2：env context 每轮重建——基础环境信息（cwd / 时间 / 模型等）+ Available Skills 摘要段（F4.1）+ 激活 Skill 段（每个激活 Skill 的 name、description、完整 SOP body、可见工具清单）合并成每轮的 env segment
- F5.3：inline 模式模型可见工具集始终为全量（不随 activeSkills 收窄）；fork 模式子 Agent 按对应 Skill 的 allowedTools 经 `definitions_filtered` 收窄
- F5.4：`load_skill` 为系统级工具（`is_system = True`），read-only，注册进主注册表，恒可见（豁免 allowedTools），**不弹权限提示**（N5），便于 Skill 嵌套触发（一个 Skill 的 SOP 里可再调 `load_skill` 激活另一个）
- F5.5：`/clear` 清空对话时顺带调 `Agent.clear_active_skills()` 清空 activeSkills 列表（避免新对话残留上一次激活的 SOP）；新会话只重新注入阶段一摘要（F4.1）

### F6 内置 Skill（commit / review / test）

- F6.1：**commit**（inline）——本地规范提交流程：先 `git status` 看全局，再 `git diff` 与 `git diff --staged` 看细节，区分 staged / unstaged 变更；按内容生成 conventional commit 格式的 message；**逐个 `git add` 而不是 `git add -A`**；最后 `git commit`；变更覆盖超过 10 个文件时主动建议用户拆分
- F6.2：**review**（fork，**必须 fork**）——代码审查按五个维度展开：逻辑正确性、安全性、性能、代码风格、可维护性；报告按严重程度分级（Warning 建议修复 / Critical 必须修复 / Info 可以改进）；代码质量好时给出正面反馈；执行结果摘要回流主对话
- F6.3：**test**（inline）——三步流程：先按项目配置文件检测项目类型决定用哪个测试框架（`go test` / `pytest` / `npm test` 等）；再跑测试命令；最后分析输出。**最关键能力是区分两种失败**：代码本身有 bug 导致测试失败（去改源码）vs 测试自己写错了（去改测试）——Agent 通过对比断言期望与实际行为、再翻看相关代码上下文做判断
- F6.4：`/review` 从 ch10 硬编码的 PROMPT 类型 SlashCommand 升级为 Skill 触发的 fork 执行；ch11 移除内置 review 命令，`/review` 由 review Skill 自动注册接管（F2.5 的冲突豁免）
- F6.5：commit / review / test 三个内置 Skill 作为生产力工具兼参考模板，覆盖共享（inline）与隔离（fork）两种模式

### F7 `/skill` 管理命令

- F7.1：`/skill list`——显示所有 Skill（名字、一句话说明、来源层级、启用状态、是否激活）
- F7.2：`/skill info <name>`——查看单个 Skill 详情（frontmatter 全部字段、源路径、当前是否激活）
- F7.3：`/skill reload [name]`——无 name：全量重新扫描（三目录 + 重新注册命令）；有 name：重读单个源文件刷新内存状态
- F7.4：`/skill load <name>`——手动全量加载一个 Skill（跳过阶段一，直接进入阶段二激活）
- F7.5：`/skill on <name>`——重新启用（从 disabled 集合移除）
- F7.6：`/skill off <name>`——禁用（加入 disabled 集合；从阶段一摘要与可用列表移除；已激活的立即失活）
- F7.7：`/skill unload <name>`——卸载（移出注册 + 清理内存状态；从 disabled 集合移除）
- F7.8：disabled 集合跨会话持久（写入状态文件，如 `~/.mewcode/skills/disabled.json`）；`on` / `off` 立即生效并同步阶段一摘要

### F8 失活机制（三层）

- F8.1：**压缩自动淘汰**——ch08 上下文压缩触发时，激活的 Skill 重新注入，但共享一个 **token 预算上限（固定 4k token）**；当前会话激活的 Skill 总 token 超出预算时，最早的（较旧的）Skill 被自动踢掉（从 activeSkills 移除）为新内容腾空间
- F8.2：**会话自然失活**——Skill 加载绑定当前会话（Session）；结束当前会话并开启新会话（/clear、/session_new）时，之前激活的 Skill 完整内容不带入新会话，新会话只重新加载所有 Skill 的简短描述（阶段一），按需再全量加载
- F8.3：**手动主动移除**——`/skill off`（禁用并立即失活）、`/skill unload`（卸载并清理内存状态）、`/clear`（全清 activeSkills，F5.5）

### F9 目录型 Skill

- F9.1：一个目录 = 一个自包含能力包，结构为 `SKILL.md`（入口，同 F1 定义）+ `tool.json`（专属工具 schema，可选）+ `references/`（工具实现代码、长文档、API 参考等资源，可选）
- F9.2：`tool.json` 负责**注册**——声明这个 Skill **自己新增**的工具，格式与标准 function calling schema 兼容（name / description / parameters / 入口脚本路径）；加载后注册进主环境，成为可被模型直接调用的真实工具；**不与 `allowedTools` 重叠**（后者负责可见性）
- F9.3：写 Skill 时**不要把已经存在的内置工具再放进 tool.json 重复定义**
- F9.4：`references/` 是**静态参考资料层**，不可直接执行；按需加载（Progressive Disclosure）——Skill 激活后 Agent 根据任务进度自主判断是否查阅，需要时用内置 Read 工具读取并注入当前上下文，作为背景知识理解新工具的实现逻辑
- F9.5：工具实现执行——tool.json 声明的工具被模型调用时，MewCode 以**子进程**方式执行 `references/` 里的实现脚本（不 import 进主进程）；脚本型工具也可按 references/ 文档说明由 Agent 构建并调用 Bash 执行
- F9.6：目录型 Skill 加载时工具注册进当前会话（F4.2 第 2 步）；Skill 失活/卸载时其注册工具一并注销
- F9.7：目录型 Skill 整体作为一个可分发的能力包（拷贝目录即分发，不做市场/版本管理，见「不包含」）

## 非功能需求

- N1：**token 经济性**——阶段一摘要总量控制在几十 token 量级；激活的完整 SOP 只在 env context、不重复进消息历史，不随每轮历史累积
- N2：**压缩兼容**——激活 Skill 的注入与淘汰与 ch08 上下文管理协同：压缩后重注入、共享 4k token 预算、超预算淘汰最旧（F8.1）
- N3：**失败隔离**——单个 Skill 解析失败/校验失败（含 allowedTools 白名单引用不存在工具）跳过并记日志（warning），不阻断整体加载；启动时校验剔除（F2.7）与整体隔离原则一致——「文件好但白名单坏」同样跳过不阻断
- N4：**安全**——目录型 Skill 工具实现一律子进程执行，不 import 第三方代码进主进程；工具执行走既有权限系统（PermissionChecker）的确认/放行路径；`load_skill` 为 read-only 系统工具不弹权限提示（N5）
- N5：**权限豁免**——`load_skill` 调用不弹权限提示（read-only 类别 + `is_system=True`）
- N6：**fork 隔离**——fork 模式必须隔离 `ConversationManager`，主对话状态不被子 Agent 修改（N3）
- N7：**热更新**——`get(name)` 每次调用重读源文件正文，文件修改即时生效；解析失败回退内存 `_cache` 旧版并记 warning（F2.3）
- N8：**动态过滤**——工具过滤经 `definitions_filtered` 在 fork 模式构造临时收窄视图（共享底层 Tool 实例），过滤动态生效不要求重启 Agent（F3.7）
- N9：**并发安全**——Skill 加载/激活/失活与 Agent Loop 的执行互斥（复用 ch10 `_run_lock` 会话级互斥语义），避免激活状态在回合中改变导致工具集不一致
- N10：**向后兼容**——无任何 Skill（全 disabled 或全未激活）时，行为与 ch10 一致（工具全可见、env context 无 Skill 段）；ch08 的 `SkillRegistry` 骨架（context/skill.py）升级为完整实现，`total_tokens(estimator)` 接口保留供压缩预算用
- N11：**与 slash 集成**——Skill 自动注册的 `/名字` 命令进入 `/help` 与 Tab 补全（经 ch10 注册中心），描述标注 `[skill]`；`/skill` 管理命令走 ch10 命令框架
- N12：**跨会话持久**——disabled 集合（F7.8）落盘；重启后禁用状态保持
- N13：**fork 成本透明**——fork 独立会话的 token 用量统计并回流报告（在主对话可见），用户能感知独立执行的开销
- N14：**性能**——启动扫描三目录 + 解析元数据有界；阶段一摘要注入不显著增加启动延迟

## 设计决策（与用户确认的记录）

| 决策点 | 结论 | 理由 |
|--------|------|------|
| commit vs github-upload 重叠 | 保持独立 | 提交规范相反（逐个 add + conventional commit vs add -A + chXX 格式）、职责不同（本地规范提交 vs 远程推送部署）；合并会互相拖累。未来可让 github-upload 嵌套调用 commit |
| 失活策略 | 三层机制（F8） | 压缩预算淘汰（自动）+ 会话自然失活 + 手动 /skill 子命令，覆盖自动/自然/主动三路径 |
| 阶段一注入位置 | env context（每轮重建） | 与激活内容同一机制；/clear 后仍在；Skill 增删零特殊处理；几十 token 每轮重复可忽略 |
| 同名冲突策略 | 内置命令优先，skill 跳过+日志 | 保护 clear/session 等生命周期命令；review 迁移为唯一例外 |
| 搜索路径 | 三级（项目 > 用户 > 内置） | 项目级 `.mewcode/skills/`、用户级 `~/.mewcode/skills/`、内置级 `mewcode/skills/builtin/` |
| 缓存 | 内存 `_cache` 回退，无磁盘缓存 | 磁盘缓存收益小（元数据解析轻量）、复杂度大（失效/脏缓存）；内存回退才有容错价值 |
| context 缺省值 | `none` | fork 默认完全隔离；需要上下文显式声明 |
| recent 无 N 时 | N 缺省 = 5 | 合理默认，带最近 5 条 |
| 压缩预算 | 固定 4k token | 先按固定值，可预测；后续可改按窗口比例 |
| context: full 形态 | LLM 压缩主对话成摘要带进 fork | 省 token、fork 不继承主对话全部历史成本 |
| 名字归一化 | 自动归一化（转小写、非字母数字转 `-`） | 与 `/名字` 命令注册合法性对齐，避免「名字合法但注册不成 slash 命令」 |
| 目录型工具实现 | schema 注册 + 子进程执行 | 不引入进程内第三方代码的安全债；复用现有权限系统 |
| references/ 处理 | 静态参考层 + Progressive Disclosure | 不可执行；Agent 用 Read 按需读取作为背景知识；脚本执行可经子进程或 Bash |
| LoadSkill 工具 | 单工具，做三件事（激活 body + 注册工具 + 返回确认） | 避免「调 load 不调 activate」的两步半状态；返回简短确认不返回完整 SOP，避免 tool_result 占空间 |
| tool.json vs allowedTools | 注册 vs 可见性，两套职责不重叠 | 明确分工，禁止重复定义内置工具 |
| is_system 标记 | Tool 协议新增 `is_system` 属性 | 显式标记系统工具（load_skill 等），过滤时自动透传、权限豁免 |
| inline 模式 allowedTools | 仅提示 + fail-fast，不真过滤 | 避免 inline 动态切换工具集的生命周期复杂度（工具集变化 → prompt cache 不稳定）；安全由 ch08 权限引擎兜底；fork 才真过滤 |
| fail-fast 时机 | 启动时 `validate_tools`（warning + 从 catalog 移除） | 贴合原始「启动时立刻报错」；不阻断其它 Skill；激活时保留防御复查 |

## 未来蓝图（ch11 不做，记录防丢失）

- **远程安装（InstallSkill）**：用户把 URL 发给 mewcode、由 Agent 自动安装到 `~/.mewcode/skills/<name>/`。支持 `skills.sh` / `github.com tree` / `raw.githubusercontent.com` 三种 URL；走 GitHub Contents API 递归拉取目录树（无需本地 git），单文件 ≤1 MiB、总大小 ≤8 MiB、文件数 ≤64、深度 ≤4；暂存到兄弟 tempdir，验证含 SKILL.md 后 atomic rename 到位；安装后自动 reload catalog + 重新注册斜杠命令，无需重启

## 验收标准

- AC1：构造一个合法的 Skill 文件（frontmatter + body），加载器能解析出全部元数据，`/skill list` 与 `/skill info` 正确显示（验证：单测 + 手动构造文件）
- AC2：构造一个 frontmatter 非法的 Skill 文件，整体加载不被阻断，日志记录该文件失败（验证：单测断言其余 Skill 仍加载）
- AC3：三级路径同名覆盖——项目级、用户级、内置级各放一个同名 Skill，生效的是最高优先级那个；`/skill info` 显示来源层级（验证：单测）
- AC4：Skill 加载后自动注册为 `/<名字>` 命令，出现在 `/help` 与 Tab 补全，描述标注 `[skill]`；`/commit` 显式触发 commit Skill（验证：集成测试 + TUI 手动）
- AC5：自然语言触发——不输 `/` 命令，说「帮我提交一下」，Agent 通过 `load_skill` 激活 commit Skill（验证：集成测试 mock provider 断言 load_skill 被调用）
- AC6：两阶段加载——启动阶段 env context 含 Skill 摘要（Available Skills 段）但**不含**完整 SOP body；`load_skill` 激活后 env context 出现完整 body，且完整 body 不在消息历史中（验证：payload 断言）
- AC7：allowedTools 提示与 fork 过滤——inline 激活 allowedTools=[read_file] 的 Skill 后，模型可见工具集仍为全量，但 env 的 SOP 顶部含「优先使用 read_file」提示；fork 模式下该 Skill 的子 Agent 工具集 = 系统工具 + read_file（验证：payload 断言 + 集成测试）
- AC8：fail-fast——构造 allowedTools 引用不存在工具的 Skill，启动时被校验剔除（warning 记录、其余 Skill 正常加载），不静默进入可用列表（验证：单测）
- AC9：`$ARGUMENTS` 替换——Skill 正文含 `$ARGUMENTS`，调用传参后注入内容完成替换；无占位符则原样返回（验证：单测）
- AC10：inline 模式——commit Skill 执行结果留在主对话历史（验证：集成测试）
- AC11：fork 模式——review Skill 开独立对话执行，结果摘要回流主对话，token 用量报告可见（验证：集成测试 mock provider）
- AC12：`/skill` 子命令——list / info / reload / load / on / off / unload 各子命令行为符合 F7（验证：handler 单测 + 集成测试）
- AC13：on/off 跨会话持久——`/skill off commit` 后重启，commit 仍禁用；`/skill on commit` 恢复（验证：单测断言 disabled 状态文件读写）
- AC14：压缩预算淘汰——激活多个 Skill 超出 4k token 预算时，最早的被自动踢出 activeSkills（验证：单测 mock 压缩流程）
- AC15：`/clear` 清空 activeSkills——清空后新对话无残留激活 SOP，只重新注入阶段一摘要（验证：集成测试）
- AC16：目录型 Skill——构造含 SKILL.md + tool.json + references/ 的目录，加载后新工具注册进工具面，模型可调用，子进程执行实现脚本（验证：单测 + 集成测试）
- AC17：Skill 嵌套——Skill A 的 SOP 内可调 `load_skill` 激活 Skill B（验证：集成测试）
- AC18：内置三 Skill 内容——commit / review / test 的 SOP 符合 F6（验证：读源文件断言关键指令片段 + fork/inline 模式执行）
- AC19：向后兼容——无 Skill 激活时行为与 ch10 一致，工具全可见（验证：既有测试全绿）
- AC20：热更新 + 容错——修改 Skill 源文件后 `get(name)` 返回新内容；构造一个解析失败场景，`get(name)` 回退旧版并记 warning（验证：单测）
- AC21：名字归一化——构造 `My_Skill.md`，解析后 name 归一化为 `my-skill`，`/my-skill` 命令注册成功（验证：单测）

## 端到端场景

- E2E1：用户说「帮我提交一下这些改动」→ Agent 经 `load_skill` 激活 commit（inline）→ 逐个 add + conventional commit → 完成后对话继续；`/skill list` 显示 commit 已激活
- E2E2：用户说「审查一下这段代码」→ Agent 激活 review（fork）→ 独立对话按五维度审查 → 分级报告摘要回流主对话
- E2E3：用户 `/skill off review` → review 从摘要与可用列表移除、立即失活；重启 MewCode → review 仍禁用
- E2E4：用户自己写了一个 skill（改 `$ARGUMENTS` 替换的模板），编辑源文件后不用重启，再触发即生效（验证：热更新）
