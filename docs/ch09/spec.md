# MewCode ch09 - 项目记忆与会话持久化 Spec

## 背景

MewCode 当前每次启动都是全新会话。ch08 解决了单进程内长时间工作的上下文窗口问题，但进程退出后，对话历史、项目规范、用户偏好和未完成工作都会丢失。本章增加三套相互独立、分层协作的机制：项目指令文件提供静态规范，会话存档提供可恢复的工作记忆，自动笔记提供项目级和用户级长期记忆。

## 目标

- **G1 新会话可理解上下文**：启动时加载项目指令和记忆索引，第一轮请求前完成注入。
- **G2 会话可恢复**：JSONL 只追加写入，崩溃最多丢最后一行；恢复可处理坏行、孤立工具调用和 token 超限。
- **G3 用户可选择恢复**：通过 `/resume` 进入支持搜索和上下键选择的历史会话列表；`/session` 提供会话管理子命令。
- **G4 记忆自动演进**：Agent 自然完成一轮后异步调用 LLM 提取四类长期笔记，不阻塞下一条输入。
- **G5 指令加载受控**：`MEWCODE.md` 支持安全 `@include`，有深度、环路、大小和路径边界限制。
- **G6 低侵入集成**：新增机制接入现有 `PromptBuilder`、`ConversationManager`、`ContextManager`、`Agent` 和 `REPL`，关闭记忆时保持原有行为。
- **G7 磁盘可控**：启动后台清理超过 30 天的新格式会话，旧格式目录不误删。
- **G8 ID 统一**：`YYYYMMDD-HHMMSS-xxxx` 同时用于 ch08 工具结果目录和本章会话目录。

## 范围

### 本章包含

- 三层 `MEWCODE.md` 的发现、优先级拼接和 `@include` 展开。
- `.mewcode/sessions/<session_id>/conversation.jsonl` 会话存档和 `tool-results/` 目录。
- `/resume` 恢复 UI、`/session` 管理命令、JSONL 扫描和 30 天清理。
- 坏行跳过、工具调用配对截断、一次性压缩和时间跨度提醒。
- `.mewcode/memory/`、`~/.mewcode/memory/` 两级记忆、`MEMORY.md` 索引和异步更新。
- Prompt 模块、Conversation 回调和 Agent 生命周期的最小集成。

### 本章不包含

- 向量数据库、RAG、embedding 或本地相似度算法。
- 团队同步、云端会话、多设备冲突合并。
- 启动时自动恢复最近会话；启动永远创建新会话，恢复必须由用户触发。
- 会话合并、会话原文重写、`MEWCODE.md` 热更新。
- 笔记全文搜索和记忆质量评分。
- 独立会话 `meta` 文件；列表概要从 JSONL 扫描计算。

## 非功能需求

- **可靠性**：JSONL 每条记录序列化为单行后追加；追加顺序为序列化、写入、`flush`、`fsync`。崩溃最多留下最后一行不完整数据，恢复必须能跳过该行。
- **性能**：普通 append 为 O(1)；指令展开目标在 200ms 内完成；50 个会话的列表扫描目标在 500ms 内完成。`fsync` 作为可靠性保证，不把 10ms 作为跨平台硬性正确性条件。
- **并发安全**：同一会话 Writer 的追加串行化；记忆更新按作用域加锁，并通过临时文件加原子替换防止索引半写。
- **安全性**：所有用户可控路径规范化后再校验；拒绝绝对路径、`..` 越界和符号链接越界；记忆更新不得把 API key、密码、私钥等明显凭据写入文件。
- **错误隔离**：指令加载、JSONL 写入、恢复、压缩、记忆 LLM 调用和清理的单点失败不得导致主会话未处理异常退出。
- **可诊断性**：坏行、截断、include 拒绝、压缩失败、记忆更新失败和清理失败记录原因；命令反馈展示实际跳过或删除结果。
- **隐私隔离**：项目记忆只在对应项目加载；用户记忆只在当前用户加载；删除/清空前展示作用域和数量并要求确认。
- **向后兼容**：没有指令文件、memory 目录或新格式会话时照常启动；旧格式会话既不展示，也不自动删除。

## 功能需求

### 第一层：项目指令文件

#### F1 三层发现和优先级

启动时按以下顺序扫描，存在且可读才加载：

