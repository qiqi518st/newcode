# MewCode ch07 — MCP 对接数据库的权限管理 Plan

## 架构概览

在 ch07 MCP 客户端之上增加**数据库权限预检**：对声明了权限预检的 DB 型 MCP server，按其连接账号从远端取得的授权快照，生成三路判定——① 高危操作拦截（L1）→ ② 资源边界（L2）→ ③ 操作权限（L3）。判定严格按此顺序，任一命中即拒绝（bypass 模式也生效）。

```
工具调用（mcp__<server>__<tool>, arguments）
   │
   ▼ ① L1 高危检查（checker.py 扩展）
   │    自由 SQL 工具：SQL 文本匹配内置高危名单
   │    结构化工具   ：危险判定参数（where）为空 → 高危
   │    命中 → DENY（bypass 也拦）
   │
   ▼ ② L2 资源检查（checker.py 扩展 + resource_scope.py）
   │    结构化工具：table 参数直取库.表
   │    自由 SQL   ：SQL 规范化清洗 → 提取表引用
   │    不在授权资源集 → DENY（bypass 也拦）
   │
   ▼ ③ L3 操作权限（rules.py dynamic 内存层 + inject_rules）
   │    工具所需操作不在快照 → deny 规则（动态注入）
   │    LLM 可见性：禁用工具从 to_definitions 过滤
   │
   ▼ 既有 L4/L5 + 执行
```

组件划分：
- **`mewcode.permission` 扩展**：`mcp_blocklist`（高危名单）、`resource_scope`（资源边界 + SQL 规范化）、`rules.RuleLayers.dynamic`（内存动态层）、`checker`（L1/L2 扩展 + 注入 API）。
- **`mewcode.mcp` 扩展**：`privilege`（快照获取/翻译/Guard）、`config`（permissions 声明）、`conn`（认证失败标记）。
- **`mewcode.tools.registry`**：工具可见性过滤。
- **`mewcode.main`**：装配。

## 核心数据结构

### MCPServerPermissions（mewcode/mcp/config.py）

```python
@dataclass
class MCPServerPermissions:
    probe_tool: str = ""        # 远端权限查询工具名（拿快照）
    sql_arg: str = ""           # 自由 SQL 工具的参数名（SQL 文本）
    table_arg: str = ""         # 专用工具的表参数名（资源直取）
    where_arg: str = ""         # 专用工具的危险判定参数（空=无 WHERE）
    privilege_map: dict[str, str] = field(default_factory=dict)  # 工具→所需操作，覆盖内置
```

`ServerConfig` 增加 `permissions: MCPServerPermissions | None`。

### PrivilegeSnapshot（mewcode/mcp/privilege.py）

```python
@dataclass
class PrivilegeSnapshot:
    grants: set[str]          # 操作集：SELECT/INSERT/UPDATE/DELETE/EXECUTE...
    dbs: set[str]             # 授权库
    tables: dict[str, set[str]]  # 每库授权表（"*" 通配全部）
    default_db: str           # 连接默认库（补全裸表名）
    read_only: bool
```

### ResourceScope（mewcode/permission/resource_scope.py）

```python
@dataclass
class ResourceScope:
    dbs: set[str]                     # 授权库（通配全部表）
    tables: dict[str, set[str]]       # db → 授权表集（"*" 通配全部表）
```

`Ref = tuple[str, str]` 归一化资源（db, table，全部小写）。

### RuleLayers.dynamic（mewcode/permission/rules.py）

`RuleLayers` 增加 `dynamic: RuleSet`——**独立内存层，不落盘**；`load_rules` 不读它；match 顺序 `dynamic > local > project > user`。

## 模块设计

### mewcode/permission/mcp_blocklist.py（新）
**职责：** 内置高危 SQL 名单（不可配置，对标 `blocklist.py`）。
- `DANGEROUS_SQL_PATTERNS: list[re.Pattern]`：自由 SQL 文本匹配——库表级删除/清空（`DROP DATABASE|TABLE`、`TRUNCATE`）、无条件删除/更新（`DELETE|UPDATE` 后无 `WHERE`，轻量判断：`\bWHERE\b` 是否出现在目标表之后）。
- `hits_dangerous_sql(sql: str) -> bool`：任一命中返回 True。
- `is_unconditional_where(where_arg: str) -> bool`：结构化工具危险判定——where 缺失/空白 → True。

