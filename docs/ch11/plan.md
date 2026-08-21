# MewCode ch11 - Skill 技能包系统 Plan

## 架构概览

```
启动装配（main.py）
    SkillLoader.load_all（三级路径扫描 + 内存 cache）──► Skill 对象
        │   validate_tools（启动校验：白名单坏 → warning + 移除）  │
        │                                                          ▼
        │  build_catalog（Available Skills 摘要段）          ActiveSkillStore（单会话激活状态）
        ▼                                                          │
   register_skill_commands（/名字 命令）◄── Executor ──► Agent（持 store 引用）
        │                                                          │
        ▼                                                          ▼
   /skill 管理命令（list/info/reload/load/on/off/unload）   每轮 env 合成（base + catalog + 激活 SOP）
                                                              fork 子 Agent 用 definitions_filtered 收窄工具集
```

核心思路：**Skill 状态与 Agent 解耦**。`mewcode/skills/` 新包承载 Skill 全生命周期（解析/加载/执行/激活态），Agent 只持 `ActiveSkillStore` 引用，每轮动态合成 env segment。context 包（ch08）经 ActiveSkillStore 挂钩实现压缩预算淘汰，不反向依赖 agent。

**关键约束（A 决策）**：allowedTools 在 **inline 模式不真过滤**——只做 SOP 顶部提示 + 启动 fail-fast 校验；**仅 fork 模式真过滤**（子 Agent 用 `definitions_filtered`）。主对话工具集始终全量，避免动态切换工具集的生命周期复杂度。

## 组件划分

| 模块 | 文件 | 职责 |
|------|------|------|
| 数据模型 | `mewcode/skills/types.py` | SkillMeta / Skill / SkillSource / ActiveEntry |
| 解析器 | `mewcode/skills/parser.py` | frontmatter+body 分离校验、名字归一化、render_body（$ARGUMENTS 替换 + 兜底） |
| 加载器 | `mewcode/skills/catalog.py` | 三级路径扫描、同名覆盖、内存 cache 回退、热加载、validate_tools、catalog 构建 |
| 激活状态 | `mewcode/skills/active.py` | ActiveSkills：激活/失活/列举/预算淘汰 |
| 执行器 | `mewcode/skills/executor.py` | inline / fork 分发执行、fork token 写回 |
| 适配桥 | `mewcode/skills/adapter.py` | catalog_to_prompt_items / active_to_prompt_entries（prompt 包零依赖 skills） |
| 目录型工具 | `mewcode/skills/script_tool.py` | ScriptTool：tool.json 声明 → 子进程执行的 Tool 实例 |
| 内置 Skill | `mewcode/skills/builtin/` | commit.md / review.md / test.md 三样板 |
| LoadSkill 工具 | `mewcode/tools/load_skill.py` | 系统级只读工具：激活 body + 注册专属工具 + 返回确认 |
| Agent 集成 | `mewcode/agent/agent.py` | 持 store、env 每轮合成、with_catalog 可选注入 |
| 压缩挂钩 | `mewcode/context/recovery.py` | 压缩后激活 Skill 注入 + 4k 预算淘汰最旧 |
| 动态命令注册 | `mewcode/slash/commands/skill_register.py` | 每个 Skill 注册为 `/名字`（描述标 `[skill]`） |
| 管理命令 | `mewcode/slash/commands/skill.py` | `/skill` list/info/reload/load/on/off/unload |

## 核心数据结构

### SkillMeta（`skills/types.py`）
```python
@dataclass
class SkillMeta:
    name: str                    # 归一化唯一名字（小写、字母数字-）
    description: str             # 一句话说明（阶段一摘要）
    allowed_tools: list[str] = field(default_factory=list)  # 白名单；空 = 不限制
    mode: Literal["inline", "fork"] = "inline"  # 空或 inline 视作 inline，其它值 warning 后按 inline
    fork_context: Literal["none", "recent", "full"] = "none"  # 仅 fork 生效
    model: str | None = None     # fork 指定模型；None = 会话模型

    def is_fork(self) -> bool:
        return self.mode == "fork"
```

### Skill（`skills/types.py`）
```python
class SkillSource(Enum):
    USER = "user"
    PROJECT = "project"
    BUILTIN = "builtin"

@dataclass
class Skill:
    meta: SkillMeta
    prompt_body: str           # SKILL.md 去 frontmatter 后的正文（启动缓存，执行时重读覆盖）
    source_dir: Path           # 绝对路径，重读 SKILL.md 时用
    source: SkillSource
    tools: tuple[ToolSchema, ...] = ()  # tool.json 声明的专属工具（目录型）
```

