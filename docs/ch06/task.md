# NewCode ch06 — 五层权限系统 任务拆解 (task.md)

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `newcode/permission/__init__.py` | 公开 API 导出 |
| 新建 | `newcode/permission/blocklist.py` | 危险命令黑名单（L1），不可配 |
| 新建 | `newcode/permission/sandbox.py` | 路径沙箱（L2），含祖先回退 |
| 新建 | `newcode/permission/rules.py` | 规则解析、加载、三层合并、match_pattern（L3） |
| 新建 | `newcode/permission/engine.py` | 规则引擎匹配（L3） |
| 新建 | `newcode/permission/modes.py` | 权限模式矩阵 + PermissionMode 枚举（L4） |
| 新建 | `newcode/permission/hitl.py` | HITL 请求/响应数据结构（L5） |
| 新建 | `newcode/permission/checker.py` | 权限检查器串联入口 + categorize + extract_target |
| 新建 | `.newcode/permissions.yaml.example` | 权限配置示例 |
| 新建 | `tests/test_permission_blocklist.py` | 黑名单测试 |
| 新建 | `tests/test_permission_sandbox.py` | 沙箱测试（含祖先回退/符号链接） |
| 新建 | `tests/test_permission_rules.py` | 规则解析、匹配、加载降级测试 |
| 新建 | `tests/test_permission_engine.py` | 规则引擎三层匹配测试 |
| 新建 | `tests/test_permission_checker.py` | 检查器串联 + categorize + extract_target 测试 |
| 新建 | `tests/test_permission_tui.py` | TUI HITL 交互 + 模式切换测试（mock 驱动） |
| 修改 | `newcode/agent/events.py` | 新增 HITL_REQUEST 事件类型 |
| 修改 | `newcode/agent/agent.py` | 插入权限检查 + HITL 阻塞等待 + resolve_hitl |
| 修改 | `newcode/tools/registry.py` | 友好名映射 + 工具分类 |
| 修改 | `newcode/tools/shell.py` | 移除 _WHITELIST 白名单 |
| 修改 | `newcode/tools/file_ops.py` | _check_path 对齐沙箱 |
| 修改 | `newcode/config/schema.py` | 新增 permission_mode 字段 |
| 修改 | `newcode/config/loader.py` | 新增 load_permission_rules |
| 修改 | `newcode/tui/app.py` | HITL 确认框、Shift+Tab、状态栏、全局取消覆盖 APPROVING |
| 修改 | `newcode/prompt/reminders.py` | plan 模式提醒调整 |
| 修改 | `newcode/main.py` | --mode 参数、权限系统初始化 |
| 修改 | `.gitignore` | 追加 .newcode/permissions.local.yaml |

---

## T1: permission 基础类型枚举

**文件：** `newcode/permission/__init__.py`、`newcode/permission/modes.py`、`newcode/permission/hitl.py`

**依赖：** 无

**步骤：**
1. 在 `modes.py` 中定义 `PermissionMode` 枚举（DEFAULT/ACCEPT_EDITS/PLAN/BYPASS），含 `display_name()`（返回 `"DEFAULT"/"ACCEPT EDITS"/"PLAN"/"BYPASS"`）和 `@staticmethod parse(s)`（大小写不敏感识别四档名，未知返回 None）
2. 在 `modes.py` 中定义 `ToolCategory` 枚举（READONLY/FILE_WRITE/COMMAND）
3. 在 `checker.py` 顶部定义 `Decision` 枚举（ALLOW/DENY/ASK）和 `CheckResult` 数据类（decision, reason 字段）
4. 在 `hitl.py` 中定义 `HITLRequest` 和 `HITLResponse` 数据类
5. 在 `__init__.py` 中导出核心类型

**验证：** `python -c "from newcode.permission import Decision, PermissionMode, CheckResult, HITLRequest, HITLResponse; print(PermissionMode.parse('default')); print(PermissionMode.parse('x'))"` 输出 `PermissionMode.DEFAULT` 和 `None`

## T2: 危险命令黑名单（L1）

**文件：** `newcode/permission/blocklist.py`

**依赖：** T1

