"""MewCode CLI 入口：argparse、配置加载、TUI/单次调用分发"""

import argparse
import asyncio
import os
import sys

from mewcode import __version__
from mewcode.config.loader import load as load_config, load_ccswitch
from mewcode.provider.base import new_provider
from mewcode.conversation.manager import ConversationManager
from mewcode.tui.renderer import RichRenderer
from mewcode.tui.app import REPL
from mewcode.prompt.resources import render_banner


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mewcode",
        description="MewCode — 终端 AI 编程助手",
    )
    parser.add_argument(
        "-c", "--command",
        type=str,
        help="单次调用模式：直接输出回复后退出",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"mewcode {__version__}",
    )
    args = parser.parse_args()

    config_path = os.path.join(os.getcwd(), ".mewcode.yaml")

    # 优先级：CC Switch（自动检测）> .mewcode.yaml（手动配置）
    config = load_ccswitch()
    if config is None:
        try:
            config = load_config(config_path)
        except Exception as e:
            print(f"配置加载失败: {e}", file=sys.stderr)
            sys.exit(1)

    # 找到激活的 provider
    provider_config = next(
        (p for p in config.providers if p.name == config.provider),
        None,
    )
    if provider_config is None:
        print(
            f"provider '{config.provider}' 不在配置的 providers 列表中",
            file=sys.stderr,
        )
        sys.exit(1)

    provider = new_provider(provider_config)
    conversation = ConversationManager(config.system_prompt, config.max_turns)
    renderer = RichRenderer()

    if args.command:
        # 单次调用模式
        asyncio.run(_oneshot(args.command, provider, conversation, renderer))
    else:
        # TUI 多轮对话模式
        print(render_banner(__version__, os.getcwd()))
        repl = REPL(provider, conversation, renderer)
        asyncio.run(repl.run())


async def _oneshot(
    command: str,
    provider,
    conversation: ConversationManager,
    renderer: RichRenderer,
) -> None:
    """单次调用模式：发送问题，流式输出回复，退出"""
    conversation.add_user(command)
    try:
        full_response = await renderer.render_stream(
            provider.stream(conversation.get_context())
        )
        conversation.add_assistant(full_response)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)