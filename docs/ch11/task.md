# MewCode ch11 - Skill 技能包系统 Tasks

> 顺序执行。每完成一个任务跑 `ruff check mewcode/skills mewcode/tools/load_skill.py` 确保无 lint 错；接入主流程的任务（T21）做完后立刻跑一次端到端冒烟（T22）再进下一项。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `mewcode/__init__.py`、`pyproject.toml` | 版本号 0.10.0 → 0.11.0 |
| 新建 | `mewcode/skills/__init__.py` | 包导出（Catalog/ActiveSkills/Executor/SkillSource） |
| 新建 | `mewcode/skills/constants.py` | 预算/路径常量、SYSTEM_TOOL_NAMES |
| 新建 | `mewcode/skills/types.py` | SkillMeta/Skill/SkillSource/ActiveEntry/ToolSchema |
| 新建 | `mewcode/skills/parser.py` | 解析 frontmatter+body、归一化、校验 |
| 新建 | `mewcode/skills/render.py` | render_body（$ARGUMENTS/兜底/提示） |
| 新建 | `mewcode/skills/catalog.py` | 三级扫描/覆盖/热加载/validate_tools/disabled/get_catalog |
| 新建 | `mewcode/skills/active.py` | ActiveSkills（激活/失活/预算淘汰） |
| 新建 | `mewcode/skills/adapter.py` | catalog_to_prompt_items / active_to_prompt_entries |
| 新建 | `mewcode/skills/executor.py` | inline / fork 执行器 |
| 新建 | `mewcode/skills/script_tool.py` | 目录型工具子进程壳 |
| 新建 | `mewcode/skills/builtin/{commit,review,test}.md` | 三个内置 Skill |
| 新建 | `mewcode/prompt/skills_block.py` | env 段 Skill 渲染 + 瘦类型 |
| 新建 | `mewcode/tools/load_skill.py` | LoadSkill 系统工具 |
| 新建 | `mewcode/slash/commands/skill.py` | /skill 管理命令 |
| 新建 | `mewcode/slash/commands/skill_register.py` | Skill 动态注册为 /名字 |
| 修改 | `mewcode/tools/base.py` | Tool 协议加 is_system |
| 修改 | `mewcode/tools/registry.py` | system_definitions / definitions_filtered |
| 修改 | `mewcode/agent/agent.py` | with_catalog / activate_skill / env 合成 |
| 修改 | `mewcode/context/recovery.py`、`context/manager.py` | 预算淘汰 + 恢复段注入 |
| 修改 | `mewcode/slash/registry.py` | unregister / remove_by |
| 修改 | `mewcode/slash/ui.py` | 4 个 UI 方法 |
| 修改 | `mewcode/slash/commands/clear.py` | 追加 clear_active_skills |
| 修改 | `mewcode/slash/commands/__init__.py` | 移除 review、加 skill/skill_register |
| 修改 | `mewcode/main.py` | 装配 Skill 系统 |
| 删除 | `mewcode/slash/commands/review.py` | 被 review Skill 接管 |
| 删除 | `mewcode/context/skill.py` | 骨架被 skills/ 取代 |
| 新建 | `tests/test_ch11_{parser,catalog,render,active,executor,load_skill,script_tool,skill_command,register,integration}.py`、`tests/test_prompt_skills.py` | 11 个测试文件 |

## T1: 版本号更新到 0.11.0

**文件：** `mewcode/__init__.py`、`pyproject.toml`
**依赖：** 无
**完成标准：**
- [ ] `mewcode/__init__.py` 的 `__version__` = `"0.11.0"`
- [ ] `pyproject.toml` 的 `version` = `"0.11.0"`，两处一致
- [ ] 独立提交 `chore: bump version to 0.11.0`

**验证：** `export PYTHONIOENCODING=utf-8 && python -c "import mewcode; print(mewcode.__version__)"` 输出 0.11.0。

## T2: skills 包骨架——constants + types

