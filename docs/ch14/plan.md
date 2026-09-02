# MewCode ch14 - Git Worktree 文件系统隔离 Plan

## 架构概览

五个模块，职责边界清晰：

1. **`mewcode/worktree/` 新包**（叶子包，不依赖 agent/工具，无循环）——Worktree 管理核心，按功能拆文件：
   - `slug.py`：slug 安全校验（F1.1）
   - `types.py`：Worktree / WorktreeSession / 枚举 / 选项 / 报告 / 异常（F2.1/F2.2）
   - `git.py`：git 子进程封装（`asyncio.create_subprocess_exec`）+ 快速恢复 fs 读（唯一 git 出入口）
   - `create.py`：create + 快速恢复 + 创建后设置 A/B/C/D（F3/F4）
   - `lifecycle.py`：enter / exit / remove / auto_cleanup（F5/F6.1-F6.3）
   - `sweep.py`：sweep_stale 三层清理（F6.4/F6.5）
   - `session.py`：WorktreeSession JSON 序列化 + 文件原子读写（F10）
   - `manager.py`：Manager 聚合（构造/扫描/访问器/run 周期循环）
   - `config.py`：`worktrees:` 配置段三层合并（F11.1，镜像 `subagent/config.py`）
   - `notice.py`：`build_worktree_notice`（F8.3）
2. **`mewcode/tools/cwd.py`（新建）**——explicit cwd 传递通道：`ContextVar` + `with_cwd` / `cwd_from_ctx` / `resolve_path`（F7.1，**机制新建**）
3. **工具改造**——`file_ops.py` / `search.py` / `shell.py` 用 ctx cwd 解析相对路径，**沙箱根跟随 ctx cwd**（F7.2）；`agent.py:607` FileTracker 一并修正
4. **SubAgent 集成**——`subagent/types.py` + `parser.py` 加 `isolation` 字段；`tools/agent_tool.py` 加 worktree 分支，逻辑放 `tools/agent_worktree.py`（F8）
5. **装配与命令**——`main.py` 装配 + `--resume`；`slash/commands/worktree.py` 五子命令（经 `WorktreeAccessor` 协议访问，不反向依赖 worktree 包）；TUI `active_cwd` 注入（F9/F10）

## 核心数据结构

### `Worktree`（worktree/types.py）
```python
@dataclass
class Worktree:
    name: str            # 原始 slug（可含 /）
    path: str            # 绝对路径 <repo_root>/.mewcode/worktrees/<flat_slug>
    branch: str          # worktree-<flat_slug>
    based_on: str        # 创建时 base 引用（"HEAD" 或 SHA）
    head_commit: str     # 创建时 commit SHA
    created: datetime
    manual: bool         # True=手动创建（auto_cleanup 跳过）
```

### `WorktreeSession`（worktree/session.py，含序列化）
```python
@dataclass
class WorktreeSession:
    original_cwd: str
    worktree_path: str
    worktree_name: str
    original_branch: str
    original_head_commit: str
    session_id: str          # uuid4 hex
    hook_based: bool = False

    def to_json(self) -> str: ...            # json.dumps(asdict(self))
    @classmethod
    def from_json(cls, raw: str) -> "WorktreeSession": ...
```

### 枚举 / 选项 / 报告 / 异常（worktree/types.py）
```python
class ExitAction(str, Enum): KEEP = "keep"; REMOVE = "remove"
@dataclass
class ExitOptions: discard_changes: bool = False
@dataclass
class ExitReport: removed: bool; path: str; branch: str
@dataclass
class AutoCleanupReport: kept: bool; path: str = ""; branch: str = ""
class WorktreeError(Exception): ...
class WorktreeExistsError(WorktreeError): ...      # create 撞名
class WorktreeNotFoundError(WorktreeError): ...
class WorktreeHasChangesError(WorktreeError): ...  # exit/remove 变更保护（F5.2）
class WorktreeGitError(WorktreeError): ...         # git 失败，携带命令+stderr（N10）
```

