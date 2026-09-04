# NewCode Plan 文件管理 — 任务拆解 (task-plan-files.md)

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `newcode/plans/__init__.py` | 导出 PlanManager, PlanMeta |
| 新建 | `newcode/plans/manager.py` | PlanManager 类 + PlanMeta dataclass |
| 修改 | `newcode/config/schema.py` | 移除 plan_file，新增 cleanup_period_days |
| 修改 | `newcode/config/loader.py` | 解析 cleanup_period_days |
| 修改 | `newcode/prompt/resources.py` | PLAN_MODE_REMINDER 增加 slug 声明 |
| 修改 | `newcode/tui/app.py` | REPL 重构，使用 PlanManager |
| 修改 | `newcode/main.py` | 创建 PlanManager，启动清理 |
| 新建 | `tests/test_plan_manager.py` | PlanManager 单元测试 |
| 修改 | `tests/test_tui_wiring.py` | 适配 PlanManager 新接口 |

## T1: 创建 plans 包和 PlanMeta

**文件：** `newcode/plans/__init__.py`、`newcode/plans/manager.py`
**依赖：** 无
**步骤：**
1. 创建 `newcode/plans/` 目录
2. 创建 `__init__.py`，导出 `PlanManager`, `PlanMeta`
3. 定义 `PlanMeta` dataclass：`slug`, `file`, `task`, `created_at`, `executed_at`

**验证：** `from newcode.plans import PlanManager, PlanMeta` 导入成功

## T2: 实现 PlanManager 核心方法

**文件：** `newcode/plans/manager.py`
**依赖：** T1
**步骤：**
1. 实现 `__init__`：接收 `plans_dir`，创建目录，记录 `.meta.json` 路径
2. 实现 `_load_meta()`：读取 JSON，损坏返回 `{}`
3. 实现 `_save_meta(data)`：写入 JSON（ensure_ascii=False, indent=2）
4. 实现 `_extract_slug(content)`：正则 `<!-- slug: xxx -->`，清洗后返回，回退日期格式
5. 实现 `_extract_task(content)`：取首个 `# 标题` 或首行前 80 字符
6. 实现 `create_plan(task, content)`：提取 slug → 写 `.md` 文件 → 更新 `.meta.json` → 返回 slug
7. 实现 `list_plans()`：加载 meta → 校验文件存在性（自愈）→ 返回 PlanMeta 列表（按时间倒序）
8. 实现 `get_plan(slug)`：查 meta 返回单个 PlanMeta
9. 实现 `read_plan_content(slug)`：读 `.md` 文件返回内容
10. 实现 `mark_executed(slug)`：更新 `executed_at` 为当前时间
11. 实现 `delete_plans(slugs)`：从 meta 移除 → 删除 `.md` 文件
12. 实现 `cleanup_old(days)`：计算截止时间 → 找出过期 → 调用 `delete_plans`

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_plan_manager.py -v` 全部通过

## T3: 更新配置

**文件：** `newcode/config/schema.py`、`newcode/config/loader.py`
**依赖：** 无
**步骤：**
1. `schema.py`：移除 `plan_file` 字段，新增 `cleanup_period_days: int = 30`
2. `loader.py`：移除 `plan_file` 解析，新增 `cleanup_period_days` 解析（`raw.get("cleanup_period_days", 30)`）
3. `loader.py`：`_parse()` 中两处 `Config(...)` 调用同步更新
4. `loader.py`：`load_ccswitch()` 中 `Config(...)` 调用无需改动（使用默认值）

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "from newcode.config.loader import load; print('OK')"`

## T4: 更新 PLAN_MODE_REMINDER

**文件：** `newcode/prompt/resources.py`
**依赖：** 无
**步骤：**
1. 在 `PLAN_MODE_REMINDER` 计划格式要求中新增一条：
   ```
   - 在计划开头用 HTML 注释声明一个简短的 slug 标识符，
     格式为 <!-- slug: 简短英文标识 -->
     例如：<!-- slug: add-login-page -->
   ```

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "from newcode.prompt.resources import PLAN_MODE_REMINDER; assert 'slug' in PLAN_MODE_REMINDER"`

## T5: REPL 重构 — 构造函数和 /plan

**文件：** `newcode/tui/app.py`
**依赖：** T2, T3, T4
**步骤：**
1. 导入 `from ..plans.manager import PlanManager, PlanMeta`
2. `__init__` 签名：`plan_file: str` → `plan_manager: PlanManager`
3. 移除 `self.plan_file`，新增 `self.plan_manager`、`self._pending_slug = ""`、`self._executing_slug = ""`
4. `_consume_agent_events` 中 plan 完成处理改为：
   ```python
   slug = self.plan_manager.create_plan("", buffer)
   self._pending_plan = buffer
   self._pending_slug = slug
   self._console.print(f"计划已保存: plans/{slug}.md", style="bold green")
   ```
5. 移除 `_read_plan_file` 和 `_write_plan_file` 方法

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_tui_wiring.py -v` 全部通过

