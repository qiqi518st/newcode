# NewCode ch08 — 上下文管理 技术设计 (plan.md)

## 架构概览

本章在 ch07 MCP 客户端之上，新增一个 **`newcode.context` 子包**，承载两层上下文管理 + Token 估算 + Context Window 解析 + 文件追踪 + Skill 骨架 + 会话落盘。子包对 Agent 主循环只暴露一个窄入口 `ContextManager`，主循环在每轮组装请求前调用它完成第一层 + 第二层压缩；TUI 斜杠命令路径与紧急压缩路径通过 `Agent` 暴露的 `compact_now()` / `force_compact()` 间接调用同一核心。所有阈值硬编码在子包内常量模块，不进配置层。

```
                          ┌──────────────────────────────────────────────────┐
                          │                   Agent.run()                     │
                          │  每轮：cancel 检查 → manage_context → assemble →   │
                          │        provider.stream → 解析 → 工具执行 → 回填    │
                          └──────────┬──────────────────────┬──────────────────┘
                                     │ 每轮前                │ 请求撞 PTL 兜底
                                     ▼                      ▼
                          ┌─────────────────────┐  ┌─────────────────────┐
                          │   ContextManager    │  │   ContextManager    │
                          │   manage_context()  │  │   force_compact()   │  ← Agent 内 emergency_retried 标记
                          │  (自动：L1+L2 检查)   │  │  (强制：先L1→摘要→  │
                          └──────┬──────────────┘  │   丢组重试，独立3次) │
                                 │                  └──────────┬──────────┘
                                 │                             │
                ┌────────────────┼────────────────┐            │ 共用
                ▼                ▼                ▼            ▼
        ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
        │ L1 offload   │ │ L2 summarize │ │ token 纯函数 │ │ F27 dropper │
        │ _and_snip()  │ │ (调 provider) │ │ (锚定usage)  │ │ (丢消息组)   │
        │ 纯字符串替换  │ │ 九段+恢复段   │ │              │ │             │
        └──────┬───────┘ └──────┬───────┘ └──────────────┘ └──────────────┘
               │                │
               ▼                ▼
        ┌──────────────┐ ┌──────────────────────────┐
        │ContentRepl.  │ │RecoveryBuilder            │
        │State(账本)    │ │ 文件快照+工具列表+边界提示│
        │ 锁保护        │ │ (工具列表与 stream 同引用) │
        └──────────────┘ └──────┬───────────────────────┘
                                │
                                ▼
                        ┌──────────────┐
                        │ FileTracker  │  ← Agent 工具执行后回填（同 task 顺序）
                        │ 锁保护        │
                        └──────────────┘

  解析侧：getContextWindowForModel(model) 四级优先 → context_window
  能力表：capabilities.py 静态表 + scripts/probe_context_window.py 手工探测回填
  Skill 骨架：SkillRegistry（空实现 + 注入挂载点，内容加载 TODO）
  会话落盘：.newcode/sessions/<session_id>/tool-results/<tool_use_id>
  TUI 侧：BUILTIN_COMMANDS 注册表（/exit /plan /do /compact …），熔断/紧急菜单复用 _ask_choice
```

### 组件划分

- **`context.tokens`**：纯函数估算——`estimate_tokens(anchor, all_msgs, anchor_msg_len)` 锚定真实 usage + 字符/3.5 增量；常量 `ESTIMATE_CHARS_PER_TOKEN=3.5`；锚点/`anchor_msg_len` 由调用方外部跟踪。
- **`context.window`**：`getContextWindowForModel(model)` 四级解析 + 静态能力表 `CAPABILITIES`。
- **`context.replacement`**：`ContentReplacementState`——账本（`seen_ids` + `replacements`），锁保护，四子项决策冻结。
- **`context.offload`**：`offload_and_snip`——第一层纯字符串替换，三步原子（落盘→改写→写账本），幂等 `wx`，预览体构造。
- **`context.files`**：`FileTracker`——最近访问文件追踪，锁保护，纯净字节记录。
- **`context.recovery`**：`RecoveryBuilder`——恢复段三块（文件快照 + 工具列表 + 边界提示），工具列表与 stream 同引用。
- **`context.summarize`**：`Summarizer`——第二层 LLM 全量摘要，九段结构 prompt，草稿+正文，不传工具；调 `provider.stream` 走独立请求。
- **`context.dropper`**：`MessageGroupDropper`——F27 摘要请求自身 PTL 的丢消息组重试（三路径共用底层）。
- **`context.manager`**：`ContextManager`——窄入口，编排 L1+L2 自动检查、`compact_now`（手动）、`force_compact`（紧急）、熔断收尾菜单。持会话级 `asyncio.Lock` 与主循环互斥。
- **`context.session`**：`SessionPaths`——会话 id 生成 + 落盘目录管理。
- **`context.capabilities`**：静态能力表 + 探测脚本引用说明。
- **`context.skill`**：`Skill` 数据类 + `SkillRegistry`（骨架，内容加载 TODO）。
- **`context.constants`**：全部硬编码阈值常量。
- **`Agent`（修改）**：每轮前调 `context_mgr.manage_context()`；`_run_lock` 贯穿整轮；工具执行回填前同步记 `FileTracker`；请求撞 PTL 时调 `force_compact()` + `emergency_retried` 标记；暴露 `run_force_compact()` 供 TUI。
- **`ConversationManager`（修改）**：新增「就地改写某条 tool_result 内容」「替换整段历史为新消息列表」两个方法；`_trim` 修正为不拆对、降级条数兜底。
- **`REPL`（修改）**：斜杠命令迁移到 `BUILTIN_COMMANDS` 注册表；新增 `/compact`；熔断/紧急菜单复用 `_ask_choice`；自动/紧急压缩 UX 提示。
- **`provider`（修改）**：新增 `llm.PromptTooLongError` 哨兵异常；anthropic/openai 适配层把 `prompt_too_long` 类错误包装成该异常经 `StreamEvent.err` 投递，Agent 用 `isinstance` 判定，供 ForceCompact 识别。
- **`scripts/probe_context_window.py`（新建）**：独立探测脚本，不在主流程，手工回填能力表。

## 核心数据结构

### Message（既有，不改）

`newcode/provider/base.py:25`——`role/content/tool_calls/tool_call_id/tool_use_id/name`。第一层就地改写只动 tool 角色消息的 `content` 字段；第二层摘要替换以消息列表为粒度整段替换。

### ToolCall（既有，不改）

`tool_use_id`（Anthropic）/ `tool_call_id`（OpenAI）作为第一层文件名与账本 key。`newcode/provider/base.py:37`。`id` 可能为空字符串（流式回填兜底），落盘与账本需兜底命名。

### TokenUsage（既有，不改）

`newcode/provider/base.py:14`——`input_tokens/output_tokens/cache_creation_input_tokens/cache_read_input_tokens`。`usage_to_anchor` 锚点 = 四者之和。

### ContentReplacementState（新增）

```python
from dataclasses import dataclass, field

@dataclass
class ContentReplacementState:
    """第一层替换决策账本（会话级，锁保护，决策冻结）。"""
    seen_ids: set[str] = field(default_factory=set)          # 已决策的 tool_use_id
    replacements: dict[str, str] = field(default_factory=str)  # id → 预览替换体字符串（冻结）

    # 锁：读账本→决策→写账本在同一临界区原子完成（N2）
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
```

方法（均 `async`，内部 `async with self._lock:`）：
- `decision_for(tool_use_id: str) -> str | None`：返回已冻结的预览字符串（已替换）或 `""`（已决定保留原文，用哨兵区分）或 `None`（未决策）。具体用 `tuple[Literal["replaced","kept","unseen"], str | None]` 返回更清晰，plan 定为后者。
- `record_replaced(tool_use_id: str, preview: str) -> None`：写 `seen_ids` + `replacements[id]=preview`。
- `record_kept(tool_use_id: str) -> None`：只写 `seen_ids`（保留原文）。
- **落盘失败绝不写账本**（F5b）：调用方在落盘成功后才调 `record_*`。

### FileTracker（新增）

```python
@dataclass
class TrackedFile:
    path: str           # 绝对路径
    content: str        # 纯净字节（不带行号、不带截断提示）
    timestamp_ns: int   # 最后一次成功读取的单调时间戳（time.monotonic_ns）

class FileTracker:
    """最近访问文件追踪（会话级，锁保护，按时间倒序保留）。"""
    _files: dict[str, TrackedFile]   # path → TrackedFile（去重，覆盖更新时间戳）
    _lock: asyncio.Lock
```

方法（均 `async`）：
- `record(path: str, content: str) -> None`：用纯净 content 覆盖/新增条目，时间戳取 `time.monotonic_ns()`。
- `recent(limit: int = 5) -> list[TrackedFile]`：按 `timestamp_ns` 倒序取前 `limit` 个。
- `max_files = 5`、`per_file_token_budget = 5000`（常量，见 `constants`）。