### `WorktreesConfig`（worktree/config.py）
```python
@dataclass
class WorktreesConfig:
    enable: bool = True
    auto_cleanup: bool = True
    background_cleanup: bool = True
    cleanup_interval_minutes: float = 60.0
    expire_minutes: float = 180.0
# load_worktree_config(project_root) -> WorktreesConfig
# 三层合并 local > project > user，非法值 warning 用缺省（镜像 subagent/config.py）
```

### ctx 机制（tools/cwd.py）
```python
_ctx_cwd: ContextVar[str | None] = ContextVar("cwd", default=None)
@contextmanager
def with_cwd(directory: str): ...       # 设置/恢复 token；空目录直接 yield
def cwd_from_ctx() -> str | None: ...   # None = 未隔离，用进程 cwd
def resolve_path(p: str) -> str:        # 绝对原样；空→base；相对 = (ctx cwd 或 Path.cwd()) join
```

### Manager（worktree/manager.py）
```python
class Manager:
    def __init__(self, repo_root: str, cfg: WorktreesConfig) -> None: ...
    # worktree_dir: <repo_root>/.mewcode/worktrees
    # session_file: <repo_root>/.mewcode/worktree_session.json
    # symlink_dirs: 默认 ["node_modules", ".venv", "vendor"]
    # lock: asyncio.Lock
    # active: dict[str, Worktree]
    # current_session: WorktreeSession | None
    async def create(self, name, base_ref="HEAD", manual=False) -> Worktree: ...
    async def enter(self, name) -> WorktreeSession: ...
    async def exit(self, name, action: ExitAction, opts: ExitOptions) -> ExitReport: ...
    async def remove(self, name, opts: ExitOptions) -> ExitReport: ...
    async def auto_cleanup(self, name) -> AutoCleanupReport: ...
    async def sweep_stale(self, cutoff: datetime) -> list[str]: ...
    async def run(self): ...            # 周期 sweep 循环（F6.5）
    def list(self) -> list[Worktree]: ...
    def get(self, name) -> Worktree | None: ...
    def current_session(self) -> WorktreeSession | None: ...
```

## 模块设计

### worktree/slug.py —— 安全校验与命名（F1）
```python
_SLUG_SEG_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
def validate_slug(name) -> None:  # 空/总长>64/首末 / /含 // /任一段不匹配/段为 . 或 .. → ValueError(原因)
def flat_slug(name) -> str:       # "/" -> "+"
def branch_name(name) -> str:     # "worktree-" + flat_slug(name)
def is_auto_name(name) -> bool:   # ^agent-a[0-9a-f]{7}$ 或 startswith("wf-")（F1.3/F6.4）
def random_agent_name() -> str:   # "agent-a" + secrets.token_hex(4)[:7]（临时 worktree 名）
```

### worktree/git.py —— git 子进程 + 快速恢复读
- `_run_git(work_dir, *args, check=True) -> str`：`asyncio.create_subprocess_exec`（非阻塞事件循环），统一 `GIT_TERMINAL_PROMPT=0`、`GIT_ASKPASS=""`、`stdin=DEVNULL`、`timeout=30`；`check=True` 失败抛 `WorktreeGitError`（命令+stderr）
- 高层封装：`rev_parse_show_toplevel`、`worktree_add(-B, path, base)`、`worktree_remove --force`、`branch_delete(-D)`、`status_porcelain`、`rev_list_count(base..HEAD)`、`rev_list_unpushed(HEAD --not --remotes)`、`ls_files_ignored_others`、`config_get/set hooksPath`、`current_branch`、`current_head`
- `_resolve_head_sha_from_fs(wt_path) -> str | None`：读 `.git` 指针 → `HEAD` → `refs/heads/<branch>`，**零 git 子进程**（F3.1.4 快速恢复；依据：大仓库 git fetch 6-8s vs fs read 3ms，且同一子 Agent 会反复进入同 worktree）

