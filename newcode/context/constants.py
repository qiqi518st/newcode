"""全部硬编码阈值常量（spec F32：不暴露为任何配置项，调整属代码变更）。

模块级变量而非字面常量，便于单测 monkeypatch 改小并 restore。
"""

# ── 第 1 层（大结果存盘，字节口径）───────────────────────────
SINGLE_RESULT_THRESHOLD = 50_000  # 单条工具结果落盘阈值（字节，spec F1）
AGGREGATE_LIMIT = 200_000  # 单轮聚合预算（字节，spec F2）
PREVIEW_MAX_LINES = 20  # 预览体头部行数上限（spec F4）
PREVIEW_MAX_BYTES = 2048  # 预览体头部字节上限（spec F4）

# ── 第 2 层（LLM 全量摘要，token 口径）───────────────────────
SUMMARY_RESERVE_TOKENS = 20_000  # 摘要输出预留（spec F7）
AUTO_SAFETY_MARGIN = 13_000  # 自动触发安全余量（spec F7）
MANUAL_SAFETY_MARGIN = 3_000  # 手动触发安全余量（仅摘要请求自检，spec F23）
RECENT_TOKEN_FLOOR = 10_000  # 近期原文保留 token 下界（spec F11）
RECENT_COUNT_FLOOR = 5  # 近期原文保留条数下界（spec F11）

# ── 压缩后恢复（spec F15/F16/F31）────────────────────────────
MAX_RECENT_FILES = 5  # 恢复段最多文件数
PER_FILE_TOKEN_BUDGET = 5_000  # 单文件快照 token 上限
SKILL_RECOVERY_BUDGET = 25_000  # Skill 恢复注入预算

# ── 重试与熔断（spec F27/F28）────────────────────────────────
COMPACT_RETRY_LIMIT = 3  # 单次压缩行动重试上限
PTL_DIRECT_RETRY_LIMIT = 3  # 摘要请求自身 PTL 直接重试次数（F27）
PTL_DROP_RATIO = 0.2  # PTL 比例丢弃步长（F27）
GROUP_DROP_STEP = 2  # 熔断菜单分组丢弃每次组数（F28 菜单分支）
AUTO_GATE_LIMIT = 3  # 自动路径连续失败闸阈值（轮）

# ── Token 估算（spec F13）────────────────────────────────────
ESTIMATE_CHARS_PER_TOKEN = 3.5  # 字符→token 估算比

# ── Context Window（spec F29）────────────────────────────────
ONE_M_WINDOW = 1_000_000  # [1m] 后缀窗口
DEFAULT_WINDOW_ANTHROPIC = 200_000  # anthropic 协议默认窗口
DEFAULT_WINDOW_OPENAI = 128_000  # openai 协议默认窗口
CAPABILITY_TABLE_FLOOR = 100_000  # 能力表收录下限（≥100K 才进表）
CONTEXT_WINDOW_FLOOR = (
    33_000  # context_window 下界（SUMMARY_RESERVE+AUTO_MARGIN，spec F7 下界检查）
)
