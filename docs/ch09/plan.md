# NewCode ch09 - 项目记忆与会话持久化技术设计

## 架构概览

本章把启动加载、会话存档、恢复选择和长期记忆拆成四个窄模块，并通过现有的 PromptBuilder、ConversationManager、Agent 和 REPL 连接起来。会话原文与长期记忆不互相改写：会话 Writer 只追加 JSONL，MemoryManager 只写两个 memory 目录。

    main.py
      |
      +-- InstructionLoader ----------------> PromptBuilder
      |       (三层 NEWCODE.md + include)      custom-instructions (80)
      |
      +-- MemoryManager ---------------------> PromptBuilder
      |       (MEMORY.md + note files)          long-term-memory (100)
      |
      +-- SessionRuntime
      |       +-- SessionWriter <------------ ConversationManager callbacks
      |       +-- SessionArchive ------------> .newcode/sessions/<id>/conversation.jsonl
      |       +-- SessionRecovery ------------> ConversationManager + ContextManager
      |
      +-- Agent.run -------------------------> MemoryManager.update_async()
      +-- REPL (/resume, /session, /memory) -> SessionArchive / MemoryManager

启动只创建新 session，不自动恢复历史。指令和记忆索引在第一条用户请求前加入稳定提示；/resume 选择后才替换 ConversationManager 和 SessionRuntime。

## 组件划分

### 新增模块

| 模块 | 文件 | 职责 | 依赖 |
|---|---|---|---|
| 指令加载 | newcode/instructions/loader.py | 发现三层 NEWCODE.md、展开 include、校验路径/大小 | pathlib、re、os |
| 记忆模型 | newcode/memory/models.py | 定义记忆类型、frontmatter、更新操作和索引条目 | dataclasses、datetime |
| 记忆存储 | newcode/memory/store.py | 管理单级笔记文件、MEMORY.md 和作用域锁 | pathlib、threading、models.py |
| 记忆管理 | newcode/memory/manager.py | 编排两级 memory、索引拼接、结构化 LLM 更新 | Provider、store.py |
| 会话 Writer | newcode/session/writer.py | JSONL 追加、compact 记录、锁、flush/fsync、close | Message、SessionContext |
| 会话存档 | newcode/session/archive.py | 会话扫描、概要计算、ID 校验、30 天清理 | pathlib、json |
| 会话恢复 | newcode/session/recovery.py | 坏行、compact 起点、工具配对、token 和时间降级 | Message、ContextManager |
| 会话门面 | newcode/session/runtime.py | 绑定当前 session、切换 Writer/Conversation、关闭后台任务 | writer、archive、recovery |

### 现有模块修改

| 文件 | 修改职责 |
|---|---|
| newcode/context/session.py | 将 _new_session_id 改为 YYYYMMDD-HHMMSS-xxxx；SessionContext 增加 session_dir 和 conversation_path；保留 spill_dir 兼容 ch08 |
| newcode/conversation/manager.py | 构造器增加可选 on_append、on_replace；追加和 replace_history 触发回调 |
| newcode/prompt/builder.py | 保持 Section/PromptBuilder API；增加按名称替换或构造两个新 section 的窄辅助函数 |
| newcode/agent/agent.py | 自然 Done 后发出可选 on_turn_complete；异常、取消和最大轮数不误记为自然完成 |
| newcode/tui/app.py | 增加 RESUMING 状态、/resume、/session、/memory 及 OptionList/搜索/确认交互 |
| newcode/main.py | 启动装配 InstructionLoader、MemoryManager、SessionRuntime 和后台清理 |
| newcode/context/manager.py | 保持 replace_history 压缩路径，由 Conversation 回调追加 compact 和消息 |
| newcode/provider/base.py | 不改变 Message、ToolCall、ToolResult；增加持久化边界的序列化/反序列化适配 |

### 测试模块

新增 tests/test_instructions_loader.py、test_memory_manager.py、test_session_writer.py、test_session_archive.py、test_session_recovery.py、test_tui_resume.py 和 test_ch09_integration.py。所有 LLM 路径使用 mock Provider，不依赖 API key、真实终端或网络。

### 文件组织

仓库使用根目录下的 newcode 包，不引入 src/ 前缀：

    newcode/
    ├── instructions/
    │   ├── __init__.py
    │   └── loader.py
    ├── session/
    │   ├── __init__.py
    │   ├── writer.py
    │   ├── archive.py
    │   ├── recovery.py
    │   └── runtime.py
    └── memory/
        ├── __init__.py
        ├── models.py
        ├── store.py
        └── manager.py

