# NewCode ch13 - 多 Agent 分发架构 Tasks

> 顺序执行。每完成一个任务跑 `export PYTHONIOENCODING=utf-8 && ruff check <改动文件>` 确保无 lint 错；接入主流程的任务（T20）做完后立刻跑一次端到端冒烟（T21）再进下一项。**文档保护**：任何批量命令（ruff、git）跑完先确认 docs/ 未被动过；测试/验证期间禁止修改 docs/ 下任何文件。
>
> 版本管理：本章开发前先升 0.13.0（T1），`newcode/__init__.py` 与 `pyproject.toml` 同步。
>
> **范围决策（已确认）**：本期**不做 ESC 手动切换后台**（B 决策，对齐参考）——后台仅两条进入路径（显式 `run_in_background` + 前台超时自动切）；`foreground_sub_agent` 跟踪字段预留后续章节。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `newcode/__init__.py`、`pyproject.toml` | 版本号 0.12.0 → 0.13.0 |
| 新建 | `newcode/subagent/__init__.py` | 包导出（AgentDefinition/Source/Catalog/TaskManager/...） |
| 新建 | `newcode/subagent/types.py` | AgentDefinition / Source / DefinitionParseError / 文案常量 |
| 新建 | `newcode/subagent/config.py` | AgentConfig + load_agent_config（agents: 段三层合并）+ effective_ 方法 |
| 新建 | `newcode/subagent/parser.py` | parse_definition（复用 skills frontmatter 分离） |
| 新建 | `newcode/subagent/catalog.py` | Catalog 四层加载 + 优先级 + resolve/list + fork_definition |
| 新建 | `newcode/subagent/builtin/{general-purpose,explore,plan,verifier}.md` | 4 个内置角色 |
| 新建 | `newcode/subagent/fork.py` | build_forked_messages + FORK_BOILERPLATE + is_fork_context |
| 新建 | `newcode/subagent/launcher.py` | SubAgentLauncher（子 Registry/模型/子 Agent/前台后台分派） |
| 新建 | `newcode/subagent/manager.py` | BackgroundTask + TaskManager（launch/foreground/adopt/continue 同 id/清理/done） |
| 新建 | `newcode/subagent/errors.py` | MaxTurnsReached |
| 新建 | `newcode/tools/filter.py` | GLOBAL_DENY / ASYNC_AGENT_ALLOWED_TOOLS / apply_agent_tool_filter |
| 新建 | `newcode/tools/agent_tool.py` | AgentTool（description 渲染角色列表 + execute 分派） |
| 新建 | `newcode/tools/task_tools.py` | TaskListTool / TaskGetTool / TaskStopTool / SendMessageTool |
| 修改 | `newcode/tools/registry.py` | Registry.view(visible) |
| 修改 | `newcode/agent/agent.py` | max_turns/dont_ask 参数、_react 抽取、run_to_completion、dont_ask 短路 |
| 修改 | `newcode/permission/checker.py` | for_subagent()（共享规则层） |
| 修改 | `newcode/hooks/executor.py` | _run_agent 真实实现（launcher 回调） |
| 修改 | `newcode/hooks/engine.py` | set_agent_launcher |
| 新建 | `newcode/slash/commands/tasks.py` | /tasks 命令族 |
| 修改 | `newcode/slash/context.py` | task_manager 字段 |
| 修改 | `newcode/slash/commands/__init__.py` | register_all 注册 /tasks |
| 修改 | `newcode/tui/app.py` | done 队列消费、clear_all、foreground_sub_agent 预留字段 |
| 修改 | `newcode/skills/executor.py` | _execute_fork 走 launcher（F10 底座统一） |
| 修改 | `newcode/main.py` | 装配（config/catalog/manager/launcher/tools/hooks/tui） |
| 新建 | `tests/test_ch13_{types,config,filter,parser,catalog,fork,launcher,manager,tools,agent,permission,hooks,skills,tui,integration}.py` | 15 个测试文件 |

## 执行顺序

```
T1（版本号）
  ↘
T2 types → T5 parser → T6 catalog → T7 builtin
T3 config（可并行 T2）
T4 filter + Registry.view（可并行）
T8 checker.for_subagent → T9 agent 参数/_react → T10 run_to_completion
T11 fork（可并行 T9）
        ↘
        T12 manager → T13 launcher → T14 agent_tool → T15 task_tools
              ↘                    ↘
                T16 hooks（依赖 T13）  T17 slash /tasks（依赖 T12）
                                     T18 tui done 队列（依赖 T12）
                                     T19 skills 底座统一（依赖 T13）
        T20 main.py 装配（依赖 T12-T19）→ T21 端到端冒烟
        T22-T25 测试批（可与 T21 交错）→ T26 ruff format + 全量测试 + 文档保护确认
```

## T1: 版本号更新到 0.13.0

