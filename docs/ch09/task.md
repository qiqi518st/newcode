# MewCode ch09 - 项目记忆与会话持久化 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | mewcode/instructions/__init__.py | 指令加载包导出 |
| 新建 | mewcode/instructions/loader.py | 三层 MEWCODE.md、include、安全边界 |
| 新建 | mewcode/session/__init__.py | 会话 API 导出 |
| 新建 | mewcode/session/writer.py | JSONL Writer、Entry 序列化、compact |
| 新建 | mewcode/session/archive.py | 会话概要扫描、路径校验、过期清理 |
| 新建 | mewcode/session/recovery.py | JSONL 恢复、工具配对、超限降级 |
| 新建 | mewcode/session/runtime.py | 当前会话、Writer 和恢复切换 |
| 新建 | mewcode/memory/__init__.py | 记忆 API 导出 |
| 新建 | mewcode/memory/models.py | NoteType、MemoryNote、MemoryOperation |
| 新建 | mewcode/memory/store.py | 单级笔记文件和 MEMORY.md |
| 新建 | mewcode/memory/prompts.py | 无工具记忆更新 prompt 和 JSON schema 约束 |
| 新建 | mewcode/memory/manager.py | 两级索引、LLM 更新、异步任务 |
| 修改 | mewcode/context/session.py | 新 session ID、session_dir、conversation_path |
| 修改 | mewcode/conversation/manager.py | on_append/on_replace 回调、恢复构造 |
| 修改 | mewcode/prompt/builder.py | custom-instructions 和 long-term-memory section |
| 修改 | mewcode/agent/agent.py | 自然 Done、关键词、每 5 轮记忆触发 |
| 修改 | mewcode/main.py | 启动装配和退出清理 |
| 修改 | mewcode/tui/app.py | /resume、/session、/memory、RESUMING |
| 新建 | tests/test_instructions_loader.py | 指令加载测试 |
| 新建 | tests/test_context_session.py | session ID、目录和兼容边界测试 |
| 修改 | tests/test_builder.py | PromptBuilder 新 section 测试 |
| 修改 | tests/test_conversation_manager.py | 持久化回调测试 |
| 新建 | tests/test_session_writer.py | JSONL 追加测试 |
| 新建 | tests/test_session_archive.py | 列表和清理测试 |
| 新建 | tests/test_session_recovery.py | 恢复容错测试 |
| 新建 | tests/test_memory_manager.py | 记忆 CRUD 和异步更新测试 |
| 新建 | tests/test_tui_resume.py | TUI 命令和状态测试 |
| 新建 | tests/test_ch09_integration.py | 启动、Agent、Prompt 和回归测试 |

## T1：稳定 session ID 和目录契约

**文件：** mewcode/context/session.py、tests/test_context_session.py

**依赖：** 无

**步骤：**

1. 在模块初始化时捕获一次进程启动时间；将 _new_session_id 默认改为使用该时间生成 YYYYMMDD-HHMMSS 加 4 位随机十六进制后缀，测试可注入时间。
2. 增加 parse_session_time，严格解析新格式；旧格式返回 None 或专用错误。
3. 扩展 SessionContext 的 session_dir 和 conversation_path 字段。
4. 让 new_session_context 创建 session_dir、tool-results 和 conversation.jsonl 所需父目录。
5. 保持 SessionPaths.path_for 仍在 tool-results 下生成路径。
6. 增加 open_session_context(workspace, session_id)，只接受安全的新格式 ID。

**验证：** python -m pytest tests/test_context_session.py -q；运行测试应覆盖 ID 正则、目录结构、旧 ID 拒绝和路径不越界。

## T2：实现 InstructionLoader

**文件：** mewcode/instructions/loader.py、mewcode/instructions/__init__.py、tests/test_instructions_loader.py

**依赖：** 无

**步骤：**

