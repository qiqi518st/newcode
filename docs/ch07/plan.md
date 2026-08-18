# MewCode ch07 — MCP 客户端 技术设计 (plan.md)

## 架构概览

本章在 ch06 五层权限系统之上，新增一个 **`mewcode.mcp` 子包**，用适配器模式把外部 server 的工具无缝接进 MewCode 既有的工具/权限/调度管线。所有协议细节下沉到官方 `mcp` SDK（`pip install mcp`，import 名 `mcp`），MewCode 只负责配置、生命周期、适配、注册集成。

```
                                  ┌─────────────────────────────────────────┐
                                  │               Agent / ToolScheduler       │
                                  │      （既有，零改动：按 tool.read_only 分批） │
                                  └──────────────────┬──────────────────────────┘
                                                     │ registry.get(name).execute(args)
                                                     ▼
                                  ┌─────────────────────────────────────────────┐
                                  │                  Registry（既有）             │
                                  │   内置 6 工具 + 装配处注册的 McpTool 实例      │
                                  └──────────────────┬──────────────────────────┘
                                                     │ register(McpTool)  ← 装配处（main）负责
                                  ┌──────────────────┴──────────────────────────┐
                                  │              MCPManager（新增）              │
                                  │  并发连接 → 各连接产各自工具 → 暴露 tools()     │
                                  │  统一生命周期：start_all / close              │
                                  │  不依赖 Registry，与 Registry 解耦             │
                                  └──────┬───────────────────────┬───────────────┘
                                         │                       │
                            start_one()  │                       │
                                         ▼                       ▼
                                  ┌──────────────┐        ┌─────────────────┐
                                  │  MCPConnection│        │  MCPConnection │
                                  │  (stdio)      │        │  (http)        │
                                  │ 自己 async 上下文 │   │ 自己 async 上下文 │
                                  └───────┬──────┘        └────────┬────────┘
                                          │                        │
                                          ▼                        ▼
                                   官方 mcp SDK            官方 mcp SDK
                              stdio_client+ClientSession   streamablehttp_client+ClientSession

        配置侧：load_mcp_servers 读两层 yaml + ${VAR} 展开 + 校验（纯函数，不碰 registry）
        权限侧：permission 包一次性泛化（正则放宽 + 工具名通配 + allow_always 落盘）
        生命周期：main._amain 单一事件循环贯穿 start_all → app 跑 → close（同一 loop）
```

### 组件划分

- **`mcp.config`（纯函数）**：两层 YAML 加载、按 server 名合并（项目级完整覆盖）、`${VAR}` 展开、字段校验、非法 server 隔离。返回 `dict[str, ServerConfig]`，**不碰 registry、永不抛**。
- **`ServerConfig`（数据类）**：单个 server 的归一化定义，字段已展开、已校验。
- **`MCPConnection`**：单 server 会话句柄。传输 / session 上下文由**内部长寿命 holder task** 经 `AsyncExitStack` 持有（holder 在 connect_and_list 返回后继续存活，close 取消 holder 触发上下文在 holder 自身 task 内退出）；`call_tool` 包 30s 超时并翻译结果。**规避共享 AsyncExitStack 并发竞态与 anyio cancel scope 跨 task 退出**（spec N7）。
- **`MCPManager`**：生命周期编排器。`start_all()` 并发起所有连接、收集各连接产出的工具、稳定排序；`tools()` 返回工具列表副本；`close()` 并发关全部连接（单层 5s 兜底）。**与 Registry 解耦**——只产工具，注册由装配处（main）负责。
- **`McpTool`**：实现既有 `Tool` 协议。持 `CallerSession`（Protocol），名字 `mcp__<server>__<tool>`、参数透传、只读性取 `readOnlyHint`、`execute` 转走所属 `CallerSession.call_tool`，把远端结果翻译成 repo 现有的 `ToolResult(status, output, error, truncated)`。
- **`main._amain`（单 loop 改造）**：把整条启动链（`load_config → start_all → 注册 → repl/oneshot 跑 → close`）收进一个 `asyncio.run(_amain())`，让 MCP session 与运行循环同寿；`try/finally` 保证退出收尾。
- **`permission` 包泛化**：规则解析正则放宽接受任意合法工具名（含 `mcp__` 前缀）、`RuleSet.match` 工具名比对加 `*` 通配（无通配时等价 `==`）、`persist_local_allow` 对 MCP 工具落盘裸工具名精确规则。泛化后未来新增 MCP 工具零改动 permission。

