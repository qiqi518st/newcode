# NewCode ch14 - Git Worktree 文件系统隔离 Spec

## 背景

ch13 给 NewCode 装上了 SubAgent 系统：子 Agent 在隔离的上下文中执行，已实现**消息隔离**、**权限隔离**、**文件读缓存隔离**、**token 计数隔离**。但**文件系统仍然共享**——主 Agent 和后台子 Agent（以及下一章 Agent Team 队员）会在同一时刻并发读写同一份工作目录的文件，出现读到对方写了一半的文件、互相覆盖修改等并行冲突——本质就是经典的并行开发文件冲突，和两个程序员同时改同一份文件一样。

Git 分支只能做**时间维度**的隔离（切换分支时工作目录被覆盖，同一时刻只有一个工作目录），不能解决并行问题；切分支还会刷被切文件的 mtime，触发依赖追踪型构建工具的链式重编。

需要的是**空间维度**的隔离：同一仓库同时挂多个工作目录、共享版本库、各自一个分支——这就是 Git Worktree（Git 2.5+）的能力。本章在 newcode 中封装一层 Worktree 管理逻辑，把这块拼图补给 SubAgent，让后台 / 并行场景安全可用。

现有相关基础设施（已核实）：
- ch13 SubAgent frontmatter 解析（`newcode/subagent/parser.py`）——解析 name/description/tools/disallowedTools/model/permissionMode/maxTurns/background 等字段，本章新增 `isolation`
- ch13 `newcode/tools/agent_tool.py` 的 `AgentTool.execute` 已是子 Agent 启动入口，本章在此插桩 isolation 分支
- ch08 文件读缓存（FileTracker）以绝对路径为 key
- `newcode/slash/` 已有 slash 命令注册系统（CommandKind: LOCAL / UI / PROMPT）
- 工具接口 `execute(arguments)` 无显式 cwd；全库无 `os.chdir`、无 contextvars（ctx 机制需**新建**）；`execute_command`/`list_files`/`search_code` 已支持可选 `cwd` 参数可作为改造模板
- 进程 cwd 是唯一工作区概念（main.py 捕获一次注入各子系统）；context/session、memory 已按绝对路径 workspace 隔离

本章不引入 Worktree 间合并策略、跨目录代码同步、多 Agent 并行编排，这些属于上层 / 下一章范畴。

## 目标

- G1：提供 `WorktreeManager` 封装完整生命周期——创建、快速恢复、进入、退出、删除；并发场景用单一 `asyncio.Lock` 保护内部状态
- G2：slug 严格安全校验——每段 `^[a-zA-Z0-9._-]+$`、总长 ≤64、拒绝整段 `.`/`..`、允许 `/` 嵌套，防 LLM 输入触发路径遍历
- G3：目录统一落在仓库内 `.newcode/worktrees/<flat_slug>/`，分支名 `worktree-<flat_slug>`，嵌套 `/`→`+` 避免 Git D/F 冲突
- G4：创建后四类环境初始化——A 复制本地配置、B 配置子目录 git hooks、C 软链大目录、D 按 `.worktreeinclude` 补被忽略但运行需要的文件；均 best-effort，失败只警告不中断
- G5：快速恢复——目录已存在时仅读 `.git` 指针 + `HEAD` + `refs/` 文件系统还原 commit SHA，不调任何 git 子进程
- G6：进入 Worktree **不 chdir**——WorktreePath 记入 WorktreeSession 并经 ctx 传给工具调用；execute_command/read_file/write_file/edit_file/list_files/search_code 从 ctx 取 cwd；进程级 cwd 不变
- G7：以绝对路径为 key 的缓存（文件读缓存、系统提示词、项目指令、记忆）天然按目录隔离；进入/退出无需清缓存
- G8：退出变更保护——`action=remove` 且未显式 `discard_changes` 时，检测到未提交修改或本地多于 base 的 commit 一律拒绝删除
- G9：自动清理——子 Agent 退出时无变更则 remove；有变更则保留，并把路径/分支名追加到子 Agent 结果文本给主 Agent review
- G10：后台过期清理——按命名模式（`agent-a[0-9a-f]{7}` / `wf-` 前缀）识别临时 Worktree + 时间过滤 + fail-closed 变更检查（未推送 commit 也保留）
- G11：WorktreeSession 持久化到 `.newcode/worktree_session.json`，启动读取校验，退出覆写 `null` 不删文件
- G12：`subagent.Definition` 新增 `isolation` 字段（`""` / `"worktree"`）；启动器检测到后自动 `create → 注入 notice → set ctx cwd → run_to_completion → auto_cleanup`
- G13：`/worktree` slash 命令（create / list / enter / exit [--remove] [--discard] / remove [--discard]）；手动创建的 Worktree 不走自动清理
- G14：与既有章节协同——工具列表不变（ctx 注入不改 schema）、prompt cache 不抖动、存量测试不破坏

