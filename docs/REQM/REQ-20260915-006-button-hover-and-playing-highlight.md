# REQ-20260915-006 — 修复：按钮 hover 变白看不见文字 + 播放中字幕行高亮无感

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260915-006 |
| 日期 | 2026-09-15 |
| 优先级 | P1（可感知的 UI 缺陷，两项均用户报告） |
| 状态 | ✅ 已完成（验证记录见 §4） |
| 关联 | REQ-20260915-001（字幕列表/播放器）；REQ-20260915-005（修订列表同款高亮） |
| 改动范围 | 仅 `slirn_home/static/home.css`（3 处规则）；Python/JS 零改动 |

## 1. 缺陷与根因

### BUG-1 按钮鼠标悬停变成一片白，看不到文字
- 现象：主要操作按钮（渐变底白字，如「生成字幕」「创建任务」「保存修订决策」）hover 后背景变近纯白，白字消失。
- 根因：`.slirn-btn:hover { background: var(--hover-bg) }` 特异度 (0,2,0)，
  高于 `.slirn-btn-primary` 的 (0,1,0) 渐变背景 → hover 时渐变被
  `--hover-bg: rgba(255,255,255,0.85)` 覆盖；`.slirn-btn-primary:hover`
  原来只补了 box-shadow 没补 background → 白底白字。
- 普通玻璃按钮（深字）hover 仍可读，故用户感知为"所有（主要）按钮"。

### BUG-2 字幕列表播放时当前行没有可见高亮
- 现象：点击字幕行定位播放后，正在播放的那行看不出高亮。
- 根因：不是逻辑缺失 — `bindSubPlayer` 已有 timeupdate 跟随 +
  `.active` 类 + scrollIntoView（REQ-001 实现）；但 `.slirn-sub-row.active`
  样式为 12% 透明度紫底 + 左侧 3px 竖条，与 `:hover` 背景完全同色，
  视觉上等于没有高亮。

## 2. 修复方案

| ID | 修复 |
|---|---|
| REQ-6.1 | `.slirn-btn-primary:hover` 重新声明 `background: var(--accent-gradient)`，hover 只加强阴影（渐变保留、白字可读）；danger/普通按钮本就各自覆盖，不动 |
| REQ-6.2 | `.slirn-sub-row.active` / `.slirn-rev-row.active` 改为**渐变底 + 白字**（序号/时间 80–85% 白、正文纯白加粗、修订说明/保留文本白色；徽章、下拉、输入框保持自身配色），与 hover（12% 淡紫）拉开明显差距；左侧指示条改白色半透明 |

## 3. 验收标准

- [x] AC-1 浏览器实测：CDP 真实 hover（Input.dispatchMouseEvent）primary 按钮，
  computed `background-image` 为 linear-gradient（非 none），文字颜色仍为白 → 可读
- [x] AC-2 普通玻璃按钮 hover 仍为浅底深字（不回归）
- [x] AC-3 字幕列表：点击行播放 / 拖动进度到任一段 → 该段行带 `.active`
  且 computed 背景为渐变、正文白色加粗；行随播放自动跟随 + scrollIntoView
- [x] AC-4 修订列表同款高亮生效（.active 渐变底白字）
- [x] AC-5 现有 pytest 不受影响（纯 CSS 改动）；截图存档

## 4. 验证记录（2026-09-15，CDP headless Chrome，任务 20260915-011）

- **AC-1**：顶栏「➕ 新建任务」primary 按钮真实 hover →
  `background-image: linear-gradient(135deg, rgb(102,126,234), rgb(118,75,162))`、
  `color: rgb(255,255,255)` — 修复前此处为 `none` + 近白底（白字不可见）
- **AC-2**：普通按钮 hover `background-image: none` + `color: rgb(30,41,59)` 不回归
- **AC-3**：点击第 1 行 → 立即 `.active`（渐变底 + 正文白 600）；继续播放 1.6s →
  高亮自动跟随到第 2 行（证明"播放时当前文字高亮跟随"）；暂停后 seek 到第 5 段 +200ms →
  高亮切到第 5 行
- **AC-4**：修订列表 seek 到第 4 段 → 第 4 行 `.active` 渐变底白字
- **AC-5**：`pytest tests/ -q` 94 passed；`ruff check .` 0 错
- 截图：`work/REQ-20260915-006-hover-highlight/`（hover primary / 字幕高亮 / 修订高亮）
- 说明：高亮跟随逻辑（timeupdate + scrollIntoView）为 REQ-001 已有实现，
  本次仅把 `.active` 视觉从 12% 淡紫（与 hover 同色、不可辨）强化为渐变底白字加粗

## 5. 非目标

- ❌ 不改任何 Python/JS 逻辑（高亮跟随机制已存在，仅视觉强化）
- ❌ 不做深色主题单独调色（渐变与白字在两主题下均可读）