## 核心数据结构

### ServerConfig

```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class ServerConfig:
    """单个 MCP server 的完整定义（已展开 ${VAR}、已校验）。"""
    name: str                      # server 名，配置里的 key，原样保留
    type: Literal["stdio", "http"]
    # stdio 专属
    command: str = ""              # stdio 必填非空
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)   # 已展开 ${VAR}
    # http 专属
    url: str = ""                 # http 必填非空
    headers: dict[str, str] = field(default_factory=dict)  # 已展开 ${VAR}
```

字段约定：`type=="stdio"` 时 `command` 必须非空；`type=="http"` 时 `url` 必须非空。校验失败在 `load_mcp_servers` 内跳过该 server 并告警，**不构造出非法 Config**。

### MCPConnection

```python
class MCPConnection:
    def __init__(self, server: ServerConfig, client_version: str) -> None: ...
    server_name: str                    # 只读，= server.name
    _type: Literal["stdio", "http"]
    _session: ClientSession | None      # connect_and_list 成功后置位
    # 传输上下文句柄（read/write 通道），存 self 以便 close

    async def connect_and_list(self) -> list[McpTool]
        # 自己 async with 打开传输 + ClientSession → initialize() → list_tools()
        # 整体受 connect_timeout（模块级，默认 30s）约束；失败抛 MCPStartupError
        # 成功返回本连接适配好的 McpTool 列表（交给 MCPManager 收集）

    async def call_tool(self, tool_name: str, arguments: dict) -> ToolResult
        # asyncio.wait_for(self._session.call_tool(...), call_timeout)（模块级，默认 30s）
        # 拼接 TextContent.text → output / 映射 is_error → status / 非text块丢弃+告警
        # 超时/协议错 → ToolResult(status="error", error=结构化文案)，不向调用方抛

    async def close(self) -> None
        # 退出自身传输 + session 上下文；自身不善再 timeout
        # （MCPManager.close 的单层 5s 兜底已覆盖）
```

`connect_and_list` 把"打开传输 + 握手 + 列工具"合在一个方法里，因为 SDK 传输是 `async with` 上下文管理器，必须在一个协程内打开并贯穿连接生命周期。MCPConnection 在该方法内进入上下文、把 `session` / 传输通道存到 `self`，供后续 `call_tool` 复用，并在 `close()` 时干净退出。返回的 `McpTool` 列表里每个工具的 `caller` 指向 `self`（连接）。

### MCPManager

```python
class MCPManager:
    def __init__(self, servers: dict[str, ServerConfig], client_version: str) -> None: ...
    _connections: list[MCPConnection]      # 成功建立的连接（供 close）

    async def start_all(self) -> None
        # asyncio.gather(*[self._start_one(s) for s in servers.values()], return_exceptions=True)
        # 每个 _start_one：await asyncio.wait_for(conn.connect_and_list(), connect_timeout)
        #   成功 → 收集该连接的工具 + 记录连接；失败/超时 → stderr 告警，跳过该连接
        # 所有连接尝试结束后，对收集到的全部工具按 full_name 稳定排序
        # 本方法不可失败——只产告警，不抛（保证 main 启动不被阻断）
        # 空 servers 字典时 gather 空列表立即返回

    def tools(self) -> list[McpTool]
        # 返回排序后的工具列表副本（防外部修改），供装配处注册

    async def close(self) -> None
        # await asyncio.wait_for(
        #     asyncio.gather(*[c.close() for c in self._connections], return_exceptions=True),
        #     close_timeout)   # 模块级，默认 5s
        # 超时 → stderr 告警 some sessions may leak，不再等（进程退出/连接断兜底收尾）

    @property
    def connections(self) -> list[MCPConnection]   # 测试用只读视图
```

