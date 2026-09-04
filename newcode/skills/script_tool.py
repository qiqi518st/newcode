"""目录型 Skill 专属工具的子进程执行壳（F9.5/N4）。

tool.json 声明的工具被模型调用时，NewCode 以子进程执行 references/ 里的实现脚本
（不 import 进主进程，避免第三方代码的安全债）。参数 JSON 走 stdin，stdout 捕获为
ToolResult.output；超时 30s（asyncio.wait_for，不阻塞 event loop，与 ch05 bash 工具一致）。
"""

from __future__ import annotations

import asyncio
import json
import sys

from ..provider.base import ToolResult
from .types import ToolSchema

_SCRIPT_TIMEOUT = 30  # 子进程执行超时（秒）


class ScriptTool:
    """Tool 协议实现：schema 声明 + 子进程执行 references/ 脚本。"""

    def __init__(self, schema: ToolSchema, skill_dir) -> None:
        self._schema = schema
        self._skill_dir = skill_dir
        self._script_path = (skill_dir / schema.entrypoint).resolve()

    @property
    def name(self) -> str:
        return self._schema.name

    @property
    def description(self) -> str:
        return self._schema.description

    @property
    def parameters(self) -> dict:
        return self._schema.parameters

    @property
    def read_only(self) -> bool:
        # 目录型专属工具按声明执行，可产生副作用，视为非只读（F9.2 注册真实工具）
        return False

    @property
    def is_system(self) -> bool:
        return False  # 普通工具，参与 allowedTools 过滤（F3.5 只豁免系统工具）

    async def execute(self, arguments: dict) -> ToolResult:
        """以子进程执行入口脚本：参数 JSON 走 stdin，捕获 stdout。

        超时 / 启动失败 / 非零退出均返回 error 结果（不抛异常进主循环）。
        """
        try:
            if not self._script_path.is_file():
                return ToolResult(
                    status="error",
                    error=f"script tool entrypoint not found: {self._script_path}",
                )
            payload = json.dumps(arguments, ensure_ascii=False).encode("utf-8")
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(self._script_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._skill_dir),
            )
            stdout_data, stderr_data = await asyncio.wait_for(
                proc.communicate(input=payload), timeout=_SCRIPT_TIMEOUT
            )
            stdout = stdout_data.decode("utf-8", errors="replace").strip()
            stderr = stderr_data.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0:
                return ToolResult(
                    status="error",
                    error=(
                        f"script tool exited with code {proc.returncode}: "
                        f"{stderr or stdout}"
                    ),
                )
            return ToolResult(status="ok", output=stdout or "(no output)")
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # noqa: S110, BLE001 - cleanup must not mask timeout
                pass
            return ToolResult(
                status="error",
                error=f"script tool timed out after {_SCRIPT_TIMEOUT}s",
            )
        except Exception as e:  # noqa: BLE001 - process failures convert to ToolResult
            return ToolResult(status="error", error=f"script tool failed: {e}")
