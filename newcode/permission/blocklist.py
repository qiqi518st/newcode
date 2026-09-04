r"""危险命令黑名单（L1）

用内置正则匹配拦截高危命令，不可配置、不可关闭。
在任何权限模式（含 bypassPermissions）下都生效。

启发式防御，非完备保证——明确不追求穷尽所有危险命令。
"""

import re

# 内置黑名单正则常量
BLOCKLIST_PATTERNS: list[re.Pattern] = [
    # === 递归强制删除根/家目录 ===
    # 匹配 rm -rf /, rm -fr ~, rm -rf /*, rm -rf /etc 等
    re.compile(
        r"\brm\s+.*-[rf]+\s+(?:"
        r"/\*|"
        r"/(?:root|home|etc|bin|boot|dev|lib|proc|sys|usr|var|opt|sbin|tmp)(?:/|\s|$)|"
        r"/(?:\s|$)|"
        r"~(?:\s|$)|"
        r"\$(?:HOME|PWD)(?:\s|$)"
        r")",
        re.IGNORECASE,
    ),
    # === 写块设备 ===
    re.compile(r"\bdd\s+.*\bof=/dev/", re.IGNORECASE),
    re.compile(r"\bcat\s+.*>\s*/dev/", re.IGNORECASE),
    re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE),
    # === fork 炸弹 ===
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
    re.compile(r"\bperl\s+-e\s+.*fork\s+while\s+true", re.IGNORECASE),
    re.compile(r"\bpython.*-c\s+.*os\.fork\(\).*while\s+True", re.IGNORECASE),
    # === 格式化文件系统 ===
    re.compile(r"\bmkfs\.", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bmke2fs\b", re.IGNORECASE),
    # === 远程脚本下载即执行 ===
    re.compile(r"\bcurl\s+.*\|\s*(?:sh|bash|zsh|ksh|dash)\b", re.IGNORECASE),
    re.compile(r"\bwget\s+.*-O\s*-\s*.*\|\s*(?:sh|bash|zsh|ksh|dash)\b", re.IGNORECASE),
    re.compile(r"\bcurl\s+.*\|\s*(?:python|perl|ruby)", re.IGNORECASE),
    re.compile(r"\bwget\s+.*\|\s*(?:sh|bash)", re.IGNORECASE),
    # === 危险系统命令 ===
    re.compile(r"\bchmod\s+.*(?:777|4777)\s+/", re.IGNORECASE),
    re.compile(r"\bchown\s+.*:\s*/", re.IGNORECASE),
    # === 清空关键磁盘 ===
    re.compile(r"\bdd\s+if=/dev/zero\s+of=/dev/", re.IGNORECASE),
    re.compile(r"\bdd\s+if=/dev/urandom\s+of=/dev/", re.IGNORECASE),
]


def hits_blacklist(command: str) -> bool:
    """任一 Pattern 匹配即返回 True"""
    return any(p.search(command) for p in BLOCKLIST_PATTERNS)
