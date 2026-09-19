# REQ-20260919-065 精剪视频·检测区域作为参数 + 参数 JSON 导出/导入

## 背景

用户反馈三条需求：

1. **检测区域信息提升为正式参数**：当前「🎨 背景图区域检测」的结果存在 `fc.bg_detect_cache`（语义是 cache = 缓存/可丢）。但「区域检测」目的是告诉程序「视频在 bg 图上的位置」（4 角点 + 宽高），这本质是**视频显示区域**这个**参数**，应该和其他参数（layout/font/output/audio）同级，导出和导入时一并处理。

2. **参数 JSON 导出功能**：精剪视频的所有参数（layout/font/output/audio/detected_region）当前只能通过「保存设置参数」存到全局模板，没法下载成 JSON 文件。用户希望能把参数导出成 JSON 文件（用于备份、迁移到其他机器、人工编辑后回传）。

3. **参数 JSON 导入功能**：与导出配套。不同人/不同机器之间可以分享参数（导出 JSON → 微信/邮件/网盘 → 另一个人导入），减少重复调参工作量。

## 验收标准

| ID | 验收项 |
|---|---|
| AC-1 | `fc.detected_region` 字段：检测后存储 4 角点 + 宽高 + 原图尺寸 + 算法/阈值 + 检测时间 |
| AC-2 | `bg_detect_cache` 字段保留（向后兼容已有任务），与 `detected_region` 同步写入同一份内容 |
| AC-3 | 检测面板 UI 显示当前 `detected_region` 内容（与原 `bg_detect_cache` 显示一致） |
| AC-4 | 操作栏行 2 新增「📤 导出参数」按钮：点 → 浏览器下载 `fine_params_{tid}_{YYYYMMDD_HHMMSS}.json` |
| AC-5 | 导出的 JSON 包含完整 fine_compose（layout/font/output/audio/detected_region/materials 元信息），不含视频/音频文件本身 |
| AC-6 | 导出的 JSON 顶层带 `_schema: 3` 标识（v2 是当前；新增 detected_region 字段后升到 v3） |
| AC-7 | 文件下载成功后，toast「✅ 参数已导出到 {文件名}」 |
| AC-8 | 操作栏行 2 新增「📥 导入参数」按钮：点 → 弹文件选择器，选 JSON → 上传到后端 → 应用到当前任务 |
| AC-9 | 导入只覆盖 layout/font/output/audio/detected_region 4 个字段，**不**改 materials（视频/封面/BGM 文件路径保持当前任务的） |
| AC-10 | 导入成功后刷新整个精剪面板（让滑块/输入框反映新参数）；toast「✅ 已从导入文件应用参数」 |
| AC-11 | 导入失败（JSON 损坏/缺 _schema/字段类型错）→ toast「❌ {错误信息}」，不修改当前任务 |
| AC-12 | 单测：_save_bg_detect_cache 同时写 bg_detect_cache + detected_region；导出 endpoint 返回正确 JSON；导入 endpoint 验证 + 应用 |

## 不做的事

- ❌ 不做导入时的素材迁移（materials 保持当前任务的；用户在新机器上要手动上传视频/BGM）
- ❌ 不做导入合并（只覆盖，不做 diff）
- ❌ 不做版本对比 / 迁移工具
- ❌ 不改变检测算法本身（仍走原有 pixel/ai/ai_color/center_expand）
- ❌ 不改变渲染逻辑（detected_region 只是参数快照，不参与 ffmpeg 计算）
- ❌ 不把 detected_region 复制到 layout.video.x/y/scale（那是 AI 智能布局的事，独立功能）