**文件：** `mewcode/skills/__init__.py`、`mewcode/skills/constants.py`、`mewcode/skills/types.py`
**依赖：** T1
**完成标准：**
- [ ] `constants.py`：`ACTIVE_SKILL_TOKEN_BUDGET = 4_000`、`RECENT_DEFAULT_N = 5`、`PROJECT_SKILLS_DIR = ".mewcode/skills"`、`USER_SKILLS_DIR = "~/.mewcode/skills"`、`DISABLED_STATE_FILE = "~/.mewcode/skills/disabled.json"`、内置目录路径
- [ ] `types.py`：`SkillMeta`（name/description/allowed_tools 缺省 []/mode 缺省 "inline"/fork_context 缺省 "none"/model 缺省 None + `is_fork()`）、`Skill`、`SkillSource`（USER/PROJECT/BUILTIN）、`ActiveEntry`（name/body）、`ToolSchema`、`SkillParseError`、`SkillDependencyError`
- [ ] `__init__.py` 导出核心类型

**验证：** `python -c "from mewcode.skills.types import SkillMeta, Skill, SkillSource; s=SkillMeta(name='commit'); assert s.mode=='inline' and s.fork_context=='none'"`。

## T3: skills/parser.py

**文件：** `mewcode/skills/parser.py`
**依赖：** T2
**完成标准：**
- [ ] `normalize_name(name)`：转小写、非字母数字转 `-`（F1.4）
- [ ] `parse_frontmatter_and_body(raw)`：处理 `---\n...\n---\n` 格式，分离 frontmatter 与 body
- [ ] `_validate_meta`：`name` 正则 `^[a-z][a-z0-9\-]*$` 非法抛 `SkillParseError`；`mode`/`context` 取值非法 warning 降级为缺省值（mode→inline、context→none），不阻断加载
- [ ] `parse_skill_dir(dir_path, source)`：读 SKILL.md、组装 Skill；mode/context 非法值 warning 降级（与 `_validate_meta` 一致）；目录型读 tool.json → ToolSchema 列表
- [ ] frontmatter 缺失/非 YAML/非 dict/必填缺失 → `SkillParseError`（不抛出到调用方，T5 捕获跳过）

**验证：** `python -c "from mewcode.skills.parser import normalize_name; assert normalize_name('My_Skill')=='my-skill'"`；构造非法 frontmatter smoke。

## T4: skills/render.py

**文件：** `mewcode/skills/render.py`
**依赖：** T2
**完成标准：**
- [ ] 替换所有 `$ARGUMENTS` 为 args（F3.3）
- [ ] 无占位符且 args 非空 → 末尾追加 `\n\n## User Request\n\n<args>`（兜底规则）
- [ ] `allowed_tools` 非空 → body 顶部插入「本 Skill 设计为只用这些工具，优先使用」提示段（F3.4 提示，不真过滤）

**验证：** 构造含占位符/无占位符两种 body 手动断言（T23 单测覆盖）。

## T5: skills/catalog.py 基础

**文件：** `mewcode/skills/catalog.py`
**依赖：** T2、T3
**完成标准：**
- [ ] `Catalog`：`_by_name`/`_order`/`_cache`/`_lock`（threading.RLock）
- [ ] 构造扫描：**内置 → 用户 → 项目**，后扫同名覆盖（F2.1，项目级 > 用户级 > 内置级）；`_scan_directory(path, source)` 同时处理 `*.md` 与 `/SKILL.md` 两种布局，目录型 `is_directory=True`
- [ ] `get(name)`：每次重读源文件（热更新），失败回退 `_cache` 旧版并 `log.warning`（F2.3/N7）
- [ ] `list()` / `names()` 对外查询（排除 disabled）

**验证：** 临时目录放 skill，`Catalog.load` 后 `list()` 含它；改源文件后 `get()` 返回新 body；构造解析失败文件验证回退。

## T6: skills/catalog.py 扩展

