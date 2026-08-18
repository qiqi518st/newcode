# MewCode ch07 — MCP 客户端 任务拆解 (task.md)

> 约定：本项目为 Python，所有验证命令在 Git Bash 下需先 `export PYTHONIOENCODING=utf-8`（见 CLAUDE.md）。
> **异步测试标记跟 repo 现有约定**：仓库已有测试一律用 `@pytest.mark.anyio`（非 `@pytest.mark.asyncio` / `pytest-asyncio`），见 `tests/test_agent.py`、`tests/test_tools.py`。ch07 新测试必须沿用 `@pytest.mark.anyio`。
> 测试遵循 CLAUDE.md 测试规范：用 `object.__new__` / fake session / stub caller 驱动**真实代码路径**，不依赖真实 MCP server / 真实终端 / 网络。
> 凡需真实 stdio/http MCP server 或真实终端才能验证的行为（AC4/AC5/AC8 真连接）属集成层，列为标「待人工验证」的独立任务，**不混入可自动验证任务的「通过」**。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `mewcode/mcp/__init__.py` | 子包门面，导出公共符号 |
| 新建 | `mewcode/mcp/config.py` | `ServerConfig`、`load_mcp_servers`、配置加载/合并/展开/校验 |
| 新建 | `mewcode/mcp/wrapper.py` | `McpTool`、`CallerSession` Protocol、`make_tool`、`_VALID_NAME`、模块级 `_non_text_warn_once` |
| 新建 | `mewcode/mcp/conn.py` | `MCPConnection`（SDK 会话封装、call_tool 翻译）、`MCPStartupError`、模块级 `call_timeout` |
| 新建 | `mewcode/mcp/manager.py` | `MCPManager`（并发启动/收集/排序/关闭）、模块级 `connect_timeout`/`close_timeout` |
| 修改 | `mewcode/permission/rules.py` | `_RULE_PARSE_RE` 放宽；`RuleSet.match` 工具名 `*` 通配 |
| 修改 | `mewcode/permission/checker.py` | `persist_local_allow` 对 `mcp__` 工具落盘裸工具名 |
| 修改 | `mewcode/main.py` | `_amain` 单 loop；接入 MCP 起停 + finally close |
| 修改 | `pyproject.toml` | `dependencies` 加 `mcp>=1.0`；`version` 升 `0.7.0` |
| 修改 | `mewcode/__init__.py` | `__version__` 升 `0.7.0` |
| 新建 | `docs/ch07/mcp-servers.example.yaml` | 配置示例（含 stdio / http 各类，用 `${VAR}`），被反向测试覆盖 |
| 新建 | `tests/test_mcp_config.py` | 两层合并/字段校验/变量展开/降级 + **示例文件反向解析** 单测 |
| 新建 | `tests/test_mcp_wrapper.py` | 命名拼接/禁用字符/只读性/描述兜底/execute 转发（stub caller） |
| 新建 | `tests/test_mcp_conn.py` | call_tool 各分支（成功/isError/超时/协议错/非text块）用 fake session 驱动；connect_and_list 包装路径 |
| 新建 | `tests/test_mcp_manager.py` | 并发启动/单 server 失败隔离（坏 command + stub 成功组合）/超时/稳定排序/close 兜底 |
| 修改 | `tests/test_permission_rules.py` | 补 `mcp__` 工具名规则解析 + `*` 通配匹配用例 |
| 修改 | `tests/test_permission_checker.py` | 补 `persist_local_allow` 对 `mcp__` 工具落盘裸名用例 |

---

## T0: Ch07 版本与依赖就绪

**文件：** `pyproject.toml`、`mewcode/__init__.py`
**依赖：** 无
**步骤：**
1. `mewcode/__init__.py`：`__version__` 从 `"0.6.0"` 改为 `"0.7.0"`。
2. `pyproject.toml`：`[project] version` 从 `"0.6.0"` 改为 `"0.7.0"`；`dependencies` 列表追加 `"mcp>=1.0"`。
3. 重装：`pip install -e .[dev]`。
4. 核对本地 `mcp` SDK 关键导出路径以便后续正确 import——执行 `python -c "import mcp, mcp.types, mcp.client.stdio, mcp.client.streamable_http as h; print('ok')"`，对照确认 `ClientSession`、`StdioServerParameters`、`Implementation`（均 `mcp`/`mcp.types`）与 `stdio_client`（`mcp.client.stdio`）、`streamable_http_client`（`mcp.client.streamable_http`，**注意是下划线连字、非 `streamablehttp_client`**）、`TextContent`/`Tool`/`CallToolResult`/`ToolAnnotations`（`mcp.types`）的实际来源；SDK 2.0 字段全 snake_case：`Tool.input_schema`、`CallToolResult.is_error`、`ToolAnnotations.read_only_hint`，`call_tool` 返回 `CallToolResult | InputRequiredResult | Result` 联合，`streamable_http_client` **无 headers 参数**（经 `httpx.AsyncClient(headers=...)` 注入）、传输皆 yield 2 元组。把结论记在首个用到它们的实现的注释里。

