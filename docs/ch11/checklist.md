# NewCode ch11 - Skill 技能包系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。验证方式写在括号里。
> AC 编号与 spec.md 一一对应；AC22-AC24 为 plan 层补充的集成验证点。
> 标注「待人工验证」的条目需真实终端交互，自动测试环境无法执行。

## 实现完整性

- [ ] **AC1**（spec AC1）: 合法 Skill 文件解析出完整元数据（验证：构造 frontmatter+body 的 skill，`parse_skill_dir` 返回 name/description/mode/context/allowed_tools/source_path，`/skill list` 与 `/skill info` 正确显示）
- [ ] **AC2**（spec AC2）: frontmatter 非法的 Skill 不阻断整体加载（验证：目录混入一个坏文件，其余 Skill 仍被 Catalog 加载，日志有 warning）
- [ ] **AC3**（spec AC3）: 三级同名覆盖，项目级 > 用户级 > 内置级（验证：三级各放同名 skill，`get(name)` 返回项目级，`get_source_label` 为 `project`）
- [ ] **AC8**（spec AC8）: fail-fast 启动校验（验证：构造 allowedTools 引用不存在工具的 Skill，启动时 `validate_tools` 返回其名、被从 Catalog 移除并 warning，其余 Skill 正常加载）
- [ ] **AC9**（spec AC9）: `$ARGUMENTS` 替换与兜底（验证：含占位符 body 替换正确；显式 `/name args` 替换 args、自然语言触发替换为空；无占位符且 args 非空末尾追加 `## User Request`；无占位符无 args 原样返回）
- [ ] **AC18**（spec AC18）: 内置三 Skill 内容（验证：读 builtin 源文件断言 commit 含「逐个 add / conventional commit / >10 文件建议拆分」、review 为 fork 且含五维审查与分级、test 含区分两种失败的方法）
- [ ] **AC20**（spec AC20）: 热更新 + 容错（验证：改源文件后 `get(name)` 返回新 body 无需重启；构造解析失败场景，`get(name)` 回退旧版并 warning）
- [ ] **AC21**（spec AC21）: 名字归一化（验证：`My_Skill.md` → name `my-skill`，`/my-skill` 命令注册成功；mode/context 非法值 warning 降级不阻断）

## 集成

- [ ] **AC4**（spec AC4）: Skill 加载后自动注册为 `/<名字>` 命令，描述标注 `[skill]`，出现在 `/help` 与 Tab 补全（验证：注册后 `registry.get` 命中、description 含 `[skill]`；`/commit` 显式触发 commit Skill）
- [ ] **AC5**（spec AC5）: 自然语言意图触发（验证：mock provider 让 Agent 输出 `load_skill` 工具调用，断言工具被执行、Skill 进入激活态）
- [ ] **AC6a**（spec AC6）: 两阶段加载——启动摘要不含完整 body（验证：启动后 env 含「Available Skills」摘要段（name+description）但**不含**任何 Skill 的完整 SOP）
- [ ] **AC6b**（spec AC6）: 两阶段加载——load_skill 激活后 env 含完整 body（验证：调 `load_skill({name})` 后，下一轮 env 的 `## Active Skills` 段含完整 SOP，且完整 SOP **不在**消息历史中）
- [ ] **AC7a**（spec AC7）: allowedTools 提示（验证：inline 激活 allowedTools=[read_file] 的 Skill 后，env 的 SOP 顶部含「优先使用 read_file」提示，模型可见工具集仍为全量）
- [ ] **AC7b**（spec AC7）: fork 工具集收窄（验证：fork Skill 子 Agent 的工具定义 = 系统工具 ∪ allowedTools，其余不可见）
- [ ] **AC10**（spec AC10）: inline 结果留在主对话（验证：inline Skill 执行后主对话历史含执行过程，无独立子对话）
- [ ] **AC11**（spec AC11）: fork 独立执行 + 结果回流 + token 报告（验证：mock provider 驱动 fork Skill，断言独立 ConversationManager 隔离、final_text 经 `append_assistant_message` 写回主对话、累计 token 计入主 anchor）
- [ ] **AC12**（spec AC12）: `/skill` 七子命令行为（验证：list/info/reload/load/on/off/unload 各 handler 经 RecordingUI 断言，list 排版对齐 `{name:<20} {desc} [{source}]`）
- [ ] **AC13**（spec AC13）: on/off 跨会话持久（验证：`/skill off commit` 后重启进程，commit 仍禁用且不在摘要；`/skill on commit` 恢复）
- [ ] **AC14**（spec AC14）: 压缩预算淘汰 + 恢复段注入（验证：激活多个 Skill 超过 4k token 预算，mock 压缩流程触发 `enforce_budget`，最早的被踢出、恢复段注入幸存者）
- [ ] **AC15**（spec AC15）: `/clear` 清空 activeSkills（验证：激活后 `/clear`，后续 env 不再含旧 SOP，只重新出现阶段一摘要）
- [ ] **AC16**（spec AC16）: 目录型 Skill 工具注册 + 子进程执行（验证：tool.json 工具注册进工具面，模型可调用，execute 以子进程跑 entrypoint 并返回 stdout）
- [ ] **AC17**（spec AC17）: Skill 嵌套（验证：Skill A 的 SOP 内调 `load_skill` 激活 Skill B，B 的 SOP 随后进入 env）
- [ ] **AC19**（spec AC19）: 向后兼容（验证：无 Skill 激活且无 catalog 时，env 与工具集行为与 ch10 一致，既有测试全绿）
- [ ] **AC22**（补充）: fork 的 context 三策略（验证：none=仅 body / recent=最近 5 条+body / full=LLM 摘要+body，单测断言 fork 初始历史）
- [ ] **AC23**（补充）: LoadSkill 系统工具豁免（验证：`load_skill` 的 `is_system=True`、`read_only=True`，不弹权限提示，allowedTools 过滤时透传；目录型 Skill 的 tool.json 工具为普通工具参与过滤）
- [ ] **AC24**（补充，spec F6.4）: review 命令迁移（验证：内置 `/review` 硬编码命令已移除，`/review` 由 review Skill（fork）自动注册接管）