**文件：** `newcode/__init__.py`、`pyproject.toml`
**依赖：** 无
**完成标准：**
- [ ] `newcode/__init__.py` 的 `__version__` = `"0.13.0"`
- [ ] `pyproject.toml` 的 `version` = `"0.13.0"`，两处一致
- [ ] 独立提交 `chore: bump version to 0.13.0`

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "import newcode; print(newcode.__version__)"` 输出 0.13.0。

## T2: subagent/types.py —— 数据结构与错误

**文件：** `newcode/subagent/errors.py`（新建，MaxTurnsReached）、`newcode/subagent/types.py`（新建）、`newcode/subagent/__init__.py`（新建）
**依赖：** T1
**完成标准：**
- [ ] `Source(IntEnum)`：BUILTIN=0 / USER=1 / PROJECT=2 / PLUGIN=3（PLUGIN 注释「本期占位恒空」）
- [ ] `AgentDefinition` dataclass 字段：`name`、`description`、`body`、`tools: list[str]`、`disallowed_tools: list[str]`、`model: str`（inherit/haiku/sonnet/opus）、`max_turns: int`（缺省 10）、`permission_mode: PermissionMode`（缺省 DEFAULT）、`dont_ask: bool=False`、`background: bool=False`、`enabled: bool=True`、`source: Source`、`source_path: str`；方法 `is_fork()`（`name == "__fork__"`）
- [ ] `DefinitionParseError(Exception)`（携带 `path` / `reason`，供 stderr 定位）
- [ ] `MaxTurnsReached(Exception)`（放 errors.py）带 `text` / `usage` / `tool_count`
- [ ] 文案常量：`NOTIFICATION_XML` 模板（`<task-notification>` 五字段：task-id/status/summary/result）、`RESULT_TRUNCATE_CHARS = 800`
- [ ] `__init__.py` 导出 `AgentDefinition / Source / DefinitionParseError / MaxTurnsReached`

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/subagent && python -c "from newcode.subagent.types import AgentDefinition, Source; d=AgentDefinition(name='x',description='d',body='b',source=Source.BUILTIN); assert d.is_fork()==False; assert Source.PROJECT>Source.USER"`。

## T3: subagent/config.py —— agents: 配置段

**文件：** `newcode/subagent/config.py`（新建）
**依赖：** T2
**完成标准：**
- [ ] `AgentConfig` dataclass：`enable_verifier=False` / `enable_subagent_background=True` / `async_timeout_s=120.0` / `idle_cleanup_minutes=15.0` / `max_idle_agents=10` / `max_tasks_per_agent=10` / `max_queue_per_agent=2` / `model_tiers: dict[str,str]`
- [ ] `effective_enable_subagent_background() -> bool` 方法（返回字段值；显式方法供调用点语义清晰）
- [ ] `load_agent_config(project_root) -> AgentConfig`：读 `.newcode/config.local.yaml` → `.newcode/config.yaml` → `~/.newcode/config.yaml` 的 `agents:` 键（局部优先，缺失全缺省）；`agents:` 键缺失 / 文件不存在 → 全缺省不报错（spec F11.1）
- [ ] 数值字段非法（非 int/float）→ stderr 警告 + 用缺省；`model_tiers` 非 dict → 警告置空

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/subagent/config.py && python -c "import tempfile,os; from newcode.subagent.config import load_agent_config; ..."`（临时目录断言 enable_verifier=True、model_tiers 命中、缺省兜底、effective_ 返回 True）。

## T4: tools/filter.py + Registry.view

**文件：** `newcode/tools/filter.py`（新建）、`newcode/tools/registry.py`（修改）
**依赖：** T1
**完成标准：**
- [ ] `GLOBAL_DENY: frozenset[str] = frozenset({"agent"})`（spec F6.1，注释「任何子 Agent 永不可用」）
- [ ] `ASYNC_AGENT_ALLOWED_TOOLS: frozenset[str]` = {read_file, write_file, edit_file, list_files, search_code, execute_command, read_memory, write_memory}，注释写明「不含 agent；mcp__* 前缀动态识别；load_skill 经 is_system_tool 豁免」（spec F6.3）
- [ ] `FilterParams` dataclass：`all: list[str]` / `background: bool` / `role_tools: list[str]` / `role_disallowed: list[str]`
- [ ] `apply_agent_tool_filter(p: FilterParams) -> list[str]`（spec F6.4 顺序）：起点 = `p.all` → 减 `GLOBAL_DENY` → 减 `role_disallowed` →（`role_tools` 非空则取交集）→（`background` 则与 `ASYNC_AGENT_ALLOWED_TOOLS` + `mcp__*` 前缀取交集）→ 追加系统工具名（`load_skill`）→ 保持原注册顺序去重返回
- [ ] `Registry.view(visible: set[str]) -> Registry`：新 Registry 只含 `visible` 内工具 + 系统工具（`is_system_tool` 豁免），共享 Tool 实例；`visible=None` → 全量（向后兼容）
- [ ] 导出 `apply_agent_tool_filter / GLOBAL_DENY / ASYNC_AGENT_ALLOWED_TOOLS`

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/tools && python -c "from newcode.tools.filter import *; r=apply_agent_tool_filter(FilterParams(all=['agent','read_file','execute_command','write_file','mcp__web','load_skill'],background=True,role_tools=[],role_disallowed=['execute_command'])); assert 'agent' not in r and 'execute_command' not in r and 'mcp__web' in r and 'load_skill' in r"`。存量 `python -m pytest tests/test_ch11_executor.py -q` 确认 `Registry.filtered` 未受影响。

