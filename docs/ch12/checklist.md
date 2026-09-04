# NewCode ch12 - Hook 生命周期挂钩系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。验证方式写在括号里。
> AC 编号与 spec.md 一一对应；AC23-AC26 为 plan 层补充的集成验证点。
> 标注「待人工验证」的条目需真实终端交互，自动测试环境无法执行。

## 实现完整性

- [ ] **AC1**（spec AC1，F1.2）: 精确规则命中（验证：`.newcode/permissions.yaml` 写 `Bash(=git status)`，启动后调用 `git status` 被命中、`git status -s` 不命中——单测断言 `evaluate(compile_matcher("=git status"), target)`）
- [ ] **AC2**（spec AC2，F1.2）: 正则规则命中 + 非法正则回退（验证：`Bash(~^npm (install|test)$)` 命中 `npm install`、不命中 `npm run dev`；构造未闭合括号的规则，启动期 stderr 打印 `rule "<raw>" parse failed: ...` 并跳过该条）
- [ ] **AC3**（spec AC3，F1.2）: 反向正则（验证：`Bash(!~^rm)` 对 `rm -rf .` 不命中、`ls -lh` 命中；`!git *` 嵌套 not+glob 对 `npm install` 命中、`git status` 不命中）
- [ ] **AC4**（spec AC4，F1.5）: 向后兼容（验证：`python -m pytest tests/test_permission_rules.py tests/test_permission_checker.py tests/test_permission_engine.py -q` 全绿；既有 `Bash(git *)` 风格配置行为与改造前一致）
- [ ] **AC5**（spec AC5，F5.4）: command 拦截 write_file（验证：`pre_tool_use` hook 条件 `tool_name=write_file` + 动作 `command: "echo blocked >&2; exit 2"`，LLM 调 write_file 时被拦截，tool_result 显示 `[hook <name>] blocked`，文件未写入）
- [ ] **AC6**（spec AC6，F5.4）: exit 0 放行（验证：同 hook 动作改 `exit 0`，write_file 放行、文件成功写入）
- [ ] **AC8**（spec AC8，F5.6）: session_start prompt 注入（验证：`session_start` hook 动作 `prompt: "用 zh-CN 回复"`，重启后首轮 LLM reminder 区可见该文本，后续轮不再注入）
- [ ] **AC9**（spec AC9，F2.2）: once 语义（验证：`once + turn_start` hook 动作 `echo first-turn >&2`，第一轮 stderr 出现 `first-turn`、后续轮不出现；`/clear` 后下一轮再次出现）
- [ ] **AC10**（spec AC10，F2.2/F6.5）: async + 拦截事件校验（验证：配置 `async: true` 的 `pre_tool_use` hook，启动 stderr 打印 `hook "<name>": async not allowed for blocking events, skipped` 并跳过，其余 hook 正常加载）
- [ ] **AC12**（spec AC12，F6.6）: 未知事件定位（验证：`event: UnknownEvent`，启动 stderr 打印 `hook "<name>": unknown event "UnknownEvent", skipped`，其余 hook 正常加载）
- [ ] **AC16**（spec AC16，F5.13）: agent 占位日志（验证：`agent` 动作 hook，启动 stderr 出现 `[hook <name>] agent not yet implemented, skipped`，Agent 主流程不受影响）
- [ ] **AC17**（spec AC17，F4.1）: 条件互斥校验（验证：`if` 同时含 `all_of` 与 `any_of`，启动 stderr 报错跳过该条，其余 hook 加载正常）
- [ ] **AC18**（spec AC18，F4.4）: 点分路径缺字段（验证：field `tool_input.path` 在非写文件事件上按空字符串求值，不报错——单测断言 `get_by_path({"tool_input":{}}, "tool_input.path") == ""`）
- [ ] **AC19a**（spec AC19a，F4.7/F4.8）: 模板变量替换（验证：动作模板 `{tool_input.path}` 替换为实际写文件路径；未知字段替换为空串、裸 `{}` 返回原文、不报错——单测断言 `render_template`）
- [ ] **AC21**（spec AC21，N10）: 未配置 Hook 时行为不变（验证：`Agent(hooks=None, runtime=None)` 下既有 agent 测试全绿；接线测试断言 hooks=None 时 `_dispatch_hook` 返回空 DispatchResult、不触发任何 dispatch、AgentLoop 行为与 ch11 一致）

## 集成

