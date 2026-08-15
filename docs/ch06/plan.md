# MewCode ch06 — 五层权限系统 技术设计 (plan.md)

## 架构概览

权限系统在 Agent 编排层（`agent.py`）与工具执行层（`tools/`）之间插入一个独立的 `permission/` 包，对每次工具调用做**执行前**判定。五层边界：`PermissionChecker.check` 实现**前四层**（黑名单/沙箱/规则/模式兜底），以返回 `ASK` 作为「请走第五层」的信号；**第五层人在回路由 Agent 在 ASK 后编排驱动**（发 HITL_REQUEST 事件、阻塞等决策）。二者合称五层。

```
Agent.run (agent.py)
  │
  ├─ 分类工具调用 (known/unknown)
  │
  ├─ PermissionChecker.check(tool_call)   ← 前四层
  │   ├─ ① Blacklist.check(command)       → Deny | (跳过非EXEC类)
  │   ├─ ② Sandbox.check(path)            → Deny | (跳过非文件类)
  │   ├─ ③ RuleEngine.match(tool, args)   → Allow | Deny | None
  │   └─ ④ ModeMatrix.resolve(tool, mode) → Allow | Ask
  │
  ├─ Allow → Scheduler.schedule()
  ├─ Deny  → ToolResult(id, reason, isError=true) 回灌
  └─ Ask   → ⑤ Agent 发 HITL_REQUEST → await Event → TUI 确认 → Allow | Deny
```