**验证：**
- `python -c "import mewcode; print(mewcode.__version__)"` → `0.7.0`
- `python -c "import mcp; print('ok')"` → `ok`
- `grep -n '0.6.0' pyproject.toml mewcode/__init__.py` → 无命中

**提交：** 版本号变更**独立提交** `chore: bump version to 0.7.0`（CLAUDE.md「版本号变更应作为独立提交」硬规则）；依赖追加另起提交 `chore(ch07): add mcp sdk dependency`。

---

## T1: 配置加载与合并（config.py）

**文件：** `mewcode/mcp/config.py`、`tests/test_mcp_config.py`
**依赖：** 无
**步骤：**
1. 定义 `@dataclass ServerConfig`：`name: str`、`type: Literal["stdio","http"]`、`command: str=""`、`args: list[str]`（默认工厂）、`env: dict[str,str]`（默认工厂）、`url: str=""`、`headers: dict[str,str]`（默认工厂）。
2. 定义内部 `@dataclass _RawServer`：含 `type`/`command`/`args`/`env`/`url`/`headers`，全部带默认值（缺失字段填默认）。
3. `_load_file(path: Path) -> dict[str, _RawServer]`：不存在→`{}`；`yaml.safe_load` 抛 `YAMLError` 或 `OSError`→stderr 告警 `[mcp] warn: load <path> failed: <err>` + `{}`（不抛）；结果非 dict→`{}`；取 `mcp_servers` 段，缺失/非 dict→`{}`；逐项映射到 `_RawServer`。
4. `_expand_value(s, server_name, seen_undef: set) -> str`：正则 `r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}"`，`os.environ.get(var, "")` 取值；未定义（`var not in os.environ`）→空串 + 把变量名加入 `seen_undef`。
5. `_apply_expansion(name, raw) -> None`：对 `raw.env`、`raw.headers` 每个值跑 `_expand_value`，就地替换；同 server 同未定义变量限一次告警（局部 set 去重），stderr `[mcp] warn: undefined env var ${X} referenced by server <name>`。**仅作用于 env/headers 的值**。
6. `_merge_servers(user, project) -> dict[str, _RawServer]`：`{**user, **project}`（项目级完整覆盖同名 server）。
7. `_validate_server(name, raw) -> ServerConfig | None`：`type` 非 `stdio`/`http`→None+告警；`stdio` 缺非空 `command`→None+告警；`http` 缺非空 `url`→None+告警；其余字段类型兜底（`args` 非 list→`[]`，`env`/`headers` 非 dict→`{}`）后构造 `ServerConfig(name=name, ...)`。告警 `[mcp] warn: skip server <name>: <reason>`。
8. `load_mcp_servers(root: str) -> dict[str, ServerConfig]`：用户级 `Path.home()/".mewcode"/"config.yaml"`（`Path.home()` 抛错时 try/except 跳过用户层）、项目级 `Path(root)/".mewcode.yaml"`；各自 `_load_file`→对可用 server `_apply_expansion`→`_merge_servers`→逐个 `_validate_server` 组装结果 dict。**全程不抛**。
9. 写 `tests/test_mcp_config.py`（用 `@pytest.mark.anyio` 测 async 部分、普通函数测 sync 部分）：
   ①两层合并、项目级覆盖用户级同名（断言字段为项目级值）；②文件缺失→空；③非法 YAML→跳过该层+不抛+`capsys` 见告警；④`${VAR}` 已定义（`monkeypatch.setenv`）→展开为环境值；未定义→空串+告警；⑤`command`/`args` 中含 `${VAR}`→不展开（保留字面量）；⑥type 缺失/非法、stdio 缺 command、http 缺 url→各自被跳过，其它 server 不受影响。断言行为而非库内部。

**验证：**
- `ruff format --check mewcode/mcp/config.py tests/test_mcp_config.py` 无 diff
- `ruff check mewcode/mcp/config.py tests/test_mcp_config.py` 无告警
- `python -m pytest tests/test_mcp_config.py -q` 全过

---

## T2: 工具适配器（wrapper.py）