### worktree/create.py —— 创建（F3/F4）
- `Manager.create(name, base_ref="HEAD", manual=False)`：
  1. `validate_slug`；2. `async with self._lock:` 查 `active[name]` 撞名抛 `WorktreeExistsError`；3. 构建 flat_slug/path/branch；4. 快速恢复：path 存在 → `_resolve_head_sha_from_fs` 还原 → 入 active 返回；5. `_run_git(repo_root, "worktree", "add", "-B", branch, path, base_ref)`（`-B` 重置，孤儿分支不失败）；失败清残留 + 抛 `WorktreeGitError`；6. `_perform_post_creation_setup`（F4，异常只 stderr 警告）；7. 读 head_sha 装填；8. 入 active 返回
- `_perform_post_creation_setup(repo_root, wt_path, symlink_dirs)`（F4，best-effort）：
  - A 复制 `.mewcode/config.local.yaml`、`config.yaml`、`permissions*.yaml`、`agents/`、`skills/`（跳过 worktrees/、sessions/、memory/、monitor/；目标存在/源缺失跳过）
  - B 读主仓库 `core.hooksPath` 或检测 `.husky/` → `_run_git(wt_path, "config", "core.hooksPath", abs)`
  - C `symlink_dirs` 默认 `[node_modules, .venv, vendor]`，主存在且 wt 缺 → `os.symlink`（Linux/macOS；Windows 失败跳过）
  - D 读根 `.worktreeinclude`（每行 glob）→ `ls_files_ignored_others` 匹配 → 复制；文件缺失/无匹配跳过

### worktree/lifecycle.py —— 进入/退出/删除/自动清理（F5/F6.1-F6.3）
- `enter(name)`：锁内取 active（缺抛 `WorktreeNotFoundError`）；记当前 `Path.cwd()`/branch/HEAD 为原状态；构造 session；`session.py::save_session(None 或 session)` 原子写；**不 os.chdir**
- `exit(name, action, opts)`：锁内取 active + current_session（`current_session.worktree_name != name` 抛）；`action=REMOVE` 且未 discard → `_has_worktree_changes` True 抛 `WorktreeHasChangesError`；`os.chdir(original_cwd)` 兜底（N4）；`current_session=None` + save null；REMOVE → `_run_git(repo, "worktree", "remove", "--force", path)` + `await asyncio.sleep(0.1)`（git lockfile 竞态，100ms 经验值）+ `_run_git(repo, "branch", "-D", branch)` + `del active[name]`
- `remove(name, opts)`：独立入口，可删非当前；变更保护同 exit
- `auto_cleanup(name)`：`manual=True` → kept；`_has_worktree_changes` False → `remove(discard_changes=True)` kept=False；有变更 → kept=True 带 path/branch
- `_has_worktree_changes(wt_path, base_commit)`：`status --porcelain` 非空 → True；`rev_list_count(base..HEAD)` >0 → True；git 异常 → True（fail-closed）

### worktree/sweep.py —— 后台过期清理（F6.4）
- `sweep_stale(cutoff)` 三层过滤：① `is_auto_name`（agent-a[0-9a-f]{7} / wf-）② 目录 mtime > cutoff 跳过 + `current_session.worktree_path` 跳过 ③ `_has_worktree_changes` True 跳过 + `rev_list_unpushed` 非空跳过 → 通过者 `remove(discard_changes=True)`，记入 removed

### worktree/session.py —— 持久化（F10）
- `save_session(session_file, session | None)`：None → 写 `"null"`；否则 `session.to_json()`；先写 `<file>.tmp` 再 `os.replace`
- `load_session(session_file) -> WorktreeSession | None`：缺失/空/null → None；非法 JSON → stderr 警告清空返回 None（N5）

### worktree/manager.py —— 构造与聚合
- `__init__(repo_root, cfg)`：`_run_git(repo_root, "rev-parse", "--show-toplevel")` 校验（失败抛 `WorktreeError`，main.py 降级 None）；`mkdir worktree_dir`；`load_session` 还原 current_session（worktree_path 不存在 → 清空+警告）；`_scan_active()` 扫描子目录还原 active（fs 读）；`check_gitignore()`（F1.4：根 .gitignore 缺两行 → stderr 警告，**不改**）
- `run()`：`while cfg.background_cleanup: await sleep(interval*60); await sweep_stale(now - expire_minutes)`（F6.5）