**纯净字节来源**：`read_file` 成功且 `truncated=False` 时直接用 `result.output`；`truncated=True` 时剥离尾部 `...（已截断…）` 提示行（用 `result.output` 截到提示前的内容，或重新读全量——plan 定为剥离提示，避免重复读盘）。**记录动作必须在 `add_tool_result` 之前同 task `await` 完成**（F19a）。

### RecoveryBundle（新增）

```python
@dataclass
class RecoveryBundle:
    """摘要后恢复段三块，序列化为**单条 user 消息的 content 片段**（不各自成 Message）。
    合并成一条 user 消息输出，避免摘要(user)+恢复(user) 连续 user 触发 Anthropic
    roles-must-alternate 400 错误（实际代码 anthropic.py 把 tool 角色翻成 user，
    连续 user 会被 API 拒绝）。"""
    file_snapshots_text: str   # 最近文件快照文本（≤5 个，每个 ≤5000 token）
    tools_declaration_text: str # 工具列表声明文本（与 stream 同一份 ToolDefinition 引用）
    boundary_notice_text: str   # 边界提示固定文案
```

`RecoveryBuilder.build(file_tracker, tool_defs) -> RecoveryBundle`：
- `file_snapshots_text`：调 `file_tracker.recent(5)`，每个 `TrackedFile.content` 按 `int(5000 * 3.5)` 估字符数截头部，超长尾部加 `(content truncated)`，拼成「路径 + 时间戳 + 片段」的多行文本块（**不是多条 Message，是一段 str**）。
- `tools_declaration_text`：**直接引用传入的 `tool_defs` 列表对象**（`id(defs)` 与 stream 一致），序列化成「当前可用工具：…」文案文本。`build` 不独立重算或选子集。
- `boundary_notice_text`：固定文案。
- 三块都是 `str`，由 `Summarizer` 拼进单条 user 消息的 content（见 summarize.py）。

### SummarizeRequest / SummarizeResult（新增）

```python
@dataclass
class SummarizeConfig:
    """单次摘要行动的参数（自动/手动/紧急各有不同）。"""
    safety_margin: int       # 自动 13000 / 手动 3000 / 紧急 3000
    keep_recent_turns: int   # 自动 6 / 手动 6 / 紧急更少（如 3）

@dataclass  
class CompactOutcome:
    """一次压缩行动的结果（供 TUI 展示与主循环决策）。"""
    triggered: bool                  # 是否真的执行了压缩（手动/紧急总是 True，自动可能未达阈值）
    before_tokens: int               # 压缩前估算
    after_tokens: int                # 压缩后估算
    replaced_results: int            # 被第一层替换的工具结果数（手动/紧急含强制 L1）
    success: bool                    # 本次行动是否成功
    failure_reason: str              # 失败时原因分类（"network"/"api"/"prompt_too_long"/…）
    messages: list[Message]          # 成功时的新消息列表（摘要+恢复+近期原文）；失败时为 None
```

### Skill / SkillRegistry（新增骨架）

```python
@dataclass
class Skill:
    """Skill 最小骨架（内容加载留 TODO）。"""
    name: str
    description: str
    content: str = ""   # TODO: 内容加载待后续章节实现，当前始终为空

class SkillRegistry:
    """Skill 注册与查询容器（骨架）。"""
    def register(self, skill: Skill) -> None: ...
    def get(self, name: str) -> Skill | None: ...
    def list(self) -> list[Skill]: ...
    def total_tokens(self, estimator) -> int: ...  # 当前总为 0（无内容）
```

F31 注入挂载点：`RecoveryBuilder` 持可选 `SkillRegistry`，非空且有 Skill 时按 25000 预算注入稳定提示段；当前 registry 始终空，注入分支为空实现（TODO 注释）。

## 核心接口

```python
# ── Token 估算（context/tokens.py，纯函数，无状态）─────────────
def usage_to_anchor(usage: TokenUsage) -> int:
    """四者之和：input+output+cache_creation+cache_read（spec F14 替换不累加）。"""
def message_chars(msgs: list[Message]) -> int:
    """单段消息列表的字节总量（content + tool_calls 序列化）。"""
def estimate_tokens(anchor: int, all_msgs: list[Message], anchor_msg_len: int) -> int:
    """anchor + ceil(message_chars(all_msgs[anchor_msg_len:]) / 3.5)。
    all_msgs 必须是 L1 之后的列表；anchor=0 且 anchor_msg_len=0 时退化为纯字符估算。"""
def estimate_messages(messages: list[Message]) -> int:
    """纯 ceil(message_chars / 3.5)，摘要请求自检用（F23）。"""

# ── Context Window 解析（context/window.py）────────────────────
def get_context_window_for_model(model: str, protocol: str) -> int: ...
    # 四级：env CLAUDE_CODE_MAX_CONTEXT_TOKENS → [1m] 后缀 → CAPABILITIES 表(≥100K) → 协议默认

# ── 第一层（context/offload.py）────────────────────────────────
async def offload_and_snip(
    messages: list[Message],
    state: ContentReplacementState,
    session_paths: SessionPaths,
) -> int:
    """对 messages 原地执行 L1 替换，返回被替换的结果数。
    纯字符串处理不调 LLM；三步原子（落盘→改写content→写账本）；幂等 wx。"""

# ── 第二层摘要（context/summarize.py）──────────────────────────
class Summarizer:
    def __init__(self, provider: Provider) -> None: ...
    async def summarize(
        self,
        messages: list[Message],
        config: SummarizeConfig,
        context_window: int,
        recovery_builder: RecoveryBuilder,
        tool_defs: list[ToolDefinition],
    ) -> CompactOutcome:
        """生成九段摘要 + 恢复段（合并单条 user 消息）+ 近期原文（role 衔接修正），返回新消息列表。
        摘要请求不传工具；草稿丢弃只留 <summary>；近期原文双下界择宽不拆对。
        摘要请求内部撞 PTL 走 F27 丢组重试（含 3 次直接 + 比例丢）。"""

# ── F27 丢消息组（context/dropper.py）──────────────────────────
class MessageGroupDropper:
    def group_by_user(self, messages: list[Message]) -> list[list[Message]]: ...
        # 按 user 消息分界分组（一条 user + 其后到下一条 user 之前）
    def drop_oldest(self, groups: list[list[Message]], n: int) -> list[list[Message]]: ...
    def drop_ratio(self, groups: list[list[Message]], ratio: float) -> list[list[Message]]: ...
        # 丢 ceil(剩余 × ratio)，至少 1 组

# ── 自动触发闸（context/manager.py 内，仅自动路径）─────────────
class AutoCompactGate:
    """仅自动路径的连续失败闸（防菜单轰炸）；手动/紧急不受限、不跨种类。"""
    def __init__(self) -> None: ...              # _consecutive_failures=0
    def record_auto_success(self) -> None: ...   # 清零（含闸外成功）
    def record_auto_failure(self) -> None: ...   # _consecutive_failures += 1
    def auto_disabled(self) -> bool: ...         # _consecutive_failures >= AUTO_GATE_LIMIT(3)
    def reset_on_manual_success(self) -> None: ...  # 手动 /compact 成功 → 清零解除闸

# ── 窄入口（context/manager.py）────────────────────────────────
class ContextManager:
    def __init__(
        self,
        provider: Provider,
        conversation: ConversationManager,
        model: str,
        protocol: str,
        file_tracker: FileTracker,
        skill_registry: SkillRegistry | None = None,
    ) -> None: ...
    @property
    def context_window(self) -> int: ...
    @property
    def usage_anchor(self) -> int: ...           # 外部锚点状态
    @property
    def anchor_msg_len(self) -> int: ...
    async def manage_context(self, tool_defs: list[ToolDefinition]) -> None:
        """Agent 每轮组装请求前调用：L1 全量扫 + L2 自动阈值检查（受自动闸约束）。
        入口 sanity check context_window>33000；自动路径单次行动 3 次重试都没成→弹菜单+record_auto_failure。"""
    async def compact_now(self) -> CompactOutcome:
        """手动 /compact：跳阈值/跳自动闸/跳 L1，无条件摘要（3000 余量仅自检）。
        等会话锁（与主循环互斥，F34）；成功→reset_on_manual_success 解除自动闸。"""
    async def force_compact(self, tool_defs: list[ToolDefinition]) -> CompactOutcome:
        """紧急压缩：先强制 L1 挪走 50K+ → 摘要 → 仍 PTL 走 F27 丢组。
        单次行动 3 次重试；emergency_retried 由 Agent 持；不受自动闸约束。"""
    def update_anchor(self, usage: TokenUsage, conv_len: int) -> None:
        """Agent 主对话路径每轮请求后调：usage_anchor=usage_to_anchor(usage)、anchor_msg_len=conv_len。
        摘要路径不调此方法（防污染锚点）。"""
    def reset_anchor(self) -> None:
        """紧急压缩成功后清零锚点（usage_anchor=0、anchor_msg_len=0），强制重估。"""
```

## 模块设计

