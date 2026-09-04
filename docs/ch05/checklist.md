# NewCode 结构化 System Prompt 与 Prompt Cache 优化 — 验收清单 (checklist.md)

> 每一项通过运行代码或观察行为来验证，聚焦系统行为，与实现解耦。

## 实现完整性

- [ ] **模块化组装**：七个固定模块按优先级（角色设定→行为准则→工具使用指南→代码质量规范→安全边界→任务执行模式→输出风格）拼装，模块间空行分隔（验证：`pytest tests/test_builder.py -q`；打印 `PromptBuilder.build()` 观察顺序）
- [ ] **可选模块追加**：配置自定义 system_prompt 后，内容以「自定义指令」模块追加在固定模块之后，七个内置模块仍保留（验证：`pytest tests/test_builder.py -q` 可选模块用例）
- [ ] **环境信息内容**：环境段包含 cwd / 操作系统平台 / 日期时间时区 / git 状态 / 应用版本 / provider+model，全部可被模型读取（验证：`pytest tests/test_env.py -q`；打印 `format_env()` 输出逐项核对）
- [ ] **关键约定双重强化**：「优先用专用工具而非 shell」「编辑前必先读」在六个工具 description 与工具使用指南模块中均出现（验证：`pytest tests/test_tools.py -q` 工具描述断言；grep 检查 ToolUsage 模块内容）
- [ ] **system-reminder 注入格式**：补充消息 role=user、`<system-reminder>` 标签包裹；每轮动态构造、不写入持久历史（验证：`pytest tests/test_reminders.py -q`；mock provider 打印 payload 观察消息结构）
- [ ] **PlanMode 注入频率**：一次规划模式请求内第 0/5 轮注入完整版、第 1-4 轮精简版（验证：`pytest tests/test_reminders.py -q`；agent mock 检查每轮 reminders）
- [ ] **评估脚本**：5 个场景脚本可运行，打印工具调用序列、每轮 TokenUsage、缓存命中字段（验证：`python scripts/eval_scenarios.py --scenario 5` 观察输出）

## 集成

- [ ] **三通道分发**：段1 含七个模块（稳定）、段2 环境信息、tools 含六个工具；环境信息变更不改变段1 稳定内容（验证：`pytest tests/test_assembler.py -q`）
- [ ] **Anthropic 缓存标记（mock）**：请求中段1 内容块与 tools 通道含 `cache_control: ephemeral`；段2 在断点之后（验证：mock 客户端捕获请求体断言）
- [ ] **OpenAI 无缓存标记**：请求结构不含任何缓存标记字段，正常发出；usage 中 `cached_tokens` 被正确解析（验证：`pytest tests/test_cache_usage.py -q`；mock 客户端捕获请求体断言）
- [ ] **缓存字段解析健壮**：Anthropic 解析 cache_creation/cache_read；兼容端点缺字段按 0 处理不抛异常（验证：`pytest tests/test_cache_usage.py -q`）
- [ ] **历史合法性**：注入 system-reminder 后消息序列角色交替合法、工具调用与结果配对完整，无 400 类错误（验证：`pytest tests/test_assembler.py -q`；mock provider 逐轮校验请求结构）
- [ ] **双协议装配一致**：同一 PromptPayload 分别过 Anthropic 与 OpenAI 适配器，段1/段2 内容、reminders 注入时机、规划模式节奏一致（验证：`pytest tests/test_provider_tools.py -q` 与 test_cache_usage 对比两适配器输出）
- [ ] **稳定前缀跨轮一致**：连续两次组装 stable_prompt 逐字节一致，变化时日志告警（验证：`pytest tests/test_assembler.py -q`；观察告警日志）

## 编译与测试

- [ ] 项目编译无错误（验证：`python -c "import newcode.main"`）
- [ ] 全部测试通过（验证：`pytest tests/ -q`）
- [ ] lint 通过（验证：`ruff format --check . && ruff check .`）
- [ ] 版本号为 0.5.0（验证：`newcode --version` 或 `python -c "import newcode; print(newcode.__version__)"`）
- [ ] **ch04 向后兼容**：Agent Loop 测试（自然终止、多工具、取消、未知工具、流式错误、Plan Mode 只读）全部通过，用例未删（验证：`pytest tests/test_agent.py -q` 全绿，diff 确认用例保留）

## 端到端场景

- [ ] **场景 1（多步工具任务）**：输入"读取 main.py 和 agent.py，对比导入，创建 analysis.md" → 多轮工具调用（read ×2 → write），每轮 TokenUsage 含缓存字段（验证：`python scripts/eval_scenarios.py --scenario 1` 观察工具序列与 usage）
- [ ] **场景 2（工具优先级）**：输入"用 grep 找 `def main` 位置" → 模型优先用 search_code 而非 execute_command（验证：`python scripts/eval_scenarios.py --scenario 2` 观察首个工具调用）
- [ ] **场景 3（先读后改）**：输入"把 main.py 里的 max_turns 改成 30" → 模型先 read_file 再 edit_file（验证：`python scripts/eval_scenarios.py --scenario 3` 观察调用顺序）
- [ ] **场景 4（PlanMode 只读）**：`/plan 分析项目结构` → 只出现只读工具，产出结构化计划文档，无文件修改、无命令执行（验证：TUI 手动或 mock 驱动 agent.run(mode="plan")）
- [ ] **场景 5（缓存命中 - 真实 API）**：多轮对话第二轮起 usage 出现 cache_read > 0 且 input token 低于首轮基线 → 缓存真生效（验证：真实 API key 下 `python scripts/eval_scenarios.py --scenario 5`）

## 待人工验证

- [ ] **场景 5 真实缓存命中**：需真实 Anthropic/OpenAI 端点与 API key；mock 仅验证解析逻辑，不验证真实命中。
  - 原因：测试环境无 API key 时无法发起真实请求
  - 替代验证：`test_cache_usage.py` mock 覆盖缓存字段解析路径
  - 风险：组装后稳定前缀若 <1024 token 缓存不生效，未被真实命中验证发现
  - 补验：由用户在有 API key 的环境运行 `--scenario 5` 确认 cache_read > 0 且 input 下降
- [ ] **场景 4 PlanMode 只读**：需真实 TUI 交互验证 `/plan` 模式切换。
  - 原因：mock 驱动 agent.run(mode="plan") 验证了工具集受限，但 TUI 的 /plan 模式切换与提示符需要真实终端
  - 替代验证：mock 驱动 agent.run(mode="plan") 断言只读工具集
  - 补验：由用户在终端手动 `/plan` 确认
