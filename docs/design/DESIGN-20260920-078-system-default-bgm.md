# DESIGN-20260920-078 精剪视频·系统默认 BGM 备选列表

## Context

[REQ-20260920-078-system-default-bgm.md](REQ-20260920-078-system-default-bgm.md) 描述了用户诉求：5 个 lo-fi mp3 作为系统默认 BGM，下拉一键选择。

本文档记录实现决策。

## 关键决策

| 维度 | 决策 | 理由 |
|---|---|---|
| BGM 来源 | **绝对路径硬编码** `D:\tmp\tttttt\`（不进 git） | 用户给的明确路径；不动态扫描避免目录消失导致运行期挂掉 |
| 5 个 ID 命名 | 用 ID 而非中文名 → 文件名 slug | URL 友好、纯 ASCII、稳定 |
| 文件存在性 | 启动时校验 + 端点每次返 `available` 字段 | UI 灰显而非失败；运行期容错 |
| 复制策略 | **复制到 `tasks/{tid}/materials/audio/<id>.mp3`** | 与上传路径格式一致；任务独立；删除任务时一起清 |
| 选 BGM 后自动行为 | 自动勾选「启用」+ 触发 `fineSaveAll(false, false)` 自动保存 | 用户最少点击；不强制保存为模板 |
| 「不选（清空）」 | 当前不动 fc（保留已上传 BGM），仅 UI 取消选中 | 与「选 BGM」语义对称需要「反选」能力，但不删文件 |
| 与上传 BGM 优先级 | **用户上传优先**：下拉只显示当前 fc.materials.audio.path 文件名对应的 ID（如果有） | 避免误覆盖用户上传的文件 |
| 复制工具 | `shutil.copy2`（保留 mtime） | 让用户能看到「这是从 D:\tmp 复制来的」 |
| 启用标志 | `fc["audio"]["enabled"] = True` + checkbox 同步 | 双重保险：服务端持久化 + UI 同步 |

## 关键文件改动

| 文件 | 行号 | 改动 | 估算行数 |
|---|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | 1580 附近 | `_DEFAULT_BGMS_DIR` + `_DEFAULT_BGMS` + `_get_default_bgms()` | +25 |
| [slirn_home/app.py](slirn_home/app.py) | 5198 附近 | 2 个新端点 `/list_default_bgms` + `/select_default_bgm` | +35 |
| [slirn_home/app.py](slirn_home/app.py) | 2835 之前 | `default_bgm_html` 注入 audio 块 | +8 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | 5400 后 | `fineDefaultBgmLoad` / `fineDefaultBgmSelect` + change 委托 | +50 |
| [slirn_home/static/home.css](slirn_home/static/home.css) | 末尾 | `.slirn-fine-default-bgm-row` / `-select` | +15 |
| [tests/test_workbench.py](tests/test_workbench.py) | 末尾 | 4 个新测试 | +100 |
| [docs/REQM/](docs/REQM/) | 新建 | REQ 文档 | +200 |
| [docs/design/](docs/design/) | 新建 | DESIGN 文档 | +100 |

净代码量约 **+130 行**（后端 +75 / 前端 +65）。

## API 设计

### `POST /slirn/api/list_default_bgms`

请求：`{}`
响应：
```json
{
  "ok": true,
  "bgms": [
    {"id": "lofi_beat_1", "name": "Pretty John — Lo-Fi Beat",
     "filename": "prettyjohn1-lo-fi-beat-580021.mp3",
     "available": true, "size_bytes": 1728679},
    ...
  ]
}
```

### `POST /slirn/api/select_default_bgm`

请求：`{"task_id": "task_xxx", "bgm_id": "lofi_beat_1"}`
响应：
```json
{
  "ok": true,
  "audio_url": "/slirn/api/video/task_xxx?src=mat&kind=audio&t=...",
  "name": "Pretty John — Lo-Fi Beat",
  "toast": "✅ 已选 BGM: Pretty John — Lo-Fi Beat"
}
```

## 数据流

```
用户点「📦 系统默认 BGM」下拉 → change 事件
  ↓
fineDefaultBgmSelect(bgmId, tid)
  ↓
POST /slirn/api/select_default_bgm
  ↓
后端：shutil.copy2(src, dst) → 写 fc.materials.audio.path + audio.enabled=True
  ↓
前端：toast → 自动勾选「启用」 → 触发 fineSaveAll（后台自动保存）
  ↓
精剪面板「启用背景音乐」checkbox 显示已勾选 + 音频预览 URL 已更新
```

## UI 设计

```
┌─ 🎵 背景音乐（与原声混合播放，保留说话人语音）────────────────┐
│  □ 启用背景音乐                                               │
│  音量（0–1，0.4 = 不压人声）        [━━━●━━━] 0.40            │
│  淡入（0–5 秒）                     [━━━━●━━] 0.0             │
│  淡出（0–5 秒）                     [━━━━●━━] 0.0             │
│  ─────────────────────────────────────────────────────────── │
│  📦 系统默认 BGM              [🎵 Pretty John — Lo-Fi Beat ▾]│
│  上传 mp3/wav/m4a 文件 → 原说话人语音 + BGM 同时播放…        │
└──────────────────────────────────────────────────────────────┘
```

下拉选项：
```
— 不选（清空）—
🎵 Pretty John — Lo-Fi Beat（1.6 MB）
🎵 Sonican — Sentimental Jazzy Love（3.1 MB）
🎵 The Mountain — Lo-Fi Beat（5.2 MB）
🎵 Zephira Music — Lo-Fi（4.4 MB）
🎵 Zephira Music — Relaxing Lo-Fi（5.5 MB）
```

## 边界与错误处理

| 场景 | 行为 |
|---|---|
| 源文件不存在 | 启动告警 + 前端灰显 + 选时返 error「文件不存在」 |
| bgm_id 错误 | 后端返 error「未知的 bgm_id: xxx」 |
| 任务不存在 | 后端返 error「任务不存在: xxx」 |
| 复制失败（权限/磁盘满） | 后端返 error + 不写 fc（保持原状） |
| 用户选「不选（清空）」 | 当前 UI 仅取消选中，不动 fc，不删已上传文件 |

## 复用现有基础设施

- `_FINE_AUDIO_DEFAULTS` 字段名（enabled/volume/fade_in/fade_out）保持不变
- `materials.audio = {"path": "..."}` 字段格式保持不变
- `_resolve_mat_abs` / `_save_fine_compose` 现有 helper 复用
- `_get_default_bgms()` 在端点启动时被调用，可加进启动日志

## 不做的事（同 REQ）

- ❌ 不做音频在线预览
- ❌ 不做 BGM 标签/分类
- ❌ 不持久化「当前用的哪个默认 BGM」（fc.materials.audio.path 已存）
- ❌ 不引入音频处理库（ffmpeg 自带 amix 足够）
