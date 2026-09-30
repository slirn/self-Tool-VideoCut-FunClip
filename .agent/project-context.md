# Project Context — FunClip-main

> 本文件只记录本项目事实和约束，不复制公共流程规范。完整协作流程见 `.agent/standards/`，项目适配例外见 `.agent/standards-profile.md`。

## 项目概述

- **名称**：FunClip-main（本地 fork of [alibaba-damo-academy/FunClip](https://github.com/alibaba-damo-academy/FunClip)）
- **目标**：本地化视频自动剪辑工具。链路 = ASR 识别（FunASR） → 字幕清洗（LLM / 规则） → 时间戳剪辑（moviepy + ffmpeg） → 字幕烧录与导出。
- **主要用户**：内容创作者、课程剪辑团队、内部使用；视频仅本地处理，不上云。

## 技术栈

- **语言/运行时**：Python 3.10 / 3.12（pyenv 管理；`.venv/` 已 fixed 到本地）
- **ASR**：FunASR（Paraformer / SeACo-Paraformer 等模型）
- **LLM**：可插拔的 4 个 LLM 客户端（`funclip/llm/`），支持 GPT / Qwen / DeepSeek / GLM 等
- **Web 框架**：Gradio 4.x
- **视频处理**：moviepy + ffmpeg（PATH 必需）
- **前端**：Gradio Blocks（默认） + 自定义 `slirn_home/static/`（home.css / router.js / pipeline.js）
- **状态存储**：`slirn_home/` 用本地 JSON（`data/users.json` + 任务目录）做用户、cookie session、任务状态；无关系型数据库
- **依赖管理**：`requirements.txt`（最小集）+ `pyproject.toml`（含 `[project.optional-dependencies].dev` 提供 pytest / pytest-cov / ruff）

## 常用命令

```text
# 安装依赖（必须用项目本地 .venv）
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/pip.exe install -e .[dev]

# 启动 Gradio（主入口）
./.venv/Scripts/python.exe funclip/launch.py
./.venv/Scripts/python.exe funclip/launch.py --share          # 公网分享模式

# CLI 识别（不启 Gradio）
./.venv/Scripts/python.exe funclip/videoclipper.py --stage 1 --file <video>

# 测试
./.venv/Scripts/python.exe -m pytest tests/ -v
./.venv/Scripts/python.exe -m pytest tests/test_<name>.py -v

# Lint
./.venv/Scripts/ruff.exe check .
./.venv/Scripts/ruff.exe format .

# Skill 调用（详见 .claude/skills/INDEX.md）
./.venv/Scripts/python.exe slirn/skill/<skill-name>/scripts/<script>.py
```

## 架构与边界

- **入口模块**：
  - `funclip/launch.py` — Gradio 主入口（CLI: `-m` / `-l` / `-p` / `-s`）
  - `slirn_home/app.py`（≈537 KB，8000+ 行 Gradio Blocks）— 任务流入口
  - `slirn_home/create_task.py` — 任务创建服务

- **核心模块**：
  - `funclip/videoclipper.py` — 上游 `VideoClipper` 核心类（**不可直接修改**）
  - `funclip/llm/` — 4 个 LLM 客户端封装
  - `funclip/utils/` — 工具函数
  - `slirn_home/` — 本地扩展层（28 个 `.py` 文件：auth / create_task / hotword / pipeline / compose / fine / revision / optimize / cutlist / execution_history / asr_service / cut_speaker / rev_speaker / llm_config / fine_profiles / bootstrap_admin / paths / task_list 等）
  - `slirn/skill/` — 通过 submodule 引用的私有 Skill 源（6 个 Skill：video-subtitle-extractor / long-video-subtitle-cleaner / course-content-review / manual-review-finalizer / video-timestamp-cutter / video-subtitle-editing-pipeline）
  - `slirn/tasklib/` — 跨 Skill 共享库（models / schema / time_utils / video / manager / hotword_lib / exceptions）

- **依赖方向**：
  ```
  funclip/  ← 上游代码（不可改）
       ↑
  slirn_home/  ← 本地扩展（自由改）
       │
       ↓ 依赖
  slirn/  ← git submodule（修改去 ../slirn-standalone/）
       │
       ↓ 提供
  slirn/skill/  ← Skill 源（junction 到 .claude/skills/）
  ```

- **禁止修改或需谨慎修改的区域**：
  - `funclip/**` — 上游代码，要改去上游 PR，不在本仓库直接修改
  - `slirn/**` — git submodule；要改去 `../slirn-standalone/` 仓库单独 commit 后 bump ref
  - `slirn/skill/**` — 修改 Skill 必须经 `slirn-standalone` 仓库

## 规范入口与适配

- **唯一 Agent 入口**：`AGENTS.md`
- **公共规范基线**：`.agent/standards/`（32 个文件，v0.21.0 锁定，installMode=committed）
- **项目事实**：本文件
- **项目适配与例外**：`.agent/standards-profile.md`
- **工具适配层**：
  - `CLAUDE.md` — Claude Code 适配层（仅保留 Claude 特有的权限 / Hooks / Skill 索引）
  - `.claude/skills/INDEX.md` — Skill 索引（指向 `slirn/skill/`）
  - `.claude/settings.json` + `.claude/settings.local.json` — Claude Code 工具权限白名单与 Hook

## 项目特有规则

- **提交信息**：Conventional Commits（CLAUDE.md §6）。`type` ∈ {feat, fix, chore, refactor, docs, test, style, perf, revert}。
- **5 阶段 SOP**：`docs/sop/01..05-*.md`（需求 → 设计 → 实现 → 评审 → 验证）。与 `.agent/standards/delivery.md` 互为 `adopt` 关系。
- **功能进度与使用说明**：新增模块按 `delivery.md` 维护功能点清单和图文使用说明书；本项目建议落点为 `docs/manual/<module>-checklist.md` 与 `docs/manual/<module>.md`，两者章节双向链接。
- **复用优先**：实现前先检查现有已验证模式；当前可优先复用 `slirn_home/` 的阶段编排、异步 job、原子 JSON 写入、执行历史和精剪 FFmpeg 渲染模式，不复制相似实现。
- **文件编码**：新建或修改的源码、文档、配置和脚本统一 UTF-8（推荐无 BOM）；验收发现中文乱码按编码缺陷处理。
- **不稳定测试**：发现 flaky 测试先重复取证并登记隔离，不直接修改产品代码掩盖偶发失败；隔离测试不冒充合并门禁通过项。
- **REQ / DESIGN / VERIFICATION 命名约定**：`*-YYYYMMDD-NNN[-<suffix>]-<slug>.md`
  - REQ 存于 `docs/REQM/`（**注意大写后缀**，与 SOP §1 的 `requirements/` 路径约定不一致；历史遗留）
  - DESIGN 存于 `docs/design/`
  - VERIFICATION 存于 `docs/verification/`
- **决策记录（ADR）**：复用 `docs/design/DESIGN-*.md`，不另开 ADR 目录。关键决策段落含"备选方案 + 拒绝理由 + 后果"。
- **不复用 `git reset --hard`**：项目通过 E2E 蓝图完整重做（见 `memory/funclip-recovery-pattern.md`）。
- **Submodule 更新**：`git submodule update --remote slirn`，commit 信息 `chore(submodule): update slirn ref`。

## 治理记录

- **质量门禁和 CI 入口**：`.claude/settings.json`（无 `.github/workflows/`，CI 是 Claude Code Hooks）
  - `PostToolUse` matcher `Write|Edit` → `ruff check --quiet` 在 `.py` 文件
  - `Stop` hook → `pytest tests/ --tb=line -q`
- **决策记录目录**：`docs/design/`（复用，不另开）
- **技术债记录位置**：`docs/REQM/` + memory/ 中 `funclip-*.md` 条目
- **事故与复盘记录位置**：`C:\Users\Administrator\.claude\projects\d--Slirn-WorkSpaces-WaytoAGI-ALI-FunClip-main\memory\funclip-recovery-pattern.md`
- **发布版本与来源提交记录**：Git tag（未启用 SemVer）+ Conventional Commits 历史；`funclip-main-post-cleanup.bundle` 为历史备份

## 验收自动化

- **统一验收平台**：暂未接入（`Agent-Acceptance-Platform v0.2.0` 可选）
- **手动验证流程**：`docs/sop/05-verification.md` + `pytest tests/`
- **当前测试规模**：27 个 `test_*.py` + 1 个 `conftest.py`；REQM pending-task-execution-log 显示 REQ-094 后达 633 passed / 0 failed
- **覆盖率**：`pytest-cov>=4.0` 在 `[project.optional-dependencies].dev`，**未配置** `.coveragerc` / `pyproject.toml [tool.coverage.*]`
- **Linter**：ruff（`pyproject.toml` 配置；`extend-exclude` 排除 `slirn`、`funclip`、`.venv`）

## 日志与诊断

- **统一日志模块/API**：**未引入**。当前依赖：
  - `funclip/` 上游模块的内置 logging
  - `slirn_home/` 各服务的 Python logging + Gradio Queue 日志
  - `docs/REQM/REQ-*.md` 和 `docs/verification/VERIFICATION-*.md` 作为事件追踪
- **日志开关**：默认 INFO；`Stop` Hook 跑测试时 `--tb=line -q`（不阻断）
- **流程追踪**：`execution_history.py`（`slirn_home/`，记录精剪 / 合成 / 任务执行历史）
- **数据库表**：无（用本地 JSON 文件 + `tasks/` 目录）
- **敏感字段脱敏规则**：用户密码使用 `pbkdf2_sha256$200000$<salt_hex>$<hash_hex>`；session cookie HttpOnly + SameSite=Lax
- **诊断页面**：Gradio 默认 UI（`/`、`/auth/login`、`/auth/me`、`/auth/bootstrap`）+ `bootstrap_admin.py` CLI 工具
- **日志写入失败时降级**：N/A（无持久化日志）

## Agent 与数据治理

- **Agent 身份 / 工具白名单**：`.claude/settings.json`（checked-in）+ `.claude/settings.local.json`（本机）
  - Allow: `git:*`、`python:*`、`.venv/Scripts/python.exe:*`、`ruff:*`、`pytest:*`、`ffmpeg:*`、`ffprobe:*`、`ls/dir/find/grep:*`、`Read(./**)`、`Glob(*)`、`Grep(*)`
  - Deny: `rm -rf:*`、`git push --force:*`、`git filter-repo:*`、`git filter-branch:*`
- **模型上下文 / 会话 / 长期记忆**：Claude Code 默认；用户级记忆 `C:\Users\Administrator\.claude\projects\...`（28 个条目）
- **高风险操作审批**：`git push` 需要用户授权（settings.json 显式 deny --force）；submodule bump 在 SOP 范围内
- **停止开关**：Claude Code `Stop` Hook 跑测试（failures 不阻断退出，但用户可见 pytest 输出）
- **数据分类**：本地视频文件 + 用户元数据（用户名、密码 hash、cookie session）。**无 PII 上传**。
- **数据保留 / 删除**：`tasks/`、`/slirn_home/tasks/`、`/work/`、`tmp_test_vid/`、`hotwords/` 均为运行时数据，gitignore 已排除；用户主动删除任务时同步清除
- **测试数据来源**：本地视频片段（gitignored）+ slirn/material/ 中保留的 1 个 sample SRT
- **审计 / 事件响应**：Conventional Commits + REQ/VERIFICATION 文档作为审计证据

## 验证要求

- **新功能 / Bug 修复**：必须按 `docs/sop/01..05` 五阶段走
- **代码评审**：使用 `/code-review <effort>` skill，effort 级别（low/medium/high/max）按改动行数与影响面选择
- **回归测试**：改动后跑 `pytest tests/ -v`（Stop Hook 自动）
- **跨模块 / 难回滚变更**：必须写 `docs/design/DESIGN-*.md` 并在 commit 中引用
- **多任务编排**：涉及并行写任务时按 `orchestration.md` 的触达文件清单划分泳道；共享文件修改统一进入集成泳道
- **长任务**：预计数小时、需要恢复或无人值守时按 `long-running.md` 记录预算、检查点、监督器能力和恢复条件

## 可靠性、架构与前端

- **SLO / 错误预算**：**不适用**（桌面工具，单进程 Gradio）
- **RTO / RPO / 容灾**：**不适用**
- **容量模型 / 压力测试**：**未建立**（视频处理时长由 `moviepy` + `ffmpeg` 决定，无量化指标）
- **架构规则文件 / ADR 目录**：`docs/design/DESIGN-*.md`（37 个）作为 ADR
- **模块依赖检查**：`funclip/` 不可改 + `slirn/` 走 submodule 强约束（无自动化 lint 校验）
- **前端支持范围 / 设计令牌 / 组件库**：`slirn_home/static/`（CSS + JS 手写），**无** Storybook / Figma / 设计令牌系统
- **可访问性 / 国际化 / 性能预算**：**未建立**（`frontend-quality.md` legacy-gap）
- **前端 E2E / 性能测试命令**：手写 Playwright/CDP 脚本在 `tests/_check_*.py`（gitignored）
- **试点反馈目录**：`/agent/feedback/`（已创建，由 `sync-standards.ps1` v0.14.0 安装）
