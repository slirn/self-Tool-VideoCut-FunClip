# REQ-20260918-043 — 视频播放浮层

## 背景与目标

之前在任一阶段点播放按钮，播放器会直接插入到对应行下方，导致整个面板上下移动、行被挤出视口。本需求改为：播放时把播放器从面板搬到独立可拖动浮层，页面不再因播放而位移。

## 验收

| # | 标准 | 状态 |
|---|---|---|
| 1 | 修订 / 切分 / 优化 / 详情页等所有阶段的播放按钮 → 播放器进浮层 | ✅ |
| 2 | 浮层标题条可拖动（minX/-76、maxX=innerWidth-76 保证能拖回屏外再抓回） | ✅ |
| 3 | ✕ 收起按钮 → 暂停所有视频 + 隐藏浮层 | ✅ |
| 4 | 再次点任意播放 → 浮层自动弹回（hidden=false） | ✅ |
| 5 | 单占位：切分播放器进浮层时，修订播放器自动清出（避免两个播放器叠加） | ✅ |
| 6 | 拖动位置记在本机 `localStorage['slirnVfPos']`，下次播放恢复 | ✅ |

## 涉及文件

- `slirn_home/static/router.js` — `vfLayer` 模块级 + `vfPlace / vfPlaceSaved / vfDragBind / vfEnsureLayer / vfShow`；所有 play 帮助函数调 `vfShow(wrap)`；捕获全局 `play` 事件把视频挪入浮层
- `slirn_home/static/home.css` — `.slirn-video-float / .slirn-vf-bar / .slirn-vf-grip / .slirn-vf-title / .slirn-vf-close / .slirn-vf-body`

## E2E

`work/REQ-20260918-040/_e2e_040_046.py` 043 段：5/5 通过。
