# NewCode Plan 文件管理 — 技术设计 (plan-plan-files.md)

## 架构概览

新增 `newcode/plans/` 包，核心是 `PlanManager` 类。所有 plan 文件操作收敛到 `PlanManager`，`REPL` 只负责交互和展示。

```
用户输入                     REPL                         PlanManager              文件系统
────────                    ────                         ───────────               ──────
/plan <任务>  ──→  Agent(plan mode)  ──→  buffer ──→  create_plan() ──→  plans/<slug>.md
                                                                         plans/.meta.json

/do <slug>   ──→  get_plan() → read_plan_content() ──→  Agent(execute) ──→  mark_executed()

/do (无参)    ──→  list_plans() → 用户选择 ──→  同上

/delete-plan ──→  list_plans() → 多选交互 ──→  delete_plans() ──→  删除文件+元数据

启动          ──→  cleanup_old(30) ──→  删除过期文件+元数据
```

## 核心数据结构

### PlanMeta

```python
@dataclass
class PlanMeta:
    slug: str  # 唯一标识，如 "create-hello-world"
    file: str  # 文件名，如 "create-hello-world.md"
    task: str  # 任务描述（从 plan 内容提取）
    created_at: str  # ISO 时间戳，如 "2026-08-08T15:30:00"
    executed_at: str | None  # 最近执行时间，None 表示未执行
```

### .meta.json 结构

```json
{
  "create-hello-world": {
    "file": "create-hello-world.md",
    "task": "创建 hello world",
    "created_at": "2026-08-08T15:30:00",
    "executed_at": null
  }
}
```

## 模块设计

### 模块 A: `newcode/plans/manager.py` — PlanManager

**职责：** 所有 plan 文件的 CRUD + 索引管理 + 清理

**对外接口：**

| 方法 | 签名 | 用途 |
|------|------|------|
| `create_plan` | `(task: str, content: str) -> str` | 创建 plan 文件和元数据，返回 slug |
| `list_plans` | `() -> list[PlanMeta]` | 列出所有 plan（含自愈校验） |
| `get_plan` | `(slug: str) -> PlanMeta \| None` | 获取单个 plan 元数据 |
| `read_plan_content` | `(slug: str) -> str` | 读取 plan 文件内容 |
| `mark_executed` | `(slug: str) -> None` | 标记已执行，更新 executed_at |
| `delete_plans` | `(slugs: list[str]) -> None` | 删除指定 plan 及其元数据 |
| `cleanup_old` | `(days: int) -> int` | 清理超过 N 天的 plan，返回删除数 |

**内部方法：**

| 方法 | 用途 |
|------|------|
| `_load_meta() -> dict` | 加载 `.meta.json`，损坏则返回 `{}` |
| `_save_meta(data: dict) -> None` | 保存 `.meta.json` |
| `_extract_slug(content: str) -> str` | 从内容中提取 `<!-- slug: xxx -->`，回退日期格式 |
| `_extract_task(content: str) -> str` | 从内容中提取任务描述（首个 `# 标题` 或首行） |

**核心逻辑：**

**Slug 提取：**
1. 正则匹配 `<!-- slug: xxx -->`
2. 将非字母数字字符替换为 `-`，去除首尾 `-`
3. 若为空或未匹配，回退 `plan-YYYY-MM-DD-HHMMSS`

**自愈（F9）：**
`list_plans()` 中遍历 `.meta.json` 条目，检查对应 `.md` 文件是否存在。不存在则从 meta 中移除，写入脏条目。

**依赖：** 无（仅标准库 `json`, `os`, `re`, `datetime`）

### 模块 B: `newcode/config/schema.py` — 配置调整

**改动：**
- 移除 `plan_file: str = "plans/plan.md"`
- 新增 `cleanup_period_days: int = 30`

### 模块 C: `newcode/config/loader.py` — 配置解析调整

**改动：**
- 移除 `plan_file` 解析和传递
- 新增 `cleanup_period_days` 解析和传递

### 模块 D: `newcode/prompt/resources.py` — PLAN_MODE_REMINDER

**改动：** 新增 slug 声明要求：
```
- 在计划开头用 HTML 注释声明一个简短的 slug 标识符，
  格式为 <!-- slug: 简短英文标识 -->
  例如：<!-- slug: add-login-page -->
```

### 模块 E: `newcode/tui/app.py` — REPL 重构

**职责：** Plan 交互入口，委托 PlanManager 处理文件操作

**改动点：**

