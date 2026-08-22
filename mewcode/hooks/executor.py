"""Hook 动作执行器（ch12 F5）：command / prompt / http / agent 四类 + {field} 模板渲染。

错误隔离（F9.1）：动作执行失败以 ExecutionResult.err 表达，由引擎记 stderr 日志，
绝不向调用方抛异常（拦截信号只经 blocked/reason 表达，F7.2）。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import TYPE_CHECKING

import httpx

from .conditions import get_by_path
from .types import ActionType, ExecutionResult

if TYPE_CHECKING:
    from .types import Hook, Payload

# {field} 模板识别：str.format_map 会把 {a.b} 当属性访问解析，无法直接用于点分路径，
# 故用正则逐组替换。字段名仅允许标识符 + 点（F4.7），其它形态视为非法模板。
_TEMPLATE_FIELD_RE = re.compile(r"\{([^{}]*)\}")
_VALID_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def render_template(text: str, payload: Payload) -> str:
    """{field} 点分路径替换（F4.7/F4.8，映射原始 $VAR 语义）。

    容错：字段不存在 → ""；裸 `{}` 或非法模板 → 返回原文；绝不抛给调用方。
    """
    def _replace(m: re.Match[str]) -> str:
        field = m.group(1)
        if not _VALID_FIELD_RE.match(field):
            raise ValueError(f"invalid template field: {field!r}")
        return get_by_path(payload, field)

    try:
        return _TEMPLATE_FIELD_RE.sub(_replace, text)
    except ValueError:
        return text


class Executor:
    """四类动作执行器：按 action.type 分发（F5.1）。"""

    def __init__(self) -> None:
        # 复用连接池；单条请求的 timeout 由各 hook 的 timeout_s 覆盖
        self._http_client = httpx.AsyncClient(timeout=30.0)

    async def run(
        self, hook: Hook, payload: Payload, *, blocking: bool
    ) -> ExecutionResult:
        action = hook.action
        if action.type == ActionType.COMMAND:
            return await self._run_shell(
                action.shell, payload, blocking, hook.timeout_s
            )
        if action.type == ActionType.PROMPT:
            return self._run_prompt(action.prompt, payload)
        if action.type == ActionType.HTTP:
            return await self._run_http(action.http, payload, blocking, hook.timeout_s)
        if action.type == ActionType.AGENT:
            return self._run_agent(action.agent, hook)
        return ExecutionResult(err=ValueError(f"unknown action type: {action.type}"))

    async def _run_shell(
        self, sa, payload: Payload, blocking: bool, timeout_s: float
    ) -> ExecutionResult:
        """command 动作（F5.2-F5.5）：sh -c 子进程 + payload JSON 走 stdin。
        拦截语义：blocking 且 exit 2 → blocked，stderr/stdout 去尾换行为 reason。
        """
        command = render_template(sa.command, payload)
        try:
            # F5.2：sh -c 执行（用户常写 |>、> 等 POSIX shell 语法）。
            # 不用 create_subprocess_shell——Windows 下它走 cmd.exe 会破坏 POSIX 语法。
            proc = await asyncio.create_subprocess_exec(
                "sh",
                "-c",
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:  # noqa: BLE001 —— 子进程创建失败按 hook 失败处理
            return ExecutionResult(err=e)
        payload_json = json.dumps(payload, sort_keys=True).encode()
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload_json), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ExecutionResult(
                err=TimeoutError(f"command timed out after {timeout_s}s")
            )
        except asyncio.CancelledError:
            # F9.3：取消传播——杀掉子进程后继续向上传播，避免卡死 Agent.run
            proc.kill()
            raise
        rc = proc.returncode or 0
        combined = (stderr or stdout).decode(errors="replace").rstrip("\n")
        if blocking and rc == 2:
            # F5.4：exit 2 → 拦截命中，stderr/stdout 去尾换行为拒绝原因
            return ExecutionResult(blocked=True, reason=combined)
        if rc == 0:
            # 成功命令的输出转发到进程 stderr 供观察（spec「输出只记日志」；
            # AC9 的 `echo first-turn >&2` 观察点依赖此行为）
            if combined:
                print(combined, file=sys.stderr)
            return ExecutionResult()
        # 其它非零 → hook 失败但不拦截（F5.4），输出并入错误信息
        return ExecutionResult(err=RuntimeError(f"exit {rc}: {combined}"))

    def _run_prompt(self, pa, payload: Payload) -> ExecutionResult:
        """prompt 动作（F5.6-F5.8）：文本进 injected_prompts，永不表达拦截。"""
        return ExecutionResult(prompt=render_template(pa.text, payload))

    async def _run_http(
        self, ha, payload: Payload, blocking: bool, timeout_s: float
    ) -> ExecutionResult:
        """http 动作（F5.9-F5.12）。
        拦截语义：blocking 且 2xx 且 body {"decision":"block"} → blocked；
        非 2xx / 缺 decision / decision 非 block → 放行；网络/超时/JSON 解析错 → hook 失败。
        """
        try:
            if ha.body is not None:
                body = render_template(ha.body, payload)
            else:
                body = json.dumps(payload, sort_keys=True)
            resp = await self._http_client.request(
                ha.method,
                ha.url,
                content=body,
                headers=ha.headers,
                timeout=timeout_s,
            )
        except (httpx.HTTPError, TimeoutError, OSError) as e:
            return ExecutionResult(err=e)
        except asyncio.CancelledError:
            raise
        if not (200 <= resp.status_code < 300):
            # 非 2xx → 放行（F5.11）
            return ExecutionResult()
        if not blocking:
            return ExecutionResult()
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001 —— JSON 解析失败按 hook 失败但不拦截
            return ExecutionResult(err=e)
        if isinstance(data, dict) and data.get("decision") == "block":
            return ExecutionResult(blocked=True, reason=str(data.get("reason", "")))
        return ExecutionResult()

    def _run_agent(self, aa, hook: Hook) -> ExecutionResult:
        """agent 动作（F5.13/N9）：本期占位——固定格式 stderr 日志，不 blocked 不 err。"""
        print(
            f"[hook {hook.name}] agent not yet implemented, skipped",
            file=sys.stderr,
        )
        return ExecutionResult()

    async def aclose(self) -> None:
        """关闭 http 连接池（Engine.close 收尾调用）。"""
        await self._http_client.aclose()