### McpTool

```python
from typing import Protocol

class CallerSession(Protocol):
    """call_tool 的抽象，便于单测注入 stub；生产实现是 MCPConnection。"""
    async def call_tool(self, tool_name: str, arguments: dict) -> ToolResult: ...

class McpTool:
    def __init__(self, caller: CallerSession, full_name: str, remote_name: str,
                 description: str, parameters: dict, read_only: bool) -> None: ...
    @property
    def name(self) -> str               # "mcp__{server}__{remote_name}"
    @property
    def description(self) -> str
    @property
    def parameters(self) -> dict         # 透传 inputSchema（空则 {"type":"object"} 兜底）
    @property
    def read_only(self) -> bool         # 取自 readOnlyHint
    async def execute(self, arguments: dict) -> ToolResult
        # 转发 caller.call_tool(self._remote_name, arguments)
```

适配分两处：MCPConnection 列工具时拿到 SDK `Tool` 元数据，逐个调 `make_tool(self.server.name, self, remote)` 计算 `mcp__` 前缀名并校验禁用字符；通过校验才 `new McpTool`，否则跳过该工具 + stderr 告警。`McpTool` 持 `CallerSession` Protocol（指向连接）而非具体 `ClientSession`，单测可注入 stub。

## 核心接口

```python
# 加载并合并两层配置；返回归一化的 server 字典。纯函数。
# - root: 项目根（定位 <root>/.mewcode.yaml）
# - 文件不存在 → 该层视为空；格式非法 → 跳过该层 + stderr 告警（降级，不抛）
# - 内部完成 ${VAR} 展开（仅 env/headers 的值）与字段校验（非法 server 直接剔除）
# - 永不抛出
def load_mcp_servers(root: str) -> dict[str, ServerConfig]: ...

# 模块级超时变量（非字面常量），便于单测临时改小并 restore；生产值 30 / 30 / 5
connect_timeout: float = 30.0
call_timeout: float = 30.0
close_timeout: float = 5.0

async def MCPManager.start_all(self) -> None: ...
def MCPManager.tools(self) -> list[McpTool]: ...
async def MCPManager.close(self) -> None: ...
async def MCPConnection.connect_and_list(self) -> list[McpTool]: ...
async def MCPConnection.call_tool(self, tool_name: str, arguments: dict) -> ToolResult: ...
async def MCPConnection.close(self) -> None: ...
async def McpTool.execute(self, arguments: dict) -> ToolResult: ...
```

## 模块设计

### mewcode/mcp/__init__.py
**职责：** 子包门面，导出 `MCPManager`、`MCPConnection`、`McpTool`、`CallerSession`、`ServerConfig`、`load_mcp_servers`。
**对外接口：** `__all__` 导出上述符号。
**依赖：** 仅 `mewcode.tools`、`mewcode.provider.base`（`ToolResult`）、`mcp` SDK、标准库；**不依赖 agent / tui / permission / conversation / config**。