**文件：** `mewcode/mcp/wrapper.py`、`tests/test_mcp_wrapper.py`
**依赖：** 无（仅依赖 `mewcode.provider.base.ToolResult`；`CallerSession` 为本地 Protocol，不依赖 conn）
**步骤：**
1. 定义 `CallerSession(Protocol)`：`async def call_tool(self, name: str, arguments: dict) -> ToolResult: ...`。
2. 模块级 `_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")`。
3. 模块级 `_non_text_warn_once: set[str] = set()`（全进程按 `full_name` 去重非 text 块告警）。
4. 实现 `make_tool(server_name: str, caller: CallerSession, remote) -> McpTool | None`：`full_name = f"mcp__{server_name}__{remote.name}"`；`_VALID_NAME.fullmatch(full_name)` 不通过→返回 None + stderr 告警 `[mcp] warn: skip tool <full_name>: name contains illegal characters`；`description = remote.description or f"MCP 工具（来自 server {server_name}）"`；`parameters = dict(remote.input_schema) if remote.input_schema else {"type":"object"}`（SDK 2.0 为 `input_schema`）；`read_only = bool(getattr(getattr(remote, "annotations", None), "read_only_hint", None))`（SDK 2.0 为 `read_only_hint`，None-safe）；返回 `McpTool(caller, full_name, remote.name, description, parameters, read_only)`。
5. 实现 `McpTool` 类：`__init__` 存 `caller/_remote_name/_full_name/_desc/_params/_ro`；`@property name`→`_full_name`、`description`、`parameters`、`read_only`；`async execute(self, arguments) -> ToolResult`：`return await self.caller.call_tool(self._remote_name, arguments or {})`，不再 try/except（极端情况由 ToolScheduler 统一 except 兜底）。
6. 写 `tests/test_mcp_wrapper.py`（`@pytest.mark.anyio`）：①命名拼接；②含非法字符（如 `mcp__s__a/b`）→`make_tool` 返回 None；③`readOnlyHint=True`→read_only True，缺失/False→False；④描述空→兜底文案；⑤inputSchema 空→`{"type":"object"}`；⑥execute 转发到 stub caller（最小 `class StubSession:` 实现 `CallerSession`）并返回其 ToolResult。⑦非 text 块的去重：直接验证 `_non_text_warn_once` 行为由 conn 测试覆盖（见 T3）——wrapper 测试只验 execute 转发。每个测试 docstring 注明防的 bug。

**验证：**
- `ruff format --check mewcode/mcp/wrapper.py tests/test_mcp_wrapper.py` 无 diff
- `ruff check mewcode/mcp/wrapper.py tests/test_mcp_wrapper.py` 无告警
- `python -m pytest tests/test_mcp_wrapper.py -q` 全过

---

## T3: SDK 会话封装与调用翻译（conn.py）

**文件：** `mewcode/mcp/conn.py`、`tests/test_mcp_conn.py`
**依赖：** T0（`mcp` 可 import）、T2（`make_tool`）
**步骤：**
1. 实现前用 `python -c` 核对 `streamablehttp_client`/`stdio_client`/`ClientSession`/`Implementation`/`TextContent` 真实 import 路径（T0 步骤 4 结论落地）。
2. 定义 `class MCPStartupError(Exception)`。
3. 模块级 `call_timeout: float = 30.0`（manager import 复用，避免循环依赖；`connect_timeout`/`close_timeout` 在 manager 定义）。
4. 实现 `MCPConnection.__init__(self, server: ServerConfig, client_version: str)`：存 `server`、`client_version`；`_session=None`；`_closed=False`；`_holder: asyncio.Task|None=None`；`_stop=asyncio.Event()`；`_ready=asyncio.Event()`；`_connect_error=None`；`_tools: list[McpTool]=[]`。（**长寿命 holder task 设计**，取代原私有 AsyncExitStack——见下）
5. 实现 `connect_and_list(self) -> list[McpTool]`：创建 holder task `self._holder = asyncio.create_task(self._hold())`，`await self._ready.wait()`（被取消则 `_teardown_holder` + 标记关闭 + raise）；ready 后若 `_connect_error` 非空→teardown + raise `MCPStartupError`；否则 `return list(self._tools)`。
6. 实现 `_hold(self) -> None`（长寿命 task，**核心生命周期**）：`async with contextlib.AsyncExitStack() as stack:` 内按 `server.type` 构造 transport ctx——
   - stdio：`StdioServerParameters(command=server.command, args=server.args, env={**os.environ, **server.env})` → `stdio_client(params)`；
   - http（SDK 2.0 无 headers 参数）：`http_client = create_mcp_http_client(headers=dict(server.headers)) if server.headers else None`（有则先 `stack.enter_async_context(http_client)`）→ `streamable_http_client(server.url, http_client=http_client)`；
   `transport = await stack.enter_async_context(ctx)`；`read_stream, write_stream = transport`（**stdio / http 都是 2 元组**）；
   `session = await stack.enter_async_context(ClientSession(read_stream, write_stream, client_info=Implementation(name="mewcode", version=self._client_version)))`；
   `await session.initialize()`；`listed = await session.list_tools()`；`self._session = session`；`self._tools = [t for t in (make_tool(self.server.name, self, remote) for remote in listed.tools) if t is not None]`；`self._ready.set()`；`await self._stop.wait()`（停在此处保持上下文存活，直到 close 取消本 task）。
   `except asyncio.CancelledError: raise`（close 取消时，`async with` 在本 task 内退栈——**规避 anyio cancel scope 跨 task 退出 RuntimeError**，spec N7）；`except BaseException as e: self._connect_error=e; self._ready.set()`（栈已在本 task 内自动退出）。