### ToolSchema（`skills/types.py`）
```python
@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    parameters: dict           # JSON Schema object
    entrypoint: str            # references/ 下实现脚本相对路径
```

### Catalog（`skills/catalog.py`）
```python
class Catalog:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_name: dict[str, Skill] = {}
        self._order: list[str] = []

    @classmethod
    def load(cls, project_dir: Path) -> "Catalog": ...
    def reload(self, work_dir: Path) -> tuple[list[str], list[str]]: ...  # (added, removed)
    def get(self, name: str) -> Skill | None: ...
    def list(self) -> list[Skill]: ...
    def names(self) -> list[str]: ...
    def validate_tools(self, registry) -> list[str]: ...  # 返回不通过的 skill 名
    def get_catalog(self) -> list[tuple[str, str]]: ...   # [(name, description)]，排除 disabled，装配层拼文本
    def get_source_label(self, name: str) -> str: ...     # project | user | builtin
    def is_disabled(self, name: str) -> bool
    def set_disabled(self, name: str, disabled: bool) -> None  # 落盘 disabled.json
```
扫描顺序：内置 `mewcode/skills/builtin/` → 用户 `~/.mewcode/skills/` → 项目 `<work_dir>/.mewcode/skills/`；后扫同名覆盖前者（项目级 > 用户级 > 内置级）。`reload` 返回 `(added, removed)` 供调用方同步 slash 命令。

### ActiveSkills（`skills/active.py`）
```python
@dataclass
class ActiveEntry:
    name: str
    body: str                    # 激活那一刻磁盘上的 SKILL.md 正文（渲染后）

class ActiveSkills:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[ActiveEntry] = []   # 保持激活顺序
        self._index: dict[str, int] = {}        # 重复激活覆盖原位置

    def activate(self, name: str, body: str) -> None: ...
    def deactivate(self, name: str) -> None: ...
    def clear(self) -> None: ...
    def snapshot(self) -> list[ActiveEntry]: ...   # 拷贝（env 装配用）
    def names(self) -> list[str]: ...
    def total_tokens(self, estimator) -> int: ...  # 对齐 ch08 SkillRegistry 接口
    def enforce_budget(self, budget: int) -> list[ActiveEntry]:
        # 按激活顺序淘汰最旧，直至总 token ≤ budget；返回幸存列表
    def to_prompt_entries(self) -> list[ActiveSkillEntry]: ...
```

### Executor（`skills/executor.py`）
```python
class Executor:
    def __init__(self, catalog: Catalog, store: ActiveSkills, registry: Registry,
                 provider: Provider, engine, version: str) -> None: ...

    # 入口：被 Slash 命令 handler / LoadSkill 调用
    async def execute(self, ctx, ui, name: str, args: str) -> None: ...
```
inline 分支：Catalog.get → 磁盘重读 SKILL.md（失败回退缓存）→ `render_body` → `store.activate` → `ui.inject_and_send(display_label, body)` 触发回合。
fork 分支：按 `fork_context` 构造独立 Conversation → 临时 Agent（`definitions_filtered` 收窄工具集）→ 跑完累计 token 写回主 anchor → `ui.append_assistant_message(final_text)`。

## 模块设计

### mewcode/skills/parser.py
**职责：** 解析 Skill 目录 → `Skill`；frontmatter 与 body 分离校验。
**对外接口：** `parse_skill_dir(dir_path, source) -> Skill`、`parse_frontmatter_and_body(text) -> tuple[dict, str]`、`normalize_name(name) -> str`
**依赖：** `pyyaml`（已在 pyproject）。
**要点：** frontmatter 缺失/非法抛 `SkillParseError`（调用方跳过+warning）；`normalize_name` 转小写、非字母数字转 `-`（F1.4）；目录型额外读 `tool.json`（F9.2）。

### mewcode/skills/render.py
**职责：** 把 Skill body 渲染为最终注入文本（inline 与 fork 都先经此层）。
**对外接口：** `render_body(skill: Skill, args: str) -> str`
**要点（F1.3/F3.4）：**
1. 替换所有 `$ARGUMENTS` 为 args
2. 无占位符且 args 非空 → 末尾追加 `\n\n## User Request\n\n<args>`（兜底规则，参考模板）
3. `allowed_tools` 非空 → body 顶部插入 ``This skill is designed to use only these tools: <list>. Prefer them over other tools when possible.\n\n---\n\n``（inline 提示，不真过滤）