## 功能需求

### F1 Slug 校验与命名

- F1.1 `worktree.validate_slug(name)`：name 非空、总长 ≤64；按 `/` 切段，每段匹配 `^[a-zA-Z0-9._-]+$` 且不是 `.`/`..`；不允许连续 `//`、首末 `/`；失败抛 `ValueError` 带具体原因
- F1.2 目录统一放 `.newcode/worktrees/<flat_slug>/`（flat_slug = slug 的 `/`→`+`）；分支名 `worktree-<flat_slug>`，如 slug `team-refactor/alice` → 目录 `team-refactor+alice`、分支 `worktree-team-refactor+alice`
- F1.3 创建者标记：自动创建（子 Agent）目录名 `agent-a<7位随机hex>`（sweep 兼容 `wf-` 前缀）；手动 `/worktree create` 不得使用这些前缀（避免误入异常清理）
- F1.4 `.gitignore`：worktree 目录按设计被忽略——项目根 `.gitignore` 应含 `.newcode/worktrees/` 与 `.newcode/worktree_session.json`；newcode 启动时检查根 `.gitignore` 是否含这两行，缺失**只 stderr 警告不修改**（尊重用户配置）

### F2 WorktreeManager 与核心数据结构

- F2.1 `Worktree`（dataclass）：`name`（原始 slug）、`path`（绝对路径）、`branch`（`worktree-<flat_slug>`）、`based_on`（创建时 base 引用）、`head_commit`（创建时 SHA）、`created`（datetime）、`manual`（bool，影响 auto_cleanup 跳过）
- F2.2 `WorktreeSession`（dataclass）：`original_cwd`、`worktree_path`、`worktree_name`、`original_branch`、`original_head_commit`、`session_id`（UUID）、`hook_based`（预留）
- F2.3 `Manager`：`repo_root`、`worktree_dir`（`<repo_root>/.newcode/worktrees`）、`session_file`（`<repo_root>/.newcode/worktree_session.json`）、`lock: asyncio.Lock`、`active: dict[str, Worktree]`、`current_session: WorktreeSession | None`
- F2.4 `Manager(repo_root)` 构造：校验 repo_root 是 git 仓库根（`git rev-parse --show-toplevel`），失败抛异常、newcode 启动降级「Worktree 功能未启用」；创建 worktree_dir；从 session_file 反序列化 current_session（缺失/非法 JSON → stderr 警告并清空、=None；worktree_path 不存在 → 警告并清空）；扫描 worktree_dir 子目录还原 active（纯文件系统读）

### F3 创建流程

- F3.1 `Manager.create(name, base_ref="HEAD", manual)`（async）：
  1. `validate_slug(name)` 不通过即抛异常
  2. `async with self.lock:` 内，`active[name]` 已存在即抛异常
  3. 构建 `flat_slug`/`wt_path`/`branch_name`
  4. **快速恢复**：wt_path 已存在 → 读 `.git` 指针 + `HEAD` + `refs/heads/<branch>` 还原 head_sha，构造 Worktree 入 active 返回（零 git 子进程）
  5. 否则执行 `git worktree add -B <branch> <wt_path> <base_ref>`（`GIT_TERMINAL_PROMPT=0`、`GIT_ASKPASS=""`、stdin 关闭）；失败抛异常并清理残留目录
  6. 创建后设置（F4）best-effort，失败仅 stderr 警告
  7. 读 head_sha（`git -C <wt_path> rev-parse HEAD`），装填 Worktree
  8. 入 active，返回
- F3.2 分支起点为当前 HEAD（或指定 base_ref）；主工作树未提交的修改不在 worktree 内（已知限制写入文档）

