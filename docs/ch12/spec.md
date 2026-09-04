# NewCode ch12 - Hook 生命周期挂钩系统 Spec

## 背景

ch11 为 NewCode 装上了 Skill 技能包系统，Agent 能按需加载预定义的提示词和工具集合。但 NewCode 在「用户怎么扩展行为」这条路径上还差最后一环：**在 Agent 生命周期的固定时刻自动跑一段用户配置的动作**。当前的扩展点都是显式触发——Skill 要 `/name` 唤起、Slash 命令要用户手敲。想做「触发条件明确、动作固定」的重复事，只能每次手动来：

- 写完文件想立刻 `ruff format`，得手动跑或写监听脚本
- 想阻止 Agent 跑 `rm -rf` 之类的命令，权限规则要逐个加 deny
- 想在每轮用户提交前提醒 Agent「记得用 zh-CN」，没现成机制
- 想在 Agent 长跑结束后给自己发个 IM 通知，要自己起进程

ch08 的权限引擎覆盖了「该不该允许工具调用」，但只在工具调用前判定一次、动作仅 Allow/Deny/Ask，做不了命令格式化、上下文注入、外部通知这些副作用。Hook 系统补的是这条缝：在 Agent 生命周期的关键时刻挂自动化动作，把「触发条件明确、动作固定」的重复工作从人工变成机器。

设计上沿用 ch08 已有的权限匹配器做条件表达式底层——但需先把单一通配匹配扩展成「精确 / 反向 / 正则 / glob」四种，让 Hook 条件与权限规则共用同一套匹配语义。

## 目标

- G1：把 Agent 生命周期上的关键时刻抽象成事件，事件 emit 时驱动 Hook 引擎；现有内部事件（工具 Start/End、Compact、Approval）继续走 asyncio 事件流，不受影响
- G2：用户用 YAML 文件声明式配置 Hook 规则，启动期一次性加载并校验，配置错误立即报到 stderr 并跳过出错规则，不阻断进程
- G3：每条 Hook 是「事件 + 条件 + 动作」三要素，条件可省略表示无条件触发
- G4：把 ch08 权限规则的匹配语法从单一通配扩展成「精确 exact / 反向 not / 正则 regex / glob」四种；Hook 条件表达式与扩展后的权限规则共用同一套匹配器
- G5：条件表达式支持嵌套字段访问，多条件用 all_of / any_of 二选一组合，不允许嵌套混用
- G6：两个可拦截事件——`pre_tool_use` 与 `user_prompt_submit`。拦截信号由动作执行结果表达：shell 动作 `exit code 2` 表达拦截、stderr 作为拒绝原因；http 动作响应 `{"decision":"block"}` 表达拦截。被拒原因回灌给 LLM / 回显到对话，形成「拦截 → 收到原因 → 调整策略」的闭环
- G7：四种动作类型——执行 shell 命令、注入提示词、发 HTTP 请求、启动子 Agent（子 Agent 本期占位不实现，等后续章节对接）
- G8：三种执行控制——once（同一会话内只跑一次）、async（异步后台执行不阻塞主流程）、timeout（命令 / HTTP 最大执行时长）；拦截类事件（pre_tool_use / user_prompt_submit）不允许 async，加载期校验出错
- G9：Hook 自身失败（命令非零退出、HTTP 超时、HTTP 解析错等）只记日志、不中断 Agent 主流程——除非该 hook 是同步拦截类且通过动作结果表达拦截信号

## 范围