instructions 包只负责静态指令安全加载；memory 包只负责笔记 CRUD 和索引，不能通过交叉导入形成循环依赖。

## 核心数据结构

### SessionContext

    @dataclass
    class SessionContext:
        session_id: str
        session_dir: str
        spill_dir: str
        conversation_path: str

new_session_context(workspace) 创建以上目录。SessionPaths 继续从 spill_dir 派生工具结果路径，避免破坏 ch08 调用方。

### Entry（JSONL 行）

    @dataclass
    class Entry:
        role: str = ""
        content: str = ""
        tool_calls: list[dict] | None = None
        tool_results: list[dict] | None = None
        tool_call_id: str | None = None
        tool_use_id: str | None = None
        name: str | None = None
        ts: int = 0
        model: str | None = None
        type: str | None = None

Entry 是 JSONL 的稳定边界；序列化只接受现有 Message 字段，反序列化严格检查 role 和工具 ID，未知字段忽略。tool_results 只用于兼容 grouped provider 记录，当前 ConversationManager 仍按 tool_call_id/tool_use_id 逐条写入。compact 是 type=compact 的单独事件，不能被当作 Message 注入 Provider。

### SessionSummary

    @dataclass(frozen=True)
    class SessionSummary:
        session_id: str
        title: str
        model: str
        message_count: int
        first_ts: int | None
        last_ts: int | None
        file_size: int
        valid: bool
        diagnostics: tuple[str, ...] = ()

概要由 SessionArchive.summarize 流式扫描 JSONL 得到，不写 meta 文件。title 是首条 user content 的 50 字符截断；旧格式 session 不返回列表项。

### SessionInfo 兼容视图

SessionArchive 对 TUI 提供轻量的 SessionInfo 视图，字段为 id、title、modified_at、model、size、dir。它由 SessionSummary 映射得到，不额外落盘；modified_at 优先取最后一条有效 JSONL 记录的 ts，文件 mtime 只作为无有效记录时的后备值。

### InstructionDocument

    @dataclass(frozen=True)
    class InstructionDocument:
        path: Path
        priority: int
        scope: Literal["project", "project_config", "user"]
        content: str
        diagnostics: tuple[str, ...] = ()

InstructionLoader(project_root, user_home=None, max_depth=5).load() 返回已经按优先级拼好的文本、来源文档和诊断；include 展开使用递归栈和 canonical visited 集合。

### MemoryNote / MemoryOperation

    MEMORY_TYPES = {
        "user_preference": "user",
        "correction_feedback": "user",
        "project_knowledge": "project",
        "reference_material": "project",
    }

    @dataclass
    class MemoryNote:
        filename: str
        type: str
        scope: Literal["user", "project"]
        title: str
        body: str
        created: str
        updated: str
        source_session: str
        status: str = "active"

    @dataclass
    class MemoryOperation:
        action: Literal["create", "update", "delete"]
        level: Literal["user", "project"]
        type: str | None = None
        filename: str | None = None
        title: str | None = None
        slug: str | None = None
        content: str | None = None

LLM 返回值先解析为 MemoryOperation，再按 MEMORY_TYPES 校验 level/type；LLM 不能绕过本地作用域规则。

### MemoryStore

    class MemoryStore:
        def __init__(self, directory: str) -> None: ...
        def ensure_dir(self) -> None: ...
        def load_index(self) -> str: ...
        def list_notes(self) -> list[MemoryNote]: ...
        def apply(self, actions: list[MemoryOperation], source_session: str) -> None: ...
        def clear(self) -> int: ...

MemoryStore 只管理一个作用域目录；MemoryManager 负责选择 user/project store 和校验类型映射。Store 的锁不能跨作用域共享，避免用户级更新阻塞项目级更新。

