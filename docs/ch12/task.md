# MewCode ch12 - Hook 生命周期挂钩系统 Tasks

> 顺序执行。每完成一个任务跑 `export PYTHONIOENCODING=utf-8 && ruff check mewcode/hooks mewcode/permission/matcher.py` 确保无 lint 错；接入主流程的任务（T13）做完后立刻跑一次端到端冒烟（T14）再进下一项。**文档保护**：任何批量命令（ruff、git）跑完先确认 docs/ 未被动过。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `mewcode/__init__.py`、`pyproject.toml` | 版本号 0.11.0 → 0.12.0 |
| 新建 | `mewcode/permission/matcher.py` | Matcher Protocol + Exact/Glob/Regex/Not 四实现 + compile_matcher/matcher_from_spec/evaluate |
| 修改 | `mewcode/permission/rules.py` | Rule.matcher 字段、match_target 用 evaluate、build_rule_set 失败 stderr（F1.4） |
| 新建 | `mewcode/hooks/__init__.py` | 包导出（Engine/Event/load/Hook/DispatchResult） |
| 新建 | `mewcode/hooks/types.py` | Event(str Enum)/Action 嵌套/Hook/Payload/DispatchResult/ExecutionResult/常量 |
| 新建 | `mewcode/hooks/conditions.py` | Condition/AtomCondition/eval_condition/get_by_path |
| 新建 | `mewcode/hooks/executor.py` | Executor 四动作 + render_template + ExecutionResult |
| 新建 | `mewcode/hooks/loader.py` | 三层加载合并校验（HOOK_FILE_*） |
| 新建 | `mewcode/hooks/engine.py` | Engine 统一 dispatch/once/后台任务/close |
| 修改 | `mewcode/session/runtime.py` | pending_reminders（create_new/resume 清空） |
| 修改 | `mewcode/agent/agent.py` | hooks 注入 + 11 节点 _dispatch_hook + reminder join + 拦截整合 |
| 修改 | `mewcode/slash/context.py` | hooks 字段 |
| 新建 | `mewcode/slash/commands/hooks.py` | /hooks 命令（F10） |
| 修改 | `mewcode/slash/commands/__init__.py` | register_all 注册 /hooks |
| 修改 | `mewcode/tui/app.py` | user_prompt_submit 拦截 / command_execute / 会话生命周期事件 |
| 修改 | `mewcode/main.py` | engine 装配/load + startup/shutdown/session_start/session_end |
| 新建 | `tests/test_ch12_{matcher,conditions,executor,loader,engine,runtime,agent,tui,integration}.py` | 9 个测试文件 |

## T1: 版本号更新到 0.12.0