1. `<project_root>/MEWCODE.md`，项目级，优先级最高。
2. `<project_root>/.mewcode/MEWCODE.md`，项目配置级。
3. `~/.mewcode/MEWCODE.md`，用户级，优先级最低。

按上述顺序拼接，各层之间保留空行；高优先级内容始终在前。缺失文件静默跳过，空文件不产生内容，二进制文件（前 512 字节包含 `\x00`）跳过并记录警告。

#### F2 `@include` 语法

只识别独占一行的 `@include <relative_path>`。路径相对于当前文件所在目录解析；非独占行中的文本保持原样。引用文件完整内容替换 include 行，引用文件可继续 include。

#### F3 include 安全边界

- 项目级文件的允许根边界为 `<project_root>`；用户级文件的允许根边界为 `~/.mewcode/`。
- 规范化绝对路径后必须仍在允许根边界内；拒绝绝对路径、目录穿越、符号链接越界和不存在目标。
- 使用 canonical path 的 `visited` 集合防环；同一文件在一次加载中最多展开一次。
- 最大嵌套深度为 5 层，根 `MEWCODE.md` 算第 1 层。超过深度时保留原 include 行，并追加：`<!-- @include 超过最大嵌套深度，已跳过: <path> -->`。
- 环路时跳过 include 行，并追加：`<!-- @include 检测到环路，已跳过: <path> -->`。
- 越界时跳过 include 行，并追加：`<!-- @include 路径超出允许范围，已跳过: <path> -->`。
- 单文件和展开总大小有硬上限；超限拒绝超出部分并记录来源和原因。

#### F4 Prompt 注入和缓存

- 加载结果注入现有 `PromptBuilder` 的 `custom-instructions` 模块，priority 为 80。
- 项目根、项目 `.mewcode`、用户级内容在同一个模块中按高到低拼接；模块内容不执行 Markdown 中的代码或命令。
- 指令加载在进程启动时执行一次，缓存到模块 content；运行期间不监听文件变化。
- 加载失败时模块为空或包含可诊断警告，不阻塞启动。

### 第二层：会话存档

#### F5 Session ID 和目录

- ID 格式为 `YYYYMMDD-HHMMSS-xxxx`，时间部分取进程启动时的本地时间，`xxxx` 为 4 个随机十六进制字符。
- ID 生成必须处理同秒碰撞；旧格式 `<unix_ts>-<random>` 只保留兼容读取，不参与新建、列表或清理。
- 当前仓库的实际集成入口是 `mewcode/context/session.py` 的 `_new_session_id()`、`SessionContext` 和 `SessionPaths`。
- 目录结构固定为：

```text
<workspace>/.mewcode/sessions/<session_id>/
├── conversation.jsonl
└── tool-results/
```

`SessionContext` 增加 `session_dir`，`spill_dir` 指向 `session_dir/tool-results`；ch08 工具结果和本章 JSONL 共用同一 session ID。

#### F6 JSONL 记录格式

每条消息序列化为一行 JSON，核心字段如下：

```json
{"role":"user","content":"...","ts":1787227200,"model":"model-name"}
{"role":"assistant","content":"...","tool_calls":[{"id":"call_1","name":"read_file","arguments":{"path":"a.py"}}],"ts":1787227201}
{"role":"tool","content":"...","tool_call_id":"call_1","tool_use_id":"call_1","name":"read_file","ts":1787227202}
```

- `role` 和 `ts` 必需；`content` 可选。
- assistant 消息可包含 `tool_calls`；tool 消息保留现有 `tool_call_id`、`tool_use_id` 和 `name`，不得破坏当前 `Message`/Provider 语义。
- 第一条消息可携带 `model`，用于会话列表展示。
- `schema_version` 可选但推荐写入；未知字段和未知事件必须可跳过。
- 若底层 Provider 使用 grouped tool result，可在事件层保存 `tool_results`，恢复时统一转换为现有 `Message`。

#### F7 追加写入和压缩标记

