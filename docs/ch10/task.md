# NewCode ch10 - SlashCommand 框架 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `newcode/__init__.py` | `__version__` → `"0.10.0"` |
| 修改 | `pyproject.toml` | `version` → `"0.10.0"` |
| 新建 | `newcode/slash/__init__.py` | 包导出 |
| 新建 | `newcode/slash/registry.py` | CommandKind / CommandDef / CommandRegistry |
| 新建 | `newcode/slash/parser.py` | parse_command |
| 新建 | `newcode/slash/context.py` | CommandContext |
| 新建 | `newcode/slash/ui.py` | UIController Protocol |
| 新建 | `newcode/slash/commands/__init__.py` | register_all(registry) |
| 新建 | `newcode/slash/commands/help.py` | /help |
| 新建 | `newcode/slash/commands/status.py` | /status |
| 新建 | `newcode/slash/commands/memory.py` | /memory /memory_list /memory_add /memory_clear |
| 新建 | `newcode/slash/commands/permission.py` | /permission /permission_rules /permission_add /permission_reset |
| 新建 | `newcode/slash/commands/session.py` | /session /session_list /session_resume /session_new |
| 新建 | `newcode/slash/commands/plan.py` | /plan /normal |
| 新建 | `newcode/slash/commands/do.py` | /do |
| 新建 | `newcode/slash/commands/clear.py` | /clear |
| 新建 | `newcode/slash/commands/compact.py` | /compact |
| 新建 | `newcode/slash/commands/review.py` | /review |
| 新建 | `newcode/slash/commands/legacy.py` | /exit /quit /resume /delete-plan |
| 修改 | `newcode/tui/app.py` | 分流器 dispatch_slash / completer / 状态栏 / RichUIController / 移除 /exit-plan |
| 修改 | `newcode/main.py` | 装配接线 + 冲突检测 panic + session_runtime/archive 传入 |
| 修改 | `newcode/context/manager.py` | 新增 reset_for_new_session()（T0b，/clear 重置 compact 子状态） |
| 修改 | `newcode/permission/checker.py` | count_rules / add_rule / reset_rules（T0a） |
| 新建 | `tests/test_ch10_registry.py` | 注册中心单测 |
| 新建 | `tests/test_ch10_parser.py` | 解析器单测 |
| 新建 | `tests/test_ch10_commands.py` | 各命令 handler 单测（mock UIController） |
| 新建 | `tests/test_ch10_tab_completion.py` | Tab 补全单测 |
| 新建 | `tests/test_ch10_tui.py` | TUI 接入测试（object.__new__ REPL） |
| 新建 | `tests/test_ch10_integration.py` | 端到端集成测试 |

> 注：`docs/ch10/` 四份文档已在开发前生成并获批（本流程产物，受文档保护规则豁免）。开发中不得再改 docs/。

## 执行顺序

```
T0a, T0b（并行）→ T1 → T2 → T3 → T4 → T5 → T6
                                  ↘ T7（依赖 T2-T5, T0a）→ T8（依赖 T7, T0a）→ T9（依赖 T8）
                                                                       ↘ T10（依赖 T7-T9, T0b）→ T11（依赖 T10, T0a, T0b）→ T12 → T13
```

T0a/T0b 为被多任务消费的底层 helper（permission API / ContextManager.reset_for_new_session），独立前置，互不依赖可并行；T2-T6 为框架核心（可先完成并单测）；T7 起为命令实现；T10 起为 UI 接入与集成。

> **依赖倒挂修正说明**：原稿把 `permission count_rules/add_rule/reset_rules` 与 `ContextManager.reset_for_new_session` 埋在本文件 T11/T10 内，但 T7 的 permission.py 命令、T8 的 clear.py 命令已调用它们，造成 T7/T8 隐性依赖 T11/T10。现拆为 T0a/T0b 前置（借鉴旧稿 T0 前置 helper 模式），所有消费方（T7/T8/T10/T11）显式依赖。

---

## T0a: permission/checker.py 新增 count_rules / add_rule / reset_rules