**包含：**
- 权限匹配器四类型扩展（前置基础，与权限规则共用）
- Hook 规则模型（event / if / action 三要素 + 执行控制）
- 18 个生命周期事件的定义与 payload schema
- 条件表达式：结构化 {field, match} 解析与求值、all_of / any_of 组合
- HookContext 上下文变量与动作模板替换、shell 动作 stdin JSON 传 payload
- 四种执行器：command / prompt / http / agent（agent 占位）
- 执行控制：once / async / timeout
- 两个可拦截事件（pre_tool_use / user_prompt_submit）与拦截闭环
- 三层配置文件加载（本地 > 项目 > 用户，追加合并）+ 加载期解析与校验
- AgentLoop 集成（事件节点插入 Hook 调用 + reminder 队列注入）
- 内置 Slash 命令 `/hooks` 列出已加载规则
- 错误隔离与防重入保护
- 对应单元测试与集成测试

**不包含（留给后续章节或明确不做）：**
- agent 执行器的真实实现（配置合法但执行时记占位日志，留给后续 SubAgent 章节）
- once 标记的跨进程持久化（只做会话内内存标记，重启即重置）
- Hook 执行顺序的显式优先级 / order 字段（加载层按声明顺序自然有序）
- Hook 文件的热更新（加载在启动期一次完成，编辑后需重启）
- Hook 对工具参数或用户消息的改写（可拦截但不可修改，只能放行或拒绝）
- command 执行器输出注入 LLM 上下文（输出只记日志，上下文注入一律走 prompt 动作）
- 事件条件的函数调用、嵌套逻辑表达式（仅 all_of / any_of 一层，不嵌套混用）
- Hook 之间的依赖 / 互斥关系、失败重试、@include 继承、独立日志文件

## 功能需求

### F1 权限匹配器扩展（前置基础）

- F1.1：把权限规则 Pattern 形态从单一字符串扩展成结构化匹配类型 `{type, value}`，type 取 `exact`、`not`、`regex`、`glob` 之一；缺省类型沿用现有 glob 语义，保证向后兼容
- F1.2：规则 YAML 串语法升级——除 `Bash(rm *)` 这种「工具(简洁串)」写法保留代表 glob 类型外，新增显式类型前缀：
  - `Bash(=value)` 精确（整串相等）
  - `Bash(!inner)` 反向（对 inner 取反，inner 自身仍按规则解析，支持 `!=value`、`!~regex`、`!glob`）
  - `Bash(~regex)` 正则
  - `Bash(value)` 不带前缀沿用 glob 语义
- F1.3：精确匹配做整串相等比较；glob 沿用现有 fnmatch 实现；正则在加载期 `re.compile` 并缓存，编译失败按 F1.4 处理；反向是「任意其它类型的取反包装」，支持嵌套（如 `Bash(!=value)`）
- F1.4：扩展后权限引擎的 Allow/Deny 判定语义不变，但规则解析失败由原本的静默跳过改为「stderr 打印失败规则与原因、其余规则正常加载」
- F1.5：现有 ch08 的所有权限测试、既有的 `.newcode/permissions.yaml` 用户配置（仅写 `Bash(git *)` 这种）必须继续工作，不破坏向后兼容

### F2 Hook 规则模型

- F2.1：一条 Hook 由三要素组成：
  - `event`：触发时刻（必填），取值必须是合法事件名（见 F3）
  - `if`：条件表达式（可选），省略表示无条件触发（见 F4）
  - `action`：动作（必填），含 `type` 与各类型独有字段（见 F5）
- F2.2：每条 Hook 可附带执行控制：
  - `once`：布尔，为真时该 Hook 在**同一会话内**首次匹配成功并执行后记入内存集合（key = hook.name），后续相同事件再次匹配时直接跳过；`/clear`、`/resume` 进新会话时集合清空；进程退出不写盘，不做跨进程持久化
  - `async`：布尔，为真时动作在新 asyncio task 中后台执行，不阻塞 Agent 主流程；拦截类事件（pre_tool_use / user_prompt_submit）不允许异步——异步无法表达拦截信号，加载期校验出错并跳过该 hook
  - `timeout`：命令 / HTTP 最大执行时长（如 `30s`，默认 30s），超时按失败处理
- F2.3：多个 Hook 匹配同一事件时，按它们在 YAML 中的出现顺序逐个执行；只要前面任何一个 Hook 表达拦截，后面所有 Hook 都不再执行

