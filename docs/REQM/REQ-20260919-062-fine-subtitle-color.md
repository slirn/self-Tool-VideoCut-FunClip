# REQ-20260919-062 精剪视频·字幕文字颜色可设置（解决「白框」误判）

## 背景

用户在精剪视频面板发现一个"白框"一直去不掉。多次反馈「背景框勾掉也还在」、「透明度调到 0 和 1 都没用」。

诊断后真相：
- `fc.font` 里**没有** `color` 字段
- `_ass_force_style` 不输出 `PrimaryColour=...`
- libass 用默认 `&H00FFFFFF`（白色）填充字符
- 在白色 PPT 背景上，白字 = 看不见 → 只剩黑边轮廓
- 用户看到的是**字符的白色填充**（不是背景框）跟白色背景混在一起，形成视觉上的「白框」

`bg_enabled` 控制的是**字幕后面的背景框**（libass BorderStyle=4 + BackColour），与**文字颜色**无关 —— 这就是为什么勾掉 bg_enabled 没用。

## 验收标准

| ID | 验收项 |
|---|---|
| AC-1 | `fc.font` 默认有 `color: "#FFFFFF"` |
| AC-2 | `_ass_force_style(font)` 在 color 为 `#RRGGBB` 时输出 `PrimaryColour=&H00BBGGRR` |
| AC-3 | 精剪视频面板「字幕字体设置」区有「文字颜色」color picker，紧跟「描边颜色」之前 |
| AC-4 | 选不同颜色 → 调 `save_fine_font` → `fine_compose.json` 里 `font.color` 更新为所选色 |
| AC-5 | 再次渲染 → 输出视频里字幕文字真的是所选颜色（不是默认白色） |
| AC-6 | 旧任务 `fc.font.color` 字段缺失时自动迁移为 `#FFFFFF`（不影响其他字段） |
| AC-7 | 颜色 picker 显示当前 `font.color` 的值（迁移后能正确显示白色） |
| AC-8 | 单元测试：`_ass_force_style` 输出包含 `PrimaryColour`、迁移补字段、`save_fine_font` 接受 color |

## 不做的事

- ❌ 不改 libass 默认 PrimaryColour（用户可能依赖白色场景）
- ❌ 不做自动对比色（用户想自己选）
- ❌ 不改 stroke_color / bg_color 的语义（它们已经是对的）
- ❌ 不动 `bg_enabled` 行为（已正确）