### F4 环境初始化（创建后设置，best-effort 失败只警告）

- F4.1 **A 复制本地配置**：从 `<repo_root>/.newcode/` 复制 `config.local.yaml`、`permissions*.yaml`、`agents/`、`skills/` 等到 Worktree 同位置（目标已存在跳过、源缺失跳过）
- F4.2 **B git hooks**：检测主仓库 `core.hooksPath` 与 `.husky/`，有则 `git -C <wt> config core.hooksPath <绝对路径>`；无则跳过
- F4.3 **C 软链大目录**：默认 `["node_modules", ".venv", "vendor"]`，主仓库存在且 Worktree 不存在则 `os.symlink`
- F4.4 **D `.worktreeinclude`**：读项目根 `.worktreeinclude`（每行 glob 模式），用 `git -C <repo_root> ls-files --others --ignored --exclude-standard --directory` 列出被忽略文件，匹配后复制到 Worktree 对应路径

### F5 进入 / 退出 / 删除

- F5.1 `Manager.enter(name)`（async）：锁内取 `active[name]`（不存在抛异常）；记录当前 `Path.cwd()` 与当前 Git HEAD/branch 为原状态；构造 WorktreeSession；写 `current_session` 并原子持久化（tmp + rename）；**不调 `os.chdir`**
- F5.2 `Manager.exit(name, action: ExitAction[KEEP|REMOVE], opts: ExitOptions(discard_changes))`（async）：锁内取 `active[name]` 与 `current_session`（`current_session.worktree_name != name` 抛异常，只能退当前）；`action=REMOVE` 且未 `discard_changes` 时调 `_has_worktree_changes`，有变更抛 `WorktreeHasChangesError`；`os.chdir(session.original_cwd)` 兜底（防 session 期间残留）；`current_session=None` 持久化为 `null`；`action=REMOVE` → `git worktree remove --force` + `await asyncio.sleep(0.1)` + `git branch -D`；`del active[name]`
- F5.3 `Manager.remove(name, opts)`：独立 remove 入口，可删非当前 session 的 Worktree；变更保护同 F5.2
- F5.4 `_has_worktree_changes(wt_path, base_commit)`：`git status --porcelain` 非空即有未提交修改；`git rev-list --count <base>..HEAD` >0 即有新增 commit；任一 git 命令出错 **fail-closed 返回 True**（宁可保留）

### F6 自动清理与后台过期清理

- F6.1 `Manager.auto_cleanup(name)`：`manual=True` 直接 keep；`_has_worktree_changes` 为 False → `remove(name, ExitOptions(discard_changes=True))`，返回 `kept=False`；有变更 → `kept=True`，报告 path + branch
- F6.2 子 Agent 完成后走 auto_cleanup；`kept=True` 时把 `\n[Worktree 保留在 <path>,分支 <branch>]` 追加到子 Agent 结果文本给主 Agent review
- F6.3 手动创建（`/worktree create`）不走自动清理，保留手动控制
- F6.4 `Manager.sweep_stale(cutoff)`（async）三层过滤：
  1. **第一层** 名字匹配 `^agent-a[0-9a-f]{7}$` 或 `wf-` 前缀
  2. **第二层** 目录 mtime > cutoff 跳过；`current_session.worktree_path` 跳过
  3. **第三层** `_has_worktree_changes` 为 True 跳过（fail-closed）；额外 `git -C <dir> rev-list --max-count=1 HEAD --not --remotes` 非空跳过（未推送 commit 保留）
  - 通过三层的调 `remove(name, ExitOptions(discard_changes=True))`，记入 removed
- F6.5 newcode 启动时 `asyncio.create_task(sweep_stale(now - expire_minutes))` 异步后台执行，不阻塞启动；后续按 `cleanup_interval_minutes` 周期执行

### F7 explicit cwd 工具改造

