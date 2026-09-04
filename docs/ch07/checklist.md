# NewCode ch07 — MCP 客户端 验收清单 (checklist.md)

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。验证方式写在每项末尾括号内。
> 凡需真实 MCP server / 真实终端 / 网络才能验证的行为，列为「待人工验证」，**不混入「通过」**（CLAUDE.md「验证受阻必上报」纪律）。
> 本文件是 spec 流程产物，本身允许写入；除此之外测试/验证过程不改动 docs/。

## 实现完整性（MCP 子系统各组件已实现且可被调用）

- [ ] **加载入口存在**：`from newcode.mcp import MCPManager, MCPConnection, McpTool, CallerSession, ServerConfig, load_mcp_servers` 不报错。（验证：`python -c "from newcode.mcp import MCPManager, MCPConnection, McpTool, CallerSession, ServerConfig, load_mcp_servers; print('ok')"` 输出 `ok`）【对应 F1–F7、AC6】
- [ ] **配置两层加载与合并**：`~/.newcode/config.yaml` 与 `<root>/.newcode.yaml` 都存在时按 server 名合并，同名 server 项目级完整覆盖；任一文件缺失或非法 YAML 时跳过该层、不抛、其它正常加载。（验证：`python -m pytest tests/test_mcp_config.py -q`，含两层合并用例 + 非法文件降级用例 + `capsys` 告警断言；手写两个临时 yaml 文件跑 `load_mcp_servers(root)` 观察合并结果）【F1、AC1】
- [ ] **字段校验跳过非法 server**：stdio 缺 command、http 缺 url、type 非法/缺失时该 server 被跳过并 stderr 告警，其它 server 不受影响。（验证：`test_mcp_config.py` 校验用例 + `capsys.readouterr().err` 含 `skip server`）【F2、AC2】
- [ ] **环境变量展开**：env / headers 的值 `${VAR}` 从宿主环境取值；未定义变量展开为空串并告警；command / args / 工具名 / server 名不展开。（验证：`test_mcp_config.py` 展开/未定义/不展开三组用例；`monkeypatch.setenv`/`delenv` 控制）【F3、AC3】
- [ ] **配置示例可被解析**：`docs/ch07/mcp-servers.example.yaml` 含 stdio+http 三类 server，经 `load_mcp_servers` 解析后三个 key 都在结果 dict、type 正确、`${VAR}` 已展开或保留字面量正确。（验证：`test_mcp_config.py` 示例文件反向用例，`monkeypatch.setenv` 预置 token 避免噪音）【F1/F2、T9/AC1】
- [ ] **工具命名空间拼接**：MCP 工具名形如 `mcp__<server>__<tool>`；前缀拼接后含非 `[A-Za-z0-9_-]` 字符的工具被跳过并告警。（验证：`python -m pytest tests/test_mcp_wrapper.py tests/test_mcp_conn.py -q`，含命名拼接 + 禁用字符用例）【F7/F8、AC6/AC7】
- [ ] **工具适配只读性**：远端 `annotations.readOnlyHint==True` → `read_only==True`；缺失/非法/False → `False`（安全默认）。（验证：`test_mcp_wrapper.py` readOnlyHint True/False/None 三态用例）【F7、AC6】
- [ ] **工具参数与描述**：`inputSchema` 透传（空则 `{"type":"object"}` 兜底）；`description` 空时给含 server 名的兜底。（验证：`test_mcp_wrapper.py` schema 透传/兜底 + 描述兜底用例）【F7、AC6】
- [ ] **调用结果翻译**：远端 `TextContent` 按序拼成结果文本；远端 `isError==True` → 结果为错误态；非 text 块（image/audio/resource_link/embedded_resource）静默丢弃并 stderr 告警一次（同 `full_name` 去重）。（验证：`python -m pytest tests/test_mcp_conn.py -q`，用 fake `_session` 驱动真实 `MCPConnection.call_tool`，覆盖成功/isError/非text块三用例，`capsys` 断言告警仅一次）【F7、AC6】
- [ ] **调用超时超回灌**：`call_tool` 30s 超时或 `session.call_tool` 抛异常，转成 `status="error"` 的结构化结果回灌，**不向调用方抛异常**。（验证：`test_mcp_conn.py` 超时分支（`call_timeout` 临时改小 + 挂起 fake）+ 协议错分支用例；断言返回 `ToolResult` 而非 raise）【F10、AC9】

