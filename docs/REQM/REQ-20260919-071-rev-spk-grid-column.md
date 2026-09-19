# REQ-20260919-071 字幕修订行·人员徽章列位

## Context

字幕修订阶段，每行有 5 个子元素（序号 / 时间 / 字幕 / 审核选择 / 折叠按钮）。
点击「关联人员 ID」后，前端在 `slirn-sub-text` 之前插入了第 6 个 `.slirn-rev-spk`
徽章。Grid 模板仍是 5 轨 `44px 216px 1fr 150px 26px`，导致徽章掉到了 216px 的
「时间戳」轨道里，时间戳被挤到下一行，徽章 + 时间戳 + 大段留白三块横向加起来
吃掉整行宽度，把 `slirn-sub-text` 挤成折行。

## 验收标准

- AC-1：`.slirn-rev-line` grid 升级为 6 轨，人员徽章独占一轨（auto，未关联塌缩 0）
- AC-2：所有 6 个子元素显式 `grid-column: N`，不受 DOM 顺序影响（与精剪阶段
  REQ-20260917-036 同口径）
- AC-3：窄屏 `@media (max-width: 720px)` 仍保持 6 轨，时间戳改为 auto（窄屏
  隐藏，腾出宽度给字幕）
- AC-4：单元测试覆盖上述三项

## 方案

### Grid 升级（[slirn_home/static/home.css:1507-1524](slirn_home/static/home.css#L1507)）

```css
.slirn-rev-line {
  display: grid;
  grid-template-columns: 44px auto 216px 1fr 150px 26px;  /* 6 轨 */
  gap: 10px;
  padding: 6px 14px;
  align-items: center;
  min-height: 34px;
}
.slirn-rev-line > .slirn-sub-idx    { grid-column: 1; }
.slirn-rev-line > .slirn-rev-spk    { grid-column: 2; justify-self: start; }
.slirn-rev-line > .slirn-sub-time   { grid-column: 3; }
.slirn-rev-line > .slirn-sub-text   { grid-column: 4; }
.slirn-rev-line > .slirn-rev-select { grid-column: 5; }
.slirn-rev-line > .slirn-rev-toggle { grid-column: 6; }
```

`auto` 的人员徽章轨在未关联时塌缩为 0（`width: max-content` + `justify-self: start`
不影响 grid 分配），不会挤占其他列。

### 窄屏媒体查询（[slirn_home/static/home.css:1985-1992](slirn_home/static/home.css#L1985)）

```css
@media (max-width: 720px) {
  .slirn-rev-line { grid-template-columns: 32px auto auto 1fr 118px 22px; }
  .slirn-rev-line .slirn-sub-time { display: none; }
  .slirn-rev-detail { padding-left: 32px; }
  .slirn-rigor-cards { grid-template-columns: 1fr; }
}
```

窄屏：人员徽章 `auto` + 时间戳 `auto`（但 display:none 实际占 0）+ 字幕 1fr
+ 审核 118px + 折叠 22px。

## 关键文件

| 文件 | 改动 |
|---|---|
| [slirn_home/static/home.css:1507-1524](slirn_home/static/home.css#L1507) | grid 升级 6 轨 + 显式 grid-column |
| [slirn_home/static/home.css:1985-1992](slirn_home/static/home.css#L1985) | 窄屏 6 轨 |
| [tests/test_workbench.py](tests/test_workbench.py) | 3 个新测试 |

## 测试

| 测试 | 验证 |
|---|---|
| `test_css_rev_line_grid_has_six_columns_with_auto_spk` | grid 6 轨，第 2 轨 = auto（AC-1） |
| `test_css_rev_line_children_have_explicit_grid_column` | 6 个子元素都显式 grid-column（AC-2） |
| `test_css_rev_line_narrow_screen_keeps_six_columns` | 窄屏 @media 也保持 6 轨 + 第 2 轨 auto（AC-3） |

```
pytest tests/ -q     # 519 通过（516 + 3 新）
```

## 真机验证

1. 浏览器打开 http://127.0.0.1:7861
2. 进字幕修订阶段 → 任意一行点「关联人员 ID」选个人员
3. 观察：
   - 人员徽章紧贴序号右侧，宽度仅够显示人员编号
   - 时间戳（HH:MM:SS,mmm）在人员徽章右侧单独一轨
   - 字幕文字占据剩余 1fr 宽度，不再被折行
4. 缩小浏览器到 720px 以下 → 验证窄屏布局依然正常（时间戳隐藏）