**文件：** `mewcode/skills/catalog.py`
**依赖：** T5
**完成标准：**
- [ ] `validate_tools(registry)`：遍历所有 allowed_tools 引用是否在 registry 存在，返回不通过名单（F2.7/B 决策）
- [ ] disabled 集合：`is_disabled(name)` / `set_disabled(name, bool)`，落盘 `~/.mewcode/skills/disabled.json`（F7.8）
- [ ] `reload(work_dir)` 返回 `(added, removed)`（借鉴项 8）
- [ ] `get_catalog()` 返回 `[(name, description), ...]`（结构化列表，排除 disabled，文本拼接上移到装配层，参考模板）；`get_source_label(name)` 按路径前缀返回 `project | user | builtin`

**验证：** 构造引用不存在工具的 skill → `validate_tools` 返回其名；`set_disabled` 后 `list()` 排除它且进程重启保持；`get_catalog()` 返回元组列表。

## T7: skills/active.py

**文件：** `mewcode/skills/active.py`
**依赖：** T2
**完成标准：**
- [ ] `ActiveSkills`：`_entries`/`_index`/`_lock`
- [ ] `activate(name, body)`（重复激活覆盖原位置）/ `deactivate` / `clear` / `snapshot`（拷贝）/ `names`
- [ ] `total_tokens(estimator)`（兼容 ch08 接口，N10）
- [ ] `enforce_budget(4k)`：按激活顺序淘汰最旧直至 ≤ 预算，返回幸存列表（F8.1）

**验证：** 激活 3 条、enforce_budget 小预算断言最旧被踢（T23 单测）。

## T8: skills/adapter.py + prompt/skills_block.py

**文件：** `mewcode/skills/adapter.py`、`mewcode/prompt/skills_block.py`
**依赖：** T2、T7
**完成标准：**
- [ ] `prompt/skills_block.py`：瘦类型 `SkillCatalogItem(name, description)`、`ActiveSkillEntry(name, body)`；`render_skills_catalog(items)`（Available Skills 段 + load_skill 指引，空返回空串）、`render_active_skills_block(entries)`（`## Active Skills` 段逐条 `### Skill: <name>` + body，空返回空串）
- [ ] `skills/adapter.py`：`catalog_to_prompt_items(catalog)`、`active_to_prompt_entries(active)`（借鉴项 1：prompt 包零依赖 skills）

**验证：** `python -c "from mewcode.prompt.skills_block import render_active_skills_block; assert render_active_skills_block([])==''"`；构造 entries 断言输出含 name/body。

## T9: tools/base.py + registry.py 扩展

**文件：** `mewcode/tools/base.py`、`mewcode/tools/registry.py`
**依赖：** 无（独立）
**完成标准：**
- [ ] `base.py`：Tool 协议新增只读属性 `is_system: bool`（实现默认 False）；`SYSTEM_TOOL_NAMES = frozenset({"load_skill"})` 双保险常量（属性 + 名单，参考模板）
- [ ] `registry.py`：`system_definitions()`（仅系统工具）、`definitions_filtered(allowed: list[str])`（系统工具豁免 + 白名单过滤，allowed 空 → 全量；`getattr(tool, "is_system", False)` 兼容旧工具）
- [ ] `SkillDependencyError` 引用自 `skills.types`（避免重复定义）

**验证：** `python -m pytest tests/test_tools.py -q` 全绿（旧工具不受 is_system 新增影响）。

## T10: tools/load_skill.py

**文件：** `mewcode/tools/load_skill.py`
**依赖：** T5、T7、T9
**完成标准：**
- [ ] `LoadSkillTool(catalog, active, registry)`：`name="load_skill"`、`read_only=True`、`is_system=True`（N5/F3.5）
- [ ] `execute({name})`：catalog.get → 不存在返回 `unknown skill: <name>`（附可用列表）；重读 body → 目录型注册 tool.json 工具 → active.activate → 返回简短确认「Skill <name> activated. SOP pinned to environment context.」（不返回完整 SOP，F4.2.3）