### mewcode/mcp/config.py
**职责：** 两层 YAML 加载、合并、字段校验、`${VAR}` 展开、非法 server 隔离。纯函数。
**对外接口：** `load_mcp_servers(root: str) -> dict[str, ServerConfig]`、数据类 `ServerConfig`。
**依赖：** `pyyaml`、`os`、`re`、标准库 `sys`（告警）。
**关键点：**
- 用户级路径 `~/.mewcode/config.yaml`、项目级路径 `<root>/.mewcode.yaml`。两文件各取 `mcp_servers` 段。
- `_load_file(path) -> dict`：不存在 → `{}`；读/`yaml.safe_load` 失败 → stderr 一行告警 + `{}`（调用方降级，不抛）；取 `mcp_servers` 段，缺失视为空。
- `_merge_servers(user, project) -> dict`：复制 user，遍历 project，同名直接整对象覆盖（不做字段级合并）。
- `_expand_value(s: str, server_name: str, seen_undef: set[str]) -> str`：正则 `\$\{([A-Za-z_][A-Za-z0-9_]*)\}` 用 `os.environ.get` 取值；未定义变量 → 空串 + 记入 `seen_undef`。**仅作用于 `env` / `headers` 的值**。同一 server 同一未定义变量限一次告警（局部 set 去重）。
- `_validate_server(name, raw) -> ServerConfig | None`：`type` 必为 `stdio`/`http`；stdio 必填非空 `command`，http 必填非空 `url`；任一不通过跳过 + stderr 告警 `[mcp] warn: skip server <name>: <reason>`。
- `load_mcp_servers(root)`：两层各自 `_load_file` → 各自对可用 server 跑展开 → `_merge_servers` → 逐个 `_validate_server` 组装结果字典。

### mewcode/mcp/conn.py
**职责：** 单 server 会话的生命周期与调用，封装官方 SDK 传输 + `ClientSession`。
**对外接口：** `MCPConnection` 类（`connect_and_list` / `call_tool` / `close`）、`MCPStartupError` 异常。（`CallerSession` Protocol、`make_tool`、`_VALID_NAME` 均**归 `wrapper.py`**，conn 依赖 wrapper。）
**依赖：** 官方 `mcp` SDK（`stdio_client`、`StdioServerParameters`、`streamablehttp_client`、`ClientSession`、`TextContent` 等类型、`Implementation`）、`asyncio`、`os`、标准库 `sys`、本包 `wrapper`（`make_tool`）。
**关键点：**
- stdio 传输：
  ```python
  from mcp.client.stdio import stdio_client
  from mcp import StdioServerParameters
  params = StdioServerParameters(command=srv.command, args=srv.args,
                                  env={**os.environ, **srv.env})  # srv.env 覆盖同名宿主
  transport_ctx = stdio_client(params)       # async with → (read_stream, write_stream)
  ```
- http 传输（SDK 2.0：`streamable_http_client` 无 `headers` 参数，自定义 headers 经预配置的 `httpx.AsyncClient` 注入；`httpx` 名即 SDK 依赖 httpx2）：
  ```python
  from mcp.client.streamable_http import streamable_http_client
  import httpx
  http_client = httpx.AsyncClient(headers=srv.headers) if srv.headers else None
  transport_ctx = streamable_http_client(srv.url, http_client=http_client)
  # async with → (read_stream, write_stream)   ← 与 stdio 同为 2 元组
  ```
- 打开会话（**长寿命 holder task 持有上下文**，替代共享/私有 AsyncExitStack——见决策表「传输上下文管理」）：
  ```python
  # connect_and_list：spawn holder，等 ready
  self._holder = asyncio.create_task(self._hold())
  await self._ready.wait()          # 失败时 _connect_error 被置位
  if self._connect_error is not None:
      raise MCPStartupError(...)
  return list(self._tools)

  # _hold：在自身 task 内经 AsyncExitStack 持有传输/session，停在 _stop 上
  async with contextlib.AsyncExitStack() as stack:
      transport = await stack.enter_async_context(transport_ctx)
      read_stream, write_stream = transport       # stdio / http 都是 2 元组
      session = await stack.enter_async_context(
          ClientSession(read_stream, write_stream,
                        client_info=Implementation(name="mewcode", version=self._client_version))
      )
      await session.initialize()            # 握手，报 client_info
      listed = await session.list_tools()
      self._session = session
      self._tools = [t for t in (make_tool(self.server.name, self, remote)
                                 for remote in listed.tools) if t is not None]
      self._ready.set()
      await self._stop.wait()              # 保持上下文存活，直到 close 取消本 task
  # close：_stop.set() + _holder.cancel() → async with 在 holder 自身 task 内退栈
  ```
  为什么用 holder：`stdio_client` 用 anyio 实现，其 cancel scope 绑定进入它的 task；若在 `connect_and_list` 所在 task 进栈、`close()` 在另一 task 调 `aclose()` 退栈，anyio 抛「Attempted to exit cancel scope in a different task」。holder 让上下文始终在**同一 task** 进出（spec N7 退出干净，实测曾出现该告警）。