## 核心接口

    # newcode/context/session.py
    def _new_session_id(started_at: datetime | None = None) -> str: ...
    def new_session_context(workspace: str) -> SessionContext: ...

    # newcode/instructions/loader.py
    class InstructionLoader:
        def __init__(self, project_root: str | Path,
                     user_home: str | Path | None = None,
                     max_depth: int = 5) -> None: ...
        def load(self) -> tuple[str, list[InstructionDocument]]: ...

    # newcode/session/writer.py
    class SessionWriter:
        def __init__(self, session_dir: str) -> None: ...
        @classmethod
        def open_existing(cls, session_dir: str) -> "SessionWriter": ...
        def append_message(self, message: Message, *, model: str | None = None) -> None: ...
        def append_compact(self) -> None: ...
        def append_all(self, messages: list[Message]) -> None: ...
        def append_event(self, event: dict[str, object]) -> None: ...
        def close(self) -> None: ...
        def __enter__(self) -> "SessionWriter": ...
        def __exit__(self, exc_type, exc, tb) -> None: ...

    # newcode/session/archive.py
    class SessionArchive:
        def list(self) -> list[SessionSummary]: ...
        def read(self, session_id: str) -> list[dict[str, object]]: ...
    def cleanup_expired(self, now: datetime | None = None,
                        active_session_id: str | None = None) -> CleanupResult: ...
        def path_for(self, session_id: str) -> Path: ...

    # newcode/session/__init__.py 提供给 TUI 的函数式门面
    def list_sessions(sessions_dir: str) -> list[SessionInfo]: ...
    def load_session(session_dir: str) -> list[Message]: ...
    def clean_expired(sessions_dir: str, max_age: timedelta,
                      active_session_id: str | None = None) -> CleanupResult: ...

    # newcode/session/recovery.py
    class SessionRecovery:
        def recover(self, records: Iterable[dict[str, object]], *, now: datetime,
                    context_window: int, compressor: Callable[..., Awaitable[list[Message]]]) -> RecoveryResult: ...

    # newcode/memory/manager.py
    class MemoryManager:
        def load_indexes(self) -> str: ...
        def list_notes(self, scope: str = "all") -> list[MemoryNote]: ...
        def set_provider(self, provider: Provider, model: str) -> None: ...
        async def update_async(self, recent_messages: list[Message], session_id: str) -> None: ...
        def show(self, filename: str, scope: str) -> MemoryNote: ...
        def edit_path(self, filename: str, scope: str) -> Path: ...
        def clear(self, scope: Literal["user", "project", "all"]) -> int: ...

    # newcode/memory/store.py
    class MemoryStore:
        def apply(self, actions: list[MemoryOperation], source_session: str) -> None: ...

SessionWriter 的写操作保持同步，因为 ConversationManager 的追加接口是同步的；锁保证线程/回调并发安全。大文件清理或索引重建可使用 asyncio.to_thread，但不能改变消息追加顺序。

## 模块设计

### context.session

模块初始化时捕获一次进程启动时间；`_new_session_id(started_at=None)` 默认使用该固定时间，测试可注入 `started_at`。时间使用本地时区的 `strftime("%Y%m%d-%H%M%S")`，后缀使用 `secrets.token_hex(2)`。创建 session 目录使用 mkdir(parents=True, exist_ok=True)。SessionPaths.path_for 继续只操作 tool-results。

### instructions.loader

1. 将三个候选路径映射为 priority 80、81、82。
2. 对每层调用 _expand(path, boundary, depth, stack, visited)。
3. 用 anchored 正则识别独占行 include，非匹配文本原样保留。
4. 读取前校验存在性、普通文件、UTF-8/二进制特征和大小；缺失文件静默跳过。
5. 对目标调用 resolve(strict=False)，用 Path.is_relative_to(boundary) 校验边界；符号链接解析后的路径也必须在边界内。
6. 命中深度、环路或越界时追加 spec 规定的 HTML 警告，继续展开其他行。
7. 每层输出来源标记和正文，用空行拼接后交给 PromptBuilder。

### session.writer

append_message 先构造完整 dict，再在锁内执行 json.dumps(ensure_ascii=False) + 换行、一次 write、flush、os.fsync。写失败抛出可诊断异常给 Runtime，但由 Agent/TUI 边界捕获，不能中断用户请求。close 幂等。

on_replace 的顺序固定为 compact 行 -> 新消息行；如果任一步失败，不删除旧 JSONL，恢复仍从最后一个完整 compact 或之前记录开始。

### session.archive