## T6: REPL 重构 — 确认弹窗

**文件：** `newcode/tui/app.py`
**依赖：** T5
**步骤：**
1. 确认弹窗文本改为 `f"是否执行此计划？[/do {slug} / not now] "`
2. 接受 `f"/do {slug}"` 作为有效确认输入
3. 确认后设置 `self._executing_slug = slug`

**验证：** 手动测试：`/plan 创建测试` → 弹窗显示 `[/do <slug> / not now]` → Y 执行

## T7: REPL 重构 — /do 命令

**文件：** `newcode/tui/app.py`
**依赖：** T5
**步骤：**
1. `/do <slug>`：调用 `plan_manager.get_plan(slug)` → 打印 plan 信息（名称、创建时间、是否已执行）→ `read_plan_content()` → execute mode
2. `/do`（无参）：调用 `plan_manager.list_plans()` → 展示编号列表 → 用户输入序号或 slug → 同上
3. 执行完成后（DONE NATURAL）调用 `plan_manager.mark_executed(self._executing_slug)`

**验证：** 手动测试：`/do` 列出计划 → 选择执行 → 打印 plan 信息 → 执行完成 → 再次 `/do` 显示"已执行"

## T8: REPL 重构 — /delete-plan

**文件：** `newcode/tui/app.py`
**依赖：** T5
**步骤：**
1. 新增 `_delete_plan_interactive()` 方法
2. 调用 `plan_manager.list_plans()` 获取列表
3. 使用 `checkboxlist_dialog` 展示多选列表（每行：slug、task、状态、日期）
4. 确认后调用 `plan_manager.delete_plans(result)`
5. 在 `_process_input` 中添加 `/delete-plan` 分支
6. 状态栏添加 `/delete-plan` 提示

**验证：** 手动测试：`/delete-plan` → 勾选 → 确认 → 文件被删除

## T9: 更新 main.py

**文件：** `newcode/main.py`
**依赖：** T3, T5
**步骤：**
1. 导入 `from newcode.plans import PlanManager`
2. 创建 `plan_manager = PlanManager(os.path.join(os.getcwd(), "plans"))`
3. 启动时调用 `plan_manager.cleanup_old(config.cleanup_period_days)`
4. REPL 构造改为 `REPL(agent, renderer, plan_manager=plan_manager, default_mode=config.default_mode)`

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "from newcode.main import main; print('OK')"`

## T10: 编写 PlanManager 单元测试

**文件：** `tests/test_plan_manager.py`
**依赖：** T2
**步骤：**
1. 使用 `tmp_path` fixture 创建临时 plans 目录
2. 测试用例：

| 测试 | 验证点 |
|------|--------|
| `test_extract_slug_from_comment` | `<!-- slug: my-plan -->` → `"my-plan"` |
| `test_extract_slug_fallback` | 无 slug 注释 → 日期格式 |
| `test_extract_slug_sanitize` | 特殊字符 → 连字符 |
| `test_create_plan` | 创建 `.md` 和 `.meta.json` |
| `test_list_plans_sorted` | 按时间倒序 |
| `test_get_plan` | 找到/找不到 |
| `test_read_plan_content` | 返回正确内容 |
| `test_mark_executed` | 设置 `executed_at` |
| `test_mark_executed_twice` | 重复执行刷新时间 |
| `test_delete_plans` | 移除文件和元数据 |
| `test_cleanup_old` | 删旧留新 |
| `test_cleanup_old_zero` | `cleanup_old(0)` 返回 0 |
| `test_meta_corrupted` | 损坏 JSON 返回空列表 |
| `test_self_heal` | 手动删文件后 list 清除条目 |

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_plan_manager.py -v` 全部通过

## T11: 更新 TUI 接线测试

**文件：** `tests/test_tui_wiring.py`
**依赖：** T5
**步骤：**
1. `_make_repl` 中：移除 `repl.plan_file = "plan.md"`
2. 新增 `import tempfile` 和 `from newcode.plans import PlanManager`
3. 新增 `repl.plan_manager = PlanManager(tempfile.mkdtemp())`
4. 新增 `repl._pending_slug = ""` 和 `repl._executing_slug = ""`

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_tui_wiring.py -v` 全部通过

## T12: 全量回归测试

**依赖：** T1-T11
**步骤：**
1. 运行全部测试
2. 确认 37 + 14 = 51 个测试通过

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/ -v` 全部通过

## 执行顺序

```
T1 ──→ T2 ──→ T10 (PlanManager 测试)
              ↘
T3 (配置) ───→ T9 (main.py)
T4 (prompt) ─┘
              ↘
T5 ──→ T6 ──→ T7 ──→ T8 ──→ T11 (TUI 测试)
                                ↘
                              T12 (全量回归)
```