7. 实现 `call_tool(self, tool_name, arguments) -> ToolResult`：`try: result = await asyncio.wait_for(self._session.call_tool(tool_name, arguments=arguments or {}), call_timeout)`；
   **SDK 2.0 `call_tool` 返回 `CallToolResult | InputRequiredResult | Result` 联合类型**——先 `if not isinstance(result, CallToolResult):` 转 `ToolResult(status="error", error="MCP 工具返回非预期结果类型")` 并 return；
   遍历 `result.content`：`isinstance(b, TextContent)`→收 `b.text`；其余类块——`full = f"mcp__{self.server.name}__{tool_name}"`，`if full not in _non_text_warn_once: _non_text_warn_once.add(full); stderr "[mcp] warn: tool <full> returned non-text content blocks (dropped)"`（**用 wrapper 模块级 `_non_text_warn_once`，全进程按 full_name 去重**）；
   映射：`is_error = bool(result.is_error)`（SDK 2.0 snake_case）；`status = "error" if is_error else "ok"`；`joined = "\n".join(texts)`；`output = "" if is_error else joined`；`error = joined if is_error else ""`；返回 `ToolResult(status=status, output=output, error=error)`；
   `except asyncio.TimeoutError: return ToolResult(status="error", error="MCP 工具调用超时 (30s)")`；
   `except Exception as e: return ToolResult(status="error", error=f"MCP 工具调用失败: {e}")`。**不向调用方抛**。
8. 实现 `_teardown_holder(self) -> None`：`self._stop.set()`；`self._holder.cancel()`；`try: await self._holder` `except (CancelledError, Exception): pass`（吞掉 holder 退出异常）；`finally: self._holder=None`。
9. 实现 `close(self)`：幂等（`if self._closed: return; self._closed=True`）；`await self._teardown_holder()`（取消 holder 触发上下文在 holder 自身 task 内退出）。自身不加超时（MCPManager.close 单层 5s 兜底已覆盖）。
10. 写 `tests/test_mcp_conn.py`（`@pytest.mark.anyio`）：
    **call_tool 各分支**——构造 fake `_session`（最小 stub 实现 `.call_tool` 返回预设 result，含 `.content` 列表与 `.is_error`），白盒 set `conn._session`，测：①多个 `TextContent`→按序拼进 `output`、`is_error=False`；②远端 `is_error=True`→文本进 `error`、`status="error"`；③非 text 块→被丢弃、不混进 output + stderr 告警（同 `full_name` 多次只告警一次，验证 `_non_text_warn_once`）；④`call_tool` 抛异常→`ToolResult(status="error", error=...)` 不抛；⑤超时——monkeypatch 把 `mewcode.mcp.conn.call_timeout` 临时改 0.2 + fake `call_tool` 内 `await asyncio.Event().wait()` 挂起→走超时分支，restore；⑥非 `CallToolResult` 返回→「非预期结果类型」错误；⑦未连接调用→「未连接」错误而非 AttributeError。
    **connect_and_list 包装路径**——monkeypatch `conn_mod.stdio_client` 返回 fake transport ctx（`__aenter__` 返回 `(obj,obj)`）+ monkeypatch `conn_mod.ClientSession` 返回包装 fake session（实现 `initialize`/`list_tools`）的 ctx；断言返回的 `McpTool` 列表名字前缀正确（`mcp__<server>__...`）、禁用字符工具被跳过、`c._session` 已置位。
    **connect_and_list 失败收尾**——fake session 的 `initialize` 抛错→`MCPStartupError` 且 `c._session is None`、`c._closed is True`、`close()` 幂等不抛（spec N7）。真 transport 连接留 T10 人工验证。测试 docstring 注明防的 bug。

**验证：**
- `ruff format --check mewcode/mcp/conn.py tests/test_mcp_conn.py` 无 diff
- `ruff check mewcode/mcp/conn.py tests/test_mcp_conn.py` 无告警
- `python -m pytest tests/test_mcp_conn.py -q` 全过
- `python -c "from mewcode.mcp.conn import MCPConnection, MCPStartupError; print('ok')"` → `ok`

---

## T4: 生命周期管理器（manager.py）

