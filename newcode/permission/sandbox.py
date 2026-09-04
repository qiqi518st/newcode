"""路径沙箱（L2）

文件类工具读写限定在项目根目录内；ch15 N14 额外开放系统临时目录白名单。
先解析符号链接再做前缀判断，防止逃逸。
在任何权限模式（含 bypassPermissions）下都生效。
"""

import os

# ch15 N14：系统临时目录白名单（file-class 工具生效；bash 走 exec-class 不受沙箱约束）。
# 理由：工具脚本和队员经常需要 /tmp 做中转文件，严格限定项目根内会误杀正常用法。
TEMP_DIR_WHITELIST: tuple[str, ...] = ("/tmp", "/private/tmp")


def resolve_root(root: str) -> str:
    """规整项目根为绝对路径 + realpath；失败返回原值"""
    try:
        return os.path.realpath(os.path.abspath(root))
    except (OSError, ValueError):
        return root


def eval_symlinks_or_ancestor(abs_path: str) -> str:
    """解析路径：存在→realpath；不存在→逐级向上找最近已存在祖先目录，
    对该祖先 realpath 后再拼接剩余段。

    覆盖「新建文件含未创建中间目录」场景。
    """
    # 路径已存在，直接解析
    if os.path.exists(abs_path):
        try:
            return os.path.realpath(abs_path)
        except OSError:
            return abs_path

    # 路径不存在，逐级向上找最近已存在祖先
    current = abs_path
    segments: list[str] = []
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            # 到达根目录，无法再向上；best-effort 返回原路径
            return abs_path
        if os.path.exists(parent):
            try:
                real_parent = os.path.realpath(parent)
            except OSError:
                real_parent = parent
            # 当前段是已存在祖先的直接子级，必须一起拼上，否则丢段
            segments.insert(0, os.path.basename(current))
            return os.path.join(real_parent, *segments)
        segments.insert(0, os.path.basename(current))
        current = parent


def check_path(target_path: str, project_root: str) -> tuple[bool, str]:
    """检查目标路径是否在项目根内。

    返回 (ok, resolved_path)。
    ok=False 表示越界，ok=True 表示通过。
    """
    # 空 path 视为 root
    if not target_path:
        return True, project_root

    # 相对路径用 root 拼接后解析为绝对路径
    if os.path.isabs(target_path):
        abs_path = target_path
    else:
        try:
            abs_path = os.path.join(project_root, target_path)
        except (OSError, ValueError):
            return False, ""

    # 规整为绝对路径
    try:
        abs_path = os.path.abspath(abs_path)
    except (OSError, ValueError):
        return False, ""

    # 解析符号链接（含祖先回退）
    try:
        resolved = eval_symlinks_or_ancestor(abs_path)
    except (OSError, ValueError):
        return False, ""

    root = resolve_root(project_root)

    # ch15 N14：系统临时目录白名单（/tmp、/private/tmp）→ 放行（file-class 中转文件）
    for temp_dir in TEMP_DIR_WHITELIST:
        if resolved == temp_dir or resolved.startswith(temp_dir + os.sep):
            return True, resolved

    # 按段比对，避免 /rootfoo 误匹配 /root/foo
    if resolved == root:
        return True, resolved
    if resolved.startswith(root + os.sep):
        return True, resolved

    return False, resolved