- `call_tool`：`result = await asyncio.wait_for(self._session.call_tool(remote, args), call_timeout)`；**SDK 2.0 `call_tool` 返回 `CallToolResult | InputRequiredResult | Result` 联合类型**——先 `isinstance(result, CallToolResult)` 分流，非 `CallToolResult`（如 `InputRequiredResult`）按协议错处理；对 `CallToolResult`，取 `result.content` 中 `isinstance(block, TextContent)` 的 `.text` 按序拼接，`result.is_error` 决定 `status`；非 text 块静默丢弃 + stderr 告警（per `full_name` 限一次，用 set 去重）。
- **三种失败统一转 `ToolResult`，不向调用方抛**（复用"不中断会话"契约）：超时 → `(status="error", error="MCP 工具调用超时 (30s)")`；`session.call_tool` 抛异常 → `(status="error", error=f"MCP 工具调用失败: {e}")`；返回非 `CallToolResult`（`InputRequiredResult` 等） → `(status="error", error="MCP 工具返回非预期结果类型")`；传输断同异常分支。
- 结果映射（兼容 repo 现有 `ToolResult`）：远端非错 → `status="ok"`、文本进 `output`；远端 `is_error==True` → `status="error"`、文本进 `error`；协议错/超时 → `status="error"`、原因进 `error`。`truncated` 保持默认 False。
- `make_tool(server_name, caller, remote)`：`full_name = f"mcp__{srv.name}__{remote.name}"`（`srv.name` 通过连接持有的 `ServerConfig.name` 取得，但 `make_tool` 形参用 `server_name` 字符串 + `caller: CallerSession`，**不依赖具象 `MCPConnection`**，契合「CallerSession Protocol」决策）；禁用字符校验 `_VALID_NAME.fullmatch(full_name)`（`_VALID_NAME = ^[A-Za-z0-9_-]+$`）不通过 → 返回 None + 告警；`description = remote.description or f"MCP 工具（来自 server {srv.name}）"`；`parameters = dict(remote.input_schema) or {"type":"object"}`（SDK 2.0 为 `input_schema` snake_case）；`read_only = bool(remote.annotations and remote.annotations.read_only_hint)`（SDK 2.0 为 `read_only_hint` snake_case）。conn 调用形为 `make_tool(self.server.name, self, remote)`。

### mewcode/mcp/wrapper.py
**职责：** `McpTool` 适配器实现 + `CallerSession` Protocol + `make_tool`。
**对外接口：** `McpTool` 类（`name/description/parameters/read_only/execute`）、`CallerSession`、`make_tool`、模块常量 `_VALID_NAME`。
**依赖：** `MCPConnection`（作为 `CallerSession` 的生产实现）、`ToolResult`。
**关键点：**
- `McpTool` 是普通类（非 dataclass 也可，便于 `name` 等 property 透传字段），持 `caller: CallerSession`、`_remote_name`、`_full_name` 等。
- `execute(arguments)`：`return await self.caller.call_tool(self._remote_name, arguments)`——翻译已在 `MCPConnection.call_tool` 完成，wrapper 不再 try/except（让 ToolScheduler 的统一 `except` 兜底极端情况）。
- `_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")`。
- `read_only` 严格只信 `annotations.readOnlyHint==True`（None-safe），缺失/非法 → False（安全默认走 Ask）。

