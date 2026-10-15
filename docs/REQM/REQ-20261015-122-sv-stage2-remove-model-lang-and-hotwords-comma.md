# REQ-20261015-122 — Stage 2 去掉模型/语言选择 + 热词改半角逗号分隔

> 状态：✅ 已完成（2026-10-15）
> 背景：用户问「提取字幕里的模型是什么意思」，排查链路时发现历史选项
> `seaco_paraformer（更准）` 是坏的（脚本 argparse 只收
> paraformer / fun-asr-nano / sensevoice，选它必 `invalid choice` 退出码 2），
> 且中文下默认项加载的就已是 SeACo-Paraformer-large，「更准」名不副实。

## 1. 需求（用户原话要点）

1. **去掉「模型」「语言」两个选择项**，默认选可用且准确的那一项
   （= paraformer-zh，zh 下实际加载 SeACo-Paraformer-large），不让用户选；
2. 热词输入**不要空格分隔，改半角逗号分隔**。

## 2. 实现

| 文件 | 改动 |
|---|---|
| `slirn_home/short_video_ui.py` | Stage 2 表单删掉模型 select + 语言 input；热词 placeholder 改「半角逗号分隔，提升专有名词识别」 |
| `slirn_home/static/short_video.js` | `stage2Run()` 不再读/发 model、lang（端点默认 paraformer/zh 兜底）；hotwords 原样上送 |
| `slirn_home/short_video_extract.py` | ① `_norm_hotwords()`：半角/全角逗号切开重排为空格分隔（funasr hotword 参数口径；空格分隔旧输入原样通过）；② `_FUNASR_MODEL_WHITELIST` 白名单，白名单外的模型值（如历史 seaco_paraformer）回默认 `paraformer` |

### 设计要点

- **逗号归一放后端 `_run_funasr_extract`**（单一收口）：Stage 2 主提取与
  Stage 5 逐条重 ASR 都走它；前端只传原始输入。
- **白名单兜底**：即使 API 直调传入坏值也不会再让子进程死于 argparse。
- `app.py` 端点本就有 `body.get("model") or "paraformer"` 默认，零改动。

## 3. 测试（+4，全量 1294 passed）

- UI：无 `slirn-stage2-model/lang`、无 seaco_paraformer/SenseVoiceSmall；
  热词框保留且 placeholder 为半角逗号文案；
- JS：`stage2Run` 函数体无 model/lang 引用，hotwords 保留；全文件无残留 ID；
- `_norm_hotwords`：半角+全角逗号、混合空格、旧空格输入、空串；
- `_run_funasr_extract`（mock subprocess）：seaco_paraformer → cmd 里
  `--model paraformer`；`--hotwords 智能体 Agent`。

## 4. E2E

7861 重启后真实项目页面（20261003-001 / sv_20261004_072937_572eb5）验证
Stage 2 区块：模型/语言选择已消失、无 seaco_paraformer / SenseVoiceSmall
残留、热词框保留且 placeholder 为「半角逗号分隔，提升专有名词识别」、
「提取字幕」按钮完好；服务端 JS 已无 `slirn-stage2-model` 引用。

## 5. 已知边界

- 直接调 `/slirn/api/short_video_stage2_run` 仍可传 model/lang 覆盖（API 级
  自由度保留），但白名单外的值会被收口回默认；UI 已无入口。