- [ ] **AC7**（spec AC7，F7.4）: 拦截整合——权限引擎未被调用 + PhaseEnd is_error=True（验证：AC5 拦截发生时，`permission.check` 未被调用（不弹审批）、产出 TOOL_CALL + TOOL_RESULT(status=error) 事件即 PhaseEnd is_error 语义）
- [ ] **AC11**（spec AC11，F7.5）: user_prompt_submit 拦截（验证：hook 条件 `prompt` 正则匹配 `(?i)delete` + `exit 2`，TUI 输入「请帮我 delete 那个文件」被拦截、输入框下方提示 `[hook <name>] prompt contains delete keyword`、消息未进入对话历史、焦点回输入框）
- [ ] **AC13**（spec AC13，F6.4）: 三层同名冲突 + /hooks 合并（验证：本地/项目/用户各放同名 hook，启动 stderr 提示冲突保留高优先级层；`/hooks` 输出合并列表、末尾 `Loaded from:` 含三个来源路径）
- [ ] **AC14**（spec AC14，F5.9）: turn_end http 通知（验证：`turn_end` hook 动作 `http: POST http://localhost:9999/done`，本地 echo server 收到一次 POST 且 body 含 `"event":"turn_end"`）
- [ ] **AC15**（spec AC15，F5.11）: http decision:block 拦截（验证：`pre_tool_use` hook 动作 http 调本地桩返回 `{"decision":"block","reason":"network policy"}`，Bash 调用被拦截、其它工具不受影响）
- [ ] **AC19**（spec AC19，F10.1）: /hooks 命令输出（验证：按 event 分组、每条一行 `<name> <event> <action.type> <flags>`，flags 含 `[once]`/`[async]`；无 hook 时输出 `No hooks loaded.`）
- [ ] **AC20**（spec AC20，F9.2）: file_change 防重入（验证：`file_change` hook 的格式化动作再次触发 file_change 时不无限递归——单测断言 dispatch 期间同事件不重入自身）
- [ ] **AC22**（spec AC22，N4/F9.3）: 拦截同步 + 取消不卡死（验证：拦截事件同步执行中用户取消，`Agent.run` 收到 CancelledError 正常退出不挂死——单测断言）
- [ ] **AC23**（补充，plan 事件职责划分）: 18 事件接线（验证：agent 层 11 个节点（turn_start/turn_end/pre_tool_use/post_tool_use/pre_send/post_receive/error/pre_compact/post_compact/permission_request/file_change）+ TUI 层 5 个（user_prompt_submit/command_execute/session_start/session_end/session_resume）+ main 层 2 个（startup/shutdown）各 emit 点被触发——接线测试用真实 Engine + 合成 rules 断言事件与 payload）
- [ ] **AC24**（补充，plan reminder 注入）: `<hook-notification>` 标签注入（验证：hook prompt 以 `<hook-notification>...</hook-notification>` 标签 Message 进 reminders，置于 plan reminder 之后，不入持久历史、不影响压缩）
- [ ] **AC25**（补充，plan 集中重置）: pending_reminders 生命周期（验证：`SessionRuntime.reset_for_new_session` 统一清空 pending_reminders + 调 `hook_engine.reset_for_new_session()` 清 once；`append_reminders`/`take_reminders` 加锁行为——单测断言）
- [ ] **AC26**（补充，plan set_context_providers）: session_id/mode 注入（验证：dispatch 时 payload 通用字段 session_id/mode 由 `set_context_providers` 注入的实际值填充，hooks 不反向依赖 session/permission——接线测试断言）

## 编译与测试

- [ ] 全部单元测试通过（验证：`export PYTHONIOENCODING=utf-8 && python -m pytest tests/ -q` 全绿，含新增 test_ch12_* 与存量 ch08-ch11 测试不受影响）
- [ ] ruff format 与 lint 清洁（验证：`ruff format --check . && ruff check .` 无输出）
- [ ] docs/ 不可变（验证：跑完批量命令后 `git status` 确认 docs/ch12/ 仅四份文档、无意外改动）
- [ ] 版本号一致（验证：`newcode/__init__.py` 与 `pyproject.toml` 均为 0.12.0，`python -c "import newcode; print(newcode.__version__)"` 输出 0.12.0）

## 端到端场景

- [ ] **E2E1**（spec E2E1）: 自动格式化——配置 `post_tool_use` hook（条件 `tool_name=write_file` 且 `is_error=false`，动作 `command: ruff format $(jq -r .tool_input.path)`、async、timeout=5s），LLM 写 Python 文件后 ruff 异步后台执行、主对话流不暂停；命令失败时 stderr 打印失败日志、Agent 不中断（验证：集成测试 mock provider 断言 + 真实 TUI 人工确认）
- [ ] **E2E2**（spec E2E2）: 危险命令拦截——`pre_tool_use` hook 条件 `tool_input.command` glob 匹配 `rm -rf *` 时 `exit 2`，LLM 执行 `rm -rf` 被拦截、拒绝原因反馈给 LLM、权限引擎未被调用（验证：集成测试 mock provider 断言 + 真实 TUI 人工确认）
- [ ] **E2E3**（spec E2E3）: 上下文注入——`turn_start` hook 注入「请先读 ARCHITECTURE.md」prompt，Agent 下一轮请求前 reminder 区出现该文本、对话历史结构不变（验证：集成测试断言 reminder 注入 + 真实 TUI 人工确认）
- [ ] **E2E4**（spec E2E4）: 防递归——`file_change` hook 格式化文件，格式化再次触发 file_change 时不无限循环（验证：集成测试断言防重入 + 真实 TUI 人工确认）
- [ ] **E2E5**（待人工验证）: T14 手动冒烟五步——`/hooks` 列出三条 hook（block-rm / zh-hint / fmt-once）并显示 Loaded from `.newcode/config.local.yaml` → Agent 执行 `rm -rf /tmp/x` 被拦截显示 `[hook block-rm] dangerous: rm -rf` 不弹审批 → 首轮 reminder 出现 zh-hint 注入、后续轮不注入 → 写文件触发 fmt-once 一次、再写不触发 → `/clear` 后写文件 fmt-once 再次触发（验证：真实终端 `python -m newcode` 人工操作；自动环境无法执行，替代验证见 AC5/AC8/AC9/AC14 单测与集成覆盖）

## 验收报告格式（阶段六使用）

```
## 验收报告

### 通过（N/M）
- [x] 条目 — 证据：命令输出 / 测试结果 / 观察行为

### 未通过（如有）
- [ ] 条目 — 预期：X，实际：Y，修复方案：...

### 待人工验证（如有）
- [ ] 条目 — 原因：需真实终端交互；替代验证：对应单测/集成测试；风险：...；补验：E2E5 由用户在 TUI 手动执行

### 端到端
- [x] E2E1 — 结果：...
- [ ] E2E5 — 待人工验证：需真实终端
```