**文件：** `newcode/permission/checker.py`、`tests/test_ch10_commands.py`（或新增 `tests/test_ch10_permission_api.py`）
**依赖：** 无
**步骤：**
1. `count_rules() -> int`：统计三层规则文件（local/project/user）规则总数
2. `add_rule(pattern, effect) -> None`：写入本地规则，镜像现有 `persist_local_allow` 的写回路径
3. `reset_rules() -> int`：清空本地规则，返回删除条数
4. 单测：count 初值、add 后 count+1、reset 后归零且返回删除条数；每个测试 docstring 标注防的 bug

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_commands.py -v
```
> 本任务为 T7 的 permission.py、T8 的 permission 系命令提供被调方法，先于命令实现落地。

---

## T0b: context/manager.py 新增 reset_for_new_session

**文件：** `newcode/context/manager.py`
**依赖：** 无
**步骤：**
1. 新增 `reset_for_new_session() -> None`：清空 `ContentReplacementState` 账本（`_seen_ids`/`_replacements`）、`AutoCompactGate` 计数归零、`usage_anchor`/`anchor_msg_len` 归零（/clear 用）
2. **实现方式：重建实例**——`self._state = ContentReplacementState()`、`self._auto_gate = AutoCompactGate()`、锚点归零（单事件循环无并发顾虑，比给两个类各加 reset 方法省事）

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -c "from newcode.context.manager import ContextManager; ContextManager().reset_for_new_session()"
```
> 本任务为 T10 的 request_clear_session（/clear 原子重置）提供被调方法。

---

## T1: 版本 bump 到 0.10.0

**文件：** `newcode/__init__.py`、`pyproject.toml`
**依赖：** 无
**步骤：**
1. `newcode/__init__.py` 中 `__version__ = "0.10.0"`
2. `pyproject.toml` 中 `version = "0.10.0"`

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -c "import newcode; print(newcode.__version__)"   # 输出 0.10.0
```
提交信息：`chore: bump version to 0.10.0`（独立提交）

---

## T2: registry.py — 注册中心

**文件：** `newcode/slash/registry.py`
**依赖：** T1
**步骤：**
1. 定义 `CommandKind(Enum)`：`LOCAL` / `UI` / `PROMPT`
2. 定义 `CommandDef` dataclass：name、aliases、description、kind、handler、usage、arg_prompt、hidden
3. 定义 `CommandRegistry`：
   - `_commands: dict[str, CommandDef]`（name + alias 都进索引）
   - `_lock: RLock`
   - `register(cmd)`：写锁下检查 name 与所有 alias 是否已存在，冲突抛 `RuntimeError(f"command name/alias conflict: {conflict}")`
   - `get(name)`：读锁下 `name.lower()` 查找
   - `list(include_hidden=False)`：读锁下按 name 字典序，返回排序后的新 list 拷贝（不把内部 dict 引用直接外泄，防外部改动）
   - `complete(prefix)`：读锁下前缀匹配 name，排除 hidden

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_registry.py -v
```
单测覆盖：注册重复名/别名冲突抛错且消息含冲突名（AC15）；get 大小写不敏感按别名可达（AC3/AC2）；list 排除 hidden；complete 前缀过滤（AC12/AC14）。

---

## T3: parser.py — 解析器

**文件：** `newcode/slash/parser.py`
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
单测用**表驱动**覆盖完整边界（每项附期望返回值）：
- `"/MEMORY add x"` → `("memory", "add x")`（大小写不敏感）
- `"/help"` → `("help", "")`；`"  /HELP  "` → `("help", "")`（strip 前导/尾随空白，name 小写）
- `"/help xx"` → `("help", "xx")`（参数保留）；`"/help  "` → `("help", "")`（尾随空白视为无参）
- `""`、`"   "`、`"hello"` → None（非命令/空输入早返回）
- `"/"` → None（退化输入）；`"//double"` → `("", "/double")` 或按实现约定的 None（明确写死一种）；`"/ /help"` → name 为空 → 按约定返回 None（明确写死）
> 补充说明（dispatch 侧消费约定）：parse 返回的退化形态（`"/"`、`"/ xx"`）在 dispatch_slash 走"未命中"分支时，引导文案**不要拼 `"/+name"`**，避免出现 `"未知命令: /, ..."` 这种悬空斜杠；直接用 `"/"` 开头的固定引导（如"输入 /help 查看可用命令"）即可。