- F7.1 新建 ctx 机制（**机制为新建**，实际代码库无 contextvars 先例）：`contextvars.ContextVar("cwd", default=None)`；`with_cwd(dir)` 返回 context manager（设置/恢复 token）；`cwd_from_ctx()` 取回；`resolve_path(p)`——绝对路径原样返回，相对路径用 ctx cwd（优先）或进程 cwd 拼接，返回绝对路径
- F7.2 改造 6 个核心工具：`read_file`/`write_file`/`edit_file`/`list_files`/`search_code` 用 `resolve_path` 解析 `path` 参数；`execute_command` 在 subprocess 调用上设 `cwd=`（ctx cwd 或进程 cwd）
- F7.3 ctx 注入点：子 Agent `isolation: worktree` 启动时 `with_cwd(wt.path)` 包住 `run_to_completion`；TUI `/worktree enter` 把 cwd 写到 `app.active_cwd`，主 Agent 下次 Run 注入
- F7.4 工具 Schema 不变——ctx 注入不暴露 cwd 字段，主 Agent 工具列表与参数与 ch13 完全一致（prompt cache 不抖动）

### F8 SubAgent 集成

- F8.1 `subagent.Definition` 新增 `isolation: str` 字段；parser 解析 frontmatter `isolation:`，合法值 `""`/`"worktree"`，非法值 stderr 警告回落到 `""`
- F8.2 `AgentTool.execute` 插桩——生效隔离为 worktree（角色声明或动态参数，F8.6）时走 `_execute_with_worktree` 分支：
  1. worktree name = `agent-a<7位随机hex>`（规避同类型并发冲突）
  2. `worktree_manager.create(name, "HEAD", manual=False)`
  3. **隔离构造子 Agent**：权限沙箱根 = worktree 路径（F4.4「沙箱根跟随工作目录」，绝对路径出 worktree 即拒）+ 权限模式 `acceptEdits`（**worktree 内写自动放行**，命令仍按规则/ASK→DENY）
  4. 构造 worktree notice（F8.3）拼到 task 文本前
  5. `with_cwd(wt.path)` 包住后续调用
  6. `sub_agent.run_to_completion(sub_conv, task_with_notice, ...)`
  7. 跑完 `manager.auto_cleanup(name)`，`kept=True` 时把保留通知追加到 final_text
  8. 返回 final_text 给主 Agent
- F8.3 `build_worktree_notice(parent_cwd, wt_path)` 模板（内容中文友好）：
  ```
  <worktree-context>
  你当前在一个独立的 Git Worktree 副本中工作，与父 Agent 隔离。
  - 父目录：<parent_cwd>
  - 你的工作目录：<wt_path>
  - 父 Agent 提到的绝对路径基于父目录，你需要翻译成本地路径（替换前缀）再读写
  - 编辑文件前，必须先在本 Worktree 重新 read_file 一次，避免使用过时内容
  </worktree-context>
  ```
- F8.4 后台 + isolation 协同：本期最小实现——`isolation: worktree` 的隔离子 Agent 强制走前台（plan 细化）
- F8.5 子 Agent 的 agent catalog（角色定义）从主项目加载（共享基础设施），不随 worktree 变化
- F8.6 **动态隔离通道**：agent 工具新增可选 `isolation` 参数（enum `worktree`/`none`）——
  `worktree`=本次调用强制 Git Worktree 隔离、`none`=本次强制不隔离、不传沿用角色 frontmatter 声明；
  动态参数**优先于**角色声明（生效值 = 参数或角色，取其一）。动态请求隔离但 worktree 不可用
  （未启用/非 git 仓库）→ 结构化错误「worktree 隔离不可用」，**不静默降级**（N6）；
  角色静态声明但不可用 → 按 F11.2 降级为不隔离

### F9 /worktree 斜杠命令

- F9.1 `/worktree create <slug>`：`manager.create(slug, "HEAD", manual=True)`，输出 path + branch
- F9.2 `/worktree list`：遍历 `manager.list()`，行格式 `<name>  <path>  <branch>  [active?]`
- F9.3 `/worktree enter <slug>`：`manager.enter(slug)`，把 ctx cwd 写到 `app.active_cwd`，主 Agent 下次 Run 注入
- F9.4 `/worktree exit [--remove] [--discard]`：退出当前 session；`--remove` 时调 `exit(name, ExitAction.REMOVE, ExitOptions(discard_changes=--discard))`
- F9.5 `/worktree remove <slug> [--discard]`：`manager.remove(slug, ...)`
- F9.6 命令属 LOCAL/UI 类（读/改 TUI 状态），不进对话历史；输出走 ui

### F10 持久化与恢复

