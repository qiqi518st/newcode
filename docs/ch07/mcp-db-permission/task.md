# MewCode ch07 — MCP 对接数据库的权限管理 Tasks

> 约定：本项目为 Python，Git Bash 下验证命令先 `export PYTHONIOENCODING=utf-8`。
> 异步测试沿用 repo 约定 `@pytest.mark.anyio`。
> 测试遵循 CLAUDE.md：mock/fake 驱动**真实代码路径**，不依赖真实 DB；凡需真实 mysql-server + mysql-mcp-server 的验证列「待人工验证」。
> 本功能不碰 docs/ 既有文件；四份 spec 文档与 `mcp-servers.example.yaml` 属 ch07 交付物。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新 | `mewcode/permission/mcp_blocklist.py` | 高危 SQL 名单（自由 SQL 文本 + 结构化 where 空判定） |
| 新 | `mewcode/permission/resource_scope.py` | ResourceScope / SQL 规范化 / 表引用提取 / check_resource |
| 改 | `mewcode/permission/rules.py` | RuleLayers.dynamic 内存层 + match 顺序 |
| 改 | `mewcode/permission/checker.py` | L1/L2 扩展 + inject_rules/clear_dynamic_rules + mcp_* 构造参数 |
| 改 | `mewcode/mcp/config.py` | ServerConfig.permissions（MCPServerPermissions） |
| 新 | `mewcode/mcp/privilege.py` | PrivilegeSnapshot/fetch_snapshot/translate_to_rules/translate_to_scopes/PrivilegeGuard |
| 改 | `mewcode/mcp/conn.py` | _auth_failed 认证失败标记 + 抑制重试 |
| 改 | `mewcode/tools/registry.py` | to_definitions(disabled) 过滤 |
| 改 | `mewcode/main.py` | 装配 guard/scopes/disabled/边界摘要/失败钩子 |
| 改 | `mewcode/mcp/__init__.py` | 导出 PrivilegeGuard |
| 新 | `tests/test_mcp_blocklist.py` | 高危 SQL 各分支 + where 空判定 |
| 新 | `tests/test_resource_scope.py` | SQL 规范化 + 表引用提取 + check_resource |
| 新 | `tests/test_mcp_privilege.py` | 快照/翻译/注入/刷新/认证抑制/LLM 可见性 |

---

## T1: 高危 SQL 名单（mcp_blocklist.py）

**文件：** `mewcode/permission/mcp_blocklist.py`、`tests/test_mcp_blocklist.py`
**依赖：** 无
**步骤：**
1. 定义 `DANGEROUS_SQL_PATTERNS: list[re.Pattern]`（自由 SQL 文本）：
   - `DROP DATABASE|TABLE`（含 `DROP TABLE IF EXISTS`）
   - `TRUNCATE`（`TRUNCATE [TABLE] <t>`）
   - `DELETE FROM <t>` 后无 `WHERE`、`UPDATE <t> SET ...` 后无 `WHERE`（轻量判断：提取语句主体，`\bWHERE\b` 未出现在 FROM/目标之后 → 命中）
   - 大小写不敏感
2. `hits_dangerous_sql(sql: str) -> bool`：任一模式命中 True。
3. `is_unconditional_where(where: str) -> bool`：结构化工具 where 缺失/空白 → True（无 WHERE 全表操作）。
4. 写测试 `tests/test_mcp_blocklist.py`（docstring 注明防的 bug，如「DELETE 无 WHERE 全表误删」「注释里藏 DROP 被漏判」）：
   - `DROP TABLE orders`、`drop database test818`、`TRUNCATE orders` → hits True
   - `DELETE FROM orders`、`UPDATE orders SET x=1` → True（无 WHERE）
   - `DELETE FROM orders WHERE id=1`、`UPDATE orders SET x=1 WHERE id=1` → False
   - `SELECT * FROM orders` → False
   - `is_unconditional_where("")`/`None`/`"   "` → True；`is_unconditional_where("id=1")` → False

**验证：** `ruff format --check && ruff check && python -m pytest tests/test_mcp_blocklist.py -q` 全过。

---

## T2: 资源边界（resource_scope.py）