**文件：** `mewcode/mcp/manager.py`、`tests/test_mcp_manager.py`
**依赖：** T2（`McpTool`）、T3（`MCPConnection`、`call_timeout`）
**步骤：**
1. 模块级 `connect_timeout: float = 30.0`、`close_timeout: float = 5.0`（`call_timeout` 从 conn import 复用）。
2. 实现 `MCPManager.__init__(self, servers: dict[str, ServerConfig], client_version: str)`：存 `servers`、`client_version`；`_connections: list[MCPConnection] = []`；`_tools: list[McpTool] = []`。
3. 实现 `async _start_one(self, name, srv) -> list[McpTool] | None`：`conn = MCPConnection(srv, self._client_version)`；`try: tools = await asyncio.wait_for(conn.connect_and_list(), connect_timeout)`；`except asyncio.TimeoutError:` stderr 告警 `[mcp] warn: connect server <name> timeout after <connect_timeout>s` 并 `return None`；`except Exception as e:` 告警 `[mcp] warn: connect server <name> failed: <e>` 并 `return None`；成功→`self._connections.append(conn)` + `return tools`。
4. 实现 `async start_all(self) -> StartupSummary`：`await asyncio.gather(*[self._start_one(n, s) for n, s in self.servers.items()], return_exceptions=True)`；收集非 None 的 tools 汇总进 `self._tools`，按 `tool.full_name` 稳定排序。**同名告警归收集阶段（spec F8）**：汇总时若某 `full_name` 已被前序工具占用，stderr 告警 `[mcp] warn: duplicate tool <full_name>, later registration overrides earlier`，保留后入者。**本方法不可失败**——空 servers 空列表 gather 立即返回。`_start_one` 已内捕，不会抛。返回 `StartupSummary(connected=sorted(per_server_counts.items()), failed=sorted(self._failures.items()), total_tools=len(self._tools))`（spec N5 可观测性，供装配处打印）。
5. 实现 `tools(self) -> list[McpTool]`：返回 `list(self._tools)` 副本。
6. 实现 `async close(self) -> None`：`try: await asyncio.wait_for(asyncio.gather(*[c.close() for c in self._connections], return_exceptions=True), close_timeout)`；`except asyncio.TimeoutError:` stderr 告警 `[mcp] warn: close timeout (<close_timeout>s), some sessions may leak`，不再等。
7. 定义 `@dataclass StartupSummary`（`connected: list[tuple[str,int]]`、`failed: list[tuple[str,str]]`、`total_tools: int`、`is_empty` 属性）+ 模块函数 `_format_summary` + 静态方法 `MCPManager.format_summary(summary)` 输出单行文案 `[mcp] startup: a(1 tools), b:failed | total N tools`。
8. `@property connections(self) -> list[MCPConnection]`：返回 `list(self._connections)`（测试用只读视图）。
9. 写 `tests/test_mcp_manager.py`（`@pytest.mark.anyio`），用 monkeypatch 把 `manager.MCPConnection` 换成可控 fake 构造，测：
   ①全部成功——工具汇总且按 `full_name` 排序（顺序由 sort 决定，与 task 完成顺序无关）；
   ②**失败隔离（真坏 command + 注入 stub 成功组合）**——起两个 server：一个直接构造 `MCPConnection` 指向 `command="/no/such/bin"` 的 stdio（真触发 SDK `stdio_client` 失败路径），另一个 monkeypatch 让其 `connect_and_list` 返回 fake 工具；断言 fake 工具被收集、坏 server 仅产生 stderr 告警且其连接不进 `_connections`；
   ③超时收尾——注入挂起的 fake `connect_and_list`（`await asyncio.Event().wait()`），把 `connect_timeout` 临时改 0.2，断言 `start_all` 在 ~0.2s 内返回且 stderr 有 timeout 告警，restore；
   ④`close` 兜底不死锁——注入 close 阻塞的 fake conn（`close` 内 `await asyncio.Event().wait()`），把 `close_timeout` 改 0.2，断言 `close()` 在 0.2s 内返回不卡死，restore；
   ⑤空 servers——`start_all` 立即完成、`tools()` 为空；
   ⑥**同名告警**——构造两个 fake 连接产出同名 `full_name` 的工具，`start_all` 汇总后 stderr 出现一次 `duplicate tool` 告警、`tools()` 中保留后入者（覆盖 spec F8 收集阶段告警）。
   # 每个测试结束断言无悬挂 task：`assert not asyncio.all_tasks() - {asyncio.current_task()}`（或等价）。
   # docstring 注明防的 bug（如「某 server 超时拖死整个 start_all」「close 卡死阻塞退出」）。

**验证：**
- `ruff format --check mewcode/mcp/manager.py tests/test_mcp_manager.py` 无 diff
- `ruff check mewcode/mcp/manager.py tests/test_mcp_manager.py` 无告警
- `python -m pytest tests/test_mcp_manager.py -q` 全过
- `python -c "from mewcode.mcp.manager import MCPManager, connect_timeout; print('ok')"` → `ok`

---

## T5: 子包门面（__init__.py）

**文件：** `mewcode/mcp/__init__.py`
**依赖：** T1、T2、T3、T4
**步骤：**
1. 导出 `MCPManager`、`MCPConnection`、`McpTool`、`CallerSession`、`ServerConfig`、`load_mcp_servers`，并设 `__all__`。
2. 子包 docstring 一句：说明 MCP 客户端职责与「仅依赖 tools/provider/sdk，不依赖 agent/tui/permission」。