### newcode/context/__init__.py
**职责：** 子包门面，导出 `ContextManager`、`AutoCompactGate`、`usage_to_anchor`/`estimate_tokens`/`estimate_messages`/`message_chars`、`get_context_window_for_model`、`ContentReplacementState`、`FileTracker`、`Skill`、`SkillRegistry`、`SessionPaths`、常量。
**依赖：** 仅 `newcode.provider.base`（`Message/ToolCall/ToolResult/TokenUsage/Provider/ToolDefinition`）、`newcode.conversation.manager`、标准库；**不依赖 agent / tui / permission / mcp / config**（context_window 经构造传入 model/protocol 字符串，不读 config）。

### newcode/context/constants.py
**职责：** 全部硬编码阈值。
**对外接口：** 常量集合。
**关键常量：**
```
SINGLE_RESULT_THRESHOLD = 50_000        # 字节（F1）
AGGREGATE_LIMIT = 200_000               # 字节（F2）
PREVIEW_MAX_LINES = 20                  # 行（F4）
PREVIEW_MAX_BYTES = 2048                # 字节（F4）
SUMMARY_RESERVE_TOKENS = 20_000         # token（F7）
AUTO_SAFETY_MARGIN = 13_000             # token（F7）
MANUAL_SAFETY_MARGIN = 3_000            # token（F8/F23/F25a）
RECENT_TOKEN_FLOOR = 10_000             # token（F11 双下界之一）
RECENT_COUNT_FLOOR = 5                  # 条（F11 双下界之一）
MAX_RECENT_FILES = 5                    # 个（F16）
PER_FILE_TOKEN_BUDGET = 5_000           # token（F16）
SKILL_RECOVERY_BUDGET = 25_000          # token（F31）
COMPACT_RETRY_LIMIT = 3                 # 次（F28 单次行动重试上限）
PTL_DIRECT_RETRY_LIMIT = 3              # 次（F27 直接重试）
PTL_DROP_RATIO = 0.2                    # F27 比例丢弃步长 / F13 第二步
GROUP_DROP_STEP = 2                     # 组（F28 菜单分组丢弃每次量）
AUTO_GATE_LIMIT = 3                     # 轮（自动路径连续失败闸，防菜单轰炸）
ESTIMATE_CHARS_PER_TOKEN = 3.5          # F13
ONE_M_WINDOW = 1_000_000                # F29 第 2 级
DEFAULT_WINDOW_ANTHROPIC = 200_000      # F29 第 4 级
DEFAULT_WINDOW_OPENAI = 128_000         # F29 第 4 级
CAPABILITY_TABLE_FLOOR = 100_000        # F29 第3级准入表下限
CONTEXT_WINDOW_FLOOR = 33_000           # F7 入口 sanity check 下界（SUMMARY_RESERVE+AUTO_MARGIN）
```
模块级变量（非字面常量），便于单测 monkeypatch 改小并 restore。

### newcode/context/tokens.py
**职责：** Token 估算——**纯函数**模型，锚定真实 usage + 字符/3.5 增量；锚点与 `anchor_msg_len` 由调用方（Agent 主循环 / ContextManager）外部跟踪，估算器本身无状态。
**对外接口：** `estimate_tokens(anchor, all_msgs, anchor_msg_len)`、`estimate_messages(messages)`、`usage_to_anchor(usage)`、`message_chars(msgs)`。
**依赖：** `TokenUsage`、`Message`、`constants.ESTIMATE_CHARS_PER_TOKEN`、`math`。
**关键点：**
- `usage_to_anchor(usage) -> int`：`usage.input_tokens + usage.output_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens`（四者之和，对应 spec F14 的「替换不累加」——每次用最新 usage 直接替换锚点）。
- `message_chars(msgs) -> int`：累加 `len(content.encode("utf-8"))` + 每个 `tool_calls[i]` 序列化后的字节长度。偏保守用字节（中文 3 字节）。
- `estimate_tokens(anchor, all_msgs, anchor_msg_len) -> int`：
  - `anchor` = 上一次主对话路径 stream 真实 usage 之和（int）；
  - `anchor_msg_len` = 锚点记录时 `conv` 的消息条数，表示锚点已涵盖 `all_msgs[:anchor_msg_len]`；
  - 只把 `all_msgs[anchor_msg_len:]` 的字符增量算进去：`anchor + math.ceil(message_chars(all_msgs[anchor_msg_len:]) / ESTIMATE_CHARS_PER_TOKEN)`；
  - `anchor==0` 且 `anchor_msg_len==0`（首轮 / 摘要后重置）时退化为纯字符估算 `math.ceil(message_chars(all_msgs) / 3.5)`。
  - **入参 `all_msgs` 必须是 L1 之后的消息列表**（offload_and_snip 已替换大结果为预览），否则估算偏高、过早触发 L2。
- `estimate_messages(messages) -> int`：纯 `math.ceil(message_chars(messages) / 3.5)`，摘要请求自检用（F23）。
- **摘要请求不更新锚点**（spec F14 隐含 + 防污染）：摘要请求的 usage 反映的是「摘要这组消息」的消耗，不能拿来当主对话锚点；锚点只由主对话路径在 `_stream_once` 完成后更新。ContextManager / Agent 负责在摘要路径**不调** `usage_to_anchor` 更新外部锚点。

### newcode/context/window.py
**职责：** `get_context_window_for_model(model, protocol)` 四级解析 + 静态能力表。
**对外接口：** `get_context_window_for_model`、`CAPABILITIES`。
**依赖：** `os`、`constants`。
**关键点：**
- 第 1 级：`os.environ.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS")` 非空 → `int(...)`（解析失败跳过该级）。
- 第 2 级：`"[1m]" in model` → `ONE_M_WINDOW`。
- 第 3 级：`CAPABILITIES.get(model)` 存在且 `≥ CAPABILITY_TABLE_FLOOR` → 取表值。
- 第 4 级：`protocol == "anthropic"` → `DEFAULT_WINDOW_ANTHROPIC`；`"openai"` → `DEFAULT_WINDOW_OPENAI`；其余 → `DEFAULT_WINDOW_ANTHROPIC`（保守默认）。
- 永不抛：任一级异常 `try/except` 跳到下一级。

### newcode/context/capabilities.py
**职责：** 静态能力表（已知大上下文模型，≥100K）。
**对外接口：** `CAPABILITIES: dict[str, int]`。
**依赖：** 无。
**关键点：** 初始收录少量已知模型（如 `claude-sonnet-4-20250514[1m]`→1_000_000 但其实会被第 2 级先命中、`gpt-4o`→128_000 等——**仅收录 ≥100K 的**，<100K 不进表）。表值旁用注释标「来源：官方文档 / 探测值 + 时间」。`scripts/probe_context_window.py` 产出的数字由开发者手工追加到此文件并提交。

### newcode/context/session.py
**职责：** 会话 id 生成 + 落盘目录管理。
**对外接口：** `SessionPaths` 类。
**依赖：** `os`、`time`、`secrets`、`pathlib`。
**关键点：**
- `SessionPaths(cwd)`：`session_id = f"{int(time.time())}-{secrets.token_hex(4)}"`；`base = cwd/.newcode/sessions/<session_id>/tool-results`。
- `path_for(tool_use_id: str) -> Path`：`base / (tool_use_id or _fallback_name())`；`_fallback_name` 用自增序号（`f"unknown-{self._seq}"`，`self._seq` 锁保护或用 `itertools.count`）。
- `ensure_dir()`：`base.mkdir(parents=True, exist_ok=True)`，已存在不报错。
- 会话 id 进程内唯一、不持久化、退出不清理（F33）。

### newcode/context/replacement.py
**职责：** `ContentReplacementState` 账本。
**对外接口：** `ContentReplacementState` 类。
**依赖：** `asyncio`。
**关键点：** 见数据结构。所有方法 `async with self._lock`。`decision_for` 返回 `tuple[Literal["replaced","kept","unseen"], str | None]`。落盘失败时调用方**不调** `record_*`，下轮 `decision_for` 返回 `unseen` 重评（F5b）。