---

## T4: ui.py — UIController 抽象接口

**文件：** `newcode/slash/ui.py`
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

**本任务同时提供 `RecordingUI` 可观测桩**（定义在 `slash/ui.py`，供 T7/T8 handler 单测复用）：
- 继承 NopUI，额外记录调用：`show_message`（text+style）、`send_user_message`（text）、`set_permission_mode`/`get_app_mode`、`request_compact`/`request_clear_session`/`request_exit`
- 暴露 `calls: list[tuple[str, ...]]` 与 `messages: list[str]`，测试据此断言"调用了什么、调了几次、文本含哪些 key"（借鉴旧稿 RecordingUI 设计，比逐命令手写 mock 直观，且覆盖 CLAUDE.md"接线测试必须自动跑"要求）
- 断言风格示例：`rec.messages` 恰好 1 条且含 6 个 key；busy 态下 `show_message` 被调、`request_compact` 未被调

---

## T5: context.py — CommandContext

**文件：** `newcode/slash/context.py`
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

**文件：** `newcode/slash/commands/__init__.py`
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

**文件：** `newcode/slash/commands/{help,status,memory,permission,session}.py`
**依赖：** T6 + T0a（permission.py 调 `ctx.permission.count_rules()` 等，方法由 T0a 提供）
**步骤：**
1. **help.py**：遍历 `ctx.registry.list()`（含 hidden 过滤），按 name 字典序输出"name + description"两列对齐——先算最长 name 长度做 key 列宽，`name.ljust(w)` 填充后再拼 description（AC1）；不硬编码命令列表（N5）
2. **status.py**：输出权限模式、token 输入/输出、工具数、记忆条目数、模型名、工作目录 6 项，顺序固定（AC4/N6）；key 列宽固定为 6 个 key 中最长者，每行 value 对齐
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

**文件：** `newcode/slash/commands/{do,plan,clear,compact,legacy}.py`（plan.py 含 /normal）
**依赖：** T7 + T0a（permission 系命令依赖 count/add/reset 方法）
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

**文件：** `newcode/slash/commands/review.py`
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

**文件：** `newcode/tui/app.py`
**依赖：** T7-T9 + T0b（request_clear_session 调 `context_manager.reset_for_new_session()`，方法由 T0b 提供；本任务不再实现）
**步骤：**
0. **红测试先行（顺序前置，动手改代码前先做）**：先跑 `tests/test_tui_wiring.py tests/test_tui_resume.py tests/test_tui_compact.py` 建立全绿基线；待本任务切换到新分发器后，确认旧 slash 相关用例因路径变迁而变红——证明旧断言确实在护卫旧 `_process_input`/`_is_known_command` 路径，后续迁移才有依据（借鉴模板 T13 红测试先行）
1. 新增 `RichUIController` 类：实现 UIController（包住现有 console.print / mode 切换 / token 统计 / _run_stream / _handle_resume / _handle_session_command / _handle_compact / 退出）；`request_clear_session` 内部按 plan.md「/clear 原子重置顺序」执行——`session_runtime.create_new()`（ch09 现成封装：close 旧 writer → 新会话上下文 → 新 writer → 重建 ConversationManager）→ **调 T0b 的 `context_manager.reset_for_new_session()`** → token 计数与回合数归零 → AppMode=NORMAL
2. 新增 `dispatch_slash(text) -> bool`：`parse_command(text)` → `registry.get(name)` 命中 → 状态机门（KindUI/KindPrompt 仅 idle，非 idle 输出"请等待当前任务完成"）→ `try/except` 包裹 `await handler(ctx, args)`（异常 → `show_message("命令执行失败: {exc}", style="red")` 上屏，不崩 REPL）→ 返回 True；未命中且 `/` 开头 → 引导 /help → True；非命令/空输入 → False。`REPL.run()` 调用它，返回 False 才走 AgentLoop
3. **退化输入文案约定**：parse 对 `"/"`、`"/ xx"` 的退化形态走"未命中"分支时，引导文案**不要拼 `"/+name"`**（避免 `"未知命令: /, ..."` 悬空斜杠），统一用 `/help` 引导文案（与 T3 说明一致）
4. PromptSession 增加 completer：注册表派生（`/` 前缀匹配、排除 hidden、显示 name+description、单匹配直补/多匹配弹列表，F9）
5. `_toolbar` 从注册表派生高频命令提示（仅 /help 硬编码，N5）
6. 删除 `_is_known_command` 与 `_process_input` 中已迁移的命令分支；删除 /exit-plan 分支