**文件：** `mewcode/permission/resource_scope.py`、`tests/test_resource_scope.py`
**依赖：** 无
**步骤：**
1. `ResourceScope` dataclass：`dbs: set[str]`、`tables: dict[str, set[str]]`。
2. `normalize_sql(sql) -> str`（**对标 sandbox 的 realpath/symlink 解析，SQL 判定的信任前提**）：
   - 去 `/* */` 块注释、`--`/`#` 行注释
   - 去 `'...'`/`"..."` 字符串字面量（替换为占位）
   - 去反引号包裹标识符并还原
   - 多语句 `;` 分割（逐句处理）
   - 大小写归一
3. `extract_table_refs(sql, default_db) -> set[Ref]`：从规范化后 SQL 提取 `FROM/JOIN/INTO/UPDATE/DELETE/TRUNCATE/ALTER TABLE` 后的标识符 → `Ref=(db, table)`（`db.table`、`db.*`、裸表名用 default_db 补全；`*` 用 `"*"` 标记）。
4. `extract_table_arg(arg, default_db) -> Ref | None`：结构化工具 table 参数直取。
5. `check_resource(ref, scope) -> bool`：`(db, *)` 或 `(*, *)` 授权通配；`db.table` 精确；裸库 `dbs` 通配全表；表集含 `"*"` 通配该库全表。
6. 写测试（docstring 注明防的 bug，如「注释藏表引用绕过资源检查」「字符串伪装表名被误判」）：
   - 规范化：`SELECT * FROM a -- DROP TABLE b` → 表引用只含 a；`SELECT * FROM 'evil'` → 不提取 evil；`SELECT * FROM \`orders\`` → orders；`SELECT * FROM test818.a; DROP TABLE test818.b` → 两语句各提取；大小写 `TEST818.ORDERS` → `(test818, orders)`
   - 提取：`SELECT * FROM test818.orders` → `{(test818, orders)}`；`FROM orders` + default_db=test818 → `{(test818, orders)}`
   - check_resource：scope={tables:{"test818":{"orders"}}} → `(test818, orders)` True、`(test818, evil)` False；scope dbs={test818} → 全表 True；`(test818, *)` 授权 → 全表 True

**验证：** `ruff format --check && ruff check && python -m pytest tests/test_resource_scope.py -q` 全过。

---

## T3: 规则引擎 dynamic 内存层（rules.py）

**文件：** `mewcode/permission/rules.py`
**依赖：** 无
**步骤：**
1. `RuleLayers.__init__` 加 `self.dynamic: RuleSet = RuleSet()`。
2. `RuleLayers.match` 遍历顺序改为 `dynamic → local → project → user`。
3. `load_rules` **不读** dynamic（只填 local/project/user，dynamic 留给运行时注入）。

**验证：** `ruff check` 通过；`python -m pytest tests/test_permission_rules.py -q` 全过（原用例不回归——原用例直接构造 RuleLayers 断言 local/project/user 顺序，dynamic 为空不影响）。

---

## T4: checker 注入 API + L1/L2 扩展（checker.py）

**文件：** `mewcode/permission/checker.py`、`tests/test_mcp_privilege.py`（L1/L2 用例）
**依赖：** T1、T2、T3
**步骤：**
1. `PermissionChecker.__init__` 增加可选参数：`mcp_sql_args: dict[str,str]`、`mcp_danger_args: dict[str,str]`、`mcp_table_args: dict[str,str]`、`mcp_resource_scopes: dict[str, ResourceScope]`（默认空 dict；**permission 不 import mewcode.mcp**）。
2. 公开方法 `inject_rules(rules: list[Rule])`：`self._layers.dynamic.allow.extend/deny.extend(rules)`（实时生效，engine 每次实时读 layers）。
3. 公开方法 `clear_dynamic_rules()`：清空 `_layers.dynamic.allow/deny`（供刷新先清再注入）。
4. **L1 扩展**（在既有 bash 黑名单分支之后、BYPASS 判断之前，`checker.py:196` 前）：`tool_call.tool_name` 以 `mcp__` 开头且 `server_name in mcp_sql_args or mcp_danger_args` 时：
   - 自由 SQL 工具：`sql = arguments.get(mcp_sql_args[server])` → `hits_dangerous_sql(sql)` → DENY（原因含「高危 SQL」）
   - 结构化工具：`where = arguments.get(mcp_danger_args[server])` → `is_unconditional_where(where)` → DENY
5. **L2 扩展**：预检 server 且非只读工具时：
   - 结构化工具：`extract_table_arg(arguments.get(table_arg), default_db)`（default_db 需传入——用 `mcp_default_dbs: dict[str,str]` 构造参数）
   - 自由 SQL 工具：`extract_table_refs(sql, default_db)`
   - 任一提取资源 `not check_resource(ref, scope)` → DENY（原因含「资源越界」）
