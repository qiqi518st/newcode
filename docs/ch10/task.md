# MewCode ch10 - SlashCommand 框架 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `mewcode/__init__.py` | `__version__` → `"0.10.0"` |
| 修改 | `pyproject.toml` | `version` → `"0.10.0"` |
| 新建 | `mewcode/slash/__init__.py` | 包导出 |
| 新建 | `mewcode/slash/registry.py` | CommandKind / CommandDef / CommandRegistry |
| 新建 | `mewcode/slash/parser.py` | parse_command |
| 新建 | `mewcode/slash/context.py` | CommandContext |
| 新建 | `mewcode/slash/ui.py` | UIController Protocol |
| 新建 | `mewcode/slash/commands/__init__.py` | register_all(registry) |
| 新建 | `mewcode/slash/commands/help.py` | /help |
| 新建 | `mewcode/slash/commands/status.py` | /status |
| 新建 | `mewcode/slash/commands/memory.py` | /memory /memory_list /memory_add /memory_clear |
| 新建 | `mewcode/slash/commands/permission.py` | /permission /permission_rules /permission_add /permission_reset |
| 新建 | `mewcode/slash/commands/session.py` | /session /session_list /session_resume /session_new |
| 新建 | `mewcode/slash/commands/plan.py` | /plan /normal |
| 新建 | `mewcode/slash/commands/do.py` | /do |
| 新建 | `mewcode/slash/commands/clear.py` | /clear |
| 新建 | `mewcode/slash/commands/compact.py` | /compact |
| 新建 | `mewcode/slash/commands/review.py` | /review |
| 新建 | `mewcode/slash/commands/legacy.py` | /exit /quit /resume /delete-plan |
| 修改 | `mewcode/tui/app.py` | 分流器 dispatch_slash / completer / 状态栏 / RichUIController / 移除 /exit-plan |
| 修改 | `mewcode/main.py` | 装配接线 + 冲突检测 panic + session_runtime/archive 传入 |
| 修改 | `mewcode/context/manager.py` | 新增 reset_for_new_session()（/clear 重置 compact 子状态） |
| 修改 | `mewcode/permission/checker.py` | count_rules / add_rule / reset_rules |
| 新建 | `tests/test_ch10_registry.py` | 注册中心单测 |
| 新建 | `tests/test_ch10_parser.py` | 解析器单测 |
| 新建 | `tests/test_ch10_commands.py` | 各命令 handler 单测（mock UIController） |
| 新建 | `tests/test_ch10_tab_completion.py` | Tab 补全单测 |
| 新建 | `tests/test_ch10_tui.py` | TUI 接入测试（object.__new__ REPL） |
| 新建 | `tests/test_ch10_integration.py` | 端到端集成测试 |

> 注：`docs/ch10/` 四份文档已在开发前生成并获批（本流程产物，受文档保护规则豁免）。开发中不得再改 docs/。

## 执行顺序

```
T1 → T2 → T3 → T4 → T5 → T6
              ↘ T7（依赖 T2-T5）→ T8（依赖 T7）→ T9（依赖 T8）
                                        ↘ T10（依赖 T7-T9）→ T11 → T12
```

T2-T6 为框架核心（可先完成并单测）；T7 起为命令实现；T10 起为 UI 接入与集成。

---

## T1: 版本 bump 到 0.10.0

