# MewCode ch14 - Git Worktree 文件系统隔离 Tasks

> 前置：spec.md（docs/ch14/spec.md）与 plan.md（docs/ch14/plan.md）均已批准。
> 每个任务自带测试（内联），2-5 分钟聚焦工作单元；每任务有明确验证方式。
> 测试命名按模块（test_worktree_*），略偏离 ch13 的 test_ch13_* 惯例（按模块更可发现）。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `mewcode/worktree/__init__.py` | 公开导出 Manager / validate_slug / 异常 |
| 新建 | `mewcode/worktree/types.py` | Worktree / ExitAction / ExitOptions / ExitReport / AutoCleanupReport / 异常 |
| 新建 | `mewcode/worktree/slug.py` | validate_slug / flat_slug / branch_name / is_auto_name / random_agent_name |
| 新建 | `tests/test_worktree_slug.py` | slug 校验单测 |
| 新建 | `mewcode/worktree/session.py` | WorktreeSession + JSON 原子持久化 |
| 新建 | `mewcode/worktree/git.py` | _run_git + _has_worktree_changes + _resolve_head_sha_from_fs + 高层封装 |
| 新建 | `tests/test_worktree_git.py` | git helper 单测 |
| 新建 | `mewcode/worktree/config.py` | WorktreesConfig / load_worktree_config 三层合并 |
| 新建 | `tests/test_worktree_config.py` | 配置缺省/覆盖/降级单测 |
| 新建 | `mewcode/worktree/manager.py` | Manager 构造 / 扫描 / list / get / current_session / run 周期 |
| 新建 | `tests/test_worktree_manager.py` | Manager 构造 + session 持久化测试 |
| 新建 | `mewcode/worktree/create.py` | create + 快速恢复 + 创建后设置 A/B/C/D |
| 新建 | `tests/test_worktree_create.py` | create + setup 单测 |
| 新建 | `mewcode/worktree/lifecycle.py` | enter / exit / remove / auto_cleanup / _has_worktree_changes |
| 新建 | `tests/test_worktree_lifecycle.py` | 生命周期单测 |
| 新建 | `mewcode/worktree/sweep.py` | sweep_stale 三层过滤 + EPHEMERAL_PATTERN |
| 新建 | `tests/test_worktree_sweep.py` | sweep_stale 单测 |
| 新建 | `mewcode/worktree/notice.py` | build_worktree_notice |
| 新建 | `mewcode/tools/cwd.py` | with_cwd / cwd_from_ctx / resolve_path |
| 新建 | `tests/test_tool_ctx.py` | resolve_path 单测 |
| 修改 | `mewcode/tools/file_ops.py` | _check_path 沙箱根跟随 ctx cwd（保留沙箱检查） |
| 修改 | `mewcode/tools/search.py` | cwd 缺省取 ctx cwd |
| 修改 | `mewcode/tools/shell.py` | cwd 缺省取 ctx cwd |
| 修改 | `mewcode/agent/agent.py` | FileTracker 用 resolve_path（:607） |
| 新建 | `tests/test_tools_cwd.py` | 6 工具 ctx cwd 解析单测 |
| 修改 | `mewcode/subagent/types.py` | AgentDefinition 加 isolation 字段 |
| 修改 | `mewcode/subagent/parser.py` | 解析 isolation frontmatter |
| 修改 | `tests/test_subagent_parser.py` | 增加 isolation 用例（扩展既有） |
| 新建 | `mewcode/tools/agent_worktree.py` | _execute_with_worktree（import build_worktree_notice） |
| 修改 | `mewcode/tools/agent_tool.py` | 加 worktree_mgr 字段 + isolation 分支（强制前台） |
| 新建 | `tests/test_agent_worktree.py` | _execute_with_worktree 单测（注入 stub sub_agent） |
| 修改 | `tests/test_agent_tool.py` | 加 isolation 分支用例（扩展既有） |
| 修改 | `mewcode/slash/ui.py` | 加 WorktreeAccessor 协议 + WorktreeSummary |
| 新建 | `mewcode/slash/commands/worktree.py` | /worktree handler + 子命令解析 |
| 修改 | `mewcode/slash/commands/__init__.py` | 注册 worktree 模块 |
| 新建 | `tests/test_worktree_command.py` | /worktree 命令 handler 单测（stub UI/accessor） |
| 新建 | `mewcode/tui/worktree_adapter.py` | 实现 WorktreeAccessor（包装 Manager + set_active_cwd） |
| 修改 | `mewcode/tui/app.py` | REPL.worktree_mgr / active_cwd / run 注入 / get_cwd / worktree_accessor |
| 新建 | `tests/test_worktree_tui.py` | TUI active_cwd 注入单测 |
| 修改 | `mewcode/main.py` | --resume / wm 装配 / sweep 任务 / finally 清理 |
| 修改 | `.gitignore` | 本开发仓库手动加 .mewcode/worktrees/ + worktree_session.json |