### newcode/context/offload.py
**职责：** `offload_and_snip` 第一层纯字符串替换。
**对外接口：** `offload_and_snip(messages, state, session_paths) -> int`。
**依赖：** `Message`、`ContentReplacementState`、`SessionPaths`、`constants`、`pathlib`、`asyncio`。
**关键点：**
- 遍历 `messages`，对每条 `role=="tool"` 消息：
  - 取 `tool_use_id = msg.tool_use_id or msg.tool_call_id or ""`。
  - 先查账本：`decision, preview = await state.decision_for(tool_use_id)`：
    - `replaced` → `msg.content = preview`（复用冻结字符串，F5d），continue；
    - `kept` → continue（保留原文，永不翻转）；
    - `unseen` → 进入 F1/F2 评估。
  - **F2a 三步原子**（对未决策的项，按 F2 单轮聚合逻辑）：
    1. 建候选列表（本条 RoleTool 消息的所有未决策 tool_result 项——注意当前 `Message` 是单条 tool 消息含一个结果，F2 的「列表」语义在 NewCode 里体现为**同一回合的多条 tool 消息**，plan 把同回合多条 tool 消息视为一组做聚合），按字节倒序。
    2. 先把超 `SINGLE_RESULT_THRESHOLD` 的项落盘（F1）；再按 `AGGREGATE_LIMIT` 继续落盘下一项（F2）直到剩余聚合达标。
    3. 每项落盘：`path = session_paths.path_for(id)`；`path.write_bytes(content.encode("utf-8"))` 用 `wx` 模式（`open(path, "xb")`，`FileExistsError` 跳过写入复用）；**落盘成功**才 `msg.content = build_preview(content, path)` 并 `await state.record_replaced(id, msg.content)`；**落盘失败**（`OSError`）→ 保持原文 + 不写账本 + stderr 告警，该项下轮重评。
- `build_preview(content, path) -> str`：取前 20 行（`content.splitlines()[:20]`）再按字节截 2048（`head.encode("utf-8")[:2048].decode("utf-8", errors="ignore")`），拼成含「原始字节数 + 头部预览 + 落盘路径 + 重读提示」四项的固定格式字符串。
- **毫秒级、不调 LLM**（N1）：落盘 I/O 用 `await asyncio.to_thread(path.write_bytes, data)` 避免阻塞 loop 超 100ms。
- 返回被替换的项数。

**F2 聚合的会话内单位澄清**：NewCode 的 `ConversationManager` 每个 tool 结果是**独立一条 `Message(role="tool")`**（`manager.py:90`），不是一条消息挂列表。故 F2「一条 RoleTool 消息的 tool_results 列表」在实现层映射为「**同一 assistant(tool_use) 回合对应的多条 tool 消息**」。`offload_and_snip` 需先按 `tool_use_id` 所属回合把 tool 消息分组，再对每组做 F2 聚合判断。回合归属：通过 assistant 消息的 `tool_calls[].id` 与 tool 消息的 `tool_use_id`/`tool_call_id` 配对确定。

### newcode/context/files.py
**职责：** `FileTracker` 最近文件追踪。
**对外接口：** `FileTracker` 类、`TrackedFile` 数据类。
**依赖：** `asyncio`、`time`。
**关键点：** 见数据结构。`record` 在 Agent 工具执行成功后、`add_tool_result` 前同 task `await` 调用（F19a）。纯净字节：`read_file` 成功时剥离截断提示（`truncated=True` 时去掉末尾 `\n...（已截断…）` 段）。锁保护（N2）。

### newcode/context/recovery.py
**职责：** `RecoveryBuilder` 恢复段三块（文本片段，非 Message 列表）。
**对外接口：** `RecoveryBuilder` 类、`RecoveryBundle` 数据类。
**依赖：** `FileTracker`、`ToolDefinition`、`constants`、`SkillRegistry`。
**关键点：**
- `build(file_tracker, tool_defs) -> RecoveryBundle`：
  - `file_snapshots_text`：`file_tracker.recent(MAX_RECENT_FILES)`，每个 content 按 `int(PER_FILE_TOKEN_BUDGET * ESTIMATE_CHARS_PER_TOKEN)` 字符截头部，超长加 `(content truncated)`，拼成多行文本块（**一段 str，不是多条 Message**）。
  - `tools_declaration_text`：**直接用传入的 `tool_defs` 引用**序列化文案（F17 `id(defs)` 一致），不重算不选子集，返回 str。
  - `boundary_notice_text`：固定文案 str（F18）。
  - Skill 注入分支：`if skill_registry and skill_registry.list():` 按 `SKILL_RECOVERY_BUDGET` 注入文本——**当前为空实现 + TODO 注释**（F31），registry 始终空。
  - **三块都是 str**，由 `Summarizer.run_summary` 拼进单条 user 消息 content（见 summarize.py 的「合并消息」决策）。
- 工具一致性以 `Agent.run` 单次迭代为粒度：`tool_defs` 在迭代开头一次性算出，`RecoveryBuilder.build` 与 `provider.stream` 共用同一引用。

### newcode/context/summarize.py
**职责：** `Summarizer` 第二层 LLM 全量摘要。
**对外接口：** `Summarizer` 类、`SummarizeConfig`、`CompactOutcome`。
**依赖：** `Provider`、`RecoveryBuilder`、`MessageGroupDropper`、`Message`、`ToolDefinition`、`constants`、`tokens`（纯函数）。
**关键点：**
- `summarize(messages, config, context_window, recovery_builder, tool_defs) -> CompactOutcome`：
  1. 构造摘要 prompt：固定九段结构说明 + `<analysis>` 草稿要求 + `<summary>` 正文要求 + 明确「禁止调用工具」。把旧块消息（除最近 `keep_recent_turns` 轮）作为待摘要内容塞进 user 消息。
  2. **摘要请求不传 tools**（F8）：`PromptPayload(stable_prompt=摘要指令, env_segment="", messages=旧块, reminders=[], tools=None)`。
  3. 摘要请求自检（F23）：`estimate_messages(摘要prompt)` 若 `> context_window - SUMMARY_RESERVE_TOKENS - config.safety_margin` → 直接进 F27 丢组重试，不白白撞墙。
  4. 调 `provider.stream(payload)` 独立请求，累积 text。**max_tokens 问题**：Anthropic 写死 4096（`anthropic.py:108`），九段摘要可能超——plan 决定**在摘要请求时通过 payload 透出一个 `max_output_tokens` 字段**（PromptPayload 新增可选字段，默认 None=用 provider 默认；摘要请求设 8192），provider 层读取该字段覆盖 4096。OpenAI 侧未设 max_tokens，沿用端点默认即可。
  5. 解析返回：只留 `<summary>...</summary>` 标签内内容（`<analysis>` 丢弃，F9）。
  6. 失败识别：若 `StreamEvent.err` 且 `isinstance(err, PromptTooLongError)`（见 provider 改动）→ 走 F27；其他错误 → 直接返回失败 `CompactOutcome(success=False, failure_reason=…)`。
  7. 成功：构造新消息列表。**关键：摘要 + 恢复段合并成单条 user 消息**（避免连续 user 触发 Anthropic roles-must-alternate 400）：
     - `summary_user_content = 摘要正文 + "\n\n" + recovery.file_snapshots_text + "\n\n" + recovery.tools_declaration_text + "\n\n" + recovery.boundary_notice_text`
     - `recent_tail = pick_recent_tail(旧块之后的近期消息)`（F11 双下界择宽 + F12 不拆对 + **role 衔接修正**，见下）
     - 新消息列表 = `[Message(role="user", content=summary_user_content)] + 衔接修正后的 recent_tail`
     - **role 衔接修正**（pick_recent_tail 内做）：若 `recent_tail` 首条是 `role=="user"`，则在新列表的摘要 user 与 recent_tail 之间**插入一条 `Message(role="assistant", content="")` 占位**，保住 user/assistant 交替约束（Anthropic 强制交替）。若 recent_tail 首条是 assistant/tool 则无需占位。
  8. 返回 `CompactOutcome(triggered=True, before/after_tokens, replaced_results=0, success=True, messages=新列表)`。
- **F27 丢组重试**（三路径共用，由 `MessageGroupDropper` 承载）：摘要请求自身 PTL 时，`group_by_user` 分组 → 最多 `PTL_DIRECT_RETRY_LIMIT` 次每次丢最旧 1 组重试 → 不行再每次丢 `ceil(剩余×PTL_DROP_RATIO)`（至少 1 组）直到成功或耗尽；耗尽仍失败 → 返回失败 outcome。不发送空 messages 摘要请求。
- **错误隔离**（N11）：`summarize` 整体 `try/except Exception` 兜底，单次失败不崩进程，返回失败 outcome。

### newcode/context/dropper.py
**职责：** `MessageGroupDropper` F27 丢消息组。
**对外接口：** `MessageGroupDropper` 类。
**依赖：** `Message`、`constants`、`math`。
**关键点：**
- `group_by_user(messages)`：扫消息，遇 `role=="user"`（非 tool_result 包装的 user——注意 Anthropic provider 把 tool 角色翻成 user，实现层按 `Message.role` 原始值分组，`role=="tool"` 归到其前的 user 组）开新组；返回 `list[list[Message]]`。
- `drop_oldest(groups, n)`：`groups[n:]`。
- `drop_ratio(groups, ratio)`：`n = max(1, math.ceil(len(groups)*ratio))`；`groups[n:]`。
- 保证不拆 tool_use/tool_result 对：分组单位本身就是「一条 user + 其后所有 assistant/tool」，整组丢弃天然保对（F12）。

### newcode/context/skill.py
**职责：** `Skill` + `SkillRegistry` 骨架。
**对外接口：** `Skill`、`SkillRegistry`。
**依赖：** 无（纯数据 + 容器）。
**关键点：** `SkillRegistry` 用 dict 存储；`register/get/list` 简单实现；`total_tokens` 当前总返回 0（内容加载 TODO）。`RecoveryBuilder` 持可选引用，注入分支空实现 + `# TODO(ch08): Skill 内容加载待后续章节` 注释。