**文件：** `mewcode/__init__.py`、`pyproject.toml`
**依赖：** 无
**步骤：**
1. `mewcode/__init__.py` 中 `__version__ = "0.10.0"`
2. `pyproject.toml` 中 `version = "0.10.0"`

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -c "import mewcode; print(mewcode.__version__)"   # 输出 0.10.0
```
提交信息：`chore: bump version to 0.10.0`（独立提交）

---

## T2: registry.py — 注册中心

**文件：** `mewcode/slash/registry.py`
**依赖：** T1
**步骤：**
1. 定义 `CommandKind(Enum)`：`LOCAL` / `UI` / `PROMPT`
2. 定义 `CommandDef` dataclass：name、aliases、description、kind、handler、usage、arg_prompt、hidden
3. 定义 `CommandRegistry`：
   - `_commands: dict[str, CommandDef]`（name + alias 都进索引）
   - `_lock: RLock`
   - `register(cmd)`：写锁下检查 name 与所有 alias 是否已存在，冲突抛 `RuntimeError(f"command name/alias conflict: {conflict}")`
   - `get(name)`：读锁下 `name.lower()` 查找
   - `list(include_hidden=False)`：读锁下按 name 字典序
   - `complete(prefix)`：读锁下前缀匹配 name，排除 hidden

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_registry.py -v
```
单测覆盖：注册重复名/别名冲突抛错且消息含冲突名（AC15）；get 大小写不敏感按别名可达（AC3/AC2）；list 排除 hidden；complete 前缀过滤（AC12/AC14）。

---

## T3: parser.py — 解析器

**文件：** `mewcode/slash/parser.py`
**依赖：** T2
**步骤：**
1. `parse_command(text) -> tuple[str, str] | None`：
   - `text.strip()` 后空串 → None（F2.3 空输入早返回）
   - 不以 `/` 开头 → None
   - 否则 `split(maxsplit=1)`：name（转小写）+ args
2. 纯函数，不依赖任何模块

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_parser.py -v
```
单测：`"/MEMORY add x"` → `("memory", "add x")`；`""`、`"   "`、`"hello"` → None；`"/"` → 边界处理。

---

## T4: ui.py — UIController 抽象接口

**文件：** `mewcode/slash/ui.py`
**依赖：** 无
**步骤：**
1. 定义 `UIController` Protocol（或 ABC），方法见 plan.md 核心数据结构一节：show_message / send_user_message / get_permission_mode / set_permission_mode / get_app_mode / query_token_usage / query_tool_count / query_memory_files / get_model_name / get_cwd / request_exit / request_session_list / request_compact / request_clear_session
2. 方法集最小化（F6.2），不暴露 TUI 内部属性
3. 定义 `NopUI` 测试桩：所有写入方法 no-op、所有查询返回零值（供 T7 起 handler 单测复用，避免逐命令手写 mock）

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_commands.py -v
```
NopUI / mock 满足 Protocol 检查（`isinstance(impl, UIController)` 通过）；命令文件不 import rich/prompt_toolkit（grep 校验）。

---

## T5: context.py — CommandContext

**文件：** `mewcode/slash/context.py`
**依赖：** T4
**步骤：**
1. 定义 `CommandContext` dataclass：registry、ui、agent、conversation、plan_manager、session_runtime、session_archive、memory_manager、permission、version、cwd
2. 各字段可为 None（测试与降级场景），命令实现做空值防御

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_commands.py -v
```
单测：构造完整/部分 ctx 均可用；字段齐全。

---

## T6: commands/__init__.py — 命令装配

**文件：** `mewcode/slash/commands/__init__.py`
**依赖：** T2-T5
**步骤：**
1. `register_all(registry)`：遍历 help/status/memory/permission/session/plan/do/clear/compact/review/legacy 各模块的 `build()`，收集所有 CommandDef 并 `registry.register()`（T10 前先以 stub handler 占位）
2. 提供 `COMMAND_MODULES` 常量列表便于遍历

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_registry.py -v
```
单测：register_all 后 registry.list() 含全部命令，无冲突抛错。

---

## T7: 命令实现 — help/status/memory/permission/session