## 执行顺序

```
T1 slug ────────────────┐
T3 git ─────────────────┤
T8 tool/ctx（并行）───────┤
                          ↓
T2 session ──→ T4 manager ──→ T5 create ──→ T6 lifecycle ──→ T7 sweep
                          ↓
                    T9 配置（并行）
T8 → T10 改造 6 工具
T11 subagent.isolation ──→ T12 agent_worktree ──→ T13 AgentTool 接入
T14 /worktree 命令 ──→ T15 TUI 接入 ──→ T16 main.py 装配 ──→ T17 全量验证 ──→ T18 tmux 端到端
```

## T1: worktree 包基础：slug + 类型

**文件：** `mewcode/worktree/types.py` + `mewcode/worktree/slug.py` + `tests/test_worktree_slug.py`
**依赖：** 无
**步骤：**
1. 创建子包 `mewcode/worktree/`，加 `__init__.py`（暂空导出，后续补）
2. `types.py`：`Worktree` dataclass（name/path/branch/based_on/head_commit/created/manual）、`ExitAction(str, Enum)` KEEP/REMOVE、`ExitOptions(discard_changes)`、`ExitReport(removed/path/branch)`、`AutoCleanupReport(kept/path/branch)`、异常 `WorktreeError` → `WorktreeExistsError`/`WorktreeNotFoundError`/`WorktreeHasChangesError`/`WorktreeGitError`
3. `slug.py::validate_slug(name)`：非空、总长 ≤64、按 `/` 切段每段匹配 `^[a-zA-Z0-9._-]+$` 且非 `.`/`..`、无连续 `//`、无首末 `/`；失败抛 `ValueError(<具体原因>)`
4. `slug.py::flat_slug(name)`：`name.replace("/", "+")`；`branch_name(name)`：`"worktree-" + flat_slug(name)`
5. `slug.py::is_auto_name(name)`：`^agent-a[0-9a-f]{7}$` 或 `startswith("wf-")`（已裁决兼容 wf-）
6. `slug.py::random_agent_name()`：`"agent-a" + secrets.token_hex(4)[:7]`（临时 worktree 名唯一入口）
7. 测试：合法 `alice`/`team/alice`/`v1.0`/`a_b`；非法 空/超长/`..`/`./x`/`a//b`/`/x`/`a/`/`a b`/`a;b` 断言 `pytest.raises(ValueError)`；`branch_name("team/a")=="worktree-team+a"`；`is_auto_name("agent-a1b2c3d")` True
**验证：** `pytest tests/test_worktree_slug.py -v`

## T2: WorktreeSession 持久化

**文件：** `mewcode/worktree/session.py`
**依赖：** T1
**步骤：**
1. `WorktreeSession` dataclass（original_cwd/worktree_path/worktree_name/original_branch/original_head_commit/session_id/hook_based），JSON 用 `dataclasses.asdict + json.dumps`，含 `to_json()`/`from_json()`
2. `load_session(path) -> WorktreeSession | None`：缺失/空/null → None；非法 JSON → stderr 警告清空 → None（N5）
3. `save_session(path, session | None)`：None 写 `"null"`；否则 to_json；先写 `<path>.tmp` 再 `os.replace`
4. `clear_session(path)`（= save_session(path, None)）
**验证：** round-trip 一致；写 null 后 load None；非法内容 load None 且清空

