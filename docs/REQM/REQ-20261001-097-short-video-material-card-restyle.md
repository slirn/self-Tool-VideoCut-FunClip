# REQ-20261001-097 · 短视频混剪：素材卡重排 + 工作台视觉美化

- **日期**：2026-10-01
- **来源**：用户需求
  > 「查看和删除按钮没有必要占那么大的空间，两个放同一行，甚至还可以再加其他按钮都可以。这个页面实在有点儿丑，你稍微美化一下」
- **状态**：已实现（全量 1081 测试通过）

## 1. 需求与实现

### 1.1 素材卡重排（按钮同行的紧凑卡）

| 层 | 改动 |
|---|---|
| UI | 素材卡从「上下两行大按钮」改为紧凑卡结构：`.slirn-sv-material-head`（类型徽章 + 单行省略文件名）→ `.slirn-sv-material-meta`（来源中文：`任务导入` / `上传`，时长）→ `.slirn-sv-material-actions`（查看 + 删 同一行 mini 按钮，`margin-top: auto` 底部对齐） |
| CSS | `.slirn-sv-material`：flex 纵向卡，hover 上浮 + accent 边框；`.slirn-sv-kind-badge` 按类型着色（视频 紫 / 图片 亮紫 / 音频 琥珀 / 背景图 青绿 / 文件 灰）；`.slirn-sv-materials` 网格 `minmax(230px, 1fr)` 自适应多列 |
| 语义 | 卡高一致（grid 拉伸），操作按钮统一沉底；后续加按钮塞进 `.slirn-sv-material-actions` 即可（用户预留扩展） |

### 1.2 工作台整体美化

| 处 | 改动 |
|---|---|
| 区块标题 | `.slirn-sv-section-title::before` 左侧 4px accent 色条 + `justify-content: flex-start`（纯文本标题紧跟色条，不再被 space-between 推到最右；带按钮的 `.slirn-sv-subtitle-row` 保持 space-between） |
| 上传工具条 | `select` / `input[type=file]` 统一圆角边框 + `--input-bg`；`::file-selector-button` 蓝紫小按钮（accent-soft 底 + accent 字），告别浏览器原生灰大块 |
| 项目卡 | `.slirn-sv-card` hover 上浮 + 阴影（与素材卡同语言） |
| 版本徽章 / 状态 | 现有 `.slirn-sv-badge` 收敛为药丸描边样式 |

### 1.3 排查记录：CSS「改了不生效」的根因（重要教训）

改动后截图一度「看不到新样式」，逐层排查（浏览器缓存禁用仍复现）后定位：**home.css 不是按请求读取的静态资源，而是 `app.py::_read_css()` 在 `launch(css=...)` 时一次性注入**（Gradio 6.x 会用 `_deprecated_css` 覆盖 `app.css`，故走 css 参数）。因此 **改 home.css 必须重启服务**，「CSS 是静态文件、刷新即生效」的直觉在本项目不成立。

重启服务后用确定性检测复核（CDP `getComputedStyle` + Range 几何 + 像素分析）：

- `.slirn-sv-section-title` computed `justify-content: flex-start`；标题文字 `offset_from_left=22px`（色条 4 + margin 8 + padding），右余量 1082px → 左对齐生效
- 上传行检出 270 个 accent 蓝紫像素 → `::file-selector-button` 新样式生效

> 注：Read→CDN 传图给视觉模型的链路对含反斜杠的 Windows 路径返回过旧缓存（假阴性），视觉结论与 computed style 矛盾时以确定性检测为准。

## 2. 验收标准与结果

| # | 验收 | 结果 |
|---|---|---|
| AC1 | 查看 / 删 两按钮同一行，不再各占整行 | ✅ `.slirn-sv-material-actions` 单行 flex；UI 断言 + 截图确认 |
| AC2 | 卡内信息紧凑（徽章 + 单行省略文件名 + 中文元信息） | ✅ `test_short_video_ui_contains_editor_and_materials`（`slirn-sv-kind-badge` / `任务导入` 断言） |
| AC3 | 整页视觉美化（标题色条、上传控件统一、hover 反馈） | ✅ 确定性检测：标题左对齐 22px、上传行 270 蓝紫像素 |
| AC4 | 既有行为不回归 | ✅ 全量 1081 passed |

## 3. 影响面

- 修改：`short_video_ui.py`（素材卡结构 + 元信息中文化）、`static/home.css`（素材卡 / 徽章 / 标题色条 / 上传控件 + 文件按钮，约 4700–5030 行区段）
- 测试：`tests/test_short_video_service.py`（`test_short_video_ui_contains_editor_and_materials` 增 3 条断言）
- 兼容性：纯展示层改动，service / 端点 / 数据结构零变更；`slirn-btn-mini` 沿用既有类
