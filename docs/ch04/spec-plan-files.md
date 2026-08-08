# MewCode Plan 文件管理 — 需求规格 (spec-plan-files.md)

## 背景

ch04 实现了 Plan Mode 两段式（`/plan` → `/do`），但 plan 文件管理存在以下不足：

- **固定文件名**：所有 plan 都写入 `plans/plan.md`，每次 `/plan` 都会覆盖前一个 plan
- **无执行状态追踪**：无法区分哪些 plan 已执行、哪些未执行
- **无清理机制**：plan 文件只增不减，长期使用会堆积
- **无多 plan 支持**：不能同时维护多个 plan 后逐一执行

本次增量在 ch04 基础上，补齐 plan 文件的命名、索引、执行追踪、清理和删除能力。

## 目标

- 每个 plan 拥有独立文件名（基于 LLM 声明的 slug），互不覆盖
- 通过 `.meta.json` 索引追踪所有 plan 的创建时间、执行状态
- 支持 `/do <slug>` 执行指定 plan，`/do` 无参列出选择
- 支持 `/delete-plan` 多选删除
- 启动时自动清理过期 plan
- 安全边界：只操作 `plans/` 目录，不动 `docs/`

## 功能需求

### F1: Plan 文件独立命名

- 每个 plan 文件名为 `plans/<slug>.md`，slug 由 LLM 在 plan 文本开头声明：`<!-- slug: xxx -->`
- 如果 LLM 未声明 slug，则回退为日期格式：`plans/plan-YYYYMMDD-HHMMSS.md`
- slug 仅包含小写字母、数字和连字符，其他字符被替换或移除

### F2: .meta.json 索引

- `plans/.meta.json` 记录所有 plan 的元数据，结构为 `{slug: PlanMeta}`
- 每条 `PlanMeta` 包含：`file`（文件名）、`task`（用户任务描述）、`created_at`（ISO 时间戳）、`executed_at`（执行时间或 null）
- 新建 plan 时追加条目，执行 plan 时更新 `executed_at`
- 删除 plan 时同步移除条目和对应文件

### F3: /do <slug> 执行指定 plan

- 用户输入 `/do <slug>` 执行指定 plan
- 执行前打印 plan 信息：文件名、创建时间、是否已执行过
- 读取 plan 文件内容，通过 `EXECUTE_DIRECTIVE` 模板注入对话上下文
- Agent 以 execute mode 运行，全工具可用

### F4: /do 无参列出选择

- 用户输入 `/do`（无参数）时，列出 `plans/` 下所有 plan
- 展示格式：序号、slug、创建时间、执行状态
- 用户输入序号或 slug 选择执行

### F5: /delete-plan 多选删除

- 列出所有 plan（名称、创建时间、执行状态）
- 用户可多选（空格勾选/取消，Enter 确认删除，Esc 取消）
- 选中的 plan 文件及 `.meta.json` 中对应条目被删除
- 只操作 `plans/` 目录，绝不触碰 `docs/` 或其他目录

### F6: 启动自动清理

- 启动时自动删除 `created_at` 超过 `cleanupPeriodDays` 天的 plan 文件及元数据
- `cleanupPeriodDays` 默认 30 天，可在配置文件中调整
- 清理操作在启动阶段完成，不影响后续使用

### F7: 执行状态追踪

- 已执行过的 plan 在列表中显示"已执行"及最近执行时间
- 允许重复执行同一 plan，每次执行刷新 `executed_at`
- 状态通过 `.meta.json` 的 `executed_at` 字段判断

### F8: Plan 完成后确认弹窗

- Plan mode 完成后，`_write_plan_file` 写入 `plans/<slug>.md`
- 弹出确认提示：`是否执行此计划？[/do <slug> / not now]`
- 用户输入 `/do <slug>` / `y` / 回车 → 以 execute mode 执行刚生成的 plan
- 用户输入 `not now` / `n` → 返回 idle，plan 保留在 `plans/` 中

### F9: .meta.json 自愈

- 列出 plan 时，校验 `.meta.json` 中每条记录对应的 `.md` 文件是否真实存在
- 如果文件已被手动删除，从 `.meta.json` 中移除对应条目，保持索引一致
- 此校验在 `list_plans()`、`/do`、`/delete-plan` 时自动执行

## 非功能需求

- N1: `.meta.json` 文件损坏或不存在时，系统应能降级运行（扫描 `plans/` 目录重建索引）
- N2: Plan 文件读写操作不应阻塞 TUI 事件循环
- N3: 清理操作不应影响用户正在进行的对话
- N4: 单次 `/delete-plan` 确认删除，不做二次确认

## 不做的事

- 不支持 plan 文件的编辑功能（用户可手动编辑 `.md` 文件）
- 不支持 plan 文件的重命名
- 不支持 plan 的层级/分组/标签
- 不提供 plan 内容搜索功能
- 不支持跨会话的 plan 执行状态同步
- 不做 `.meta.json` 的并发写入锁（单用户本地使用，无并发问题）

## 验收标准

- AC1: `/plan 创建hello world` → LLM 声明 slug，plan 写入 `plans/<slug>.md`，`.meta.json` 记录创建，文件名不覆盖已有 plan
- AC2: LLM 未声明 slug 时，回退为 `plans/plan-YYYYMMDD-HHMMSS.md`
- AC3: `/do <slug>` → 打印 plan 信息（名称、创建时间、是否已执行），然后执行计划内容
- AC4: `/do`（无参）→ 列出所有 plan 供选择，选择后执行
- AC5: `/delete-plan` → 列出 plan 可多选，确认后删除，不触碰 `docs/`
- AC6: 启动时清理超过 30 天的 plan 文件
- AC7: 执行过的 plan 在列表中显示"已执行"及时间，重复执行后时间刷新
- AC8: Plan 完成后弹窗 `[/do <slug> / not now]`，选 Y → 执行，选 N → 保留 plan 返回 idle
- AC9: 手动删除 `.md` 文件后，下次 list 时 `.meta.json` 中对应条目被自动清除
- AC10: `.meta.json` 不存在时，系统扫描 `plans/` 目录自动重建