6. 写 L1/L2 用例（并入 `tests/test_mcp_privilege.py` 或新 `tests/test_permission_mcp.py`）：
   - 构造 PermissionChecker 传 mcp_* 参数 + ResourceScope；断言 `mysql_query("DROP TABLE x")` DENY、`mysql_query("SELECT * FROM test818.orders")` 放行（scope 授权）、`mysql_delete(table="test818.evil")` DENY（资源越界）、`mysql_delete(where="")` DENY（高危）、`mysql_delete(where="id=1", table="test818.orders")` 放行。

**验证：** `ruff format --check && ruff check && python -m pytest tests/test_mcp_privilege.py tests/test_permission_rules.py -q` 全过；`python -m pytest tests/test_permission_*.py -q` 全过（五层不回归）。

---

## T5: 配置 schema 扩展（mcp/config.py）

**文件：** `mewcode/mcp/config.py`、`tests/test_mcp_config.py`（追加）
**依赖：** 无
**步骤：**
1. 定义 `MCPServerPermissions` dataclass（probe_tool/sql_arg/table_arg/where_arg/privilege_map，全默认）。
2. `_RawServer` 加 `permissions: dict`（可选）；`_validate_server` 构造 `ServerConfig.permissions: MCPServerPermissions | None`（解析 permissions 段，非法字段兜底为空）。
3. `tests/test_mcp_config.py` 追加：带 `permissions` 段的 server 解析出 MCPServerPermissions；缺 permissions → None；permissions 非法字段 → 兜底不阻断。

**验证：** `ruff format --check && ruff check && python -m pytest tests/test_mcp_config.py -q` 全过。

---

## T6: 权限快照与 Guard（mcp/privilege.py）

**文件：** `mewcode/mcp/privilege.py`、`tests/test_mcp_privilege.py`
**依赖：** T4、T5
**步骤：**
1. `PrivilegeSnapshot` dataclass（grants/dbs/tables/default_db/read_only）。
2. `fetch_snapshot(conn, probe_tool, default_db) -> PrivilegeSnapshot | None`：`result = await conn.call_tool(probe_tool, {})`；解析远端返回（约定格式：JSON 含 `grants`/`dbs`/`tables`/`read_only`，或文本行 `GRANT ... ON db.* TO ...` 形式做基础解析）；解析失败 → stderr 告警 `[mcp-priv] warn: fetch snapshot <server> failed: <e>` + None（不阻断）。
3. `translate_to_rules(server_name, tools, snapshot, privilege_map) -> list[Rule]`：内置「工具→所需操作」映射（见 plan 决策表）；快照 `grants` 不含所需操作 → deny 规则 `mcp__<server>__<tool>`；privilege_map 覆盖。
4. `translate_to_scopes(snapshot) -> ResourceScope`：dbs/tables 翻译。
5. `PrivilegeGuard`：
   - `__init__(checker, servers, connections)`（或 main 传入）；`disabled_tools: set[str]`、`scopes: dict[str, ResourceScope]`
   - `async refresh_all()`：对每个 `server.permissions.probe_tool` 的 server → `fetch_snapshot` → `translate_to_rules`（用该 server 的 tools）→ `checker.clear_dynamic_rules()` 后 `checker.inject_rules(全部 deny 规则)`；`disabled_tools` 收集被 deny 的工具；`scopes[server] = translate_to_scopes(...)`
   - `async on_call_failed(server_name)`：重查该 server 快照 → 更新 dynamic 规则（先 clear 再注入该 server 规则，注意不能清掉其它 server 的——改为按 server 记录规则集合、刷新时重建全部）
6. 写测试（fake conn 返回预设快照）：
   - `translate_to_rules`：只读快照（grants 含 SELECT）→ mysql_insert/update/delete 生成 deny；root 快照（全 grants）→ 无 deny
   - `translate_to_scopes`：dbs/tables → ResourceScope
   - guard.refresh_all：fake 连接 → dynamic 层规则注入 → `PermissionChecker.check` 该工具 DENY；disabled_tools 含被禁工具
   - on_call_failed：快照变化 → 规则更新
   - fetch_snapshot 解析失败 → None + 告警不阻断

**验证：** `ruff format --check && ruff check && python -m pytest tests/test_mcp_privilege.py -q` 全过。

---

## T7: 认证失败抑制（mcp/conn.py）

