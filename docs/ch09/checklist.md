# NewCode ch09 - 项目记忆与会话持久化 Checklist

> 每一项通过运行代码或观察行为验证。未能在当前环境执行的项目必须标记为“待人工验证”，并记录原因、风险和替代证据。

## 实现完整性

- [ ] AC1 三层指令加载：三份 `NEWCODE.md` 同时存在时，系统提示中的 `custom-instructions` 按项目根、项目配置、用户级顺序排列；缺失层静默跳过。（验证：临时 workspace/home + 单元测试）
- [ ] AC2 `@include` 展开：独占行的相对引用被完整替换，段落中的 `@include` 保持原文。（验证：InstructionLoader 测试）
- [ ] AC3 `@include` 安全：深度超过 5、环路、相对路径逃逸、绝对路径和符号链接越界均跳过并产生对应警告。（验证：边界 fixture + 输出断言）
- [ ] AC4 指令缓存：进程启动后修改指令文件不会改变已缓存的系统提示模块。（验证：加载后修改文件并重新组装提示）
- [ ] AC5 会话 ID 和目录：新 ID 匹配 `YYYYMMDD-HHMMSS-xxxx`，并创建 `conversation.jsonl` 与 `tool-results/`。（验证：SessionContext 测试）
- [ ] Session ID 时间基准：同一进程创建的 session 使用固定进程启动时间，测试可注入基准时间。（验证：注入时钟的 SessionContext 测试）
- [ ] AC6 JSONL 写入：user、assistant、tool 消息各写入合法 JSON 行，首条消息携带模型信息。（验证：SessionWriter 逐行解析）
- [ ] AC7 追加和崩溃安全：已有行不被重写，截断尾行后重新打开，前面的完整行仍可解析。（验证：SessionWriter fixture）
- [ ] AC8 compact 标记：压缩时先追加 `compact` 标记，再追加压缩后的消息；恢复从最后一个标记后开始。（验证：压缩/恢复测试）
- [ ] AC9 Conversation 回调：追加和替换回调次数、参数副本正确；未设置回调时既有行为不变。（验证：ConversationManager 测试）
- [ ] AC10 `/resume` 路由：IDLE 输入不发送给 LLM，进入列表；Esc 返回 IDLE；Agent 运行期间返回等待提示。（验证：mock TUI 事件测试）
- [ ] AC11 列表和搜索：有效新格式会话按修改时间倒序展示标题、相对时间、模型和文件大小；搜索只保留标题匹配项。（验证：列表扫描和 TUI 测试）
- [ ] AC12 坏行跳过：JSONL 中间存在非法行时，其余有效消息正常恢复。（验证：Recovery 测试）
- [ ] AC13 工具调用截断：末尾未配对的 assistant tool call 被截断，孤立 tool result 不被伪造配对。（验证：多工具调用 fixture）
- [ ] AC14 compact 恢复：有标记时只加载最后标记后的消息，无标记时从首条有效消息加载。（验证：Recovery 测试）
- [ ] AC15 超限和时间跨度：超出阈值最多尝试一次压缩，失败后按完整消息组降级；暂停超过 6 小时追加固定提醒。（验证：mock compact + 时间 fixture）
- [ ] AC16 追加恢复：恢复后新消息追加到原 JSONL，原新会话文件保留。（验证：Runtime 集成测试）
- [ ] AC17 过期清理：31 天前的新格式目录被后台清理；旧格式和当前活动目录保留且不出现在列表中。（验证：清理测试 + 启动集成测试）
- [ ] AC18 记忆创建：显式偏好触发 mock LLM 的 create 操作，在正确作用域生成带 frontmatter 的 Markdown 笔记。（验证：MemoryManager 测试）
- [ ] AC19 索引更新：create/update/delete 与 `MEMORY.md` 行保持一致，索引不超过 200 行和 25KB。（验证：Store 测试）
- [ ] AC20 记忆注入：项目索引在用户索引之前进入 `long-term-memory`；超过 25KB 时截断并追加 `(index truncated)`。（验证：Prompt/Manager 测试）
- [ ] AC21 异步不阻塞：记忆更新未完成时下一条输入仍立即进入 Agent 主流程。（验证：可控延迟 mock provider）
- [ ] AC22 失败隔离：provider 错误、JSON 解析错误和磁盘写入错误只记录日志，主会话继续运行。（验证：失败注入测试）
- [ ] AC23 类型和路径隔离：四类笔记只能写入规定级别目录，非法文件名、路径穿越和跨项目路径被拒绝。（验证：Store 安全测试）
- [ ] AC24 Prompt 模块：非空指令和记忆分别进入 priority 80/100 模块，空内容不增加模块。（验证：Prompt 测试）
- [ ] AC25 启动顺序：第一条用户请求前已完成指令和索引加载，过期清理在后台运行。（验证：启动集成测试）
- [ ] AC26 压缩并发：记忆更新只读会话快照、只写 memory；与 `/compact` 并发不会破坏 conversation。（验证：并发集成测试）
- [ ] AC27 回归兼容：现有 Agent Loop、工具调用、上下文压缩、权限和 TUI 行为继续通过。（验证：全量测试）
- [ ] `/session` 管理命令：`list`、`resume`、`new`、`path`、`clean` 行为可用，删除/清理操作需要确认。（验证：mock 命令路由测试）
- [ ] `/memory` 管理命令：`list`、`show`、`edit`、`path`、`clear` 行为可用，作用域和确认流程正确。（验证：mock 命令路由测试）