### mewcode/skills/catalog.py
**职责：** 三级路径扫描与覆盖管理 + 启动校验。
**对外接口：** 见数据结构节。
**依赖：** `parser`、`types`、`constants`（路径/预算）。
**要点：** 扫描顺序内置→用户→项目，后扫覆盖（F2.1）；`get(name)` 每次重读源文件（热更新），失败回退内存 `_cache` 并 warning（F2.3/N7）；`validate_tools(registry)` 遍历所有 Skill 的 allowed_tools，引用不存在的工具 → 返回该 Skill 名（启动期调用方 warning + 从 catalog 移除，F2.7/B 决策）；disabled 集合读写 `~/.mewcode/skills/disabled.json`（F7.8）。

### mewcode/skills/active.py
**职责：** 单会话激活状态（F5.1）。
**对外接口：** 见数据结构节。
**依赖：** `types`。
**要点：** `enforce_budget(4k)` 按激活顺序淘汰最旧直至 ≤ 预算（F8.1），压缩时由 ContextManager 调用；`total_tokens(estimator)` 兼容 ch08 `SkillRegistry` 接口（N10）。

### mewcode/skills/executor.py
**职责：** inline / fork 分发执行（F3.1）。
**对外接口：** `Executor.execute(ctx, ui, name, args)`。
**依赖：** `catalog`、`active`、`render`；`agent.Agent`（局部 import 避循环）。
**execute_inline 流程：**
1. `Catalog.get(name)` → 不存在返回错误信息
2. 磁盘重读 SKILL.md（失败回退缓存，F2.3）
3. `render_body(skill, args)`（$ARGUMENTS 替换 + allowed_tools 顶部提示，F3.4）
4. 目录型 skill：注册 tool.json 工具进主注册表（ScriptTool）
5. `store.activate(name, body)`（F4.2.1）
6. `ui.inject_and_send(f"/{name}", body)` 触发回合
**execute_fork 流程：**
1. `render_body`；按 `fork_context` 构造 fork 初始对话：
   - `none`：仅一条 user 消息（rendered_body）
   - `recent`：从主对话拷最近 N 条（缺省 5）原始消息，再追加 rendered_body
   - `full`：LLM 把主对话压缩成摘要（复用 summarize 模式），作 system/user 消息插入，再追加 rendered_body
2. `fork_defs = registry.definitions_filtered(skill.meta.allowed_tools)`（系统工具豁免透传，F3.7）→ 子 Agent 工具集收窄
3. `skill.meta.model` 非空 → `new_provider` 建模型覆盖 provider（F1.2）
4. 独立内存 `ConversationManager`（不落盘，N3）+ 临时 Agent（`with_catalog` 注入）
5. 跑至 DONE，累计 token → **写回主 runtime anchor（`usage += sub`）**（N13）
6. 取子对话最后一条 assistant 文本作 final_text → `ui.append_assistant_message(final_text)` 写回主对话
7. 任一步出错 → `final_text = "[skill <name> failed: <reason>]"`，仍以 assistant 消息写回

### mewcode/skills/script_tool.py
**职责：** 目录型专属工具的子进程执行壳（F9.5）。
**对外接口：** `ScriptTool(schema, skill_dir)`，实现 `Tool` 协议。
**要点：** `execute(arguments)` 用 `asyncio.create_subprocess_exec` 起 entrypoint 子进程（参数 JSON 走 stdin），`asyncio.wait_for(..., timeout=30)`，stdout 捕获为 ToolResult.output；不 import 进主进程（N4）；`is_system = False`。

### mewcode/tools/load_skill.py
**职责：** 系统级只读 LoadSkill 工具（F4.2）。
**对外接口：** `LoadSkillTool(catalog, active, registry)`，`name="load_skill"`。
**要点：** `read_only=True`（READONLY 类不弹权限，N5）、`is_system=True`（豁免 allowedTools，F3.5）；`execute({name})`：`catalog.get` → 重读 body → 目录型注册工具 → `active.activate` → 返回简短确认（不返回完整 SOP，F4.2.3）；未知名 → 错误结果。