### newcode/context/manager.py
**职责：** `ContextManager` 窄入口，编排 L1+L2、手动、紧急、熔断收尾；`AutoCompactGate` 仅自动路径连续失败闸。
**对外接口：** `ContextManager` 类、`AutoCompactGate` 类。
**依赖：** 上述所有 context 子模块、`ConversationManager`、`Provider`、`Message`、`ToolDefinition`、`asyncio`、`logging`。
**关键点：**
- 持 `asyncio.Lock`（会话级，F34 互斥）、`ContentReplacementState`、`FileTracker`、`Summarizer`、`SessionPaths`、`RecoveryBuilder`、`AutoCompactGate`、`context_window`（构造时算一次：`get_context_window_for_model(model, protocol)`）、`_usage_anchor: int=0`、`_anchor_msg_len: int=0`（外部锚点状态，纯函数估算用）。**不持估算器实例**——tokens 是纯函数模块。
- `manage_context(tool_defs)`（自动，每轮前）：
  - **入口 sanity check**：`context_window <= SUMMARY_RESERVE_TOKENS + AUTO_SAFETY_MARGIN`（即 ≤33000）时，自动阈值 `窗口-33000` 为非正数会导致每轮都触发摘要死循环；此时 `logging.warning("context_window too small for auto-compact, skipping layer2")` 并**跳过自动 layer2**，仅跑 L1 后返回。
  - **自动闸检查**：`if self._auto_gate.auto_disabled():` → 自动触发已被停，仅跑 L1 后返回（不弹菜单，静默跳过 L2）。
  - `async with self._lock:`：
    - L1：`replaced = await offload_and_snip(self.conv.get_messages_ref(), self._state, self._session)`——**注意要拿原始 `_messages` 引用不是副本**（见 ConversationManager 改动）。
    - L2 自动检查：`estimate_tokens(self._usage_anchor, self.conv.get_messages_ref(), self._anchor_msg_len)` ≥ `context_window - SUMMARY_RESERVE_TOKENS - AUTO_SAFETY_MARGIN` → 触发摘要。（**估算必须用 L1 之后的消息列表**，否则偏高过早触发。）
    - 触发则发 `Event(EventType.CONTEXT_COMPACTING, "auto")`（新增事件，供 TUI 显示「正在压缩...」F24a），调 `summarizer.summarize(...)`，成功则 `self.conv.replace_history(outcome.messages)` + `reset_anchor()` + `self._auto_gate.record_auto_success()` + 记日志；失败（行动内 3 次重试都没成，由 summarize 内部 F27 处理后返回失败 outcome）→ `self._auto_gate.record_auto_failure()` + **熔断收尾**：发 `Event(EventType.COMPACT_FAILED, outcome)` 交 TUI 弹菜单（F28 自动路径）。
  - 未达阈值不做任何事。
- `compact_now()`（手动）：`async with self._lock:`（等主循环释放，F34）；跳阈值/跳自动闸/跳 L1，直接 `summarizer.summarize(messages, SummarizeConfig(safety_margin=MANUAL_SAFETY_MARGIN, keep_recent_turns=6), ...)`；成功→替换历史+`reset_anchor()`+`self._auto_gate.reset_on_manual_success()`（解除自动闸）+展示前后 token（经事件）；失败→发 `COMPACT_FAILED` 交 TUI 弹菜单（F28 手动路径，显示失败原因）。**手动失败不计自动闸**。
- `force_compact(tool_defs)`（紧急）：
  - 先强制 L1：`await offload_and_snip(...)` 挪走 50K+（F25）。
  - 再摘要：`summarizer.summarize(..., SummarizeConfig(safety_margin=MANUAL_SAFETY_MARGIN, keep_recent_turns=3), ...)`（更激进，保留更少最近轮）。
  - 成功→`conv.replace_history` + `reset_anchor()`（F25a）+ 重估；估算 `< context_window - MANUAL_SAFETY_MARGIN` 才允许 Agent 重试原请求，否则视为不可恢复返回失败 outcome。
  - 失败（含 F27 丢组耗尽）→ 返回失败 outcome；**单次行动 3 次重试上限由 Agent 持 `emergency_retried` 标记控制**，ContextManager 本身不累计；**紧急失败不计自动闸**（不跨种类）。
- `update_anchor(usage, conv_len)`：`self._usage_anchor = usage_to_anchor(usage)`、`self._anchor_msg_len = conv_len`。**仅主对话路径调用**（Agent `_stream_once` 成功后）；摘要路径不调（防污染锚点）。
- `reset_anchor()`：`self._usage_anchor=0`、`self._anchor_msg_len=0`（摘要/紧急成功后用，强制重估）。
- **熔断收尾菜单**（F28 三路径统一）：失败 outcome 经 `COMPACT_FAILED` 事件交 TUI；TUI 用 `_ask_choice` 弹菜单（重试/分组丢弃重试（标明丢量）/放弃/退出/其他说明）。菜单选中「分组丢弃重试」→ 调 `MessageGroupDropper` 按 F28 菜单分支步进（每次 2 组×3 次→每次 20%）再试；此为菜单驱动的用户主动重试，属**新的压缩行动**，又有自己的 3 次。
- **N10 日志**：每次压缩 `logging.info` 记触发原因/前后 token/被替换结果数。
- **N11 错误隔离**：`manage_context`/`compact_now`/`force_compact` 整体 `try/except Exception` 兜底，异常不抛给主循环。

### newcode/context/autogate.py（或并入 manager.py）
**职责：** `AutoCompactGate` 仅自动路径连续失败闸。
**关键点：** `_consecutive_failures: int=0`；`record_auto_success`→清零；`record_auto_failure`→+1；`auto_disabled`→`_consecutive_failures >= AUTO_GATE_LIMIT(3)`；`reset_on_manual_success`→清零（手动 /compact 成功解除闸）。**仅自动路径读写**，手动/紧急不碰。防 provider 持续故障时每轮弹菜单轰炸。

### newcode/conversation/manager.py（修改）
**改动 1**：新增 `get_messages_ref() -> list[Message]`：返回 `self._messages`（**原始引用**，非副本），供 `offload_and_snip` 就地改写 content。保留 `get_context()` 返回副本不变（Agent assemble 仍用副本，但副本取自压缩后的原始列表）。
**改动 2**：新增 `replace_history(new_messages: list[Message]) -> None`：`self._messages = list(new_messages)`（第二层摘要替换整段历史）。
**改动 3**：`_trim`（`manager.py:112`）修正为不破坏 tool_use/tool_result 配对 + 降级条数兜底：裁剪单位改为「以 user 消息分界的组」（复用 `MessageGroupDropper.group_by_user` 或本地等价实现），从头部整组丢弃，天然保对；只在消息条数远超 `max_turns*某倍数` 时触发（兜底，主裁剪权已交 context）。原 `add_assistant` 后触发 `_trim` 保留，但语义改为条数兜底。