**步骤：**
1. 定义 `BLOCKLIST_PATTERNS: list[re.Pattern]` 常量，覆盖高危模式：
   - `rm -rf /`、`rm -fr ~`、`rm -rf $HOME`、`rm -rf /*` 等变体
   - `dd ... of=/dev/` 写块设备
   - `:(){ :|:& };:` fork 炸弹
   - `mkfs.` 格式化文件系统
   - 重定向覆盖磁盘设备（`> /dev/sd*` 等）
   - 远程脚本下载即执行（`curl ... | sh`、`wget ... | bash` 等）
2. 实现 `hits_blacklist(command: str) -> bool`：任一 Pattern 匹配即返回 True
3. 模块 docstring 顶部声明「启发式、非完备、不可配置放开」

**验证：** 单测：`rm -rf /`、`rm -fr ~`、`:(){ :|:& };:`、`dd if=/dev/zero of=/dev/sda` 命中；`rm -rf ./build`、`git status`、`ls -la` 不命中

## T3: 路径沙箱（L2）

**文件：** `newcode/permission/sandbox.py`

**依赖：** T1

**步骤：**
1. 实现 `resolve_root(root: str) -> str`：`os.path.abspath(root)` → `os.path.realpath()`；失败返回原值
2. 实现 `eval_symlinks_or_ancestor(abs_path: str) -> str`：
   - 存在 → `os.path.realpath()`
   - 不存在 → 逐级向上找最近**已存在祖先**目录，对该祖先 `realpath`，再 `os.path.join(real_ancestor, 剩余段)`
   - 覆盖「新建文件含未创建中间目录」场景
3. 实现 `check_path(target_path: str, project_root: str) -> CheckResult`：
   - 空 path 视为 root
   - 相对路径用 root 拼接后解析为绝对路径
   - 调用 `eval_symlinks_or_ancestor` 得到 resolved
   - 判断 `resolved == root or resolved.startswith(root + os.sep)`（按段比对，避免 `/rootfoo` 误匹配 `/root/foo`）

**验证：** 单测（`tmp_path` 造 root + 内外文件 + `os.symlink`）：root 内文件 Allow；**含多级未创建中间目录的新建文件路径 Allow**（祖先回退分支）；`/etc/passwd`、`../outside`、root 内指向 root 外的软链接 → Deny

## T4: 规则数据结构与匹配

**文件：** `newcode/permission/rules.py`

**依赖：** T1

**步骤：**
1. 定义 `Rule` 数据类：tool_name, pattern, action, source 字段
2. 实现 `Rule.parse(raw: str, action: str, source: str) -> Rule | None`：
   - 正则提取 `^(Bash|Read|Write|Edit|Glob|Grep)(?:\((.*)\))?$`
   - 无括号→pattern=""（匹配所有）；有括号→提取括号内容
   - 非法（空字符串、括号不配对）→返回 None
3. 实现 `match_pattern(pattern: str, target: str) -> bool`：
   - `pattern == ""` → True（恒匹配）
   - 命令 glob：`*` 匹配任意字符（含空格），`**` 等价 `*`；其余字面匹配
   - 文件路径：按 `/` 分段，`*` 匹配单段内任意字符，`**` 匹配任意多段
4. 定义 `RuleSet` 类（allow/deny 两个列表）：
   - `match(friendly, target) -> Decision | None`：先遍历 deny 命中→DENY，再 allow 命中→ALLOW，否则 None
5. 定义 `RuleLayers` 类（local/project/user 三个 RuleSet）：
   - `match(friendly, target) -> Decision | None`：local→project→user 顺序，首命中即返回

**验证：** 单测：`Rule.parse("Bash(git *)")` 正确；`match_pattern("git *","git status")` True；`match_pattern("src/**","src/a/b.py")` True；同层 deny+allow 同时命中→DENY

## T5: 规则加载与配置映射

**文件：** `newcode/permission/rules.py`（同上文件，追加）、`newcode/permission/checker.py`（追加 categorize + extract_target）

**依赖：** T1, T4

**步骤：**
1. 在 `rules.py` 中实现 `load_settings(filepath: str) -> dict`：
   - 文件不存在→`{}`，不抛
   - 用 `yaml.safe_load` 解析；失败→`{}` + 打印警告
2. 实现 `build_rule_set(entries: list[str], action: str, source: str) -> RuleSet`：
   - 遍历 entries，各 `Rule.parse`；非法条目跳过
   - 分别入 allow/deny 列表