### F3 生命周期事件与 payload

- F3.1：共 18 个事件，按类别划分（snake_case 命名）：

| 类别 | 事件 | 触发时刻 | 拦截 |
|------|------|----------|------|
| 会话级 | `session_start` | 会话开始、env context 装配完毕、首条 user 消息进入对话历史之前 | 否 |
| 会话级 | `session_end` | 进程关闭前、`/clear` 关闭旧会话前、`/resume` 切换离开旧会话前 | 否 |
| 会话级 | `session_resume` | `/resume` 选中历史会话、恢复完成、首条 user 消息进入之前 | 否 |
| 轮次级 | `user_prompt_submit` | 用户提交一条非 Slash 命令的 user 消息、写入对话历史之前 | **是** |
| 轮次级 | `turn_start` | 用户发送新消息、一轮对话开始时 | 否 |
| 轮次级 | `turn_end` | Agent 完成回复、一轮对话结束时（run 自然停止、Done emit 之前；取消、出错路径不触发） | 否 |
| 工具级 | `pre_tool_use` | 工具执行之前、权限引擎 check 之前 | **是** |
| 工具级 | `post_tool_use` | 工具拿到 result 之后；被权限 Deny 的也触发（is_error=True） | 否 |
| 消息级 | `pre_send` | 消息发送给 LLM 之前（每轮 stream 前，payload 含对话末尾的 user 消息） | 否 |
| 消息级 | `post_receive` | 收到 LLM 响应之后 | 否 |
| 系统级 | `startup` | NewCode 进程启动时 | 否 |
| 系统级 | `shutdown` | NewCode 进程退出时 | 否 |
| 系统级 | `error` | 发生错误时 | 否 |
| 系统级 | `pre_compact` | 上下文压缩之前（自动 / 紧急 / 手动三路径合并） | 否 |
| 系统级 | `post_compact` | 上下文压缩完成后 | 否 |
| 系统级 | `permission_request` | 权限审批请求弹出时 | 否 |
| 系统级 | `file_change` | 文件被修改时 | 否 |
| 系统级 | `command_execute` | SlashCommand 执行时 | 否 |

- F3.2：`pre_tool_use` 与 `user_prompt_submit` 是仅有的两个可拦截事件；其余事件都是通知型——事件发生后通知 Hook 看是否要做额外处理，不改变主流程结果
- F3.3：`startup`/`shutdown`（进程级）与 `session_start`/`session_end`（会话级）区分：一个进程内可能开启多个会话（如多次进入对话、`/clear`、`/resume`）
- F3.4：每个事件对应一份固定的 payload schema，作为 Hook 条件表达式与动作输入的数据源：

```
# 通用字段（每个事件都有）
event: <事件名>
session_id: <当前会话 ID>
cwd: <项目工作目录>
mode: <permission.Mode 名>

# 事件特化字段
user_prompt_submit / turn_start / pre_send:
  prompt: <用户输入文本>
pre_send:
  last_user_message: <conversation 末尾的 user 消息>
turn_end:
  iter: <本轮 run 走完的迭代数>
pre_tool_use / post_tool_use:
  tool_name: <内部工具名,如 write_file>
  tool_input: <工具参数 dict>
  tool_result: <仅 post_tool_use,工具结果摘要文本>
  is_error: <仅 post_tool_use,bool>
post_receive:
  message: <LLM 响应文本>
error:
  error: <错误信息摘要>
pre_compact / post_compact:
  trigger: auto | emergency | manual
  before_tokens: <int,仅 post_compact>
  after_tokens: <int,仅 post_compact>
permission_request:
  tool_name: <工具名>
  tool_input: <工具参数 dict>
file_change:
  file_path: <被修改的文件路径>
command_execute:
  command: <slash 命令名>
  args: <命令参数>
```

### F4 条件表达式