### mewcode/permission/resource_scope.py（新）
**职责：** 资源边界判定 + SQL 规范化清洗。
- `normalize_sql(sql) -> str`：去注释（`--`、`#`、`/* */`）、去字符串字面量、去反引号包裹、多语句按 `;` 分割逐句、大小写归一——**对标 `sandbox.py` 的 realpath/symlink 解析，是 SQL 型资源判定的信任前提**。
- `extract_table_refs(sql, default_db) -> set[Ref]`：从清洗后 SQL 提取 `FROM/JOIN/INTO/UPDATE/DELETE/TRUNCATE/ALTER TABLE` 后的库表引用；裸表名用 default_db 补全为绝对资源。
- `extract_table_arg(arg, default_db) -> Ref | None`：结构化工具 table 参数直取（`db.table` / `db.*` / 裸表名）。
- `check_resource(ref, scope) -> bool`：资源 ∈ 授权集（`db.*` 或裸库通配全表）——对标 `startswith(root + os.sep)` 前缀比较。

### mewcode/permission/rules.py（改）
- `RuleLayers` 加 `dynamic: RuleSet`；`match` 顺序改为 dynamic→local→project→user。

### mewcode/permission/checker.py（改）
- 构造参数增加：`mcp_sql_args: dict[str, str]`（server→自由 SQL 参数名）、`mcp_danger_args: dict[str, str]`（server→危险判定参数名）、`mcp_table_args: dict[str, str]`（server→表参数名）、`mcp_resource_scopes: dict[str, ResourceScope]`（server→授权资源）。**permission 不 import mewcode.mcp**。
- **L1 扩展**（在既有 bash 黑名单之后、BYPASS 判断之前）：tool 以 `mcp__` 开头且属于预检 server → 取对应参数（sql_arg 文本 or where_arg）→ `hits_dangerous_sql` / `is_unconditional_where` 命中 → DENY。
- **L2 扩展**：预检 server 且非只读工具 → 提取资源（table 参数 or SQL 文本）→ `check_resource` 不通过 → DENY。
- **公开方法**：`inject_rules(rules, layer="dynamic")`（append 到 `_layers.dynamic`，实时生效）、`clear_dynamic_rules()`（清空 dynamic，供刷新时先清再注入）。

### mewcode/mcp/config.py（改）
- `ServerConfig` 加 `permissions: MCPServerPermissions | None`；YAML `permissions` 段解析。

### mewcode/mcp/privilege.py（新）
**职责：** 权限快照获取、翻译、Guard。
- `fetch_snapshot(conn, probe_tool, default_db) -> PrivilegeSnapshot | None`：调 `conn.call_tool(probe_tool, {})`，解析远端返回（约定结构化格式含 grants/dbs/tables/read_only；default_db 从配置 env 或快照取）；解析失败 → stderr 告警一次 + None（不阻断）。
- `translate_to_rules(server_name, tools, snapshot, privilege_map) -> list[Rule]`：内置「工具→所需操作」映射（基于 mysql-mcp-server 真实工具：mysql_insert↔INSERT、mysql_update↔UPDATE、mysql_delete↔DELETE、mysql_query↔EXECUTE/SELECT；只读类 listTables/describeTable/sampleData/summarizeTable/tableRelations/listIndexes/listDatabases/explain/ping/version/generateSchemaDiagram↔SELECT/SHOW）；快照缺操作 → deny 规则 `mcp__<server>__<tool>`；privilege_map 覆盖内置。
- `translate_to_scopes(snapshot) -> ResourceScope`。
- `PrivilegeGuard`：持 `PermissionChecker` 引用 + server 配置。
  - `refresh_all()`：启动时对启用 probe 的 server 查快照 → `clear_dynamic_rules()` 后 `inject_rules` 全部规则；产出 `disabled_tools: set[str]` 与 `scopes: dict[str, ResourceScope]`。
  - `on_call_failed(server_name)`：调用失败（远端拒绝）→ 重查该 server 快照 → 更新规则。

### mewcode/mcp/conn.py（改）
- `call_tool` 远端错误含认证失败特征（`Access denied for user` / `ER_ACCESS_DENIED`）→ 置 `self._auth_failed = True`；后续调用直接返回 `ToolResult(status="error", error="该 server 凭据异常，已停止重试...")`，stderr 告警一次。

### mewcode/tools/registry.py（改）
- `to_definitions(disabled: set[str] | None = None)`：过滤 disabled 中的工具，不进入 LLM 可见列表。

### mewcode/main.py（改）
- `load_mcp_servers` 后构建 `mcp_sql_args`/`mcp_danger_args`/`mcp_table_args` 传入 `PermissionChecker`；`start_all` 后 `guard.refresh_all()` → 把 `guard.scopes` 传给 checker、`disabled_tools` 给 agent 组装工具列表、边界摘要进 `env_segment`；`McpTool.execute` 失败路径接 `guard.on_call_failed`。

