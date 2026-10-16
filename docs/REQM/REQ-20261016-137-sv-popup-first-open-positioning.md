# REQ-20261016-137 — 视频预览浮窗首次打开定位 bug 修复

> 状态：✅ 已完成（2026-10-16）
> 父 REQ：[REQ-132 SV Stage5 Video Popup](REQ-20261016-132-sv-stage5-video-popup.md)（视频弹窗原始实现）
> 父 REQ：[REQ-135 SV Stage6 Tabs + Video Preview](REQ-20261016-135-sv-stage6-tabs-and-video-preview.md)（Stage 6 复用弹窗）
> 范围：`svStage5Preview` 首次打开分支的位置计算

## 1. 需求（用户原话）

> 点预览的时候，除了这个弹出的视频播放窗口只看到左边那小条，在窗口的右边，所有的视频根本都看不到。这个 bug 是第一次进到页面的时候出现。

> 用户截图：弹窗标题「#2 预览」+ 下载按钮可见，视频左侧窄条可见（0:38/0:38），其余部分全部超出屏幕右边。

## 2. 根因分析

[short_video.js:1407-1413](slirn_home/static/short_video.js#L1407) 首次打开逻辑：

```js
if (!popup.style.width) {
  popup.style.width = Math.min(640, Math.round(window.innerWidth * 0.6)) + 'px';
  popup.style.height = Math.min(480, Math.round(window.innerHeight * 0.5)) + 'px';
  popup.style.left = (window.innerWidth - popup.offsetWidth - 24) + 'px';  // ← BUG
  popup.style.top = '80px';
}
popup.hidden = false;  // ← 此时才显示
```

**致命点**：`popup.style.left` 计算时 popup 还是 `hidden = true`（`display: none`），所以 `offsetWidth = 0`。

带入公式（设 innerWidth=1500，w=900，h=750）：
- `popup.style.left = 1500 - 0 - 24 = 1476`（屏幕右边缘外 -24 像素）
- 弹窗 width = 900px
- 弹窗实际位置：从 1476 到 2376 → **876px 在屏幕外**

CSS 的 `max-width: calc(100vw - 24px)` 看起来能挡，但因为 `popup.style.width` 是 inline style，**优先级最高**，max-width 形同虚设。

**触发条件**：`popup.style.width` 为空时（即刚 `ensureSvVideoPopup()` 创建的第一次预览）。后续点击因 `style.width` 已有值不再重算 left，bug 持续。

## 3. 关键设计决策

| 维度 | 选型 | 理由 |
|---|---|---|
| 修复方式 | 不用 `offsetWidth`，用刚算出的 `w` 推 left | 最简：不需要 reflow，不需要等渲染 |
| 替代方案 A | `popup.hidden = false; popup.offsetWidth;` 强制 reflow | 多一次强制 reflow，浪费 |
| 替代方案 B | 弹窗先 visible 再定位 | 用户看到弹窗「闪现」从屏幕外拉回，UX 差 |
| max-width 防御 | 保留 `max-width: calc(100vw - 24px)` | 兜底；inline style 优先级高时也能挡（实测 inline max-width 仍可设） |
| 测点 | 静态分析 + 节点语法 check | DOM 行为难测，但 bug 根因是单个变量读错，静态分析足够锁住 |

## 4. 关键文件改动

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/static/short_video.js:1407-1421](slirn_home/static/short_video.js#L1407) | `popup.offsetWidth` 改用 `w` 变量推 left | +8 / -2 |
| [tests/test_short_video_popup_first_open_positioning.py](tests/test_short_video_popup_first_open_positioning.py) | 7 个新测试 | +150 |

1373 → 1380 pytest 全过（+7）。

## 5. 验收

- [x] 1380/1380 pytest 全过
- [x] JS node --check 通过
- [x] 静态分析：svStage5Preview 不再读 `popup.offsetWidth`
- [x] 静态分析：left 公式 = `innerWidth - w - 24`（`w` 至少 1px ≤ 60% innerWidth → left 必然在视口内）
- [x] CSS max-width 防御保留
- [x] 路由派发 `sv-stage5-preview` 不破

## 6. 用户操作

按 [memory:slirn-dynamic-script-load-race](slirn-dynamic-script-load-race.md)，JS 改动**不需重启 7861** — 刷新页面即可。

打开「8课3短视频」项目 → Stage 6 → 点「▶ 预览」按钮 → 弹窗正常显示在屏幕右上角，宽 60% 视口 / 高 50% 视口，可见。

## 7. 关键教训

**「hidden 元素 + 读 offsetWidth/offsetHeight = 0」是经典陷阱** —— 任何在 `display: none` 状态下读几何属性的代码都会拿到 0。修复模式：
1. 先算出想要的几何值（基于 innerWidth/innerHeight/视口）
2. 直接用算出的值推位置
3. 不要依赖「先设再读」（offsetWidth 在 hidden 下永远是 0）

## 8. 关联

- [[req-132-sv-stage5-video-popup]] — 浮窗原始实现
- [[req-135-sv-stage6-tabs-and-video-preview]] — Stage 6 复用
- [[slirn-dynamic-script-load-race]] — JS 改动不需重启
- [[js-edit-showtab-regression]] — node --check 防回归
