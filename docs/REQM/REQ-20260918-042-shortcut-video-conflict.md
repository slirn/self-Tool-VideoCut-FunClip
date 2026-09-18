# REQ-20260918-042 — 快捷键 · 视频播放键冲突提醒

## 背景与目标

字幕修订 / 切分修剪支持自定义快捷键，但用户配键时常与浏览器/视频播放器内建快捷键冲突。本需求在配键时显式提示视频键对照表，并在录制过程中如选了视频键则 toast 警告。

## 视频键对照表（21 项）

| 键 | 含义 |
|---|---|
| Space / k | 播放/暂停 |
| ← | 后退 5 秒 |
| → | 前进 5 秒 |
| j | 后退 10 秒 |
| l | 前进 10 秒 |
| ↑ | 音量 + |
| ↓ | 音量 - |
| m | 静音切换 |
| f | 全屏切换 |
| c | 字幕开关 |
| 0-9 | 跳到 0% – 90% |
| Home | 回到开头 |
| End | 跳片尾 |

## 验收

| # | 标准 | 状态 |
|---|---|---|
| 1 | 模态弹窗渲染视频键对照表（≥ 20 键） | ✅ |
| 2 | 对照表每个键位带含义说明（如「J=后退 10 秒」） | ✅ |
| 3 | 默认键位与视频键重叠时，行动作行带 ⚠ 视频键徽章 | ✅ |
| 4 | 录制时按视频键 → toast 警告（含键名 + 视频键义 + 将被绑定的动作） | ✅ |
| 5 | 对照表对已被绑定的视频键标「已绑」 | ✅ |

## 涉及文件

- `slirn_home/app.py` — 模态 HTML（新增 `<div class="slirn-revkeys-vlist"></div>`）
- `slirn_home/static/router.js` — `REV_VIDEO_KEYS` 常量、`revKeysRenderRows` / `revKeysRenderVideo` 函数、录制冲突 toast
- `slirn_home/static/home.css` — `.slirn-revkeys-vwarn` / `.slirn-revkeys-vtitle` / `.slirn-revkeys-vsub` / `.slirn-revkeys-vgrid` / `.slirn-revkeys-vitem` / `.slirn-revkeys-vnote`

## E2E

`work/REQ-20260918-040/_e2e_040_046.py` 042 段：6/6 通过。
