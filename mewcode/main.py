"""MewCode CLI 入口：argparse、配置加载、TUI/单次调用分发。

ch07：改为单一事件循环 _amain -- MCP 连接、TUI/oneshot、退出收尾共享同一 loop
（MCP session 的底层 transport 绑定所在事件循环，跨 loop 会失效）；
退出路径 finally 统一关闭 MCP 连接（5s 兜底）。
"""

import argparse
import asyncio
import os
import sys

from mewcode import __version__
from mewcode.agent import Agent
from mewcode.agent.events import EventType
from mewcode.config.loader import load as load_config
from mewcode.config.loader import load_ccswitch
from mewcode.context import ContextManager, FileTracker, SkillRegistry
from mewcode.conversation.manager import ConversationManager
from mewcode.mcp import MCPManager, load_mcp_servers
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
        description="MewCode - 终端 AI 编程助手",
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
        except Exception as e:  # noqa: BLE001 -- 配置层任意异常给友好错误并退出（原逻辑）
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

    # 单一事件循环：MCP 连接 / TUI / oneshot / 退出收尾同寿
    asyncio.run(_amain(args, config, provider))


async def _amain(args: argparse.Namespace, config, provider) -> None:
    """主流程：MCP 初始化 -> Agent 装配 -> TUI/oneshot -> finally 关 MCP。"""
    conversation = ConversationManager(config.max_turns)
    registry = Registry.default()

    # ch05：模块化系统提示 + 环境信息（会话内各构建/采集一次，稳定前缀跨轮不变）
    cwd = os.getcwd()
    builder = PromptBuilder(fixed_sections() + optional_sections(config.system_prompt))
    stable_prompt = builder.build()
    env_segment = format_env(
        collect_env(cwd, __version__, provider.name, provider.model)
    )

    # 权限系统初始化（建在 MCP 之前，权限层不依赖 MCP）
    project_root = os.path.abspath(cwd)
    permission = PermissionChecker.create(project_root)

    # 命令行 --mode 覆盖配置
    if args.mode:
        cli_mode = PermissionMode.parse(args.mode)
        if cli_mode is not None:
            permission.set_mode(cli_mode)

    # ── ch07：MCP 初始化（进 TUI 前同步完成，失败只告警不阻塞） ──
    mcp_mgr = MCPManager(load_mcp_servers(cwd), client_version=__version__)
    summary = await mcp_mgr.start_all()
    # 可观测性：有 server 被尝试（成功或失败）时打一行启动摘要（spec N5）
    if not summary.is_empty:
        print(MCPManager.format_summary(summary), file=sys.stderr)
    for tool in mcp_mgr.tools():
        registry.register(tool)

    is_interactive = not bool(args.command)

    # ch08 上下文管理装配（FileTracker / SkillRegistry 骨架 / ContextManager）
    file_tracker = FileTracker()
    skill_registry = SkillRegistry()  # 骨架：当前无 Skill，注入分支空实现（F31）
    active_provider_cfg = next(
        (p for p in config.providers if p.name == config.provider), None
    )
    context_mgr = ContextManager(
        provider,
        conversation,
        provider.model,
        active_provider_cfg.protocol if active_provider_cfg else "anthropic",
        file_tracker,
        skill_registry=skill_registry,
        emit_event=lambda kind, payload: agent_ref[0]._context_events.append((kind, payload)),
        workspace=cwd,
    )
    # 延迟绑定 agent 引用（emit_event 闭包需访问 agent，但 agent 在下方构造）
    agent_ref: list[Agent] = []
    agent = Agent(
        provider,
        conversation,
        registry,
        stable_prompt,
        env_segment,
        permission=permission,
        is_interactive=is_interactive,
        context_mgr=context_mgr,
        file_tracker=file_tracker,
    )
    agent_ref.append(agent)
    renderer = RichRenderer()
    plan_manager = PlanManager(os.path.join(cwd, "plans"))

    # 启动清理：删除超过保留期的过期计划
    cleaned = plan_manager.cleanup_old(config.cleanup_period_days)
    if cleaned > 0:
        print(f"已清理 {cleaned} 个过期计划（>{config.cleanup_period_days}天）")

    try:
        if args.command:
            # 单次调用模式
            mode = "plan" if args.plan else "normal"
            await _oneshot(args.command, agent, mode)
        else:
            # TUI 多轮对话模式
            print(render_banner(__version__, cwd))
            repl = REPL(
                agent,
                renderer,
                plan_manager=plan_manager,
                default_mode=config.default_mode,
            )
            await repl.run()
    finally:
        # 统一关闭 MCP 连接（stdio 子进程终止 / HTTP 会话释放；5s 兜底）
        await mcp_mgr.close()


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
                    print(f"  -> {summary}")
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
    except SystemExit:
        # SystemExit 经 _amain 的 finally（关 MCP）后正常向外传播退出
        raise
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        raise SystemExit(1) from e
