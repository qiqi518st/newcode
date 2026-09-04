"""ch12 动作执行器（spec F5）：shell / prompt / http / agent 单测（mock，无真实终端/API）。

防的 bug：
- Windows 下 create_subprocess_shell 走 cmd.exe，`>&2; exit 2` 被当字面量回显、
  `;` 不是命令分隔——F5.2 要求 sh -c，用 create_subprocess_exec("sh", "-c", ...)（AC5）。
- blocking 且 rc==2 的 reason 曾取错通道（stderr 空时丢 stdout），拒绝原因不完整。
- 成功命令的输出曾被静默丢弃，AC9 的 `echo first-turn >&2` 观察点失效——
  非拦截语义下命令输出须转发到进程 stderr（spec「输出只记日志」）。
- http 拦截信号只应来自 2xx 的 {"decision":"block"}；非 2xx / 缺 decision / 非 block
  若误判为拦截会误伤所有工具（AC15）。
- agent 动作占位日志格式固定（N9），改动格式会破坏后续章节的文本搜索替换。
"""

from __future__ import annotations

import shutil

import httpx
import pytest

from newcode.hooks import executor as executor_mod
from newcode.hooks.executor import Executor
from newcode.hooks.types import (
    Action,
    ActionType,
    AgentAction,
    Event,
    Hook,
    HttpAction,
    PromptAction,
    ShellAction,
)

# 本文件全部为 async 测试（anyio 后端，见 conftest.py）
pytestmark = pytest.mark.anyio


def _hook(
    name="h",
    command=None,
    text=None,
    url=None,
    method="POST",
    agent=None,
    timeout_s=30.0,
):
    if command is not None:
        action = Action(type=ActionType.COMMAND, shell=ShellAction(command=command))
    elif text is not None:
        action = Action(type=ActionType.PROMPT, prompt=PromptAction(text=text))
    elif url is not None:
        action = Action(type=ActionType.HTTP, http=HttpAction(url=url, method=method))
    else:
        action = Action(
            type=ActionType.AGENT,
            agent=AgentAction(agent_name=agent or "foo", prompt="test"),
        )
    return Hook(name=name, event=Event.PRE_TOOL_USE, action=action, timeout_s=timeout_s)


def _mock_http_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestShell:
    async def test_exit_2_blocks_with_reason(self):
        """blocking + exit 2 → blocked，reason 取 stderr 去尾换行（AC5）。"""
        ex = Executor()
        r = await ex.run(_hook(command="echo blocked >&2; exit 2"), {}, blocking=True)
        assert r.blocked and r.reason == "blocked"
        await ex.aclose()

    async def test_exit_0_passes(self):
        """blocking + exit 0 → 放行，文件可写（AC6）。"""
        ex = Executor()
        r = await ex.run(_hook(command="exit 0"), {}, blocking=True)
        assert not r.blocked and r.err is None
        await ex.aclose()

    async def test_other_exit_is_err_not_block(self):
        """其它非零 → hook 失败但不拦截（F5.4/F9.1）。"""
        ex = Executor()
        r = await ex.run(_hook(command="echo oops >&2; exit 3"), {}, blocking=True)
        assert not r.blocked and r.err is not None
        assert "exit 3" in str(r.err)
        await ex.aclose()

    async def test_non_blocking_exit_2_not_blocked(self):
        """非拦截事件下 rc==2 不拦截（拦截信号只在 blocking 事件有效）。"""
        ex = Executor()
        r = await ex.run(_hook(command="echo x >&2; exit 2"), {}, blocking=False)
        assert not r.blocked and r.err is not None
        await ex.aclose()

    async def test_success_output_forwarded_to_stderr(self, capsys):
        """成功命令输出转发进程 stderr（AC9 观察点）。"""
        ex = Executor()
        r = await ex.run(_hook(command="echo first-turn >&2"), {}, blocking=False)
        assert r.err is None and not r.blocked
        assert "first-turn" in capsys.readouterr().err
        await ex.aclose()

    async def test_stdout_used_when_stderr_empty(self):
        """stderr 空时 reason 取 stdout（F5.4「stderr 或 stdout」）。"""
        ex = Executor()
        r = await ex.run(
            _hook(command="echo stdout-blocked; exit 2"), {}, blocking=True
        )
        assert r.blocked and r.reason == "stdout-blocked"
        await ex.aclose()

    async def test_timeout_kills_and_errs(self):
        """超时终止子进程并返回超时错误（F5.3）。"""
        ex = Executor()
        r = await ex.run(_hook(command="sleep 5", timeout_s=0.5), {}, blocking=True)
        assert not r.blocked and r.err is not None
        assert "timed out" in str(r.err)
        await ex.aclose()

    async def test_template_in_command(self):
        """command 动作支持 {field} 内嵌替换（F4.9）。"""
        ex = Executor()
        r = await ex.run(
            _hook(command="test -n '{tool_name}'"),
            {"tool_name": "write_file"},
            blocking=False,
        )
        assert r.err is None
        await ex.aclose()

    async def test_no_shell_found_is_err_not_block(self, monkeypatch):
        """找不到 POSIX shell → hook 失败但不拦截（Windows 缺 Git 场景，F9.1）。

        防的 bug：_find_posix_shell 返回 None 时若抛异常或误拦截，会让所有
        command 动作的 hook 在无 shell 环境下失控（曾出现 create_subprocess_exec
        硬编码 "sh" 在 Windows 上抛 WinError 2，护栏静默失效）。
        """
        monkeypatch.setattr(executor_mod, "_find_posix_shell", lambda: None)
        ex = Executor()
        r = await ex.run(_hook(command="exit 0"), {}, blocking=True)
        assert not r.blocked and r.err is not None
        await ex.aclose()

    async def test_uses_detected_shell(self, monkeypatch):
        """探测出的 shell 路径被实际使用（非硬编码 sh，AC5）。"""
        real = shutil.which("sh")
        if real is None:
            pytest.skip("当前环境无 sh，跳过探测路径用例")
        monkeypatch.setattr(executor_mod, "_find_posix_shell", lambda: real)
        ex = Executor()
        r = await ex.run(_hook(command="exit 0"), {}, blocking=False)
        assert r.err is None
        await ex.aclose()


