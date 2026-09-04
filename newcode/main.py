"""NewCode CLI 入口：argparse、配置加载、TUI/单次调用分发。

ch07：改为单一事件循环 _amain -- MCP 连接、TUI/oneshot、退出收尾共享同一 loop
（MCP session 的底层 transport 绑定所在事件循环，跨 loop 会失效）；
退出路径 finally 统一关闭 MCP 连接（5s 兜底）。
"""

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

logger = logging.getLogger(__name__)

from newcode import __version__
from newcode.agent import Agent
from newcode.agent.events import EventType
from newcode.config.features import load_features_config
from newcode.config.loader import load as load_config
from newcode.config.loader import load_ccswitch
from newcode.context import ContextManager, FileTracker
from newcode.coordinator import allowed_tools as coordinator_allowed_tools
from newcode.coordinator import is_enabled as coordinator_enabled
from newcode.coordinator import system_prompt_suffix as coordinator_prompt_suffix
from newcode.hooks import load as load_hooks
from newcode.hooks.types import Event as HookEvent
from newcode.instructions import InstructionLoader
from newcode.mcp import MCPManager, load_mcp_servers
from newcode.memory import MemoryManager
from newcode.permission.checker import PermissionChecker
from newcode.permission.modes import PermissionMode
from newcode.plans import PlanManager
from newcode.prompt.builder import PromptBuilder
from newcode.prompt.env import collect_env, format_env
from newcode.prompt.resources import render_banner
from newcode.prompt.sections import fixed_sections, optional_sections
from newcode.provider.base import new_provider
from newcode.session.archive import SessionArchive, clean_expired
from newcode.session.runtime import SessionRuntime
from newcode.skills import ActiveSkills, Catalog, Executor
from newcode.slash import CommandContext, CommandRegistry
from newcode.slash.commands import register_all
from newcode.slash.commands.skill_register import register_skills_as_commands
from newcode.subagent.catalog import load_catalog
from newcode.subagent.config import load_agent_config
from newcode.subagent.launcher import SubAgentLauncher
from newcode.subagent.manager import TaskManager
from newcode.team.cleanup import TEAM_CLEANUP_DISCIPLINE, guard_team_git_cleanup
from newcode.team.manager import Manager as TeamManager
from newcode.team.registry import AgentNameRegistry
from newcode.team.spawn import TeamHookImpl
from newcode.team.tools import (
    new_send_message_tool,
    new_task_create_tool,
    new_task_get_tool,
    new_task_list_tool,
    new_task_update_tool,
    new_team_create_tool,
    new_team_delete_tool,
)
from newcode.tools import Registry
from newcode.tools.agent_tool import AgentTool
from newcode.tools.load_skill import LoadSkillTool
from newcode.tools.memory_read import ReadMemoryTool
from newcode.tools.memory_write import WriteMemoryTool
from newcode.tools.shell import ExecuteCommandTool
from newcode.tools.task_tools import (
    SendMessageTool,
    TaskGetTool,
    TaskListTool,
    TaskStopTool,
)
from newcode.tui.app import REPL
from newcode.tui.renderer import RichRenderer
from newcode.worktree import Manager as WorktreeManager
from newcode.worktree.config import load_worktree_config
from newcode.worktree.types import WorktreeError


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="newcode",
        description="NewCode - 终端 AI 编程助手",
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
        "--resume",
        action="store_true",
        help="ch14：恢复上次 worktree 会话的工作目录（F10.3）",
    )
    # ch15：Pane 后端子进程的 team-member 模式（F6/F19a）
    parser.add_argument(
        "--team-member",
        action="store_true",
        help="ch15：以团队队员自治协程运行（不启动 TUI，被 tmux/iterm2 后端 spawn）",
    )
    parser.add_argument("--team", type=str, help="ch15：团队名（--team-member 用）")
    parser.add_argument("--member", type=str, help="ch15：队员名（--team-member 用）")
    parser.add_argument(
        "--agent-id", type=str, help="ch15：队员 agent_id（--team-member 用）"
    )
    parser.add_argument(
        "--session-dir", type=str, help="ch15：队员 session 目录（--team-member 用）"
    )
    parser.add_argument(
        "--worktree", type=str, help="ch15：队员 worktree 路径（--team-member 用）"
    )
    parser.add_argument(
        "--agent-type", type=str, help="ch15：角色名（--team-member 用）"
    )
    parser.add_argument("--model", type=str, help="ch15：模型覆盖（--team-member 用）")
    parser.add_argument(
        "--plan-mode",
        action="store_true",
        help="ch15：以 plan 模式起步（--team-member 用）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"newcode {__version__}",
    )
    args = parser.parse_args()

    # ch15 F6：--team-member 短路——不构造 TUI/oneshot，跑自治协程后退出
    if args.team_member:
        from newcode.team.cli_team_member import run_team_member

        asyncio.run(run_team_member(args))
        return

    config_path = os.path.join(os.getcwd(), ".newcode.yaml")

    # 优先级：CC Switch（自动检测）> .newcode.yaml（手动配置）
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
    cwd = os.getcwd()
    # ch10：SessionRuntime 统一持有会话生命周期（create_new = 新会话上下文 + writer + Conversation）
    session_runtime = SessionRuntime(
        cwd, max_turns=config.max_turns, model=provider.model
    )
    session_archive = SessionArchive(cwd)
    conversation = session_runtime.create_new()
    cleanup_task = asyncio.create_task(
        asyncio.to_thread(
            clean_expired,
            cwd,
            config.cleanup_period_days,
            active_session_id=session_runtime.session_id,
        )
    )
    registry = Registry.default()

    # ch05：模块化系统提示 + 环境信息（会话内各构建/采集一次，稳定前缀跨轮不变）
    instructions = InstructionLoader(cwd, Path.home()).load()
    memory = MemoryManager(
        Path(cwd) / ".newcode" / "memory",
        Path.home() / ".newcode" / "memory",
        provider=provider,
        model=provider.model,
    )
    # 记忆读写工具：跨项目/用户两级读写记忆（spec F13 闭环），注册进主注册表。
    # read_memory 只读；write_memory 只写记忆命名空间（MEMORY 类权限，四档免确认）
    registry.register(ReadMemoryTool(memory))
    registry.register(WriteMemoryTool(memory))
    builder = PromptBuilder(fixed_sections() + optional_sections(config.system_prompt))
    builder.set_custom_instructions(instructions.text)
    builder.set_long_term_memory(memory.load_indexes())
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

    # ── ch12 Hook 装配（T13）：三层配置加载 + context providers 注入 ──
    hook_engine = load_hooks(project_root)
    session_runtime.hook_engine = hook_engine
    hook_engine.set_context_providers(
        lambda: session_runtime.session_id or "",
        lambda: permission.mode.value,
    )

    # ── ch07：MCP 初始化（进 TUI 前同步完成，失败只告警不阻塞） ──
    mcp_mgr = MCPManager(load_mcp_servers(cwd), client_version=__version__)
    summary = await mcp_mgr.start_all()
    # 可观测性：有 server 被尝试（成功或失败）时打一行启动摘要（spec N5）
    if not summary.is_empty:
        print(MCPManager.format_summary(summary), file=sys.stderr)
    for tool in mcp_mgr.tools():
        registry.register(tool)

    is_interactive = not bool(args.command)

    # ── ch11 Skill 装配（T21）：先 LoadSkillTool 进 registry，再构造 Agent ──
    active_skills = ActiveSkills()
    catalog = Catalog.load(Path(cwd))
    bad_skills = catalog.validate_tools(registry)
    for bad in bad_skills:
        # B 决策：启动时白名单引用不存在工具 → warning + 从 catalog 移除（不阻断其它）
        logger.warning(
            "skill %s references nonexistent allowedTools, removed from catalog",
            bad,
        )
        catalog.remove(bad)
    registry.register(LoadSkillTool(catalog, active_skills, registry))

    # ch08 上下文管理装配（FileTracker / ActiveSkills / ContextManager）
    file_tracker = FileTracker()
    active_provider_cfg = next(
        (p for p in config.providers if p.name == config.provider), None
    )
    # ch13：模型工厂（launcher 模型分层与 Skill Executor 共用；无 provider 配置时为 None）
    make_provider = (
        (
            lambda model: (
                new_provider(replace(active_provider_cfg, model=model))
                if active_provider_cfg
                else None
            )
        )
        if active_provider_cfg
        else None
    )

    # ── ch13 SubAgent 装配（T20）：agents 配置 → catalog → 后台管理器 → launcher → 工具 ──
    agents_cfg = load_agent_config(cwd)
    subagent_catalog = load_catalog(cwd, agents_cfg)
    task_manager = TaskManager(
        max_tasks_per_agent=agents_cfg.max_tasks_per_agent,
        max_queue_per_agent=agents_cfg.max_queue_per_agent,
        max_idle_agents=agents_cfg.max_idle_agents,
        idle_cleanup_minutes=agents_cfg.idle_cleanup_minutes,
    )
    launcher = SubAgentLauncher(
        provider,
        make_provider=make_provider,
        parent_permission=permission,
        hooks=hook_engine,
        catalog=subagent_catalog,
        manager=task_manager,
        cfg=agents_cfg,
        get_main_agent=lambda: agent_ref[0],  # 惰性取用（agent_ref 在下方构造后填充）
    )
    # ── ch14 Worktree 装配（T16）：配置 → 管理器（失败降级 None）→ sweep 后台任务 ──
    worktrees_cfg = load_worktree_config(cwd)
    try:
        worktree_mgr = WorktreeManager(cwd, worktrees_cfg)
    except WorktreeError as exc:
        print(f"Worktree 管理器降级（功能未启用）: {exc}", file=sys.stderr)
        worktree_mgr = None
    if worktree_mgr is not None and worktree_mgr.cfg.background_cleanup:
        sweep_task = asyncio.create_task(worktree_mgr.run())  # F6.5 周期清理
    else:
        sweep_task = None

    # ── ch15 Team 装配（T27）：features → name_reg → TeamManager → 工具 → team_hook ──
    features_cfg = load_features_config(cwd)
    name_reg = AgentNameRegistry()
    task_manager.set_name_registry(name_reg)
    try:
        team_mgr = TeamManager(
            home_dir=str(Path.home()),
            project_root=cwd,
            wt_mgr=worktree_mgr,
            task_mgr=task_manager,
            registry=name_reg,
        )
    except Exception as exc:  # noqa: BLE001 —— 团队目录不可写等 → 结构化降级
        print(f"Team Manager 降级（团队功能未启用）: {exc}", file=sys.stderr)
        team_mgr = None
    team_hook = None
    team_sweep_task = None
    if team_mgr is not None:
        # TD-13：成员完成回调（cli 装配，非 Manager 自注册）
        task_manager.on_task_done(lambda tid: team_mgr.handle_task_done(tid))
        team_hook = TeamHookImpl(team_mgr, subagent_catalog, launcher, features_cfg)
        # ch15 收尾 F2.7/F3.4：execute_command 守卫 + 孤儿清扫（启动一次 + 周期）
        registry.register(
            ExecuteCommandTool(guard=lambda cmd: guard_team_git_cleanup(team_mgr, cmd))
        )
        try:
            await team_mgr.sweep_orphan_worktrees()  # 启动补扫（best-effort）
        except Exception as exc:  # noqa: BLE001 —— 启动清扫失败不阻断
            print(f"team: 启动孤儿清扫失败: {exc}", file=sys.stderr)
        team_sweep_task = asyncio.create_task(
            team_mgr.run(worktrees_cfg.cleanup_interval_minutes * 60)
        )

    # 协作工具注册辅助（TD-2）：建队时动态注册 / 删队后注销
    def _register_collab() -> None:
        if team_mgr is None:
            return
        for factory in (
            new_task_create_tool,
            new_task_get_tool,
            new_task_list_tool,
            new_task_update_tool,
            new_send_message_tool,
        ):
            name = {
                new_task_create_tool: "TaskCreate",
                new_task_get_tool: "TaskGet",
                new_task_list_tool: "TaskList",
                new_task_update_tool: "TaskUpdate",
                new_send_message_tool: "SendMessage",
            }[factory]
            if registry.get(name) is None:
                registry.register(factory(team_mgr))

    def _unregister_collab() -> None:
        if team_mgr is None or team_mgr.list_():
            return
        for name in ("TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "SendMessage"):
            registry.unregister(name)

    coordinator_on = (
        coordinator_enabled(features_cfg) if team_mgr is not None else False
    )
    if coordinator_on:
        _register_collab()  # coordinator 启动即注册（TD-2）

    # 工具注册（主 Agent 可见；agent 工具经 GLOBAL_DENY 对子 Agent 剔除，F6.1）
    registry.register(
        AgentTool(
            subagent_catalog,
            launcher,
            lambda: agent_ref[0],
            worktree_mgr=worktree_mgr,
            worktrees_cfg=worktrees_cfg,
            team_hook=team_hook,
        )
    )
    registry.register(TaskListTool(task_manager))
    registry.register(TaskGetTool(task_manager))
    registry.register(TaskStopTool(task_manager))
    registry.register(SendMessageTool(task_manager))
    if team_mgr is not None:
        registry.register(
            new_team_create_tool(team_mgr, on_team_created=_register_collab)
        )
        registry.register(
            new_team_delete_tool(team_mgr, on_team_deleted=_unregister_collab)
        )
    # hook agent 动作接通（F9.1）
    hook_engine.set_agent_launcher(launcher.launch_hook_agent)
    # 常驻空闲清理（F7.7）
    task_manager_task = asyncio.create_task(task_manager.run())

    context_mgr = ContextManager(
        provider,
        conversation,
        provider.model,
        active_provider_cfg.protocol if active_provider_cfg else "anthropic",
        file_tracker,
        active_skills=active_skills,
        emit_event=lambda kind, payload: agent_ref[0]._context_events.append(
            (kind, payload)
        ),
        workspace=cwd,
    )
    # ch15 收尾 F1.1：团队清理纪律（团队功能启用时通用生效）
    if team_mgr is not None:
        stable_prompt = stable_prompt + "\n\n" + TEAM_CLEANUP_DISCIPLINE
    # ch15 F14：Coordinator 激活——stable_prompt 拼四阶段提示词（构造前），构造后收窄工具集
    if coordinator_on:
        stable_prompt = stable_prompt + "\n\n" + coordinator_prompt_suffix()

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
        memory_manager=memory,
        active_skills=active_skills,
        hooks=hook_engine,
        runtime=session_runtime,
    )
    if coordinator_on:
        agent.set_allowed_tools(coordinator_allowed_tools())  # F14.3 收窄（TD-11）
    agent_ref.append(agent)
    # 阶段一摘要注入：with_catalog 后 env 每轮含 Available Skills 段（F4.1）
    agent.with_catalog(catalog)
    renderer = RichRenderer()
    plan_manager = PlanManager(os.path.join(cwd, "plans"))

    # 启动清理：删除超过保留期的过期计划
    cleaned = plan_manager.cleanup_old(config.cleanup_period_days)
    if cleaned > 0:
        print(f"已清理 {cleaned} 个过期计划（>{config.cleanup_period_days}天）")

    # ch12：startup / session_start 事件（装配完成后、首条消息前，F8.1）
    await hook_engine.dispatch(HookEvent.STARTUP, {"cwd": cwd})
    await hook_engine.dispatch(HookEvent.SESSION_START, {"cwd": cwd})

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
                session_runtime=session_runtime,
                session_archive=session_archive,
                memory_manager=memory,
                task_manager=task_manager,
                worktree_mgr=worktree_mgr,
                resume_worktree=args.resume,
                team_mgr=team_mgr,
                coordinator_mode=coordinator_on,
            )
            # ch10：命令注册 + CommandContext 组装。register_all 冲突 → 打印冲突名并退出（F1.3/N4）
            cmd_registry = CommandRegistry()
            try:
                register_all(cmd_registry)
            except RuntimeError as exc:
                print(f"启动失败: 命令名/别名冲突 {exc}", file=sys.stderr)
                sys.exit(1)
            # ch11：Skill 执行器 + 动态 /名字 注册（内置命令先注册，冲突时 Skill 跳过，F2.5）
            executor = Executor(
                catalog,
                active_skills,
                registry,
                provider,
                engine=None,
                version=__version__,
                make_provider=make_provider,
            )
            register_skills_as_commands(cmd_registry, catalog, executor)
            cmd_ctx = CommandContext(
                registry=cmd_registry,
                ui=repl.ui,
                agent=agent,
                conversation=conversation,
                plan_manager=plan_manager,
                session_runtime=session_runtime,
                session_archive=session_archive,
                memory_manager=memory,
                permission=permission,
                version=__version__,
                cwd=cwd,
                catalog=catalog,
                active_skills=active_skills,
                executor=executor,
                hooks=hook_engine,
                task_manager=task_manager,
            )
            repl.command_registry = cmd_registry
            repl.command_ctx = cmd_ctx
            await repl.run()
    finally:
        if not cleanup_task.done():
            cleanup_task.cancel()
        # ch13：后台任务全局清空（不跨进程/会话持久化，F7.9）
        task_manager.clear_all()
        if not task_manager_task.done():
            task_manager_task.cancel()
        # ch14：取消 worktree 周期清理任务（F6.5）
        if sweep_task is not None and not sweep_task.done():
            sweep_task.cancel()
        # ch15 收尾 F3.4：取消团队孤儿清扫周期任务
        if team_sweep_task is not None and not team_sweep_task.done():
            team_sweep_task.cancel()
        # ch12：session_end + shutdown + engine.close（F8.1/F9.5，后台任务尽力而为）
        await hook_engine.dispatch(HookEvent.SESSION_END, {})
        await hook_engine.dispatch(HookEvent.SHUTDOWN, {})
        await hook_engine.close()
        session_runtime.close()
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
            elif event.type == EventType.CONTEXT_COMPACTING:
                pass  # 单次模式不展示压缩提示
            elif event.type == EventType.COMPACT_FAILED:
                outcome = event.payload
                print(
                    f"\n压缩失败: {getattr(outcome, 'failure_reason', '未知')}",
                    file=sys.stderr,
                )
    except SystemExit:
        # SystemExit 经 _amain 的 finally（关 MCP）后正常向外传播退出
        raise
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        raise SystemExit(1) from e
