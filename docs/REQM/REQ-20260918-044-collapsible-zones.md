# REQ-20260918-044 — 各阶段顶部功能区可折叠

## 背景与目标

字幕修订 / 切分修剪 / 优化字幕等阶段顶部常有多个功能区（说明、统计、快捷键、筛选、批量、搜索等），全部展开时挤占正文区域。本需求把这些功能区都改为可折叠收起，让用户按需展开。

## 验收

| # | 标准 | 状态 |
|---|---|---|
| 1 | 8 个已知区域自动包裹 `.slirn-col` 折叠壳（修订 filter/batch/search、cut spk-bar/batch/search、opt words/word-filters） | ✅ |
| 2 | 点击折叠头 → 整个区域收起（只剩胶囊头） | ✅ |
| 3 | 折叠状态写入 `localStorage['slirnCols']` | ✅ |
| 4 | 刷新页面后仍保持收起 | ✅ |
| 5 | 说明 / 统计 / 快捷键等连续 form-hint 自动并为一组（一折叠头收起整段） | ✅ |

## 涉及文件

- `slirn_home/static/router.js` — `colState / colSet / colWrap / colPaneKey / colEnhance / colObs`；`colObs` MutationObserver（60ms 去抖）+ 全局 data-action 委托 `col-toggle`
- `slirn_home/static/home.css` — `.slirn-col / .slirn-col-head / .slirn-col-btn / .slirn-col-chev / .slirn-col.off`

## E2E

`work/REQ-20260918-040/_e2e_040_046.py` 044 段：5/5 通过（修改后的隐藏子元素检测）。