## T3: git helper

**文件：** `mewcode/worktree/git.py` + `tests/test_worktree_git.py`
**依赖：** 无
**步骤：**
1. `async _run_git(work_dir, *args) -> str`：`asyncio.create_subprocess_exec("git", *args, cwd=work_dir, env=..., stdin=DEVNULL, stdout=PIPE, stderr=PIPE)`，env 注入 `GIT_TERMINAL_PROMPT=0`+`GIT_ASKPASS=""`（os.environ 副本），`await communicate()`，返回 stdout decode 后 rstrip 换行；失败抛 `WorktreeGitError`（命令+stderr）
2. 高层封装：`rev_parse_show_toplevel` / `worktree_add(-B,path,base)` / `worktree_remove_force` / `branch_delete(-D)` / `status_porcelain` / `rev_list_count(base..HEAD)` / `rev_list_unpushed(HEAD --not --remotes)` / `ls_files_ignored_others` / `config_get` / `config_set_hooks_path` / `current_branch` / `current_head`
3. `async _has_worktree_changes(wt_path, base_commit) -> bool`：① `status --porcelain` 非空 ② `rev-list --count <base>..HEAD` >0；git 命令出错 fail-closed 返回 True
4. `_resolve_head_sha_from_fs(wt_path) -> str | None`：读 `wt_path/.git` 取 `gitdir: <path>`，读 `<gitdir>/HEAD`，若 `ref: refs/heads/<name>` 读 `<gitdir>/<refpath>` 拿 SHA；失败返回 None（零 git 子进程）
5. 测试：pytest fixture + `subprocess.run` 造临时 git 仓库 + 真实 worktree，断言各函数行为
**验证：** `pytest tests/test_worktree_git.py -v`

## T4: Manager 构造

**文件：** `mewcode/worktree/manager.py` + `tests/test_worktree_manager.py`
**依赖：** T2、T3
**步骤：**
1. `Manager` 类 + `DEFAULT_SYMLINK_DIRS = ["node_modules", ".venv", "vendor"]`
2. `__init__(repo_root, cfg)`：`self.repo_root = resolve()`；**同步** `subprocess.run(["git","-C",repo_root,"rev-parse","--show-toplevel"])` 校验（不匹配抛 `WorktreeError`）；初始化 worktree_dir/session_file/active/lock；mkdir worktree_dir；`load_session` 还原 current_session（worktree_path 不存在 → 清空+stderr 警告）；`_scan_active()` 扫描子目录还原 active（fs 读）；`check_gitignore()`（根 .gitignore 缺 `.mewcode/worktrees/` 与 `.mewcode/worktree_session.json` → stderr 警告不改，F1.4）
3. `list()`（按 name 排序）/ `get(name)` / `current_session()`
4. `async run()`：`while cfg.background_cleanup: await sweep_stale(now - expire); await sleep(interval*60)`（首轮即清理，F6.5）
5. 测试：临时 git 仓库构造成功；非 git 目录抛 WorktreeError；空 session current_session()=None；预放 session 文件能加载；目录被删后清空+警告；check_gitignore 缺失时只警告
**验证：** `pytest tests/test_worktree_manager.py -v`

## T5: create + 快速恢复 + 创建后设置