## 集成（MCP 工具接入既有管线）

- [ ] **并发启动与失败隔离**：`MCPManager.start_all` 用 `asyncio.gather` 并发连接所有 server，单 server 连接/握手/列工具失败或超时只跳过自身、其它照常；启动总时延上界受 30s 约束（并发实现）。（验证：`python -m pytest tests/test_mcp_manager.py -q`，含「坏 command server + 注入 stub 成功 server」组合用例——失败 server 仅告警、stub 工具被收集；超时用例临时改小 `connect_timeout` 断言 ~0.2s 返回）【F9、N1、AC8】
- [ ] **退出干净不死锁**：`MCPManager.close` 并发关全部连接，单连接 close 卡住不阻塞整体；整体 5s 兜底。（验证：`test_mcp_manager.py` close 阻塞 fake 用例，临时改小 `close_timeout` 断言 0.2s 内返回；测试结束断言无悬挂 task `assert not asyncio.all_tasks() - {asyncio.current_task()}`）【F11、N7、AC10（部分，子进程终止待人工）】
- [ ] **工具稳定排序**：`MCPManager.tools()` 返回的工具按 `full_name` 稳定排序，与 task 完成顺序无关。（验证：`test_mcp_manager.py` 全成功用例断言顺序由 sort 决定）【F9】
- [ ] **权限链路自然命中（规则层）**：`mcp__<server>__*` 与精确 `mcp__github__create_issue` 的 allow/deny 规则正确作用到对应 MCP 工具；黑名单/沙箱对 MCP 工具不命中自动跳过。（验证：`python -m pytest tests/test_permission_rules.py tests/test_permission_engine.py tests/test_permission_agent.py -q`，含 `mcp__github__*` 通配匹配 `mcp__github__create_issue` 为 True、`mcp__other__x` 为 False 用例 + 五层权限不回归）【F12、AC11（规则层）】
- [ ] **权限 allow_always 对 MCP 落盘**：MCP 工具选「永久允许」后，本地权限文件 allow 列表出现裸工具名精确规则（无括号、无 target）；重启后对同工具不再命中 Ask。（验证：`python -m pytest tests/test_permission_checker.py -q`，含 `mcp__github__create_issue` 落盘裸名 + 去重用例；用 `tmp_path` 隔离）【F12、AC11（落盘层）】
- [ ] **权限模式兜底对 MCP**：`read_only==True` 的 MCP 工具归只读类（DEFAULT 下放行、可并发）；非只读归命令执行类（DEFAULT/acceptEdits 下触发人在回路 Ask）；bypass 下放行。**permission 包源码对 MCP 路径无新增依赖**，靠 `friendly_name` 原样 + `categorize` 按 `read_only` 优先 + `extract_target` 对未知工具降级。（验证：`python -m pytest tests/test_permission_rules.py -q` 验 `_RULE_PARSE_RE` 接受 `mcp__` 名 + `_tool_name_matches` 通配；代码层面 `git diff newcode/permission/` 仅见泛化改动（正则放宽 + match 通配 + persist 裸名），无破坏内置工具既有行为）【F12、AC11（模式层）】
- [ ] **provider 适配层零 diff**：MCP 工具与 provider（Anthropic/OpenAI）无关；provider 适配层对本章无修改。（验证：`git diff --stat newcode/provider/` 对 ch07 分支无改动）【N3、AC12】
- [ ] **装配处注册无感**：`main._amain` 在 `Registry.default()` 之后加载 MCP 配置、启动 manager、把 `mgr.tools()` 注册进 registry；Agent 与 provider 适配层不感知工具来自远端。（验证：`git diff newcode/main.py` 仅见 `_amain` 单 loop + MCP 起停 + finally close，Agent 构造与既有一致；`python -c "import newcode.main; print('ok')"` import 链不断）【F8/F9、AC6/AC12】
- [ ] **启动摘要可观测（N5 补强）**：有 server 被尝试时，进 TUI 前 stderr 打印一行 `[mcp] startup: ... | total N tools`（成功 server 带工具数、失败 server 标 `:failed`）；无任何 server 时不打印（避免噪音）。（验证：配好 server 后 `python -m newcode` 观察 stderr 摘要行；`python -m pytest tests/test_mcp_manager.py -q` 含 `format_summary` 文案断言）【N5】