### newcode/agent/agent.py（修改）
**改动 1**：`__init__` 新增可选 `context_mgr: ContextManager | None = None`、`file_tracker: FileTracker | None = None`，以及 `self._run_lock = asyncio.Lock()`（**会话级互斥锁，F34**）。无 context_mgr 时 Agent 行为与 ch07 完全一致（N8 向后兼容），`_run_lock` 仍存在但无实质竞态。
**改动 1a（互斥锁 scope，F34 关键）**：`run()` 入口 `async with self._run_lock:` **贯穿整轮**（从 manage_context 到工具回填到流结束），不是只锁 manage_context。这样手动 `run_force_compact` 持同一锁等 run 结束，避免它在「manage_context 释放锁→该轮 add_tool_result 之间」这个窗口 `replace_history`，导致流回来写 tool_result 到新历史（新历史无对应 tool_use）→ 配对断裂。紧急 `force_compact` 在 run 内部调用、**已持锁**，用 `asyncio.Lock` 的不可重入特性需注意——plan 决定紧急路径调的是 `context_mgr.force_compact`（ContextManager 的 `_lock` 与 Agent 的 `_run_lock` 是**两把不同的锁**：`_run_lock` 管 run 与手动入口互斥，ContextManager `_lock` 管 context 内部三个方法互斥），紧急 force_compact 经 `context_mgr._lock` 保护、与 manage_context 同锁故不会并发，而 `_run_lock` 在 run 内全程持有故手动入口进不来，无死锁。
**改动 2**：`run()` 循环体（`agent.py:94`）每轮 `TURN_START` 之后、`assemble`（`agent.py:106`）之前插入：
```
if self._context_mgr is not None:
    await self._context_mgr.manage_context(tool_defs)
```
**改动 3**：工具执行回填（`agent.py:306` `add_tool_result` 前）插入文件追踪：
```
if self._file_tracker is not None and tc.tool_name == "read_file" and sr.result.status == "ok":
    await self._file_tracker.record(path, _strip_truncation(sr.result.output))
await self.conv.add_tool_result(tc, sr.result)
```
（同 task 顺序，F19a）。`_strip_truncation` 剥离 read_file 截断提示。
**改动 4**：`provider.stream` 错误处理（`agent.py:139`）识别 PTL（用 `isinstance` 判定 `PromptTooLongError` 哨兵）：
```
if _stream_error is not None and isinstance(_stream_error, PromptTooLongError):
    if self._context_mgr is not None and not emergency_retried:
        emergency_retried = True
        outcome = await self._context_mgr.force_compact(tool_defs)
        if outcome.success:
            # 用新历史重组 payload 重试本轮（不进下一 turn，重跑当前 turn 的请求）
            payload = self._assembler.assemble(..., self.conv.get_context(), ...)
            stream = self.provider.stream(payload)
            # 重新消费流（重构为内层循环或 goto）
        else:
            yield Event(EventType.ERROR, PromptTooLongError("紧急压缩失败，上下文不可恢复"))
            yield Event(EventType.DONE, StopReason.STREAM_ERROR)
            return
    # emergency_retried 已 True 或无 context_mgr → 原错误处理
```
`emergency_retried` 为 `run()` 内局部变量，保证一次迭代内只重试一次（F26）。**重试粒度**：ForceCompact 是一次压缩行动（内部 summarize 已含 F27 的 3 次直接重试 + 比例丢组），其整体成功/失败由 `outcome.success` 体现；`emergency_retried` 控制的是「外层 ForceCompact 动作一次迭代内只发生一次」。
**改动 5**：每轮主对话请求成功后 `agent.py:145` `TOKEN_USAGE` 事件处补 `self._context_mgr.update_anchor(_token_usage, len(self.conv.get_messages_ref()))`。**仅在主对话路径且流成功时调**（摘要路径不调，防污染锚点，见 tokens.py）。
**改动 6**：暴露 `async def run_force_compact(self, tool_defs: list[ToolDefinition]) -> CompactOutcome`：**入口先 `async with self._run_lock:`**（等主循环 run 释放，F34），再调 `self._context_mgr.compact_now()`，供 TUI `/compact` 调用。注意 `compact_now` 内部还持 ContextManager 自己的 `_lock`——顺序是「先 `_run_lock`（等 run）→ 再 `_lock`（等 context 内部）」，不与 run 内「`_run_lock` → manage_context 内 `_lock`」的顺序冲突，无死锁。
**改动 7**：新增 `EventType.CONTEXT_COMPACTING`、`EventType.COMPACT_FAILED`（见 events.py 改动），Agent 在压缩时透传给 TUI。

### newcode/agent/events.py（修改）
新增两个事件类型：
```
CONTEXT_COMPACTING = "context_compacting"   # payload: str ("auto"/"manual"/"force")，TUI 显示提示
COMPACT_FAILED = "compact_failed"           # payload: CompactOutcome，TUI 弹熔断菜单
```

### newcode/llm/__init__.py（修改）
**改动**：新增哨兵异常 `class PromptTooLongError(Exception):`（docstring「Provider 上报上下文超出窗口时统一抛出的哨兵异常」）。provider 适配层包装 `prompt_too_long` 类错误时用该异常，经 `yield StreamEvent(err=wrapped)` 投递；`wrapped.__cause__ = orig` 保留原 SDK 异常。

### newcode/provider/anthropic.py（修改）
**改动 1**：`stream` 异常处理（`anthropic.py:195`）识别 PTL：`AnthropicAPIError` 中 `status_code == 400` 且消息含 `prompt is too long` / `context length` 等关键词 → `wrapped = PromptTooLongError(...)`，`wrapped.__cause__ = e`，`yield StreamEvent(err=wrapped)`；其余 `AnthropicAPIError` 维持原 `ProviderError(f"Anthropic API 错误: {e}")`。
**改动 2**：`max_tokens` 可配（`anthropic.py:108`）：读 `payload.max_output_tokens`（PromptPayload 新字段，默认 None）；None 时维持 4096，有值时用该值。摘要请求设 8192。

### newcode/provider/openai.py（修改）
**改动**：`stream` 异常处理（`openai.py:169`）识别 PTL：`OpenAIAPIError` 中 `status_code == 400` 且 `code == "context_length_exceeded"`（或消息含 `maximum context length`）→ `wrapped = PromptTooLongError(...)`，`wrapped.__cause__ = e`，`yield StreamEvent(err=wrapped)`；其余维持原样。OpenAI 侧 max_tokens 沿用端点默认（不改）。

### newcode/prompt/assembler.py（修改）
**改动**：`PromptPayload` 新增 `max_output_tokens: int | None = None` 字段（默认 None，普通对话不设；摘要请求设 8192）。`assemble` 透传该字段。

### newcode/tui/app.py（修改）
**改动 1**：抽出 `BUILTIN_COMMANDS` 注册表（F21）：`/exit`、`/plan`、`/do`、`/delete-plan`、`/normal`、`/exit-plan`、`/compact` 各自注册处理函数；`_process_input`（`app.py:193`）改为「以 `/` 开头 → 查注册表 → 命中执行/未命中走未知命令兜底（给可用命令提示，不发给 LLM）」。原有命令行为不变（N7）。命令路径不写入 conversation，结果只通过系统消息展示。
**改动 2**：新增 `/compact` 处理：调 `self.agent.run_force_compact(tool_defs)`，展示 `CompactOutcome` 前后 token（F24）；失败 outcome 弹熔断菜单（F28 手动路径）。
**改动 3**：`_consume_agent_events`（`app.py:323`）处理新事件：
- `CONTEXT_COMPACTING`：按 payload（auto/manual/force）显示「正在压缩上下文...」/「上下文撞墙，自动压缩中...」（F24a/F24b）；完成后显示「已压缩，token 从 X 降至 Y」。
- `COMPACT_FAILED`：调 `_ask_choice` 弹熔断菜单（重试/分组丢弃重试（标明丢量）/放弃/退出/其他说明），菜单选中驱动 `MessageGroupDropper` 重试（F28）。
**改动 4**：`_toolbar`（`app.py:154`）把 `/compact` 加入命令提示文案。
**改动 5**：`Turn {x+1}/10` 硬编码（`app.py:389`）与 `_MAX_AGENT_TURNS`（`agent.py:19`）同步——本章不改 max turns，但若 context 改动触及此处需注意（plan 标注，不主动改）。
**改动 6**：`_consume_agent_events` 的 `max_retries=3`（`app.py:330`）是**流错误重试**，与 F28 压缩行动重试是两回事——plan 显式注释区分，不合并。

### newcode/main.py（修改）
**改动**：`_amain`（`main.py:92`）构造 `ContextManager` 并注入 Agent：
```
from newcode.context import ContextManager, FileTracker, SkillRegistry
file_tracker = FileTracker()
skill_registry = SkillRegistry()   # 骨架，空
context_mgr = ContextManager(
    provider, conversation, provider.model, provider_config.protocol,
    file_tracker, skill_registry,
)
agent = Agent(provider, conversation, registry, stable_prompt, env_segment,
              permission=permission, is_interactive=is_interactive,
              context_mgr=context_mgr, file_tracker=file_tracker)
```
（`provider_config.protocol` 从 `main.py:75` 已取的 `provider_config` 拿。）

### scripts/probe_context_window.py（新建）
**职责：** 独立探测脚本，估算模型上下文长度。
**依赖：** `anthropic` / `openai` SDK、`argparse`。
**关键点：**
- `python scripts/probe_context_window.py --protocol anthropic --model <name> [--base-url …] [--api-key …]`。
- 二分逼近：构造逐步增长的 prompt（填充字符）发请求，直到 provider 返回 `prompt_too_long`，二分定位边界。
- 产出的数字打印到 stdout，附「这是经验下界，手工填入 `newcode/context/capabilities.py`」提示。
- **不在 Agent 主流程、不被 import、不被 F29 引用**（F30）。

## 模块交互

### 每轮自动压缩时序（manage_context）

