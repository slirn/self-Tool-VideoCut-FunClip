# REQ-20260918-041 — 复选框可见性 + 关键开关默认值

## 背景与目标

两处勾选框在亮主题下看不清：
1. 字幕生成阶段「区分说话人」默认勾选，且描边与白底同色
2. 切分修剪阶段「跳过已删除」默认未勾选

## 验收

| # | 标准 | 状态 |
|---|---|---|
| 1 | 全局 input[type=checkbox]：1.5px 实线边框 + 主题强调色描边 + accent 勾选色 + 15×15 命中区 | ✅ |
| 2 | 亮 / 暗主题分别用 --chk-border token（深灰 0.8 / 浅灰 0.65） | ✅ |
| 3 | 字幕生成「区分说话人」默认未勾选 | ✅ |
| 4 | 切分「跳过已删除」默认勾选 | ✅ |
| 5 | 即使未选中也能看清勾选框（描边 1.5px 非透明） | ✅ |

## 涉及文件

- `slirn_home/app.py` — sd-switch 默认未勾选；skipdel 默认 checked
- `slirn_home/static/home.css` — 全局 input[type=checkbox] 规则 + --chk-border token
- `slirn_home/static/router.js` — cutSpkBarRender 模板 `type="checkbox" checked`
- `tests/test_asr_service.py` — sd-switch 默认未勾选 + id 存在
- `tests/test_cut_speaker.py` — skipdel 默认 checked

## E2E

`work/REQ-20260918-040/_e2e_040_046.py` 041 段：2/2 通过。