## 编译与测试

- [ ] **包可导入、版本正确**：`newcode.__version__` 为 `0.7.0`；`pyproject.toml` 的 `version` 与之一致；`mcp` SDK 可 import。（验证：`python -c "import newcode; print(newcode.__version__)"` 输出 `0.7.0`；`python -c "import mcp; print('ok')"`)【T0】
- [ ] **格式规范**：`ruff format --check .` 无 diff。（验证：`ruff format --check .` 退出码 0）【N8、AC15】
- [ ] **静态检查**：`ruff check .` 无告警。（验证：`ruff check .` 退出码 0）【N8、AC15】
- [ ] **类型检查（可选）**：`mypy newcode/mcp`（strict 子集亦可）通过或仅已知不阻断项。（验证：`mypy newcode/mcp` —— 若环境装 mypy 则跑，未装记「待人工验证」）【N8、AC15】
- [ ] **单元测试全过**：`python -m pytest -q` 全过，含 `tests/test_mcp_config.py`、`tests/test_mcp_wrapper.py`、`tests/test_mcp_conn.py`、`tests/test_mcp_manager.py`、`tests/test_permission_rules.py`、`tests/test_permission_checker.py` 及 ch01–ch06 既有测试。（验证：`python -m pytest -q` 退出码 0；子包全量 `python -m pytest tests/test_mcp_config.py tests/test_mcp_wrapper.py tests/test_mcp_conn.py tests/test_mcp_manager.py -q` 退出码 0）【N5、AC13/AC15】
- [ ] **既有能力不退化**：ch01–ch06 既有测试全过（多轮连环、用户取消、流出错恢复、历史一致、缓存命中、规划按轮次注入、五层权限）。（验证：`python -m pytest tests/test_agent.py tests/test_tools.py tests/test_cache_usage.py tests/test_conversation_tools.py tests/test_provider_tools.py tests/test_tui_wiring.py tests/test_permission_*.py -q` 退出码 0）【N5、AC13】
- [ ] **凭据不落盘**：配置示例与 `.newcode.yaml` 均用 `${VAR}` 引用密钥，仓库内无 token 明文命中。（验证：`git grep -nE "(Bearer|sk-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}"` 无命中；`grep -rn "TOKEN\|token" docs/ch07/mcp-servers.example.yaml` 仅见 `${VAR}` 占位）【N6、AC14】
- [ ] **并发/收尾无悬挂 task**：MCP manager 测试结束无悬挂 asyncio task、close 不死锁。（验证：`python -m pytest tests/test_mcp_manager.py -q` 内用例已 `assert not asyncio.all_tasks() - {asyncio.current_task()}`）【N1/N7、AC8/AC10（可自动部分）】
- [ ] **docs 保护自检**：测试/验证过程未改动 docs/ 下任何已存在文件，仅新建了 `docs/ch07/mcp-servers.example.yaml`（新建交付物）+ 四份 spec 流程文档。（验证：`git status docs/` 仅显示 `docs/ch07/` 新增文件，`git diff docs/` 对既有文档无改动）【CLAUDE.md docs 保护规则】

## 端到端场景