```
Agent.run() 第 turn 轮：  [async with self._run_lock 贯穿整轮，F34]
  cancel 检查 → TURN_START 事件
  ↓
  [新] if context_mgr: await context_mgr.manage_context(tool_defs)
      │ async with ContextManager._lock
      ├─ sanity check: context_window ≤ 33000? → 跳过 L2，仅 L1，返回
      ├─ 自动闸: auto_gate.auto_disabled()? → 跳过 L2，仅 L1，返回（静默）
      ├─ L1: offload_and_snip(conv.get_messages_ref(), state, session)
      │       ├─ 遍历 tool 消息 → 查账本 → 未决策项按 F1/F2 落盘+改写+写账本（三步原子）
      │       └─ 返回 replaced 计数
      ├─ L2 自动检查: estimate_tokens(usage_anchor, conv.get_messages_ref(), anchor_msg_len) ≥ 窗口-33000?
      │   否 → 返回（不动历史）
      │   是 → 发 CONTEXT_COMPACTING("auto") 事件
      │        → summarizer.summarize(conv.get_context(), auto_config, 窗口, recovery_builder, tool_defs)
      │           ├─ 摘要请求自检（F23）→ 超 → MessageGroupDropper 丢组
      │           ├─ provider.stream(无tools, max_output_tokens=8192)
      │           ├─ PTL → F27 丢组重试（3次直接→比例丢）→ 耗尽返回失败
      │           └─ 成功 → 构造 [摘要+恢复 合并单条user] + [role衔接修正] + [近期原文] 新列表
      │        成功 → conv.replace_history(新列表) + reset_anchor() + auto_gate.record_auto_success() + 日志
      │        失败 → auto_gate.record_auto_failure() + 发 COMPACT_FAILED(outcome) 事件（TUI 弹菜单，F28 自动路径）
      └─ 释放 ContextManager._lock（_run_lock 仍持有到流结束）
  ↓
  assemble(conv.get_context(), ...) → provider.stream → …（原逻辑）
  ↓
  请求成功后（仅主对话路径）: context_mgr.update_anchor(usage, len(conv.get_messages_ref()))  [新]
  ↓ run 结束释放 _run_lock
```

### 紧急压缩时序（force_compact，请求撞 PTL）

```
Agent.run() 第 turn 轮 provider.stream 消费：  [_run_lock 仍持有]
  StreamEvent.err 且 isinstance(err, PromptTooLongError)
  ↓
  if context_mgr and not emergency_retried:
      emergency_retried = True
      outcome = await context_mgr.force_compact(tool_defs)
        │ async with ContextManager._lock（与 _run_lock 不同；run 内已持 _run_lock 故手动入口进不来，无死锁）
        ├─ 强制 L1: offload_and_snip() 挪走 50K+
        ├─ summarizer.summarize(..., force_config(keep_recent=3))
        │   └─ 内部 F27 处理摘要自身 PTL（单次行动 3 次重试）
        ├─ 成功 → conv.replace_history + reset_anchor()
        │        → 重估 estimate_tokens(0, conv, 0) < 窗口-3000? 允许重试 / 否则不可恢复失败
        └─ 失败 → 返回失败 outcome（不计自动闸，不跨种类）
      if outcome.success:
          重组 payload（用新历史）→ provider.stream 重试本轮（不进下一 turn）
      else:
          ERROR + DONE(STREAM_ERROR)  # 不二次 ForceCompact（F26）
  else:
      原错误处理（ERROR + STREAM_ERROR）
```

### 手动 /compact 时序

```
REPL 输入 /compact → BUILTIN_COMMANDS["/compact"] → agent.run_force_compact(tool_defs)
  │ async with self._run_lock（等主循环 run 结束，F34）
  → context_mgr.compact_now()
      │ async with ContextManager._lock
      ├─ 跳 L1/阈值/自动闸
      ├─ summarizer.summarize(..., manual_config(余量3000))
      ├─ 成功 → conv.replace_history + reset_anchor() + auto_gate.reset_on_manual_success()（解除自动闸）
      │        → 返回 outcome（TUI 显示前后 token，F24）
      └─ 失败 → 返回失败 outcome（不计自动闸；TUI 弹菜单，F28 手动路径）
```

### 数据流（第一层就地改写）

```
工具执行返回 ToolResult(output=大内容)
  → conv.add_tool_result(tc, result)  # 追加 Message(role="tool", content=大内容)
  [同 task，在此之前] file_tracker.record(path, 纯净content)  # read_file 时
  ↓
下一轮 manage_context:
  offload_and_snip 遍历到该 tool 消息
  → decision_for(id) = unseen
  → 字节 > 50000? 落盘 .newcode/sessions/<sid>/tool-results/<id> (wx, 已存在跳过)
  → 落盘成功 → msg.content = build_preview(content, path)  # 就地改写原始 _messages
              → state.record_replaced(id, msg.content)
  → assemble 用 conv.get_context()（副本，但取自改写后的原始列表）→ provider 收到预览体
```

## 文件组织