### mewcode/mcp/manager.py
**职责：** 多 server 生命周期编排：并发启动、收集工具、稳定排序、统一关闭、失败隔离。
**对外接口：** `MCPManager` 类（`start_all` / `tools` / `close`）、模块级 `connect_timeout` / `close_timeout`。（不提供 async 工厂；装配处用 `MCPManager(servers, client_version)` + `await start_all()` 两步，见 main 设计。）
**依赖：** `ServerConfig`、`MCPConnection`、`McpTool`、`asyncio`。
**关键点：**
- `start_all`：`asyncio.gather(*[self._start_one(s) for s in servers.values()], return_exceptions=True)`；每个 `_start_one`：`conn = MCPConnection(s, version)`；`await asyncio.wait_for(conn.connect_and_list(), connect_timeout)` → 成功则 `self._connections.append(conn)`、把该连接返回的工具 join 进临时收集；失败/超时 stderr 告警（含 server 名与原因）并跳过。
- 收集汇总后，按 `tool.full_name` 对全部工具**稳定排序**。
- **同名告警归收集阶段**（spec F8）：`start_all` 汇总 `_tools` 时若发现 `tool.full_name` 已被某前序工具占用，则 stderr 告警 `[mcp] warn: duplicate tool <full_name>, later registration overrides earlier`，后保留后入者。此为 Manager 自身职责，装配处（main）注册时直接覆盖即可不再重复告警。
- `close`：`await asyncio.wait_for(asyncio.gather(*[c.close() for c in cons], return_connections=True), close_timeout)`（单层兜底），超时 → 告警 `some sessions may leak`，不再等。
- `start_all` 本身**不可失败**——只产告警不抛。空 servers → 空列表 gather 立即返回。

### mewcode/permission/rules.py（修改）
**改动 1**：`_RULE_PARSE_RE`（rules.py:23）从 `^(Bash|Read|Write|Edit|Glob|Grep)(?:\((.*)\))?$` 放宽为接受任意合法工具名：
  `^([A-Za-z0-9_-]+)(?:\((.*)\))?$`。`Rule.parse` 其余逻辑不变。
**改动 2**：`RuleSet.match`（rules.py:104）工具名比对由 `rule.tool_name == friendly` 改为**支持 `*` 通配**：无 `*` 时等价 `==`，含 `*` 时用 `fnmatch.fnmatchcase(friendly, rule.tool_name)`。令 `mcp__github__*` 能匹配 `mcp__github__create_issue`。括号内 target 的 `*` 语义不变。

### mewcode/permission/checker.py（修改）
**改动 3**：`persist_local_allow`（checker.py:218）对 MCP 工具落盘**裸工具名精确规则**：检测 `tool_call.tool_name` 以 `mcp__` 开头时，`rule_str = f"{fn}"`（不加括号、不取 target——MCP 工具 `extract_target` 返回 `ok=False`，原凭 target 落盘的逻辑对 MCP 是空操作，需在 MCP 情况下改走裸工具名）。内置工具逻辑保持原样。