- F4.1：`if` 是一个对象，顶层只能出现 `all_of` 或 `any_of` 中**一个**——两个同时出现按加载错误处理（跳过该 hook）；`if` 缺省视为无条件触发
- F4.2：`all_of` / `any_of` 的值是一个原子条件数组，每个原子条件包含 `field` 与 `match` 两个字段：

```yaml
if:
  all_of:
    - field: tool_name
      match: { type: exact, value: write_file }
    - field: tool_input.path
      match: { type: glob, value: "**/*.py" }
```

- F4.3：`field` 取 payload 中的字段路径，用 `.` 分隔嵌套（如 `tool_input.command`、`tool_input.path`）；路径不存在按空字符串处理，不报错
- F4.4：`match` 取四种类型之一，对应四种操作符语义（与 F1 的匹配器共用实现）：
  - `{type: exact, value: "..."}` —— 精确（对应 `==`）
  - `{type: glob, value: "..."}` —— glob（对应 `~=`）
  - `{type: regex, value: "..."}` —— 正则（对应 `~`）
  - `{type: not, inner: {...}}` —— 反向（对应 `!=`，inner 自身为任意合法类型，支持嵌套）
- F4.5：正则编译失败、`not` 缺少 `inner`、`inner` 自身非法、未知 type 均视为加载错误，报错并跳过该 hook
- F4.6：条件求值在事件 emit 时实时进行；匹配器实例在加载期一次构造、运行期复用

### F4.5 动作模板变量替换

- F4.7：所有动作的文本模板（command 命令串、prompt 文本、http body、agent prompt）在执行前执行 `{field}` 变量替换，field 取 payload 字段的点分路径（与 F4.3 同语义）。替换规则映射（对应原始 $VAR 语义）：
  - `{event}` ≡ `$EVENT` → 事件名
  - `{tool_name}` ≡ `$TOOL_NAME` → 工具名
  - `{file_path}` ≡ `$FILE_PATH` → 文件路径
  - `{message}` ≡ `$MESSAGE` → 消息内容
  - `{error}` ≡ `$ERROR` → 错误信息
  - `{tool_input.xxx}` ≡ `$TOOL_ARGS.xxx` → 工具参数字典 xxx 字段的字符串表示
- F4.8：模板替换容错——字段不存在替换为空字符串，不报错；裸 `{}` 或非法模板返回原文，绝不抛给调用方（错误隔离）
- F4.9：command 动作取数双通道——主通道是把事件 payload 序列化成单行 JSON 经 stdin 传给命令（脚本侧可用 `jq` 取任意字段，见 F5.2）；`{field}` 内嵌替换默认启用（含裸 `{}` 时按 F4.8 容错返回原文），满足 `$TOOL_ARGS.xxx` 直接内嵌进命令串的场景

### F5 执行器

- F5.1：动作类型 `type` 取 `command` / `prompt` / `http` / `agent` 之一

#### command 动作

- F5.2：`command` 动作字段：`command`（字符串，必填），由 shell 子进程执行（`sh -c`）；执行时把事件 payload 序列化成单行 JSON 通过 stdin 传给命令——脚本侧可用 `jq` 取字段
- F5.3：`timeout` 默认 30 秒，超时终止子进程并返回超时错误，按失败处理；async 时由后台 asyncio task 异步执行，超时同样按失败处理
- F5.4：拦截事件下的 command 同步执行：
  - returncode == 2 视为拦截命中，stderr 或 stdout 合并去尾换行后作为拒绝原因
  - returncode == 0 视为放行
  - 其它非零 returncode 视为 hook 失败但不拦截（记日志、Agent 继续）
- F5.5：command 动作的模板替换注意注入风险——配置者不应把不可信的 payload 内容直接拼进 shell 敏感位置；执行器实现避免不必要的 shell 解释

#### prompt 动作

