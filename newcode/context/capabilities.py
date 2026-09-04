"""静态能力表（spec F29 第 3 级）：已知大上下文模型的最大输入 token。

仅收录 ≥100K 的模型（CAPABILITY_TABLE_FLOOR），<100K 不进表、落入协议默认。
表值旁标注来源与时间；scripts/probe_context_window.py 产出的探测值由开发者
手工追加到此并提交（spec F30 不自动回填）。
"""

CAPABILITIES: dict[str, int] = {
    # 来源：OpenAI 官方文档（128K 输入）
    "gpt-4o": 128_000,
    "gpt-4o-2024-11-20": 128_000,
    "gpt-4-turbo": 128_000,
    # 来源：OpenAI 官方文档（200K 输入）
    "gpt-4.1": 1_000_000,
    "gpt-4.1-mini": 1_000_000,
    "gpt-4.1-nano": 1_000_000,
}
