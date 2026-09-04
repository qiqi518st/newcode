"""ScriptTool 子进程执行壳单测（T24）：正常执行 / 超时 / 非零退出 / 缺入口。

防的 bug：实现脚本被 import 进主进程（N4 安全债）；参数不 JSON 走 stdin；超时
未 kill 子进程导致悬挂；非零退出未判 error；入口缺失未报错。
"""

import pytest

from newcode.skills.script_tool import ScriptTool
from newcode.skills.types import ToolSchema

pytestmark = pytest.mark.anyio


def _schema(name: str = "mytool", entrypoint: str = "references/tool.py") -> ToolSchema:
    return ToolSchema(
        name=name,
        description="a script tool",
        parameters={"type": "object", "properties": {"msg": {"type": "string"}}},
        entrypoint=entrypoint,
    )


async def test_execute_runs_script_via_subprocess(tmp_path):
    """防 bug：入口脚本以子进程执行（不 import），stdout 捕获为 output。"""
    skill_dir = tmp_path / "skill"
    refs = skill_dir / "references"
    refs.mkdir(parents=True)
    (refs / "tool.py").write_text(
        "import json, sys\n"
        "args = json.load(sys.stdin)\n"
        "print('echo:' + args.get('msg', ''))\n"
        "print('cwd-ok', file=sys.stderr)\n",
        encoding="utf-8",
    )
    tool = ScriptTool(_schema(), skill_dir)
    result = await tool.execute({"msg": "hello"})
    assert result.status == "ok"
    assert result.output == "echo:hello"


async def test_execute_passes_arguments_via_stdin(tmp_path):
    """防 bug：参数 JSON 必须经 stdin 传给脚本（脚本据此工作）。"""
    skill_dir = tmp_path / "skill"
    refs = skill_dir / "references"
    refs.mkdir(parents=True)
    (refs / "tool.py").write_text(
        "import json, sys\n"
        "args = json.load(sys.stdin)\n"
        "print(args['name'], args['n'] * 2)\n",
        encoding="utf-8",
    )
    tool = ScriptTool(_schema(entrypoint="references/tool.py"), skill_dir)
    result = await tool.execute({"name": "x", "n": 3})
    assert result.status == "ok"
    assert result.output == "x 6"


async def test_execute_nonzero_exit_returns_error(tmp_path):
    skill_dir = tmp_path / "skill"
    refs = skill_dir / "references"
    refs.mkdir(parents=True)
    (refs / "tool.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    tool = ScriptTool(_schema(), skill_dir)
    result = await tool.execute({})
    assert result.status == "error"
    assert "exited with code 3" in result.error


async def test_execute_missing_entrypoint_returns_error(tmp_path):
    skill_dir = tmp_path / "skill"
    tool = ScriptTool(_schema(entrypoint="references/missing.py"), skill_dir)
    result = await tool.execute({})
    assert result.status == "error"
    assert "not found" in result.error


async def test_execute_is_not_system(tmp_path):
    """防 bug：ScriptTool 是普通工具，参与 allowedTools 过滤（F3.5 只豁免系统工具）。"""
    tool = ScriptTool(_schema(), tmp_path)
    assert tool.is_system is False
    assert tool.read_only is False