3. 实现 `load_rules(project_root: str) -> RuleLayers`：
   - 加载三层：user `~/.config/newcode/permissions.yaml`、project `<root>/.newcode/permissions.yaml`、local `<root>/.newcode/permissions.local.yaml`
   - 调用 `load_settings` → `build_rule_set`
4. 在 `checker.py` 中实现 `friendly_name(internal: str) -> str`：
   - `bash→Bash, read_file→Read, write_file→Write, edit_file→Edit, glob→Glob, grep→Grep`；未知原样返回
5. 在 `checker.py` 中实现 `categorize(internal: str, read_only: bool) -> ToolCategory`：
   - **read_only==True** → `READONLY`（优先于名字判定）
   - `write_file`/`edit_file` → `FILE_WRITE`
   - 其余（含 Bash、未知工具）→ `COMMAND`（最严）
6. 在 `checker.py` 中定义 `TargetInfo(target, is_file, ok)` 数据类，实现 `extract_target(tool_call) -> TargetInfo`：
   - 用 `json.loads(tool_call.input)` 解析 JSON
   - `read_file`/`write_file`/`edit_file` → 取 `path`（is_file=true）
   - `glob`/`grep` → 取 `path`（搜索根目录，空→`"."`，is_file=true）
   - `Bash` → 取 `command`（is_file=false）
   - 未知工具 → `TargetInfo("", false, false)`
   - **JSON 解析失败或缺必填字段 → `ok=False`**

**验证：** 单测：缺失文件得空 RuleSet 不抛；非法 YAML 降级、不致命；`categorize` 含未知→EXEC、readOnly 优先；`extract_target` 各分支正确、解析失败 ok=false

## T6: 权限检查器（前四层流水线 + 规则引擎 + 模式矩阵）

**文件：** `newcode/permission/checker.py`（完整实现）、`newcode/permission/engine.py`、`newcode/permission/modes.py`

**依赖：** T2, T3, T5

**步骤：**
1. 在 `modes.py` 中定义 `MODE_MATRIX` 常量（四档 × 三类 → ALLOW/ASK），实现 `resolve_mode(mode, category) -> Decision`（只产 Allow/Ask）
2. 在 `engine.py` 中实现 `RuleEngine` 类：`match(friendly, target) -> CheckResult | None`（调用 `RuleLayers.match`）
3. 在 `checker.py` 中实现 `PermissionChecker` 类：
   - `__init__(self, root, mode, layers)`：存 root、mode、layers、engine
   - `@staticmethod create(project_root) -> PermissionChecker`：
     - `resolve_root`；失败 → stderr 警告 + 返回非 null 空引擎
     - 加载三层规则 → `RuleLayers`（单个文件失败仅降级跳过）
     - `start_mode`：依次 local/project/user 的 `defaultMode`（local 优先），皆无→DEFAULT
     - 永不返回 None
   - `check(tool_call, is_interactive=True) -> CheckResult`：
     - ① categorize + extract_target
     - ② `cat == COMMAND && target != "" && hits_blacklist(target)` → `DENY`
     - ③ `is_file`：`!ok` → `DENY`；`!sandbox_ok(target)` → `DENY`
     - ④ `engine.match(friendly, target)`，命中 → 返回
     - ⑤ `resolve_mode(mode, cat)` → `ALLOW`/`ASK`
     - bypassPermissions 跳过 ④⑤（规则引擎和权限模式；HITL 由Agent层跳过）
   - `set_mode(mode)`、`property mode`、`property start_mode`
4. 实现 `persist_local_allow(tool_call)`：
   - 生成精确规则字符串（`Bash(<command>)` 或 `Write(<relpath>)` 等，无通配）
   - Bash 命令经 `escape_glob` 转义 `*`/`?`/`[`/`]`
   - 读 local 文件（缺失则空）→ 追加到 `permissions.allow`（去重）→ YAML dump 写回
   - `os.makedirs` 确保父目录存在
   - 失败仅抛 `IOError`（调用方捕获只记不阻断）