## 编译与测试

- [ ] 全部单元测试通过（验证：`export PYTHONIOENCODING=utf-8 && python -m pytest tests/ -q` 全绿，含新增 ch11 测试与存量迁移）
- [ ] ruff format 与 lint 清洁（验证：`ruff format --check . && ruff check .` 无输出）
- [ ] docs/ 不可变（验证：跑完批量命令后 `git status` 确认 docs/ch11/ 仅四份文档、无意外改动）

## 端到端场景

- [ ] **E2E1**（spec E2E1）: 用户说「帮我提交一下这些改动」→ Agent 经 `load_skill` 激活 commit（inline）→ 逐个 add + conventional commit → 完成后对话继续；`/skill list` 显示 commit 已激活（验证：集成测试 mock provider 断言 + 真实 TUI 人工确认）
- [ ] **E2E2**（spec E2E2）: 用户说「审查一下这段代码」→ Agent 激活 review（fork）→ 独立对话五维审查 → 分级报告摘要回流主对话（验证：集成测试 mock provider 断言 + 真实 TUI 人工确认）
- [ ] **E2E3**（spec E2E3）: `/skill off review` → review 从摘要与可用列表移除、立即失活；重启 NewCode → review 仍禁用（验证：自动断言 + 重启人工确认）
- [ ] **E2E4**（spec E2E4）: 用户自己写 skill（改 `$ARGUMENTS` 替换模板），编辑源文件后不重启再触发即生效（验证：集成测试断言热更新 + 真实 TUI 人工确认）
- [ ] **E2E5**（待人工验证）: T22 手动冒烟五步——`/help` 列出 `/test-skill` 与 `/skill` → `/test-skill` 加载 SOP → 编辑源文件不重启再触发新内容生效 → 自然语言触发 `load_skill` 后 env 出现完整 SOP → `/clear` 后 env 不再出现旧 SOP（验证：真实终端 `python -m newcode` 人工操作；自动环境无法执行，替代验证见 AC6a/AC6b/AC20 单测覆盖）

## 验收报告格式（阶段六使用）

```
## 验收报告

### 通过（N/M）
- [x] 条目 — 证据：命令输出 / 测试结果 / 观察行为

### 未通过（如有）
- [ ] 条目 — 预期：X，实际：Y，修复方案：...

### 待人工验证（如有）
- [ ] 条目 — 原因：需真实终端交互；替代验证：对应单测/集成测试；风险：...；补验：T22 由用户在 TUI 手动执行

### 端到端
- [x] E2E1 — 结果：...
- [ ] E2E5 — 待人工验证：需真实终端
```
