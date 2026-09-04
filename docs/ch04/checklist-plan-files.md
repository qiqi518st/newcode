# NewCode Plan 文件管理 — 验收清单 (checklist-plan-files.md)

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] PlanManager 所有方法已实现且可调用（验证：`from newcode.plans import PlanManager` 成功）
- [ ] Config 移除 `plan_file`，新增 `cleanup_period_days`（验证：`from newcode.config.schema import Config; print(Config.__dataclass_fields__)` 确认字段变化）
- [ ] PLAN_MODE_REMINDER 包含 slug 声明要求（验证：`assert 'slug' in PLAN_MODE_REMINDER`）
- [ ] REPL 使用 PlanManager 替代 `_read_plan_file`/`_write_plan_file`（验证：grep 确认两个方法已移除）
- [ ] main.py 启动时创建 PlanManager 并执行清理（验证：`python -c "from newcode.main import main"` 无导入错误）

## 集成

- [ ] PlanManager 正确读写 `.meta.json`（验证：`test_plan_manager.py` 全部通过）
- [ ] REPL 正确调用 PlanManager 的 create_plan / get_plan / list_plans / delete_plans（验证：`test_tui_wiring.py` 全部通过）
- [ ] 启动清理正确调用 `plan_manager.cleanup_old()`（验证：创建过期 plan 文件，启动后确认被删除）

## 编译与测试

- [ ] 项目编译无错误（验证：`python -c "import newcode"` 无错误）
- [ ] 所有单元测试通过（验证：`pytest tests/ -v` 全部通过）
- [ ] 无 lint 错误（验证：`ruff check newcode/` 无错误）

## 端到端场景

### 场景 1：Plan 创建与确认执行
- [ ] `/plan 创建一个 hello world 脚本` → Agent 只读模式探查 → 生成计划（含 slug 声明）→ 输出 `计划已保存: plans/<slug>.md` → 弹窗 `[/do <slug> / not now]` → 选 `/do <slug>` → 打印 plan 信息 → 执行计划 → 文件被创建

### 场景 2：Plan 创建但不执行
- [ ] `/plan 分析项目结构` → 生成计划 → 弹窗 → 选 `not now` → 返回 idle → plan 文件保留在 `plans/` 中

### 场景 3：/do 无参选择
- [ ] `/do`（无参数）→ 列出所有 plan（序号、slug、任务、创建时间、执行状态）→ 输入序号 → 执行对应 plan

### 场景 4：/do <slug> 直接执行
- [ ] `/do create-hello-world` → 打印 plan 信息（名称、创建时间、是否已执行）→ 执行计划

### 场景 5：重复执行
- [ ] 执行过的 plan 在列表中显示"已执行"及时间 → 再次 `/do <slug>` → 执行后时间刷新

### 场景 6：/delete-plan 多选删除
- [ ] `/delete-plan` → 列出所有 plan（名称、创建时间、状态）→ 空格勾选 → Enter 确认 → `[y/N]` 确认 → 文件被删除 → `.meta.json` 更新

### 场景 7：/delete-plan 取消
- [ ] `/delete-plan` → 勾选 → Esc 取消 → 文件保留

### 场景 8：启动清理
- [ ] 手动在 `plans/` 下创建超过 30 天的 plan 文件和 `.meta.json` 条目 → 启动 NewCode → 过期文件被自动删除

### 场景 9：.meta.json 自愈
- [ ] 手动删除 `plans/<slug>.md` 文件（保留 `.meta.json` 中的条目）→ `/do` 列出 plan → 对应条目被自动清除

### 场景 10：安全边界
- [ ] `/delete-plan` 列出的文件全部在 `plans/` 目录下 → `docs/ch04/plan.md` 不受影响

### 场景 11：LLM 未声明 slug
- [ ] 模拟 LLM 不输出 `<!-- slug: xxx -->` → plan 文件以 `plan-YYYY-MM-DD-HHMMSS.md` 格式保存

### 场景 12：.meta.json 损坏
- [ ] 手动将 `.meta.json` 改为无效 JSON → 启动或 `/do` → 系统不崩溃 → 降级扫描 `plans/` 目录重建