**验证：**
- `ruff format --check mewcode/mcp/__init__.py` 无 diff
- `ruff check mewcode/mcp/__init__.py` 无告警
- `python -c "from mewcode.mcp import MCPManager, MCPConnection, McpTool, CallerSession, ServerConfig, load_mcp_servers; print('ok')"` → `ok`
- `python -m pytest tests/test_mcp_config.py tests/test_mcp_wrapper.py tests/test_mcp_conn.py tests/test_mcp_manager.py -q` 全过（子包全量回归）

---

## T6: 权限规则泛化——正则放宽 + 工具名通配（rules.py）

**文件：** `mewcode/permission/rules.py`、`tests/test_permission_rules.py`
**依赖：** 无（与 T1–T5 可并行）
**步骤：**
1. 放宽 `rules.py:23` 的 `_RULE_PARSE_RE`：从 `^(Bash|Read|Write|Edit|Glob|Grep)(?:\((.*)\))?$` 改为 `^([A-Za-z0-9_-]+)(?:\((.*)\))?$`（接受任意合法工具名，含 `mcp__` 前缀；仍因 `[A-Za-z0-9_-]+` 拒非法字符）。
2. 改 `RuleSet.match`（rules.py:104 附近）工具名比对：新增模块级 `_tool_name_matches(rule_name: str, friendly: str) -> bool`——rule_name 含 `*` 时 `fnmatch.fnmatchcase(friendly, rule_name)`，否则 `rule_name == friendly`；把 `if rule.tool_name == friendly and rule.match_target(target):` 改为 `if _tool_name_matches(rule.tool_name, friendly) and rule.match_target(target):`。括号内 target 的 `*` 语义（`match_pattern`）不变。
3. 在 `tests/test_permission_rules.py` 补：①`mcp__github__create_issue` 能被 `Rule.parse` 接受；②裸写 `mcp__github__*`（无括号、pattern=""）→ match `mcp__github__create_issue` 为 True、match `mcp__other__x` 为 False；③带括号 `mcp__fs__read_file(/path)` 仍解析正确；④6 个内置友好名原用例仍全过（不回归）；⑤含非法字符的规则（如 `mcp__a b__x`）被 `Rule.parse` 拒绝返回 None。

**验证：**
- `ruff format --check mewcode/permission/rules.py tests/test_permission_rules.py` 无 diff
- `ruff check mewcode/permission/rules.py tests/test_permission_rules.py` 无告警
- `python -m pytest tests/test_permission_rules.py -q` 全过（新 mcp__ 用例 + 原用例不回归）

---

## T7: 权限 allow_always 对 MCP 落盘裸名（checker.py）

**文件：** `mewcode/permission/checker.py`、`tests/test_permission_checker.py`
**依赖：** T6（规则正则已接受 mcp__ 名，裸名规则才可被加载匹配）
**步骤：**
1. 改 `persist_local_allow`（checker.py:218）：方法开头判 `if tool_call.tool_name.startswith("mcp__"):`，则 `rule_str = fn`（裸工具名精确规则，不加括号、不取 target——MCP 工具 `extract_target` 返回 `ok=False`，原凭 target 落盘对 MCP 是空操作）；跳过后续 `info.is_file` 分支，直接进入「读现有配置→去重追加→写回」逻辑。内置工具逻辑保持原样。
2. 在 `tests/test_permission_checker.py` 补：①对 `mcp__github__create_issue` 的 `ToolCall` 调 `persist_local_allow` → 本地 rules 文件 allow 列表出现 `mcp__github__create_issue`（无括号）；②再调一次→去重不重复；③内置 `execute_command` 工具落盘仍走带括号/转义既有逻辑（不回归）。用 `tmp_path` 隔离本地文件，测完清理。

**验证：**
- `ruff format --check mewcode/permission/checker.py tests/test_permission_checker.py` 无 diff
- `ruff check mewcode/permission/checker.py tests/test_permission_checker.py` 无告警
- `python -m pytest tests/test_permission_checker.py -q` 全过
- `python -m pytest tests/test_permission_rules.py tests/test_permission_engine.py tests/test_permission_agent.py tests/test_permission_sandbox.py tests/test_permission_blocklist.py tests/test_permission_tui.py -q` 全过（五层权限全量不回归）

---

## T8: 启动接入与单 loop 收尾（main.py）