**文件：** `mewcode/__init__.py`、`pyproject.toml`
**依赖：** 无
**完成标准：**
- [ ] `mewcode/__init__.py` 的 `__version__` = `"0.12.0"`
- [ ] `pyproject.toml` 的 `version` = `"0.12.0"`，两处一致
- [ ] 独立提交 `chore: bump version to 0.12.0`

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "import mewcode; print(mewcode.__version__)"` 输出 0.12.0。

## T2: permission/matcher.py —— 共享匹配器（前置基础 F1）

**文件：** `mewcode/permission/matcher.py`
**依赖：** T1
**完成标准：**
- [ ] `Matcher` Protocol：`match(s) -> bool` + `__str__()`
- [ ] `ExactMatcher(value)`：`match == s == value`；`__str__` → `=value`
- [ ] `GlobMatcher(pattern, is_command)`：`is_command=True` 走 `match_command`（fnmatch 整串通配），`False` 走 `match_path`（`**` 递归，从 rules.py 移入 `_match_parts`）；`__str__` → pattern
- [ ] `RegexMatcher(src, compiled)`：加载期 `re.compile`；`match` 用 `.search`；`__str__` → `~src`
- [ ] `NotMatcher(inner)`：一元取反；`__str__` → `!inner`
- [ ] `compile_matcher(pattern, *, is_command=False)`：`=v`→Exact、`~re`→Regex（编译失败抛 ValueError）、`!inner`→Not（递归）、无前缀→Glob；空串抛 ValueError
- [ ] `matcher_from_spec(d, *, is_command=False)`：`{type: exact|glob|regex, value}` / `{type: not, inner}`；未知 type / 缺 value / not 缺 inner → ValueError
- [ ] `evaluate(spec, target)` ≡ `spec.match(target)`；保留 `match_pattern` 名称 re-export 兼容 rules

**验证：** `python -c "from mewcode.permission.matcher import compile_matcher, evaluate; assert evaluate(compile_matcher('=git status'),'git status'); assert not evaluate(compile_matcher('=git status'),'git status -s'); assert evaluate(compile_matcher('~^rm'),'rm -rf .'); assert not evaluate(compile_matcher('!~^rm'),'rm -rf .')"`。

## T3: permission/rules.py 改造

**文件：** `mewcode/permission/rules.py`
**依赖：** T2
**完成标准：**
- [ ] `Rule` 增加 `matcher: Matcher | None`（None = 该工具全匹配）+ 保留 `pattern`/`raw` 原文供错误日志与调试
- [ ] `Rule.parse`：正则提取 `(tool_name, pattern)` 后调 `compile_matcher(pattern, is_command=(tool_name=="Bash"))`；parse 失败返回 None（与现状一致）+ 调用方 stderr
- [ ] `Rule.match_target` 改用 `evaluate(self.matcher, target)`（matcher=None → 恒匹配）
- [ ] `build_rule_set`：解析失败由静默跳过改为 stderr 打印 `rule "<raw>" parse failed: <原因>` 并跳过（F1.4）
- [ ] 向后兼容：`Bash(git *)` 无前缀 → GlobMatcher，行为与现状一致（F1.5）

**验证：** `python -m pytest tests/test_permission_rules.py tests/test_permission_checker.py tests/test_permission_engine.py -q` 全绿（存量不受影响）。

## T4: hooks/types.py —— 数据结构与常量

**文件：** `mewcode/hooks/types.py`（新建包）
**依赖：** T2
**完成标准：**
- [ ] `Event(str, Enum)` 18 个（snake_case，见 spec F3.1）；`BLOCKING_EVENTS = {PRE_TOOL_USE, USER_PROMPT_SUBMIT}`；`is_blocking(e)`
- [ ] `CombineMode`（all_of/any_of）、`ActionType`（command/prompt/http/agent）
- [ ] `ShellAction(command)` / `PromptAction(text)` / `HttpAction(url, method="POST", headers, body=None)` / `AgentAction(agent_name, prompt)`；`Action{type, shell?, prompt?, http?, agent?}`
- [ ] `Hook{name, event, action, condition=None, once=False, asyncio_mode=False, timeout_s=30.0, source=""}`
- [ ] `Payload = dict[str, Any]`（json.dumps(payload, sort_keys=True) 用，N5）
- [ ] `DispatchResult{blocked, reason, blocking_hook_name, injected_prompts}`、`ExecutionResult{blocked, reason, prompt, err}`

**验证：** `python -c "from mewcode.hooks.types import Event, is_blocking; assert Event('pre_tool_use') is Event.PRE_TOOL_USE; assert is_blocking(Event.PRE_TOOL_USE); assert not is_blocking(Event.TURN_START)"`。

## T5: hooks/conditions.py

**文件：** `mewcode/hooks/conditions.py`
**依赖：** T2、T4
**完成标准：**
- [ ] `Condition{mode, atoms}`、`AtomCondition{field, matcher}`
- [ ] `get_by_path(payload, path)`：点分遍历嵌套 dict（如 `tool_input.path`）；路径不存在 → `""`；值非 str 时 **bool 转小写（`"true"`/`"false"`，与 YAML 直觉及 spec 场景 1 的 `is_error: false` 一致）**、int/float → `str()`，嵌套 dict/list → `json.dumps(value, sort_keys=True)`（与 N5 稳定序列化一致）（F4.3）
- [ ] `eval_condition(cond, payload)`：cond=None → True；否则逐一 `evaluate(atom.matcher, get_by_path(...))`，按 mode 做 all/any 组合（F4.6）

**验证：** `python -c "from mewcode.hooks.conditions import get_by_path, eval_condition; from mewcode.hooks.types import Condition, AtomCondition, CombineMode; from mewcode.permission.matcher import matcher_from_spec; p={'tool_input':{'path':'a/b.py'}}; assert get_by_path(p,'tool_input.path')=='a/b.py'; assert get_by_path(p,'tool_input.missing')==''; c=Condition(CombineMode.ALL_OF,[AtomCondition('tool_input.path', matcher_from_spec({'type':'glob','value':'**/*.py'}))]); assert eval_condition(c,p)"`。

## T6: hooks/executor.py

**文件：** `mewcode/hooks/executor.py`
**依赖：** T4
**完成标准：**
- [ ] `render_template(text, payload)`：`{field}` 点分替换（{event}/{tool_name}/{file_path}/{message}/{error}/{tool_input.xxx} 映射 $VAR 语义，F4.7）；容错——format_map 抛 KeyError/IndexError/ValueError（裸 `{}` 等）返回原文、未知字段→`""`、绝不抛给调用方（F4.8）
- [ ] `Executor`：`run(hook, payload, *, blocking) -> ExecutionResult` 按 action.type 分发
- [ ] `_run_shell`（F5.2-F5.5）：`create_subprocess_shell(render_template(command), stdin/stdout/stderr=PIPE)`；stdin 写 `json.dumps(payload, sort_keys=True)`；`asyncio.wait_for(communicate, timeout)` 超时 kill 子进程 → err；`blocking and rc==2` → blocked、reason=(stderr or stdout).decode().rstrip("\n")；rc==0 → 放行；其它非零 → err（不拦截）
- [ ] `_run_prompt`（F5.6-F5.8）：`ExecutionResult(prompt=render_template(text))`，永不 blocked
- [ ] `_run_http`（F5.9-F5.12）：body = render_template(ha.body) if ha.body else payload JSON；httpx request(method/url/content/headers/timeout)；2xx 且 json `{"decision":"block","reason"}` → blocked；网络/超时/JSON 解析错 → err；`_http_client = httpx.AsyncClient(timeout=30.0)`
- [ ] `_run_agent`（F5.13）：stderr `[hook <name>] agent not yet implemented, skipped`（N9），不 blocked 不 err

**验证：** `python -c "from mewcode.hooks.executor import render_template; assert render_template('x {tool_input.path} y', {'tool_input':{'path':'a.py'}})=='x a.py y'; assert render_template('{missing}', {})==''; assert render_template('echo {}', {})=='echo {}'"`。

## T7: hooks/loader.py —— 三层加载与校验

**文件：** `mewcode/hooks/loader.py`
**依赖：** T4、T5
**完成标准：**
- [ ] `HOOK_FILE_LOCAL = ".mewcode/config.local.yaml"`（本地，最高优先级）、`HOOK_FILE_PROJECT = ".mewcode/config.yaml"`、`HOOK_FILE_USER = os.path.expanduser("~/.mewcode/config.yaml")`（F6.1）
- [ ] `load(project_root) -> Engine`：本地 → 项目 → 用户 依次加载、追加合并（优先级高者在前）；文件缺失跳过；整体 YAML 非法/顶层非 dict → stderr 告警 + 该文件空；同名 hook 冲突 → stderr 提示 + 跳过后到者（F6.4）
- [ ] 逐条校验（F6.5/F6.6）：name 必填、event 枚举、action.type 四选一 + 子字段必填（command 有 command、http 有 url、prompt 有 text、agent 有 agent_name+prompt）、if 顶层 all_of/any_of 互斥（同时出现报错）、matcher 编译失败、async+拦截事件冲突、timeout 格式合法
- [ ] 任一失败 → stderr `hook "<name>" (in <file>): <原因>, skipped` 并跳过该条，其余正常加载（N1/N2/N3）
- [ ] `_parse_duration(s) -> float`：`re` 匹配 `\d+(\.\d+)?([smh]?)`，支持 `30s`/`5m`/`1.5`（浮点秒）；非法 → 报错跳过该条；缺省 30.0
- [ ] **hook 条件 matcher 的 glob 复用现有 `match_pattern` 自动判断**（无 `/` → fnmatch 整串通配、有 `/` 或 `**` → 路径分段递归，与权限规则一致）——`rm -rf *` 匹配命令串（spec 场景 2）、`**/*.py` 匹配路径，两者都不失效；`matcher_from_spec` 的 glob 不带 is_command

**验证：** 临时目录构造 hooks.yaml 冒烟 `load()` 返回 Engine、rules 数正确、冲突/非法条目标 stderr 并跳过（T17 单测覆盖）。

## T8: hooks/engine.py + hooks/__init__.py

**文件：** `mewcode/hooks/engine.py`、`mewcode/hooks/__init__.py`
**依赖：** T5、T6、T7
**完成标准：**
- [ ] `Engine(rules, sources)`：`_once_fired: set[str]`、`_lock = asyncio.Lock()`、`_executor = Executor()`
- [ ] `async dispatch(event, payload) -> DispatchResult`（统一接口，is_blocking 内部判定）：
  1. 过滤匹配 event 的 hook（按声明顺序）
  2. once 过滤（`_once_fired` 命中跳过）
  3. 串行求值条件
  4. `asyncio_mode` → `asyncio.create_task` 后台执行（记 `_tasks`）、once 立即标记、continue（不等结果，不参与 block/inject）
  5. 同步执行：err → stderr `[hook <name>] <event> failed: <err>`，continue（F9.1）；prompt → `injected_prompts.append`；blocked 且 is_blocking → 设 blocked/reason/blocking_hook_name，break（F7.3）
  6. 同步执行成功后 once 标记
- [ ] `reset_for_new_session()`：清空 `_once_fired`（F2.2/N8）
- [ ] `sources` / `rules` 属性（拷贝）；`close()`：记录未完成后台任务，不强制等待（F9.5）
- [ ] `hooks/__init__.py` 导出 `Engine / Event / load / Hook / DispatchResult`

**验证：** `python -c "from mewcode.hooks import Engine, Event, load"` 通过；mock executor 冒烟 dispatch 顺序/短路（T18 单测覆盖）。

## T9: session/runtime.py —— pending_reminders

**文件：** `mewcode/session/runtime.py`
**依赖：** T8（Engine 类型标注，TYPE_CHECKING 避免运行期依赖）
**完成标准：**
- [ ] `SessionRuntime.pending_reminders: list[str] = field(default_factory=list)` + `_reminders_lock = threading.Lock()`
- [ ] `append_reminders(prompts: list[str])`：加锁追加
- [ ] `take_reminders() -> list[str]`：加锁取出并清空
- [ ] `hook_engine: Engine | None = None` 字段（TYPE_CHECKING 标注；TUI 装配时设置）
- [ ] `reset_for_new_session()`：加锁清空 `pending_reminders` + 若 `hook_engine` 非 None 调 `await hook_engine.reset_for_new_session()`（**集中一处重置，调用方只调这一个**，模板借鉴）
- [ ] `create_new()` / `resume()` 时同样清空（与 ActiveSkills 同生命周期，N8）

**验证：** `python -m pytest tests/test_session_writer.py tests/test_session_recovery.py tests/test_ch09_integration.py -q` 全绿 + 新增断言（T19 覆盖）。

## T10: agent/agent.py 集成

**文件：** `mewcode/agent/agent.py`
**依赖：** T8、T9
**完成标准：**
- [ ] `__init__` 增加 `hooks: Engine | None = None`、`runtime: SessionRuntime | None = None`（None 时全部短路，N10 与 active_skills 同模式；runtime 用于取 pending_reminders——模板借鉴：Agent 需访问 runtime 的 take_reminders）
- [ ] **事件职责划分（18 个）**：agent 层 11 个（turn_start / turn_end / pre_tool_use / post_tool_use / pre_send / post_receive / error / pre_compact / post_compact / permission_request / file_change）、TUI 层 5 个（user_prompt_submit / command_execute / session_start / session_end / session_resume）、main 层 2 个（startup / shutdown）
- [ ] 私有 `_dispatch_hook(event, payload) -> DispatchResult`：hooks=None → 空结果；否则 `await self._hooks.dispatch(...)` 并把 `injected_prompts` 经 `runtime.append_reminders()` 写入（runtime=None 时丢弃）
- [ ] `run()` 节点插入（编号对齐 plan 交互时序图；② conv.add_user、⑥ assemble 为既有流程不列）：
  - ① `turn_start`（{prompt: user_input}）在 conv.add_user 后、ReAct 循环前
  - ③ `pre_compact` / `post_compact`（{trigger: auto}）包在 manage_context 调用外；force_compact → trigger=emergency；run_force_compact → manual
  - ④ `pre_send`（{prompt, last_user_message}）在 provider.stream 前
  - ⑤ stream 前 `reminders = plan_reminders + runtime.take_reminders()`（join 并清空，F8.3），hook prompt 用 `<hook-notification>` 标签（新增 `hook_notification()` 构造 Message）
  - ⑦ `post_receive`（{message}）在 stream 循环后
  - ⑧ `pre_tool_use`（{tool_name, tool_input}）对每个 known_call，在权限 check 前：blocked → `ToolResult(error=f"[hook {name}] {reason}")` → yield TOOL_CALL + TOOL_RESULT → conv.add_tool_result → continue（跳过权限与执行，F7.4，与 Deny 路径同构）
  - ⑨ ASK 分支 HITL 前 `permission_request`（{tool_name, tool_input}）；**DENY 分支产 TOOL_RESULT(error) 后也 `post_tool_use`（is_error=True）**（spec F3.1：被权限 Deny 的也触发）
  - ⑩ 执行后 `post_tool_use`（{tool_name, tool_input, tool_result, is_error}）；write/edit 成功 → `file_change`（{file_path}）
  - ⑪ 结束：NATURAL/MAX_TURNS → `turn_end`（{iter}）；STREAM_ERROR → `error`（{error}）；CANCELLED 不触发 turn_end（F3.1）

**验证：** `python -m pytest tests/test_agent.py tests/test_agent_context.py -q` 全绿（hooks=None 行为不变）+ 新增接线测试（T18）。

## T11: slash —— /hooks 命令 + context 字段

**文件：** `mewcode/slash/context.py`、`mewcode/slash/commands/hooks.py`（新）、`mewcode/slash/commands/__init__.py`
**依赖：** T8
**完成标准：**
- [ ] `CommandContext` 增加 `hooks: object | None = None`
- [ ] `hooks.py`：`/hooks`（KindLocal，零参数）——按 event 分组输出 `  <name>  <event>  <action.type>  <flags>`（flags 含 `[once]`/`[async]`），末尾 `Loaded from: <来源文件列表>`；无 hook 输出 `No hooks loaded.`（F10.1/F10.2）；hooks=None 时输出 `Hook 系统未启用`
- [ ] `__init__.py` register_all 注册 hooks 模块

**验证：** `python -m pytest tests/test_ch10_commands.py tests/test_ch10_registry.py -q` 全绿 + 新增用例（T19 覆盖）。

## T12: tui/app.py 集成

**文件：** `mewcode/tui/app.py`
**依赖：** T8、T11
**完成标准：**
- [ ] `_process_input`：`dispatch(USER_PROMPT_SUBMIT, {prompt: text})` 在 `_run_stream` 前；blocked → `self._console.print(f"[hook {name}] {reason}", style="red")` 后 return（消息不写历史、不启动 agent、焦点回输入框，F7.5）
- [ ] `dispatch_slash`：命中命令后 `dispatch(COMMAND_EXECUTE, {command: name, args})`（通知型）
- [ ] `request_clear_session`：dispatch `session_end`（旧）→ `create_new()` → `reset_for_new_session()` → dispatch `session_start`（新）
- [ ] `resume_session`：dispatch `session_end`（旧）→ `runtime.resume()` → `reset_for_new_session()` → dispatch `session_resume`
- [ ] `new_session`：同 /clear（session_end → session_start）
- [ ] hooks 访问经 `command_ctx.hooks`（未接线为 None 时全部跳过，N10）

**验证：** `python -m pytest tests/test_ch10_tui.py tests/test_tui_wiring.py tests/test_tui_resume.py tests/test_tui_compact.py -q` 全绿（hooks=None 行为不变）+ 新增拦截用例（T19）。

## T13: main.py 装配

**文件：** `mewcode/main.py`
**依赖：** T8-T12
**完成标准：**
- [ ] `_amain` 装配顺序：permission 创建后 → `hook_engine = load_hooks(project_root)`（调 `hooks.load`，返回 Engine）→ `runtime.hook_engine = hook_engine` → 注入 agent(`hooks=hook_engine, runtime=session_runtime`) 与 `CommandContext(hooks=hook_engine)`
- [ ] `get_session_id` / `get_mode` 注入：Engine 构造经 loader 时（loader 返回 Engine，get_session_id/get_mode 由 main 装配——若 loader 不接收则 Engine 构造后设置）——**实现约定：`hooks.load(project_root)` 后由 main 调 `engine.set_context_providers(get_session_id, get_mode)`**，读 `session_runtime.session_id` 与 `permission.mode.value`
- [ ] `startup` 事件在装配完成后 dispatch（{仅通用字段}）
- [ ] `session_start` 在 create_new 后、首条消息前 dispatch
- [ ] 退出 finally：dispatch `session_end` + `shutdown` → `engine.close()`
- [ ] `.gitignore` 追加 `config.local.yaml`（保证 `.mewcode/config.local.yaml` 不被追踪，F6.1）

**验证：** `python -c "import mewcode.main"` 通过；T14 端到端冒烟覆盖装配后行为。

## T14: 手动端到端冒烟（参考 ch11 T22 五步）

**文件：** 无（仅运行验证）；测试配置 `.mewcode/config.local.yaml`
**依赖：** T13
**完成标准：**
- [ ] 创建 `.mewcode/config.local.yaml`：
  ```yaml
  hooks:
    - name: block-rm
      event: pre_tool_use
      if:
        all_of:
          - field: tool_input.command
            match: { type: glob, value: "rm -rf *" }
      action: { type: command, command: "echo 'dangerous: rm -rf' >&2; exit 2" }
    - name: zh-hint
      event: turn_start
      action: { type: prompt, text: "请使用 zh-CN 回复" }
    - name: fmt-once
      event: post_tool_use
      once: true
      if:
        all_of:
          - field: tool_name
            match: { type: exact, value: write_file }
          - field: is_error
            match: { type: exact, value: "false" }
      action: { type: command, command: "echo fmt {tool_input.path}" }
  ```
- [ ] 手动启动 `python -m mewcode`，依次验证：
  1. `/hooks` 列出三条 hook、按 event 分组、末尾 Loaded from 含 `.mewcode/config.local.yaml`
  2. 让 Agent 执行 `rm -rf /tmp/x` → 工具被拦截、tool_result 显示 `[hook block-rm] dangerous: rm -rf`、不弹审批
  3. 首轮对话 LLM reminder 区出现 zh-Hint 注入（调试通道观察），后续轮不注入
  4. 让 Agent 写文件 → fmt-once 触发一次，再次写文件不触发（once 生效）
  5. `/clear` 后写文件 → fmt-once 再次触发（once 集合清空）

**验证：** 人工操作 TUI 观察 5 步现象（需真实终端，属「待人工验证」，其余 T15-T19 自动跑）。

## T15: 测试批 1 —— permission 匹配器

**文件：** `tests/test_ch12_matcher.py`
**依赖：** T2、T3
**完成标准（覆盖点清单，模板借鉴：`pytest.mark.parametrize` 表驱动 + `id=` 描述）：**
- [ ] compile_matcher 四种前缀：`=v`/`~re`/`!inner`/`无前缀 glob`；`__str__` 输出 `=foo`/`~re`/`!inner`/`pattern`
- [ ] Exact：`=git status` 命中 `git status`、不命中 `git status -s`；空串 / 前缀非空
- [ ] Glob：is_command=True 整串通配 / is_command=False `**` 递归 / 空 pattern 全匹配 / 转义字符
- [ ] Regex：`~^npm (install|test)$` 命中 `npm install`、不命中 `npm run dev`；`~[invalid` 编译失败抛 ValueError；`.search` 部分匹配
- [ ] Not 嵌套：`!=foo` 不命中 foo 命中 bar；`!~^rm` 命中 `ls -lh` 不命中 `rm -rf .`；`!git *` 命中 `npm install` 不命中 `git status`（not+glob）
- [ ] 空串 `""` 抛 ValueError
- [ ] matcher_from_spec：四 type / not 缺 inner 抛 / 未知 type 抛 / 缺 value 抛
- [ ] rules 向后兼容：`Bash(git *)` 行为与改造前一致（对比断言）
- [ ] build_rule_set 失败条目 stderr 输出 + 其余加载（F1.4）
- [ ] 每测试 docstring 标注防的 bug（CLAUDE.md 测试规范）

**验证：** `export PYTHONIOENCODING=utf-8 && python -m pytest tests/test_ch12_matcher.py tests/test_permission_rules.py -q` 全绿。

## T16: 测试批 2 —— conditions + executor

**文件：** `tests/test_ch12_conditions.py`、`tests/test_ch12_executor.py`
**依赖：** T5、T6
**完成标准（覆盖点清单）：**
- [ ] conditions：all_of / any_of / 空 atoms / cond=None / 点分路径多层 / 路径不存在→"" / 非 str 值 str() 化
- [ ] render_template：{field} 替换 / 未知字段→"" / 裸 `{}` 返回原文 / 多层嵌套 {tool_input.path}
- [ ] executor shell：exit 2 拦截（blocking=True）+ reason 取 stderr / exit 0 放行 / 其它非零 err / 超时 kill 子进程 / blocking=False 时 rc==2 不拦截
- [ ] executor http：2xx+decision:block 拦截 / 非 2xx 放行 / body 缺 decision 放行 / 网络错 err（pytest-httpserver 桩或 httpx MockTransport）
- [ ] executor prompt：返回 prompt 文本 / 永不 blocked
- [ ] executor agent：stderr 占位日志格式 `[hook <name>] agent not yet implemented, skipped` / 不 blocked 不 err
- [ ] 每测试 docstring 标注防的 bug

**验证：** `python -m pytest tests/test_ch12_conditions.py tests/test_ch12_executor.py -q` 全绿（mock，无真实终端/API）。

## T17: 测试批 3 —— loader + engine

**文件：** `tests/test_ch12_loader.py`、`tests/test_ch12_engine.py`
**依赖：** T7、T8
**完成标准（覆盖点清单）：**
- [ ] loader：三层合并顺序（本地>项目>用户）/ 文件缺失跳过 / 整体 YAML 非法告警 / 同名冲突跳过后到者 / 逐条校验失败 stderr 定位 + 跳过其余（event 未知、action.type 非法、必填字段缺失、all_of+any_of 同现、regex 编译失败、async+拦截冲突、timeout 格式非法）/ parse_timeout 转秒
- [ ] engine：dispatch 过滤 event / once 过滤 + reset_for_new_session 清空 / 顺序执行 + 拦截短路（前面 blocked 后面不跑）/ prompt 累积 injected_prompts / async hook 起 task 不等 + 失败 stderr / 执行失败 stderr 不中断 / 防重入（同事件 dispatch 中不重入自身）
- [ ] 每测试 docstring 标注防的 bug

**验证：** `python -m pytest tests/test_ch12_loader.py tests/test_ch12_engine.py -q` 全绿。

## T18: 测试批 4 —— 接线（runtime/agent/tui/slash）

**文件：** `tests/test_ch12_runtime.py`、`tests/test_ch12_agent.py`、`tests/test_ch12_tui.py`
**依赖：** T9-T12
**完成标准（覆盖点清单）：**
- [ ] runtime：pending_reminders 初始空 / create_new 清空 / resume 清空
- [ ] agent：hooks=None 时行为与 ch11 一致（既有测试全绿）/ 11 节点 _dispatch_hook 接线（真实 Engine + 合成 rules 记录事件与 payload）/ pre_tool_use 拦截 → TOOL_CALL+TOOL_RESULT(error)+conv.add_tool_result+跳过权限 / pre_send 前 pending_reminders join 进 reminders（hook_notification 标签）/ turn_end 仅 NATURAL/MAX_TURNS（CANCELLED/STREAM_ERROR 不触发）/ file_change 在 write/edit 成功后触发 / **DENY 分支也触发 post_tool_use（is_error=True）**
- [ ] tui：user_prompt_submit 拦截 → console 显示 `[hook <name>] <reason>` + 不启动 agent（mock REPL）/ command_execute 通知 / clear/resume/new 会话事件顺序 + reset_for_new_session
- [ ] slash：/hooks 输出分组 + flags + Loaded from / No hooks loaded.
- [ ] **接线测试用真实 `Engine` + 合成 rules 注入 agent（模板借鉴）**，而非 mock Engine——驱动真实 dispatch/executor 路径，防回归；rules 用假 executor 可替换为最小 shell 命令
- [ ] 每测试 docstring 标注防的 bug

**验证：** `python -m pytest tests/test_ch12_runtime.py tests/test_ch12_agent.py tests/test_ch12_tui.py tests/test_ch10_commands.py tests/test_ch10_tui.py tests/test_tui_wiring.py -q` 全绿。

## T19: 集成测试 + ruff 清洁

**文件：** `tests/test_ch12_integration.py`、存量测试适配、全仓清洁
**依赖：** T13-T18
**完成标准：**
- [ ] `test_ch12_integration.py`（对应 T14 五步的自动化断言）：真实 YAML 文件 → load → 三层合并 → pre_tool_use 拦截闭环（fake 执行器）/ prompt 注入 reminder / once 重置 / 条件 all_of 组合 / 失败隔离（坏 hook 不影响其余）/ 空引擎短路（无 hooks 时 Agent 行为不变）
- [ ] 存量测试适配：main.py 装配变化影响的用例适配；确认既有 ch08-ch11 测试不受影响
- [ ] `export PYTHONIOENCODING=utf-8 && ruff format . && ruff check --fix .` + 确认 **docs/ 未被改动**（CLAUDE.md 文档保护）
- [ ] 提交信息形如 `ch12: Hook 系统——共享匹配器扩展 + hooks 包 + 18 事件集成（T1-T19）`

**验证：** `python -m pytest tests/ -q` 全绿；`ruff format --check . && ruff check .` 清洁；`git status` 确认 docs/ 无改动。

## 执行顺序

```
T1 → T2 ──► T3 ──────────────────────────┐
      └──► T4 ──► T5 ──► T7 ──► T8 ──► T10 ─┐
            │  └─────► T6 ────────┘      │   │
            └────────────────────────────┘   │
                                              ▼
T9（独立，与 T4 并行）────────────► T10       │
                                   │          ▼
T11（依赖 T8）─► T12（依赖 T11）──► T13 ──► T14（冒烟）
                                            │
T2-T3 → T15；T5-T6 → T16；T7-T8 → T17；T9-T12 → T18；T13-T18 → T19
```

**可并行组：** [T3、T4]（都依赖 T2，T4 只依赖 T2 不依赖 T3）；[T5、T6]（都依赖 T4）；[T9]（独立）；[T11、T12 与 T10]（T10 依赖 T8，T11 也依赖 T8，二者并行）。

**明确不做（用户已拍板 + spec「不包含」）：** once 跨进程持久化、显式优先级字段、agent 执行器真实实现、Hook 热更新、Hook 改写工具参数、command 输出注入 LLM、事件条件函数调用/嵌套逻辑。
