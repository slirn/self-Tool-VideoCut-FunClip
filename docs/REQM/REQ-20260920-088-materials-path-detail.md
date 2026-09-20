# REQ-20260920-088 — 精剪素材路径详情（来源区分 + 完整路径展示）

## 背景

用户反馈：精剪视频·素材生成器 → 上传素材区域，6 个素材卡片只显示文件名 + 来源 badge（📥 自动获取 / 📤 手动上传），**看不到素材实际存储的路径**。用户需要确认：

1. **每个素材的实际路径**（绝对路径或相对 repo root 路径）
2. **区分来源类型**：
   - 上游产物（自动获取）— 系统从上游阶段输出读
   - 用户上传（手动上传）— 用户通过浏览器上传的文件
   - 系统默认 BGM — 从 `_DEFAULT_BGMS_DIR` 复制到 task 目录

> 你现在上传素材区域，把这些素材的实际路径展现出来。我现在要确认一下，另外，这个路径要区分是系统里边的路径，还是用户上传的之后保存的路径

## 6 个素材的物理路径规律（探索阶段确认）

| kind | source 类型 | 物理路径（相对 repo_root）|
|---|---|---|
| video | auto | `tasks/{tid}/outputs/rough_compose.mp4`（粗剪合成阶段产物）|
| subtitle | auto | `tasks/{tid}/tmp/optimized_subs.srt`（subtitle 优化阶段产物）|
| video | upload | `tasks/{tid}/upload/video_{filename}`（用户上传）|
| subtitle | upload | `tasks/{tid}/upload/subtitle_{filename}`（用户上传）|
| cover | upload | `tasks/{tid}/upload/cover_{filename}`（用户上传，仅人工）|
| bg | upload | `tasks/{tid}/upload/bg_{filename}`（用户上传，仅人工）|
| reference | upload | `tasks/{tid}/upload/reference_{filename}`（用户上传，仅人工）|
| audio | upload | `tasks/{tid}/upload/audio_{filename}`（用户上传）|
| audio | default_bgm | `tasks/{tid}/materials/audio/{bgm_id}.mp3`（系统 BGM 复制品）|

**关键事实**：
- video / subtitle 可「自动获取」或「手动上传」（**两种来源共存**）
- cover / bg / reference / audio 默认只能「手动上传」
- audio 多一个 `select_default_bgm` 入口：选系统 BGM → 复制到 `tasks/{tid}/materials/audio/`
- 无论哪种 source，最终都写 `fc.materials[kind].path` 字符串

## 验收标准

### AC-1：每个素材卡片底部加「🔍 详情」按钮 ✅ 待验证
**位置**：`.slirn-fine-upload-card` 内部，状态文本下方，按钮样式 `slirn-btn slirn-btn-xs`。
**disabled 条件**：未上传/未自动获取（path 为空）时按钮 disabled + tooltip「请先上传或自动获取素材」。

### AC-2：点击「🔍 详情」弹出模态框显示完整信息 ✅ 待验证
模态框内容（参考字段顺序）：
```
[kind 中文名] · [来源中文标签 + 色块]

📁 物理路径：[完整绝对路径]
🔗 fc.materials path：[相对 repo_root 路径]
📊 文件大小：[XX KB / MB]
🕒 最后修改：[YYYY-MM-DD HH:MM:SS]
🔖 source 字段：[auto / upload / default_bgm]
🎬 类型：[video / subtitle / image / audio]

[关闭]
```

### AC-3：来源色块（视觉区分） ✅ 待验证
| source | 色块 | 中文标签 |
|---|---|---|
| `auto`（上游产物）| 🟦 蓝色 `#3b82f6` | 上游产物 |
| `upload`（用户上传）| 🟩 绿色 `#22c55e` | 用户上传 |
| `default_bgm`（系统 BGM 复制品）| 🟪 紫色 `#a855f7` | 系统默认 BGM |

### AC-4：路径缺失时不弹模态框，给出明确提示 ✅ 待验证
如果 `path` 为空但 `source` 不为空（例如 `source=auto` 但上游产物不存在），模态框显示「⚠️ 路径解析失败：上游产物已不存在」+ 提供「📥 重新自动获取」按钮。

### AC-5：物理路径必须真实可访问（文件系统 stat） ✅ 待验证
后端 `_resolve_mat_abs` 已支持 3 种候选路径（tasks_dir / repo_root / tasks_dir+tid 兜底），前端弹窗显示前**调用 `/slirn/api/material_info`** 拿真实物理路径 + 文件大小 + mtime。

### AC-6：modal 默认在屏幕中央 + ESC 关闭 + 点击遮罩关闭 ✅ 待验证
通用 modal 行为（复用 `_render_fine_cut_zone` 已有 modal 模式或新建）。

### AC-7：详情按钮不影响现有交互（上传/自动获取/预览仍工作） ✅ 待验证
新加的 `data-action="fine-mat-detail"` 不与现有 action 冲突。

### AC-8：新增 6+ 测试（覆盖 3 种 source + 路径缺失 + 按钮 disabled 条件）✅ 待验证

## Why

精剪素材的「路径 + 来源」是排查 BGM、静音、白框、字幕错位等问题的关键线索。当前 UI 只显示文件名（用户看不到实际路径），导致：
1. 用户上传错文件（文件名相似但路径错误）
2. 用户不知道系统 BGM 复制到了 task 目录里哪个位置
3. 用户不知道上游产物是否存在（fc.materials.video.path 写着，但 outputs/rough_compose.mp4 实际不存在）
4. 排查 BGM 问题时无法定位路径

## How to apply

未来任何「素材/文件/资源」类 UI：
1. **必须暴露完整路径**（不止文件名）— 用户需要知道东西在哪
2. **区分来源类型**（系统生成 / 用户上传 / 第三方复制）— 排查问题时要溯源
3. **弹窗详情优于 inline 展示** — 详情多（路径 + size + mtime + source + type）但用得少，inline 容易撑爆布局

## 关联

- [REQ-20260919-061](../REQM/REQ-20260919-061-fine-cut-5-materials.md) — 精剪视频 5 素材上传卡（原版）
- [REQ-20260919-063](../REQM/REQ-20260919-063-fine-cut-preview.md) — 精剪视频预览
- [REQ-20260920-082](../REQM/REQ-20260920-082-move-bgm-selector.md) — 系统默认 BGM 嵌入 audio 卡
- [REQ-20260920-085](../REQM/REQ-20260920-085-upload-audio-auto-enable-bgm.md) — 上传音频自动启用 BGM
- [REQ-20260920-087](../REQM/REQ-20260920-087-task-search-box.md) — 上一轮回归修复（showTab 完整性）
