"""上下文管理子包。

两层压缩（第一层大结果落盘 / 第二层 LLM 摘要）+ Token 估算 + Context Window 解析
+ 文件追踪 + 压缩后恢复 + 自动触发闸 + Skill 骨架 + 会话落盘。

依赖边界：仅依赖 mewcode.provider.base（Message/ToolCall/ToolResult/TokenUsage/Provider/
ToolDefinition）、mewcode.conversation.manager、标准库；不依赖 agent / tui / permission /
mcp / config（context_window 经构造传入 model/protocol 字符串，不读 config）。

完整导出见 __all__（ch08 T18 填充）。
"""
