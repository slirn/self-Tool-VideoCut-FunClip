# REQ-20260919-066 精剪视频·预览开始时间改为 HH:MM:SS 输入

## 背景

REQ-20260919-064 引入了「预览开始时间」输入框，用 `<input type=number>` 接收秒数（如「120」= 第 2 分钟整）。

用户反馈：「秒数不直观，特别是 30 分钟以上的视频要数到 1800，写起来容易错」。希望改为 **时:分:秒** 三段输入（HH:MM:SS），默认 00:00:00，前端在发送前拼成总秒数给后端。

## 验收标准

| ID | 验收项 |
|---|---|
| AC-1 | HTML 含 3 个 number input：id=slirn-fine-preview-start-h / -m / -s，分别表示时/分/秒 |
| AC-2 | 默认值全 0（00:00:00 = 从头） |
| AC-3 | 分钟/秒 max=59；小时不限（长视频支持） |
| AC-4 | 前端把 3 段拼成 `h*3600 + m*60 + s` 总秒数，发送到后端 `preview_start` 字段 |
| AC-5 | 按钮渲染中文案：「渲染中（HH:MM:SS 起 N 秒）」|
| AC-6 | 旧 id `slirn-fine-preview-start` 已删除，不应再出现 |

## 不做的事

- ❌ 不改变后端 `preview_start` 字段语义（仍接受秒数）
- ❌ 不改变 `_run_fine_render` 实现（已接受 preview_start）
- ❌ 不加 Date picker / 时区逻辑（纯时间码）
- ❌ 不支持负值/倒放