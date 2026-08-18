# MewCode ch08 — 上下文管理 验收清单 (checklist.md)

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。验证方式写在每项末尾括号内。
> 凡需真实 LLM API key / 真实终端 / 网络才能验证的行为，列为「待人工验证」，**不混入「通过」**（CLAUDE.md「验证受阻必上报」纪律）。
> 本文件是 mew-spec 流程产物，本身允许写入；除此之外测试/验证过程不改动 docs/ 下任何已存在文件。

## 实现完整性（context 子包各组件已实现且可被调用）

- [ ] **子包门面可导入**：`from mewcode.context import ContextManager, AutoCompactGate, estimate_tokens, get_context_window_for_model, ContentReplacementState, FileTracker, Skill, SkillRegistry` 不报错。（验证：`python -c "from mewcode.context import ContextManager, AutoCompactGate, estimate_tokens, get_context_window_for_model, ContentReplacementState, FileTracker, Skill, SkillRegistry; print('ok')"` 输出 `ok`）【T18、AC 全集基础】
- [ ] **Token 估算纯函数**：`usage_to_anchor` 四字段求和；`estimate_tokens(anchor, all_msgs, anchor_msg_len)` 只算锚点之后增量；`anchor=0` 退化纯字符；`estimate_messages` 纯字符。（验证：`python -m pytest tests/test_context_tokens.py -q` 全过，含 `test_usage_anchor_sum` / `test_estimate_tokens_delta_only` / `test_estimate_tokens_zero_anchor`）【F13/F14、AC23】
- [ ] **Context Window 四级解析**：env 覆盖 → `[1m]` 后缀 → 能力表(≥100K) → 协议默认；查表 miss / 异常不抛落默认。（验证：`python -m pytest tests/test_context_window.py -q` 全过，含 env 覆盖 / `[1m]` / 能力表 / 协议默认 / 非法 env 跳级用例）【F29、AC21】
- [ ] **会话目录**：`.mewcode/sessions/<unix_ts>-<short_random>/tool-results/` 按需创建、幂等；空 id 兜底命名不抛。（验证：`python -m pytest tests/test_context_session.py -q` 全过，含格式 / 落盘路径 / 幂等 / 兜底用例）【F33、AC22】
- [ ] **替换决策账本**：`decide_once` 一次完成查账本→决策→写账本；kept/replaced/skip 三态；同 id 永不翻转、预览复用不重造；并发原子无中间态。（验证：`python -m pytest tests/test_context_replacement.py -q` 全过，含冻结 / skip 不记账 / 并发恰好调一次用例）【F5、AC4、AC24a】
- [ ] **自动闸**：连续 3 轮自动失败停自动触发；成功清零；手动成功解除；仅自动路径无跨种类接口。（验证：`python -m pytest tests/test_context_autogate.py -q` 全过）【F28、AC20a】
- [ ] **文件追踪**：record 覆盖更新、recent 倒序、并发无重复/错乱、返回拷贝。（验证：`python -m pytest tests/test_context_files.py -q` 全过）【F19/F20、AC9、AC24b】
- [ ] **丢消息组**：按 user 分界分组不拆 tool_use/tool_result 对；丢最旧 / 比例丢至少 1 组。（验证：`python -m pytest tests/test_context_dropper.py -q` 全过）【F27、AC19】
- [ ] **第一层单条/聚合存盘**：60000 字节单条 → 预览替换 + 文件落盘 + 预览四项信息 + 头部 ≤20 行且 ≤2048 字节；3 条 80000 聚合 → 按大→小落盘直到 ≤200000、替换数最小达标。（验证：`python -m pytest tests/test_context_offload.py -q` 全过，含 `test_single_result_offload` / `test_aggregate_offload`）【F1/F2/F4、AC1/AC2】
- [ ] **落盘幂等 + 决策冻结**：同 id 两次 → `st_mtime_ns` 不变、跳过 I/O；两轮预览逐字节一致；落盘失败 → 不进账本、下轮重评。（验证：`python -m pytest tests/test_context_offload.py -q`，含 `test_spill_idempotent` / `test_decision_freeze` / `test_spill_failure_retryable`）【F3/F5、AC3/AC4】
- [ ] **三步原子**：落盘失败时 content 未改写 + 账本未写（无中间态）。（验证：`test_context_offload.py::test_three_step_atomic`）【F2a、AC2】
- [ ] **恢复段三块**：文件快照 ≤5 倒序 + 超 5000 token 截头尾标 `(content truncated)`；工具列表与传入 tool_defs 一致且不重算；BOUNDARY_NOTICE 固定文案；Skill 分支空实现跳过。（验证：`python -m pytest tests/test_context_recovery.py -q` 全过）【F15/F16/F17/F18、AC9/AC10/AC11】
- [ ] **摘要请求无工具 + 结构**：摘要请求体 tools 为空；只留 `<summary>` 正文、9 小节、第 6 节含所有用户原文。（验证：`python -m pytest tests/test_context_summarize.py -q`，含 `test_summary_request_no_tools` / `test_extract_summary_only`）【F8/F9/F10、AC6/AC7】
- [ ] **摘要+恢复合并单条 user + role 衔接**：新消息列表首条单条 user 含摘要+恢复全文、全程无连续 user；近期原文首条 user 时插 assistant 占位。（验证：`test_context_summarize.py` 含 `test_merge_single_user_message` / `test_role_join_placeholder`）【F10/F15、AC8a】
- [ ] **近期原文双下界不拆对**：token≥10000 且 条数≥5 双下界才停；首条非落单 tool_result。（验证：`test_context_summarize.py::test_recent_tail_dual_floor`）【F11/F12、AC8】
- [ ] **摘要请求不更新锚点**：summarize 内不调 update_anchor（spy 断言）。（验证：`test_context_summarize.py::test_summary_does_not_update_anchor`）【F14、AC23】
- [ ] **Skill 骨架**：`Skill` / `SkillRegistry` 可注册/查询/列举；内容加载 TODO 留空；registry 空时注入跳过。（验证：`python -m pytest tests/test_context_skill.py -q` 全过）【F31、AC26】