- F10.1 WorktreeSession 序列化 JSON（小写下划线字段），原子写（先写 `<session_file>.tmp` 再 `os.replace`）
- F10.2 启动读取 session_file：`null`/空 → `current_session=None`；`worktree_path` 不存在 → 清空文件 + stderr 警告（"session worktree gone, cleared"）
- F10.3 CLI **新增** `--resume`（实际代码库无此参数）：读到已有 session 时把 `active_cwd` 设为 `session.worktree_path`，主 Agent 后续工具调用按 explicit cwd 走

### F11 配置

- F11.1 `.newcode/config.yaml` 新增 `worktrees:` 段（三层合并 local > project > user，缺省全可用，段缺失不报错）：
  ```yaml
  worktrees:
    enable: true                # 总开关；false 时 isolation: worktree 角色退化为不隔离（F11.2）
    auto_cleanup: true          # 子 Agent 完成后自动清理开关（F6.1/F6.2）
    background_cleanup: true    # 异常残留后台清理开关（F6.4/F6.5）
    cleanup_interval_minutes: 60  # 后台清理周期
    expire_minutes: 180         # 异常残留过期时间
  ```
- F11.2 `enable=false` 时 `isolation: worktree` 角色退化为不隔离，不建目录

## 非功能需求

- N1：**工具列表稳定**——ctx 注入不改 schema，prompt cache 不抖动
- N2：**创建后设置失败不阻塞**——仅 `git worktree add` 本身失败抛异常；F4 各步失败仅警告
- N3：**并发安全**——Manager 状态变更受 `asyncio.Lock` 保护；worktree 内部 git 操作不持锁 + 一次性 `sleep(0.1)` 解 lockfile 竞态
- N4：**os.chdir 仅出现在 exit 兜底**——其余一律 explicit cwd
- N5：**session 文件损坏不阻断启动**——stderr 警告并清空
- N6：**错误隔离**——worktree 操作失败不影响主 Agent 主流程与 TUI
- N7：**安全**——slug 严格校验防路径遍历；worktree 操作仅限仓库内
- N8：**数据保护**——三层清理保护不删脏/有新增 commit 的 worktree
- N9：**兼容**——cwd 显式化不改默认行为（默认 cwd = 进程 cwd），存量测试全绿
- N10：**可诊断**——worktree 操作失败定位到 git 命令与原因
- N11：**优雅降级**——非 git 仓库 / 无 commit / git 未安装 → 结构化错误不崩
- N12：**中文友好**——错误消息与命令输出全中文
- N13：**测试规范**——接线测试自动跑、mock 驱动真实路径、每测试标注防的 bug
- N14：**文档保护**——docs/ 不可变
- N15：**版本号**——0.14.0（已 bump，两处一致）

## 不做的事

- Worktree 间合并策略（交给上层 `git merge` / `git cherry-pick`）
- 跨 Worktree 代码同步、文件 watcher
- 多 Agent 并行编排 / Agent Team（下一章）
- 远端/推送集成（无网络操作；sweep 的 `--not --remotes` 仅本地 refs 检查）
- 非 git 仓库的文件隔离（worktree 机制依赖 git）
- Worktree 生命周期 hook 事件（ch12 hook 系统本期不扩展）
- 插件来源的 Worktree 配置
- Windows 平台特殊支持（symlink 行为不保证；本期以 Linux/macOS 为主）
- 跨 newcode 进程实例共享（同一仓库同一时刻单 newcode 实例操作 worktree session）
- Worktree git 操作的 retry / exponential backoff（一次性 `sleep(0.1)` 解竞态即可）

## 验收标准