### mewcode/tools/registry.py（修改）
**要点：** Tool 协议新增 `is_system: bool`（默认 False，`getattr` 兼容旧工具）；`Registry.system_definitions()`（仅系统工具）；`Registry.definitions_filtered(allowed: list[str])`（系统工具豁免 + 白名单过滤，返回新定义列表；allowed 为空 → 全量）。**inline 模式不调用**（F3.7/A 决策），仅 fork 子 Agent 用。

### mewcode/tools/base.py（修改）
**要点：** Tool 协议新增 `is_system: bool` 只读属性。

### mewcode/agent/agent.py（修改）
**职责：** Skill 状态持有 + 每轮动态 env 合成。
**新增：** `with_catalog(c: Catalog)` 可选注入（None 时跳过 env 组装，向后兼容 N10）；`activate_skill(name, body)` / `clear_active_skills()` 转发 store。
**修改 `run()`：** 每轮组装时：
- `stable_prompt` 不变（catalog 在 env，不破坏缓存通道）
- `env_text = base_env + render_skills_catalog(...) + render_active_skills_block(...)`（F4.1/F5.2）
- `tool_defs` 保持全量（inline 不真过滤，F5.3）

### mewcode/prompt/skills_block.py（新增）
**职责：** env 段的 Skill 渲染（prompt 包不依赖 skills 包）。
**对外接口：** `render_skills_catalog(items: list[SkillCatalogItem]) -> str`（Available Skills 摘要段 + load_skill 指引）、`render_active_skills_block(entries: list[ActiveSkillEntry]) -> str`（`## Active Skills` 段，逐条 `### Skill: <name>` + body；空返回空串）
**类型：** `SkillCatalogItem(name, description)`、`ActiveSkillEntry(name, body)`（瘦 dataclass，skills 包经 adapter 转换）

### mewcode/context/recovery.py（修改）
**职责：** 压缩后恢复段落地 Skill 注入 + 预算淘汰（F8.1）。
**要点：** RecoveryBuilder 的 skill 分支由 TODO 改为：调 `active_store.enforce_budget(4k)` → 幸存激活 Skill 追加进恢复段；`active_store` 经 ContextManager 构造注入（复用现 skill_registry 参数位）。`context/` 只依赖 `skills.active`（零依赖 agent）。

### mewcode/slash/commands/skill_register.py（新）
**职责：** Skill 动态注册为 `/名字` 命令（F2.4）。
**对外接口：** `register_skills_as_commands(reg, catalog, executor)`、`remove_skill_commands(reg)`。
**要点：** 每个 Skill 注册 `CommandDef(name, kind=UI, description=f"{description} [skill]", handler)`；**闭包循环变量用 `functools.partial(handler, name=skill.name)` 显式拷贝**（Python 闭包按引用绑定陷阱）；handler 按 `mode` 分发：inline → `executor.execute` 后 `ui.inject_and_send`；fork → `asyncio.create_task(_run_fork)`（结果经 `ui.append_assistant_message` 回流）；与内置命令冲突：`try register except RuntimeError → warning 跳过`（F2.5）；`remove_skill_commands` 供 `/skill reload` 与 InstallSkill 后同步。

### mewcode/slash/commands/skill.py（新）
**职责：** `/skill` 管理命令（F7）。
**要点：** 子命令：list / info <n> / reload [n] / load <n> / on <n> / off <n> / unload <n>；reload 调 `catalog.reload` 后同步命令注册（用返回的 added/removed）；off 同时 `store.deactivate` + `set_disabled`；on/off 后重建 catalog 并同步 `[skill]` 命令；unload 移出注册 + 清理内存状态 + 清 disabled 标记。

### mewcode/slash/ui.py（修改）
**要点：** UI 协议新增 4 方法（参考模板风格）：`list_catalog_skills() -> list[SkillSummary]`、`list_active_skills() -> list[str]`、`clear_active_skills() -> None`、`append_assistant_message(text) -> None`（fork 结果写主对话）；NopUI 提供零值实现，RecordingUI 记录调用。

### mewcode/slash/commands/clear.py（修改）
**要点：** `handle_clear` 在 `request_clear_session` 后调 `ui.clear_active_skills()`（F5.5）。

### mewcode/slash/registry.py（修改）
**要点：** 新增 `unregister(name)`（锁内删除）与 `remove_by(filter)`，供 reload 重扫与 `remove_skill_commands` 用。

## 模块交互