## 集成（编排、Agent、Provider、TUI 协作）

- [ ] **自动触发与 L1 联动**：达阈值触发摘要并 replace_history；未达阈值仅 L1 不动历史；L1 替换后重估跌到阈值下不再触发 L2（防「用 L1 前估算偏高过早触发」）。（验证：`python -m pytest tests/test_context_manager.py -q`，含 `test_auto_triggers_on_threshold` / `test_auto_skipped_below_threshold` / `test_auto_uses_layer1_output`）【F7/F9、AC5】
- [ ] **context_window 下界 sanity**：窗口 ≤33000 跳过自动 L2 + warning，不出现每轮反复触发摘要死循环。（验证：`test_context_manager.py::test_context_window_floor_check`）【F7、AC5a】
- [ ] **手动 /compact 跳阈值/跳闸**：远低于阈值仍摘要；自动闸已触发仍能手动压缩、成功解除闸。（验证：`test_context_manager.py` 含 `test_manual_bypasses_everything` / `test_manual_success_resets_gate`）【F22/F23/F28、AC13/AC14/AC20a】
- [ ] **紧急压缩先强制 L1 + 绕过闸**：EMERGENCY 先 offload 挪走 50K+ 再摘要；自动闸触发仍能紧急压缩。（验证：`test_context_manager.py` 含 `test_emergency_runs_layer1_first` / `test_emergency_bypasses_gate`）【F25、AC17】
- [ ] **自动失败记录闸**：连续 3 轮自动失败 → 闸停自动触发（防菜单轰炸）。（验证：`test_context_manager.py::test_auto_failure_records_gate`）【F28、AC20a】
- [ ] **手动/主循环互斥**：并发调 manage_context 与 compact_now 不交错改写 conversation。（验证：`test_context_manager.py::test_concurrent_manage_and_compact_mutex`）【F34、AC25】
- [ ] **Conversation 改造**：`get_messages_ref` 返回原始引用且改动反映到副本；`replace_history` 整体替换；`_trim` 不拆 tool_use/tool_result 对。（验证：`python -m pytest tests/test_conversation_manager.py -q` 全过）【F9/N3、AC8/AC9】
- [ ] **PromptTooLongError 哨兵两家识别**：anthropic/openai 典型 PTL → `StreamEvent(err)` 且 `isinstance(err, PromptTooLongError)`；其他 4xx/5xx 不误判；`__cause__` 保留原异常；`max_output_tokens` 透传到 anthropic `max_tokens`。（验证：`python -m pytest tests/test_provider_ptl.py -q` 全过）【F25/N6、AC17/AC29】
- [ ] **Agent 每轮前压缩钩子**：注入 context_mgr 后每轮 assemble 前 manage_context 被调；不注入时行为与既有一致。（验证：`python -m pytest tests/test_agent_context.py -q`，含 `test_manage_context_called_each_turn` / `test_backward_compat_without_context_mgr`）【F9/N8、AC28】
- [ ] **Agent PTL 兜底 + 只重试一次**：第 K 次 PTL → force_compact → 新历史重试一次；重试又 PTL → 上抛不二次。（验证：`test_agent_context.py` 含 `test_ptl_triggers_force_compact_retry_once` / `test_second_ptl_no_second_compact`）【F25/F26、AC17/AC18】
- [ ] **Agent 文件追踪回填**：read_file 成功 → file_tracker.record 被调（同 task、add_tool_result 前）；主对话成功后 update_anchor 被调。（验证：`test_agent_context.py` 含 `test_read_file_tracks_recovery` / `test_update_anchor_after_main_stream`）【F19a、AC9】
- [ ] **压缩状态事件 emit**：自动路径 BEFORE/AFTER 事件顺序 + before>after。（验证：`test_agent_context.py::test_emit_compact_events`）【F24a/F24b、AC16】
- [ ] **TUI /compact 路由**：`/compact` 走命令路径、不触发 LLM 普通请求、`run_force_compact` 被调；`/unknown` 友好提示含可用命令、不发 LLM；成功显示前后 token；自动压缩显示「正在压缩上下文...」；`/exit`/`/plan`/`/do` 迁移后行为不回归。（验证：`python -m pytest tests/test_tui_compact.py -q` 全过）【F21/F22/F24/F24a、AC12/AC15/AC16/AC28】
- [ ] **装配注入**：`main._amain` 构造 ContextManager 注入 Agent，`_oneshot` 路径同样生效。（验证：`python -c "import mewcode.main; print('import ok')"` import 链不断；`git diff mewcode/main.py` 仅见构造 + 注入）【F9、AC28】

