# CLAUDE.md — 项目级 AI 协作规范

> 本文件是 AI 协作的**入口文档**。任何 AI Agent 在本项目工作时，必须先读这份文件。

## 1. 项目身份

- **上游**：[alibaba-damo-academy/FunClip](https://github.com/alibaba-damo-academy/FunClip) 本地 fork
- **目的**：本地化视频自动剪辑工具，ASR 识别 + 字幕清洗 + 时间戳剪辑
- **技术栈**：Python 3.10/3.12 + Gradio 4.x + FunASR + moviepy + ffmpeg
- **私有扩展**：`slirn/` 子模块（6 个 Skill + 课程字幕清洗管线）

## 2. 目录结构

```
FunClip-main/                      ← 本仓库（上游 fork + 私有扩展）
├── funclip/                       ← 上游源码（不要改，要改去上游 PR）
│   ├── launch.py                  ← Gradio 入口（CLI: -m/-l/-p/-s）
│   ├── videoclipper.py            ← VideoClipper 核心类
│   ├── llm/                       ← 4 个 LLM 客户端
│   └── utils/                     ← 工具
├── tests/                         ← 单元测试（覆盖率低，1 文件 4 用例）
├── docs/                          ← 文档 + demo 图
├── font/                          ← 字体（55MB STHeitiMedium.ttc 保留）
├── requirements.txt               ← 依赖
├── start.bat / start.ps1          ← 启动脚本
├── .claude/                       ← AI 协作配置（本文档所在根）
│   ├── skills/                    ← 6 个 Skill junction
│   │   └── INDEX.md               ← Skill 调用索引
│   ├── settings.json              ← 权限白名单 + Hooks
│   └── settings.local.json        ← 本机用户权限（不入库）
├── docs/sop/                      ← 5 阶段 SOP checklist
└── slirn/                         ← 私有工作（git submodule）
    ├── skill/                     ← 6 个 Skill 源
    ├── docs/解析过程/             ← 源码逆向
    ├── prompts/                   ← LLM 提示词
    └── material/                  ← 输入素材
```

`slirn/` 指向 `../slirn-standalone/`（独立仓库）。修改 Skill 去那里提交。

## 3. 启动与测试

| 任务 | 命令 |
|---|---|
| 启动 Gradio | `./.venv/Scripts/python.exe funclip/launch.py` |
| 启动并分享 | `./.venv/Scripts/python.exe funclip/launch.py --share` |
| CLI 识别 | `./.venv/Scripts/python.exe funclip/videoclipper.py --stage 1 --file <video>` |
| 安装依赖 | `./.venv/Scripts/pip.exe install -r requirements.txt` |
| 跑测试 | `./.venv/Scripts/python.exe -m pytest tests/ -v` |
| 格式化 | `ruff format .`（如已装 ruff） |
| 静态检查 | `ruff check .` |

## 4. 工作流（5 阶段 SOP）

任何新功能/修改都必须按 5 阶段走，详见 [docs/sop/](docs/sop/)：

1. **[需求澄清](docs/sop/01-requirements.md)** — 写 `requirements/REQ-<id>.md`，每条有验收标准
2. **[系统设计](docs/sop/02-design.md)** — 写 `design/DESIGN-<id>.md`，关键决策有理由
3. **[实现](docs/sop/03-implementation.md)** — 写代码 + 自测
4. **[评审](docs/sop/04-review.md)** — 用 `code-review` Skill 自检
5. **[验证](docs/sop/05-verification.md)** — 对照验收标准逐条验证

每阶段必须满足该阶段 checklist 才能进下一阶段。

## 5. Skill 调用

完整索引见 [.claude/skills/INDEX.md](.claude/skills/INDEX.md)。快速参考：

| 想做的事 | 调用的 Skill |
|---|---|
| 一键从视频到成品 | `video-subtitle-editing-pipeline` |
| 单独提取字幕 | `video-subtitle-extractor` |
| 单独清洗字幕 | `long-video-subtitle-cleaner` |
| 单独剪视频 | `video-timestamp-cutter` |
| 人工审核辅助 | `course-content-review` / `manual-review-finalizer` |

## 6. 编码与提交规范

### Commit 信息
用 **Conventional Commits**：
```
<type>(<scope>): <subject>

<body>

<footer>
```

`type`: `feat` / `fix` / `chore` / `refactor` / `docs` / `test` / `style` / `perf`
`scope`: 受影响模块，如 `gitignore` / `repo` / `clipper` / `skill-pipeline`

示例：
- `feat(clipper): add batch export for SRT`
- `fix(recog): handle empty audio gracefully`
- `chore(gitignore): exclude generated artifacts`

### 代码风格
- 命名、风格与 funclip/ 现有代码保持一致
- 关键函数写 docstring
- 关键路径加日志或异常提示
- 不引入新依赖（除非已批准）

### 改动范围
- 改上游源码前，**先确认这是 fork 项目，不直接合上游**
- 改 Skill 去 `slirn-standalone` 仓库提交
- 大改动（影响 funclip/）需要写 `design/DESIGN-<id>.md`

## 7. 不要做的事

- ❌ 不要把生成产物（mp4/srt/log）提交（.gitignore 已挡）
- ❌ 不要直接改 `funclip/` 源码去加新功能（提 PR 到上游）
- ❌ 不要在 funclip-main 改 Skill 源码（去 slirn-standalone 改）
- ❌ 不要 force push main（除非走 force-with-lease 流程）
- ❌ 不要跳阶段（每个 SOP 阶段必须满足 checklist）

## 8. 子模块操作

```bash
# 更新子模块到最新
git submodule update --remote slirn

# 在 slirn-standalone 仓库改 Skill
cd ../slirn-standalone
# 改完后 commit + push

# 回到 funclip-main 更新引用
cd ../FunClip-main
git submodule update --remote slirn
git add slirn
git commit -m "chore(submodule): update slirn ref"
```

## 9. 故障排查

| 现象 | 排查 |
|---|---|
| `slirn/` 是空目录 | `git submodule update --init --recursive` |
| FunASR 模型下载失败 | 检查网络，或手动放到 `modelscope/` 缓存 |
| Gradio 启动慢 | 关掉 `analytics_enabled` 或用 `--share` |
| moviepy 剪辑崩溃 | 检查 ffmpeg 是否在 PATH：`ffmpeg -version` |
| 历史里看到大 mp4 | 那是已清理的旧文件，新历史已无（见 `funclip-main-post-cleanup.bundle`） |

## 10. 联系方式 / 重要引用

- 上游文档：见 `README.md`
- Skill 编排详细说明：见各 Skill 的 `SKILL.md`
- 历史备份：`../FunClip-main-backup-20260914/funclip-main-pre-filter.bundle`