- F5.6：`prompt` 动作字段：`text`（字符串，必填）；执行时把 `text` 加入「下一次 LLM 请求的 reminder 区」队列——所有 hook 注入的 prompt 按 hook 在 yaml 中的声明顺序拼接，置于现有 plan reminder 之后
- F5.7：reminder 队列仅本轮有效，下一轮重新装配；不入持久对话历史、不影响压缩、不参与 token 估算的历史增长部分（与 plan reminder 同语义）
- F5.8：prompt 动作永不表达拦截——即使位于拦截类事件，动作执行后视为放行，仅做副作用注入

#### http 动作

- F5.9：`http` 动作字段：`url`（必填）、`method`（默认 POST）、`headers`（可选键值对）、`body`（可选字符串模板，支持 `{field}` 取 payload 字段）；缺省 `body` 时把事件 payload 序列化成 JSON 作为请求体
- F5.10：`timeout` 默认 30 秒；async 时由后台 asyncio task 异步执行
- F5.11：拦截事件下的 http 同步执行：
  - 响应 status 2xx 且 body 解析成 `{"decision":"block","reason":"..."}` 时视为拦截命中，reason 作为拒绝原因
  - 其它情况（非 2xx、body 缺 decision 字段、decision 非 block）视为放行
  - 网络错误、超时、JSON 解析失败按 hook 失败但不拦截
- F5.12：http 请求体模板渲染失败按 hook 失败处理；模板只支持最基本的 `{field}` 字段插值，不开放函数调用

#### agent 动作

- F5.13：`agent` 动作字段：`agent_name`（必填）、`prompt`（必填字符串模板）；**本期占位实现**——加载时校验字段完整、执行时仅记一行 stderr 日志 `[hook <name>] agent not yet implemented, skipped`，不报错也不拦截；后续章节对接 SubAgent 系统后再补完整逻辑

### F6 配置加载与校验

- F6.1：Hook 配置从三个位置加载，按顺序 本地临时 > 项目级 > 用户级：
  - 本地临时：`.newcode/config.local.yaml`（与权限规则的 `permissions.local.yaml` 命名对齐，不被 git 追踪，优先级最高）
  - 项目级：项目根目录下的 `.newcode/config.yaml`
  - 用户级：`~/.newcode/config.yaml`
- F6.2：三处文件不存在不报错，找到就加载；Hook 配置是**追加合并**的——所有规则共同参与事件分派，不存在「覆盖同名」概念；合并后同一事件的多个 Hook 按优先级高者在前、同级内按 YAML 出现顺序执行
- F6.3：YAML 顶层结构为 `hooks:` 数组，每条 hook 为对象，字段：`name`（必填，用于日志、once 跟踪、冲突检测）、`event`（必填）、`if`（可选）、`action`（必填）、`once`（可选 bool）、`async`（可选 bool）、`timeout`（可选时长字符串）
- F6.4：三层中出现同名 hook 时，加载期 stderr 提示冲突并跳过后到者（低优先级层）
- F6.5：加载期校验规则：
  - 事件名必须在合法事件列表（F3.1）
  - action.type 必须是 command / prompt / http / agent 四者之一
  - 每种 action 类型检查必填字段（command 有 command、http 有 url、prompt 有 text、agent 有 agent_name 与 prompt）
  - async 不能用在拦截类事件（pre_tool_use / user_prompt_submit）上
  - 条件表达式结构合法（all_of / any_of 二选一、match 类型合法、正则可编译）
- F6.6：任何加载错误（YAML 解析错误、字段缺失、event 未知、name 冲突、async + 拦截事件冲突、regex 编译失败等）一律 stderr 输出明确错误信息并定位到具体是哪个 Hook（文件 + 条目 + 字段），报错后该 Hook 被跳过，其余合法 Hook 正常加载，进程正常启动

### F7 拦截与拒绝闭环