### mewcode/main.py（修改）
**改成单一事件循环 `_amain`**，让 MCP session 与运行循环同寿：
```python
async def _amain(args, config, provider, ...) -> None:
    cwd = os.getcwd()
    registry = Registry.default()
    permission = PermissionChecker.create(cwd)  # 在 MCP 之前建（权限层不依赖 MCP）

    # —— MCP 接入 ——
    mcp_servers = load_mcp_servers(cwd)
    mcp_mgr = MCPManager(mcp_servers, client_version=__version__)
    await mcp_mgr.start_all()
    for t in mcp_mgr.tools():
        registry.register(t)

    ...其余 agent/renderer/plan_manager 构造...

    try:
        if args.command:
            await _oneshot(args.command, agent, mode)
        else:
            print(render_banner(...))
            await repl.run()
    finally:
        await mcp_mgr.close()          # 单层 5s 兜底，不卡退出

def main() -> None:
    ...argparse + 配置加载 + provider 构造...
    asyncio.run(_amain(args, config, provider, ...))
```
要点：
- `PermissionChecker.create` 在 MCP start_all 之前——权限层不依赖 MCP（泛化对 MCP 已就绪，但建得早/晚皆可，放前面更清晰）。
- `start_all()` 显式 await（非塞进 async 工厂），步骤分明、便于调试与 finally 编排。
- `finally: await mcp_mgr.close()` 保证正常退出与致命错路径都收尾。`_oneshot` 抛 `sys.exit(1)` 的异常分支由 `asyncio.run` 内 try/finally + 关闭覆盖；`main` 顶层 sys.exit(1) 不进 `_amain` 的 try，但那是配置阶段、尚未 start MCP，无连接可漏。
- **现有 `main.py` 多处 `asyncio.run`（main.py:123、133）合并为 `asyncio.run(_amain)`**，TUI / oneshot / MCP 共享同一 loop。

### 数据流（一轮内工具调用）

```
LLM 输出 tool_call(name="mcp__github__create_issue", args={...})
   │
Agent: known_calls → permission.check(tc, read_only=registry.is_read_only(name))
   │   categorize: read_only? READONLY : COMMAND          [permission 现有，MCP 自然命中]
   │   extract_target: 未知工具 → TargetInfo("", False, False)  [黑名单/沙箱自动跳过]
   │   rule engine: friendly_name(name)=name 原样 → 命中 mcp__github__*/mcp__github__create_issue  [泛化后]
   │   模式兜底: 非只读 → DEFAULT 下 ASK；只读 → ALLOW(并发)；bypass 放行  [现有矩阵]
   │
allowed → ToolScheduler.schedule → registry.execute(name, args)
   │   └ McpTool.execute(args) ──► MCPConnection.call_tool
   │        └─ wait_for(session.call_tool, call_timeout=30)
   │             └─ success: 拼 TextContent.text → output / is_error → status
   │             └─ timeout/异常: ToolResult(status="error", error="...")
   └ ToolResult 写回 conversation、产出 EVENT（Agent 既有逻辑，零新增）
```

### 配置加载与合并

```
~/.mewcode/config.yaml  ─┐
                         ├─ _load_file → 各 mcp_servers 段（文件非法→跳过+告警）
<root>/.mewcode.yaml   ─┘
      │
      ▼ 按 server 名合并 {**user, **project}   （项目级完整覆盖）
      │
      ▼ 逐 server: 展开 env/headers 的 ${VAR}（未定义→空串+告警，不阻断）
      │
      ▼ _validate_server: type 非法/必填缺失 → 跳过+告警
      │
      ▼ dict[str, ServerConfig]  （只含可用的 server）
```

## 文件组织

