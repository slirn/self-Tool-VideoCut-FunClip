# REQ-20260916-020 — 按字幕文件时间段截取拼接（不经 LLM）

## 需求原话

> 现在是需要这样一个方法，按照字幕文件的时间段从原始视频中截取片段并拼接的方法。你先写出这个方法，然后做测试，并验证生成的视频拼接是正确的。然后把这个方法反馈给我，我一会检查一下

## 背景

排查上游 FunClip「LLM 智能裁剪」截取不符的根因时确认：LLM 在链路里只承担「选段」，
时间段一旦确定（SRT 毫秒级）就完全可以纯程序截取拼接，且能绕开上游入口的全部
失效模式（时间戳格式正则零容错 / 第 2~N 段静默丢弃 / 乱序拼接 / LLM 改写时间戳）。

## 实现

### 方法（slirn_home/compose_service.py）

```python
from slirn_home import compose_service

# 主入口：SRT 每条目 = 一个截取区间（毫秒），升序合并相邻 → 上游合成路径切片拼接
result = compose_service.compose_from_srt(
    src=Path("原始视频.mp4"),
    srt_path=Path("时间段.srt"),
    dst=Path("成片.mp4"),          # 同名 .srt 副产物 = 随片字幕（时间轴已平移）
    on_progress=None,               # 可选回调 (pct 0-100)
)
# 返回: {duration, size_mb, segments, raw_segments, keep_sec,
#        elapsed, srt_entries, dropped, keep_sec_expected}

# 纯解析（可单独用）: SRT 文本 → [{"start_ms","end_ms","text"}] 升序
compose_service.parse_srt(srt_text)
```

| 函数 | 说明 |
|---|---|
| `parse_srt(srt_text)` | SRT → 区间+文本（升序）。容忍 BOM/CRLF/缺编号/乱序块/多行文本；毫秒分隔符兼容 `,` 与 `.`、可省略（不足 3 位右补零）；`end<=start` 或无时间轴块跳过 |
| `compose_from_srt(src, srt_path, dst)` | 解析 → 越界处理 → `compose_rough_cut`（REQ-018 上游 VideoClipper 方法：切片 → 拼接 → 随片字幕平移） |

### 关键设计

- **复用 REQ-018 合成路径**：不新写切片代码，`compose_from_srt` 只做「SRT → 区间+
  文本」的转换，拼接交给实战验证过的 `compose_rough_cut`（上游 `video_clip` 的
  `timestamp_list` 分支 = 毫秒区间 → `subclip` → `concatenate_videoclips`）。
- **越界不静默**（针对上游「第 2~N 段静默丢弃」缺陷）：end 超视频时长截到时长；
  整体越界条目丢弃并在返回值 `dropped` 计数。
- **相邻无缝合并**：相邻区间（缝隙 <1ms）合并成一段连续切片，不产生无谓切点。
- **随片字幕免费获得**：SRT 文本随行传入，成片自带时间轴平移后的 `.srt` 副产物。

## 验证

- `pytest tests/ -q` → **201 passed**（新增 5：`parse_srt` 标准/点号毫秒+CRLF+BOM+
  乱序/多行文本/非法块/小时位+短毫秒）；`ruff check` clean
- **拼接正确性硬核验证**（`work/REQ-20260916-020-srt-clip-concat/_service_test.py`）：
  合成 12s 源视频 = 6 段纯色（红/绿/蓝/黄/青/品红各 2s）+ 440Hz 音轨，成片
  **整片导出帧序列逐帧判色**：
  1. 乱序 SRT 3 段（0.5-2.5 / 4.5-6.5 / 9.5-11.5）→ 6.0s：段中点颜色严格正确
     （段序红→蓝→品红），5 个边界切换帧全部落在预期帧 ±2 帧内，音轨在，
     随片字幕首条平移到 `00:00:00,000`
  2. 相邻两段 [1.2,3.2]+[3.2,5.2] → 合并 1 段 4.0s，段内红→绿（帧10 精确）、
     绿→蓝（帧34 精确）两边界正确
  3. 越界：end 14s 截到 12s、整体越界 [15,16]s 丢弃且 `dropped=1`，成片 1.5s
  4. 守卫：非 SRT 内容 / 文件不存在 → 清晰报错
- **边界精度说明**：moviepy `subclip`+`concatenate` 的切点吸到帧网格上，边界有
  ≤1-2 帧抖动（合成测试 12fps 即 ≤167ms；真实 25/30fps 视频 ≈ 33-80ms）——
  上游合成方法的固有精度，对课程剪辑不可感知；段序与段长不受影响。

## 边界说明

- 不改 funclip/ 上游源码；不引入新依赖；未接 UI/端点（服务无需重启）——
  纯 service 层方法，供后续按需接入（如工作台「导入 SRT 重剪」入口）
- 与上游 LLM 入口同底层（`video_clip` timestamp_list 分支），但无 LLM 环节、
  无格式正则依赖、无静默丢段
- 任务数据只读；测试全部落 `work/`（gitignored）

## 关联

- 前置：REQ-20260916-018（上游 VideoClipper 合成方法封装 `compose_rough_cut`）
- 参考：同日上游「LLM 智能裁剪截取不符」根因排查（`work/_upstream_clip_algorithm_test.py`）
- 提交：`feat(compose): 按字幕文件时间段截取拼接方法 compose_from_srt (REQ-20260916-020)`