- F7.1：`pre_tool_use` 与 `user_prompt_submit` 两个可拦截事件的处理必须同步执行、等待结果、检查是否拦截
- F7.2：拦截信号由动作执行结果表达（shell exit 2 / http decision:block，见 F5.4 / F5.11），无单独的 reject 字段
- F7.3：多个 Hook 匹配同一拦截事件时按出现顺序执行，任一个表达拦截即拦截，后面 Hook 不执行；拦截原因取该动作返回的拒绝原因
- F7.4：`pre_tool_use` 拦截整合——把 reason 拼成 `[hook <name>] <reason>` 形式当 tool_result 回灌给 LLM，跳过权限引擎与真实工具执行；PhaseStart/PhaseEnd 事件按当前实现继续 emit，PhaseEnd 的 is_error=True
- F7.5：`user_prompt_submit` 拦截整合——阻止该 user 消息写入对话历史，TUI 在输入框下方显示 `[hook <name>] <reason>`，焦点返回输入框等用户重新编辑
- F7.6：Hook 检查在权限审批之前：Hook 拦截 → 直接拦截，不进入权限检查；Hook 放行 → 继续走原有权限审批流程（该弹审批还是弹）
- F7.7：拦截动作的拒绝原因支持变量替换，可包含工具名、工具参数、用户消息等实际值，让 LLM / 用户清楚被拒原因，形成「拦截 → 收到原因 → 调整策略」的闭环

### F8 AgentLoop 集成

- F8.1：AgentLoop 在以下节点插入 Hook 调用（对应 F3.1 的事件触发时刻）：
  - 会话起始 / 结束 / 恢复 → `session_start` / `session_end` / `session_resume`
  - 用户提交（可拦截）、轮次起止 → `user_prompt_submit` / `turn_start` / `turn_end`
  - 消息发送前 / 接收后 → `pre_send` / `post_receive`
  - 工具执行前（同步、可拦截）/ 执行后 → `pre_tool_use` / `post_tool_use`
  - 系统级各节点 → `startup` / `shutdown` / `error` / `pre_compact` / `post_compact` / `permission_request` / `file_change` / `command_execute`
- F8.2：Hook 系统由独立模块承载，内部至少包含规则加载器、引擎（事件分派 + 集合状态）、四类动作执行器、匹配器；Agent 在构造期通过参数注入 Hook 引擎
- F8.3：`injected_prompts` 集合在下一次 LLM 请求时拼到 reminder 串末尾（置于现有 plan reminder 之后）；无拦截语义的事件触发的 prompt 注入同样走 reminder 队列
- F8.4：通知型事件的 Hook 执行结果不影响主流程

### F9 错误隔离与防重入

- F9.1：Hook 自身执行失败（命令非零退出但非拦截信号、HTTP 错误、超时等）只记一行 stderr `[hook <name>] <event> failed: <reason>`，不写日志文件、不弹 UI 通知；async 失败同上、不重试；绝不中断 Agent 主流程
- F9.2：Hook 动作触发的二次事件不产生无限递归（如 file_change 的格式化动作又改文件触发新一轮 file_change）——引擎对同事件重入有防护，Hook 执行期间不重新触发自身
- F9.3：事件分派接口必须支持 asyncio.CancelledError 传播——拦截事件下同步等待、async 后台执行中被取消都应及时退出，避免卡死 Agent.run
- F9.4：拦截事件下的同步 hook 串行执行，以单条 hook 的 timeout 累加；命令自身超时按 F5.3 处理，不设全局上限
- F9.5：async 后台任务为尽力而为：会话结束 / 进程退出时不强制等待完成，但记录未完成的后台任务

### F10 Slash 命令

- F10.1：新增内置 Slash 命令 `/hooks`，零参数：输出当前已加载的所有 hook 的精简列表，按 `event` 分组、每条一行 `  <name>  <event>  <action.type>  <flags>`，flags 含 `[once]` / `[async]` 标志；末尾追加 `Loaded from: <加载来源文件列表>`
- F10.2：无任何 hook 时输出 `No hooks loaded.`

## 非功能需求