**文件：** `mewcode/mcp/conn.py`、`tests/test_mcp_conn.py`（追加）
**依赖：** 无
**步骤：**
1. `MCPConnection` 加 `self._auth_failed = False`。
2. `call_tool`：远端错误文本含认证失败特征（`Access denied for user` / `ER_ACCESS_DENIED`）→ `self._auth_failed = True`；方法开头 `if self._auth_failed: return ToolResult(status="error", error="该 server 凭据异常，已停止重试（请检查连接账号）")`。
3. 告警一次：首次置位时 stderr `[mcp-priv] warn: server <name> authentication failed, retries suppressed`。
4. `tests/test_mcp_conn.py` 追加：fake 返回认证错误 → 后续调用直接结构化错误不重试、告警一次；非认证错误不置位。

**验证：** `ruff format --check && ruff check && python -m pytest tests/test_mcp_conn.py -q` 全过。

---

## T8: LLM 可见性 + 装配（registry.py + main.py + __init__.py）

**文件：** `mewcode/tools/registry.py`、`mewcode/main.py`、`mewcode/mcp/__init__.py`
**依赖：** T5、T6、T7
**步骤：**
1. `registry.to_definitions(disabled: set[str] | None = None)`：过滤 `tool.name in disabled` 的工具（`read_only_definitions` 同样加参或共用过滤逻辑）。
2. `main.py` 装配：
   - `load_mcp_servers` → 构建 `mcp_sql_args`/`mcp_danger_args`/`mcp_table_args`/`mcp_default_dbs`（从 servers 的 permissions 声明）
   - `PermissionChecker(..., mcp_* )`
   - `start_all` 后 `PrivilegeGuard(checker, servers, mcp_mgr.connections)` → `await guard.refresh_all()` → `checker.mcp_resource_scopes = guard.scopes`
   - agent 组装：`registry.to_definitions(disabled=guard.disabled_tools)`
   - 边界摘要（guard 产出的 read_only/限库文案）拼进 `env_segment`（仅在启用预检的 server 时追加，不落盘）
   - `McpTool.execute` 失败路径接 `guard.on_call_failed(server_name)`（在 conn 层通过 manager 暴露 server_name 与失败事件，或 wrapper 捕获后回调）
3. `mewcode/mcp/__init__.py` 导出 `PrivilegeGuard` / `PrivilegeSnapshot`。

**验证：** `ruff format --check && ruff check && python -c "from mewcode.mcp import PrivilegeGuard"` ok；全量 `python -m pytest -q` 全过。

---

## T9: 全量规范与 docs 保护自检

**文件：** —
**依赖：** T1–T8
**步骤：**
1. `ruff format mewcode/ tests/` 后 `ruff format --check .`（全仓基线 27 个 pre-existing 告警仍存在，ch07 文件零违规——与 ch07 主体一致）。
2. `ruff check mewcode/mcp/ mewcode/permission/ mewcode/tools/registry.py mewcode/main.py`（ch07+ch08 相关文件零告警）。
3. `python -m pytest -q` 全过。
4. docs 保护自检：`git status docs/` 仅显示 ch07 既有文档 + 新建 `docs/ch07/mcp-db-permission/` 四份 + `mcp-servers.example.yaml`，无对既有文档的修改。

**验证：** 全部通过。

---

## 执行顺序

```
T1 ─┐
T2 ─┤→ T4 → T6 → T8 ─┐
T3 ─┘                  ├→ T9
T5 ─→ T6 ─────────────┘
T7 ─────────────→ T8
```

- T1/T2/T3（permission 三个新件）可并行，T4 依赖三者；T5（配置）独立并行；T6 依赖 T4+T5；T7 独立；T8 依赖 T5+T6+T7；T9 收尾。

## 自检

- **spec 覆盖**：F1→T5、F2→T6、F3→T1+T4、F4→T2+T4、F5→T6（翻译+注入）+T8（LLM 可见性）、F6→T6（刷新）+T3（dynamic 不落盘）、F7→T7、F8→T8。✓
- **依赖链**：无环；T4 依赖 T1/T2/T3，T6 依赖 T4/T5，T8 依赖 T6/T7。✓
- **验证完整性**：每任务含 ruff + pytest；真 DB 场景列「待人工验证」（checklist）。✓
- **类型一致性**：与 plan 一致——`MCPServerPermissions`、`PrivilegeSnapshot`、`ResourceScope`、`PrivilegeGuard`、`inject_rules/clear_dynamic_rules`、`to_definitions(disabled)`。✓