- 当前仓库 `mewcode/conversation/manager.py` 的 `ConversationManager.add_user`、`add_assistant`、`add_assistant_with_tool_calls`、`add_tool_results` 和 `replace_history` 都支持通过可选回调持久化。
- `on_append(message)` 在单条消息加入后调用；`on_replace(messages)` 在整体替换后调用。未设置回调时行为与 ch08 完全一致。
- `replace_history` 被调用时先追加 `{"type":"compact","ts":<unix_ts>}`，再逐条追加压缩后的消息。原始 JSONL 不重写。
- Writer 持有文件句柄和锁（asyncio 或线程锁均可）；append 时保证单行原子顺序，随后 `flush` + `fsync`。
- Writer 提供 `close()`、`__enter__`、`__exit__`；进程退出时关闭句柄。

### 第三层：会话恢复和管理

#### F8 `/resume` 和 `/session`

- `/resume` 仅在 `mewcode/tui/app.py` 的 `SessionState.IDLE` 可用；运行中输入时返回“请等待当前任务完成”，不得发送给 LLM。
- `/resume` 扫描 `.mewcode/sessions/*/conversation.jsonl`，按最后有效消息时间倒序排列。
- 会话列表复用 Textual `OptionList` 或等价选择组件：上下键导航、输入搜索过滤、Enter 选择、Esc 取消；新增 `SessionState.RESUMING`。
- 每项展示：首个 user 消息标题（最多 50 字符）、相对时间、第一条消息的模型标签、JSONL 文件大小。
- `/session list` 展示同一列表；`/session resume <id>` 等价于选择指定会话；`/session new` 创建新会话；`/session path [id]` 定位文件；`/session clean` 清理过期会话；删除命令必须二次确认。

#### F9 恢复流程

1. 逐行读取 JSONL，跳过 JSON/schema 解析失败的行，并记录行号。
2. 从最后一个 `compact` 标记之后构建恢复消息；没有标记则从第一条有效消息开始。
3. 按工具调用 ID 跟踪配对。assistant 的 `tool_calls` 未全部获得后续 tool 结果时，截断到该 assistant 消息之前；孤立 tool 结果不伪造配对。
4. 估算 token。若超过 `context_window - summary_reserve - auto_safety_margin`，先调用一次现有压缩流程；仍超限时按完整消息组丢弃最早可丢弃内容，不拆工具调用对。
5. 若最后有效消息距当前超过 6 小时，在末尾追加 user 消息：`[系统提示] 本会话已暂停 <duration>。部分上下文可能已过时，如需最新信息请重新读取相关文件。`。时间使用 UTC 计算，展示使用本地时区。
6. 重建 `ConversationManager`，重新打开同一 session 的 Writer（追加模式），替换当前 `SessionContext`；后续消息继续追加到原 JSONL。
7. 原来新建但未使用的 session 文件保留，不自动删除。完成后展示：`已恢复会话 <session_id>，共 <N> 条消息`。

#### F10 过期清理

- 启动时后台扫描 `.mewcode/sessions/`，解析新格式 ID 的时间部分；超过 30 天且不是当前活动 session 的目录整体删除，包括 `conversation.jsonl` 和 `tool-results/`。
- 旧格式 ID、无法解析的目录和当前活动目录不删除，也不进入 `/resume` 列表。
- 单个目录删除失败只记录并继续其他目录；清理不阻塞 TUI 启动。

### 第四层：自动记忆

#### F11 记忆类型和存储

四类固定类型为：`user_preference`、`correction_feedback`、`project_knowledge`、`reference_material`。

- 项目级目录：`<workspace>/.mewcode/memory/`，只保存项目知识和参考资料。
- 用户级目录：`~/.mewcode/memory/`，只保存用户偏好和纠正反馈。
- 每条笔记一个 Markdown 文件，文件名为 `<type>_<short_slug>.md`，slug 小写、下划线分隔；文件名和标题必须经过路径安全校验，不能由 LLM 写出目录分隔符。
- 每级一个 `MEMORY.md` 索引，每行格式为 `- [<type>] <title> — <一句话描述>`。
- 每条笔记 frontmatter 至少包含 `type`、`title`、`created`、`updated`、`scope`、`source_session` 和 `status`。
- 每级索引最多 200 行且不超过 25KB。写入端应通过 LLM 合并/淘汰后保持限制；读取端仍需在超过限制时截断到 25KB 并追加 `(index truncated)`，作为防御性兜底。

#### F12 记忆注入