```
newcode/
├── context/                      [新增子包]
│   ├── __init__.py               — 门面导出
│   ├── constants.py              — 全部硬编码阈值
│   ├── tokens.py                 — 纯函数 estimate_tokens/usage_to_anchor/estimate_messages/message_chars
│   ├── window.py                 — get_context_window_for_model 四级解析
│   ├── capabilities.py           — 静态能力表 CAPABILITIES
│   ├── session.py                — SessionPaths（会话 id + 落盘目录）
│   ├── replacement.py            — ContentReplacementState 账本（锁保护）
│   ├── offload.py                — offload_and_snip 第一层（三步原子、幂等 wx、预览体）
│   ├── files.py                  — FileTracker + TrackedFile（锁保护）
│   ├── recovery.py               — RecoveryBuilder + RecoveryBundle（文本片段，工具同引用）
│   ├── summarize.py              — Summarizer + SummarizeConfig + CompactOutcome（合并单条user消息 + role衔接）
│   ├── dropper.py                — MessageGroupDropper（F27 丢消息组）
│   ├── autogate.py               — AutoCompactGate（仅自动路径连续失败闸）
│   └── skill.py                  — Skill + SkillRegistry（骨架，TODO）
├── conversation/
│   └── manager.py                — 修改：get_messages_ref / replace_history / _trim 不拆对
├── agent/
│   ├── agent.py                  — 修改：_run_lock 贯穿整轮 / manage_context 钩子 / 文件追踪 / PTL 兜底 / run_force_compact / update_anchor
│   └── events.py                 — 修改：CONTEXT_COMPACTING / COMPACT_FAILED 事件
├── provider/
│   ├── base.py                   — （PromptPayload 字段在 assembler）
│   ├── anthropic.py              — 修改：PTL 识别（PromptTooLongError）/ max_output_tokens 透传
│   └── openai.py                 — 修改：PTL 识别（PromptTooLongError）
├── llm/
│   └── __init__.py               — 修改：新增 PromptTooLongError 哨兵异常
├── prompt/
│   └── assembler.py              — 修改：PromptPayload.max_output_tokens
├── tui/
│   └── app.py                    — 修改：BUILTIN_COMMANDS 注册表 / /compact / 压缩 UX / 熔断菜单
└── main.py                       — 修改：构造 ContextManager 注入 Agent

scripts/
└── probe_context_window.py       [新建] 独立探测脚本，不在主流程

tests/
├── test_context_tokens.py        — 纯函数 estimate_tokens/usage_to_anchor/estimate_messages 锚定/增量/重置
├── test_context_window.py        — 四级解析各分支
├── test_context_offload.py       — F1/F2/F2a/F3/F4/F5 单轮聚合/三步原子/幂等/决策冻结
├── test_context_replacement.py   — 账本四子项 + 并发安全
├── test_context_files.py         — FileTracker 记录/倒序/并发安全
├── test_context_recovery.py      — 恢复段三块（文本片段）+ 工具同引用 + 文件截断
├── test_context_summarize.py     — 九段结构/草稿丢弃/不传工具/摘要+恢复合并单条user/role衔接占位/近期原文双下界不拆对
├── test_context_dropper.py       — 分组/丢最旧/比例丢/不拆对
├── test_context_autogate.py      — AutoCompactGate 连续失败闸/成功清零/手动解闸/仅自动路径
├── test_context_manager.py       — manage_context 自动阈值/下界 sanity check/compact_now 跳阈值/force_compact/熔断收尾/自动闸联动/互斥锁
├── test_context_skill.py         — Skill/SkillRegistry 骨架 + 注入空实现
├── test_context_session.py       — 会话 id 格式 + 落盘目录
├── test_agent_context.py         — Agent 集成：_run_lock 贯穿/每轮前压缩钩子/PTL 兜底 emergency_retried/文件追踪回填/update_anchor
├── test_tui_compact.py           — /compact 路由（run_force_compact）/压缩 UX/熔断菜单（mock agent）
├── test_provider_ptl.py          — PromptTooLongError 哨兵两家识别 + __cause__ 保留
└── test_conversation_manager.py  — 修改：get_messages_ref / replace_history / _trim 不拆对（原有用例不回归）
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 第一层计量口径 | 字节 `len(content.encode("utf-8"))` | 第一层是纯字符串替换不调 LLM，字节确定性强、毫秒级、无需估算；token 估算只留给第二层触发判断。spec F1/F2 + 字节口径说明 |
| Token 估算 | 锚定真实 usage + 字符/3.5 增量，锚点替换不累加 | 锚定 provider 真实计数比纯字符估算准得多；探查确认 `TokenUsage` 已在 provider 层解析可拿到（`base.py:14`、流尾 `StreamEvent.usage`）。spec F13/F14 |
| 第一层就地改写作用点 | 改 `ConversationManager._messages` 原始列表（非副本） | `get_context` 返回副本（`manager.py:108`），Agent assemble 拿副本；就地改写须动原始列表才能让下一轮副本反映压缩。新增 `get_messages_ref` 暴露原始引用 |
| 账本原子性 | 落盘→改写content→写账本三步串行，任一失败全不发生；锁保护读-决策-写 | 防「已 Seen 但 replacement 没写」中间态；防同 id 出现两个预览版本。spec F2a/F5/N2 |
| 落盘幂等 | 文件名 = `tool_use_id`，`open(path,"xb")`（wx 模式），`FileExistsError` 跳过 | spec F3；`os.stat().st_mtime_ns` 不变可验证（AC3） |
| 落盘路径 | `.newcode/sessions/<session_id>/tool-results/<id>` 会话隔离 | 防跨会话 id 碰撞；会话 id `<unix_ts>-<short_random>` 进程内唯一不持久化。spec F33 |
| 落盘 I/O | `asyncio.to_thread(path.write_bytes, data)` | 避免阻塞 event loop 超 100ms（N1） |
| 预览结构 | 头部 20 行或 2048 字节择短 + 原始字节数 + 路径 + 重读提示；字符串冻结复用 | 源文件头部信息密度最高；择短防一行超长爆字节；冻结保缓存。spec F4/F5d |
| Context Window 四级解析 | env → [1m] 后缀 → 能力表(≥100K) → 协议默认 | 用户明确给的 `getContextWindowForModel` 算法；第 4 级用协议默认（anthropic 200K/openai 128K）。spec F29 |
| 阈值可配范围 | 全部硬编码，不进 YAML（env 仅保留 CLAUDE_CODE_MAX_CONTEXT_TOKENS） | 用户拍板「都改成不可配置」；避免配置项爆炸。spec F32 |
| 熔断模型 | 单次压缩行动独立 3 次重试上限，无跨种类/跨行动累计 | 用户明确纠正「不是整个周期只允许失败三次」；三路径熔断后统一弹菜单。spec F28 |
| F27 共用底层 | 摘要请求自身 PTL 的丢组重试由 MessageGroupDropper 承载，自动/手动/紧急共用 | spec F27；步进统一（3 次直接每次 1 组→比例 20%） |
| ForceCompact 重试粒度 | 纳入统一「单次行动 3 次」模型；外层 emergency_retried 标记保证一次迭代内 ForceCompact 动作只发生一次 | 用户拍板「纳入统一 3 次」；F26 emergency_retried 防 PTL 循环 |
| ForceCompact 先强制 L1 | 调摘要前先 offload_and_snip 挪走 50K+ | 避免摘要请求自身立刻撞 PTL。spec F25 |
| 紧急压缩后重估 | 用新消息 + reset_anchor 重估，仍超窗口-3000 视为不可恢复上抛 | spec F25a；防无谓重试 |
| 手动 /compact 余量语义 | 3000 仅用于摘要请求自检（摘要 prompt 自身是否撞墙），非触发阈值 | spec F23；手动无条件触发 |
| 摘要请求不传工具 | `PromptPayload.tools=None` | spec F8；摘要不允许调工具 |
| 摘要 max_tokens | PromptPayload 新增 `max_output_tokens`，摘要请求设 8192 覆盖 Anthropic 写死的 4096 | 九段摘要可能超 4096 输出；探查确认 4096 写死在 `anthropic.py:108`。普通对话不设（None=用默认） |
| 摘要草稿/正文 | `<analysis>` 草稿丢弃，只留 `<summary>` 正文 | spec F9 |
| 近期原文保留 | 双下界（token≥10000 且 条数≥5）择宽，不拆 tool_use/tool_result 对 | spec F11/F12；从尾部倒序累加 |
| 恢复段工具一致性 | 与 stream 请求 tools 同一份列表引用（`id(defs)` 相同），Agent.run 单次迭代粒度 | 防「恢复段说有工具 A 但 tools 没传 A」幻觉调用。spec F17 |
| 文件追踪时机 | 工具执行成功后、add_tool_result 前，同 task 同步 await | spec F19a；保证下轮 manage_context 能观察到本轮 ReadFile |
| 文件追踪纯净字节 | 剥离 read_file 截断提示后的 content | spec F19；read_file 输出本身不带行号（探查确认 `file_ops.py:94`），只需剥离截断提示 |
| 文件追踪/账本并发 | asyncio.Lock 保护 | spec F20/N2；TUI 命令路径、主循环、紧急压缩路径并发访问 |
| 手动/主循环互斥 scope | `Agent._run_lock` 贯穿整轮 run（非只锁 manage_context）；手动 `run_force_compact` 持同一锁等 run 结束 | 防「manage_context 释放锁→该轮 add_tool_result 之间」窗口被手动 compact 抢入 replace_history，导致流回来写 tool_result 到新历史（新历史无对应 tool_use）→ 配对断裂。spec F34；两把锁（_run_lock 管 run/手动入口，ContextManager._lock 管 context 内部）顺序一致无死锁 |
| Token 估算纯函数 + 外部锚点 | `estimate_tokens(anchor, all_msgs, anchor_msg_len)` 纯函数；anchor/anchor_msg_len 由调用方外部跟踪 | 估算器无状态、可重入、易测；锚点不与估算逻辑耦合。spec F13/F14 |
| 摘要请求不更新锚点 | 锚点只由主对话路径 `_stream_once` 成功后更新；摘要路径不调 update_anchor | 摘要 usage 反映「摘要这组消息」的消耗，不能当主对话锚点，否则污染后续估算 |
| 摘要+恢复合并单条 user 消息 | 9 段摘要 + 三段恢复拼进**一条** `Message(role="user", content=...)` | Anthropic API 强制 user/assistant 交替；恢复段多个 file_snapshot 各成 user 消息会连续 user → 400 roles-must-alternate。探查确认 `anthropic.py:69` 把 tool 翻成 user，连续 user 被拒 |
| 近期原文 role 衔接 | pick_recent_tail 截断点前推保配对 + 若 summary(user) 紧接近期原文首条 user 则插 assistant 占位 | 同上交替约束；摘要(user)+近期原文(user) 连续 → 400，占位保交替 |
| context_window 下界 sanity check | manage_context 入口检查 `context_window > 33000`，过小跳过自动 L2 + warning | 小窗口（如 env 设 10000）会使 `窗口-33000` 为负、阈值判断永远成立 → 每轮触发摘要死循环 |
| 自动路径连续失败闸 | `AutoCompactGate`：连续 3 轮自动压缩行动失败 → 停自动触发；手动 /compact 成功即解除；仅自动路径、不跨种类 | 用户拍板「每行动独立 3 次」在 provider 持续故障时会每轮弹菜单轰炸；闸只「停自动触发」不改变单次行动内部 3 次重试，与「无跨行动计数」并存 |
| Skill 骨架 | 建类 + Registry，注入分支空实现 + TODO，内容加载留后续 | 用户拍板「先将相关类创建好做好标记后面再补」。spec F31 |
| 探测脚本 | 独立 scripts/probe_context_window.py，不在主流程，手工回填 | 用户拍板「只交付独立探测脚本」。spec F30 |
| PTL 识别 | `llm.PromptTooLongError` 哨兵异常：provider 适配层按状态码+关键词判定后 wrap 并经 `StreamEvent.err` 投递（`__cause__` 保留原异常），Agent/Summarizer 用 `isinstance` 判定 | 用户拍板改投哨兵方案；`isinstance` 类型安全、`__cause__` 保留调试信息；provider 错误统一从流里以 `StreamEvent.err` 吐出（探查确认 `anthropic.py:195`/`openai.py:169`），不抛异常 |
| 斜杠命令 | 抽 BUILTIN_COMMANDS 注册表，/exit /plan /do 迁移行为不变，新增 /compact | spec F21；现有是硬编码 if/elif 链（`app.py:193`） |
| 熔断/紧急菜单 | 复用 TUI 现有 _ask_choice（`app.py:574`） | 已有方向键选择基础设施，不重造 |
| 压缩 UX 提示 | 经新事件 CONTEXT_COMPACTING/COMPACT_FAILED 透传 TUI | spec F24a/F24b；避免用户以为卡死 |
| _trim 改造 | 降级条数兜底，裁剪单位改 user 分组不拆对，主裁剪权交 context | spec N3/F9；探查确认现有 _trim 整对丢弃会拆配对（`manager.py:112`） |
| Agent 向后兼容 | context_mgr/file_tracker 可选，None 时行为同 ch07 | spec N8；不强制启用 |
| 流错误重试 vs 压缩重试 | TUI max_retries=3（`app.py:330`）是流错误重试，F28 是压缩行动重试，两者独立不合并 | 探查确认两者并存，plan 显式区分防串 |
| 不做的 | 精确 tokenizer / ML 优化 / Skill 内容加载 / 能力表自动回填 / 跨会话持久化 / 文件 GC / PTL 外请求级兜底 / 阈值进 YAML / 跨行动熔断累计 | spec「不做的事」 |