**文件：** `mewcode/worktree/create.py` + `tests/test_worktree_create.py`
**依赖：** T4
**步骤：**
1. `async create(name, base_ref="HEAD", manual=False) -> Worktree`：
   - `validate_slug` 失败抛 ValueError；`async with self.lock:` 查 `active[name]` 撞名抛 `WorktreeExistsError`
   - 算 flat/wt_path/branch；`wt_path.exists()` → `_resolve_head_sha_from_fs` 还原 → 入 active 返回（快速恢复，跳过 setup，零 git）
   - 否则 `_run_git(repo, "worktree", "add", "-B", branch, str(wt_path), base_ref)`（-B 重置）；失败 `shutil.rmtree(wt_path, ignore_errors=True)` + 重抛
   - `await _perform_post_creation_setup(repo_root, wt_path, symlink_dirs)`（子步骤异常仅 stderr 警告）
   - `head_sha = await _run_git(wt_path, "rev-parse", "HEAD")`；构造 Worktree 入 active 返回
2. `_perform_post_creation_setup` 四子函数（各 try/except 仅警告）：
   - A `copy_local_configs`：复制 `.mewcode/config.local.yaml`、`config.yaml`、`permissions*.yaml`、`agents/`、`skills/`（跳过 worktrees/sessions/memory/monitor；源缺失/目标存在跳过）
   - B `setup_git_hooks`：优先 `.husky/`，回退 `git -C <repo> config --get core.hooksPath`；有则 `git -C <wt> config core.hooksPath <绝对路径>`
   - C `symlink_large_dirs`：对每个 symlink_dirs 目录主存在且 wt 缺 → `os.symlink`（Windows 失败跳过）
   - D `copy_included_ignored`：读根 `.worktreeinclude` 模式 → `ls_files_ignored_others` 列出忽略文件 → `fnmatch.fnmatch` 命中 → `shutil.copy` 到 wt
3. 测试：create 后目录/分支落地；A 复制 config.local.yaml；B 配 hooks；C 软链 node_modules（is_symlink）；D 按 .worktreeinclude 复制 .env；快速恢复 monkeypatch `_run_git` 断言未调用
**验证：** `pytest tests/test_worktree_create.py -v`

## T6: enter / exit / remove / auto_cleanup

**文件：** `mewcode/worktree/lifecycle.py` + `tests/test_worktree_lifecycle.py`
**依赖：** T5
**步骤：**
1. `ExitAction(str, Enum)` / `ExitOptions` / `ExitReport` / `AutoCleanupReport` / `WorktreeHasChangesError`（types.py）
2. `async enter(name) -> WorktreeSession`：锁内取 active（缺抛 `WorktreeNotFoundError`）；`original_cwd = str(Path.cwd())`；`original_branch/head` 用 `_run_git` 取（try/except 空串兜底）；`session_id = secrets.token_hex(8)`；写 current_session + save_session；**不 os.chdir**
3. `async exit(name, action, opts) -> ExitReport`：锁内校验 current_session 且 `worktree_name == name`（不符抛）；取 active；`action=REMOVE` 且未 discard → `_has_worktree_changes` True 抛 `WorktreeHasChangesError`；`os.chdir(original_cwd)` 用 `contextlib.suppress(OSError)` 兜底；current_session=None + save null；REMOVE → `_run_git(repo,"worktree","remove","--force",path)` + `await asyncio.sleep(0.1)` + `_run_git(repo,"branch","-D",branch)` + del active
4. `async remove(name, opts)`：独立入口，可删非当前；保护同 exit
5. `async auto_cleanup(name) -> AutoCleanupReport`：`manual=True` → kept；`_has_worktree_changes` False → `remove(discard_changes=True)` kept=False；有变更 → kept=True 带 path/branch
6. 测试：enter 不改进程 cwd、exit 切回、exit remove 变更保护、auto_cleanup 三分支
**验证：** `pytest tests/test_worktree_lifecycle.py -v`

## T7: sweep_stale