### worktree/notice.py —— F8.3
`build_worktree_notice(parent_cwd, wt_path) -> str`：`<worktree-context>...</worktree-context>` 模板（父目录/工作目录/绝对路径翻译/编辑前重读）

### tools/cwd.py —— ctx 通道（F7.1）
如上「ctx 机制」。**新建**，不动 `Tool.execute(arguments)` 签名。

### 工具改造（F7.2）
- `file_ops.py`：`_check_path(path)` 沙箱根 `os.getcwd()` → `cwd_from_ctx() or os.getcwd()`（**保留沙箱检查**，沙箱根跟随 ctx cwd，N2/N7）
- `search.py`：`cwd = arguments.get("cwd") or cwd_from_ctx() or os.getcwd()`
- `shell.py`：`cwd = arguments.get("cwd") or cwd_from_ctx() or os.getcwd()`
- `agent.py:607`：FileTracker 的 `os.path.abspath(path)` → `resolve_path(path)`
- 各工具 schema **不变**（N1）

### SubAgent 集成（F8）
- `subagent/types.py`：`AgentDefinition` 加 `isolation: str = ""`
- `subagent/parser.py`：解析 `meta.get("isolation")`，合法 `""`/`"worktree"`，非法 stderr 警告回落到 `""`
- `tools/agent_tool.py`：
  - `AgentTool.__init__` 末尾追加 `worktree_mgr: WorktreeManager | None = None`、`worktrees_cfg: WorktreesConfig | None = None`
  - `execute`：`subagent_type` 有值时先 `role = self._catalog.resolve(subagent_type)`；`role.isolation == "worktree" and wm and wm.cfg.enable` → `agent_worktree._execute_with_worktree(...)`；否则原 `launch_defined`；Fork 路径不变（F8.5）
- `tools/agent_worktree.py`（新建）：
  - `_execute_with_worktree(launcher, wm, role, prompt, model_override, parent_cwd) -> LaunchResult`（F8.2/F8.4，**强制前台**）：
    1. `name = random_agent_name()`（agent-a<7hex>）
    2. `wt = await wm.create(name, "HEAD", manual=False)`
    3. `sub, _ = launcher.make_sub_agent(role, is_background=False, model_override=model_override)`
    4. `task = build_worktree_notice(parent_cwd, wt.path) + "\n\n" + prompt`
    5. `with with_cwd(wt.path):` `try: text = await sub.run_to_completion(task)`；`except MaxTurnsReached as e: text = e.final_text, err=...`；`finally: report = await wm.auto_cleanup(name)`；`report.kept` 时 `text += f"\n[Worktree 保留在 {report.path},分支 {report.branch}]"`
    6. 返回 `LaunchResult(status="completed", text=text)` / error
  - 忽略 `run_in_background`（isolation 强制前台，F8.4）
  - worktree 包不依赖 agent → 无导入循环

### slash/commands/worktree.py + WorktreeAccessor（F9）
- `slash/ui.py`：UI 协议加 `worktree_accessor() -> WorktreeAccessor | None`（协议定义在 slash 层，**不导入 worktree 包**，避免反向依赖）
  ```python
  @dataclass
  class WorktreeSummary: name; path; branch; active: bool; manual: bool
  class WorktreeAccessor(Protocol):
      async def create(self, name) -> tuple[str, str]: ...
      def list(self) -> list[WorktreeSummary]: ...
      async def enter(self, name) -> None: ...
      async def exit(self, action: str, discard: bool) -> bool: ...
      async def remove(self, name, discard: bool) -> None: ...
  ```
- `slash/commands/worktree.py`：`build() -> [CommandDef(name="worktree", kind=LOCAL, usage=..., handler=_handler)]`（镜像 `tasks.py`）；`_handler(ctx, args)` split 派发 create/list/enter/exit/remove；`accessor = ctx.ui.worktree_accessor()` 为 None 显示「Worktree 功能未启用」；`ctx.ui.show_message` 输出（N12 中文）
- `slash/commands/__init__.py`：import + COMMAND_MODULES 加 `worktree`

