"""MewCode CLI 入口：argparse、配置加载、TUI/单次调用分发"""

import argparse
import asyncio
import os
import sys

from mewcode import __version__
from mewcode.agent import Agent
from mewcode.agent.events import EventType
from mewcode.config.loader import load as load_config
from mewcode.config.loader import load_ccswitch
from mewcode.conversation.manager import ConversationManager
from mewcode.permission.checker import PermissionChecker
from mewcode.permission.modes import PermissionMode
from mewcode.plans import PlanManager
from mewcode.prompt.builder import PromptBuilder
from mewcode.prompt.env import collect_env, format_env
from mewcode.prompt.resources import render_banner
from mewcode.prompt.sections import fixed_sections, optional_sections
from mewcode.provider.base import new_provider
from mewcode.tools import Registry
from mewcode.tui.app import REPL
from mewcode.tui.renderer import RichRenderer


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mewcode",
        description="MewCode — 终端 AI 编程助手",
    )
    parser.add_argument(
        "-c",
        "--command",
        type=str,
        help="单次调用模式：直接输出回复后退出",
    )
    parser.add_argument(
        "-p",
        "--plan",
        action="store_true",
        help="计划模式：只用只读工具探查代码，产出计划后退出",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["default", "acceptEdits", "plan", "bypassPermissions"],
        help="权限模式：default / acceptEdits / plan / bypassPermissions",
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
    conversation = ConversationManager(config.max_turns)
    registry = Registry.default()

    # ch05：模块化系统提示 + 环境信息（会话内各构建/采集一次，稳定前缀跨轮不变）
    cwd = os.getcwd()
    builder = PromptBuilder(fixed_sections() + optional_sections(config.system_prompt))
    stable_prompt = builder.build()
    env_segment = format_env(
        collect_env(cwd, __version__, provider.name, provider.model)
    )

    # 权限系统初始化
    project_root = os.path.abspath(os.getcwd())
    permission = PermissionChecker.create(project_root)

    # 命令行 --mode 覆盖配置
    if args.mode:
        cli_mode = PermissionMode.parse(args.mode)
        if cli_mode is not None:
            permission.set_mode(cli_mode)

    is_interactive = not bool(args.command)
    agent = Agent(
        provider,
        conversation,
        registry,
        stable_prompt,
        env_segment,
        permission=permission,
        is_interactive=is_interactive,
    )
    renderer = RichRenderer()
    plan_manager = PlanManager(os.path.join(os.getcwd(), "plans"))

    # 启动清理：删除超过保留期的过期计划
    cleaned = plan_manager.cleanup_old(config.cleanup_period_days)
    if cleaned > 0:
        print(f"已清理 {cleaned} 个过期计划（>{config.cleanup_period_days}天）")

    if args.command:
        # 单次调用模式
        mode = "plan" if args.plan else "normal"
        asyncio.run(_oneshot(args.command, agent, mode))
    else:
        # TUI 多轮对话模式
        print(render_banner(__version__, os.getcwd()))
        repl = REPL(
            agent,
            renderer,
            plan_manager=plan_manager,
            default_mode=config.default_mode,
        )
        asyncio.run(repl.run())


async def _oneshot(command: str, agent: Agent, mode: str = "normal") -> None:
    """单次调用模式：发送问题，消费 Agent Event 流，退出"""
    try:
        async for event in agent.run(command, mode=mode):
            if event.type == EventType.TEXT:
                print(event.payload, end="", flush=True)
            elif event.type == EventType.TOOL_CALL:
                tc = event.payload
                params = ", ".join(f"{k}={v!r}" for k, v in tc.arguments.items())
                print(f"\n● {tc.tool_name}({params})")
            elif event.type == EventType.TOOL_RESULT:
                tr = event.payload
                if tr.status == "error":
                    print(f"  ✗ {tr.error}")
                else:
                    summary = (
                        tr.output[:200] + "..." if len(tr.output) > 200 else tr.output
                    )
                    print(f"  → {summary}")
            elif event.type == EventType.TOKEN_USAGE:
                pass  # 单次模式不展示 token 用量
            elif event.type == EventType.TURN_START:
                pass  # 单次模式不展示轮次
            elif event.type == EventType.TURN_END:
                pass
            elif event.type == EventType.DONE:
                stop_reason = event.payload
                if stop_reason.value != "natural":
                    print(f"\n[终止: {stop_reason.value}]")
                print()
                return
            elif event.type == EventType.ERROR:
                print(f"\n错误: {event.payload}", file=sys.stderr)
                sys.exit(1)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