**文件：** `mewcode/worktree/sweep.py` + `tests/test_worktree_sweep.py`
**依赖：** T6
**步骤：**
1. `EPHEMERAL_PATTERN = re.compile(r"^agent-a[0-9a-f]{7}$")`（`random_agent_name` 在 slug.py，见 T1）
2. `async sweep_stale(cutoff) -> list[str]`：遍历 worktree_dir——
   - ① 不匹配 `EPHEMERAL_PATTERN` 且非 `wf-` 前缀 → 跳过
   - ② `mtime > cutoff` 跳过；`current_session.worktree_path == str(p)` 跳过
   - ③ `_has_worktree_changes(p, base)` 为 True 跳过（fail-closed；base 取 active 记录的 head_commit，孤儿回落 status 检查）；额外 `rev-list --max-count=1 HEAD --not --remotes` 非空跳过（未推送 commit 保留）
   - 通过者 `remove(name, ExitOptions(discard_changes=True))`，记 removed
3. 测试：构造三个目录（匹配+干净+过期 / 匹配+有变更 / 不匹配）→ 仅第一个被删；current_session 路径跳过
**验证：** `pytest tests/test_worktree_sweep.py -v`

## T8: worktrees 配置

**文件：** `mewcode/worktree/config.py` + `tests/test_worktree_config.py`
**依赖：** 无
**步骤：**
1. `WorktreesConfig` dataclass（enable/auto_cleanup/background_cleanup/cleanup_interval_minutes/expire_minutes）
2. `load_worktree_config(project_root)`：三层 `~/.mewcode/config.yaml` → `<project>/.mewcode/config.yaml` → `<project>/.mewcode/config.local.yaml` 追加合并；非法值 warning 用缺省（镜像 `subagent/config.py`）
3. 测试：无配置全缺省；local 覆盖 project；非法值降级警告
**验证：** `pytest tests/test_worktree_config.py -v`

## T9: tool ctx

**文件：** `mewcode/tools/cwd.py` + `tests/test_tool_ctx.py`
**依赖：** 无（与 T1-T8 并行）
**步骤：**
1. `_ctx_cwd: ContextVar[str | None] = ContextVar("cwd", default=None)`
2. `with_cwd(directory)`：`directory==""` 直接 yield；否则 set token + finally reset
3. `cwd_from_ctx() -> str | None`；`resolve_path(p) -> str`：空→base；绝对原样；相对 = `Path(base or Path.cwd()) / p`
4. 测试：三种 path、ctx 无 cwd 回落进程 cwd、空字符串返回 cwd 本身
**验证：** `pytest tests/test_tool_ctx.py -v`

## T10: 改造 6 个核心工具

**文件：** `mewcode/tools/{file_ops,search,shell}.py`、`mewcode/agent/agent.py`、`tests/test_tools_cwd.py`
**依赖：** T9
**步骤：**
1. `file_ops._check_path`：沙箱根 `os.getcwd()` → `cwd_from_ctx() or os.getcwd()`（**保留沙箱检查**，沙箱根跟随 ctx cwd）
2. `search.py`：`cwd = arguments.get("cwd") or cwd_from_ctx() or os.getcwd()`（list_files/search_code 两处）
3. `shell.py`：`cwd = arguments.get("cwd") or cwd_from_ctx() or os.getcwd()`
4. `agent.py:607`：FileTracker 的 `os.path.abspath(...)` → `resolve_path(...)`
5. **不改 schema、不改 description**（N1）
6. 测试：`with_cwd(tmp)` 下 read_file/write_file/edit_file/list_files/search_code/bash 以 tmp 为基准（bash 断言子进程 cwd 参数）
**验证：** `pytest tests/test_tools_cwd.py -v` + 全量存量测试通过

## T11: subagent isolation 字段

**文件：** `mewcode/subagent/types.py`、`mewcode/subagent/parser.py`、`tests/test_subagent_parser.py`
**依赖：** 无
**步骤：**
1. `AgentDefinition` 加 `isolation: str = ""`
2. parser 读 `meta.get("isolation")`，合法 `""`/`"worktree"`，非法 stderr 警告回落 `""`
3. 扩展 `test_subagent_parser.py`：`isolation: worktree` 解析成功；`isolation: gibberish` 警告回落空（`capsys` 断言 stderr）
**验证：** `pytest tests/test_subagent_parser.py -v`

## T12: worktree notice + agent_worktree