1. **构造函数**：`plan_file: str` → `plan_manager: PlanManager`
2. **新增属性**：`_pending_slug: str`、`_executing_slug: str`
3. **`/plan` 命令**：plan 完成后调用 `plan_manager.create_plan()` 替代 `_write_plan_file()`
4. **确认弹窗**：提示 `[/do <slug> / not now]`
5. **`/do <slug>`**：调用 `plan_manager.get_plan()` + `read_plan_content()`，打印 plan 信息后执行
6. **`/do`（无参）**：调用 `plan_manager.list_plans()`，展示列表，用户选择
7. **`/delete-plan`**：调用 `plan_manager.list_plans()`，`checkboxlist_dialog` 多选，确认后 `delete_plans()`
8. **执行后标记**：DONE(NATURAL) 时若 `_executing_slug` 非空，调用 `mark_executed()`
9. **移除方法**：`_read_plan_file()`、`_write_plan_file()`
10. **状态栏**：新增 `/delete-plan` 提示

### 模块 F: `newcode/main.py` — 启动流程

**改动：**
1. 创建 `PlanManager(plans_dir)`
2. 启动时调用 `plan_manager.cleanup_old(config.cleanup_period_days)`
3. 传递 `plan_manager` 给 `REPL`

## 模块交互

### 交互 1：Plan 创建流程

```
TUI._process_input("/plan 创建hello")
  → Agent.run("创建hello", mode="plan")
    → 流式事件 → buffer 累积
    → DONE(NATURAL)
  → plan_manager.create_plan("", buffer)
    → _extract_slug(buffer) → "create-hello-world"
    → 写入 plans/create-hello-world.md
    → 更新 .meta.json
    → 返回 slug
  → 显示 "计划已保存: plans/create-hello-world.md"
  → 弹出 "是否执行？[/do create-hello-world / not now]"
```

### 交互 2：/do 执行流程

```
TUI._process_input("/do create-hello-world")
  → plan_manager.get_plan("create-hello-world") → PlanMeta
  → 打印 plan 信息（名称、创建时间、执行状态）
  → plan_manager.read_plan_content("create-hello-world") → 计划文本
  → Agent.run("", mode="execute", plan_content=计划文本)
    → EXECUTE_DIRECTIVE.format(plan=计划文本)
    → Agent 执行...
    → DONE(NATURAL)
  → plan_manager.mark_executed("create-hello-world")
```

### 交互 3：/do 无参选择流程

```
TUI._process_input("/do")
  → plan_manager.list_plans() → [PlanMeta, ...]
  → 展示编号列表
  → 用户输入序号或 slug
  → 解析 → 获取 plan → 执行（同交互 2）
```

### 交互 4：/delete-plan 流程

```
TUI._process_input("/delete-plan")
  → plan_manager.list_plans() → [PlanMeta, ...]
  → checkboxlist_dialog 多选
  → 确认 [y/N]
  → plan_manager.delete_plans(selected_slugs)
    → 删除 .md 文件
    → 从 .meta.json 移除条目
```

## 文件组织

```
newcode/
├── plans/                    ← 新增包
│   ├── __init__.py           ← 导出 PlanManager, PlanMeta
│   └── manager.py            ← PlanManager 类 + PlanMeta dataclass
├── config/
│   ├── schema.py             ← 修改：移除 plan_file，新增 cleanup_period_days
│   └── loader.py             ← 修改：解析 cleanup_period_days
├── prompt/
│   └── resources.py          ← 修改：PLAN_MODE_REMINDER 增加 slug 声明
├── tui/
│   └── app.py                ← 修改：REPL 重构，使用 PlanManager
└── main.py                   ← 修改：创建 PlanManager，启动清理

tests/
├── test_plan_manager.py      ← 新增：PlanManager 单元测试
└── test_tui_wiring.py        ← 修改：适配 PlanManager
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 元数据存储 | 单文件 `.meta.json` | 简单，无需引入数据库依赖；几十个 plan 场景下 JSON 足够 |
| Slug 来源 | LLM 声明 + 日期回退 | LLM 生成的 slug 语义化、可读；回退保证健壮性 |
| 自愈时机 | `list_plans()` 时检查 | 每次列表展示前校验，开销小，保证一致性 |
| 删除交互 | `checkboxlist_dialog` | prompt_toolkit 内置，原生支持多选和键盘操作 |
| 清理时机 | 启动时 | 不影响运行时性能，用户无感知 |
| `plan_file` 配置 | 移除 | 多文件命名后不再需要固定路径 |
| 执行状态 | `executed_at` 字段而非布尔 | 支持多次执行追踪，信息更丰富 |
| 重复执行 | 允许，覆盖 `executed_at` | 用户可能需要重新执行或重复执行同一 plan |