```
启动：
  main.py → Catalog.load(work_dir) → validate_tools(registry)（坏项 warning+移除，B 决策）
         → LoadSkillTool(catalog, active, registry) → registry.register（系统工具）
         → ActiveSkills() → 注入 agent + ContextManager
         → Executor(...) → register_skills_as_commands（+ functools.partial）
         → /skill 命令注册

inline 显式（/commit）：
  用户输入 → dispatch_slash → skill handler → executor.execute(ctx, ui, "commit", "")
    → 重读 body → render_body → store.activate → ui.inject_and_send → Agent.run
    → 每轮 env = base + catalog + render_active_skills_block（含完整 SOP）

fork 显式（/review）：
  用户输入 → skill handler → asyncio.create_task(executor.execute)
    → 独立 Conversation + 临时 Agent（definitions_filtered 收窄工具集）
    → 完成 → token 写回主 anchor → ui.append_assistant_message(final_text)

意图触发：
  Agent 某轮调 load_skill({name}) → LoadSkillTool.execute
    → catalog.get → 重读 body → active.activate → 返回确认
    → 下一轮 env 含完整 SOP（render_active_skills_block）

压缩：
  ContextManager.manage_context → RecoveryBuilder.build
    → active_store.enforce_budget(4k) → 幸存 Skill 注入恢复段

清理：
  /clear → ui.request_clear_session → ui.clear_active_skills() → store.clear()
    → 后续轮 env 无旧 SOP

reload / InstallSkill 后：
  catalog.reload → (added, removed) → remove_skill_commands + register_skills_as_commands
    → /help 与补全立即同步
```

## 文件组织

