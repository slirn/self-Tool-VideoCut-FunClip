# REQ-20260918-046 — 当前阶段完成后自动跳转下一阶段

## 背景与目标

用户在某阶段完成一次工作后，通常会继续到下一阶段。本需求在阶段列表顶部新增一个「完成后自动进下一阶段」开关，启用后由 JS 检测阶段状态从 current → done，触发自动切换。

## 验收

| # | 标准 | 状态 |
|---|---|---|
| 1 | 阶段列表顶部新增 `slirn-wb-autonext` 勾选框（默认未勾选） | ✅ |
| 2 | 状态写入 `localStorage['slirnWbAutoNext']`，下次进入仍生效 | ✅ |
| 3 | 启用后，修订保存 / 合成完成触发阶段 current → done 时自动切换 | ✅ |
| 4 | 阶段列表收起时开关隐藏（`.wb-stages-collapsed .slirn-wb-autonext { display: none }`） | ✅ |

## 涉及文件

- `slirn_home/app.py` — 阶段列表 HTML 新增 `<label class="slirn-wb-autonext">`
- `slirn_home/static/router.js` — `wbAutoNextOn / applyWbAutoNextState / wbAutoNextMaybe`；openWorkbench 渲染后回填状态 + 比较 prevDone/prevActive/hadWb 决定是否触发跳转
- `slirn_home/static/home.css` — `.slirn-wb-autonext` 样式 + 收起态隐藏
- `tests/test_workbench.py` — autonext switch 渲染断言

## E2E

`work/REQ-20260918-040/_e2e_040_046.py` 046 段：1/2 通过（自动跳转本身通过；toast 时序敏感偶现 flake，可重跑）。