**验证：** mock provider 驱动真实工具路径：调 execute 断言确认文本与 active 状态（T24 单测；此处 smoke import）。

## T11: skills/script_tool.py

**文件：** `mewcode/skills/script_tool.py`
**依赖：** T2、T9
**完成标准：**
- [ ] `ScriptTool(schema, skill_dir)`：`is_system=False`、`read_only=False`
- [ ] `execute(arguments)`：`asyncio.create_subprocess_exec` 起 entrypoint 子进程（JSON 走 stdin），`asyncio.wait_for(..., timeout=30)`，stdout → ToolResult.output；超时/失败 → error 结果（借鉴项 5）

**验证：** 构造 echo 临时脚本 execute 断言输出；sleep 脚本断言 30s 超时路径（T24 单测）。

## T12: skills/executor.py（inline 分支）

**文件：** `mewcode/skills/executor.py`
**依赖：** T4、T5、T7、T8、T10
**完成标准：**
- [ ] `Executor` 构造（catalog/store/registry/provider/engine/version）
- [ ] `execute(ctx, ui, name, args)` 骨架：catalog.get → 不存在返回错误
- [ ] inline 分支：磁盘重读 body（失败回退缓存）→ render_body → 目录型注册工具 → store.activate → `ui.inject_and_send(f"/{name}", body)` 触发回合（F3.1/F4.2.1）；**不立即调 LLM**（rendered 钉到 env 后由 command handler 触发 loop）

**验证：** RecordingUI 桩驱动：execute inline skill 后断言 store 已激活 + UI 收到注入（T24 单测）。

## T13: skills/executor.py（fork 分支）

**文件：** `mewcode/skills/executor.py`
**依赖：** T12
**完成标准：**
- [ ] fork 分支按 `fork_context` 构造独立 Conversation：
  - `none`：仅一条 user（rendered_body）
  - `recent`：主对话拷最近 N（缺省 5）条 + 追加 rendered_body
  - `full`：LLM 压缩主对话成摘要（复用 summarize 模式）作单条 user 消息插入 + 追加 rendered_body
- [ ] `registry.definitions_filtered(allowed)` 收窄子 Agent 工具集（F3.7）；`skill.meta.model` 非空 → new_provider 覆盖
- [ ] 独立内存 ConversationManager + 临时 Agent（局部 import 避循环），跑至 DONE；累计 token 写回主 anchor（`usage += sub`，N13）；`ui.append_assistant_message(final_text)` 写回主对话（F3.1）
- [ ] 任一步出错 → `final_text = "[skill <name> failed: <reason>]"` 仍写回

**验证：** mock provider 产出事件流 → execute fork skill → 断言子对话隔离 + final_text 经 append_assistant_message 写回 + token 计入主 anchor（T24 单测）。

## T14: skills/builtin/ 三个内置 Skill

**文件：** `mewcode/skills/builtin/commit.md`、`review.md`、`test.md`
**依赖：** T2（格式）
**完成标准：**
- [ ] `commit.md`：frontmatter `mode: inline`；body 写清 conventional commit 流程（status → diff 区分 staged/unstaged → 逐个 add → commit → >10 文件建议拆分）（F6.1）
- [ ] `review.md`：frontmatter `mode: fork`；body 写清五维审查 + 分级报告 + 正面反馈（F6.2）
- [ ] `test.md`：frontmatter `mode: inline`；body 写清三步流程 + 区分两种失败的方法（F6.3）

**验证：** 三个都能经 `parse_skill_dir` 解析、mode 正确（T23 覆盖）。

## T15: agent/agent.py 集成

**文件：** `mewcode/agent/agent.py`
**依赖：** T5、T7、T8
**完成标准：**
- [ ] `with_catalog(catalog)` 可选注入（None 时跳过 env 组装，N10）
- [ ] `activate_skill(name, body)` / `clear_active_skills()` 转发 store
- [ ] `run()` 每轮组装 env：`base_env + render_skills_catalog(...) + render_active_skills_block(...)`（F4.1/F5.2）；`tool_defs` 保持全量（inline 不真过滤，F5.3）