**验证：** 单测：逐层短路（黑名单先于沙箱/规则；deny 规则先于模式；allow 规则不进模式）；跳层放行（非 EXEC 不被黑名单拦、Bash 不被沙箱拦）；模式矩阵逐档逐类断言；三级优先级（本地 deny 盖项目 allow）；`create` resolveRoot 失败仍得非 null 引擎

## T7: 工具分类与友好名映射（Registry 扩展）

**文件：** `newcode/tools/registry.py`

**依赖：** T1

**步骤：**
1. 新增 `FRIENDLY_NAME_MAP` 类属性：Bash→execute_command, Read→read_file, Write→write_file, Edit→edit_file, Glob→search_code, Grep→search_code
2. 新增 `INTERNAL_TO_FRIENDLY` 反向映射
3. 新增 `get_friendly_name(internal: str) -> str` 方法
4. 新增 `get_category(internal: str) -> ToolCategory` 方法（read_only 优先→READONLY）

**验证：** `registry.get_category("read_file")` → `READONLY`；`get_category("execute_command")` → `COMMAND`；`get_category("unknown_tool")` → `COMMAND`

## T8: 事件系统扩展 + Agent 集成

**文件：** `newcode/agent/events.py`、`newcode/agent/agent.py`

**依赖：** T6, T7

**步骤：**
1. 在 `events.py` 的 `EventType` 枚举中新增 `HITL_REQUEST = "hitl_request"`
2. 在 `agent.py` 中：
   - `Agent.__init__` 新增 `permission: PermissionChecker`、`is_interactive: bool = True` 参数
   - 新增 `_hitl_event: asyncio.Event`、`_hitl_response: HITLResponse | None` 属性
   - 新增 `resolve_hitl(response: HITLResponse) -> None` 方法
   - 在 `run()` 中 known_calls 分类后、scheduler.schedule 前，插入权限检查循环：
     - **DENY**：产 `TOOL_CALL` + `TOOL_RESULT(error)` 事件，写入历史，不执行；**不触发 `_unknown_streak` 计数**
     - **ALLOW**：加入 `allowed_calls` 列表
     - **ASK（交互）**：`yield HITL_REQUEST` → `await _hitl_event.wait()` → 根据 response 处理
     - **ASK（非交互）**：直接转为 DENY
   - 只读批 Deny 调用**不纳入并发执行**（同批其他只读仍并发）
   - 保序：Deny 与 Allow 项的 TOOL_CALL + TOOL_RESULT 事件按调用序，互不串位
   - plan 模式：暴露全部工具定义（`to_definitions()`），移除硬性只读过滤
   - `cancel()` 方法中设置 `_hitl_event` 兜底解阻塞

**验证：** 轻量自检：mock Agent 测试，工具调用 DENY 后仍继续循环，下一轮看到拒绝原因；Deny 不触发 CONSECUTIVE_UNKNOWN_TOOLS

## T9: 配置系统扩展 + 工具适配

**文件：** `newcode/config/schema.py`、`newcode/config/loader.py`、`newcode/tools/shell.py`、`newcode/tools/file_ops.py`、`newcode/prompt/reminders.py`、`.gitignore`、`.newcode/permissions.yaml.example`

**依赖：** T6

**步骤：**
1. `schema.py`：`Config` 新增 `permission_mode: str = "default"` 字段
2. `loader.py`：新增 `load_permission_rules(project_root) -> RuleLayers`，调用 `rules.load_rules`
3. `shell.py`：删除 `_WHITELIST` 常量、删除 `_get_command_token` 白名单检查逻辑
4. `file_ops.py`：`_check_path` 更新为调用 `sandbox.check_path`（符号链接感知）
5. `reminders.py`：plan 模式提醒新增「若为单纯询问无需代码改动，直接回答，不生成计划文件」
6. `.gitignore`：追加 `.newcode/permissions.local.yaml`
7. `.newcode/permissions.yaml.example`：创建配置文件示例

**验证：** 运行现有 shell/file_ops 测试确认不因移除白名单而失败；prompt 文本含新增指令

## T10: TUI 集成（HITL 确认框 + 模式切换 + 状态栏）

**文件：** `newcode/tui/app.py`

**依赖：** T8