### tui（F9.3/F10.3/F7.3）
- `tui/app.py`：REPL 加 `worktree_mgr: Manager | None`、`active_cwd: str = ""`（空=进程 cwd）；`_effective_cwd()` 返回 `active_cwd or str(Path.cwd())`；主 run 路径（app.py:727）用 `with_cwd(_effective_cwd())` 包住 `agent.run(...)`；`UIController.get_cwd()` 返回 `_effective_cwd()`；实现 `worktree_accessor()` 返回适配器
- `tui/worktree_adapter.py`（新建）：实现 `WorktreeAccessor`——包装 `worktree.Manager` + 持有 REPL 引用（`enter` 成功时 `app.active_cwd = wt.path`）
- 启动时 `Manager.current_session()` 非 None → `active_cwd = session.worktree_path`（--resume/恢复）

### main.py 装配（F10.3/F6.5/N5/N11）
- argparse 增 `--resume`
- `worktrees_cfg = load_worktree_config(cwd)`；`try: wm = WorktreeManager(cwd, worktrees_cfg) except WorktreeError: wm = None`（降级「Worktree 功能未启用」）
- `AgentTool(subagent_catalog, launcher, get_main_agent, worktree_mgr=wm, worktrees_cfg=worktrees_cfg)`（main.py:240 处）
- REPL 构造传入 `worktree_mgr=wm`
- `--resume` 且 `wm.current_session()` → `active_cwd = session.worktree_path`（也可并入 Manager.__init__ 后由 App 读取）
- `if wm and wm.cfg.background_cleanup: sweep_task = asyncio.create_task(wm.run())`（F6.5）
- `finally:` 取消 sweep_task（镜像 task_manager 处理）

### 配置（F11）
- `worktree/config.py`：`WorktreesConfig` + `load_worktree_config`（三层 `~/.mewcode/config.yaml` → `<project>/.mewcode/config.yaml` → `<project>/.mewcode/config.local.yaml` 追加合并，镜像 `subagent/config.py`）

## 模块交互（数据流）

```
启动:  main.py
  └─ WorktreeManager(cwd, cfg) ── rev-parse 校验 / 扫描 worktree_dir / load_session / check_gitignore
  └─ create_task(wm.run())  ── 周期 sweep_stale（F6.5）
  └─ --resume → REPL.active_cwd = session.worktree_path

主 Agent 调 agent 工具 (isolation:worktree):
  AgentTool.execute → role.isolation=="worktree"?
    → agent_worktree._execute_with_worktree
        1. random_agent_name() → 2. wm.create(agent-a<7hex>, manual=False)
        3. launcher.make_sub_agent(role)     # 独立 conv/权限/工具集（复用 ch13）
        4. task = build_worktree_notice + prompt
        5. with with_cwd(wt.path): sub.run_to_completion   # ctx cwd 注入
             └─ 工具: file_ops/search/shell 经 resolve_path 取 ctx cwd → 落 worktree（沙箱根=ctx cwd）
        6. wm.auto_cleanup → kept? 追加保留通知 : 已删除
        7. 返回 final_text

用户 /worktree 命令（经 WorktreeAccessor 协议，不反向依赖 worktree 包）:
  create → accessor.create(slug)        → wm.create(manual=True)
  list   → accessor.list()              → wm.list()
  enter  → accessor.enter(slug)         → wm.enter + REPL.active_cwd = path
  exit   → accessor.exit(KEEP/REMOVE, discard) → wm.exit
  remove → accessor.remove(slug, discard)      → wm.remove

主 Agent 会话中 enter 后:
  REPL.run → with_cwd(REPL.active_cwd) 包住 agent.run → 主 Agent 工具落 worktree
```

## 文件组织