- 启动时和每次成功更新后读取两级 `MEMORY.md`，项目级在前、用户级在后。
- 拼接结果注入 `PromptBuilder` 的 `long-term-memory` 模块，priority 为 100；只注入索引纯文本，不自动注入笔记全文。
- 注入发生在处理第一条请求之前；索引损坏时跳过损坏条目或整个作用域，不阻塞会话。

#### F13 记忆更新触发和输入

- Agent Loop 发出 Done 且最终 assistant 回复没有工具调用，才算自然完成一轮。
- 默认每 5 个完成轮次触发一次；用户消息包含“记住”“记忆”“别忘”“remember”“memo”等显式请求时立即触发；应用退出前再尽力触发一次。
- 更新在独立 asyncio task 中运行，不阻塞下一条输入；同一作用域同时只允许一个更新任务。
- LLM 输入为最近一轮（最后一条 user 到最终 assistant）、两级现有索引和当前笔记摘要；请求不携带工具定义且不允许工具调用。

#### F14 记忆更新协议

LLM 必须返回合法 JSON 数组，空数组表示无需更新：

```json
[
  {"action":"create","level":"project","type":"project_knowledge","title":"API 约定","slug":"api_conventions","content":"..."},
  {"action":"update","level":"user","filename":"user_preference_terse_replies.md","title":"简洁回复","content":"..."},
  {"action":"delete","level":"project","filename":"project_knowledge_old_api.md"}
]
```

- `create` 创建 Markdown 并更新索引；`update` 重写 frontmatter/正文并替换索引行；`delete` 删除笔记并移除索引行。
- `level` 与 `type` 必须符合本 spec 的作用域映射；不允许 LLM 将用户偏好写到项目级或将项目知识写到用户级。
- 去重、合并和是否忽略候选由 LLM 判断，本地不实现相似度算法。
- LLM 错误、JSON 解析失败或文件写入失败只记录日志，不重试当前任务，不影响主会话；下一次触发可重新评估。

### 第五层：生命周期集成

- 启动顺序为：确定 workspace 和新 session ID -> 加载三层指令 -> 初始化两级记忆 -> 后台启动过期清理 -> 创建 Writer/ConversationManager -> 将两个 Prompt 模块传入现有 prompt 组装 -> 接受用户输入。
- 当前实现应接入 `mewcode/prompt/builder.py` 的 `Section/PromptBuilder`，而不是硬编码一个不存在的 `build_system_prompt` 函数；如需增加门面函数，必须保持现有调用兼容。
- `ConversationManager` 的持久化回调与 `ContextManager.replace_history` 兼容；压缩只修改内存窗口并追加 compact 记录，记忆更新只读快照、只写 memory 目录，两者可并发。
- `/resume` 期间不允许新的 Agent run；Agent run 期间不进入恢复选择列表。

## 数据格式

### 会话记录

```json
{"role":"user","content":"请读取 README","ts":1787227200,"model":"claude-sonnet"}
{"role":"assistant","content":"","tool_calls":[{"id":"call_1","name":"read_file","arguments":{"path":"README.md"}}],"ts":1787227201}
{"role":"tool","content":"...","tool_call_id":"call_1","tool_use_id":"call_1","name":"read_file","ts":1787227202}
{"type":"compact","ts":1787227300}
```

### 记忆文件

```markdown
---
type: project_knowledge
title: 测试命令
scope: project
created: 2026-08-20T12:00:00+08:00
updated: 2026-08-20T12:00:00+08:00
source_session: 20260820-120000-a1b2
status: active
---

项目测试命令为 `python -m pytest -q`。
```

## 用户可观察行为

- 新会话启动提示已加载的指令文件数和记忆索引数，但不把它们伪装成用户输入。
- `/resume` 恢复时显示加载中；坏行、工具截断、压缩和时间跨度提醒都能看到实际结果。
- 自动记忆后台执行不改变当前最终回复；成功或失败可通过日志和 `/memory list` 观察。
- `/memory list/show/edit/path/clear` 支持查看、编辑、定位和清空；删除及清空必须明确确认。

## 验收标准

### 项目指令

