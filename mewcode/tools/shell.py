"""Shell 命令执行工具"""

import asyncio
import os
import shlex

from ..provider.base import ToolResult

_WHITELIST = {
    "ls",
    "cat",
    "grep",
    "find",
    "python",
    "pytest",
    "git",
    "pwd",
    "echo",
    "head",
    "tail",
    "wc",
    "mkdir",
    "touch",
    "sleep",
}

_CMD_OUTPUT_LIMIT = 10 * 1024  # 命令输出上限 10KB
_CMD_TIMEOUT = 60  # 命令执行超时 60 秒


def _get_command_token(command: str) -> str:
    """解析命令的第一个 token"""
    try:
        tokens = shlex.split(command)
        return tokens[0] if tokens else ""
    except ValueError:
        # shlex 解析失败，用简单空格分割
        return command.split()[0] if command.strip() else ""


class ExecuteCommandTool:
    """在指定目录下执行 shell 命令（白名单控制）"""

    @property
    def read_only(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "execute_command"

    @property
    def description(self) -> str:
        return (
            "在指定工作目录下执行 shell 命令，返回 stdout/stderr/exit_code，仅允许白名单内的命令。"
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
        cwd = arguments.get("cwd", os.getcwd())

        token = _get_command_token(command)
        if token not in _WHITELIST:
            return ToolResult(
                status="error",
                error=f"命令 '{token}' 不在白名单",
            )

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
            except Exception:
                pass
            return ToolResult(
                status="error",
                error="命令执行超时（60 秒）",
            )
        except Exception as e:
            return ToolResult(
                status="error",
                error=f"命令执行失败: {e}",
            )