**文件：** `mewcode/slash/commands/{help,status,memory,permission,session}.py`
**依赖：** T6
**步骤：**
1. **help.py**：遍历 `ctx.registry.list()`（含 hidden 过滤），按 name 字典序输出"name + description"两列对齐（AC1）；不硬编码命令列表（N5）
2. **status.py**：输出权限模式、token 输入/输出、工具数、记忆条目数、模型名、工作目录 6 项，顺序固定（AC4/N6）
3. **memory.py**：`/memory` 列记忆文件名（ctx.memory_manager 的 project/user store 文件名）；`/memory_list` 列条目详情；`/memory_add <类型> <内容>` 调 MemoryStore.apply(create)；`/memory_clear` 调 MemoryStore.clear()（清空作用域全部，AC16）
4. **permission.py**：`/permission` 输出当前权限模式字符串名（AC6）；`/permission_rules` 调 `ctx.permission.count_rules()` 或列出规则；`/permission_add <规则> <效果>` 调新增 `add_rule`；`/permission_reset` 调新增 `reset_rules`
5. **session.py**：`/session` 输出当前会话存档路径 + session 标识（AC7）；`/session_list` 调 `ctx.session_archive.list()`（AC8 后半）；`/session_resume <id>` 调 `ctx.session_runtime.resume(id)`；`/session_new` 调 `create_new()`。session_runtime 为 None 时输出提示（未接线场景）

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_commands.py -v
```
单测：NopUI（或 mock UIController）+ mock MemoryStore/PermissionChecker/SessionRuntime 驱动真实 handler 路径；每测试 docstring 标注防的 bug。

---

## T8: 命令实现 — do/plan/normal/clear/compact/legacy

**文件：** `mewcode/slash/commands/{do,plan,clear,compact,legacy}.py`（plan.py 含 /normal）
**依赖：** T7
**步骤：**
1. **do.py**：`/do <slug>` → `ctx.plan_manager.get_plan` + `read_plan_content` → 执行；`/do` 无参 → 经 `ctx.ui` 弹计划列表选择后执行（语义与 app.py 现有 `_process_input` 完全一致，N8/AC10）
2. **plan.py**：`/plan` 进入计划模式（AppMode=PLAN + 权限模式联动 PLAN）；`/normal` 退出计划模式（AppMode=NORMAL）；移除 /exit-plan（F8.26）
3. **clear.py**：`/clear` → `ctx.ui.request_clear_session()`（RichUIController 内部按 plan.md「/clear 原子重置顺序」：`session_runtime.create_new()`（close 旧 writer → 新会话上下文 → 新 writer → 重建 Conversation）→ `context_manager.reset_for_new_session()`（清 L1 替换账本 + 自动闸 + 锚点）→ token/回合归零 → AppMode=NORMAL）→ 输出 notice（F8.7/AC8）
4. **compact.py**：`/compact` → `ctx.ui.request_compact()`（走同一事件流推送压缩进度，N8）
5. **legacy.py**：`/exit`、`/quit`（= /exit 别名）、`/resume`（= /session_resume 隐藏别名）、`/delete-plan`（存量迁移）；/exit 触发 `ctx.ui.request_exit()`（内部先取消主 cancel scope，N12）

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_commands.py tests/test_ch10_tui.py -v
```
单测：mock 驱动；复用/迁移现有 test_tui_resume.py / test_tui_compact.py 断言到新框架后仍通过（AC10）。

---

## T9: 命令实现 — review.py（KindPrompt）

