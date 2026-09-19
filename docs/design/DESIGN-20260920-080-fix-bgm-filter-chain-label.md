# DESIGN-20260920-080 修复 BGM filter_complex label 拼接 bug

## Context

[REQ-20260920-080](../REQM/REQ-20260920-080-fix-bgm-filter-chain-label.md) 描述：BGM filter chain 末尾 label 拼接错误导致 `[bgm]` 前多一个逗号，ffmpeg 把 `[bgm]` 当成 filter name → `No such filter: ''` → 整条 filter_complex 被拒绝 → 预览/导出无 BGM。

## 关键决策

| 维度 | 决策 | 理由 |
|---|---|---|
| 修复位置 | 仅 `_assemble_fine_filter` audio block（[app.py:2075-2086](slirn_home/app.py#L2075)） | 单点故障，最小修复 |
| 修复方式 | **字符串拼接**（`+= "[bgm]"`）替代 `list + ",".join` | label 不进 join 列表 → 不可能被误加逗号 |
| 不重构 voice / amix | voice 是单一 filter；amix 是两个 label 间连线 | 已正确，无需动 |
| 测试 | 1 个新测试：断言 `",[bgm]" not in filter_complex` | 直接验证修复（不依赖真实 ffmpeg 跑通） |

## 关键文件改动

| 文件 | 行号 | 改动 | 估算行数 |
|---|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | 2075-2086 | `bgm_filters = [...]` + `",".join(...)` 改为 `bgm_chain = ""` + `+=` | ±0（行数基本不变） |
| [tests/test_workbench.py](tests/test_workbench.py) | 末尾 | 1 个新测试 | +35 |
| [docs/REQM/](docs/REQM/) | 新建 | REQ | +100 |
| [docs/design/](docs/design/) | 新建 | DESIGN | +80 |

净代码量约 **+30 行**（test + 文档）。

## 修复前后对比

**修复前**（bug 状态）：

```
filter_complex:
...
[0:a]volume=1.0[voice];
[1:a]aloop=loop=-1:size=2e9,volume=0.40,[bgm];   ← 错误：,[bgm] 前有逗号
[voice][bgm]amix=inputs=2:duration=first:normalize=0[aout]
```

→ ffmpeg 解析时把 `,` 后的 `[bgm]` 当 filter name → `No such filter: ''`

**修复后**（正确）：

```
filter_complex:
...
[0:a]volume=1.0[voice];
[1:a]aloop=loop=-1:size=2e9,volume=0.40,afade=t=in:st=0:d=0.50,afade=t=out:st=0:d=0.50[bgm];
[voice][bgm]amix=inputs=2:duration=first:normalize=0[aout]
```

→ filter 之间逗号合法；label 紧接 filter chain 末尾，无逗号

## 数据流

```
勾「启用背景音乐」+ 上传 mp3 → 点「生成预览」/「导出最终视频」
  ↓
_assemble_fine_filter → audio block
  ├─ REQ-080 修复后：bgm_chain 字符串拼接
  └─ 返回 filter_complex 字符串（无 `,[bgm]`）
  ↓
ffmpeg 解析 filter_complex
  ├─ 修复前：失败 → No such filter: '' → 输出无 BGM
  └─ 修复后：通过 → amix 输出 BGM + voice
  ↓
预览 / 最终视频含 BGM 混音
```

## 边界与错误处理

| 场景 | 行为 |
|---|---|
| 修复前 bgm_filters list 误用 `,` | 不再可能（label 不进 join 列表） |
| fade_in / fade_out = 0 | `if X > 0` 分支不追加，chain 正常 |
| 多个 afade 链 | 中间逗号合法（`,afade=t=in:...,afade=t=out:...`） |
| audio_input_enabled = False | 走 `else: chain.append("[voice]anull[aout]")`，未触发 BGM block，不受影响 |

## 复用现有基础设施

- `_FINE_AUDIO_DEFAULTS`（enabled/volume/fade_in/fade_out）→ 字段名/类型不变
- `amix=inputs=2:duration=first:normalize=0[aout]` → 不变
- `_resolve_mat_abs` audio kind → 不变

## 不做的事

- ❌ 不重构整条 audio chain（最小修复原则）
- ❌ 不改 amix 参数
- ❌ 不改 fc.audio 持久化格式