**文件：** `mewcode/worktree/notice.py` + `mewcode/tools/agent_worktree.py` + `tests/test_agent_worktree.py`
**依赖：** T6、T9、T11
**步骤：**
1. `worktree/notice.py`：`build_worktree_notice(parent_cwd, wt_path) -> str`（spec F8.3 模板，唯一实现处）
2. `agent_worktree.py` 顶部 `from mewcode.worktree import Manager, random_agent_name` 与 `from mewcode.worktree.notice import build_worktree_notice`（worktree 包不依赖 agent，无循环）
3. `async _execute_with_worktree(manager, definition, sub_agent, sub_conv, prompt, model_override="") -> LaunchResult`（**sub_agent/sub_conv 由调用方注入**，可测）：
   - `name = random_agent_name()`；`wt = await manager.create(name, "HEAD", manual=False)`
   - `task = build_worktree_notice(str(Path.cwd()), wt.path) + "\n\n" + prompt`
   - `with with_cwd(wt.path):` `try: text = await sub_agent.run_to_completion(task)`；`except MaxTurnsReached as e: text=e.final_text, err=...`；`finally: report = await manager.auto_cleanup(name)`
   - `report.kept` → `text += f"\n[Worktree 保留在 {report.path},分支 {report.branch}]"`；返回 LaunchResult
4. 测试：真实临时 git 仓库构造 Manager；sub_agent 用 mock（run_to_completion 内断言 `cwd_from_ctx()` 为 wt.path）；断言 auto_cleanup 被调用、保留通知追加
**验证：** `pytest tests/test_agent_worktree.py -v`

## T13: AgentTool 接入 isolation 分支

**文件：** `mewcode/tools/agent_tool.py`、`tests/test_agent_tool.py`
**依赖：** T12
**步骤：**
1. `AgentTool.__init__` 末尾追加 `worktree_mgr=None`、`worktrees_cfg=None`
2. `execute` 中 `subagent_type` 有值时先 `role = self._catalog.resolve(subagent_type)`；生效隔离 = **动态 `isolation` 参数**（worktree/none，优先）或角色 frontmatter `role.isolation`；生效为 worktree 且 wm 可用 → `sub, _ = launcher.make_sub_agent(role, is_background=False, model_override=model)` 后调 `agent_worktree._execute_with_worktree(...)`；动态请求但不可用 → 结构化错误；角色静态声明但不可用 → 降级 `launch_defined`（F11.2）；否则原 `launch_defined`；Fork 不变（F8.5）
3. schema 增 `isolation` 参数（enum worktree/none，F8.6 动态通道）；description 提示动态隔离用法
3. `worktree_mgr is None` 或 `enable=False` → 回落到原 `launch_defined`（降级，AC23）
4. isolation 时**忽略 run_in_background 强制前台**（F8.4）
5. 扩展 `test_agent_tool.py`：isolation 分支走 worktree 路径；worktree_mgr=None 时回落
**验证：** `pytest tests/test_agent_tool.py tests/test_agent_worktree.py -v`

## T14: /worktree 命令 + WorktreeAccessor

**文件：** `mewcode/slash/ui.py`、`mewcode/slash/commands/worktree.py`、`mewcode/slash/commands/__init__.py`、`tests/test_worktree_command.py`
**依赖：** T4
**步骤：**
1. `slash/ui.py`：UI 协议加 `worktree_accessor() -> WorktreeAccessor | None`；`WorktreeSummary` dataclass + `WorktreeAccessor` Protocol（**不导入 worktree 包**）
2. `worktree.py`：`build() -> [CommandDef(name="worktree", kind=LOCAL, usage=..., handler=_handler)]`（镜像 tasks.py，**handler(ctx, args) 已透传 args**，无需新 args_handler 机制）；handler split 派发 create/list/enter/exit/remove：
   - `create <slug>` → accessor.create(slug)，输出「Worktree 已创建: <path> (分支 <branch>)」
   - `list` → 遍历，格式 `<name>  <path>  <branch>  [active?]`
   - `enter <slug>` → accessor.enter(slug)，输出「已进入 <slug>」
   - `exit [--remove] [--discard]` → accessor.exit(action, discard)
   - `remove <slug> [--discard]` → accessor.remove(slug, discard)
   - accessor 为 None 显示「Worktree 功能未启用」；未知子命令报错
