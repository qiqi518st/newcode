"""MCP 配置加载单测（ch07 T1）。

防的 bug（背景注明，防后人误删）：
- YAML 格式非法曾可能直接致启动失败 -> 锁定「跳过该层 + stderr 告警 + 不抛」（spec F1/N1）。
- 未定义 ${VAR} 曾可能按硬错误处理 -> 锁定「空串 + 告警 + 不阻断该 server」（spec F3/N2）。
- command/args 中的 ${VAR} 曾可能被误展开 -> 锁定「仅 env/headers 的值展开」（spec F3）。
- type 缺失/非法曾被静默放行为 stdio -> 锁定「跳过该 server + 告警，其它不受影响」（spec F2/N2）。
"""

from pathlib import Path

import pytest

from newcode.mcp.config import ServerConfig, load_mcp_servers


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 Path.home() 指到临时目录，隔离用户级 ~/.newcode/config.yaml。"""
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake)
    return fake


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    r.mkdir()
    return r


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _user_yaml(home: Path) -> Path:
    return home / ".newcode" / "config.yaml"


def _project_yaml(root: Path) -> Path:
    return root / ".newcode.yaml"


# ── 两层合并 ──────────────────────────────────────────────


def test_both_files_missing_returns_empty(home: Path, root: Path):
    """两文件均缺失 -> 空 dict、不抛（spec F1）。"""
    assert load_mcp_servers(str(root)) == {}


def test_project_only(home: Path, root: Path):
    _write(
        _project_yaml(root),
        "mcp_servers:\n"
        "  demo:\n"
        "    type: stdio\n"
        "    command: npx\n"
        "    args: ['-y', 'demo-server']\n",
    )
    servers = load_mcp_servers(str(root))
    assert list(servers) == ["demo"]
    assert servers["demo"].command == "npx"
    assert servers["demo"].args == ["-y", "demo-server"]
    assert servers["demo"].type == "stdio"


def test_user_only(home: Path, root: Path):
    _write(
        _user_yaml(home),
        "mcp_servers:\n"
        "  remote:\n"
        "    type: http\n"
        "    url: https://mcp.example.com/mcp\n",
    )
    servers = load_mcp_servers(str(root))
    assert list(servers) == ["remote"]
    assert servers["remote"].url == "https://mcp.example.com/mcp"


def test_same_name_project_overrides_user_wholesale(home: Path, root: Path):
    """同名 server 项目级完整覆盖（整对象，非字段级合并）（spec F1）。"""
    _write(
        _user_yaml(home),
        "mcp_servers:\n"
        "  both:\n"
        "    type: http\n"
        "    url: https://user.example.com/mcp\n"
        "    headers:\n"
        "      X-Only-User: 'user'\n",
    )
    _write(
        _project_yaml(root),
        "mcp_servers:\n  both:\n    type: stdio\n    command: python\n",
    )
    servers = load_mcp_servers(str(root))
    assert list(servers) == ["both"]
    # 完整覆盖：项目级是 stdio，用户级的 url/headers 不残留（防半合并出畸形 server）
    assert servers["both"].type == "stdio"
    assert servers["both"].command == "python"
    assert servers["both"].url == ""
    assert servers["both"].headers == {}


# ── 非法文件降级 ──────────────────────────────────────────


def test_invalid_yaml_skips_layer_with_warning(home: Path, root: Path, capsys):
    """项目级 YAML 非法 -> 跳过该层 + 告警 + 不抛；用户级照常加载（spec F1/N1）。"""
    _write(
        _user_yaml(home),
        "mcp_servers:\n  ok:\n    type: stdio\n    command: python\n",
    )
    _write(_project_yaml(root), "mcp_servers: [unclosed\n  broken: - {")

    servers = load_mcp_servers(str(root))  # 不抛
    assert list(servers) == ["ok"]
    err = capsys.readouterr().err
    assert "load" in err and "failed" in err


# ── ${VAR} 展开 ──────────────────────────────────────────


def test_env_and_headers_expansion(home: Path, root: Path, monkeypatch, capsys):
    """env/headers 的值 ${VAR} 从宿主环境展开（spec F3）。"""
    monkeypatch.setenv("MY_TOKEN", "secret-value")
    monkeypatch.setenv("EXAMPLE_TOKEN", "tok-123")
    _write(
        _project_yaml(root),
        "mcp_servers:\n"
        "  a:\n"
        "    type: stdio\n"
        "    command: npx\n"
        "    env:\n"
        "      TOKEN: '${MY_TOKEN}'\n"
        "  b:\n"
        "    type: http\n"
        "    url: https://mcp.example.com/mcp\n"
        "    headers:\n"
        "      Authorization: 'Bearer ${EXAMPLE_TOKEN}'\n",
    )
    servers = load_mcp_servers(str(root))
    assert servers["a"].env["TOKEN"] == "secret-value"
    assert servers["b"].headers["Authorization"] == "Bearer tok-123"
    assert "undefined env var" not in capsys.readouterr().err


def test_undefined_var_expands_empty_with_warning(
    home: Path, root: Path, monkeypatch, capsys
):
    """未定义 ${VAR} -> 空串 + 告警 + 不阻断该 server（spec F3/N2）。"""
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    _write(
        _project_yaml(root),
        "mcp_servers:\n"
        "  a:\n"
        "    type: stdio\n"
        "    command: npx\n"
        "    env:\n"
        "      TOKEN: '${NOPE_TOKEN}'\n"
        "      OTHER: 'x${NOPE_TOKEN}y'\n",
    )
    servers = load_mcp_servers(str(root))
    assert servers["a"].env["TOKEN"] == ""
    assert servers["a"].env["OTHER"] == "xy"  # 部分占位也展开为空串
    err = capsys.readouterr().err
    # 同 server 同变量限一次告警
    assert err.count("undefined env var ${NOPE_TOKEN}") == 1


def test_command_and_args_not_expanded(home: Path, root: Path, monkeypatch, capsys):
    """command/args 中的 ${VAR} 不展开（spec F3：命令不被环境间接影响）。"""
    monkeypatch.setenv("CMD", "echo")
    _write(
        _project_yaml(root),
        "mcp_servers:\n"
        "  a:\n"
        "    type: stdio\n"
        "    command: '${CMD}'\n"
        "    args: ['${CMD}', '-x']\n",
    )
    servers = load_mcp_servers(str(root))
    assert servers["a"].command == "${CMD}"
    assert servers["a"].args == ["${CMD}", "-x"]
    assert "undefined" not in capsys.readouterr().err


# ── 字段校验与跳过 ────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("    command: npx\n", "type 缺失"),
        ("    type: sse\n    command: npx\n", "type 非法"),
        ("    type: stdio\n", "stdio 缺 command"),
        ("    type: http\n", "http 缺 url"),
        ("    type: stdio\n    command: ''\n", "stdio command 为空串"),
    ],
)
def test_invalid_server_skipped_others_unaffected(
    home: Path, root: Path, capsys, body: str, reason: str
):
    """非法 server 被跳过 + 告警；同文件其它 server 不受影响（spec F2/N2）。"""
    _write(
        _project_yaml(root),
        "mcp_servers:\n"
        "  bad:\n" + body + "  good:\n"
        "    type: http\n"
        "    url: https://ok.example.com/mcp\n",
    )
    servers = load_mcp_servers(str(root))
    assert list(servers) == ["good"]
    assert "skip server bad" in capsys.readouterr().err


def test_returned_type_is_serverconfig(home: Path, root: Path):
    _write(
        _project_yaml(root),
        "mcp_servers:\n  a:\n    type: stdio\n    command: python\n",
    )
    servers = load_mcp_servers(str(root))
    assert isinstance(servers["a"], ServerConfig)
    assert servers["a"].name == "a"


# ── 示例文件反向解析（ch07 T9）────────────────────────────


def test_example_yaml_parses(home: Path, root: Path, monkeypatch, capsys):
    """docs/ch07/mcp-servers.example.yaml 是交付物示例，纳入测试覆盖。

    防的 bug：示例文件写错（type 拼错/字段名错）或解析逻辑改动后
    示例悄悄失配，用户照抄示例却配置不生效。
    """
    import yaml as _yaml

    monkeypatch.setenv("GITHUB_TOKEN", "gh-test")
    monkeypatch.setenv("EXAMPLE_TOKEN", "ex-test")
    # 项目级配置指向示例文件（copy 到 root/.newcode.yaml 位置由加载器读取）
    example = (
        Path(__file__).resolve().parent.parent
        / "docs"
        / "ch07"
        / "mcp-servers.example.yaml"
    )
    assert example.exists(), "示例文件必须存在"
    # 直接以项目级身份加载：把示例写到 root/.newcode.yaml
    _write(_project_yaml(root), example.read_text(encoding="utf-8"))

    servers = load_mcp_servers(str(root))
    assert set(servers) == {"github", "local-sqlite", "example-http"}
    assert servers["github"].type == "stdio"
    assert servers["github"].command == "npx"
    assert servers["github"].env["GITHUB_TOKEN"] == "gh-test"
    assert servers["local-sqlite"].type == "stdio"
    assert servers["example-http"].type == "http"
    assert servers["example-http"].headers["Authorization"] == "Bearer ex-test"
    # 示例凭据一律 ${VAR}，无明文 token
    raw = example.read_text(encoding="utf-8")
    for token_hint in ("ghp_", "github_pat_", "sk-"):
        assert token_hint not in raw
    # 环境变量已定义，无 undefined 告警
    assert "undefined env var" not in capsys.readouterr().err
    # 示例本身是合法 YAML（防手写缩进错）
    assert isinstance(_yaml.safe_load(raw), dict)