- **AC1 三层加载**：三份文件同时存在时，`custom-instructions` 内容顺序为项目根、项目 `.mewcode`、用户级；缺失层静默跳过。
- **AC2 include 展开**：独占行的相对 include 被替换，普通段落中的 `@include` 保持原文。
- **AC3 include 安全**：6 层链不展开第 6 层；A -> B -> A 命中环路；`../../etc/passwd`、绝对路径、符号链接越界均被拒绝并追加对应警告。
- **AC4 指令缓存**：启动后修改 `MEWCODE.md` 不改变当前进程已缓存的模块内容。

### 会话存档

- **AC5 ID 和目录**：新 session ID 匹配 `YYYYMMDD-HHMMSS-xxxx`，并创建 `.mewcode/sessions/<id>/conversation.jsonl` 和 `tool-results/`。
- **AC6 JSONL 写入**：一轮 user/assistant/tool 消息各自形成合法 JSON 行；第一条消息可展示模型。
- **AC7 追加和崩溃安全**：已有行不被改写，模拟截断尾行后重新打开时前面的行全部可解析。
- **AC8 compact 标记**：执行上下文压缩时先写 compact 标记，再写压缩后的消息。
- **AC9 回调兼容**：`ConversationManager` 的追加/替换回调次数和参数正确；不设置回调时既有测试不变。

### 会话恢复和清理

- **AC10 `/resume` 路由**：IDLE 输入 `/resume` 不发送给 LLM，进入列表；Esc 返回 IDLE；运行期间输入返回等待提示。
- **AC11 列表和搜索**：3 个有效新格式会话显示标题、相对时间、模型和文件大小；搜索过滤只保留标题匹配项。
- **AC12 坏行跳过**：中间插入非法 JSON 后，其余有效消息仍可恢复。
- **AC13 工具截断**：末尾 assistant 存在未配对 tool call 时，恢复结果停在该 assistant 之前；孤立 tool result 不被伪造配对。
- **AC14 compact 恢复**：存在 compact 标记时只从最后标记后恢复；无标记时从首条有效消息恢复。
- **AC15 超限和时间跨度**：超限先恰好尝试一次压缩；仍超限按完整消息组降级；超过 6 小时追加固定提醒。
- **AC16 追加恢复**：恢复后新消息追加到同一 JSONL，原新 session 文件保留。
- **AC17 过期清理**：31 天前的新格式目录启动后被后台清理；旧格式和当前活动目录保留且不在列表中。

### 自动记忆

- **AC18 记忆创建**：明确表达“回复简洁点”后，mock LLM 返回 create，正确作用域生成带 frontmatter 的 Markdown。
- **AC19 索引更新**：create/update/delete 与 `MEMORY.md` 对应行保持一致，索引不超过 200 行/25KB。
- **AC20 记忆注入**：启动时项目索引在用户索引之前注入 `long-term-memory`；超过 25KB 时出现 `(index truncated)`。
- **AC21 异步不阻塞**：记忆更新未完成时下一条输入立即进入 Agent 主流程。
- **AC22 失败隔离**：mock provider、JSON 解析或磁盘写入失败只记录日志，主会话继续运行。
- **AC23 类型隔离**：四类笔记只能写入规定的用户级/项目级目录，跨项目路径和非法文件名被拒绝。

### 集成

- **AC24 Prompt 模块**：非空指令和记忆分别进入 priority 80 的 `custom-instructions` 与 priority 100 的 `long-term-memory`；空内容不增加模块。
- **AC25 启动顺序**：第一条用户请求处理前已完成指令和记忆索引注入；过期清理在后台运行。
- **AC26 压缩并发**：记忆更新只读会话快照、只写 memory；与 `/compact` 并发不会改坏 conversation。
- **AC27 回归兼容**：现有 Agent Loop、工具调用、上下文压缩、权限和 TUI 测试继续通过。

## 已确认的设计决策

- 不维护独立会话 meta 文件；概要直接扫描 JSONL。
- 会话和项目记忆统一位于 workspace 的 `.mewcode/` 下。
- `/resume` 是恢复快捷命令，`/session` 负责完整会话管理并提供等价恢复入口。
- 会话 ID 使用本地时间生成；JSONL 内的 `ts` 使用 Unix 秒，时间跨度计算使用 UTC。
- 时间跨度提醒阈值为 6 小时，过期清理阈值为 30 天。
- 自动记忆默认每 5 个自然完成轮次触发，显式记忆关键词和退出时额外触发。
- 记忆更新失败不阻塞、不重试当前任务；下一次触发可以重新评估。