3. `__init__.py`：import + COMMAND_MODULES 加 worktree
4. 测试：stub UI + stub Accessor 驱动 handler 各子命令
**验证：** `pytest tests/test_worktree_command.py -v`

## T15: TUI 适配 + 注入 ctx

**文件：** `mewcode/tui/worktree_adapter.py`、`mewcode/tui/app.py`、`tests/test_worktree_tui.py`
**依赖：** T12、T14
**步骤：**
1. `worktree_adapter.py`：`WorktreeAdapter(WorktreeAccessor)`——包装 `worktree.Manager` + `set_active_cwd: Callable[[str], None]`；enter 内部调 `Manager.enter` 后 `set_active_cwd(session.worktree_path)`；exit 后 set_active_cwd("")
2. `app.py`：REPL 加 `worktree_mgr`、`active_cwd: str = ""`（空=进程 cwd）；`_effective_cwd()` 返回 `active_cwd or str(Path.cwd())`；主 run 路径（app.py:727）用 `with_cwd(_effective_cwd())` 包住 `agent.run`；`UIController.get_cwd()` 返回 `_effective_cwd()`；实现 `worktree_accessor()`
3. 构造时 `Manager.current_session()` 非 None → `active_cwd = session.worktree_path`
4. 测试：mock 断言 REPL.run 在 with_cwd 内；enter 后 active_cwd 更新；get_cwd 返回 effective
**验证：** `pytest tests/test_worktree_tui.py -v`；`python -m mewcode` 可启动

## T16: main.py 装配 + --resume

**文件：** `mewcode/main.py`
**依赖：** T8、T13、T14、T15
**步骤：**
1. argparse 增 `--resume`
2. `worktrees_cfg = load_worktree_config(cwd)`；`try: wm = WorktreeManager(cwd, worktrees_cfg) except WorktreeError: wm = None`（降级「Worktree 功能未启用」，stderr）
3. `AgentTool(..., worktree_mgr=wm, worktrees_cfg=worktrees_cfg)`（main.py:240）
4. REPL 构造传 `worktree_mgr=wm`
5. `--resume` 且 `wm.current_session()` → `active_cwd = session.worktree_path`（F10.3）
6. `if wm and wm.cfg.background_cleanup: sweep_task = asyncio.create_task(wm.run())`；`finally:` 取消（镜像 task_manager）
7. 测试：非 git 目录启动不崩（降级警告）；`--resume` 路径
**验证：** `python -m mewcode --version` 正常；全量测试通过

## T17: 本仓库 .gitignore + 全量验证

**文件：** `.gitignore`（手动）
**依赖：** T16
**步骤：**
1. 本开发仓库 `.gitignore` 手动追加（与 F1.4 运行时「只警告不修改」不冲突——那是工具对用户项目的行为）：
   ```
   # ch14: Worktree 隔离副本（运行时数据，不入库）
   .mewcode/worktrees/
   .mewcode/worktree_session.json
   ```
2. `pytest -q` 全量通过（ch13 行为零回归，AC25）
3. `ruff check mewcode tests` 通过；`python -m mewcode --version` 可启动
4. 确认 docs/ 未被改动（N14）
**验证：** 全部通过

## T18: 端到端 tmux 验证

**文件：** 无代码修改，运行验证
**依赖：** T17
**步骤：**
1. 准备临时 git 仓库 + 项目级自定义角色 `.mewcode/agents/worktree-writer.md`（`isolation: worktree`）
2. tmux 启动 `python -m mewcode`，跑 checklist 端到端场景（见 checklist.md）
3. 验证主目录文件未变、worktree 副本已变、保留/清理符合预期
**验证：** 见 checklist.md 场景 1-7
