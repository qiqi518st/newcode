---
name: github-upload
description: "Push NewCode project to GitHub with checks, .gitignore, commit, tag, and push"
---

# GitHub 上传流程

将当前 NewCode 项目代码推送到 GitHub 仓库 `https://github.com/qiqi518st/newcode`。

## 前置条件检查

1. **git 是否安装**：`git --version`
2. **用户名和邮箱是否配置**：
   - `git config user.name`
   - `git config user.email`
3. **当前目录是否是 git 仓库**：`git rev-parse --git-dir`
4. **GitHub 远程是否已配置**：`git remote -v`
5. **提交邮箱合法性检查**：本项目的提交人**硬编码**为 `qiqi518st <qiqi518st@gmail.com>`（见步骤 4），不受本地 git 配置影响。但本地配置仍应正确，以便 `git log` 等工具显示一致。校验本地配置：

```bash
git config user.email
```

   - 期望是 `qiqi518st@gmail.com`（GitHub 已验证邮箱，Primary + Verified）或账号专属匿名邮箱 `177993992+qiqi518st@users.noreply.github.com`
   - 如果本地邮箱是别的（如 `qiqi@users.noreply.github.com`），提交人将无法关联到 GitHub 账号，提示用户修正

如果用户名或邮箱未配置，提示用户先执行：
```bash
git config --global user.name "qiqi518st"
git config --global user.email "qiqi518st@gmail.com"
```

## 执行步骤

### 步骤 1：初始化仓库（如需要）

如果当前目录不是 git 仓库：
```bash
git init
git branch -m master
```

### 步骤 2：创建/更新 .gitignore

确保 `.gitignore` 存在且包含以下内容（Python 项目标准）：

```
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# 虚拟环境
venv/
env/
ENV/
.venv/

# 测试
.pytest_cache/
.coverage
htmlcov/

# IDE
.vscode/
.idea/
*.swp
*.swo

# 操作系统
.DS_Store
Thumbs.db

# NewCode 敏感配置（含 API key）
.newcode.yaml
```

> ⚠️ **重要**：`.newcode.yaml` 必须加入 `.gitignore`，因为它包含真实 API key。
> 如果 `.newcode.yaml` 已经被提交过，需要执行 `git rm --cached .newcode.yaml` 将其从版本控制中移除但保留本地文件。

### 步骤 3：检查敏感文件

提交前确认以下文件**不在**暂存区：
- `.newcode.yaml`
- `*.key`
- `.env`

如果发现有敏感文件被暂存，立即取消暂存：
```bash
git reset HEAD <文件>
```

### 步骤 4：暂存并提交

```bash
git add -A
git status   # 让用户确认暂存内容
git commit --author="qiqi518st <qiqi518st@gmail.com>" -m "<提交信息>"
```

> ⚠️ **提交人硬编码**：`--author` 固定为 `qiqi518st <qiqi518st@gmail.com>`，这是 GitHub 已验证邮箱（Primary + Verified），确保提交能正确关联到账号 `qiqi518st`。**不要省略 `--author`**，即使本地 `git config` 有误也能保证提交人正确。

默认提交信息格式：`chXX: 简短描述`，其中 XX 是当前章节号（从版本号或用户输入推断）。
如果用户提供了自定义提交信息，优先使用用户的。

### 步骤 5：关联远程仓库（如需要）

如果远程未配置：
```bash
git remote add origin https://github.com/qiqi518st/newcode.git
```

如果远程已配置但地址不对，更新：
```bash
git remote set-url origin https://github.com/qiqi518st/newcode.git
```

### ⏱️ 网络超时规则（步骤 6-8 所有网络操作强制，最高优先级）

> **所有网络命令（`git pull` / `git push` / `git fetch` / `git ls-remote`）必须带 60 秒超时**，用 `timeout 60` 包裹。
> **一旦等待超过 60s → 立即判定为网络失败**：直接报错并分析可能原因，**绝不无限等待 / 自动循环重试**（用户明确要求不干等）。

```bash
timeout 60 git pull origin master --rebase
timeout 60 git push origin master
timeout 60 git push origin <tag-name>
```

超时报错后**立即分析并报告可能原因**，逐个排查：
1. **网络不通** — `timeout 10 curl -sI https://github.com` 也超时 → 本机到 GitHub 断连
2. **代理/VPN/防火墙** — WSL 或公司网络需代理；查 `git config --get http.proxy` 与环境变量 `HTTP(S)_PROXY`
3. **DNS 解析** — 能 curl 通 IP 但域名不通 → DNS 问题
4. **GitHub 服务端/限流** — 偶发；可稍后手动重试，或提示用户在自己网络环境执行
5. **认证问题（区别于网络）** — 若**快速**返回 `Authentication failed`/`403` 是凭据问题，不是超时，走认证排查

**报告后停止，交给用户决定**（重试 / 换网络 / 用户手动推），不得自行反复重试。

### 步骤 6：拉取合并（避免冲突）

```bash
timeout 60 git pull origin master --rebase
```

如有冲突，停下来让用户解决，不要自动处理。

### 步骤 7：推送代码

```bash
timeout 60 git push origin master
```

### 步骤 8：打 tag（如用户要求）

如果用户提供了 tag 名称（如 `v0.3.0` 或 `ch03`）：

```bash
git tag <tag-name>
timeout 60 git push origin <tag-name>
```

如果没有提供 tag 名称但项目版本号是 `0.X.0`，默认打 tag `v0.X.0`。

## 注意事项

1. **绝不要把 `.newcode.yaml` 提交到 GitHub**。每次推送前检查 `git status`，确认它不在暂存区。
2. **冲突时停下来问用户**。不要自动 `git merge` 或覆盖。
3. **先 pull 再 push**。避免覆盖远程的更新。
4. **提交信息要描述清楚当前做了什么**。例如 `ch03: 实现工具系统（6 个核心工具 + Agent 单轮闭环）`。
5. **tag 命名**：版本号用 `v0.X.0`，章节标记用 `chXX`。
6. **提交人必须硬编码为 `qiqi518st <qiqi518st@gmail.com>`**（步骤 4 的 `--author`），不可省略或改用本地配置，否则提交可能无法关联到 GitHub 账号。
7. **网络操作 60s 超时即报错**：所有 pull/push/fetch/ls-remote 用 `timeout 60` 包裹；超时立即报错 + 分析原因（见「⏱️ 网络超时规则」），不得无限等待或自动重试。

## 快捷调用

用户可以直接输入：
- `/github-upload` — 按默认流程推送
- `/github-upload ch03` — 推送并打 tag `ch03`
- `/github-upload v0.3.0` — 推送并打 tag `v0.3.0`
- `/github-upload "自定义提交信息"` — 使用自定义提交信息