class TestPrompt:
    async def test_returns_prompt_text(self):
        ex = Executor()
        r = await ex.run(
            _hook(text="hi {tool_name}"), {"tool_name": "x"}, blocking=False
        )
        assert r.prompt == "hi x"

    async def test_never_blocks(self):
        """prompt 动作永不表达拦截（F5.8），即使 blocking 事件。"""
        ex = Executor()
        r = await ex.run(_hook(text="remind"), {}, blocking=True)
        assert not r.blocked and r.err is None


class TestHttp:
    async def test_2xx_block_decision(self):
        """2xx + {"decision":"block"} → 拦截，reason 取回（AC15）。"""

        def handler(request):
            return httpx.Response(
                200, json={"decision": "block", "reason": "network policy"}
            )

        ex = Executor()
        ex._http_client = _mock_http_client(handler)
        r = await ex.run(_hook(url="http://x/check"), {}, blocking=True)
        assert r.blocked and r.reason == "network policy"
        await ex.aclose()

    async def test_non_2xx_passes(self):
        """非 2xx → 放行（F5.11）。"""

        def handler(request):
            return httpx.Response(500, json={})

        ex = Executor()
        ex._http_client = _mock_http_client(handler)
        r = await ex.run(_hook(url="http://x/check"), {}, blocking=True)
        assert not r.blocked and r.err is None
        await ex.aclose()

    async def test_missing_decision_passes(self):
        """body 缺 decision → 放行。"""

        def handler(request):
            return httpx.Response(200, json={"ok": True})

        ex = Executor()
        ex._http_client = _mock_http_client(handler)
        r = await ex.run(_hook(url="http://x/check"), {}, blocking=True)
        assert not r.blocked and r.err is None
        await ex.aclose()

    async def test_non_block_decision_passes(self):
        def handler(request):
            return httpx.Response(200, json={"decision": "allow"})

        ex = Executor()
        ex._http_client = _mock_http_client(handler)
        r = await ex.run(_hook(url="http://x/check"), {}, blocking=True)
        assert not r.blocked
        await ex.aclose()

    async def test_network_error_is_err_not_block(self):
        """网络错误 → hook 失败但不拦截（F5.11/F9.1）。"""

        def handler(request):
            raise httpx.ConnectError("boom")

        ex = Executor()
        ex._http_client = _mock_http_client(handler)
        r = await ex.run(_hook(url="http://x/check"), {}, blocking=True)
        assert not r.blocked and r.err is not None
        await ex.aclose()

    async def test_default_body_is_payload_json(self):
        """缺省 body → payload JSON（sort_keys，N5）。"""
        captured = {}

        def handler(request):
            captured["content"] = request.content
            return httpx.Response(200, json={"decision": "allow"})

        ex = Executor()
        ex._http_client = _mock_http_client(handler)
        await ex.run(_hook(url="http://x/check"), {"event": "turn_end"}, blocking=False)
        assert captured["content"] == b'{"event": "turn_end"}'
        await ex.aclose()


class TestAgent:
    async def test_placeholder_log_format(self, capsys):
        """占位日志固定格式 `[hook <name>] agent not yet implemented, skipped`（N9/AC16）。"""
        ex = Executor()
        r = await ex.run(_hook(name="a1", agent="foo"), {}, blocking=False)
        assert "[hook a1] agent not yet implemented, skipped" in capsys.readouterr().err
        assert not r.blocked and r.err is None
        await ex.aclose()