**文件：** `mewcode/slash/commands/review.py`
**依赖：** T8
**步骤：**
1. 定义固定"代码审查请求"文本（含审查关键字，如"review"、"code review"、"diff" 相关引导但不读 diff）
2. handler：`ctx.ui.send_user_message(固定文本)` → 触发 `ctx.agent.run(prompt_text, mode="normal")` 流式回合（F8.13/F3.4）
3. 注入消息与真实用户消息走相同持久化路径（conversation + SessionWriter）

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_commands.py -v
```
单测：mock agent 断言收到含"审查/review"关键字的 user 消息且不读 git diff（AC9）；存档中新增 user 角色消息。

---

## T10: tui/app.py 接入

**文件：** `mewcode/tui/app.py`
**依赖：** T7-T9
**步骤：**
1. 新增 `RichUIController` 类：实现 UIController（包住现有 console.print / mode 切换 / token 统计 / _run_stream / _handle_resume / _handle_session_command / _handle_compact / 退出）；`request_clear_session` 内部按 plan.md「/clear 原子重置顺序」执行
2. `ContextManager` 新增 `reset_for_new_session()`：清空 `ContentReplacementState` 账本（_seen_ids/_replacements）、`AutoCompactGate` 计数归零、`usage_anchor`/`anchor_msg_len` 归零（供 request_clear_session 调用）。**实现方式：重建实例**——`self._state = ContentReplacementState()`、`self._auto_gate = AutoCompactGate()`、锚点归零（单事件循环无并发顾虑，比给两个类各加 reset 方法省事）
3. 新增 `dispatch_slash(text) -> bool`：`parse_command(text)` → `registry.get(name)` 命中 → 状态机门（KindUI/KindPrompt 仅 idle，非 idle 输出"请等待当前任务完成"）→ `try/except` 包裹 `await handler(ctx, args)`（异常 → `show_message("命令执行失败: {exc}", style="red")` 上屏，不崩 REPL）→ 返回 True；未命中且 `/` 开头 → 引导 /help → True；非命令/空输入 → False。`REPL.run()` 调用它，返回 False 才走 AgentLoop
4. PromptSession 增加 completer：注册表派生（`/` 前缀匹配、排除 hidden、显示 name+description、单匹配直补/多匹配弹列表，F9）
5. `_toolbar` 从注册表派生高频命令提示（仅 /help 硬编码，N5）
6. 删除 `_is_known_command` 与 `_process_input` 中已迁移的命令分支；删除 /exit-plan 分支

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_tui.py tests/test_tui_wiring.py tests/test_tui_resume.py tests/test_tui_compact.py -v
```
单测：object.__new__(REPL) + mock UIController 驱动；/ 命令不触发 Agent.run（AC2）；普通输入走 AgentLoop（分流正确）。

---

## T11: main.py 接线 + permission API

**文件：** `mewcode/main.py`、`mewcode/permission/checker.py`
**依赖：** T10
**步骤：**
1. checker.py 新增 `count_rules()` / `add_rule(pattern, effect)` / `reset_rules()`（镜像 persist_local_allow 写回路径，T8 依赖）
2. main.py：构造 `registry = CommandRegistry()` → `register_all(registry)` → try/except 捕获冲突 RuntimeError → 打印冲突名字 → `sys.exit(1)`（N4/F1.3）
3. 组装 `CommandContext(registry, ui=RichUIController(...), agent, conversation, plan_manager, session_runtime, session_archive, memory_manager, permission, version, cwd)`
4. `REPL(..., registry=registry, ctx=ctx)` 传入；**补上 session_runtime / session_archive 传入 REPL**（ch09 遗留缺口）

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_integration.py -v
```
集成测试：启动无 panic（AC15 反向：正常注册不炸）；/help /status 可用。

---

## T12: 集成测试收尾

**文件：** `tests/test_ch10_integration.py`（补全）
**依赖：** T11
**步骤：**
1. 端到端：分流（/ 命令不触 Agent、普通输入触 Agent）；/help 输出；/clear → /session_list 见旧会话（AC8）；/review 触发流式（AC9）
2. 命令异常兜底：某 handler 抛异常时 REPL 不崩、错误信息上屏（dispatch_slash 的 try/except）
3. 状态机：STREAMING 态注入 /clear 被拒（N3a）
4. Tab 补全：/s 前缀过滤；hidden 命令排除但可执行（AC12/AC14）
5. 每个测试 docstring 标注防的 bug（AGENTS.md 要求）

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/ -v
```
全量测试通过；ruff check 通过；确认 docs/ 未被改动。
