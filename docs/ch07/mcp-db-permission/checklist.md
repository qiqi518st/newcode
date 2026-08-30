# MewCode ch07 — MCP 对接数据库的权限管理 Checklist

> 每一项通过运行代码或观察行为验证，聚焦系统行为。验证方式写在括号内。
> 需真实 mysql-server + mysql-mcp-server 才能验证的行为列「待人工验证」，不混入「通过」。
> 本文件是 mew-spec 流程产物，本身允许写入；除此之外测试/验证过程不改动 docs/。

## 实现完整性

- [ ] **配置声明可解析**：`mcp_servers` 内带 `permissions` 段（probe_tool/sql_arg/table_arg/where_arg/privilege_map）的 server 解析出权限预检配置；缺 `permissions` 的 server 行为同 ch07。（验证：`python -m pytest tests/test_mcp_config.py -q`，含 permissions 解析 + 缺省用例）【F1、AC1】
- [ ] **模块可导入**：`from mewcode.mcp import PrivilegeGuard, PrivilegeSnapshot`、`from mewcode.permission import ...` 不报错。（验证：`python -c` import）【各 F】

## 判定链路（L1 → L2 → L3）

- [ ] **① 高危拦截**：自由 SQL 工具传入高危 SQL（`DROP TABLE`/`TRUNCATE`/无 WHERE 的 `DELETE`/`UPDATE`）→ DENY；结构化工具危险判定参数（where）为空 → DENY；带明确条件（`WHERE id=1`）→ 放行；**bypass 模式同样拦截**。（验证：`python -m pytest tests/test_mcp_blocklist.py tests/test_mcp_privilege.py -q`，含 bypass 断言）【F3、AC3】
- [ ] **② 资源边界**：结构化工具 `table` 参数直取资源、自由 SQL 工具 SQL 规范化后提取表引用；访问不在授权集合的库/表 → DENY；`db.*` 通配、裸表名按默认库补全均生效。（验证：`python -m pytest tests/test_resource_scope.py tests/test_mcp_privilege.py -q`，含越界/通配/补全用例）【F4、AC4】
- [ ] **③ 操作权限**：工具所需操作不在快照 → deny 规则注入 L3；该工具调用被判定层拒绝。（验证：`tests/test_mcp_privilege.py` translate_to_rules + inject_rules 后 check 断言 DENY）【F5、AC5】
- [ ] **判定顺序恒定**：同一调用同时命中高危与资源越界时按 ①→②→③ 顺序给出第一个命中结果。（验证：构造同时越权 + 高危的调用，断言拒绝且原因正确）【N1】
- [ ] **SQL 规范化防绕过**：注释藏表引用（`FROM a -- DROP b`）、字符串伪装（`FROM 'evil'`）、反引号标识符、多语句、大小写——均正确提取/不误判。（验证：`tests/test_resource_scope.py` 规范化用例）【F4、AC4】

## 动态规则生命周期

- [ ] **不落盘**：注入 dynamic 层的规则只存内存；权限文件（.mewcode/permissions*.yaml）内容不变。（验证：`tests/test_mcp_privilege.py` 注入后 `git diff .mewcode/` 无权限文件改动；读取权限文件内容断言未变）【F6、AC6】
- [ ] **按账号生成**：不同快照（只读 vs 全权限）生成不同 deny 规则集；启动时按当前连接账号快照生成。（验证：`tests/test_mcp_privilege.py` translate_to_rules 两快照断言）【F6、AC6】
- [ ] **调用失败刷新**：远端拒绝（权限变更）→ 重查快照 → dynamic 规则更新（先清再注入，不残留旧规则、不影响其它 server）。（验证：`tests/test_mcp_privilege.py` on_call_failed 用例）【F6、AC6】

## LLM 感知

- [ ] **禁用工具不可见**：被 deny 的工具不出现在 `to_definitions(disabled=...)` 结果中（LLM 看不到）。（验证：`tests/test_mcp_privilege.py` 断言工具定义列表不含禁用工具）【F5、AC5】
- [ ] **边界提示**：启用预检的 server 其账号边界摘要（只读/限库文案）出现在 `env_segment`/提示词中。（验证：装配后检查环境段含边界文案）【F8、AC8】

## 认证失败抑制