1. 定义 InstructionLoader(project_root, user_home, max_depth=5) 和 InstructionDocument。
2. 按项目根、项目 .mewcode、用户 .mewcode 顺序发现 MEWCODE.md。
3. 实现独占行 include 解析，非独占文本保持原样。
4. 使用 canonical path、visited 集合和递归深度处理环路、重复和深度上限。
5. 使用 Path.resolve 和 is_relative_to 校验项目/用户根边界，拒绝目录穿越和符号链接越界。
6. 加入缺失、空、二进制、UTF-8 和文件/总展开大小处理。
7. 返回拼接文本、来源文档和诊断信息；缺失层不阻塞加载。

**验证：** python -m pytest tests/test_instructions_loader.py -q；覆盖三层顺序、include、深度 5、环路、越界、二进制和缓存输入。

## T3：接入 PromptBuilder 模块

**文件：** mewcode/prompt/builder.py、mewcode/prompt/__init__.py、tests/test_builder.py

**依赖：** T2

**步骤：**

1. 保持 Section 和 PromptBuilder 现有排序、同优先级稳定顺序。
2. 增加构造/替换 custom-instructions(priority 80) 和 long-term-memory(priority 100) 的窄辅助函数。
3. 空文本不注册模块；非空文本只生成一个对应模块。
4. 确认模块更新只发生在下一次请求组装边界，不在流式请求中途修改。
5. 保持现有 builder 调用方和 prompt cache 行为。

**验证：** python -m pytest tests/test_builder.py tests/test_assembler.py -q；新增空/非空、优先级和更新边界断言。

## T4：定义 Entry 和 Message 序列化

**文件：** mewcode/session/writer.py、mewcode/provider/base.py（必要时仅新增适配函数）、tests/test_session_writer.py

**依赖：** T1

**步骤：**

1. 定义 Entry 字段：role、content、tool_calls、tool_results、tool_call_id、tool_use_id、name、ts、model、type。
2. 实现 Message 到 Entry 的序列化，第一条消息才写 model。
3. 实现 Entry 到 Message 的反序列化，严格检查 role、工具字段和 compact 事件。
4. 保留当前 Anthropic/OpenAI tool ID 语义；grouped tool_results 仅作为兼容事件。
5. 未知字段/未知事件可跳过，非法 schema 返回诊断而不是伪造消息。

**验证：** python -m pytest tests/test_session_writer.py -q；使用现有 Message、ToolCall、ToolResult fixture 往返序列化。

## T5：实现 SessionWriter

**文件：** mewcode/session/writer.py、tests/test_session_writer.py

**依赖：** T1、T4

**步骤：**

1. 实现 SessionWriter(session_dir) 和 open_existing(session_dir)，以追加模式打开 conversation.jsonl。
2. 在 threading.Lock 内完成 json.dumps、单次 write、flush 和 fsync。
3. 实现 append、append_message、append_all、write_compact_marker、append_event。
4. 保证 compact marker -> 压缩消息的顺序；close 幂等并实现上下文管理器。
5. 写入失败抛出可诊断异常，不删除既有 JSONL。
6. 确认同一 Writer 的并发追加不会交错行。

**验证：** python -m pytest tests/test_session_writer.py -q；mock fsync，验证崩溃尾行、锁、追加不重写和 close。

## T6：给 ConversationManager 增加持久化回调 API

**文件：** mewcode/conversation/manager.py、tests/test_conversation_manager.py

**依赖：** T1

**步骤：**

1. 构造器增加可选 on_append 和 on_replace。
2. add_user、add_assistant、add_assistant_with_tool_calls、add_tool_result(s) 成功追加后调用 on_append。
3. replace_history 复制新列表后调用 on_replace。
4. 增加 from_messages 或等价恢复构造，恢复初始消息时不重复写入 Writer。
5. 回调异常记录并隔离，不回滚已在内存中的消息。
6. 确认 _trim 只影响内存窗口，不改变回调顺序；Writer 的具体绑定由 T9 完成。

**验证：** python -m pytest tests/test_conversation_manager.py -q；检查每个追加入口的回调次数、参数副本和未设置回调时的兼容性。

## T7：实现 SessionArchive 和列表概要

**文件：** mewcode/session/archive.py、mewcode/session/__init__.py、tests/test_session_archive.py

**依赖：** T1、T4

**步骤：**