**验证：** 无 catalog 时 env 行为与 ch10 一致（既有 agent 测试全绿）；注入 catalog+激活后 env 含 SOP（T25 单测）。

## T16: context/recovery.py + manager.py + 删 skill.py

**文件：** `mewcode/context/recovery.py`、`mewcode/context/manager.py`、删除 `mewcode/context/skill.py`
**依赖：** T7
**完成标准：**
- [ ] `RecoveryBuilder` 持有 ActiveSkills（复用 skill_registry 参数位），skill 分支落地：`enforce_budget(4k)` → 幸存激活 Skill 追加进恢复段（F8.1）
- [ ] `context/manager.py`：skill_registry 参数位改为 ActiveSkills；更新 import
- [ ] 删除 `context/skill.py`（骨架被 skills/ 取代）

**验证：** `python -m pytest tests/test_context_recovery.py tests/test_context_manager.py -q` 迁移后全绿（test_context_skill.py 改测 ActiveSkills 或删除）。

## T17: slash/registry.py

**文件：** `mewcode/slash/registry.py`
**依赖：** 无
**完成标准：**
- [ ] `unregister(name)`：锁内删除（含别名键）
- [ ] `remove_by(filter)`：按谓词批量移除（供 remove_skill_commands 用）

**验证：** `python -m pytest tests/test_ch10_registry.py -q` 全绿 + 新增 unregister 用例（T25）。

## T18: slash/ui.py

**文件：** `mewcode/slash/ui.py`
**依赖：** 无
**完成标准：**
- [ ] UI 协议新增：`list_catalog_skills()`、`list_active_skills()`、`clear_active_skills()`、`append_assistant_message(text)`（借鉴项 2）
- [ ] `NopUI` 零值实现、`RecordingUI` 记录调用

**验证：** `python -m pytest tests/test_ch10_tui.py -q` 全绿（协议扩展不破坏既有桩）。

## T19: slash/commands/skill_register.py

**文件：** `mewcode/slash/commands/skill_register.py`
**依赖：** T12、T13、T17、T18
**完成标准：**
- [ ] `register_skills_as_commands(reg, catalog, executor)`：每个 Skill 注册 `CommandDef(name, kind=UI, description=f"{description} [skill]", handler)`；**闭包用 `functools.partial(handler, name=skill.name)` 显式拷贝**（借鉴项 3）；冲突 `except RuntimeError → warning 跳过`（F2.5）
- [ ] handler 按 mode 分发：inline → executor.execute + ui.inject_and_send；fork → `asyncio.create_task(_run_fork)`
- [ ] 模块级 `_REGISTERED_SKILL_NAMES` 集合跟踪已注册，再调用先清旧（借鉴项 5）；`remove_skill_commands(reg)` 用 `remove_by` 清掉 `[skill]` 标注命令

**验证：** 构造 Catalog（含 conflict 名）→ 注册后 registry.get 命中、冲突项被跳过并 log；`[skill]` 标注在 description（T25 单测）。

## T20: slash/commands/skill.py + clear.py + __init__.py + 删 review.py

**文件：** `mewcode/slash/commands/skill.py`（新）、`clear.py`（改）、`__init__.py`（改）、删除 `review.py`
**依赖：** T17、T18、T19
**完成标准：**
- [ ] `skill.py`：`/skill` 命令（KindLocal），子命令 list / info <n> / reload [n] / load <n> / on <n> / off <n> / unload <n>（F7）；list 输出对齐排版 `f"  {name:<20} {desc}  [{source}]"`（借鉴项 7）；reload 同步命令注册（用 added/removed）；off 同时 store.deactivate + set_disabled
- [ ] `clear.py`：`handle_clear` 在 request_clear_session 后调 `ui.clear_active_skills()`（F5.5）
- [ ] `__init__.py`：COMMAND_MODULES 移除 review、加 skill 与 skill_register 装配；删除 `review.py`

