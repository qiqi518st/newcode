"""ch05 F7 典型场景评估脚本：跑 Agent，打印工具调用序列 + 每轮 TokenUsage + 缓存命中标注。

用法：
    python scripts/eval_scenarios.py --scenario 1          # 跑场景 1（真实 provider，读 .mewcode.yaml）
    python scripts/eval_scenarios.py --scenario 5 --mock   # 用 mock provider（无 API key，验证输出管线）

场景：
    1 工具优先级遵守：用户让用 grep/ls 找东西，观察是否优先用 search_code/list_files
    2 先读后改：修改文件，观察是否先 read 再 edit
    3 Plan Mode 行为：/plan 只读、产结构化计划
    4 多工具配合：需多步工具调用的任务
    5 缓存命中与成本：多轮请求观察 input token 与缓存命中字段
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mewcode import __version__
from mewcode.agent import Agent
from mewcode.agent.events import EventType
from mewcode.conversation.manager import ConversationManager
from mewcode.prompt.builder import PromptBuilder
from mewcode.prompt.env import collect_env, format_env
from mewcode.prompt.sections import fixed_sections, optional_sections
from mewcode.provider.base import StreamEvent, TokenUsage, ToolCall
from mewcode.tools import Registry

SCENARIOS = [
    ("工具优先级遵守", "用 grep 在项目里找 'def main' 的位置", "normal"),
    ("先读后改", "把 main.py 里的 max_turns 改成 30", "normal"),
    ("Plan Mode 行为", "分析项目结构并给出重构建议", "plan"),
    (
        "多工具配合",
        "读取 main.py 和 mewcode/agent/agent.py，对比导入部分，创建 analysis.md",
        "normal",
    ),
    ("缓存命中与成本", "读取 main.py 的内容，然后总结", "normal"),
]


class MockProvider:
    """无 API key 的 mock：3 轮（只读工具 + 文本），usage 带缓存字段"""

    def __init__(self) -> None:
        self._calls = 0
        self._name = "mock"
        self._model = "mock-model"

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    async def stream(self, payload):
        self._calls += 1
        if self._calls == 1:
            yield StreamEvent(tool_call=ToolCall("read_file", {"path": "main.py"}))
            yield StreamEvent(
                done=True,
                usage=TokenUsage(
                    100, 20, cache_creation_input_tokens=90, cache_read_input_tokens=0
                ),
            )
        elif self._calls == 2:
            yield StreamEvent(tool_call=ToolCall("search_code", {"pattern": "def "}))
            yield StreamEvent(
                done=True, usage=TokenUsage(110, 25, cache_read_input_tokens=90)
            )
        else:
            yield StreamEvent(text="（mock 完成）")
            yield StreamEvent(
                done=True, usage=TokenUsage(60, 12, cache_read_input_tokens=90)
            )


def _cache_annotation(u: TokenUsage) -> str:
    """标注缓存命中状态：读取>0 → 命中；否则按创建/未命中"""
    if u.cache_read_input_tokens > 0:
        return f"cache 命中(读{u.cache_read_input_tokens})"
    if u.cache_creation_input_tokens > 0:
        return f"cache 创建(写{u.cache_creation_input_tokens})"
    return "cache 未命中"


async def _run_scenario(index: int, mock: bool) -> None:
    name, user_input, mode = SCENARIOS[index - 1]
    print(
        f"\n{'=' * 50}\n场景 {index}: {name}\n输入: {user_input!r}  模式: {mode}\n{'=' * 50}"
    )

    # 与 main.py 一致的组装管线
    cwd = os.getcwd()
    max_turns = 20
    if mock:
        builder = PromptBuilder(fixed_sections() + optional_sections(""))
        stable_prompt = builder.build()
        env_segment = format_env(collect_env(cwd, __version__, "mock", "mock-model"))
        provider: object = MockProvider()
    else:
        from mewcode.config.loader import load as load_config
        from mewcode.config.loader import load_ccswitch
        from mewcode.provider.base import new_provider

        config = load_ccswitch()
        if config is None:
            config = load_config(os.path.join(cwd, ".mewcode.yaml"))
        provider_config = next(p for p in config.providers if p.name == config.provider)
        provider = new_provider(provider_config)
        builder = PromptBuilder(
            fixed_sections() + optional_sections(config.system_prompt)
        )
        stable_prompt = builder.build()
        env_segment = format_env(
            collect_env(cwd, __version__, provider.name, provider.model)
        )
        max_turns = config.max_turns

    conversation = ConversationManager(max_turns)
    agent = Agent(
        provider, conversation, Registry.default(), stable_prompt, env_segment
    )

    print(f"\n[环境] 工作目录: {cwd}")
    print(
        f"[环境] {env_segment.splitlines()[3] if len(env_segment.splitlines()) > 3 else ''}"
    )

    turn = 0
    async for event in agent.run(user_input, mode=mode):
        if event.type == EventType.TURN_START:
            turn = event.payload
            print(f"\nTurn {turn + 1}:")
        elif event.type == EventType.TOOL_CALL:
            tc = event.payload
            params = ", ".join(f"{k}={v!r}" for k, v in tc.arguments.items())
            print(f"  ● {tc.tool_name}({params})")
        elif event.type == EventType.TOOL_RESULT:
            tr = event.payload
            if tr.status == "error":
                print(f"    ✗ {tr.error}")
            else:
                print(f"    → {tr.output[:120]}")
        elif event.type == EventType.TOKEN_USAGE:
            u = event.payload
            print(
                f"    [usage] in={u.input_tokens} out={u.output_tokens} "
                f"写={u.cache_creation_input_tokens} 读={u.cache_read_input_tokens} "
                f"→ {_cache_annotation(u)}"
            )
        elif event.type == EventType.DONE:
            print(f"\n[完成] {event.payload.value}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="eval_scenarios",
        description="MewCode ch05 典型场景评估脚本（人工定性对比）",
    )
    parser.add_argument(
        "--scenario", type=int, choices=range(1, 6), help="场景编号 1-5"
    )
    parser.add_argument(
        "--mock", action="store_true", help="用 mock provider（无 API key）"
    )
    args = parser.parse_args()

    selected = [args.scenario] if args.scenario else [1, 2, 3, 4, 5]
    for idx in selected:
        asyncio.run(_run_scenario(idx, args.mock))


if __name__ == "__main__":
    main()
