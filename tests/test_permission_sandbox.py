"""L2 路径沙箱单元测试

背景：ch03 只做前缀判断，未解析符号链接，项目内符号链接可逃逸到外部。
本章沙箱先解析符号链接（含 Windows junction）再判前缀，且用祖先回退覆盖
「新建文件含未创建中间目录」场景。
这些测试防的 bug：符号链接逃逸放行、祖先回退路径错乱、/rootfoo 误匹配。
"""

import os
import subprocess
import sys

import pytest

from newcode.permission.sandbox import check_path, resolve_root


def _make_symlink(link: str, target: str) -> bool:
    """创建符号链接，失败返回 False（如 Windows 无权限时）"""
    try:
        os.symlink(target, link)
        return True
    except OSError:
        return False


def _make_junction(link: str, target: str) -> bool:
    """Windows 上创建 junction（无需管理员权限），失败返回 False"""
    if sys.platform != "win32":
        return False
    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J", link, target],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


class TestResolveRoot:
    def test_resolve_abspath(self, tmp_path):
        root = resolve_root(str(tmp_path))
        assert os.path.isabs(root)

    def test_resolve_nonexistent_no_crash(self):
        # 防的 bug：无法解析的根直接抛异常导致崩溃
        # 空串→abspath 得 cwd；不存在的路径→realpath 原样返回，均不抛
        assert os.path.isabs(resolve_root(""))
        nonexistent = os.path.join("no_such_dir_xyz", "sub")
        assert resolve_root(nonexistent).endswith("no_such_dir_xyz" + os.sep + "sub")


class TestCheckPath:
    def test_empty_path_returns_root(self, tmp_path):
        ok, resolved = check_path("", str(tmp_path))
        assert ok
        assert resolved == str(tmp_path)

    def test_within_project_ok(self, tmp_path):
        (tmp_path / "src").mkdir()
        ok, resolved = check_path("src/main.py", str(tmp_path))
        assert ok
        assert resolved.endswith("src" + os.sep + "main.py")

    def test_absolute_within_ok(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        ok, _ = check_path(str(tmp_path / "a.txt"), str(tmp_path))
        assert ok

    def test_outside_denied(self, tmp_path):
        # ch15 N14：/tmp 是系统临时白名单（pytest tmp_path 在 /tmp 下，../ 会落到 /tmp）。
        # 项目根用非 /tmp 的假路径，../ 越界到非白名单位置仍拒。
        root = "/nonexistent-proj-root-xyz"
        ok, _ = check_path("../outside.txt", root)
        assert not ok

    def test_absolute_outside_denied(self, tmp_path):
        # 防的 bug：绝对越界路径被前缀匹配放行；/etc 非白名单（N14 仅 /tmp /private/tmp）
        ok, _ = check_path("/etc/nonexistent-outside.txt", str(tmp_path))
        assert not ok

    def test_prefix_confusion_denied(self, tmp_path):
        # 防的 bug：/rootfoo 被 startswith(/root) 误判为项目内
        root = "/nonexistent-parent-xyz/proj"
        evil = "/nonexistent-parent-xyz" + "evil"  # 与 root 同前缀但不同目录
        ok, _ = check_path(os.path.join(evil, "x.txt"), root)
        assert not ok

    def test_nonexistent_with_missing_intermediate_ok(self, tmp_path):
        # 祖先回退：新建文件含未创建中间目录，仍应判定在项目内
        ok, resolved = check_path("new/deep/dir/file.py", str(tmp_path))
        assert ok
        assert "new" in resolved
        assert "file.py" in resolved

    def test_nonexistent_outside_denied(self, tmp_path):
        # 祖先回退也要阻止越界：../../outside 不含任何项目内祖先；
        # 用非 /tmp 假根（/tmp 是 N14 白名单，pytest 根在其下无法构造真越界）
        root = "/nonexistent-proj-root-xyz"
        ok, _ = check_path("../..//outside", root)
        assert not ok

    def test_symlink_escape_denied(self, tmp_path, monkeypatch):
        # 防的 bug：符号链接指向外部目录，前缀匹配放行导致逃逸。
        # 本测试只关心 symlink 解析——关掉 N14 /tmp 白名单（pytest tmp_path 在 /tmp 下，
        # 真 escape 目标必然落在 /tmp，会撞白名单；白名单行为由 test_n14 单独测）
        import tempfile

        import newcode.permission.sandbox as sandbox_mod

        monkeypatch.setattr(sandbox_mod, "TEMP_DIR_WHITELIST", ())
        with tempfile.TemporaryDirectory() as outside:
            link = tmp_path / "link"
            if not _make_symlink(str(link), outside):
                pytest.skip("当前环境无法创建符号链接")
            ok, _ = check_path(str(link) + os.sep + "secret.txt", str(tmp_path))
            assert not ok

    def test_junction_escape_denied(self, tmp_path):
        # Windows junction 逃逸（N5 跨平台）
        import tempfile

        if sys.platform != "win32":
            pytest.skip("仅 Windows")
        with tempfile.TemporaryDirectory() as outside:
            link = tmp_path / "jlink"
            if not _make_junction(str(link), outside):
                pytest.skip("当前环境无法创建 junction")
            ok, _ = check_path(str(link) + os.sep + "secret.txt", str(tmp_path))
            assert not ok

    def test_symlink_inside_still_ok(self, tmp_path):
        # 符号链接指向项目内目录，仍应放行
        (tmp_path / "real").mkdir()
        (tmp_path / "real" / "f.txt").write_text("x")
        link = tmp_path / "link_inside"
        if not _make_symlink(str(link), str(tmp_path / "real")):
            pytest.skip("当前环境无法创建符号链接")
        ok, _ = check_path(str(link) + os.sep + "f.txt", str(tmp_path))
        assert ok