1. 实现新格式 session 目录扫描，只接受包含 conversation.jsonl 的目录。
2. 实现 parse_session_id 和旧格式过滤。
3. 流式扫描 JSONL，计算首个 user 标题、模型、消息数、首末有效 ts、文件大小和 diagnostics。
4. 以最后有效消息 ts 作为 modified_at，文件 mtime 只作无有效消息时的后备。
5. 导出 list_sessions 函数式门面和 SessionArchive 类。
6. 所有 session_id 输入只能解析为当前 sessions 根目录下的目录，拒绝绝对路径和跨项目访问。

**验证：** python -m pytest tests/test_session_archive.py -q；构造有效、坏行、空文件、旧格式和跨目录 fixture。

## T8：实现 SessionRecovery

**文件：** mewcode/session/recovery.py、tests/test_session_recovery.py

**依赖：** T4、T7、现有 mewcode/context/manager.py

**步骤：**

1. 从最后一个 compact 事件之后读取有效 Entry；没有 compact 时从首条有效记录开始。
2. 按 tool_call_id/tool_use_id 建立待配对集合，多个工具必须全部闭合。
3. 缺少 tool 结果时截断到未完成 assistant 之前；孤立 tool result 跳过并记录诊断。
4. 使用现有 token 估算接口判断恢复后的上下文大小。
5. 超限时调用恢复消息副本的压缩窄接口，最多一次；失败后用 MessageGroupDropper 按完整 user 组降级。
6. 最后有效 ts 距当前超过 6 小时追加固定时间跨度提醒。
7. 返回 messages、截断原因、跳过行、是否压缩和时间提醒等 RecoveryResult。

**验证：** python -m pytest tests/test_session_recovery.py -q；覆盖尾行/中间坏行、多工具调用、compact、压缩失败和 6 小时提醒。

## T9：实现 SessionRuntime 和恢复切换

**文件：** mewcode/session/runtime.py、mewcode/main.py、tests/test_ch09_integration.py

**依赖：** T5、T6、T8

**步骤：**

1. 定义 Runtime 持有当前 SessionContext、ConversationManager、SessionWriter 和后台任务句柄。
2. 实现 create_new(workspace) 创建新会话并绑定 Writer 回调。
3. 实现 resume(session_id)，先恢复副本，再关闭旧 Writer，最后切换 Conversation/Context/Writer。
4. 恢复初始消息不得重新追加；切换后的新消息必须追加到被恢复 JSONL。
5. 旧新 session 文件保留；当前活动 session 切换期间禁止并发 Agent.run。

**验证：** python -m pytest tests/test_ch09_integration.py -q；验证新建、恢复、追加同一文件和切换失败回滚。

## T10：实现 MemoryNote、MemoryOperation 和 MemoryStore

**文件：** mewcode/memory/models.py、mewcode/memory/store.py、mewcode/memory/__init__.py、tests/test_memory_manager.py

**依赖：** T1

**步骤：**

1. 定义 NoteType 四类枚举、MemoryNote、MemoryOperation 和 type-to-scope 白名单。
2. 实现 MemoryStore(directory) 的 ensure_dir、load_index、list_notes、apply、clear。
3. 解析和生成 frontmatter，确保 type、title、scope、created、updated、source_session、status 完整。
4. 校验 filename/slug 只能是当前目录下的安全文件名，拒绝目录分隔符和路径穿越。
5. create/update/delete 使用作用域锁；笔记文件和 MEMORY.md 使用临时文件 + os.replace。
6. 应用操作后检查 200 行/25KB 限制；超限保留旧状态并返回诊断。

**验证：** python -m pytest tests/test_memory_manager.py -q；覆盖四类笔记、frontmatter、路径注入、原子替换、清空确认所需的计数。

## T11：实现 MemoryManager 和记忆更新请求

**文件：** mewcode/memory/manager.py、mewcode/memory/prompts.py、tests/test_memory_manager.py

**依赖：** T10

**步骤：**