- [ ] **场景 1（无 MCP 配置降级）**：项目根 `.newcode.yaml` 无 `mcp_servers` 段时，newcode 启动行为与 ch06 一致——内置 6 工具可用、TUI/banner 正常、无 MCP 告警、`start_all` 立即返回。（验证：临时移除/注释 `mcp_servers` 段，`python -m newcode` 进 TUI 观察状态栏与提示符同 ch06；为不依赖 TUI 的纯启动侧验，用单 `asyncio.run` 包住 start_all+close 以保 session 与 loop 同寿——`python -c "import asyncio; from newcode.mcp import MCPManager, load_mcp_servers; async def go(): m=MCPManager(load_mcp_servers('.'), '0.7.0'); await m.start_all(); print(m.tools()); await m.close(); asyncio.run(go())"` 观察空工具列表。**注意**：必须用单 `asyncio.run` 一次性包住 start_all 与 close，不可分两次 `asyncio.run`（MCP session 的底层 transport 绑定所在 loop，跨 loop 调用 close 会失效，见 plan「单一事件循环」决策））【F1、N6、AC1/AC6】
- [ ] **场景 2（坏 server + 好 server 混合启动）**：配置一个 command 不存在的 stdio server + 一个能跑的 server，启动 stderr 有失败告警，能跑的 server 工具仍注册可用、启动不阻塞。（验证：`test_mcp_manager.py` 坏 command + stub 成功组合用例已覆盖自动侧；真实端侧标「待人工验证」见下）【F9、N1、AC8】

## 待人工验证（依赖真实 server / 真实终端，不混入「通过」）

- [ ] **AC4：stdio 真实启动 + 子进程终止**——能拉起一个真实 stdio MCP server（如 `npx -y @modelcontextprotocol/server-everything`），握手 + 列工具成功；env 注入生效；newcode 退出时子进程被终止、无僵尸。
  - **受阻原因**：需真实 stdio MCP server 可执行 + 真实终端观察 `ps`。
  - **替代验证**：T3 用 fake `ClientSession` 覆盖 `connect_and_list` 包装路径与 `call_tool` 翻译；T4 用 fake `MCPConnection` 覆盖 start_all/close 并发与超时——覆盖了集成层逻辑，但**真 SDK transport 生命周期、子进程 fork/exec/wait、env 合并注入生效值**未在 CI 验证。
  - **风险**：真 SDK `stdio_client` 上下文在私有 AsyncExitStack 上的进入/退出、子进程 signal 终止、env 覆盖同名宿主变量的真实效果 若有 bug，单测测不到。
  - **补验**：由开发者在有 npx + 真实终端的环境按 task T10 步骤执行，观察启动 stderr、工具数、退出后 `ps -ef | grep server-everything` 无残留。
- [ ] **AC5：HTTP 真实连接 + 自定义 headers**——能对一个真实 HTTP MCP server 完成握手 + 列工具；`headers` 注入到 HTTP 请求。
  - **受阻原因**：无公开可用的免鉴权 HTTP MCP server 端点；需自建或用社区 http server。
  - **替代验证**：T3 的 fake transport 覆盖 `streamablehttp_client` 返回 3 元组的解包与 session 包装路径；静态确认 `streamablehttp_client(url, headers=...)` 的调用签名正确。
  - **风险**：真 HTTP 传输的 headers 注入、SSE 通道不消费的真实行为、网络错/鉴权错回灌未在 CI 验证。
  - **补验**：由开发者在有 HTTP MCP server（或起 `mcp dev` 调试）的环境按 task T10 类似步骤验证 headers 生效（可 server 端日志看 `Authorization`）。
- [ ] **AC8 真连接部分 + AC10 退出子进程终止（端侧）**——真实多个 server 混合（含失败/超时）启动不阻塞；退出后所有 stdio 子进程终止、HTTP 会话关闭、无悬挂 task。
  - **受阻原因**：同 AC4/AC5，需真实 server + 终端 `ps`/网络观察。
  - **替代验证**：T4 覆盖失败隔离/超时/close 兜底的逻辑层；子进程真实终止需 AC4 同条件补验。
  - **补验**：随 AC4 一并在 T10 端实跑验证。