**步骤：**
1. `NewCodeModel` 新增 `permission` 属性；构造时从 `agent.permission` 获取；`mode` 初值 `engine.start_mode`
2. 新增 `APPROVING` 状态；新增 `pending: HITLRequest | None`、`approve_cursor: int` 属性
3. **HITL 确认框**：`_consume_agent_events` 收到 `HITL_REQUEST` 事件：
   - 保存 `pending`、`approve_cursor = 0`、切 `APPROVING` 态
   - 渲染多行确认块：`● <工具名>` + 缩进参数预览 + 灰字触发原因 + 三行菜单（光标项 `> ` 高亮，其余 `  `）`1. 允许本次 / 2. 永久允许（写入本地配置） / 3. 拒绝本次` + 底部灰字 `↑↓ 选择 · 回车确认 · Esc 取消`
   - 交互：`ArrowUp`/`k`、`ArrowDown`/`j` 循环移光标；`Enter`/空格提交
   - 数字键 `1`/`2`/`3` 直选（1=allow_once, 2=allow_always, 3=deny）
   - 便捷键：`y`=允许本次，`n`/`d`=拒绝本次
   - 选定后 `agent.resolve_hitl(response)`，切回 `STREAMING`
4. **全局取消覆盖 APPROVING（blocker 修复）**：Ctrl+C/Esc 分派条件从 `STREAMING` 扩展到 `STREAMING || APPROVING`
   - approving 态取消时先 `agent.resolve_hitl(HITLResponse(action="deny"))` 兜底解阻塞
   - 再走 `cancel_turn()` 收尾
5. **Shift+Tab 切换**：仅 IDLE 态生效；`next_mode = Mode.values()[(current.ordinal() + 1) % 4]` 循环四档（含 bypass）
   - 切换后 `agent._permission.set_mode(new_mode)`
   - scrollback 追加 noticeLabel 提示新模式
6. **状态栏**：左侧常驻权限模式名（取代 provider 名）：DEFAULT→`DEFAULT`（灰/绿）、ACCEPT_EDITS→`ACCEPT EDITS`、PLAN→`PLAN`（黄）、BYPASS→`BYPASS`（红）；右侧模型名+token 用量不变
7. 保留 `/plan`（→Mode.PLAN）、`/do`（→Mode.DEFAULT + 注入执行指令）、`/delete-plan`；不新增 `/mode` 命令

**验证：** `python -m newcode` 能正常启动进 TUI

## T11: main.py 接线 + smoke 适配

**文件：** `newcode/main.py`

**依赖：** T6, T9, T10

**步骤：**
1. 新增 `--mode` 参数：`choices=["default", "acceptEdits", "plan", "bypassPermissions"]`
2. 启动时：`project_root = os.path.abspath(".")` → `PermissionChecker.create(project_root)` → 注入 Agent
3. 非交互模式（`-c`）：`is_interactive=False`；默认模式取自 `--mode` 或配置，未指定则 `default`
4. `-c -p` 保留：直接生成计划文件，不弹确认

**验证：** `newcode -c "echo test" --mode bypassPermissions` 不触发权限确认；`newcode --help` 显示 `--mode` 选项

## T12: 测试——permission 包

**文件：** `tests/test_permission_blocklist.py`、`tests/test_permission_sandbox.py`、`tests/test_permission_rules.py`、`tests/test_permission_engine.py`、`tests/test_permission_checker.py`

**依赖：** T2, T3, T5, T6

**步骤：**
1. **黑名单**：高危命令命中、安全命令不命中、bypass 下仍生效
2. **沙箱**：项目内正常、越界拒绝、符号链接逃逸拒绝、**祖先回退**（新建文件含未创建中间目录）、Windows junction（如有条件）
3. **规则**：`parse_rule` 精确/glob/无参/非法；`match_pattern` 命令/路径 glob；`RuleSet.match` deny 优先；`RuleLayers.match` 三层优先；`load_rules` 缺失/格式错误降级
4. **引擎**：端到端匹配（exact + glob + 三层优先）
5. **检查器**：
   - 黑名单命中+规则 allow→最终 DENY（短路）
   - 沙箱越界+规则 allow→DENY
   - bypass 全放行但黑名单/沙箱仍生效
   - default/acceptEdits/plan/bypass 逐档逐类断言
   - 非交互 ASK→DENY
   - **categorize**：未知工具→EXEC、readOnly 优先
   - **extract_target**：各工具字段正确、JSON 解析失败 ok=false、文件类不可解析→Deny、Bash 缺 command→空串落 Ask
   - `create` resolveRoot 失败仍得非 null 引擎