1. MemoryManager 接收 project_dir、user_dir、可选 provider 和 model；provider 未选定时允许 load_indexes。
2. 实现 set_provider，确保使用当前主会话 provider。
3. load_indexes 拼接项目级在前、用户级在后的 MEMORY.md，并在 25KB 处防御性截断。
4. 构造无工具记忆更新 prompt，输入最近一轮消息、两级索引和固定四类输出 schema。
5. 严格解析 JSON 数组，校验 action、level、type、filename、slug 和正文大小。
6. 通过对应 MemoryStore 应用 create/update/delete；失败保留旧文件和旧索引。
7. update_async 使用每作用域一个任务锁；当前任务失败只记录日志，不阻塞、不重试。

**验证：** python -m pytest tests/test_memory_manager.py -q；mock provider 覆盖空数组、创建、更新、删除、非法 JSON、provider 错误和并发更新。

## T12：接入 Prompt 与启动加载

**文件：** mewcode/main.py、mewcode/prompt/builder.py、tests/test_ch09_integration.py

**依赖：** T2、T3、T9、T11

**步骤：**

1. 启动确定 workspace 和 user_home，创建 InstructionLoader(workspace, user_home) 并加载文本。
2. 创建 MemoryManager，加载两级 MEMORY.md；provider 选定后调用 set_provider。
3. 创建 priority 80/100 的两个 section，并传入现有 prompt 组装路径。
4. 确认启动永远创建新 session，不自动恢复历史。
5. 在启动阶段创建后台清理任务：使用 asyncio.to_thread 调用同步 clean_expired，并传入当前 active_session_id 以保护活动会话。
6. 退出顺序为停止接受输入、关闭 Writer、有限等待记忆任务、取消超时任务。

**验证：** python -m pytest tests/test_ch09_integration.py -q；检查启动顺序、空文件降级、section 排序和退出清理。

## T13：接入 Agent 自动记忆触发

**文件：** mewcode/agent/agent.py、mewcode/session/runtime.py、tests/test_ch09_integration.py

**依赖：** T9、T11、T12

**步骤：**

1. 只在最终 assistant 已写入、无工具调用且发出 DONE 时计为自然完成轮次。
2. 每 5 轮触发一次；检测中文和英文显式记忆关键词时立即触发。
3. 复制最后一条 user 到最终 assistant 的消息快照，携带 session_id 调用 update_async。
4. 使用任务句柄避免同一作用域并发记忆更新；下一轮输入不等待。
5. 取消、异常和达到最大轮数不误触发自然完成。
6. 应用退出时额外尽力触发一次，设置有限超时。

**验证：** python -m pytest tests/test_ch09_integration.py -q；mock 延迟 provider，验证下一条消息不等待、关键词触发和退出超时。

## T14：实现 TUI /resume 状态和会话命令

**文件：** mewcode/tui/app.py、tests/test_tui_resume.py

**依赖：** T7、T9、T12

**步骤：**

1. 增加 SessionState.RESUMING 和 builtin /resume 路由。
2. IDLE 时进入列表；STREAMING/APPROVING 时返回等待提示，不发送给 LLM。
3. 使用 OptionList 或现有等价组件展示标题、相对时间、模型和文件大小。
4. 增加搜索输入、上下键、Enter、Esc；搜索只匹配标题。
5. 接入 /session list、resume、new、path、clean，并对删除/清理结果提供确认。
6. Enter 后调用 Runtime.resume，展示恢复消息和诊断，成功回到 IDLE。

**验证：** python -m pytest tests/test_tui_resume.py -q；覆盖 IDLE/STREAMING/RESUMING、搜索、Esc、恢复互斥和无会话列表。

## T15：实现 TUI /memory 命令

**文件：** mewcode/tui/app.py、mewcode/memory/manager.py、tests/test_tui_resume.py、tests/test_memory_manager.py

**依赖：** T10、T11、T12

**步骤：**

1. 注册 /memory list、show、edit、path、clear。
2. list 默认只展示 ID、类型、更新时间和摘要，不打印完整正文。
3. show/edit/path 严格校验 scope 和 filename，只允许 user/project memory 根目录。
4. clear 执行前显示作用域和数量，要求明确确认；all 需要再次确认。
5. 编辑后刷新内存索引缓存，使下一次请求加载新索引。