- [ ] **认证失败停止重试**：远端返回认证失败（`Access denied for user`）→ 该 server 后续调用直接结构化错误、不自动重试、stderr 告警一次；其它 server 不受影响。（验证：`tests/test_mcp_conn.py` 认证抑制用例，capsys 断言告警一次）【F7、AC7】

## 编译与测试

- [ ] **ch07+本功能 ruff 干净**：`ruff check mewcode/mcp/ mewcode/permission/ mewcode/tools/registry.py mewcode/main.py` 零告警。（验证：命令退出码 0）【N7、AC10】
- [ ] **全量测试通过**：`python -m pytest -q` 全过（含 ch01–ch07 既有 + 本功能新增）。（验证：退出码 0）【N5、AC9】
- [ ] **既有不退化**：五层权限、MCP 连接/调用、内置工具既有测试全过；未声明预检的 server 行为与 ch07 一致。（验证：`python -m pytest tests/test_permission_*.py tests/test_mcp_*.py -q`）【N5、AC9】
- [ ] **docs 保护自检**：`git status docs/` 仅显示 ch07 既有文档 + 新建 `docs/ch07/mcp-db-permission/` 四份 + 示例，无对既有文档修改。（验证：`git diff docs/` 空）【docs 保护】

## 待人工验证（需真实 mysql-server + mysql-mcp-server，不混入「通过」）

- [ ] **AC4 端侧：受限账号资源边界**——MySQL 建只读账号（仅 test818 SELECT/SHOW），MCP 配置用该账号：agent 调 `mysql_query("SELECT * FROM test818.orders")` 放行；调 `mysql_insert`/`mysql_delete`（越权）被 L3 提前拦（MySQL 侧未收到）；`mysql_query("SELECT * FROM other_db.t")` 被 L2 拦。
  - **受阻原因**：需真实 DB 账号 + server 权限查询能力 + 真实 MCP 连接。
  - **替代验证**：T4/T6 用 fake 连接 + fake 快照覆盖判定/翻译/注入逻辑层。
  - **风险**：远端 probe_tool 的真实返回格式、SQL 规范化对真实 SQL 的覆盖度未在 CI 验证。
  - **补验**：由开发者在有 MySQL 的环境建受限账号、按 spec 配置后实跑观察。
- [ ] **AC3 端侧：大权限账号高危拦截**——配 root 账号：`mysql_query("DROP TABLE ...")` 被高危名单 DENY（bypass 模式也拦）；`mysql_delete(where="")` 被拒。
  - **受阻原因**：需真实 DB + 真实 MCP 连接。
  - **补验**：随 AC4 一并实跑。
- [ ] **AC7 端侧：认证失败防锁**——MCP 配置错误密码：启动告警 + 不再反复重试（观察日志无循环调用）。
  - **受阻原因**：需真实 DB 触发认证失败。
  - **补验**：随 AC4 实跑。

## 验收报告模板

```
## 验收报告
### 通过（N/M）
- [x] 条目 — 证据：...
### 未通过（如有）
- [ ] 条目 — 预期：X，实际：Y，修复方案：...
### 待人工验证
- [ ] AC4 端侧资源边界 / AC3 端侧高危拦截 / AC7 端侧认证防锁 — 原因：需真实 DB；替代：fake 覆盖逻辑层；补验：开发者实跑
### 端到端
- [x] 场景 1（fake 连接全链路：快照→翻译→注入→判定→LLM 可见性）— 结果：...
- [ ] 场景 2（真实 DB 端到端）— 待人工验证
```

## 自检

- **spec 对齐**：AC1–AC10 每条均有对应条目（AC1→实现完整性、AC2→快照降级、AC3→高危、AC4→资源+待人工、AC5→操作+LLM 可见性、AC6→动态生命周期、AC7→认证+待人工、AC8→边界提示、AC9→既有不退化、AC10→规范）。✓
- **可观测性**：每项为「运行 X 期望 Y」或「观察行为」，带命令/断言。✓
- **耦合测试**：锚定行为而非文件名/行号（pytest 用例名 + 公开 import）。✓
- **端到端**：场景 1（fake 全链路可自动）、场景 2（真实 DB）列待人工验证。✓
- **受阻上报**：AC3/AC4/AC7 端侧列「待人工验证」，每项给原因/替代/风险/补验。✓
