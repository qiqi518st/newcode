# ch11 变更记录：/skill 生命周期命令整合（load/unload 合并 on/off）

> 日期：2026-08-21
> 范围：`/skill` 管理命令的 on/off/load/unload 四命令 → load/unload 两命令
> 状态：已实现并测试通过

## 背景与动机

原 `/skill` 命令有 7 个子命令：`list` / `info` / `reload` / `load` / `on` / `off` / `unload`。
其中 `load`/`unload`（会话级激活/卸载）与 `on`/`off`（持久启停）功能高度重叠，对用户造成心智负担。
决定整合为 5 个子命令：`list` / `info` / `reload` / `load` / `unload`。

## 变更内容

### 1. `load <name>` = 原 `load` + 原 `on`

合并后的行为（**获得持久特性，方向 A**）：

- 从 disabled 集合移除（**跨会话持久**，落盘 `disabled.json`，重启后恢复可发现）
- 激活 SOP 到当前会话（跳过阶段一，直接阶段二激活）
- 恢复 `/<名字>` 命令注册（若之前被 unload 删除）

### 2. `unload <name>` = 原 `unload` + 原 `off`

合并后的行为（**获得持久特性，方向 A**）：

- 加入 disabled 集合（**跨会话持久**，重启后仍不出现、`/help` 与 Tab 补全不列出）
- 立即失活当前会话的激活态
- 删除 `/<名字>` 命令注册（精确删单个，不误伤其它 `[skill]` 命令）
- 清除内存缓存（下次 `get()` 从磁盘重读）
- **Skill 文件保留在磁盘，`/skill list` 仍可见并标注 `[disabled]`**

### 3. 删除 `on` / `off`

两个子命令及其 handler（`_do_on` / `_do_off`）移除。

### 4. `/skill list` 显示全部 Skill（含禁用的）

- 改用 `Catalog.list_all()`（不再排除 disabled）
- 每行末尾标注 `[disabled]`（禁用的 Skill）
- 目的：用户 unload 后仍能在管理视图看到该 Skill 存在，避免「消失无痕迹」；
  只有 `/<名字>` 命令、Tab 补全、阶段一摘要排除禁用项

### 5. `/skill info` 增加 `disabled:` 状态行

## 新旧对比

```
旧：/skill <list|info|reload|load|on|off|unload> [<name>]
新：/skill <list|info|reload|load|unload> [<name>]
```

| 旧命令 | 新命令 | 备注 |
|--------|--------|------|
| `load <n>` | `load <n>` | 现也启用（持久） |
| `unload <n>` | `unload <n>` | 现也禁用（持久） |
| `on <n>` | 删除 | 并入 `load` |
| `off <n>` | 删除 | 并入 `unload` |

## 实现改动

| 文件 | 改动 |
|------|------|
| `mewcode/slash/commands/skill.py` | 删 on/off；load/unload 新语义；list 用 list_all + [disabled]；info 加 disabled 行；usage 更新 |
| `mewcode/skills/catalog.py` | 新增 `list_all()`（全部含 disabled）与 `invalidate(name)`（清内存缓存） |
| `tests/test_ch11_skill_command.py` | on/off 用例迁移到 load/unload 新语义；新增 list 显示 [disabled]、load 恢复命令用例 |
| `tests/test_ch10_integration.py` | /skill usage 断言更新 |

## 验证

- `tests/test_ch11_skill_command.py` 13 用例全绿（含 unload 持久、load 恢复命令、list 显示禁用状态）
- 全量 `pytest tests/` 通过（701 passed, 3 skipped）
- `ruff format --check` + `ruff check` 清洁

## 设计要点（本次确认的决策）

- **持久性归 load/unload**：方向 A，保住 spec F7.8/N12 的跨会话禁用要求
- **两个列表职责分离**：`/<名字>` 命令 / `/help` / Tab 补全 = 只列可用项；
  `/skill list` 管理视图 = 列全部项并标注状态
- **彻底移除 Skill**：`unload` 后再删磁盘文件 + `/skill reload`（文件保留是 unload 语义）