**验证：** `python -m pytest tests/test_ch10_commands.py tests/test_ch10_integration.py -q` 迁移后全绿（review 相关用例改为断言 review Skill 注册）；/skill 各子命令 RecordingUI 断言（T25）。

## T21: main.py 装配

**文件：** `mewcode/main.py`
**依赖：** T5-T20
**完成标准：**
- [ ] 装配顺序（借鉴项 3）：**先 `LoadSkillTool` 注册进 registry，再构造 Agent**（保证 registry 已含 load_skill）→ `Catalog.load(work_dir)` → `validate_tools(registry)`（坏项 warning + 从 catalog 移除，B 决策）→ `ActiveSkills()` 注入 agent 与 ContextManager → `Executor(...)` → `register_skills_as_commands` → `/skill` 命令注册
- [ ] `get_catalog()` 结果在装配层拼成 `"You can use the following Skills:\n\n- <name>: <desc>\n...\nIf the user's request matches a Skill, call load_skill to activate it."` 调 `agent.with_catalog(...)`（借鉴项 4：结构化列表 + 上移拼接）
- [ ] `CommandContext` 塞入 skill 依赖（catalog/store/executor）供 handler 取用

**验证：** `python -c "import mewcode.main"` 通过；T22 端到端冒烟覆盖装配后行为。

## T22: 手动端到端冒烟（参考模板 T10 五步）

**文件：** 无（仅运行验证）；测试用 skill 目录 `.mewcode/skills/test-skill/SKILL.md`
**依赖：** T21
**完成标准：**
- [ ] 创建测试 skill：`.mewcode/skills/test-skill/SKILL.md`（frontmatter `name: test-skill / description: A test skill / mode: inline`，body `Echo hello`）
- [ ] 手动启动 `python -m mewcode`，依次验证：
  1. `/help` 列出 `/test-skill`、`/skill` 命令（含 `[skill]` 标注）
  2. `/test-skill` 能加载对应 SOP 并执行
  3. 编辑 SKILL.md 改一行后**不重启**再 `/test-skill`，新行进入 prompt（热重载验证）
  4. 自然语言触发 `load_skill({name: "test-skill"})`，env 出现完整 SOP
  5. `/clear` 后 env 不再出现旧 SOP

**验证：** 人工操作 TUI 观察 5 步现象（此任务需真实终端，属「待人工验证」，其余 T23-T26 自动跑）。

## T23: skills 核心单测

**文件：** `tests/test_ch11_parser.py`、`test_ch11_render.py`、`test_ch11_catalog.py`、`test_ch11_active.py`、`test_prompt_skills.py`
**依赖：** T2-T8
**完成标准（覆盖点清单，参考模板 T11）：**
- [ ] parser：valid / missing opening `---` / unclosed / invalid yaml / non-dict / missing name / missing description / invalid name format / invalid mode / fork mode with context / nonexistent file / 目录型 tool.json
- [ ] substitute_arguments / render_body：with args / without args / no placeholder / multiple placeholders / ## User Request 兜底 / allowed_tools 顶部提示
- [ ] catalog：三级加载 / 项目覆盖用户 / 内置最低优先级 / get / get_unknown / 热重载成功 / 热重载失败回退 / 目录型识别 / source_label / 失败文件跳过 / reload (added,removed) / validate_tools / disabled 持久
- [ ] active：激活 / 重复激活覆盖 / 失活 / clear / snapshot 拷贝 / enforce_budget 淘汰最旧 / total_tokens
- [ ] prompt_skills：catalog 块 / active 块 / 空块返回空串 / 多 Skill 并存
- [ ] 每个测试 docstring 标注防的 bug（CLAUDE.md 测试规范）

**验证：** `python -m pytest tests/test_ch11_parser.py tests/test_ch11_render.py tests/test_ch11_catalog.py tests/test_ch11_active.py tests/test_prompt_skills.py -q` 全绿。