**验证：** `pytest tests/test_permission_*.py -v` 全部通过

## T13: 测试——Agent 集成 + TUI 集成

**文件：** `tests/test_permission_tui.py`、修改 `tests/test_agent.py`（如有）

**依赖：** T8, T10

**步骤：**
1. **Agent 集成**（用 mock 事件流驱动）：
   - Deny 回灌不中断：构造 deny→工具结果 isError=True、Loop 继续到次轮
   - **保序配對回灌**：单批含「被拒 + 放行」→结果按原调用下标序、各自 ID 正确配对，不串位
   - Ask 人在回路：收 `HITL_REQUEST`→向 respond 回传 `ALLOW_ONCE/DENY_ONCE`，断言执行/回灌生效
   - 永久放行：送 `ALLOW_ALWAYS`，断言 local 文件被写、含 allow 条
   - 只读并发不退化：一批只读不产生任何 `HITL_REQUEST` 事件；被沙箱拦的只读得 errResult、其余仍并发
   - **取消**：HITL 等待中 `cancel()` → Loop 干净收尾、历史合法、无 asyncio task 泄漏（`@pytest.mark.timeout(5)` 守护）
   - plan 迁移：`Mode.PLAN` 仍放全部工具定义、注入计划提醒
2. **TUI 集成**（mock 驱动）：
   - Shift+Tab 循环切换：断言 mode 依次 DEFAULT→ACCEPT_EDITS→PLAN→BYPASS→DEFAULT、scrollback 多一行提示
   - **模式跨轮保持**：切到 ACCEPT_EDITS 后再 begin_turn，断言 mode 仍为 ACCEPT_EDITS
   - HITL_REQUEST 触发确认框：`state == APPROVING`、`approve_cursor == 0`
   - 按键：ArrowDown+Enter→`ALLOW_ALWAYS`；数字键 `1`→`ALLOW_ONCE`、`3`→`DENY_ONCE`
   - approving 态 Esc/Ctrl+C→触发取消、respond 得兜底 `DENY_ONCE`、不退出程序
   - 状态栏左侧在各模式显示对应模式名，且不含 provider 名

**验证：** `pytest tests/test_permission_tui.py tests/test_agent.py -v --timeout=30` 全部通过

## T14: 最终验证

**文件：** 全部

**依赖：** T1–T13

**步骤：**
1. `ruff format .` 无错误
2. `ruff check .` 无告警（含 permission 子包）
3. `pytest tests/ -v --timeout=30` 全部通过（重点守护人在回路阻塞/取消）
4. （可选）`mypy newcode/` 通过
5. `python -m newcode --version` 正常输出版本号
6. `git check-ignore .newcode/permissions.local.yaml` 命中（本地层已 gitignore）
7. 端到端实跑：
   - default 下写文件触发 Ask 弹窗；选「允许本次」后文件被写
   - Shift+Tab 循环到 bypassPermissions 后不再 Ask、状态栏显示 `BYPASS`
   - `rm -rf /` 在 bypass 下仍被拦
   - HITL 弹窗按 Esc 后干净回到空闲、不退出程序、再发消息可继续

**验证：** 全部通过

## 执行顺序

```
T1(枚举类型) ─┬─────────────────────────────────────┐
T2(黑名单) ───┤                                      │
T3(沙箱) ────┤                                      ├─→ T6(检查器+引擎+矩阵)
T4(规则) ────┴─→ T5(加载/映射/extract) ──────────────┘        │
                                                              │
                                            T6 ─→ T7(Registry) ─→ T8(Agent)
                                                              │        │
                                            T9(配置+工具+prompt)        │
                                                              │        │
                                            T11(main.py) ←───┴────────┤
                                                                      │
                                            T8,T10 ─→ T10(TUI)        │
                                                       │              │
                                            T12(permission测试) ←──────┤
                                            T13(agent+TUI测试) ←───────┘
                                                       │
                                                       ↓
                                                  T14(最终验证)
```

T12 可在 T6 完成后并行开始；T13 需等 T8/T10 完成。