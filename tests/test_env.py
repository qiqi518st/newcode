"""环境信息采集与格式化测试（ch05，spec F3 / N12）"""

from mewcode.prompt.env import EnvContext, collect_env, format_env


class TestCollectEnv:
    """collect_env 字段齐全 + git 降级"""

    def test_collects_all_fields(self, monkeypatch):
        monkeypatch.setattr(
            "mewcode.prompt.env._collect_git",
            lambda cwd: ("master", True),
        )
        env = collect_env("/proj", "0.5.0", "ccswitch", "claude-sonnet-4")
        assert env.cwd == "/proj"
        assert env.platform
        assert env.datetime
        assert env.version == "0.5.0"
        assert env.provider == "ccswitch"
        assert env.model == "claude-sonnet-4"
        assert env.git_branch == "master"
        assert env.git_dirty is True

    def test_git_failure_degrades(self, monkeypatch):
        """git 采集失败 → 降级为 None，不抛异常（N12）"""

        def boom(cwd):
            raise OSError("git not found")

        monkeypatch.setattr("mewcode.prompt.env._collect_git", boom)
        env = collect_env("/proj", "0.5.0", "p", "m")
        assert env.git_branch is None
        assert env.git_dirty is None

    def test_git_subprocess_error_degrades(self, monkeypatch):
        """git 非零退出 → 降级（非 git 仓库场景）"""

        class FakeProc:
            returncode = 128
            stdout = "fatal: not a git repository"

        monkeypatch.setattr(
            "mewcode.prompt.env.subprocess.run",
            lambda *a, **k: FakeProc(),
        )
        env = collect_env("/proj", "0.5.0", "p", "m")
        assert env.git_branch is None and env.git_dirty is None


class TestFormatEnv:
    """format_env 呈现与缺失项省略"""

    def test_full_env(self):
        env = EnvContext(
            cwd="/proj",
            platform="Linux",
            datetime="2026-08-11 10:00:00",
            timezone="CST",
            version="0.5.0",
            provider="p",
            model="m",
            git_branch="master",
            git_dirty=True,
        )
        out = format_env(env)
        assert "工作目录: /proj" in out
        assert "平台: Linux" in out
        assert "2026-08-11 10:00:00 CST" in out
        assert "应用版本: 0.5.0" in out
        assert "p/m" in out
        assert "分支 master" in out and "有未提交修改" in out

    def test_missing_git_omits_line(self):
        """git 缺失 → 省略 git 行，其余行保留"""
        env = EnvContext(
            cwd="/proj",
            platform="Linux",
            datetime="t",
            timezone="",
            version="0.5.0",
            provider="p",
            model="m",
            git_branch=None,
            git_dirty=None,
        )
        out = format_env(env)
        assert "git:" not in out
        assert "工作目录: /proj" in out
        assert "当前模型: p/m" in out

    def test_empty_timezone_rstrips(self):
        """时区为空时不留尾部空格"""
        env = EnvContext(
            cwd="/proj",
            platform="Linux",
            datetime="t",
            timezone="",
            version="0.5.0",
            provider="p",
            model="m",
        )
        assert "当前时间: t" in format_env(env)
        assert "当前时间: t " not in format_env(env)