## T24: 执行路径单测

**文件：** `tests/test_ch11_executor.py`、`test_ch11_load_skill.py`、`test_ch11_script_tool.py`
**依赖：** T10-T13
**完成标准：**
- [ ] executor：inline 激活+注入 / fork 隔离+final_text 回流+token 写回 / fork_context 三策略（none/recent/full）/ mock provider 事件流 / 出错兜底 final_text
- [ ] load_skill：激活确认 / unknown skill / 构造缺依赖防御（catalog/store 必传）/ 嵌套触发（skill A SOP 调 load_skill 激活 B）/ is_system 与 read_only
- [ ] script_tool：子进程执行 / 超时路径 / 错误结果
- [ ] 每测试 docstring 标注防的 bug（用 `MagicMock`/`AsyncMock` 替代真实 Agent，`pytest.mark.asyncio`）

**验证：** `python -m pytest tests/test_ch11_executor.py tests/test_ch11_load_skill.py tests/test_ch11_script_tool.py -q` 全绿（mock provider，无真实 API/终端）。

## T25: 接线单测

**文件：** `tests/test_ch11_skill_command.py`、`test_ch11_register.py`、`tests/test_ch10_registry.py`（补 unregister 用例）
**依赖：** T17-T20
**完成标准：**
- [ ] skill_command：/skill 各子命令 handler 行为（RecordingUI 断言，list 排版对齐）
- [ ] register：动态注册 / [skill] 标注 / 冲突跳过 / functools.partial 闭包正确性 / `_REGISTERED_SKILL_NAMES` 清旧 / remove_skill_commands
- [ ] 既有 ch10 测试迁移：registry 加 unregister 用例、review 相关断言改为 review Skill
- [ ] 每测试 docstring 标注防的 bug

**验证：** `python -m pytest tests/test_ch11_skill_command.py tests/test_ch11_register.py tests/test_ch10_registry.py tests/test_ch10_commands.py -q` 全绿。

## T26: 集成测试 + 存量迁移 + ruff 清洁

**文件：** `tests/test_ch11_integration.py`、存量测试适配、全仓清洁
**依赖：** T21-T25
**完成标准：**
- [ ] `test_ch11_integration.py`（对应 T22 五步的自动化断言）：两阶段加载（启动 env 含摘要不含 body / load_skill 后 env 含完整 body）/ 意图触发（mock provider 断言 load_skill 被调）/ inline 工具集全量 / fork 工具集收窄 / /clear 清 activeSkills / 热重载后新 body 生效 / E2E 场景（commit inline / review fork / off 后重启仍禁用）
- [ ] 存量测试迁移：main.py 装配变化影响的用例适配；test_context_skill.py 改测 ActiveSkills
- [ ] `ruff format` + `ruff check --fix` + 确认 **docs/ 未被改动**（CLAUDE.md 文档保护）

**验证：** `python -m pytest tests/ -q` 全绿；`ruff format --check . && ruff check .` 清洁；`git status` 确认 docs/ 无改动。

## 执行顺序

```
T1 → T2 → T3 → T5 → T6 ──────┐
        │        └─────────► T10 ─┐
        ├→ T4 ──────► T12 → T13 ──┼─► T15 ─► T21 ─► T22
        └→ T7 → T8 ─────────────┘    │
T9（独立）──────────────► T11 ──┘     │
T14（独立，与 T12 并行）                │
                              T16（依赖 T7）──► 与 T21 汇合
T17（独立）→ T18（独立）→ T19 ──► T20 ──► T21
T2-T8 → T23；T10-T13 → T24；T17-T20 → T25；T21-T25 → T26
```

**可并行组：** [T3、T4、T7]（都依赖 T2）；[T9、T14、T17、T18]（互相独立）；[T12、T14]（都依赖 T2/T4/T7）。

**明确不做（用户已拍板）：** InstallSkill 远程安装维持「不做」，记入 spec「未来蓝图」，ch11 不实现。