**文件：** `mewcode/main.py`
**依赖：** T5（mcp 子包）、T6（规则泛化）、T7（落盘泛化）
**步骤：**
1. 顶部 import：`from mewcode.mcp import MCPManager, load_mcp_servers`、`from mewcode import __version__`。
2. 把 `main()` 的「建 registry → 跑 TUI/oneshot → 退出」收进 `async def _amain(args, config, provider)`；`main()` 末尾改为 `asyncio.run(_amain(args, config, provider))`（**取代原 main.py:123/133 的两次 asyncio.run**，TUI / oneshot / MCP 共享同一 loop）。
3. `_amain` 内：`registry = Registry.default()`；`permission = PermissionChecker.create(project_root)`（建在 MCP 之前）；`--mode` 覆盖仍在；`mcp_servers = load_mcp_servers(os.getcwd())`；`mcp_mgr = MCPManager(mcp_servers, client_version=__version__)`；`summary = await mcp_mgr.start_all()`；`if not summary.is_empty: print(MCPManager.format_summary(summary), file=sys.stderr)`（**启动可观测摘要，spec N5**）；`for t in mcp_mgr.tools(): registry.register(t)`；构造 agent/renderer/plan_manager（原逻辑）。
4. `try:` 跑 `await _oneshot(...)`（`_oneshot` 从 `def` 改 `async def`，去掉其内部 `sys.exit(1)`，改为 raise 让 finally 跑）或 `await repl.run()`；`finally: await mcp_mgr.close()`。banner 打印条件不变。`REPL.run` 已 async，无需改。
5. 版本号、provider 解析等同步部分保持。

**验证：**
- `ruff format --check mewcode/main.py` 无 diff；`ruff check mewcode/main.py` 无告警
- `python -m mewcode --version` → `mewcode 0.7.0`
- `python -c "import mewcode.main; print('import ok')"` → import 链不断
- 全量回归：`python -m pytest -q` 全过（ch01–ch06 既有 + ch07 新测试）

---

## T9: 配置示例文件 + 反向测试覆盖

**文件：** `docs/ch07/mcp-servers.example.yaml`、`tests/test_mcp_config.py`（追加用例）
**依赖：** T1（解析逻辑就绪）
**步骤：**
1. 新建 `docs/ch07/mcp-servers.example.yaml`（YAML 注释说明放置位置与覆盖语义），含 stdio 与 http 各类，凭据一律 `${VAR}`：
   ```yaml
   # 项目级放 <root>/.mewcode.yaml；用户级放 ~/.mewcode/config.yaml。
   # 同名 server 项目级完整覆盖用户级。
   # env / headers 的值支持 ${VAR} 从宿主环境变量展开；command/args 不展开。
   mcp_servers:
     github:
       type: stdio
       command: npx
       args: ["-y", "@modelcontextprotocol/server-github"]
       env:
         GITHUB_TOKEN: "${GITHUB_TOKEN}"
     local-sqlite:
       type: stdio
       command: python
       args: ["-m", "mcp_server_sqlite", "--db", "./data.db"]
     example-http:
       type: http
       url: "https://mcp.example.com/mcp"
       headers:
         Authorization: "Bearer ${EXAMPLE_TOKEN}"
   ```
   # 注意：docs/ 下文件受 CLAUDE.md docs 保护规则约束——本文件是新建交付物（非四份 spec 流程产物），
   # 创建即定稿，后续不在测试/验证过程中回改它；测试只**读**它。
2. 在 `tests/test_mcp_config.py` 追加：`monkeypatch.setenv("GITHUB_TOKEN","x")`/`setenv("EXAMPLE_TOKEN","x")` 避免 undefined 噪音，读 `docs/ch07/mcp-servers.example.yaml`（用 `Path` 定位项目根 + `docs/ch07/...`）→ 断言三个 server 都解析成功（`github`/`local-sqlite`/`example-http` 都在结果 dict，type 正确，`${VAR}` 已展开或保留字面量正确）。此用例把示例文件纳入测试覆盖，防止示例写错或后续改解析逻辑时示例悄悄失配。

**验证：**
- `ruff format --check tests/test_mcp_config.py` 无 diff
- `ruff check tests/test_mcp_config.py` 无告警
- `python -m pytest tests/test_mcp_config.py -q` 全过（含示例文件反向用例）

---

## T10: tmux 端到端实跑【待人工验证】

> 本任务依赖真实 stdio MCP server 与真实终端，无法在无终端/无网络环境自动执行。
> 按 CLAUDE.md「验证受阻必上报」纪律：列为「待人工验证」，**不混入可自动验证任务的「通过」**。

**文件：** —（用临时 `.mewcode.yaml`，测完恢复项目根干净）
**依赖：** T1–T9
**步骤：**
1. 准备真实可用的 stdio MCP server。优先 `npx -y @modelcontextprotocol/server-everything`（官方示例，自带 echo/add 等基础工具）；若无 npx 用最小 Python server。
2. 项目根写临时 `.mewcode.yaml`：
   ```yaml
   mcp_servers:
     demo:
       type: stdio
       command: npx
       args: ["-y", "@modelcontextprotocol/server-everything"]
   ```