## T5: subagent/parser.py —— 定义解析

**文件：** `newcode/subagent/parser.py`（新建）
**依赖：** T2
**完成标准：**
- [ ] `parse_definition(path, source) -> AgentDefinition`：frontmatter+正文分离**复用** `skills/parser.py` 的 `parse_frontmatter_and_body`（不重复实现切分）；正文存 `body`
- [ ] `name` 缺省取文件基名；归一化 `^[a-z][a-z0-9-]*$`，非法抛 `DefinitionParseError`（path+reason）
- [ ] `description` 必填，缺失抛 `DefinitionParseError`
- [ ] `model`：非 inherit/haiku/sonnet/opus → warning 降级 inherit；`permissionMode`：`dontAsk` → `permission_mode=DEFAULT + dont_ask=True`；非法值 → warning 降级 default；`tools/disallowedTools` 非 list → warning 置空（spec F2.4）
- [ ] `maxTurns`/`background`/`enabled` 类型校验：非法 → warning 缺省（10 / False / True）

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/subagent/parser.py && python -c "from newcode.subagent.parser import parse_definition; import tempfile,os; ..."`（临时 .md：合法角色、permissionMode: dontAsk→dont_ask=True、未知 model→inherit、缺 description→抛错）。

## T6: subagent/catalog.py —— 四层加载与优先级

**文件：** `newcode/subagent/catalog.py`（新建）
**依赖：** T2、T3、T5
**完成标准：**
- [ ] `Catalog`：`_defs: dict[str, AgentDefinition]`（name → 最高优先级）+ `_lock`（线程安全）；`resolve(name)` / `list()`（按 name 排序）/ `list_by_source(src)`
- [ ] `fork_definition() -> AgentDefinition`：`name="__fork__"`、`body=""`、`background=True`、`permission_mode=DEFAULT`、`dont_ask=False`、`tools/disallowed_tools` 空、`max_turns=DEFAULT_MAX_TURNS`（spec F3.2/F3.3）
- [ ] `load_catalog(project_root, agents_cfg) -> Catalog`：顺序 ①项目 `<root>/.newcode/agents/*.md` ②用户 `~/.newcode/agents/*.md` ③内置 `importlib.resources.files("newcode.subagent.builtin")` ④插件（跳过）；**高优先级先写**——后扫到的同名只写入 key 尚未存在的（前者覆盖后者，spec F2.3）
- [ ] 内置级解析失败 → raise（N4 fail-fast）；用户/项目级单文件失败 → `print(f"subagent {name}: ... skipped", file=sys.stderr)` 跳过，其余正常加载
- [ ] `agents_cfg.enable_verifier=False` 时跳过内置 verifier.md（spec F2.5）；`.newcode/agents/` / `~/.newcode/agents/` 目录不存在 → 跳过不报错
- [ ] `__init__.py` 导出 `Catalog / load_catalog`

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/subagent/catalog.py && python -c "...临时目录：项目级 explore.md 覆盖内置，resolve 返回项目级；用户级独有 custom.md 可 resolve；内置解析坏文件 raise"`。

## T7: subagent/builtin/*.md —— 4 个内置角色

**文件：** `newcode/subagent/builtin/{general-purpose,explore,plan,verifier}.md`（新建）
**依赖：** T6
**完成标准：**
- [ ] `general-purpose.md`：`description` 通用全能；无 `tools`/`disallowedTools`；`model: inherit`；`maxTurns: 20`；`permissionMode: default`；正文按 Claude Code 内置 subagent 风格写（身份/职责/工具使用纪律）
- [ ] `explore.md`：`disallowedTools: [write_file, edit_file]`；`model: haiku`；`maxTurns: 15`；`permissionMode: default`；正文=只读代码探索
- [ ] `plan.md`：`disallowedTools: [write_file, edit_file]`；`model: inherit`；`maxTurns: 10`；`permissionMode: plan`；正文=计划制定
- [ ] `verifier.md`：`enabled: false`；正文=验证角色
- [ ] 四个文件都能被 `load_catalog` 正常解析

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "from newcode.subagent.catalog import load_catalog; from newcode.subagent.config import AgentConfig; c=load_catalog('.', AgentConfig()); assert c.resolve('explore') and c.resolve('plan') and c.resolve('general-purpose') and not c.resolve('verifier'); c2=load_catalog('.', AgentConfig(enable_verifier=True)); assert c2.resolve('verifier')"`。

## T8: permission/checker.py —— for_subagent()

**文件：** `newcode/permission/checker.py`（修改）
**依赖：** T1
**完成标准：**
- [ ] 新增 `for_subagent(mode: PermissionMode) -> PermissionChecker`：构造新实例**复用父 `_layers`**（共享规则层，A2），mode 换为参数（spec F4.2/F5.3）
- [ ] 不加 DONT_ASK 枚举成员（plan 决策：dontAsk 语义由 Agent 层短路实现）
- [ ] `PermissionMode` 枚举不动；TUI Shift+Tab 与 CLI `--mode` 不涉及新值（无改动）

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/permission && python -m pytest tests/test_permission_checker.py tests/test_permission_engine.py tests/test_permission_rules.py -q` 全绿；再 `python -c "from newcode.permission.checker import PermissionChecker; pc=PermissionChecker.create('.'); sub=pc.for_subagent(__import__('newcode.permission.modes',fromlist=['PermissionMode']).PermissionMode.ACCEPT_EDITS); assert sub.mode is not pc.mode"`。

## T9: agent/agent.py —— max_turns / dont_ask 参数 + _react 抽取

**文件：** `newcode/agent/agent.py`（修改）
**依赖：** T8
**完成标准：**
- [ ] `__init__` 增加 `max_turns: int = 10`、`dont_ask: bool = False`；`_MAX_AGENT_TURNS` 改为实例字段 `self._max_turns`（run 循环内引用改 `self._max_turns`；主 Agent 构造不传 → 10，行为不变）
- [ ] 抽 `_react(mode: str, plan_content: str, *, inject: bool)` 私有 async generator：ReAct 循环主体（**不含** `add_user`）；`run()` = `add_user`（execute 模式注入 EXECUTE_DIRECTIVE）+ `_react(..., inject=True)`；本任务先保证 run 行为不变（dont_ask 短路在 T10 一并做）
- [ ] 存量测试全绿（回归防线）：`python -m pytest tests/test_agent*.py tests/test_ch08*.py tests/test_ch12_agent.py -q`

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/agent/agent.py && python -m pytest tests/test_ch12_agent.py -q` 全绿（证明 _react 抽取未破坏 run 行为）。

## T10: agent/agent.py —— run_to_completion + dont_ask 短路

**文件：** `newcode/agent/agent.py`（修改）
**依赖：** T9
**完成标准：**
- [ ] 新增 `async run_to_completion(task: str, *, already_injected=False, observer=None) -> str`：
  - `already_injected=False` → `self.conv.add_user(task)`；True（Fork）→ 跳过
  - 驱动 `_react("normal", "", inject=False)`：TEXT 累积、TOKEN_USAGE/TOOL_CALL 经 `observer(event)` 回调、DONE
  - `NATURAL` → 返回累积文本；`MAX_TURNS` → 抛 `MaxTurnsReached(text, usage, tool_count)`；`CANCELLED` → 抛 `asyncio.CancelledError`；`STREAM_ERROR`/`CONSECUTIVE_UNKNOWN_TOOLS` → 抛对应错误
  - 不触发 memory update / compact reminder（子 Agent 专属轻量，spec 不做的事）
- [ ] **dont_ask 短路**：权限分支处（HITL 前）`if self.dont_ask and decision == ASK: decision = ALLOW`——规则/黑名单/沙箱的 DENY 不受影响；非交互 + 非 dont_ask → ASK 仍转 DENY（B1：无 HITL 升级、无 approval_upgrader）

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/agent newcode/subagent && python -c "...（mock provider 驱动 run_to_completion：NATURAL 返回文本、max_turns=1 抛 MaxTurnsReached、observer 计数、dont_ask ASK→ALLOW）"`。

## T11: subagent/fork.py —— Fork 消息装填

**文件：** `newcode/subagent/fork.py`（新建）
**依赖：** T9
**完成标准：**
- [ ] `FORK_BOILERPLATE` 常量：`<fork_boilerplate>` 包裹（不得再 Fork / 不对话不提问不请求确认 / 直接用工具 / 严格限制任务范围 / 最终报告 `Scope:` 开头 ≤500 字）（spec F3.4）
- [ ] `build_forked_messages(parent_conv: ConversationManager, task: str) -> list[Message]`：①深拷贝全部消息（`tool_calls` dict 深拷贝）②末尾 assistant 的悬空 tool_use（无对应 tool 结果）追加 `role="tool"` placeholder（content="（继承上下文，未完成）"）③追加 `user(FORK_BOILERPLATE + "\n" + task)`
- [ ] `is_fork_context(msgs: list[Message]) -> bool`：扫描全部 user 消息内容含 `<fork_boilerplate>`（B2 层 1 兜底）
- [ ] `__init__.py` 导出

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/subagent/fork.py && python -c "...（构造带悬空 tool_use 的 parent_conv，断言补全 placeholder、首条消息以 <fork_boilerplate> 起头、is_fork_context True）"`。

## T12: subagent/manager.py —— BackgroundTask + TaskManager

**文件：** `newcode/subagent/manager.py`（新建）
**依赖：** T10、T11
**完成标准：**
- [ ] `BackgroundTask` dataclass（plan 定义全字段：id/name/sub_agent/task_text/status/result/err/start_time/end_time/usage/total_usage/tool_count/last_activity/round/queue/idle_since/cancel_event/run_task）
- [ ] `TaskManager`：
  - `launch(agent, task_text, *, name=None) -> str`：`agent-<hex>` id；`asyncio.create_task(self._drive(bt))`；`_drive` 包 `try/except BaseException` → completed / failed / cancelled；结束置 end_time/idle_since、`_done.put_nowait(task_id)`（`_done = asyncio.Queue(maxsize=32)`，满 → 丢弃 + stderr 警告）
  - `launch_foreground(...) -> ForegroundHandle`：注册运行中任务（供工具 await + 超时移交）
  - `adopt_running(task_id) -> bool`：**前台→后台移交**——任务所有权转后台继续跑（**不杀、不重启**；状态/计数已在 Manager，无需 PartialState 接力）
  - `get / get_by_name（最新同名）/ list（start_time 升序）/ stop（set cancel_event + run_task.cancel()）`
  - `continue_agent(task_id_or_name, message) -> str`（**同 id 复用**）：校验存在且非 cancelled、`round < max_tasks_per_agent`；空闲 → 立即新轮（status completed→running、round+1、result 清空、conv 追加 user(message)、`_drive` 新任务）；运行中 → `len(queue) < max_queue_per_agent` 入队否则报错
  - `drain_done() -> list[str]`、`clear_all()`、`async run()`（每 ~30s 空闲清理：idle_cleanup_minutes 超时 / max_idle_agents 超限关最旧）
- [ ] `_drive` 调 `agent.run_to_completion(task_text, already_injected=<fork>, observer=计数)`（observer 更新 tool_count/last_activity/usage）

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/subagent/manager.py && python -c "...（mock agent 的 run_to_completion：launch→completed、stop→cancelled、continue_agent 同 id + round 递增 + 上限拒绝、adopt_running 不重跑）"`。

## T13: subagent/launcher.py —— SubAgentLauncher

**文件：** `newcode/subagent/launcher.py`（新建）
**依赖：** T4、T6、T8、T12
**完成标准：**
- [ ] `SubAgentLauncher(provider, make_provider, parent_permission, hooks, catalog, manager, cfg, get_main_agent)`
- [ ] `resolve_model(tier) -> Provider`：inherit/空 → 父；haiku/sonnet/opus → `cfg.model_tiers.get(tier)`，缺配置 → warning 降级父，有配置 → `make_provider(model_id)`
- [ ] `build_sub_registry(role, is_background) -> Registry`：`apply_agent_tool_filter` → 可见名 → `Registry.view(visible)`（spec F6.4）
- [ ] `make_sub_agent(role, *, fork_history=None) -> Agent`：`ConversationManager(role.max_turns or 10, messages=fork_history or [])`、`stable_prompt=role.body if not role.is_fork() else 父 stable_prompt`、`permission=parent_permission.for_subagent(role.permission_mode)`、`dont_ask=role.dont_ask`、`is_interactive=False`、`hooks=engine`（runtime=None）、`max_turns=role.max_turns or 10`
- [ ] `launch_defined(role_name, prompt, *, name=None, background=False) -> LaunchResult`；`launch_fork(prompt, *, name=None) -> LaunchResult`（`catalog.fork_definition()` + `build_forked_messages(父 conv, prompt)` + 强制 background）；`launch_hook_agent(agent_name, prompt) -> str | None`（后台，失败 None）
- [ ] 前台分派：`launch_foreground` → **用 `asyncio.wait` 竞速（非 wait_for）**——完成 → `{status:"completed", text}`；超时 `async_timeout_s` → `adopt_running` → `{task_id, "timed_out_to_background"}`（**不 cancel、不杀重来**，spec F7.3）；`enable_subagent_background=False` 时 fork 返回结构化错误「后台禁用，无法 Fork」
- [ ] `LaunchResult`：`{task_id, status, text}`；`__init__.py` 导出

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/subagent/launcher.py && python -c "...（mock catalog/manager/registry，断言 build_sub_registry 过滤、resolve_model 分层、launch_fork 强制后台；前台超时移交后 mock run 计数未归零=未杀重来）"`。

## T14: tools/agent_tool.py —— Agent 工具

**文件：** `newcode/tools/agent_tool.py`（新建）
**依赖：** T13
**完成标准：**
- [ ] `AgentTool(catalog, task_mgr, launcher, cfg, get_main_agent)`；`name="agent"`、`read_only=False`
- [ ] `parameters` JSON Schema：`prompt`(required string)、`description`(string)、`subagent_type`(string)、`model`(string enum)、`run_in_background`(boolean)、`name`(string)（spec F1.2）
- [ ] `description` 属性：启动期渲染 `catalog.list()` 角色名（`可用 subagent_type: general-purpose, explore, plan, verifier, ...`）
- [ ] `execute(arguments) -> ToolResult`：
  1. 解析 + 校验 `prompt` 非空（空 → error「prompt 必填」）
  2. 防嵌套兜底（B2 层 1）：`is_fork_context(主 conv)` 命中 → `error="Fork 子 Agent 不能再启动 Agent"`（正常情况 agent 工具已被过滤剔除，此步双保险）
  3. `subagent_type` 非空 → `catalog.resolve`（不存在 → `error="未知 subagent_type: X"`）；空 → `catalog.fork_definition()`
  4. background 判定：`role.background or args.run_in_background or role.is_fork()`（`cfg.effective_enable_subagent_background()` 为 False 时 fork → error「后台禁用，无法 Fork」）
  5. `launcher.launch_defined / launch_fork`；前台 await → 返回 `{status, text}` 或 `{task_id, status:"timed_out_to_background"}`
- [ ] `tools/__init__.py` 不自动注册（main.py 显式注册）

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/tools/agent_tool.py && python -c "...（mock launcher/catalog：未知角色 error、description 含角色名、execute 转发参数）"`。

## T15: tools/task_tools.py —— Task 工具组

**文件：** `newcode/tools/task_tools.py`（新建）
**依赖：** T12
**完成标准：**
- [ ] `TaskListTool`（read_only=True）：无参 → 摘要列表（id/name/status/tool_count/last_activity/round）
- [ ] `TaskGetTool`（read_only=True）：`{task_id}` → 完整状态（含 result/err/usage/起止/round）
- [ ] `TaskStopTool`（read_only=False）：`{task_id}` → `manager.stop`，返回 `{"status":"cancellation_requested"}`；已结束返回当前状态
- [ ] `SendMessageTool`（read_only=False）：`{task_id|name, message}` → `manager.continue_agent`，返回 `{"status":"accepted","task_id"}`（同 id）或结构化错误
- [ ] 四个工具的 `name`：`task_list` / `task_get` / `task_stop` / `send_message`（内部 snake_case）；**不设 `is_system=True`**（不能让子 Agent 豁免过滤看到管理工具，spec F6.3）

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/tools/task_tools.py && python -c "...（mock manager 断言参数转发与错误路径）"`。

## T16: hooks —— agent 动作接通

**文件：** `newcode/hooks/executor.py`（修改）、`newcode/hooks/engine.py`（修改）
**依赖：** T13
**完成标准：**
- [ ] `Engine.set_agent_launcher(callable)`：存到 `self._launch_agent`（None 缺省）；`_run_agent` 需要时从引擎取
- [ ] `Executor._run_agent(aa, hook)`：占位日志改为——`prompt = render_template(aa.prompt, payload)`；launcher 非空 → `await launch_hook_agent(aa.agent_name, prompt)`，未知角色/失败 → `ExecutionResult(err=...)`，成功 → `ExecutionResult()`（不 blocked 不 err，F9.4）；launcher 为空 → 保留原占位日志（向后兼容）
- [ ] 存量 hook 测试全绿（占位语义无破坏）

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/hooks && python -m pytest tests/test_ch12_executor.py tests/test_ch12_engine.py -q` 全绿；再 mock launcher 断言 agent 动作触发与失败隔离。

## T17: slash —— /tasks 命令族

**文件：** `newcode/slash/commands/tasks.py`（新建）、`newcode/slash/context.py`（修改）、`newcode/slash/commands/__init__.py`（修改）
**依赖：** T12
**完成标准：**
- [ ] `CommandContext` 增加 `task_manager` 字段（None 缺省，向后兼容）
- [ ] `tasks.py` `build() -> list[CommandDef]`：`name="tasks"`、`kind=LOCAL`、`usage="[show|kill|send] ..."`
  - `/tasks`：列表（`<task_id>  <status>  <角色|fork|name>  <耗时>  in:<n> out:<n>`）；无任务 → `No background tasks.`
  - `/tasks show <id>`：详情（状态/角色/起止/耗时/token/完整结果/终止原因/round）
  - `/tasks kill <id>`：`manager.stop`；已结束提示当前状态
  - `/tasks send <id|name> <message>`：`manager.continue_agent`（同 id）；错误转文案
- [ ] `register_all` 注册 `/tasks`

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/slash && python -c "...（mock manager + mock ui，调 handler 断言列表/详情/终止/续派输出）"`。

## T18: tui —— done 队列消费 + clear_all

**文件：** `newcode/tui/app.py`（修改）
**依赖：** T12
**完成标准：**
- [ ] `REPL.__init__` 增加 `task_manager`（None 缺省，向后兼容）
- [ ] 空闲点消费：`_consume_agent_events` 结束后 + `run()` 回到提示符前，`drain_done()` → 每个 task_id 组 `<task-notification>`（五字段 XML，`<result>` 截断 800 字）→ `agent.conv.add_user(xml)` + `console.print`（spec F7.6；流式中不消费）
- [ ] `/clear` / `/resume` / `/session_new` 路径调用 `manager.clear_all()`（spec F7.9）
- [ ] `foreground_sub_agent` 跟踪字段预留（本期不实现 ESC 手动切换，仅留字段注释）
- [ ] 接线：`REPL` 构造传入 `task_manager`

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/tui && python -m pytest tests/test_ch12_tui.py -q` 全绿（无回归）；mock manager 的 drain_done 断言注入历史与打印（用 `object.__new__` 绕过 PromptSession，按 ch12 手法）。

## T19: skills/executor.py —— fork 底座统一

**文件：** `newcode/skills/executor.py`（修改）
**依赖：** T13
**完成标准：**
- [ ] `_execute_fork` 改为走 `launcher.launch_fork`：构造临时 `AgentDefinition(name=f"skill-fork-{skill.name}", disallowed_tools=<skill 的 allowed_tools 反推或等同>, body="", ...)`；删除 `_make_fork_agent` / `_run_fork_agent` 中与 SubAgent 底座重叠的部分
- [ ] `fork_context`（none/recent/full）摘要逻辑**保留**（skill 独有语义，不走 build_forked_messages 逐字节继承）；`ui.append_assistant_message` 与 token 写回由 Executor 保留（对外行为不变）
- [ ] 与 `run_to_completion` 的 `already_injected` 语义对齐：skill fork 的 conv 已含任务 → 传 `already_injected=True`

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/skills && python -m pytest tests/test_ch11_executor.py tests/test_ch11_parser.py -q` 全绿（skill fork 行为不变）。

## T20: main.py 装配

**文件：** `newcode/main.py`（修改）
**依赖：** T12-T19
**完成标准：**
- [ ] 装配顺序（plan「main.py 装配」）：
  1. `agents_cfg = load_agent_config(project_root)`
  2. `catalog = load_catalog(project_root, agents_cfg)`
  3. `task_manager = TaskManager(cfg=agents_cfg)`
  4. `launcher = SubAgentLauncher(provider, make_provider=..., parent_permission=permission, hooks=hook_engine, catalog=catalog, manager=task_manager, cfg=agents_cfg, get_main_agent=lambda: agent_ref[0])`
  5. `registry.register(AgentTool(...))` + `TaskListTool/TaskGetTool/TaskStopTool/SendMessageTool(task_manager)`
  6. `hook_engine.set_agent_launcher(launcher.launch_hook_agent)`
  7. `agent_ref[0] = Agent(...)`
  8. `REPL(..., task_manager=task_manager)` + `register_all(/tasks)` + `CommandContext.task_manager`
  9. `task_manager.run()` 常驻任务 + 退出 finally `task_manager.clear_all()`
- [ ] `make_provider` 复用现有 Executor 的 lambda（`new_provider(replace(active_provider_cfg, model=model))`）
- [ ] 启动不崩（无角色/无后台时零开销）

**验证：** `export PYTHONIOENCODING=utf-8 && ruff check newcode/main.py && python -c "from newcode.main import main"`（import 无错）；`newcode --version` 输出 0.13.0。

## T21: 端到端冒烟（手动 + 自动）

**文件：** 无（运行验证）
**依赖：** T20
**完成标准：**
- [ ] 定义式前台：`python -m newcode`（TUI）让主 Agent 调 `agent(subagent_type="explore")` 查一个文件 → 同步返回结果文本
- [ ] `/tasks` 空 → `No background tasks.`；后台任务出现后 `/tasks` 列出、`/tasks show` 详情
- [ ] `agent` 工具后台路径返回 `{task_id, status:"async_launched"}`，完成注入 `<task-notification>`
- [ ] Fork 路径（不带 subagent_type）后台执行 + 通知注入
- [ ] 子 Agent 工具定义不含 `agent`（观察 TOOL_CALL 或日志）
- [ ] hook agent 动作（session_start）触发 explore 后台运行
- [ ] 无真实 API key / 终端时：mock provider 跑 test_ch13_integration.py 覆盖上述链路（见 T25）

**验证：** 记录实际输出；受限项标「待人工验证」（原因 + 替代验证）。

## T22: 测试批 A —— subagent 基础

**文件：** `tests/test_ch13_types.py`、`tests/test_ch13_config.py`、`tests/test_ch13_filter.py`、`tests/test_ch13_catalog.py`、`tests/test_ch13_parser.py`（新建）
**依赖：** T7
**完成标准：** 每文件 docstring 标注防的 bug：
- [ ] types：字段缺省、`is_fork()`、Source 排序、MaxTurnsReached 属性
- [ ] config：三层合并优先级、缺省兜底、非法数值降级、effective_ 返回（防「agents: 缺省被误读」）
- [ ] filter：GLOBAL_DENY 剔除 agent、黑名单/白名单组合、后台交集、mcp__* 保留、系统工具豁免、顺序稳定（防「多层防线漏层」）
- [ ] catalog：4 层优先级覆盖、verifier 开关、内置 fail-fast vs 用户 skip、fork_definition（防「同名覆盖方向错」）
- [ ] parser：dontAsk→dont_ask、非法 model/mode 降级、缺 description 抛错（防「dontAsk 被当未知模式拒绝」）

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch13_types.py tests/test_ch13_config.py tests/test_ch13_filter.py tests/test_ch13_catalog.py tests/test_ch13_parser.py -q` 全绿。

## T23: 测试批 B —— fork / launcher / manager

**文件：** `tests/test_ch13_fork.py`、`tests/test_ch13_launcher.py`、`tests/test_ch13_manager.py`（新建）
**依赖：** T13
**完成标准：** 每文件 docstring 标注防的 bug：
- [ ] fork：深拷贝隔离（防「改子 conv 污染父」）、悬空 tool_use 补全（防「API 报配对错误」）、Boilerplate 前缀、is_fork_context（防「Fork 嵌套漏拦」）
- [ ] launcher：build_sub_registry 过滤、resolve_model 分层/降级、make_sub_agent 字段、launch_fork 强制后台、**前台超时移交不杀重来（asyncio.wait 非 wait_for，mock 计数未归零）**、后台总闸报错
- [ ] manager：状态机、stop→cancelled、**续派同 id 复用 + round 递增 + 上限拒绝**、排队 ≤2、空闲清理（idle 超时/max_idle_agents）、clear_all、done 队列（防「续派变新任务」）

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch13_fork.py tests/test_ch13_launcher.py tests/test_ch13_manager.py -q` 全绿。

## T24: 测试批 C —— tools / agent / permission / hooks

**文件：** `tests/test_ch13_tools.py`、`tests/test_ch13_agent.py`、`tests/test_ch13_permission.py`、`tests/test_ch13_hooks.py`（新建）
**依赖：** T16
**完成标准：** 每文件 docstring 标注防的 bug：
- [ ] tools：AgentTool 参数 schema 稳定（防「工具列表随角色变」）、未知 subagent_type error、description 渲染角色、Task* / SendMessage 错误路径（防「SendMessage 找不到目标静默」）、Task 工具非 is_system
- [ ] agent：run_to_completion 共用循环（防「两套循环漂移」）、observer 计数、MaxTurnsReached（防「max_turns 静默丢文本」）、dont_ask ASK→ALLOW 短路且规则仍拦
- [ ] permission：for_subagent 共享规则层（防「父已批准的子 Agent 重复问/被拒」）、无 HITL 升级（防「子 Agent 弹审批」）
- [ ] hooks：agent 动作接通、未知 agent_name 失败隔离、未注入 launcher 时占位日志（防「hook agent 动作崩主流程」）

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch13_tools.py tests/test_ch13_agent.py tests/test_ch13_permission.py tests/test_ch13_hooks.py -q` 全绿。

## T25: 测试批 D —— skills / tui / integration

**文件：** `tests/test_ch13_skills.py`、`tests/test_ch13_tui.py`、`tests/test_ch13_integration.py`（新建）
**依赖：** T21
**完成标准：** 每文件 docstring 标注防的 bug：
- [ ] skills：skill fork 走底座行为不变（防「改造破坏 ch11 fork 语义」）
- [ ] tui：done 队列注入历史 + 打印（mock，无真实终端）（防「通知在流式中误注入」）
- [ ] integration：端到端——定义式前台返回、Fork 后台 + 通知、**超时移交不杀重来**、续派同 id、嵌套防护（子 Agent 工具集无 agent + is_fork_context 拦截）、clear_all 清理（防「多条路径交叉坏掉」——按 CLAUDE.md「重复路径交叉验证」）

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch13_skills.py tests/test_ch13_tui.py tests/test_ch13_integration.py -q` 全绿。

## T26: ruff format + 全量测试 + 文档保护确认

**文件：** 全库
**依赖：** T22-T25
**完成标准：**
- [ ] `export PYTHONIOENCODING=utf-8 && ruff format newcode tests` 后 `ruff check newcode tests` 全绿（注意：只在 newcode/ tests/ 下格式化，**禁止** `ruff format .` 以免扫到 docs/）
- [ ] 全量 `python -m pytest tests/ -q` 全绿（含全部存量 ch01-ch12 测试）
- [ ] `git status` 确认 **docs/ 未被任何批量命令改动**（newcode/、tests/ 之外无异常文件）
- [ ] `python -c "import newcode; print(newcode.__version__)"` = 0.13.0
- [ ] 提交：按逻辑分组提交（如「ch13: subagent 底座」「ch13: 后台任务与工具」「ch13: TUI/装配/测试」）

**验证：** 记录全量测试输出与 git status 结果；若 docs/ 有改动立即停下报告。