- N1：**错误隔离**——任何 Hook 执行失败、配置非法、执行器报错，都不影响 Agent 主流程的正常运行（对应 F9.1、F6.6）
- N2：**加载不阻断**——所有加载错误一律 stderr 输出后继续启动，不阻断 newcode 进程（对应 F6.6）
- N3：**配置可诊断**——非法配置的错误信息必须定位到具体 Hook（文件 + 条目 + 字段），不能只报「配置错误」
- N4：**防卡死**——事件分派支持 CancelledError 传播，拦截同步等待、async 后台执行被取消时及时退出（对应 F9.3）
- N5：**payload 稳定序列化**——Hook payload JSON 序列化 `sort_keys=True` 保持字段顺序稳定，方便用户脚本直接 grep
- N6：**匹配器共用**——扩展后的匹配器对权限规则与 Hook 条件共用同一实现，单元测试覆盖四种 type × 边界条件（空串、转义、嵌套 not、空 path）
- N7：**reminder 无侵入**——注入的 reminder 文本不入序列化对话历史、不参与 token 估算的历史增长部分（与 plan reminder 同语义，对应 F5.7）
- N8：**once 内存集合生命周期**——放在会话运行时状态上，与 ActiveSkills 同生命周期；`/clear` 与 `/resume` 切换时清空（对应 F2.2）
- N9：**占位日志可搜索**——agent 占位日志输出固定格式 `[hook <name>] agent not yet implemented, skipped`，方便后续章节对接时文本搜索替换
- N10：**无侵入**——未配置任何 Hook 时，AgentLoop 的开销接近于零（空引擎短路），不改变现有行为
- N11：**文档保护**——docs/ 不可变；ch12 四份文档走 new-spec 流程生成，逐份经用户审批
- N12：**版本号**——本章完成后 bump 到 0.12.0，`newcode/__init__.py` 与 `pyproject.toml` 同步更新
- N13：**测试规范**——接线测试自动跑、不依赖真实终端；mock 驱动真实代码路径；每个测试标注它防的 bug

## 验收标准

