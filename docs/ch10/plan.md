# NewCode ch10 - SlashCommand 框架 Plan

## 架构概览

```
用户回车输入
    │
    ▼
REPL.run() ──► 分流器（/ 前缀 + 空输入早返回 + 状态机门）────────┐
    │                                                              │ 是命令
    │ 非命令                                                        ▼
    ▼                                                   CommandRegistry.lookup(name)
  AgentLoop（原逻辑）                                    （读写锁，名字/别名大小写不敏感）
    │                                                         │
    ▼                                                         ▼
  ...                                              CommandParser 拆参 + CommandContext 打包
                                                         │
                                                         ▼
                                          Handler(ctx, args) ──► UIController（UI 抽象）
                                          ├─ KindLocal：只读查询，任何状态可执行
                                          ├─ KindUI：改模式/换会话/退出/压缩，仅 idle
                                          └─ KindPrompt：注入 user 消息 → Agent.run 触发回合
```

核心思路：**命令框架（newcode/slash/）与 UI 完全解耦**。TUI 只负责把回车输入交给分流器、把 UIController 实现注入；命令注册中心持有元数据与 handler，handler 通过 UIController 抽象操作界面，不 import prompt_toolkit / rich。

## 组件划分

| 模块 | 文件 | 职责 |
|---|---|---|
| 命令注册中心 | newcode/slash/registry.py | CommandKind、CommandDef、CommandRegistry（RLock、启动期冲突检测、lookup/list/complete） |
| 命令解析器 | newcode/slash/parser.py | parse_command(text) → (name, args) 或 None；大小写不敏感 |
| 命令上下文 | newcode/slash/context.py | CommandContext 打包全部依赖 |
| UI 抽象 | newcode/slash/ui.py | UIController 抽象接口（Protocol/ABC） |
| 内置命令 | newcode/slash/commands/*.py | 各命令实现（见文件组织） |
| 命令装配 | newcode/slash/commands/__init__.py | register_all(registry) |
| TUI 接入 | newcode/tui/app.py | 分流器、Tab 补全 completer、状态栏、RichUIController 实现 |
| 装配 | newcode/main.py | 构造 registry、register_all、组装 CommandContext、注入 REPL |
| 权限 API | newcode/permission/checker.py | 新增 count_rules / add_rule / reset_rules（/permission_* 需要） |
| 会话 API | newcode/session/runtime.py（复用） | /session_* 复用现有 SessionRuntime / SessionArchive |
| 记忆 API | newcode/memory/store.py（复用） | /memory_* 复用现有 MemoryStore.list_notes / apply / clear |

## 核心数据结构

### CommandKind（枚举）
`LOCAL`（纯本地）、`UI`（影响界面）、`PROMPT`（提示词）

### CommandDef（dataclass）
```python
@dataclass(frozen=True)
class CommandDef:
    name: str                  # 命令名（不含 /，小写）
    aliases: tuple[str, ...]   # 别名集合
    description: str           # 一句描述（/help、补全菜单共用）
    kind: CommandKind          # LOCAL / UI / PROMPT
    handler: Callable[[CommandContext, str], Awaitable[None]]
    usage: str = ""            # 用法示例（含参数形式）
    arg_prompt: str = ""       # 参数格式提示（Tab 补全补充）
    hidden: bool = False       # 隐藏命令（不出现在 /help 与补全，dispatcher 仍命中）
```

### CommandRegistry
```python
class CommandRegistry:
    _commands: dict[str, CommandDef]      # name -> def（含别名索引）
    _lock: RLock                          # 读写锁（为 Skill 动态注册预留）
    def register(self, cmd: CommandDef) -> None
        # 启动期冲突检测：name 与所有 alias 与既有索引交叉检查，
        # 冲突抛 RuntimeError（含冲突名字），main 捕获后立即终止启动（N4）
    def get(self, name: str) -> CommandDef | None      # 大小写不敏感，名字/别名均可
    def list(self, include_hidden: bool = False) -> list[CommandDef]  # 按 name 字典序
    def complete(self, prefix: str) -> list[CommandDef]               # 前缀匹配，排除 hidden
    def register_all_from(self, builders: Iterable[Iterable[CommandDef]]) -> None
```

### CommandParser
```python
def parse_command(text: str) -> tuple[str, str] | None:
    # strip 后空串 → None；不以 "/" 开头 → None
    # 第一个空格前为 name（转小写），之后为 args（原样保留）
```

### CommandContext
```python
@dataclass
class CommandContext:
    registry: CommandRegistry
    ui: UIController              # UI 抽象
    agent: Agent
    conversation: ConversationManager
    plan_manager: PlanManager
    session_runtime: SessionRuntime | None
    session_archive: SessionArchive | None
    memory_manager: MemoryManager | None
    permission: PermissionChecker | None
    version: str
    cwd: Path
```

### UIController（抽象接口）
```python
class UIController(Protocol):
    # 输出
    def show_message(self, text: str, style: str = "") -> None: ...
    # 用户消息注入（KindPrompt 用）
    def send_user_message(self, text: str) -> None: ...
    # 模式
    def get_permission_mode(self) -> str: ...
    def set_permission_mode(self, mode: str) -> None: ...
    def get_app_mode(self) -> str: ...
    # 查询
    def query_token_usage(self) -> tuple[int, int]: ...   # (in, out)
    def query_tool_count(self) -> int: ...
    def query_memory_files(self) -> list[str]: ...
    def get_model_name(self) -> str: ...
    def get_cwd(self) -> str: ...
    # 生命周期
    def request_exit(self) -> None: ...          # 触发退出（含 cancel scope 取消，N12）
    def request_session_list(self) -> None: ...  # 打开历史会话选择
    def request_compact(self) -> None: ...       # 触发上下文压缩
    def request_clear_session(self) -> None: ... # 清空并新建会话（内部原子重置顺序见"模块交互"节）
```

## 模块设计

### newcode/slash/registry.py
- `register`：写锁下检查 name 与所有 alias 是否已存在于 `_commands` 索引；冲突抛 `RuntimeError`（消息含具体冲突名字）。
- `get`：读锁下按 name/alias 查找，`name.lower()` 归一化实现大小写不敏感。
- `list`：读锁下按 name 字典序返回；`include_hidden=False` 时过滤 hidden。
- `complete`：读锁下对 prefix 前缀匹配 name，排除 hidden；返回按字典序。
- RLock 保证 Skill 系统动态注册与查找并发安全（读写锁）。

### newcode/slash/parser.py
纯函数。`strip()` 后空串 → None；不以 `/` 开头 → None；否则 `split(maxsplit=1)` 拆 name/args，name 转小写。不解析参数结构（spec F7）。

### newcode/slash/context.py
`CommandContext` dataclass。在 main.py 组装一次，注入 registry / ui / agent / 各管理器。

### newcode/slash/ui.py
`UIController` Protocol 定义抽象接口（F6.2 最小暴露）。同时提供 `NopUI` 测试桩：所有写入方法 no-op、所有查询返回零值，供 handler 单测与集成测试复用（避免逐命令手写 mock，N11 可测试性）。TUI 侧实现 `RichUIController`（在 app.py 内，包一层 console.print / mode 切换 / token 统计 / 退出 / 会话操作）。

### newcode/slash/commands/
每个命令一个文件，每个文件导出 `build(registry_deps) -> list[CommandDef]`。`register_all()` 遍历收集。

| 文件 | 命令 |
|---|---|
| help.py | /help |
| status.py | /status |
| memory.py | /memory、/memory_list、/memory_add、/memory_clear |
| permission.py | /permission、/permission_rules、/permission_add、/permission_reset |
| session.py | /session、/session_list、/session_resume、/session_new |
| plan.py | /plan、/normal |
| do.py | /do |
| clear.py | /clear |
| compact.py | /compact |
| review.py | /review |
| legacy.py | /exit、/quit、/resume、/delete-plan |

### newcode/tui/app.py 接入
- 新增 `RichUIController` 类实现 UIController（包住现有 console/mode/token/exit/session/compact 逻辑）。
- 新增 `REPL.dispatch_slash(text) -> bool`：命中并处理完成返回 `True`，非命令返回 `False`（上层走 AgentLoop）。内部——`CommandParser.parse` → `registry.get`；命中 → 状态机门（KindUI/KindPrompt 仅 idle，非 idle 提示"请等待当前任务完成"）→ `try/except` 包裹 `await handler(ctx, args)`（异常 → `ui.show_message(f"命令执行失败: {exc}", style="red")` 上屏，不崩 REPL）；未命中且 `/` 开头 → 引导 /help；空输入 → 早返回 `False`。
- `REPL.run()`：回车后先调用 `dispatch_slash(text)`，返回 `False` 才走 AgentLoop 原路径。
- PromptSession 增加 completer：注册表派生（/ 前缀匹配、排除 hidden、显示 name+description）。
- `_toolbar` 从注册表派生高频命令提示（仅 /help 可硬编码，N5）。
- 移除 /exit-plan 分支；现有 if/elif 命令分支迁移到命令文件。

### newcode/main.py 接线
1. 构造 `registry = CommandRegistry()`
2. `register_all(registry)`（从 commands 包收集）
3. 组装 `CommandContext(registry, ui=RichUIController(...), agent, conversation, plan_manager, session_runtime, session_archive, memory_manager, permission, version, cwd)`
4. `REPL(agent, ..., registry=registry, ui=ui_impl, command_ctx=ctx)`
5. 启动期冲突检测：`register_all` 抛异常时捕获 → 打印冲突名字 → `sys.exit(1)`（N4/F1.3）
6. 顺带把 session_runtime / session_archive 传入 REPL（ch09 遗留缺口，本 spec F8.6/F8.21 依赖）

### newcode/permission/checker.py 新增
- `count_rules() -> int`：统计三层规则文件（local/project/user）规则总数
- `add_rule(pattern, effect) -> None`：写入本地规则（镜像现有 `persist_local_allow` 的写回路径）
- `reset_rules() -> int`：清空本地规则，返回删除条数

## 模块交互

```
main.py → register_all(registry) → registry.register(每条 CommandDef)
main.py → REPL(agent, registry, ctx, ui_impl)（含 session_runtime/archive 传入）
REPL.run() 回车
    → CommandParser.parse(text)
    → 命中：registry.get → 状态机门（KindUI/KindPrompt 仅 idle）
        → await handler(ctx, args)
        → handler 通过 ctx.ui / ctx.agent / ctx.session_runtime / ctx.memory_manager ... 执行
    → 未命中且 / 开头：引导 /help（来自 registry.list）
    → 非命令：原 AgentLoop 路径
handler 各司其职：
    KindLocal → ctx.ui.show_message（只读查询，无副作用）
    KindUI     → ctx.ui.set_permission_mode / request_clear_session / request_exit ...
    KindPrompt → ctx.ui.send_user_message(注入文本) + agent.run(mode) → 流式渲染

/clear 原子重置顺序（RichUIController.request_clear_session 内部）：
    session_runtime.create_new()（ch09 现成封装：close 旧 writer → new_session_context → 新 writer → 重建 ConversationManager）
    → context_manager.reset_for_new_session()（新方法：清 ContentReplacementState 账本 + AutoCompactGate 计数 + usage_anchor/anchor_msg_len 归零）
    → token 计数与回合数归零 → AppMode=NORMAL
```

## 文件组织

```
newcode/slash/
├── __init__.py          # 导出 CommandRegistry、CommandKind、CommandDef、parse_command、CommandContext
├── registry.py          # CommandKind / CommandDef / CommandRegistry
├── parser.py            # parse_command
├── context.py           # CommandContext
├── ui.py                # UIController Protocol
└── commands/
    ├── __init__.py      # register_all(registry)
    ├── help.py
    ├── status.py
    ├── memory.py
    ├── permission.py
    ├── session.py
    ├── plan.py
    ├── do.py
    ├── clear.py
    ├── compact.py
    ├── review.py
    └── legacy.py        # /exit /quit /resume /delete-plan

newcode/context/manager.py      # 修改：新增 reset_for_new_session()（清 L1 替换账本 + 自动闸 + 锚点，/clear 用）
newcode/permission/checker.py   # 修改：count_rules / add_rule / reset_rules
newcode/tui/app.py              # 修改：分流器 / completer / 状态栏 / RichUIController
newcode/main.py                 # 修改：装配接线 + 启动冲突检测 panic

tests/
├── test_ch10_registry.py       # 注册中心单测（冲突检测/查找/列举/补全/并发）
├── test_ch10_parser.py         # 解析器单测
├── test_ch10_commands.py       # 各命令 handler 单测（mock UIController）
├── test_ch10_tab_completion.py # Tab 补全单测
├── test_ch10_tui.py            # TUI 接入测试（object.__new__ REPL + mock UIController）
└── test_ch10_integration.py    # 端到端集成测试
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 包位置 | 独立 newcode/slash/ | Skill 动态注册复用、跨 UI 解耦（用户拍板） |
| /do 语义 | 保留执行计划（local-ui） | 用户拍板 |
| /memory /permission /session | 只读基础命令 + /xxx_* 扁平衍生命令 | 用户拍板（与模板对齐） |
| /review | 不读 diff，注入固定文本（KindPrompt） | 用户拍板（与模板对齐） |
| 补全 | 简单 Tab：单匹配直补、多匹配弹列表 | 用户拍板 |
| 冲突检测 | register 时抛 RuntimeError，main 捕获后 sys.exit(1) | 用户要求（启动即炸，N4 带冲突名） |
| 锁 | RLock 读写锁 | 用户要求（Skill 动态注册预留） |
| 状态机 | KindUI/KindPrompt 仅 idle；KindLocal 任意状态 | 吸收模板 N3a |
| 单一信源 | /help 与未命中提示来自 registry.list()，只允许 /help 硬编码 | 吸收模板 N5 |
| /exit | 先取消主 cancel scope 再退出 | 吸收模板 N12 |
| 提示词持久化 | KindPrompt 注入消息与真实用户消息同路径 | 吸收模板 F3.4 |
| hidden | 不进 /help 与补全，dispatcher 仍命中 | 吸收模板 F10 |
| 会话/记忆/权限 API | 复用 ch09 现有 SessionRuntime/MemoryStore；permission 补 3 个方法 | 不重复造轮子 |
| /clear 原子重置 | `request_clear_session` 内部：`SessionRuntime.create_new()`（ch09 现成）+ **新增 `ContextManager.reset_for_new_session()`** 清 compact 子状态 | 模板的 reset 假设建立在 runtime 持有 compact 子状态上，ch09 实际在 ContextManager（L1 账本/自动闸/锚点）；create_new 已封装换会话流程，不重复造 |
| 分发返回值 | `dispatch_slash(text) -> bool`，REPL.run() 按返回值分支 | 分流链可脱离 REPL 单测；REPL.run() 保持可读 |
| handler 异常兜底 | dispatch_slash 用 try/except 包裹 handler，异常上屏不崩 REPL | 命令实现出错对用户可见，避免静默失败 |
| 测试桩 | slash/ui.py 提供具名 NopUI（写入 no-op、查询零值） | 替代逐命令手写 mock，N11 可测试性（模板同款做法） |