## 编译与测试

- [ ] **包可导入、版本正确**：`mewcode.__version__` 为 `0.8.0`；`pyproject.toml` 的 `version` 与之一致。（验证：`python -c "import mewcode; print(mewcode.__version__)"` 输出 `0.8.0`）【T26】
- [ ] **格式规范**：`ruff format --check .` 无 diff。（验证：`ruff format --check .` 退出码 0）【N12、AC30】
- [ ] **静态检查**：`ruff check .` 无告警。（验证：`ruff check .` 退出码 0）【N12、AC30】
- [ ] **单元测试全过**：`python -m pytest -q` 全过，含 `tests/test_context_*.py`、`tests/test_agent_context.py`、`tests/test_tui_compact.py`、`tests/test_provider_ptl.py`、`tests/test_conversation_manager.py` 及 ch01–ch07 既有测试。（验证：`python -m pytest -q` 退出码 0）【N7/N12、AC28/AC30】
- [ ] **并发/收尾无竞态**：replacement / files / manager 测试的并发用例无数据竞争、无「已 Seen 但 replacement 未写」中间态、同 id 无两个预览版本。（验证：`python -m pytest tests/test_context_replacement.py tests/test_context_files.py tests/test_context_manager.py -q` 退出码 0）【N2/N3、AC24a/AC24b】
- [ ] **探测脚本独立可用（自动侧）**：`scripts/probe_context_window.py --help` 正常打印用法退出 0；脚本不被 Agent 主流程 import。（验证：`python scripts/probe_context_window.py --help` 退出码 0；`grep -rn "probe_context_window" mewcode/` 无命中）【F30、AC27】
- [ ] **.gitignore 生效**：`.mewcode/sessions/` 不入库。（验证：启动后跑一次工具调用，`git status` 不出现 sessions 子目录）【F33】
- [ ] **docs 保护自检**：测试/验证过程未改动 docs/ 下任何已存在文件，仅 `docs/ch08/` 四份 mew-spec 流程文档为交付物。（验证：`git status docs/` 仅显示 `docs/ch08/` 新增，`git diff docs/` 对既有文档无修改）【CLAUDE.md docs 保护规则】

## 端到端场景

- [ ] **场景 1（长会话自动压缩不瘫）**：连续多轮对话使估算 token 逼近窗口 → 每轮前自动摘要触发、TUI 显示「正在压缩上下文...」→「已压缩，token 从 X 降至 Y」，对话持续不因上下文超长被 provider 拒绝。（验证：配小窗口如 `CLAUDE_CODE_MAX_CONTEXT_TOKENS=80000` 手动冒烟；自动侧由 `test_context_manager.py::test_auto_triggers_on_threshold` + `test_agent_context.py::test_emit_compact_events` 覆盖）【G1、F7/F24a、AC5/AC16】
- [ ] **场景 2（大工具结果落盘可重读）**：一次返回 60000 字节以上的工具调用 → 对话中该结果变预览体（含字节数/头部/路径/重读提示四项）；`.mewcode/sessions/<id>/tool-results/<tool_use_id>` 下有完整原文；模型可用文件读取工具显式重读。（验证：`test_context_offload.py::test_single_result_offload` + AC1 端侧见待人工验证）【G4、F3/F4、AC1】
- [ ] **场景 3（撞墙紧急压缩续命）**：provider 返回 `prompt_too_long` → TUI 显示「上下文撞墙，自动压缩中...」→ 压缩后用新历史重试成功、用户最新输入不丢。（验证：`test_agent_context.py::test_ptl_triggers_force_compact_retry_once` 自动侧；真实端侧见待人工验证）【G6、F25、AC17】

