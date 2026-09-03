"""Shell 命令执行工具"""

import asyncio
import os
from collections.abc import Callable

from ..provider.base import ToolResult
from .cwd import cwd_from_ctx

_CMD_OUTPUT_LIMIT = 10 * 1024  # 命令输出上限 10KB
_CMD_TIMEOUT = 60  # 命令执行超时 60 秒


class ExecuteCommandTool:
    """在指定目录下执行 shell 命令"""

    def __init__(
        self,
        guard: Callable[[str], str | None] | None = None,
    ) -> None:
        """ch15 收尾 F2.7：可选命令守卫（团队清理引导；None=不守卫，行为与现状一致）。"""
        self._guard = guard

    @property
    def read_only(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "execute_command"

    @property
    def description(self) -> str:
        return (
            "在指定工作目录下执行 shell 命令，返回 stdout/stderr/exit_code。"
            "优先用专用工具（read_file / search_code / list_files）而非 shell 命令。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
                "cwd": {
                    "type": "string",
                    "description": "工作目录（可选，默认当前目录）",
                },
            },
            "required": ["command"],
        }

    async def execute(self, arguments: dict) -> ToolResult:
        command = arguments.get("command", "")
        # ch15 收尾 F2.3：守卫命中 → 返回结构化引导错误，不执行、不弹权限确认
        if self._guard is not None:
            hint = self._guard(command)
            if hint:
                return ToolResult(status="error", error=hint)
        # ch14 F7.2：显式 cwd 参数 > ctx cwd > 进程 cwd（子进程 cwd 跟随）
        cwd = arguments.get("cwd") or cwd_from_ctx() or os.getcwd()

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout_data, stderr_data = await asyncio.wait_for(
                proc.communicate(),
                timeout=_CMD_TIMEOUT,
            )
            stdout = stdout_data.decode("utf-8", errors="replace")
            stderr = stderr_data.decode("utf-8", errors="replace")
            exit_code = proc.returncode

            output = f"[stdout]\n{stdout}\n[stderr]\n{stderr}\n[exit_code]\n{exit_code}"
            truncated = False

            if len(output) > _CMD_OUTPUT_LIMIT:
                output = output[:_CMD_OUTPUT_LIMIT] + "\n...（输出已截断）"
                truncated = True

            status = "ok" if exit_code == 0 else "error"
            return ToolResult(
                status=status,
                output=output,
                error="" if status == "ok" else f"命令退出码非零: {exit_code}",
                truncated=truncated,
            )

        except asyncio.TimeoutError:
            # 超时后尝试终止进程
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # noqa: S110, BLE001 - cleanup must not mask timeout
                pass
            return ToolResult(
                status="error",
                error="命令执行超时（60 秒）",
            )
        except Exception as e:  # noqa: BLE001 - convert process failures to ToolResult
            return ToolResult(
                status="error",
                error=f"命令执行失败: {e}",
            )