## 模块交互

```
main._amain
  ├─ load_mcp_servers → servers（含 permissions 声明）
  ├─ PermissionChecker(..., mcp_sql_args, mcp_danger_args, mcp_table_args, mcp_resource_scopes={} 初始空)
  ├─ MCPManager.start_all() → connections + tools
  ├─ PrivilegeGuard(checker, servers, connections)
  │    └─ refresh_all()
  │         ├─ 对每个 probe server: fetch_snapshot
  │         ├─ translate_to_rules → clear_dynamic_rules + inject_rules
  │         ├─ translate_to_scopes → scopes → checker.mcp_resource_scopes
  │         └─ disabled_tools → 装配时给 agent 过滤 to_definitions
  ├─ 边界摘要（read_only/限库）→ env_segment
  ├─ agent 运行：每轮 to_definitions(disabled=...) + checker.check（L1→L2→L3→L4→L5）
  │    └─ McpTool.execute 失败 → guard.on_call_failed(server) → 重查快照更新
  └─ finally: await mcp_mgr.close()
```

## 文件组织

```
mewcode/
├── permission/
│   ├── mcp_blocklist.py   — 新：高危 SQL 名单（自由 SQL 文本 + 结构化 where 空判定）
│   ├── resource_scope.py  — 新：ResourceScope / SQL 规范化 / 表引用提取 / check_resource
│   ├── rules.py           — 改：RuleLayers.dynamic 内存层，match 顺序 dynamic>local>project>user
│   └── checker.py         — 改：L1/L2 扩展 + inject_rules/clear_dynamic_rules + mcp_* 构造参数
├── mcp/
│   ├── config.py          — 改：ServerConfig.permissions（MCPServerPermissions）
│   ├── privilege.py       — 新：PrivilegeSnapshot/fetch_snapshot/translate_to_rules/translate_to_scopes/PrivilegeGuard
│   ├── conn.py            — 改：_auth_failed 认证失败标记 + 抑制重试
│   └── __init__.py        — 改：导出 PrivilegeGuard
├── tools/registry.py      — 改：to_definitions(disabled) 过滤
└── main.py                — 改：装配 guard/scopes/disabled/边界摘要/失败钩子

tests/
├── test_mcp_blocklist.py  — 新：高危 SQL 正则各分支 + where 空判定
├── test_resource_scope.py — 新：SQL 规范化（注释/字符串/反引号/多语句/大小写）+ 表引用提取 + check_resource 越界
├── test_mcp_privilege.py  — 新：快照解析/翻译规则/翻译 scopes/guard 注入/刷新/认证抑制
└── test_permission_*.py   — 改：checker L1/L2 扩展用例、dynamic 层用例（或并入上述）
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 判定顺序 | 严格 ① 高危(L1) → ② 资源(L2) → ③ 操作(L3)，任一命中即拒 | 与用户确认；与五层 L1→L2→L3 一致；高危先拦避免越权资源判断 |
| 工具二分 | 结构化参数工具（table/where 直取）vs 自由 SQL 工具（文本解析） | 基于 mysql-mcp-server 真实 schema 探查：专用工具不传完整 SQL，query 是 any SQL |
| 高危名单 | 内置、不可配置、bypass 也拦 | 对标 ch06 bash 黑名单定位；用户要"大范围破坏性操作不允许出 agent" |
| 动态规则存储 | RuleLayers.dynamic 独立内存层，inject_rules 只改内存不写文件 | 用户确认：不落盘、不污染权限文件；换账号重启重生成 |
| dynamic 优先级 | dynamic > local > project > user | 账号无权限是硬限制，不应被手写规则覆盖 |
| 刷新时机 | 启动时 + 调用失败时 | 用户确认；性能与权限变更生效的平衡 |
| SQL 规范化 | 去注释/字符串/反引号/多语句/大小写，对标 sandbox realpath | 用户确认：SQL 先整理干净再对比，防绕过 |
| 裸表名补全 | 用连接默认库补全为绝对资源 | 对标 resolve_root 绝对化 |
| LLM 感知 | 禁用工具从 to_definitions 过滤 + 边界摘要进 env_segment | 工具级 deny 剔除可见性；内容级用提示词引导；安全靠判定层兜底 |
| 认证抑制 | _auth_failed 标记，停止自动重试，告警一次 | 防账号锁定；作用域限该 server |
| 依赖方向 | permission 不 import mewcode.mcp（接收 mcp_* 构造参数）；mcp.privilege → permission | 保持解耦无环 |
| 认证快照格式 | 远端 probe_tool 返回结构化 grants/dbs/tables/read_only | 约定协议，解析失败降级不阻断 |
