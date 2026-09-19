# REQ-20260920-082 — 精剪·「系统默认 BGM」下拉从参数区移到素材上传区

## 背景

REQ-20260920-078 实现了「系统默认 BGM」一键选择：用户可在精剪视频面板下拉选 5 个内置 lo-fi mp3 之一，自动写入 `fc.materials.audio.path`。

**位置选择错误**：该下拉当前嵌在「🎵 背景音乐」参数块内（`audio_html`，音量/淡入/淡出滑块下方），作为参数区的附属控件。**这与用户的认知模型不符**：

- 用户心智：「系统默认 BGM」是一组**素材**（与上传 mp3 等价的「BGM 来源」），不是参数。
- 当前问题：把它放在参数区让用户以为它是「BGM 配置项」，但实际上选了之后会**直接替换 audio 素材**，与上传 mp3 互斥。
- 用户反馈：「系统提供的背景音乐列表的选择应该放在上传素材的里边，而不是应该放在参数里边」。

## 目标

把 `<select id="slirn-fine-default-bgm">` 从「🎵 背景音乐」参数块（`audio_html`）里**移到素材上传区**，紧贴「🎵 背景音乐」上传卡（`audio` kind）下方，作为该素材卡的快捷选项。

## 影响范围

| 文件 | 改动 | 原因 |
|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | 1. 从 `audio_html` 删除 `.slirn-fine-default-bgm-row` 块；2. 在上传卡循环里（`kind == "audio"`）append 一个 `<div class="slirn-fine-upload-bgm-row">…</div>` | 位置迁移主战场 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | 新增 `.slirn-fine-upload-bgm-row` 样式（或复用现有 `.slirn-fine-default-bgm-row` class） | 新位置的视觉对齐 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | 新增对 `fineDefaultBgmLoad()` 的调用时机（panel 加载完成时；或保留现有 change 委托） | 让下拉首次出现就能加载 bgm 列表（**REQ-078 遗留 bug：fineDefaultBgmLoad 函数存在但从未被调用**） |
| [tests/test_workbench.py](tests/test_workbench.py) | 新增 3 个测试（验证：1）BGM 下拉不再在 audio block；2）BGM 下拉在 audio upload card 内；3）音频参数区不再含 BGM select） | 回归保护 |
| [docs/REQM/REQ-20260920-082-move-bgm-selector.md](docs/REQM/REQ-20260920-082-move-bgm-selector.md) | 本文档 | 5 阶段 SOP 第 1 阶段产物 |
| [docs/design/DESIGN-20260920-082-move-bgm-selector.md](docs/design/DESIGN-20260920-082-move-bgm-selector.md) | 设计文档（ADR-082-1 / 082-2） | 第 2 阶段产物 |
| [docs/verification/VERIFICATION-20260920-082-move-bgm-selector.md](docs/verification/VERIFICATION-20260920-082-move-bgm-selector.md) | 验证文档 | 第 5 阶段产物 |

## 验收标准（10 条）

- **AC-1**：`grep` `slirn_home/app.py` 中 `slirn-fine-default-bgm-row` 不再出现在 `audio_html`（参数块）里
- **AC-2**：`<select id="slirn-fine-default-bgm">` 出现在素材上传循环中 `data-kind="audio"` 的卡片内（紧跟「🎵 背景音乐」上传卡的 status 行后）
- **AC-3**：`grep -c "slirn-fine-default-bgm"` 在 app.py 中恰好 1 处（迁移后不再重复）
- **AC-4**：`audio_html` 中不再包含「系统默认 BGM」字样（提示文案也可下移到上传区或保留作为参数区简略说明）
- **AC-5**：音频参数区仍然存在音量/淡入/淡出 4 个滑块（`#slirn-fine-audio-volume` / `_fade_in` / `_fade_out` + checkbox `data-key="audio"`）
- **AC-6**：`<select id="slirn-fine-default-bgm">` 的 `data-task-id` 属性正确传递（router.js 的 change 委托读 `e.target.getAttribute('data-task-id')` 必须拿到当前 tid）
- **AC-7**：router.js 在 fine-cut 面板加载完成后调用 `fineDefaultBgmLoad()`（**修复 REQ-078 遗留 bug：函数存在但从未被调用**）
- **AC-8**：现有 BGM select 行为不变：选择后调 `/slirn/api/select_default_bgm`、自动勾选 audio checkbox、调 fineSaveAll 自动保存
- **AC-9**：现有所有测试（567）仍然 pass + 新增 3 个测试 pass（567 → 570）
- **AC-10**：浏览器实测（手动）：打开精剪面板 → 上传区展开 → 「🎵 背景音乐」卡片下方出现「📦 系统默认 BGM」下拉，下拉框列出 5 个 lo-fi mp3；选中后 audio 上传卡状态变更为对应 BGM 文件名

## 不做的事

- ❌ 不改变 `select_default_bgm` / `list_default_bgms` 后端 API 的接口契约
- ❌ 不改 `audio_html`（音量/淡入/淡出滑块）的位置和顺序
- ❌ 不改 BGM 选择后的行为（自动启用 audio、自动保存、缓存 `_slirnFineAudioPath`）
- ❌ 不在参数区再保留任何 BGM 下拉的镜像/链接
- ❌ 不修改 CSS class 名 `.slirn-fine-default-bgm-row` / `.slirn-fine-default-bgm-select`（避免 router.js / CSS 同步改动）

## 命名约定

| 项 | 值 |
|---|---|
| REQ 文档 | `REQ-20260920-082-move-bgm-selector.md` |
| DESIGN 文档 | `DESIGN-20260920-082-move-bgm-selector.md` |
| VERIFICATION 文档 | `VERIFICATION-20260920-082-move-bgm-selector.md` |
| Container class | 复用现有 `.slirn-fine-default-bgm-row` / `.slirn-fine-default-bgm-select`（不改名） |
| Select id | 复用现有 `slirn-fine-default-bgm`（不改） |

## Why

用户的认知模型：BGM 是一类「素材」（音频文件），与上传 mp3 是互斥的两种来源；下拉选 BGM = 从内置库「取」一份音频素材，等价于「上传一份内置素材」。把它放在参数区会让用户误以为它是 audio 配置项（音量、淡入等），与实际行为不符。

## How to apply

未来类似「X 是一份素材，不是参数」的需求复用本模式：
- 上传卡是「来源选项」（上传 / 自动获取 / 内置列表）的容器
- 参数块只放「素材被使用时的配置项」（位置 / 缩放 / 音量等）
- 二者不要交叉