- AC1（F1.1）：`validate_slug` 对 `"feature/a"` 通过；对 `"../etc"` / `".."` / `"a//b"` / `"a/b "` / `""` 拒绝并带原因
- AC2（F1.2/F3.1）：`create("alice", "HEAD", manual=True)` → `.newcode/worktrees/alice/` 落地，分支 `worktree-alice`
- AC3（F1.2/F3.1）：`create("team/alice", ...)` → `.newcode/worktrees/team+alice/`，分支 `worktree-team+alice`
- AC4（F3.1/F5.1）：目录已存在的合法 worktree 再 create → 快速恢复，不调 `git worktree add`（断言无 git 子进程启动）
- AC5（F4.1）：主仓库存在 `.newcode/config.local.yaml` → worktree 内同位置出现该文件
- AC6（F4.2）：主仓库有 `.husky/` 或 `core.hooksPath` → worktree 的 `.git/config` 含 `core.hooksPath`
- AC7（F4.3）：主仓库有 `node_modules/` → worktree 内是 symlink（`Path.is_symlink()` 为 True）
- AC8（F4.4）：主仓库有 `.worktreeinclude` 含 `*.env` 且存在被忽略的 `.env` → worktree 内出现 `.env`
- AC9（F5.1）：`enter(name)` **不改变**进程 `Path.cwd()`；返回 session 字段正确
- AC10（F5.2）：`exit(name, REMOVE, ExitOptions())` 遇未提交修改 → 抛 `WorktreeHasChangesError`，worktree 目录仍在
- AC11（F5.2）：`exit(name, REMOVE, ExitOptions(discard_changes=True))` → 目录删、分支删
- AC12（F6.1）：`auto_cleanup` 对 `manual=True` 直接 keep；对 `manual=False` 且无变更 remove
- AC13（F7.2）：`read_file`/`write_file`/`edit_file`/`list_files`/`search_code`/`execute_command` 在 ctx cwd 注入下以 cwd 为基准解析相对路径
- AC14（F7.2）：`execute_command` 在 ctx cwd 注入下子进程 `cwd=` 参数为 ctx cwd
- AC15（F8.2）：`Definition.isolation == "worktree"` → `AgentTool.execute` 创建临时 worktree、注入 notice、传 ctx cwd、跑完 auto_cleanup
- AC16（F8.2）：隔离子 Agent 写文件不影响主工作目录（主目录对应文件未变）
- AC17（F9.1/F9.2）：`/worktree create alice` 成功落地，`/worktree list` 输出含 alice
- AC18（F9.4）：`/worktree exit --remove` 遇未提交修改报错；加 `--discard` 后删除成功
- AC19（F6.4）：`sweep_stale` 只删名字匹配 `agent-a[0-9a-f]{7}`/`wf-`、跳过当前 session、跳过有变更/未推送 commit 的目录
- AC20（F10）：session 持久化到 `.newcode/worktree_session.json`；worktree 目录被外部删除后启动清空 + stderr 警告
- AC21（F1.4）：根 `.gitignore` 缺 `.newcode/worktrees/` 与 `.newcode/worktree_session.json` 两行时，启动 stderr 警告、**不修改** .gitignore
- AC22（F7.1/F7.4）：cwd ContextVar 机制从无到有（新建）；主 Agent 工具 schema 不变
- AC23（F11.2）：`worktrees.enable=false` → `isolation: worktree` 角色退化为不隔离，不建目录
- AC24（N11）：非 git 仓库 / 无 commit / git 缺失 → worktree 命令返回结构化错误，主流程不崩
- AC25（N6/N9/N10）：项目可启动（`python -m newcode`）、全量存量测试通过（ch13 行为零回归）、`ruff check` 通过

## 端到端场景（验收参考）

- 场景 1（并行隔离）：两个 `isolation: worktree` 的子 Agent 同时各写各自文件 → 互不覆盖，各落各 worktree
- 场景 2（保留→review→接着改）：子 Agent 写了代码 → worktree 保留 → 主 Agent `/worktree list` 看到 → `/worktree enter` 接着改 → `/worktree exit`
- 场景 3（无价值自动清理）：子 Agent 只读分析无改动 → 完成即自动清理（目录+分支消失）
- 场景 4（手动不清理）：`/worktree create review-fix` 手动创建 → 子 Agent 完成后 / 后台清理均不删它
- 场景 5（异常残留清理）：模拟异常退出残留 `agent-xxx` worktree → 过期 + 干净 → 被后台清理；过期但脏 / 有 commit → 保留
- 场景 6（--resume）：退出后 `--resume` 重启 → session 状态恢复，list 可见，可 enter
- 场景 7（优雅降级）：在非 git 目录运行 → `/worktree list`/`create` 返回结构化错误不崩
- 场景 8（tmux 实跑）：启动 + 触发 `isolation: worktree` 子 Agent 改文件 → 验证主目录 `server.py` 未变、Worktree 副本里已变；Worktree 留盘 / 自动清理符合预期