- AC1（F1.2）：写一份只含 `Bash(=git status)` 的精确规则到 `.newcode/permissions.yaml`，启动后调用 `git status` 被该规则命中、调用 `git status -s` 不命中
- AC2（F1.2）：写一份 `Bash(~^npm (install|test)$)` 的正则规则，启动后调用 `npm install` 命中、`npm run dev` 不命中；写法非法（如未闭合括号）启动期 stderr 打印失败规则与原因并跳过该条
- AC3（F1.2）：写一份 `Bash(!~^rm)` 的反向正则规则，调用 `rm -rf .` 不命中（以 rm 起头）、调用 `ls -lh` 命中（不以 rm 起头）
- AC4（F1.5）：现有 ch08 权限测试全部通过，既有 `Bash(git *)` 风格配置行为不变
- AC5（F5.4）：在 `<projectRoot>/.newcode/config.yaml` 写一条 pre_tool_use hook——条件 `tool_name = write_file`，动作 `command: "echo blocked >&2; exit 2"`；启动后 LLM 调用 write_file 工具时被拦截，tool_result 显示 `[hook <name>] blocked`，文件未被写入
- AC6（F5.4）：上面 AC5 的 hook 把动作命令改成 `exit 0`，再调用 write_file，hook 触发但放行，文件成功写入
- AC7（F7.4）：AC5 拦截发生时，权限引擎未被调用（不弹审批），PhaseEnd 的 is_error=True
- AC8（F5.6）：写一条 session_start hook——动作 `prompt: "用 zh-CN 回复"`；重启 newcode 后首轮对话中 LLM reminder 区能看到该文本，后续轮不再注入
- AC9（F2.2）：写一条 once + turn_start 的 hook，动作 `command: "echo first-turn >&2"`；第一轮 turn_start 时 stderr 出现 `first-turn`，后续轮不再出现；执行 `/clear` 进入新会话后下一轮再次出现
- AC10（F2.2/F6.5）：写一条 async + pre_tool_use 的 hook，启动 newcode 时 stderr 打印 `hook "<name>": async not allowed for blocking events, skipped` 并跳过该条，其余 hook 正常加载
- AC11（F7.5）：写一条 user_prompt_submit hook——条件 prompt 正则匹配 `(?i)delete`，动作 `command: "echo \"prompt contains delete keyword\" >&2; exit 2"`；用户在 TUI 输入「请帮我 delete 那个文件」时被拦截，输入框下方提示 `[hook <name>] prompt contains delete keyword`，消息未进入对话历史
- AC12（F6.6）：在 hooks 配置中写 `event: UnknownEvent`，启动后 stderr 打印 `hook "<name>": unknown event "UnknownEvent", skipped`，其余 hook 正常加载
- AC13（F6.4）：同时在用户级与项目级配置各写一条同名 hook，启动后 stderr 提示冲突并保留高优先级层那条；`/hooks` 命令输出合并列表，末尾显示两个加载来源文件路径
- AC14（F5.9）：写一条 turn_end hook——动作 `http: POST http://localhost:9999/done`；本地起一个 echo server，Agent 完成一轮回复后该 server 收到一次 POST 请求且 body 含 `"event":"turn_end"`
- AC15（F5.11）：写一条 pre_tool_use hook——动作 `http: POST http://localhost:9999/check`；本地 server 对 Bash 工具返回 `{"decision":"block","reason":"network policy"}`，Bash 调用被拦截、其它工具不受影响
- AC16（F5.13）：写一条 session_start hook——动作 `agent: agent_name=foo, prompt=test`；启动后 stderr 出现 `[hook <name>] agent not yet implemented, skipped`，Agent 主流程不受影响
- AC17（F4.1）：在 hook 的 `if` 中同时写 `all_of` 与 `any_of` 两个键，启动 stderr 报错跳过该条，其余 hook 加载正常
- AC18（F4.4）：field 点分路径不存在时（如 `tool_input.path` 在非写文件事件上）按空字符串求值，不报错
- AC19a（F4.7/F4.8）：动作模板中的 `{field}` 被替换为 payload 实际值（如 `{tool_input.path}` 替换为写文件路径）；未知字段替换为空串、裸 `{}` 返回原文，不报错
- AC19（F10.1）：`/hooks` 命令输出按 event 分组、每条一行含 name/event/action.type/flags，末尾有 Loaded from 列表
- AC20（F9.2）：file_change 的格式化动作不会导致无限递归循环（格式化再次触发 file_change 时不重入）
- AC21（N10）：未配置任何 Hook 时，Agent 主流程行为与未装 Hook 系统前一致
- AC22（N4/F9.3）：拦截事件同步执行 + 用户取消时，Agent.run 正常退出不卡死

## 端到端场景（验收参考）

- 场景 1（自动格式化）：配置 post_tool_use hook——条件 `tool_name = write_file` 且 `is_error = false`，动作 `command: ruff format $(jq -r .tool_input.path)`、async、timeout=5s；LLM 写一个 Python 文件后 ruff 异步在后台执行，主对话流不暂停；命令失败时 stderr 打印失败日志、Agent 不中断
- 场景 2（危险命令拦截）：配置 pre_tool_use hook——条件 `tool_input.command` glob 匹配 `rm -rf *`，动作 `command: "echo \"dangerous: rm -rf\" >&2; exit 2"`；观察工具被拦截、拒绝原因反馈给 LLM、权限引擎未被调用
- 场景 3（上下文注入）：配置 turn_start hook 注入「请先读 ARCHITECTURE.md」prompt；观察 Agent 下一轮请求前 reminder 区出现该文本，对话历史结构不变
- 场景 4（防递归）：file_change hook 格式化文件，格式化再次触发 file_change；观察不会无限循环