- iter_session_dirs 只接受 .newcode/sessions/<id>/conversation.jsonl。
- parse_session_id 只接受新格式；旧格式返回 None。
- summarize_file 逐行解析，统计消息数、首个 user 标题、模型和最后有效 ts；坏行计入 diagnostics。
- read 保留记录顺序，跳过 schema 不合法的行；尾部半行自然由 JSONDecodeError 跳过。
- cleanup_expired 根据新格式 ID 的时间转换为 UTC 后比较 30 天，并通过 `active_session_id` 排除当前活动 ID。
- cleanup_expired 保持同步、可单独测试；启动后台清理通过 asyncio.to_thread(cleanup_expired, ...) 调用，不能把同步函数直接传给 create_task。

### session.recovery

恢复分三步：解析有效记录 -> 得到完整消息前缀 -> 做 token/时间降级。工具配对状态以 ID 集合维护；一个 assistant 有多个 tool calls 时必须全部闭合才可成为完整边界。compact 记录只改变读取起点，不生成 Message。

超限时调用可复用 ContextManager/Summarizer 的压缩窄接口，传入恢复消息副本，不能调用会修改当前会话的 Agent.run_force_compact。压缩失败后使用 MessageGroupDropper 按 user 分组降级，最多一次压缩尝试。

### memory.manager

记忆目录由构造器接收 project_root 和 Path.home，不从 LLM 输出中推导。MemoryManager 可以在 provider 尚未选择时先 load_indexes，选定 provider 后通过 set_provider 注入同一主会话 Provider。frontmatter 使用已有依赖或最小 YAML 解析器；不使用 ad-hoc 相似度。所有 create/update/delete 在作用域锁内执行，索引写入采用临时文件 + os.replace。

更新流程：复制最近消息和索引快照 -> 调用同一 Provider 的无工具请求 -> 解析严格 JSON 数组 -> 校验类型、level、filename 和正文大小 -> 交给对应 MemoryStore 应用文件操作 -> 重建对应 MEMORY.md -> 原子替换 -> 刷新内存索引缓存。任意步骤失败保留旧文件和旧索引。

索引超出 200 行或 25KB 时拒绝本次变更并记录原因，读取端仍做 25KB 防御性截断；本地只负责格式和大小，不负责判断语义重复。

### prompt 和启动装配

main.py 的一次性启动装配顺序：

1. 创建 InstructionLoader(workspace, user_home) 并加载文本。
2. 创建 MemoryManager 并加载项目/用户 MEMORY.md。
3. 创建 PromptBuilder 时按内容非空添加 Section("custom-instructions", instructions, 80) 和 Section("long-term-memory", memory, 100)。
4. 将同一个 builder 或稳定 prompt 文本传给 REPL/Agent 请求组装路径。

memory 更新成功后只替换 long-term-memory section 的缓存内容；指令 section 在进程内不变。更新发生在下一轮请求组装边界，不在流式请求中途改写。

### ConversationManager 回调

构造器保存可选回调。add_user、add_assistant、add_assistant_with_tool_calls、add_tool_result(s) 追加成功后调用 on_append；replace_history 复制新列表后调用 on_replace。回调异常由 SessionWriter/Runtime 记录并隔离，不能回滚内存消息，也不能让 trim 改变持久化顺序。

### Agent 和自动记忆

Agent 在每个 run 内记录用户输入、是否发生工具调用和最终 stop reason。只有最终 assistant 已写入 Conversation、没有待处理工具且发出 DONE 时才递增完成轮次并调度 MemoryManager.update_async。任务句柄保存到 Runtime，避免同作用域并发；下一轮输入不等待任务。退出阶段使用有限超时等待，超时取消任务并保留主会话。

### tui.app

把命令解析集中到现有 builtin command 路径：

    /resume
    /session list
    /session resume <id>
    /session new
    /session path [id]
    /session clean
    /memory list [scope]
    /memory show <id>
    /memory edit <id>
    /memory path [id]
    /memory clear <scope>

RESUMING 状态期间只处理搜索、上下键、Enter、Esc；Agent run 期间 slash command 返回等待提示。恢复切换成功后更新 REPL 的 Conversation、Writer、SessionContext 和 long-term-memory 状态，并保留旧未使用 session 文件。

## 关键数据流

### 新会话

    workspace
      -> new_session_context
      -> InstructionLoader.load()
      -> MemoryManager.load_indexes
      -> SessionWriter(open append)
      -> PromptBuilder sections
      -> REPL accepts input
      -> ConversationManager callback -> conversation.jsonl

