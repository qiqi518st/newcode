---
name: commit
description: 本地规范提交流程（conventional commit，逐个 add）
mode: inline
---

# Commit Skill

你正在执行「本地规范提交」任务。请严格按以下 SOP 完成，不要跳跃步骤。

## 1. 查看全局状态

运行 `git status` 查看工作区全局状态。确认存在未提交的变更；若没有变更，直接告知用户「没有需要提交的变更」并结束。

## 2. 查看变更细节

- 运行 `git diff` 查看**未暂存（unstaged）**的变更。
- 运行 `git diff --staged` 查看**已暂存（staged）**的变更。
- 明确区分这两类变更，理解每个文件改了什么。

## 3. 生成提交信息

按 **Conventional Commits** 规范生成提交信息：

- 格式：`<type>(<scope>): <subject>`
- type 可选：`feat`（新功能）/ `fix`（修复）/ `refactor`（重构）/ `docs`（文档）/ `test`（测试）/ `chore`（杂务）/ `perf`（性能）/ `style`（格式）
- scope 可选：涉及的主要模块/章节，如 `ch10`
- subject：一句话概括，小写开头、祈使语气、不超过 50 字符

示例：`feat(ch10): add slash command registry`

## 4. 逐个暂存

**逐个 `git add <文件>`，禁止使用 `git add -A` 或 `git add .` 一次性全加。**

- 每个文件独立 `git add`，让用户看清加了什么。
- 若变更覆盖超过 **10 个文件**，主动建议用户拆分提交（按逻辑分组分多次 commit），不要一次性提交全部。

## 5. 提交

运行 `git commit -m "<提交信息>"` 完成提交，并展示提交结果。

## 约束

- 只提交用户明确要求提交的变更，不擅自提交无关文件。
- 提交前确认提交信息准确反映实际变更。
