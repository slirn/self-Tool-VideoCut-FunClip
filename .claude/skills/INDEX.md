# Skill Index

> 本文件仅做**文档索引**。6 个 skill 的真实源在 `slirn/skill/`（git submodule → `../slirn-standalone/`）。

## ⚠️ 重要：Windows Junction 与 git 的冲突

本目录 **不**存放 skill 真实文件。原因：

- 6 个 skill 的内容已经在 `slirn/skill/`（通过 submodule 引用）
- Windows 的 `mklink /J` 创建的 junction 会被 git 当成**真实目录**并把所有内容加入仓库
- 这会导致 skill 内容在 funclip-main 和 slirn-standalone 两处重复，违反单一源原则

如果想让 Claude Code 自动发现这些 skill（出现在可用 skill 列表中），请运行 setup 脚本创建 junction：

```bash
# Windows PowerShell（在 funclip-main 根目录）
./.claude/skills/setup-junctions.ps1

# Linux / macOS（用 symlink）
./.claude/skills/setup-junctions.sh
```

setup 脚本会：
1. 在 `.claude/skills/` 下为每个 skill 创建一个 junction/symlink
2. junction 本身**不会被提交**（在 `.gitignore` 中）
3. 你本地工作区里 Claude 就能"看到"这些 skill

如果不想用 junction，可以直接读 `slirn/skill/<name>/SKILL.md` 来了解 skill 用途，调用时直接用 `python slirn/skill/<name>/scripts/*.py`。

## Skill 清单

| Skill | 用途 | 触发场景 | 输入 | 输出 |
|---|---|---|---|---|
| `video-subtitle-extractor` | 视频 → SRT 字幕（ASR） | "提取视频字幕"、"音频转字幕" | 视频文件 | `<name>.raw.srt` |
| `long-video-subtitle-cleaner` | SRT → 清洗后正文 | 课程长视频去口水词/互动 | `<name>.raw.srt` | `.intermediate.txt` `.cleaned.txt` `.removed.txt` |
| `course-content-review` | 自动标 + 人工删 | cleaned.txt 最后一轮人工审核 | `<name>.cleaned.txt` | `.marked.txt` `.final.txt` `.manual-removed.txt` |
| `manual-review-finalizer` | 校验 + 连续字幕 | 校验异常词、生成剪映字幕 | `<name>.manual-final.txt` | `.manual-validation.txt` `.manual-final.continuous.srt` |
| `video-timestamp-cutter` | 字幕时间戳 → 视频片段 | 按时间戳剪视频 | 视频 + SRT/JSON/文本 | `<name>_cut.mp4` |
| `video-subtitle-editing-pipeline` | **编排器** — 一键跑完整链路 | 从原始视频一键得到剪辑后视频+字幕 | 视频文件 | `_cut.mp4` + `.continuous.srt` + 中间产物 |

## 调用顺序（Pipeline）

```
视频 ──→ [video-subtitle-extractor] ──→ .raw.srt
                                            │
                                            ↓
                          [long-video-subtitle-cleaner]
                                            │
                          ┌─────────────────┴─────────────────┐
                          ↓                                   ↓
                   .intermediate.txt                  .cleaned.txt
                          │                                   │
                          ↓                                   │
                   [course-content-review]                   │
                          │                                   │
                          ↓                                   │
                   .marked.txt (人工审核)                    │
                          │                                   │
                          ↓                                   │
                   [manual-review-finalizer]                 │
                          │                                   │
                          ↓                                   ↓
                  .manual-final.continuous.srt    [video-timestamp-cutter]
                          │                                   │
                          ↓                                   ↓
                      剪映字幕                            _cut.mp4
```

## 何时用哪个 Skill

| 用户说法 | 应调用的 Skill |
|---|---|
| "从这个视频提取字幕" | `video-subtitle-extractor` |
| "清洗这个 SRT 字幕" | `long-video-subtitle-cleaner` |
| "对这份 cleaned.txt 做人工审核" | `course-content-review` |
| "把人工审核结果生成最终字幕" | `manual-review-finalizer` |
| "按时间戳剪视频" | `video-timestamp-cutter` |
| "一键从视频到剪辑后成品" | `video-subtitle-editing-pipeline` |

## 文件位置

- **Skill 源**：`slirn/skill/<name>/`（submodule 内的目录）
- **Submodule 源仓库**：`../slirn-standalone/`（独立 git 仓库）
- **本 INDEX**：`.claude/skills/INDEX.md`（唯一在 funclip-main 仓库的文件）
- **修改 skill**：去 `slirn-standalone` 仓库改，提交后再更新本仓库的 submodule 引用