3. `tmux` 起 mewcode，人工观察：
   - 启动 stderr 显示 server 连接成功 + 工具数；TUI 状态栏正常；
   - 让模型调 `mcp__demo__echo` 一类工具：default 模式弹人在回路→允许本次→结果回灌→模型续答；
   - 选「永久允许」后本地权限规则被写入；重启 mewcode再调同工具不再弹窗（验证永久规则 + MCP 命名空间联动）；
   - 切 bypassPermissions：调用不弹窗；让模型跑 `rm -rf /` 仍被内置黑名单拦下（MCP 工具不绕过黑名单作用域）；
   - Esc 取消弹窗：干净回到 idle，不退出；
   - 退出 mewcode 后 `ps -ef | grep server-everything` 确认子进程已终止（AC10 退出干净）；
4. 配一个 command 不存在的 server + 一个能跑的 server：启动 stderr 有失败告警，能跑的 server 工具仍可用（AC8 启动失败隔离）。

**验证方式（人工）：** 上述全部观察通过；删临时 `.mewcode.yaml`，恢复项目根干净。
**若环境受限无法验证：** 标「待人工验证」，说明原因（无 npx/无真实 MCP server/无真实终端）、替代为 T3/T4 的 fake session/stub 覆盖的集成层、风险（真连接生命周期/退出子进程终止未在 CI 验证）、责任方（由谁在有环境时补验）。

---

## T11: 全量规范与凭据扫描

**文件：** —
**依赖：** T1–T10
**步骤：**
1. `ruff format --check .`（无 diff）；`ruff check .`（无告警）。
2. （可选）`mypy mewcode/mcp`（strict 子集亦可）。
3. `python -m pytest -q`（含 `tests/test_mcp_*.py` 全量）。
4. 重点守护并发/收尾：`python -m pytest tests/test_mcp_manager.py -q` 内用例已断言无悬挂 task、close 不死锁。
5. 凭据不落盘扫描：`git grep -nE "(Bearer|sk-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}"`（无命中）；确认 `docs/ch07/mcp-servers.example.yaml`、项目根 `.mewcode.yaml` 无 token 明文。
6. docs 保护自检：确认 T1–T11 全程未改动 `docs/` 下任何已存在文件（仅 T9 新建了 `docs/ch07/mcp-servers.example.yaml`，属新建交付物）——`git status docs/` 应仅显示该新建文件，无对既有文档的修改。

**验证：** 全部通过。

---

## 执行顺序

```
T0 ─┬─► T1 ─┐
    │       ├─► T3 ─► T4 ─► T5 ─┐
    └─► T2 ─┘                     ├─► T8 ─► T10 ─┐
       T6 ─► T7 ──────────────────┘                ├─► T11
              T9（依赖 T1，可与 T2–T7 并行）────────┘
```

- T0 最先（版本独立提交 + mcp 可 import）。
- T1（config）与 T2（wrapper）互不依赖，可与 T6（permission rules）三者并行起步。
- T3（conn）依赖 T0+T2；T4（manager）依赖 T2+T3；T5（门面）依赖 T1–T4。
- T6（rules 泛化）→ T7（checker 落盘）串行，整条与 T1–T5 并行。
- T9（配置示例 + 反向测试）依赖 T1 解析逻辑，可与 T2–T7 并行。
- T8（main 接入）是终点起点，依赖 T5+T7。
- T10（tmux 人工）依赖 T1–T8；T11（规范扫描）依赖全部。
- 提交节奏（遵循 CLAUDE.md「每组逻辑相关任务完成后提交」）：T0 版本号独立提交；依赖追加独立提交；T1+T2+T9（配置侧）一组；T3+T4+T5（会话与生命周期）一组；T6+T7（permission 泛化）一组；T8 一组；T10 不产代码改动（不提交）；T11 一组。

## 自检

- **plan 覆盖**：plan 每个模块各 ≥1 任务：config→T1+T9、wrapper→T2、conn→T3、manager→T4、__init__→T5、rules→T6、checker→T7、main→T8、pyproject+版本→T0、配置示例→T9、规范→T11、人工实跑→T10。✓
- **占位符扫描**：无「类似 TX」模糊引用；步骤具体到符号名与行号锚点（rules.py:23/104、checker.py:218、main.py:123/133）。
- **依赖链**：T0→T3→T4→T5→T8→T10→T11、T6→T7→T8、T2→T3、T1→T9、T1→T8，无环。
- **验证完整性**：可自动验证任务均含 ruff format/check + pytest；T10 明确标「待人工验证」不混入通过；每测试 docstring 注明防的 bug。
- **类型一致性**：与 plan.md 一致——`ServerConfig`（含 `name`）、`MCPConnection.connect_and_list/call_tool/close`（holder task 持有传输上下文）、`MCPManager.start_all(->StartupSummary)/tools/close/format_summary`、`McpTool`、`make_tool(server_name, caller, remote)`、`CallerSession`、`StartupSummary`、模块级 `connect_timeout`（conn 之 `call_timeout` 由 manager import）。
- **异步测试标记**：ch07 新测试沿用 repo 约定 `@pytest.mark.anyio`（与 tests/test_agent.py、tests/test_tools.py 一致），非 `pytest-asyncio`/`@pytest.mark.asyncio`。