```
mewcode/
├── skills/                      # 新增
│   ├── __init__.py              — 导出 Catalog / ActiveSkills / Executor / SkillSource
│   ├── constants.py             — ACTIVE_SKILL_TOKEN_BUDGET=4_000、路径常量、recent 缺省 N=5
│   ├── types.py                 — SkillMeta / Skill / SkillSource / ActiveEntry / ToolSchema
│   ├── parser.py                — parse_skill_dir / parse_frontmatter_and_body / normalize_name
│   ├── render.py                — render_body（$ARGUMENTS + ## User Request 兜底 + allowed_tools 提示）
│   ├── catalog.py               — Catalog（三级扫描/覆盖/热加载/validate_tools/disabled/reload）
│   ├── active.py                — ActiveSkills（激活/失活/预算淘汰/to_prompt_entries）
│   ├── adapter.py               — catalog_to_prompt_items / active_to_prompt_entries 桥接
│   ├── executor.py              — Executor（inline/fork）
│   ├── script_tool.py           — ScriptTool（create_subprocess_exec + wait_for 30s）
│   └── builtin/
│       ├── commit.md            — inline：conventional commit + 逐个 add
│       ├── review.md            — fork：五维审查 + 分级报告
│       └── test.md              — inline：类型检测 + 跑测试 + 区分两种失败
├── tools/
│   ├── base.py                  — Tool 协议加 is_system（修改）
│   ├── registry.py              — system_definitions / definitions_filtered（修改）
│   └── load_skill.py            — LoadSkillTool（新增）
├── prompt/
│   └── skills_block.py          — render_skills_catalog / render_active_skills_block / 瘦类型（新增）
├── agent/agent.py               — with_catalog / activate_skill / clear_active_skills / 每轮 env 合成（修改）
├── context/recovery.py          — 落地 Skill 注入 + 预算淘汰（修改）
├── slash/
│   ├── registry.py              — 加 unregister / remove_by（修改）
│   ├── ui.py                    — 新增 4 个 UI 方法 + NopUI/RecordingUI（修改）
│   └── commands/
│       ├── skill.py             — /skill 管理命令（新增）
│       ├── skill_register.py    — 动态注册 + functools.partial（新增）
│       ├── clear.py             — 追加 clear_active_skills（修改）
│       ├── __init__.py          — 移除 review、加 skill/skill_register（修改）
│       └── review.py            — 删除（被 review Skill 接管）
└── main.py                      — 装配 Catalog/Store/Executor/LoadSkillTool/validate_tools（修改）

tests/
├── test_ch11_parser.py          — 解析/归一化/替换/兜底/tool.json
├── test_ch11_catalog.py         — 三级覆盖/失败隔离/热加载回退/validate_tools/disabled 持久
├── test_ch11_render.py          — $ARGUMENTS / ## User Request 兜底 / allowed_tools 提示
├── test_ch11_active.py          — 激活/失活/预算淘汰
├── test_ch11_executor.py        — inline/fork/fork_context 三策略/token 写回
├── test_ch11_load_skill.py      — 工具行为/系统豁免/嵌套
├── test_ch11_script_tool.py     — 子进程执行/超时
├── test_ch11_skill_command.py   — /skill 管理命令 handler
├── test_ch11_register.py        — 动态注册/冲突跳过/[skill] 标注/闭包拷贝
├── test_prompt_skills.py        — catalog/active 块渲染/空块
└── test_ch11_integration.py     — 两阶段加载/意图触发/env 断言/端到端
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 阶段一摘要位置 | env context（每轮重建） | 与激活 SOP 同机制；不破坏 stable prompt 缓存通道 |
| Agent 与 skill 解耦 | Agent 持 ActiveSkills 引用 + with_catalog 可选注入 | context 包也引用 store，避免反向依赖；None 时跳过（N10） |
| **inline 模式 allowedTools** | **仅提示 + fail-fast，不真过滤** | 避免动态切换工具集的生命周期复杂度（工具集变化 → prompt cache 不稳定）；安全由 ch08 权限引擎兜底；fork 才真过滤（A 决策） |
| **fail-fast 时机** | **启动时 validate_tools（warning + 从 catalog 移除）** | 贴合原始「启动时立刻报错」；不阻断其它 Skill；激活时保留防御复查（B 决策） |
| fork 隔离 | 独立内存 ConversationManager（不落盘） | N3 主对话零污染；fork 一次性执行无需持久化 |
| fork 结果回流 | `ui.append_assistant_message(final_text)` | 参考模板：UI 协议方法写回主对话历史；provider 兼容 |
| context=full | LLM 压缩主对话成摘要（复用 summarize 模式） | 省 token、fork 不继承全部历史成本（已定） |
| 预算淘汰 | `enforce_budget(4k)`，按激活顺序淘汰最旧 | 固定值可预测（已定）；ch08 `SKILL_RECOVERY_BUDGET` 占位由新常量取代 |
| 内存 cache | `get()` 重读源 + 失败回退 `_cache`（无磁盘缓存） | 已定：磁盘缓存收益小复杂度大；热更新即时 |
| CommandRegistry | 新增 `unregister` / `remove_by` | reload 与 InstallSkill 后同步命令 |
| skill 命令 Kind | 统一 KindUI | inline 触发回合、fork 后台跑都需 idle 状态机门 |
| 闭包循环变量 | `functools.partial(handler, name=skill.name)` | Python 闭包按引用绑定，循环里必须显式拷贝（参考模板） |
| prompt 包依赖 | adapter 桥接（skills 包 → prompt 瘦类型） | prompt 包零依赖 skills，避免循环依赖（参考模板） |
| ScriptTool 执行 | `asyncio.create_subprocess_exec` + `wait_for(30s)` | 不阻塞 event loop；与 ch05 bash 工具一致（参考模板） |
| fork token 成本 | 累计后写回主 runtime anchor（`usage += sub`） | N13 成本透明落到 token 统计（参考模板） |
| review 迁移 | 删 `slash/commands/review.py`，review Skill 接管 | F6.4 |
| 名字归一化 | parser 统一 `normalize_name` | F1.4：与 `/名字` 注册合法性对齐 |
| disabled 持久 | `~/.mewcode/skills/disabled.json` | F7.8 已定：跨会话保持禁用 |

## 待实现细节确认（实现期可能遇到的接线点）

1. `Catalog.reload` 返回 `(added, removed)`——启动期无 removed；InstallSkill / `/skill reload` 时调用方同步 slash 命令（先 remove_skill_commands 再 register_skills_as_commands）
2. fork 的临时 Agent 是否接 `context_mgr`：fork 会话短小，ch11 暂不接（无压缩），超窗依赖 provider 报错兜底
3. `LoadSkillTool` 注册时机：主注册表早于 Agent 构造——工具持 catalog/store 引用即可（不依赖 Agent），env 每轮由 Agent 自行读 store，无需反向注入
4. inline 模式目录型 Skill 注册的 ScriptTool 对全量工具集可见（inline 不真过滤）——tool.json 工具本质是「新增工具」，不是「收窄」，与 A 决策一致
5. `render_body` 的 allowed_tools 提示与 fork 过滤的关系：inline 只提示不过滤，fork 既提示又过滤（definitions_filtered）