```
mewcode/worktree/            # 新建包（叶子，无循环依赖）
├── __init__.py              # 导出 Manager / validate_slug / 异常 / with_cwd?（cwd 在 tools）
├── slug.py                  # validate_slug / flat_slug / branch_name / is_auto_name / random_agent_name
├── types.py                 # Worktree / ExitAction / ExitOptions / ExitReport / AutoCleanupReport / 异常
├── git.py                   # _run_git + 高层封装 + _resolve_head_sha_from_fs
├── create.py                # create + 快速恢复 + _perform_post_creation_setup（A/B/C/D）
├── lifecycle.py             # enter / exit / remove / auto_cleanup / _has_worktree_changes
├── sweep.py                 # sweep_stale
├── session.py               # WorktreeSession（含 to_json/from_json）+ save/load（原子写）
├── manager.py               # Manager（构造/扫描/访问器/run 周期循环）
├── config.py                # WorktreesConfig / load_worktree_config
└── notice.py                # build_worktree_notice
mewcode/tools/
├── cwd.py                   # 新建：ContextVar / with_cwd / cwd_from_ctx / resolve_path
├── file_ops.py              # 改：_check_path 沙箱根用 ctx cwd
├── search.py                # 改：cwd 缺省取 ctx cwd
├── shell.py                 # 改：cwd 缺省取 ctx cwd
├── agent_tool.py            # 改：isolation 分支（调 agent_worktree）
└── agent_worktree.py        # 新建：_execute_with_worktree（import build_worktree_notice from worktree.notice）
mewcode/subagent/
├── types.py                 # 改：AgentDefinition.isolation
└── parser.py                # 改：解析 isolation
mewcode/agent/agent.py       # 改：FileTracker 用 resolve_path（:607）
mewcode/slash/ui.py          # 改：UI 协议加 worktree_accessor / WorktreeSummary / WorktreeAccessor
mewcode/slash/commands/worktree.py   # 新建：/worktree 五子命令
mewcode/slash/commands/__init__.py   # 改：注册 worktree 模块
mewcode/tui/app.py           # 改：REPL.worktree_mgr / active_cwd / run 注入 / get_cwd / worktree_accessor
mewcode/tui/worktree_adapter.py      # 新建：WorktreeAccessor 实现（包装 Manager + 设 REPL.active_cwd）
mewcode/main.py              # 改：--resume / wm 装配 / sweep 任务 / finally 清理
tests/test_worktree_*.py 等  # 新建：见「验证」
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| cwd 传递机制 | `contextvars.ContextVar` + `with_cwd`（**新建**） | 不动 `execute(arguments)` 签名、不改 schema → 工具列表稳定、prompt cache 不抖（N1/F7.4）；async 单循环天然透传。（注：参考稿称「已有 conv/subagent_depth 范式」与实际不符，机制为新建） |
| git 操作 | `asyncio.create_subprocess_exec`（`worktree/git.py`），不引 gitpython | 非阻塞事件循环；统一注入 env 与 stdin=DEVNULL；与项目 subprocess 风格一致 |
| worktree 目录 | `.mewcode/worktrees/<flat_slug>`（扁平化） | 嵌套 `/`→`+` 避免 Git 分支 D/F 冲突；目录不嵌套、扫描清理简单 |
| 并发 | 状态变更持 `asyncio.Lock`，git 调用不持锁 + 一次性 `sleep(0.1)` | 项目跑在 asyncio 单循环，异步友好；避免长锁阻塞；100ms 解 lockfile 竞态（经验值） |
| 隔离执行 | 强制前台，绕过 TaskManager，直接 `run_to_completion` | F8.4 本期最小实现；后台+隔离留后续章节 |
| 变更保护 | 先查后强删：`_has_worktree_changes` fail-closed → `worktree remove --force` + `branch -D` | git 原生保护被预查替代，杜绝「脏删」「丢未推送 commit」（N8/F5.4） |
| 快速恢复 | 纯 fs 读（`.git`/HEAD/refs），零 git 子进程 | 大仓库 git fetch 6-8s vs fs read 3ms；同一子 Agent 反复进同 worktree 是主场景（G5） |
| `-B` vs `-b` | `-B`（重置） | 上次残留的孤儿分支不会让 create 失败 |
| session 持久化 | 单文件 JSON 只存当前 session；注册表靠启动扫描重建；退出写 `null` | 贴合「同时只在一个 worktree」模型；免同步两份状态；N5 损坏不阻断 |
| .gitignore | 启动检查根 .gitignore 缺两行**只警告不修改** | F1.4 已裁决采纳 F35；尊重用户配置（参考稿「追加」作废） |
| 名字模式 | 生成 `agent-a<7hex>`（`secrets.token_hex(4)[:7]`）；sweep 认 `^agent-a[0-9a-f]{7}$` + `wf-` | 已裁决：兼容 Q2 与参考稿；标准库加密强随机 |
| 配置 | `.mewcode/config.yaml` 的 `worktrees:` 段 | 三层合并镜像 `agents:`；`enable=false` 降级（F11.2） |
| slash 访问 worktree | `WorktreeAccessor` 协议在 slash/ui.py + tui 适配器 | slash 层不反向依赖 worktree 包（避免技术债）；适配器持 REPL 引用设 active_cwd |
| TUI active_cwd | `str = ""`（空=进程 cwd） | 与既有 `self.cwd` 字符串字段并存，不引入新结构 |
| os.chdir 使用场景 | 仅 `Manager.exit` 兜底一次 | 其他全部 explicit cwd；避免进程级 cwd 成为同步点（N4） |
| 后台清理触发 | `wm.run()` 周期循环（interval），启动即 `create_task` | 满足 F6.5 周期性；不阻塞启动（镜像 session.clean_expired 的后台异步做法） |
| 工具沙箱 | `_check_path` 沙箱根跟随 ctx cwd | **保留沙箱检查**（参考稿只提 resolve 未提沙箱——安全回归不可丢）；worktree 在 project_root 内天然放行 |
| 主 Agent enter | REPL 主 run 路径 `with_cwd(active_cwd)` 包住 `agent.run` | 最小侵入；主 Agent 工具链复用同一 ctx 机制（F9.3/F7.3） |

## 验证方式

- **单元/接线测试（tests/test_worktree_*.py 等，按模块命名，与 task.md 一致）**，mock 驱动真实代码路径、不依赖真实终端/API key（N13）：
  - `test_worktree_slug.py`：AC1 各正反例 + ValueError 原因
  - `test_worktree_git.py`：git helper（_run_git / _has_worktree_changes / _resolve_head_sha_from_fs）
  - `test_worktree_manager.py`：AC2/AC3 create → 目录+分支；AC4 快速恢复（mock git 断言零 git 子进程）；AC20 session 持久化 + 目录被删清空；AC21 .gitignore 只警告；AC24 非 git 降级
  - `test_worktree_create.py`：AC5/AC6/AC7/AC8 四类初始化（临时仓库造 .mewcode/config.local.yaml / .husky / node_modules / .worktreeinclude）
  - `test_worktree_lifecycle.py`：AC9 enter 不 chdir；AC10/AC11 exit 变更保护与 discard；AC12 auto_cleanup manual/无变更
  - `test_worktree_sweep.py`：AC19 sweep_stale 三层
  - `test_worktree_config.py`：AC23 enable=false 降级
  - `test_tool_ctx.py` + `test_tools_cwd.py`：AC13/AC14 六个工具 ctx cwd 解析、execute_command 子进程 cwd 参数（mock scheduler 驱动真实工具 execute）
  - `test_subagent_parser.py`（扩展）：isolation 字段解析
  - `test_agent_worktree.py` + `test_agent_tool.py`：AC15/AC16 isolation 分支，断言 create→notice→with_cwd→auto_cleanup 顺序与保留通知追加
  - `test_worktree_command.py`：AC17/AC18 /worktree 命令 handler + WorktreeAccessor（mock UI/Manager）
  - `test_worktree_tui.py`：主 Agent enter 后工具落 worktree
- **AC25**：全量 `pytest` 通过（ch13 行为零回归）+ `ruff check` + `python -m mewcode` 可启动
- **端到端**：临时 git 仓库实测 create/enter/exit/remove、自动清理、sweep、`--resume`（对应 spec 场景 1-7）