```
mewcode/
├── mcp/
│   ├── __init__.py      — 门面导出
│   ├── config.py        — 两层合并、校验、${VAR} 展开、ServerConfig、load_mcp_servers
│   ├── conn.py          — MCPConnection：SDK 会话封装、HTTP 3 元组、HTTP/stdio 传输、call_tool 翻译
│   ├── wrapper.py       — McpTool + CallerSession Protocol + make_tool + _VALID_NAME
│   └── manager.py       — MCPManager：并发启动、收集排序、统一关闭、失败隔离、超时变量
├── permission/
│   ├── rules.py         — 修改：_RULE_PARSE_RE 放宽接受任意合法工具名；RuleSet.match 工具名 * 通配
│   └── checker.py       — 修改：persist_local_allow 对 mcp__ 工具落盘裸工具名精确规则
└── main.py              — 修改：_amain 单 loop，接入 load_mcp_servers + MCPManager 起停 + finally close

tests/
├── test_mcp_config.py   — 两层合并 / 字段校验 / 变量展开 / 非法 server 隔离
├── test_mcp_conn.py     — 用 CallerSession stub 测 call_tool：超时/协议错/isError/TextContent 拼接/非 text 块丢弃
├── test_mcp_wrapper.py  — make_tool 命名拼接 / 禁用字符 / 只读性 / 描述兜底 / execute 转发
└── test_mcp_manager.py  — 并发启动 / 单 server 失败隔离 / 超时 / 稳定排序 / close 兜底不死锁
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 协议栈 | 官方 `mcp` SDK，不自研 | JSON-RPC 帧/能力协商/版本兼容坑多；集中精力做适配。spec F6 明确 |
| 传输上下文管理 | **长寿命 holder task** 经 `AsyncExitStack` 持有传输/session；`close` 取消 holder 触发上下文在 **holder 自身 task** 内退出 | SDK 传输是资源型上下文，必须横跨连接生命周期持有；且 `stdio_client` 用 anyio cancel scope 绑定进入它的 task，跨 task `aclose()` 会抛 RuntimeError（实测曾现「Attempted to exit cancel scope in a different task」）；holder 让上下文始终同一 task 进出 |
| 单一事件循环 | `main._amain` 把 start_all → app → close 收进一个 `asyncio.run` | MCP session 的底层 transport 绑定所在 loop；多 `asyncio.run` 会让 session 在新 loop 失效。session 必须与运行循环同寿 |
| Manager / Registry 解耦 | Manager 只暴露 `tools()`，注册放在装配处（main） | Manager 可独立测试不碰 Registry；职责单一 |
| CallerSession Protocol | `McpTool` 持 `CallerSession` 而非具体 `ClientSession` | 单测注入 stub 测 execute 各分支，无需起真 server |
| stdio env 注入 | `{**os.environ, **server.env}`，server.env 覆盖同名宿主 | 多数 stdio server 依赖 HOME/PATH/TMPDIR 才能跑；凭据靠 `${VAR}` 不落盘注入 |
| http 不订阅 SSE | 直接 `streamable_http_client`（SDK 2.0；自定义 headers 经 `httpx.AsyncClient` 注入），皆返回 2 元组 `(read_stream, write_stream)` | 本章只要请求-响应式调用，spec F5 明确 |
| 调用错误回灌 | `call_tool` 把超时/协议错/传输断统一转 `ToolResult(status="error")`，不抛 | 复用"工具失败不中断 Agent Loop"契约 |
| ToolResult 模型 | 沿用 repo 既有 `ToolResult(status, output, error, truncated)`，远端 `isError` 映射 status、文本进 output/error | 不改 ToolResult 数据结构（波及全 agent/tui）；spec F7 的 `content/is_error` 是行为描述措辞，实现按此映射 |
| 工具名禁用字符 | `full_name` 须匹配 `[A-Za-z0-9_-]+`，否则跳过 + 告警 | 远端工具名含特殊字符会让 provider 拒收。spec F8 |
| 失败隔离粒度 | 单 server 任一阶段失败只跳过自身，stderr 告警不阻断启动 | spec F9/N1；总时延受单连接 30s 约束（并发 gather） |
| 权限泛化范围 | 放宽正则 + 工具名 `*` 通配 + allow_always 落盘裸名规则 | 兑现 AC11；泛化后未来加新 MCP 工具零改 permission |
| 超时变量可调 | `connect_timeout`/`call_timeout`/`close_timeout` 为模块级变量非字面常量 | 单测临时改小并 restore，避免长超时拖慢测试 |
| 关闭兜底 | 单层 `wait_for(gather(*close), 5)` | 简洁；个别 server 卡死不拖死退出。spec F11/N7 |
| 握手报版本 | `client_info=Implementation(name="mewcode", version=__version__)` | 便于 server 端识别来源 |
| 不做的协议能力 | resources / prompts / sampling / roots / 工具变更通知 / 重连 | spec「不做的事」明确；ch07 范围可控 |