## 架构与集成

- [ ] 指令、session、memory 三个子包可独立导入，核心逻辑可在无 provider/无终端环境下测试。（验证：单元测试和导入检查）
- [ ] 恢复流程原子切换 Conversation、SessionContext、SessionWriter；切换失败时旧会话仍可继续工作。（验证：Runtime 集成测试）
- [ ] SessionWriter 的追加操作具备跨协程/线程锁保护，并在每次写入后 flush + fsync。（验证：并发 SessionWriter 测试）
- [ ] memory Store 的文件和索引更新使用锁及临时文件替换，异常不会留下半写文件。（验证：故障注入测试）
- [ ] 退出流程停止接收输入、关闭 SessionWriter，并有限等待或取消后台记忆任务。（验证：生命周期测试）
- [ ] 退出记忆触发：退出前对未完成的记忆快照尽力触发一次更新，超时后取消且不影响会话退出。（验证：延迟 mock provider）

## 编译与测试

- [ ] `python -m pytest -q` 全部通过，且测试未写入真实用户 home、API 或网络。（验证：命令输出和临时目录检查）
- [ ] `ruff check .` 通过；若项目配置类型检查，则运行并记录类型检查实际结果。（验证：命令输出）
- [ ] `git status --short` 和 docs 变更检查确认除本章四份流程文档外没有意外文档修改。（验证：版本控制检查）

## 端到端场景

- [ ] 新项目启动：加载项目规范和已有记忆，发送一轮对话后检查提示模块、JSONL user/assistant 行和工具结果目录。（验证：mock provider 启动测试）
- [ ] 中断后恢复：准备含坏行、compact 标记和孤立工具调用的会话，执行 `/resume`，确认列表、恢复降级、时间提醒和后续追加均可观察。（验证：无真实终端的 TUI/Runtime 集成测试）
- [ ] 偏好记忆闭环：用户表达“回复简洁点”，异步更新完成后新会话加载对应索引，主会话期间下一条输入不等待更新。（验证：mock provider 端到端测试）

## 待人工验证

- [ ] 真实 Textual 终端中的上下键导航、输入过滤、Enter 选择和 Esc 取消。（原因：自动化环境无真实终端；替代证据：mock 事件路径测试；风险：终端布局或焦点问题；补验：接入真实终端后执行）
- [ ] 真实 provider 的流式回复、记忆更新 JSON 输出和权限/工具组合。（原因：当前环境无 API key；替代证据：provider 接口 mock 测试；风险：真实 SDK 事件格式差异；补验：配置 provider 后执行）