- [ ] **AC11 端侧弹窗交互**——非只读 MCP 工具在 DEFAULT 模式下真实触发人在回路 Ask 弹窗、允许后回灌、永久允许后重启不再弹。
  - **受阻原因**：Ask 弹窗需真实 TUI 终端交互观察。
  - **替代验证**：`test_permission_agent.py`/`test_permission_tui.py` 已覆盖 Ask/HITL 既有链路对工具的行为；`test_permission_checker.py` 覆盖 `mcp__` 工具落盘裸名——MCP 工具走同一 `categorize`/模式矩阵，行为等价可推；规则/模式层自动验证已覆盖（见集成项）。
  - **风险**：MCP 工具名经 HITL 弹窗的显示、用户选择调用 `persist_local_allow` 的端到端串接未在 CI 验证。
  - **补验**：由开发者在真实终端按 T10 步骤触发 `mcp__demo__echo` 类非只读工具观察弹窗与永久规则联动。

## 验收报告模板

```
## 验收报告

### 通过（N/M）
- [x] 条目 — 证据：（命令输出/观察到的行为）
...

### 未通过（如有）
- [ ] 条目 — 预期：X，实际：Y，修复方案：...

### 待人工验证（环境受限项）
- [ ] AC4 stdio 真实启动+子进程终止 — 原因：无真实 server/终端；替代：fake session 覆盖集成层；风险：真 transport 生命周期未验；补验：开发者有环境时按 T10 执行
- [ ] AC5 HTTP 真实连接+headers — 原因：无 http server 端点；替代：fake transport 覆盖解包/签名；风险：headers 注入/网络错回灌未验；补验：有 http server 环境验证
- [ ] AC8 真连接端侧 / AC10 子进程终止 — 原因：同 AC4；替代：T4 逻辑层；补验：随 AC4
- [ ] AC11 端侧弹窗交互 — 原因：需真实 TUI；替代：既有 HITL 测试 + 落盘单测等价可推；补验：真实终端按 T10

### 端到端
- [x] 场景 1（无 MCP 配置降级）— 结果：...
- [x] 场景 2（坏 server + 好 server 混合，自动侧）— 结果：...
- [ ] 场景 3（真实 server 端到端，含权限联动+退出终止）— 待人工验证：见 T10
```

## 自检

- **spec 对齐**：spec.md 的 AC1–AC15 每条均有对应 checklist 条目——AC1→实现完整性(配置加载/示例)、AC2→字段校验、AC3→变量展开、AC4→待人工、AC5→待人工、AC6→命名/适配/翻译、AC7→命名空间、AC8→并发启动+待人工、AC9→调用超时回灌、AC10→退出干净+待人工、AC11→权限规则/落盘/模式三层、AC12→provider 零 diff、AC13→既有不退化、AC14→凭据不落盘扫描（独立条目）、AC15→规范（命令用 `pytest`，异步测试沿用 `@pytest.mark.anyio`）。✓
- **可观测性**：每项均为「运行 X，期望 Y」或「观察行为」，不依赖逐行读代码；带可执行命令或 git diff 断言。✓
- **耦合测试**：条目锚定行为（"loading returns merged dict"、"tools sorted by full_name"），不锚定文件名/函数名——重命名或重构 mcp 子包内部、条目仍适用（验证命令用包公开 import 与 pytest 用例名，pytest 用例名随测试文件移动不失效）。git diff 锚定的是"改动范围"语义不是具体行号。✓
- **端到端**：含场景 1（无配置降级，可自动+轻交互）、场景 2（混合启动，自动+真连接拆分），真实端到端（场景 3）列入待人工验证。✓
- **受阻上报**：AC4/AC5/AC8 真连接/AC10 子进程/AC11 弹窗 五项列「待人工验证」，每项给原因/替代/风险/补验——不混入「通过」。✓