## 待人工验证（依赖真实 API / 真实终端，不混入「通过」）

- [ ] **端到端真实摘要 + 自动压缩**——真实 provider 下长会话触发自动摘要、`/compact` 手动触发、TUI 不闪退不卡死。
  - **受阻原因**：需有效 LLM API key + 网络 + 真实终端观察 TUI。
  - **替代验证**：T31/T32/T34 用 mock provider 覆盖摘要流程、合并 user 消息、PTL 重试、agent 集成；真实 LLM 调用与真实 TUI 渲染未在 CI 验证。
  - **风险**：摘要请求真实 max_output_tokens=8192 是否够、真实 Anthropic user/assistant 交替约束、`<summary>` 标签解析稳定性 若有 bug 单测测不到。
  - **补验**：开发者按 task T36 步骤执行（配 80000 窗口触发自动摘要、大文件 ReadFile 落盘、/compact、/unknown、迁移命令回归）。
- [ ] **真实大结果落盘 + 重读**——80KB 文件 ReadFile 后 `.mewcode/sessions/<id>/tool-results/` 出现对应文件、下一轮请求该结果展示预览体、模型可重读。
  - **受阻原因**：需真实终端观察落盘与预览体。
  - **替代验证**：T30 `test_single_result_offload` 用 `tmp_path` 覆盖落盘与预览构造。
  - **风险**：真实 read_file 结果字节数、截断提示剥离的真实效果未在 CI 验证。
  - **补验**：随 T36 一并执行。
- [ ] **探测脚本真实探测**——对真实模型二分逼近 PTL 边界并打印结果，手工回填能力表。
  - **受阻原因**：需有效 API key + 网络。
  - **替代验证**：`--help` 与「不被 import」已自动验证；二分逻辑本身简单（发请求→判 PTL→缩区间）。
  - **风险**：探测请求消耗 token、不同 provider 的 PTL 错误形态差异 未在 CI 验证。
  - **补验**：开发者有 key 环境跑 `scripts/probe_context_window.py`，结果手工填入 `capabilities.py` 提交。

## 验收报告模板

```
## 验收报告

### 通过（N/M）
- [x] 条目 — 证据：（命令输出/观察到的行为）
...

### 未通过（如有）
- [ ] 条目 — 预期：X，实际：Y，修复方案：...

### 待人工验证（环境受限项）
- [ ] 端到端真实摘要+自动压缩 — 原因：需真实 API/终端；替代：mock 覆盖集成层；风险：真实 LLM 调用未验；补验：按 T36
- [ ] 真实大结果落盘+重读 — 原因：需真实终端；替代：tmp_path 单测；风险：真实 read_file 效果未验；补验：随 T36
- [ ] 探测脚本真实探测 — 原因：需 API key；替代：--help+不被 import；风险：PTL 形态差异未验；补验：有 key 环境跑

### 端到端
- [x] 场景 1（长会话自动压缩，自动侧）— 结果：...
- [x] 场景 2（大结果落盘，自动侧）— 结果：...
- [x] 场景 3（撞墙紧急压缩，自动侧）— 结果：...
- [ ] 场景 1/2/3 真实端侧 — 待人工验证：见 T36
```

## 自检

- **spec 对齐**：spec.md 的 AC1–AC30 每条均有对应 checklist 条目——AC1/AC2/AC3/AC4→实现完整性（offload/幂等/冻结/原子）、AC5/AC5a→集成（自动触发/下界）、AC6/AC7→摘要无工具/结构、AC8/AC8a→近期原文双下界/合并 user+role 衔接、AC9/AC10/AC11→恢复段三块、AC12/AC13/AC14/AC15→手动路由/跳阈值/跳闸/提示、AC16→UX 事件、AC17/AC18→紧急压缩/不重复、AC19→PTL 重试、AC20/AC20a→单次行动熔断/自动闸、AC21→四级解析、AC22→会话目录、AC23→锚点更新、AC24a/AC24b→并发、AC25→互斥、AC26→Skill、AC27→探测脚本、AC28→既有不退化、AC29→跨协议、AC30→规范。✓
- **可观测性**：每项均为「运行 X，期望 Y」或「观察行为」，带可执行命令或 pytest 用例名，不依赖逐行读代码。✓
- **耦合测试**：条目锚定行为（"连续 3 轮自动失败停触发"、"预览含四项信息"），不锚定行号/具体函数实现；pytest 用例名随测试文件移动不失效。✓
- **端到端**：含场景 1（长会话自动压缩）、场景 2（大结果落盘）、场景 3（撞墙紧急压缩），自动侧可跑、真实端侧标待人工验证。✓
- **受阻上报**：三项待人工验证各给原因/替代/风险/补验，不混入「通过」。✓
