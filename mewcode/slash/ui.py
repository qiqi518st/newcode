"""UIController 抽象接口（F6）+ NopUI / RecordingUI 测试桩。

方法集最小化（F6.2）：只暴露命令实际需要的抽象操作，不把 TUI 内部属性外泄。
命令实现一律经此接口操作界面，不 import prompt_toolkit / rich（F6.3）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..permission.modes import PermissionMode


@runtime_checkable
class UIController(Protocol):
    """命令执行所需的界面控制抽象：输出 / 注入 / 查询 / 生命周期 / 交互选择。

    - show_message / 查询方法为同步；
    - 触发 LLM 回合 / 会话操作 / 交互选择为 async（handler 为 async，直接 await）。
    """

    # ── 输出 ──────────────────────────────────────────────
    def show_message(self, text: str, style: str = "") -> None: ...

    # ── 用户消息注入（KindPrompt 用），触发 LLM 回合（F3.4）──
    async def send_user_message(self, text: str) -> None: ...

    # ── 按指定模式触发一轮 Agent（KindUI 用，如 /do、/plan <task>）──
    async def run_agent(
        self, user_input: str, mode: str = "normal", plan_content: str = "", execute_slug: str = ""
    ) -> None: ...

    # ── 权限模式 ───────────────────────────────────────────
    def get_permission_mode(self) -> str: ...

    def set_permission_mode(self, mode: str) -> None: ...

    # ── App 模式（plan / normal）────────────────────────────
    def get_app_mode(self) -> str: ...

    def set_app_mode(self, mode: str) -> None: ...

    # ── 查询 ───────────────────────────────────────────────
    def query_token_usage(self) -> tuple[int, int]: ...  # (in, out)

    def query_tool_count(self) -> int: ...

    def query_memory_files(self) -> list[str]: ...

    def get_model_name(self) -> str: ...

    def get_cwd(self) -> str: ...

    # ── 生命周期 ───────────────────────────────────────────
    def request_exit(self) -> None: ...  # 退出（先取消主 cancel scope，N12）

    async def request_session_list(self) -> None: ...  # 打开历史会话列表并恢复（/resume）

    async def resume_session(self, session_id: str) -> None: ...  # 按 id 恢复会话（/session_resume）

    async def new_session(self) -> None: ...  # 新建会话（/session_new）

    async def request_compact(self) -> None: ...  # 触发上下文压缩（/compact）

    async def request_clear_session(self) -> None: ...  # 清空并新建会话（/clear，原子重置）

    # ── 交互选择（/do、/resume、/delete-plan 用）────────────
    async def choose(
        self,
        question: str,
        options: list[tuple[str, str]],
        default_index: int = 0,
    ) -> str | None: ...

    async def choose_multi(
        self, question: str, options: list[tuple[str, str]]
    ) -> list[str] | None: ...


class NopUI:
    """测试桩：所有写入方法 no-op、所有查询返回零值（供 handler 单测复用，N11）。"""

    def show_message(self, text: str, style: str = "") -> None:
        return None

    async def send_user_message(self, text: str) -> None:
        return None

    async def run_agent(
        self, user_input: str, mode: str = "normal", plan_content: str = "", execute_slug: str = ""
    ) -> None:
        return None

    def get_permission_mode(self) -> str:
        return PermissionMode.DEFAULT.value

    def set_permission_mode(self, mode: str) -> None:
        return None

    def get_app_mode(self) -> str:
        return "normal"

    def set_app_mode(self, mode: str) -> None:
        return None

    def query_token_usage(self) -> tuple[int, int]:
        return (0, 0)

    def query_tool_count(self) -> int:
        return 0

    def query_memory_files(self) -> list[str]:
        return []

    def get_model_name(self) -> str:
        return ""

    def get_cwd(self) -> str:
        return ""

    def request_exit(self) -> None:
        return None

    async def request_session_list(self) -> None:
        return None

    async def resume_session(self, session_id: str) -> None:
        return None

    async def new_session(self) -> None:
        return None

    async def request_compact(self) -> None:
        return None

    async def request_clear_session(self) -> None:
        return None

    async def choose(
        self,
        question: str,
        options: list[tuple[str, str]],
        default_index: int = 0,
    ) -> str | None:
        return None

    async def choose_multi(
        self, question: str, options: list[tuple[str, str]]
    ) -> list[str] | None:
        return None

    @property
    def permission_mode(self) -> PermissionMode:
        """NopUI 持有的当前权限模式（set_permission_mode 修改它），测试可读。"""
        return PermissionMode.DEFAULT

    @permission_mode.setter
    def permission_mode(self, value: PermissionMode) -> None:
        return None


class RecordingUI(NopUI):
    """可观测桩：记录写入/查询/注入/切换/请求 调用与消息，供 handler 行为断言。

    借鉴 ch09 RecordingUI 设计：暴露 calls（调用序列）与 messages（show_message 文本），
    测试断言"调用了什么、调了几次、文本含哪些 key"（覆盖 CLAUDE.md 接线测试必须自动跑）。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.messages: list[str] = []
        self._permission_mode: PermissionMode = PermissionMode.DEFAULT
        self._app_mode: str = "normal"
        self._tokens: tuple[int, int] = (0, 0)
        self._memory_files: list[str] = []

    def _record(self, name: str, *args: object) -> None:
        self.calls.append((name,) + tuple(str(a) for a in args))

    def show_message(self, text: str, style: str = "") -> None:
        self._record("show_message", text, style)
        self.messages.append(text)

    async def send_user_message(self, text: str) -> None:
        self._record("send_user_message", text)

    async def run_agent(
        self, user_input: str, mode: str = "normal", plan_content: str = "", execute_slug: str = ""
    ) -> None:
        self._record("run_agent", user_input, mode, plan_content, execute_slug)

    def get_permission_mode(self) -> str:
        return self._permission_mode.value

    def set_permission_mode(self, mode: str) -> None:
        parsed = PermissionMode.parse(str(mode))
        if parsed is not None:
            self._permission_mode = parsed
        self._record("set_permission_mode", mode)

    def get_app_mode(self) -> str:
        return self._app_mode

    def set_app_mode(self, mode: str) -> None:
        self._app_mode = mode
        self._record("set_app_mode", mode)

    def query_token_usage(self) -> tuple[int, int]:
        return self._tokens

    def query_tool_count(self) -> int:
        return 0

    def query_memory_files(self) -> list[str]:
        return self._memory_files

    def get_model_name(self) -> str:
        return ""

    def get_cwd(self) -> str:
        return ""

    def request_exit(self) -> None:
        self._record("request_exit")

    async def request_session_list(self) -> None:
        self._record("request_session_list")

    async def resume_session(self, session_id: str) -> None:
        self._record("resume_session", session_id)

    async def new_session(self) -> None:
        self._record("new_session")

    async def request_compact(self) -> None:
        self._record("request_compact")

    async def request_clear_session(self) -> None:
        self._record("request_clear_session")

    async def choose(
        self,
        question: str,
        options: list[tuple[str, str]],
        default_index: int = 0,
    ) -> str | None:
        self._record("choose", question, *[o[0] for o in options])
        return None

    async def choose_multi(
        self, question: str, options: list[tuple[str, str]]
    ) -> list[str] | None:
        self._record("choose_multi", question, *[o[0] for o in options])
        return None

    @property
    def permission_mode(self) -> PermissionMode:
        return self._permission_mode

    @permission_mode.setter
    def permission_mode(self, value: PermissionMode) -> None:
        self._permission_mode = value