### 恢复会话

    /resume
      -> SessionArchive.list/summarize
      -> OptionList selection
      -> SessionArchive.read
      -> SessionRecovery.recover
      -> close current Writer
      -> replace ConversationManager + SessionContext
      -> open selected Writer append
      -> show recovery result

### 自动笔记

    Agent DONE (no tools)
      -> turn_count / explicit keyword gate
      -> snapshot recent messages + MEMORY.md
      -> async provider request without tools
      -> validate MemoryOperation
      -> locked atomic file/index update
      -> refresh long-term-memory for next request

## 执行顺序和依赖

1. 扩展 context.session 的 ID 和目录模型，并为旧格式保留只读兼容。
2. 实现 Message JSON 序列化、SessionWriter 和单文件恢复解析。
3. 给 ConversationManager 增加回调，接入 SessionWriter；先覆盖追加和 compact 顺序。
4. 实现 SessionArchive 列表概要、路径校验和过期清理。
5. 实现 SessionRecovery 的工具配对、compact 起点、token 降级和时间提醒。
6. 实现独立 instructions 包和 PromptBuilder 两个新 section 的启动装配。
7. 实现 MemoryNote/MemoryManager、索引原子更新和 mock Provider 更新协议。
8. 在 Agent 自然 Done 路径接入异步记忆任务、显式关键词触发及退出清理。
9. 在 TUI 接入 /resume、/session、/memory 和互斥状态。
10. 完成集成测试、旧测试回归和后续 checklist。

依赖关系为：context.session -> session.writer -> conversation callbacks；session.archive/recovery -> TUI；instructions 和 memory -> PromptBuilder；Agent 只依赖 MemoryManager 的窄回调，不直接读写文件。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 会话目录 | .newcode/sessions/<id>/conversation.jsonl | 与 ch08 工具结果目录共享 session 生命周期 |
| 会话列表概要 | 流式扫描 JSONL，不维护 meta | 单一事实来源，符合已确认约束 |
| ID 时间 | 生成使用本地时间，记录 ts 使用 Unix 秒 | 满足展示格式，同时用 Unix ts 做可靠时间计算 |
| 指令优先级 | 单一 `custom-instructions` section 为 80，来源按项目根、项目配置、用户级拼接 | 保持 spec 的模块优先级，同时保留来源顺序 |
| 记忆优先级 | long-term-memory 100 | 作为稳定提示模块排在自定义指令之后 |
| 工具消息 | 保持现有 Message 字段和 tool ID | 防止 Anthropic/OpenAI 工具配对语义被破坏 |
| Writer 锁 | 同步 threading.Lock | ConversationManager 追加接口同步，避免异步改变顺序 |
| 压缩恢复 | 最多一次 LLM 压缩，失败后整组降级 | 保护工具调用对并控制恢复延迟 |
| 指令包边界 | 独立 newcode/instructions 包 | 指令加载是安全边界，不与长期记忆 CRUD 混合 |
| 记忆作用域 | type 到 scope 的本地白名单 | 防止跨项目泄漏 |
| 旧 session | 不展示、不清理、只读保留 | 避免误删 ch08 遗留数据 |

## 测试策略

- test_instructions_loader.py：三层顺序、缺失/空/二进制、include 独占行、深度 5、环路、路径逃逸、符号链接、大小和缓存。
- test_session_writer.py：ID regex、目录、JSONL 字段、tool ID、追加不改写、compact 顺序、锁、flush/fsync mock、close 幂等。
- test_session_archive.py：概要扫描、标题截断、模型/大小、坏行诊断、旧格式过滤、31 天清理和当前 session 保护。
- test_session_recovery.py：最后 compact、尾部/中间坏行、多 tool call 配对、孤立 result、一次压缩、整组降级、6 小时提醒。
- test_memory_manager.py：frontmatter、四类 scope、create/update/delete、索引上限、路径注入、原子替换、LLM 错误和并发锁。
- test_tui_resume.py：IDLE/STREAMING/RESUMING 路由、列表搜索、Enter/Esc、恢复成功和等待提示。
- test_ch09_integration.py：启动顺序、PromptBuilder priority、Conversation 回调、恢复后追加、Agent Done 触发、记忆更新不阻塞和旧测试兼容。

所有测试使用 tmp_path 隔离 workspace/home；Provider、编辑器、时间和 fsync 均可注入或 mock。实现阶段运行现有全量测试以及新增 ch09 测试，不能用真实用户目录作为 fixture。