**验证：** python -m pytest tests/test_tui_resume.py tests/test_memory_manager.py -q；覆盖非法参数、跨目录路径、取消确认和索引刷新。

## T16：补齐清理、诊断和兼容边界

**文件：** mewcode/session/archive.py、mewcode/session/runtime.py、mewcode/main.py、tests/test_session_archive.py

**依赖：** T7、T9、T12

**步骤：**

1. clean_expired 只处理可解析的新格式 ID，旧格式和非法目录跳过，并接受 active_session_id 保护当前会话。
2. 排除当前活动 session；单目录删除失败继续处理其他目录。
3. 所有恢复、加载、清理、写入错误转换为诊断信息或日志，不泄漏敏感内容。
4. 确认没有 meta 文件创建逻辑，概要始终从 JSONL 计算。
5. 为 Windows 路径、符号链接和文件权限错误补充降级测试。

**验证：** python -m pytest tests/test_session_archive.py -q；构造 31 天目录、旧格式目录、当前目录和权限失败 fixture。

## T17：运行 ch09 单元和集成测试

**文件：** tests/test_instructions_loader.py、tests/test_session_writer.py、tests/test_session_archive.py、tests/test_session_recovery.py、tests/test_memory_manager.py、tests/test_tui_resume.py、tests/test_ch09_integration.py

**依赖：** T2-T16

**步骤：**

1. 运行所有 ch09 专项测试，修复跨模块契约不一致。
2. 运行现有 conversation、context、agent、prompt 和 TUI 测试。
3. 检查旧 session ID 不展示、不清理；关闭记忆时旧流程不变。
4. 检查测试 fixture 不写入真实用户目录，不依赖 API key、网络或真实终端。
5. 记录无法执行的验证及替代证据，不把未验证项目标记为通过。

**验证：** python -m pytest tests/test_instructions_loader.py tests/test_session_writer.py tests/test_session_archive.py tests/test_session_recovery.py tests/test_memory_manager.py tests/test_tui_resume.py tests/test_ch09_integration.py -q。

## T18：全量回归和静态检查

**文件：** 全部 Python 源码和 tests/

**依赖：** T17

**步骤：**

1. 运行完整 pytest 测试集，确认 ch01-ch08 既有行为没有回归。
2. 运行项目已有的 ruff check 配置，修复本章新增代码的 lint 问题。
3. 如项目已配置类型检查，再运行类型检查并记录实际结果；未配置时不新增工具依赖。
4. 若测试或检查失败，回到对应任务修复后重新执行。

**验证：** python -m pytest -q；ruff check .；每条命令都记录实际输出。

## T19：环境隔离和文档保护检查

**文件：** docs/ch09/spec.md、docs/ch09/plan.md、docs/ch09/task.md、工作区状态

**依赖：** T18

**步骤：**

1. 确认测试没有写入真实用户 memory、sessions 或 home 目录。
2. 确认 docs/ 下只有本章四份流程文档发生预期变更。
3. 确认旧格式 session 目录未被清理，当前活动 session 未被删除。
4. 确认没有生成独立会话 meta 文件。
5. 汇总无法验证的真实终端、API key 或权限场景，列入 checklist 的待人工验证项。

**验证：** git status --short；对 docs/ 运行变更检查；检查临时 workspace/home 目录为空或只包含测试产物。

## 执行顺序

    T1
     ├── T4 -> T5 -> T6
     └── T10 -> T11
    T2 -> T3
    T5 + T6 + T7 -> T8 -> T9
    T2 + T3 + T9 + T11 -> T12
    T9 + T11 + T12 -> T13
    T7 + T9 + T12 -> T14
    T10 + T11 + T12 -> T15
    T7 + T9 + T12 -> T16
    T2-T16 -> T17 -> T18 -> T19

## 任务规则

- 每个任务完成后先执行其验证命令，再标记完成。
- 不修改 docs/ 中除本章生成文件外的既有文档；发现验证工具改动 docs/ 时立即停止并报告。
- 不在 T19 环境隔离、文档保护和回归检查完成前声称本章实现完成。
- 任一任务遇到环境限制时记录原因、风险和替代验证，不静默跳过。