- **permission/** 包零依赖 `tui/`、`provider/`，只依赖 `tools/`（工具分类）和标准库
- HITL 通过 `asyncio.Event` 实现 Agent ↔ TUI 阻塞等待，TUI 通过 `agent.resolve_hitl()` 回传用户选择
- 规则文件 YAML 在会话启动时加载，运行中不热重载
- **`PermissionChecker.create()` 永不返回 None**：即使致命错（仅 `resolveRoot` 失败）也返回非 null 空规则安全引擎 + stderr 警告，保证 `check` 不抛 NPE

## 核心数据结构

### Decision（决策枚举）

```python
from enum import Enum


class Decision(Enum):
    ALLOW = "allow"  # 放行
    DENY = "deny"  # 拒绝（含原因）
    ASK = "ask"  # 需要用户确认（进入第五层）
```

### CheckResult（检查结果）

```python
@dataclass
class CheckResult:
    decision: Decision
    reason: str = ""  # 原因文案，格式见下方「reason 文案来源表」
```

**reason 文案来源表**（统一格式，供 Deny 回灌与 Ask 展示一致）：

| 裁决来源 | reason 文案格式 |
|---------|---------------|
| 黑名单命中 | `命中危险命令黑名单：<命令片段>` |
| 沙箱逃逸 | `路径在项目目录之外：<target>` |
| 参数解析失败（文件类） | `无法解析文件路径参数，安全拒绝` |
| deny 规则命中 | `匹配 deny 规则：<Tool(pattern)>` |
| allow 规则命中 | `""`（空，无需展示） |
| 模式兜底 Ask | `<mode> 模式下 <category> 类操作需确认` |
| 模式兜底 Allow | `""`（空） |

### ToolCategory（工具类别）

```python
class ToolCategory(Enum):
    READONLY = "readonly"  # Read / Glob / Grep，只读且永不触发 Ask
    FILE_WRITE = "file_write"  # Write / Edit
    COMMAND = "command"  # Bash
```

### PermissionMode（权限模式）

```python
class PermissionMode(Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS = "bypassPermissions"

    def display_name(self) -> str:
        """返回用户可见名称：DEFAULT, ACCEPT EDITS, PLAN, BYPASS"""

    @staticmethod
    def parse(s: str) -> Optional["PermissionMode"]:
        """大小写不敏感识别四档名，未知返回 None"""
```

### Rule（单条规则）

```python
@dataclass
class Rule:
    tool_name: str  # 友好名：Bash / Read / Write / Edit / Glob / Grep
    pattern: str  # 匹配模式（"" 表示匹配该工具全部调用）
    action: Literal["allow", "deny"]
    source: str  # 来源文件路径（用于调试和反馈）

    @staticmethod
    def parse(
        raw: str, action: Literal["allow", "deny"], source: str
    ) -> Optional["Rule"]:
        """解析 'Bash(git *)' 或 'Read'；非法返回 None"""
```

### RuleSet（规则集）

```python
class RuleSet:
    """单层规则集，维护 allow/deny 两个列表"""

    allow: list[Rule]
    deny: list[Rule]

    def match(self, friendly: str, target: str) -> Decision | None:
        """同层内先 deny 后 allow 遍历；命中 deny → DENY，命中 allow → ALLOW，未命中 → None"""
```

### RuleLayers（三层规则）

```python
class RuleLayers:
    """三层规则：本地 > 项目 > 用户，跨层先命中定案"""

    local: RuleSet  # 本地级（.mewcode/permissions.local.yaml）
    project: RuleSet  # 项目级（.mewcode/permissions.yaml）
    user: RuleSet  # 用户级（~/.config/mewcode/permissions.yaml）

    def match(self, friendly: str, target: str) -> Decision | None:
        """local → project → user 顺序，首命中即返回"""
```

### HITLRequest / HITLResponse（人在回路）

```python
@dataclass
class HITLRequest:
    tool_name: str  # 友好名
    params_preview: str  # 关键参数预览
    reason: str  # 触发原因（来自 CheckResult.reason）


@dataclass
class HITLResponse:
    action: Literal["allow_once", "allow_always", "deny"]
```

### 事件扩展

```python
# 在 mewcode/agent/events.py 中新增
class EventType(Enum):
    # ... 现有类型 ...
    HITL_REQUEST = "hitl_request"  # payload: HITLRequest
```

### TargetInfo（参数提取结果）

```python
@dataclass
class TargetInfo:
    target: str  # 提取的匹配目标（命令串 / 路径 / ""）
    is_file: bool  # 是否文件类操作
    ok: bool  # 解析是否成功
```

## 模块设计

### mewcode/permission/blocklist.py — 危险命令黑名单（L1）

**职责：** 用内置正则匹配拦截高危命令，不可配置、不可关闭。

**核心接口：**
```python
BLOCKLIST_PATTERNS: list[
    re.Pattern
]  # 内置常量，覆盖：rm -rf 根/家目录、dd写块设备、fork炸弹、mkfs.*、重定向覆盖磁盘设备、远程下载即执行等


def hits_blacklist(command: str) -> bool:
    """任一 Pattern 匹配即返回 True"""
```

**依赖：** 无（仅标准库 re）

**关键设计：**
- 仅对 `ToolCategory.COMMAND` 生效，且仅当 `target` 非空时检查
- 非命令执行类工具直接跳过此层
- 模块文档顶部声明「启发式、非完备、不可配置放开」（N2 安全底线）

### mewcode/permission/sandbox.py — 路径沙箱（L2）

**职责：** 限制文件类工具读写只能落在项目根目录内，解析符号链接防逃逸。

**核心接口：**
```python
def resolve_root(root: str) -> str:
    """规整项目根为绝对路径 + realpath；失败返回原值"""


def eval_symlinks_or_ancestor(abs_path: str) -> str:
    """解析路径：存在→realpath；不存在→逐级向上找最近已存在祖先目录，
    对该祖先 realpath 后再拼接剩余段。覆盖「新建文件含未创建中间目录」场景。"""


def check_path(target_path: str, project_root: str) -> CheckResult:
    """检查目标路径是否在项目根内。越界/无法解析→Deny，通过→Allow"""
```

**依赖：** 无（仅标准库 os/os.path）

**关键设计：**
- 空 path 视为 root
- 相对路径用 `root.resolve(path)` 解析为绝对
- `Path.startswith` 按段比对（避免 `/rootfoo` 误匹配 `/root/foo`）
- 对文件类工具（Read/Write/Edit/Glob/Grep）的 path 参数做检查
- **命令执行工具不调用此层**

**glob/grep 沙箱盲区**（已知限制）：
- `extract_target` 取 glob/grep 的 `path`（搜索根目录）做围栏；`pattern`/`Glob` 字段不参与沙箱
- glob/grep 的遍历由现有 `os.walk` / `glob` 实现（不跟随目录软链接）限制越界遍历
- 沙箱对 glob/grep 为**尽力围栏搜索根**，登记为已知盲区

### mewcode/permission/rules.py — 规则加载与分层（L3）

**职责：** 从 YAML 文件加载三层规则，提供统一的匹配入口。

**核心接口：**
```python
RULE_FILE_LOCAL = ".mewcode/permissions.local.yaml"  # 本地级
RULE_FILE_PROJECT = ".mewcode/permissions.yaml"  # 项目级
RULE_FILE_USER = "~/.config/mewcode/permissions.yaml"  # 用户级


def load_settings(filepath: str) -> dict:
    """加载单个 YAML 文件；文件不存在→{}；格式错误→{} + 打印警告"""


def load_rules(project_root: str) -> RuleLayers:
    """加载三层规则文件，任一缺失/格式错误→降级为空规则集"""


def parse_rule(raw: str, action: str, source: str) -> Rule | None:
    """解析单条规则字符串；非法条目返回 None（调用方 skip）"""


def build_rule_set(entries: list[str], action: str, source: str) -> RuleSet:
    """从 YAML 的 allow/deny 列表构建 RuleSet，跳过非法条目"""


def match_pattern(pattern: str, target: str) -> bool:
    """glob 匹配：'' 恒匹配；* 匹配任意字符（含空格）；** 仅文件路径跨段"""
```

**依赖：** yaml（pyyaml，已在项目依赖中）、fnmatch（标准库）

**关键设计：**
- 规则格式 `工具名(模式)`：正则提取工具名和括号内模式
- 无参工具（如 `Glob`）不带括号，pattern 为空字符串，匹配所有参数
- 加载顺序：user → project → local（更高层覆盖低层，但仅在 match 时按 local→project→user 顺序查找）
- 每个文件 try/except 加载，失败→空规则集 + 打印警告
- 不存在的文件静默视为空

### mewcode/permission/engine.py — 规则引擎（L3）

**职责：** 对一次工具调用匹配三层规则，返回 allow/deny/None。

**核心接口：**
```python
class RuleEngine:
    def __init__(self, layers: RuleLayers): ...

    def match(self, friendly: str, target: str) -> CheckResult | None:
        """跨层查找：local → project → user，首命中即返回。
        返回 None 表示未命中。"""
```

**依赖：** rules.py

### mewcode/permission/modes.py — 权限模式矩阵（L4）

**职责：** 根据当前模式和工具类别返回兜底裁决（Allow/Ask，绝不 Deny）。

**核心接口：**
```python
MODE_MATRIX: dict[PermissionMode, dict[ToolCategory, Decision]] = {
    PermissionMode.DEFAULT: {READONLY: ALLOW, FILE_WRITE: ASK, COMMAND: ASK},
    PermissionMode.ACCEPT_EDITS: {READONLY: ALLOW, FILE_WRITE: ALLOW, COMMAND: ASK},
    PermissionMode.PLAN: {READONLY: ALLOW, FILE_WRITE: ASK, COMMAND: ASK},
    PermissionMode.BYPASS: {READONLY: ALLOW, FILE_WRITE: ALLOW, COMMAND: ALLOW},
}


def resolve_mode(mode: PermissionMode, category: ToolCategory) -> Decision:
    """查表返回 Allow 或 Ask"""
```

**依赖：** 无（纯数据）

### mewcode/permission/hitl.py — 人在回路数据结构（L5）

**职责：** 定义 HITL 请求/响应的数据结构，不包含 UI 逻辑。

```python
@dataclass
class HITLRequest:
    tool_name: str
    params_preview: str
    reason: str


@dataclass
class HITLResponse:
    action: Literal["allow_once", "allow_always", "deny"]
```

**依赖：** 无（纯数据）

### mewcode/permission/checker.py — 权限检查器（串联入口 + 参数提取 + 工具分类）

**职责：** 串联前四层防线，输出最终 CheckResult；内置参数提取与工具分类逻辑。是 Agent 调用的唯一入口。

**核心接口：**
```python
class PermissionChecker:
    def __init__(self, project_root: str, mode: PermissionMode, layers: RuleLayers): ...

    @staticmethod
    def create(project_root: str) -> "PermissionChecker":
        """工厂方法：解析项目根、加载三层配置、编译黑名单、确定启动模式。
        即使致命错也返回非 null 空规则安全引擎 + stderr 警告；
        配置格式错误只降级对应文件，不抛致命异常。"""

    def check(self, tool_call: ToolCall, is_interactive: bool = True) -> CheckResult:
        """前四层流水线：
        ① 分类 → extract_target → 黑名单(仅COMMAND+非空)
        ② 沙箱(仅文件类，ok==False → Deny)
        ③ 规则引擎(local → project → user)
        ④ 模式兜底 → Allow/Ask
        bypassPermissions 跳过③④（规则引擎和权限模式；HITL 由Agent层跳过）"""

    def set_mode(self, mode: PermissionMode) -> None: ...
    def persist_local_allow(self, tool_call: ToolCall) -> None: ...

    @property
    def mode(self) -> PermissionMode: ...
    @property
    def start_mode(self) -> PermissionMode: ...
```

**依赖：** blocklist.py、sandbox.py、engine.py、modes.py、hitl.py、json（标准库）

**关键设计：**

**`categorize`（工具分类）优先级：**
1. `is_read_only == True` → `READONLY`（优先于名字判定）
2. 工具名 `write_file` / `edit_file` → `FILE_WRITE`
3. 其余（含 `Bash`、未知工具）→ `COMMAND`（最严）

**`extract_target`（参数提取）通过 JSON 解析 `ToolCall.input`：**
- `read_file` / `write_file` / `edit_file` → 取 `path`（is_file=true）
- `glob` / `grep` → 取 `path`（搜索根目录，空→`"."`，is_file=true）
- `Bash` → 取 `command`（is_file=false）
- 未知工具 → `TargetInfo("", false, false)`
- **JSON 解析失败或缺必填字段 → `ok=False`**

**`check` 流水线（短路）：**
1. 分类 + 提取 target
2. `cat == COMMAND && target != "" && hits_blacklist(target)` → `DENY`
3. 文件类：`!ok` → `DENY`；`!sandbox_ok(target)` → `DENY`
4. 规则引擎 `local → project → user` 匹配，命中 → `ALLOW`/`DENY`
5. `mode_fallback(mode, cat)` → `ALLOW` 或 `ASK`
6. bypassPermissions 跳过 4/5（规则引擎和权限模式；HITL 由Agent层跳过）

**`persist_local_allow`（人在回路「永久」调用）：**
- 据 `extract_target` + `friendly_name` 生成**精确**规则（无通配）
- Bash 命令串经 `escape_glob` 转义字面 `*`/`?`/`[`/`]` 防止规则被泛化
- 读 `local_path`（缺失则空）→ 追加规则串到 `permissions.allow`（去重）→ 用 YAML dump 写回
- `create_directories` 确保父目录存在
- 失败仅抛 `IOError`（调用方捕获只记不阻断）

### 工具分类与友好名映射（Registry 扩展）

**职责：** 在现有 Registry 中新增工具分类和友好名映射。

```python
# mewcode/tools/registry.py

FRIENDLY_NAME_MAP: dict[str, str] = {
    "Bash": "execute_command",
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "edit_file",
    "Glob": "search_code",
    "Grep": "search_code",
}

INTERNAL_TO_FRIENDLY: dict[str, str] = {v: k for k, v in FRIENDLY_NAME_MAP.items()}


def get_friendly_name(self, internal: str) -> str:
    """内部名 → 友好名；未知原样返回"""


def get_category(self, internal: str) -> ToolCategory:
    """read_only==True → READONLY；write_file/edit_file → FILE_WRITE；其余 → COMMAND"""
```

### Agent 集成（agent.py 修改）

**修改点：** 在 `Agent.run` 中 `scheduler.schedule` 之前插入权限检查；承载第五层人在回路。

```python
# Agent.__init__ 新增参数
def __init__(self, ..., permission: PermissionChecker, is_interactive: bool = True):
    self._permission = permission
    self._interactive = is_interactive
    self._hitl_event = asyncio.Event()
    self._hitl_response: HITLResponse | None = None

# 新增方法供 TUI 调用
def resolve_hitl(self, response: HITLResponse) -> None:
    self._hitl_response = response
    self._hitl_event.set()

# run() 中 known_calls 执行前插入权限检查
for tc in known_calls:
    result = self._permission.check(tc, is_interactive=self._interactive)
    if result.decision == Decision.DENY:
        # 产 TOOL_CALL + TOOL_RESULT(error) 事件，写入历史，不执行
        yield Event(EventType.TOOL_CALL, tc)
        tr = ToolResult(status="error", error=result.reason)
        yield Event(EventType.TOOL_RESULT, tr)
        self.conv.add_tool_result(tc, tr)
    elif result.decision == Decision.ASK:
        if not self._interactive:
            # 非交互：直接转为 DENY
            yield Event(EventType.TOOL_CALL, tc)
            tr = ToolResult(status="error", error=result.reason)
            yield Event(EventType.TOOL_RESULT, tr)
            self.conv.add_tool_result(tc, tr)
        else:
            # 交互：发 HITL 事件阻塞等待
            self._hitl_event.clear()
            request = HITLRequest(
                tool_name=self.registry.get_friendly_name(tc.tool_name),
                params_preview=self._preview_args(tc.arguments),
                reason=result.reason,
            )
            yield Event(EventType.HITL_REQUEST, request)
            await self._hitl_event.wait()
            self._hitl_event.clear()
            response = self._hitl_response
            if response.action == "deny":
                yield Event(EventType.TOOL_CALL, tc)
                tr = ToolResult(status="error", error="用户拒绝")
                yield Event(EventType.TOOL_RESULT, tr)
                self.conv.add_tool_result(tc, tr)
            else:
                # allow_once / allow_always
                if response.action == "allow_always":
                    try:
                        self._permission.persist_local_allow(tc)
                    except IOError:
                        pass  # 仅记不阻断
                allowed_calls.append(tc)
    else:
        # ALLOW → 正常执行
        allowed_calls.append(tc)

# 对 allowed_calls 调用 scheduler.schedule()（现有逻辑不变）
# 按原始顺序产 TOOL_CALL + TOOL_RESULT 事件（与被拒调用交叉，保序）
```

**关键设计：**
- Deny 的处理不触发 `_unknown_streak` 计数（与 unknown 工具区分）
- **只读批 Deny 保序**：Deny 项也发 TOOL_CALL + TOOL_RESULT 事件，按调用序，与被拒的副作用调用行为一致
- 只读批 Deny 调用**不纳入并发执行**（同批其他只读仍并发）
- 取消路径：`cancel()` 方法中设置 `_hitl_event` 避免 HITL 挂起时死锁
- plan 模式：暴露全部工具定义（移除 `read_only_definitions()` 调用），移除硬性只读过滤

### TUI 集成（app.py 修改）

**修改点：**

1. **HITL 确认框**：收到 `HITL_REQUEST` 事件时展示多行确认块
```python
elif event.type == EventType.HITL_REQUEST:
    req = event.payload
    # 多行渲染：● 动作名 + 缩进参数预览 + 灰字原因 + 三行菜单
    response = await self._ask_choice(
        f"待批准: {req.tool_name}\n{req.params_preview}\n{req.reason}",
        [
            ("allow_once", "允许本次"),
            ("allow_always", "永久允许（写入本地配置）"),
            ("deny", "拒绝本次"),
        ],
        default_index=0,  # 默认高亮「允许本次」
    )
    self.agent.resolve_hitl(HITLResponse(action=response))
```

2. **HITL 交互细节**：
   - ↑↓/j/k 循环移动光标，Enter/空格提交当前项
   - 数字键 1/2/3 直选（1=允许本次, 2=永久, 3=拒绝本次）
   - 便捷键：`y`=允许本次，`n`/`d`=拒绝本次
   - 底部灰字提示：`↑↓ 选择 · 回车确认 · Esc 取消`

3. **Shift+Tab 切换**：仅 IDLE 态生效，循环四档（含 bypass）
```python
modes = list(PermissionMode)
next_idx = (modes.index(current) + 1) % len(modes)
self.agent._permission.set_mode(modes[next_idx])
# 切换后在 scrollback 追加 noticeLabel 提示新模式
```

4. **状态栏**：左侧常驻显示当前权限模式（取代 provider 名）
   - DEFAULT→`DEFAULT`（灰/绿）、ACCEPT_EDITS→`ACCEPT EDITS`、PLAN→`PLAN`（黄）、BYPASS→`BYPASS`（红）
   - 右侧模型名 + token 用量不变

5. **HITL 可取消（blocker 修复）**：全局 Ctrl+C/Esc 分派条件从 `STREAMING` 扩展到 `STREAMING || APPROVING`
   - approving 态取消时先 `agent.resolve_hitl(HITLResponse(action="deny"))` 兜底解阻塞
   - 再走 `cancel_turn()` 收尾
   - 不泄漏 task、不死锁、不退出程序

### 配置系统扩展

**schema.py 新增：**
```python
@dataclass
class Config:
    # ... 现有字段 ...
    permission_mode: str = "default"  # 启动默认权限模式
```

**loader.py 新增：**
```python
def load_permission_rules(project_root: str) -> RuleLayers:
    """加载三层规则文件，供 PermissionChecker.create 使用"""
```

**main.py 新增：**
```python
parser.add_argument(
    "--mode", type=str, choices=["default", "acceptEdits", "plan", "bypassPermissions"]
)
```

### 文件操作工具适配

**file_ops.py 修改：**
- `_check_path` 更新为调用 `sandbox.check_path` 做符号链接感知的沙箱检查
- 保留 `_check_path` 作为防御性内联检查（双重保险），但主要沙箱逻辑已收敛到 `permission/` 包

### Shell 工具适配

**shell.py 修改：**
- 移除 `_WHITELIST` 常量
- 移除 `_get_command_token` 白名单检查逻辑
- 命令执行不再受首 token 白名单限制

### Plan 模式整合

**agent.py 修改：**
- plan 模式在 `run()` 中保持 ch04.5 行为：暴露全部工具定义，由 SystemPrompt 引导自觉只读
- plan 权限矩阵与 default 一致（只读 Allow，写/命令 Ask），仅作防御性兜底
- `/plan` 和 `defaultMode=plan` 配置均按 `Mode.PLAN` 应用

**prompt/reminders.py 修改：**
- 新增指令：若为单纯询问无需计划，直接回答，不生成计划文件

### 权限配置示例文件

**`.mewcode/permissions.yaml.example`（新建）：**
```yaml
# 权限规则配置示例
# 三层文件：用户级 ~/.config/mewcode/permissions.yaml
#           项目级 .mewcode/permissions.yaml（可入库团队共享）
#           本地级 .mewcode/permissions.local.yaml（个人本地，建议 gitignore）

defaultMode: default

permissions:
  allow:
    - "Bash(git *)"
    - "Bash(./gradlew test)"
  deny:
    - "Bash(rm *)"
    - "Read(.env)"
    - "Write(.env)"
```

## 模块交互

### 工具调用全流程（权限系统视角）

```
1. Agent.run 收到模型 tool_use
2. 分类 known/unknown（现有逻辑不变）
3. 对每个 known_call：
   a. PermissionChecker.check(tool_call, is_interactive)
   b. 内部分类 + extract_target
   c. L1→L2→L3→L4 短路判定
   d. 若 DENY：产 TOOL_CALL + TOOL_RESULT(error) 事件 + 写入历史，不执行
   e. 若 ALLOW：加入 allowed_calls 队列
   f. 若 ASK（交互）：
      - yield HITL_REQUEST 事件
      - await asyncio.Event
      - TUI 展示确认框，用户选择
      - TUI 调 agent.resolve_hitl(response) 设置 Event
      - deny → 产 error 事件；allow_once → 加入 allowed_calls；allow_always → persist + 加入 allowed_calls
   g. 若 ASK（非交互）：直接转为 DENY
4. 对 allowed_calls 调用 scheduler.schedule()（现有逻辑不变）
5. 按原始顺序产 TOOL_CALL + TOOL_RESULT 事件（与被拒调用交叉，保序）
```

### HITL 确认框交互流程

```
Agent (agent.py)                    TUI (app.py)
     │                                  │
     ├─ yield HITL_REQUEST ────────────→│ 收到事件
     │                                  ├─ 切 APPROVING 态
     │  await _hitl_event.wait()        │ 渲染多行确认块：
     │  (阻塞)                          │   ● Bash(git push)
     │                                  │     参数预览...
     │                                  │     原因：default 模式下命令执行需确认
     │                                  │   > 1. 允许本次
     │                                  │     2. 永久允许（写入本地配置）
     │                                  │     3. 拒绝本次
     │                                  │   ↑↓ 选择 · 回车确认 · Esc 取消
     │                                  │ 用户操作（↑↓/数字键/y/n/Enter/Esc）
     │                                  ├─ 构造 HITLResponse
     │←─ resolve_hitl(response) ───────┤
     │  _hitl_event.set()              │
     │                                  │
     ├─ 处理响应                        │
     ├─ allow_once → 执行工具           │
     ├─ allow_always → persist + 执行   │
     └─ deny → 产 error 回灌            │
```

### 规则文件加载流程

```
main.py 启动
  ├─ 加载 Config（现有）
  ├─ PermissionChecker.create(project_root)
  │   ├─ resolve_root(project_root) → 规整项目根
  │   ├─ 加载用户级 ~/.config/mewcode/permissions.yaml
  │   │   ├─ 存在 → 解析 YAML → RuleSet
  │   │   └─ 不存在 → 空 RuleSet
  │   ├─ 加载项目级 .mewcode/permissions.yaml
  │   │   ├─ 存在 → 解析 → 合并到 RuleLayers
  │   │   └─ 不存在/格式错误 → 空 RuleSet（降级）
  │   ├─ 加载本地级 .mewcode/permissions.local.yaml
  │   │   ├─ 存在 → 解析 → 合并（最高层）
  │   │   └─ 不存在 → 空 RuleSet
  │   ├─ start_mode：依次取 local/project/user 的 defaultMode
  │   │   （local 优先），皆无 → DEFAULT
  │   └─ 返回 PermissionChecker（永不 None）
  └─ 构造 Agent(..., permission=checker)
```

## 文件组织

```
mewcode/
├── permission/                          ← 新建
│   ├── __init__.py                     — 公开 API（Decision, PermissionMode, PermissionChecker, RuleLayers 等）
│   ├── blocklist.py                    — 黑名单正则常量（L1）
│   ├── sandbox.py                      — 路径沙箱 + 祖先回退（L2）
│   ├── rules.py                        — 规则解析、加载、三层合并、match_pattern（L3）
│   ├── engine.py                       — 规则引擎匹配（L3）
│   ├── modes.py                        — 权限模式矩阵 + PermissionMode 枚举（L4）
│   ├── hitl.py                         — HITL 请求/响应数据结构（L5）
│   └── checker.py                      — 权限检查器串联入口 + categorize + extract_target
├── agent/
│   ├── agent.py                        — 修改：插入权限检查 + HITL 阻塞等待 + resolve_hitl
│   ├── events.py                       — 修改：新增 HITL_REQUEST 事件类型
│   └── scheduler.py                    — 不变
├── tools/
│   ├── registry.py                     — 修改：新增友好名映射 + 工具分类
│   ├── shell.py                        — 修改：移除 _WHITELIST 白名单
│   ├── file_ops.py                     — 修改：_check_path 对齐沙箱
│   └── search.py                       — 不变
├── config/
│   ├── schema.py                       — 修改：新增 permission_mode 字段
│   └── loader.py                       — 修改：新增 load_permission_rules
├── tui/
│   └── app.py                          — 修改：HITL 确认框、Shift+Tab、状态栏、全局取消覆盖 APPROVING
├── prompt/
│   └── reminders.py                    — 修改：plan 模式提醒新增「单纯询问直接回答」
└── main.py                             — 修改：--mode 参数、权限系统初始化

.mewcode/
└── permissions.yaml.example            — 新建：权限配置示例

tests/
├── test_permission_blocklist.py        — 新建：黑名单测试
├── test_permission_sandbox.py          — 新建：路径沙箱测试（含祖先回退/符号链接逃逸）
├── test_permission_rules.py            — 新建：规则解析、匹配、加载降级测试
├── test_permission_engine.py           — 新建：规则引擎三层匹配测试
├── test_permission_checker.py          — 新建：权限检查器串联 + categorize + extract_target 测试
└── test_permission_tui.py              — 新建：TUI HITL 交互 + 模式切换测试（mock 驱动）
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 权限判定落点 | 独立 permission 包(前四层) + agent 编排层(第五层) | 与 provider 解耦（N10 跨协议一致免费）；逻辑内聚、可单测 |
| 五层短路 | check 顺序 黑名单→沙箱→规则→模式 单方法 early-return；Ask 作第五层信号 | 满足 F1；黑名单/沙箱按类别跳过；规则就近命中即返回 |
| `create` 永不返回 None | 致命错也返回非 null 空引擎 + stderr 警告 | Main 注入永不为 null、check 不抛 NPE；配置格式错只降级不致错 |
| 黑名单不可配 | 代码内硬编码常量列表 | 任何配置/模式都碰不到；bypass 也拦 |
| 黑名单完备性 | 启发式，模块文档显式声明非完备 | 不可能穷尽危险命令；防御纵深由沙箱+规则+人在回路补 |
| 沙箱解析顺序 | 先 realpath（或最近祖先）再 startswith 比对 | 防软链接逃逸；新建文件按已存在祖先判，避免误判 |
| 沙箱不管命令执行 | Bash 不做路径围栏 | 无法可靠静态解析任意命令的文件访问；交黑名单+规则+模式 |
| glob/grep 沙箱盲区 | 只围栏搜索根 path，pattern 不参与沙箱 | 已知限制；工具内部遍历不跟随目录软链接作补充 |
| 参数解析失败归属 | 文件类不可解析→Deny；bash 缺 command→空串落 Ask；未知工具→EXEC/Ask | N11 安全默认，绝不静默 Allow |
| 工具分类优先级 | readOnly 属性优先于名字判定 | 防御性：标记只读的工具永远不会被误判为有副作用 |
| 模式切换 | Shift+Tab 循环四档（含 bypass）；仅 IDLE 态生效 | 用户拍板；/plan·/do 保留计划工作流语义 |
| 状态栏 | 左侧常驻权限模式，取代 provider 名 | 用户拍板；右侧模型名+用量不变 |
| plan 语义 | 沿用 ch04.5 全部工具定义 + SystemPrompt 引导；矩阵 plan 行仅防御兜底 | 用户拍板；/plan 与 defaultMode=plan 都按 Mode.PLAN 应用 |
| 模式兜底值域 | 只产 Allow/Ask（无 Deny 档） | Deny 仅来自黑名单/沙箱/deny 规则/人在回路拒绝 |
| 规则优先级 | 本地>项目>用户；同层 deny 优先 allow | 越靠近项目越优先；deny 优先更安全 |
| 规则名 | 友好名 Bash/Read/Write/Edit/Glob/Grep ↔ 内部名映射 | 对齐 Claude Code 习惯，规则更可读 |
| 规则匹配 | fnmatch（Python 标准库） | 零额外依赖，支持 `*` 和 `**` 通配 |
| 规则文件格式 | YAML，permissions: {allow: [...], deny: [...]} | 与 Claude Code 兼容 |
| 规则文件路径 | 三层：用户/项目/本地 | 本地级可 .gitignore，不进 git |
| 永久放行落点 | 写本地层 .mewcode/permissions.local.yaml | 不进 git、不影响队友 |
| 自动规则泛化 | 不泛化，只生成精确规则 | 自动猜泛化模式有误放行风险；泛化交用户手写 |
| 永久放行实现 | escapeGlob 转义 + 去重 + createDirectories | 防止命令中的 `*`/`?` 被误当通配；幂等安全 |
| 人在回路选项集 | 三选一（允许本次/永久/拒绝）+ 默认高亮允许本次 | 1:1 复刻 Claude Code |
| 人在回路交互 | ↑↓+回车+数字键 1/2/3 + 便捷键 y/n/d | 多模态输入，确保可访问性 |
| HITL 阻塞机制 | asyncio.Event + Agent.resolve_hitl() | 不破坏事件流单向性；Event 天然适合「暂停→唤醒」 |
| 取消兜底 | 全局 Ctrl+C/Esc 覆盖 APPROVING；先 offer deny 再 cancel_turn | 否则 approving 态 Ctrl+C 走默认 quit handler 退出程序 |
| 拒绝不终止 | Deny 产 error 回灌，不触发 _unknown_streak 计数 | 模型有机会调整策略 |
| 只读并发保持 | 只读永不 Ask，权限检查恒为 Allow/Deny | 不影响 scheduler 的并发/串行判断 |
| 非交互检测 | Agent 构造时传入 is_interactive 参数 | 简单可靠，不依赖 TTY 检测 |
| 跨协议一致 | 权限检查在 agent 编排层，不触及 provider | 架构天然隔离 |
| 配置降级 | try/except 包裹每个规则文件加载 | 安全默认，不崩溃，不额外放开权限 |

## spec 覆盖检查

| F 需求 | 对应模块 |
|--------|---------|
| F1 权限检查器 | checker.py（前四层串联） |
| F2 黑名单 | blocklist.py + checker.py（L1 短路） |
| F3 路径沙箱 | sandbox.py + checker.py（L2 短路，含祖先回退） |
| F4 规则引擎 | engine.py + rules.py |
| F5 规则分层 | rules.py（RuleLayers）+ checker.py |
| F6 权限模式 | modes.py + checker.py（set_mode） |
| F7 HITL | hitl.py + agent.py + app.py |
| F8 拒绝不终止 | agent.py（Deny 跳过 _unknown_streak） |
| F9 plan 整合 | agent.py（mode 参数）+ reminders.py |
| F10 非交互 | checker.py（is_interactive）+ main.py |
| F11 模式切换 | app.py（Shift+Tab + 状态栏 + 跨轮保持） |