**验证：**
```bash
export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch10_tui.py tests/test_tui_wiring.py tests/test_tui_resume.py tests/test_tui_compact.py -v
```
单测：object.__new__(REPL) + mock UIController 驱动；/ 命令不触发 Agent.run（AC2）；普通输入走 AgentLoop（分流正确）；`"/"`、`"/ xx"` 退化输入不出现 `"未知命令: /"` 悬空斜杠文案；**`/Help` 与 `/help` 行为一致（AC3，大小写不敏感）**；**`/help` 输出含全部已注册命令名（AC1，来自 registry.list() 单一信源，不硬编码）。**

---

## T11: main.py 接线

**文件：** `newcode/main.py`
**依赖：** T10 + T0a + T0b
**步骤：**
1. main.py：构造 `registry = CommandRegistry()` → `register_all(registry)` → try/except 捕获冲突 RuntimeError → 打印冲突名字 → `sys.exit(1)`（N4/F1.3）
2. 组装 `CommandContext(registry, ui=RichUIController(...), agent, conversation, plan_manager, session_runtime, session_archive, memory_manager, permission, version, cwd)`
3. `REPL(..., registry=registry, ctx=ctx)` 传入；**补上 session_runtime / session_archive 传入 REPL**（ch09 遗留缺口，F8.6/F8.21 依赖）
4. 本任务不再实现 permission API（已由 T0a 提供）与 ContextManager.reset_for_new_session（已由 T0b 提供），仅组装入 CommandContext

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

---

## T13: 端到端人工冒烟（真实终端/tmux 实跑）

**文件：** 无（运行可执行文件）
**依赖：** T12
**步骤：**
1. `uv sync`（或 `pip install -e ".[dev]"`）；`tmux new-session -d -s mewspec 'uv run newcode'`（无 tmux 环境时改用真实终端直接 `python -m newcode`，或对应项标"待人工验证"）
2. 按 checklist.md 的「端到端场景」全部项（当前 12 项）+ 存量 slash 行为逐条实跑并记录观测：
   - 键入 `/` 补全立即激活（AC11）；`/s` 过滤为 /session* 候选项、多匹配弹列表（AC12/AC13）
   - `/help` 全部命令两列对齐、`/status` 六项固定顺序（AC1/AC4）
   - `/memor<Tab>` 补全 `/memory`；`/do` 无参弹计划列表 / 带参执行（AC10）
   - `/permission mode acceptEdits` 后状态栏模式徽章变化（F11.1）
   - `/clear` 后 `/session_list` 见旧会话；`/review` 进入流式且存档新增含审查关键字的 user 消息（AC8/AC9）
   - 未知 `/foobar` 引导 /help 且不触发 LLM；`/Help` 大小写混合行为与 `/help` 一致（AC2/AC3）
3. 启动期冲突检测实跑：临时给某条已注册命令再注册一个同名，重启后应打印冲突名并立即退出（AC15）
4. 全部通过后 `tmux kill-session -t mewspec`
5. 无法在真实终端执行的项目（tmux 不可用、需人工交互等）在验收报告单独标「待人工验证」，说明原因与替代验证，不得混入「通过」

**验证：** checklist.md「端到端场景」与「待人工验证」逐项落实并记录证据；期间出错则修复后从失败项重跑
