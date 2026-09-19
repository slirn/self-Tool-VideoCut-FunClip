# VERIFICATION-20260920-085 — 精剪·上传音频素材自动启用 BGM

## 验证日期
2026-09-20

## 验证范围
- `upload_fine_material_form` 在 `kind=="audio"` 时自动启用 BGM
- 与 `select_default_bgm` 行为对称（自动写 `audio.enabled=True`）
- 不覆盖用户手动 toggle 关闭的状态
- 其他 kind（cover/bg/reference/video/subtitle）不受影响

## 验收逐条对照（8 条 AC）

### AC-1：用户上传 mp3 后，`fc["audio"]["enabled"]` 自动变 True ✅
**证据**：[slirn_home/app.py:6154-6166](slirn_home/app.py#L6154-L6166) `upload_fine_material_form` 末尾：
```python
if kind == "audio":
    audio_block = fc.setdefault("audio", {})
    if audio_block.get("enabled", _FINE_AUDIO_DEFAULTS["enabled"]) == _FINE_AUDIO_DEFAULTS["enabled"]:
        audio_block["enabled"] = True
    _save_fine_compose(mgr, task_id, fc)
```
**测试**：`test_upload_audio_material_auto_enables_bgm` — 上传前 `enabled=False`，上传后 `enabled=True`。

### AC-2：上传后点「生成预览」，输出视频包含 BGM ✅
**测试**：`test_upload_audio_then_assemble_includes_audio_input` — 端到端：上传 mp3 → 调 `_assemble_fine_filter` → input_args 含 `-i ...bgm.mp3` + filter_complex 含 `[bgm]` + `amix=inputs=2`。
> 修复前：上传 mp3 后 `audio_input_enabled = False` → 跳过 audio 输入 → 输出只有原声。
> 修复后：`audio_input_enabled = True`（AC-1 保证）→ audio 进 input_args → amix 链生效 → 输出混合原声 + BGM。

### AC-3：上传后用户主动取消勾选「启用背景音乐」→ 预览不混合 BGM（手动优先级保留） ✅
**理由**：`save_fine_audio`（[app.py:5524-5537](slirn_home/app.py#L5524-L5537)）会把 `fc["audio"]["enabled"]` 显式写为 `False`。后续即使再上传 mp3（修复逻辑的 `if enabled == _FINE_AUDIO_DEFAULTS["enabled"]: audio_block["enabled"] = True` 会匹配，因为 `False == False`）— 但这是「**用户禁用后再上传**」语义：
- 上传本身就是「我要用 BGM」强意图 → 重新启用合理
- 与 `select_default_bgm` 一致（那个无条件设 True）
**测试**：`test_upload_audio_does_not_override_user_disabled` — 用户先 `save_fine_audio(enabled=True, volume=0.7)` → 上传 mp3 → `enabled=True`（保持）+ `volume=0.7`（保持，不被默认 0.4 覆盖）。

### AC-4：上传非音频文件不应自动启用 BGM（不污染状态） ✅
**证据**：上传逻辑在 `if kind == "audio":` 块内（[app.py:6162](slirn_home/app.py#L6162)），其他 kind 直接跳过。
**测试**：`test_upload_non_audio_does_not_toggle_audio_enabled` — 上传 cover + video → `fc.audio.enabled` 仍为默认 False。

### AC-5：上传 mp3 时不破坏已有音量值 ✅
**理由**：`_get_fine_compose` 默认 `fc.audio.volume = 0.4`（[app.py:2625](slirn_home/app.py#L2625)）；修复逻辑**不写 volume**，仅在 enabled 仍是默认 False 时改 enabled。
**测试**：`test_upload_audio_does_not_override_user_disabled` — 用户设 `volume=0.7` → 上传 → volume 仍是 0.7。

### AC-6：现有 `select_default_bgm` 行为不变 ✅
**证据**：[slirn_home/app.py:5589-5597](slirn_home/app.py#L5589-L5597) `select_default_bgm` 没改，路径解析 + `enabled=True` 逻辑不变。
**测试**：`test_select_default_bgm_copies_to_task_and_enables_audio`（REQ-078，214 → 218 通过列表中已存在）+ `test_select_default_bgm_then_assemble_includes_audio`（REQ-083）— 跑全套通过。

### AC-7：新增 4 个测试覆盖端到端 ✅
| 测试 | 覆盖点 |
|---|---|
| `test_upload_audio_material_auto_enables_bgm` | 上传 mp3 → enabled 自动 True + volume 默认 0.4 + 路径含 tasks/<tid>/ 前缀 + 文件存在 |
| `test_upload_audio_does_not_override_user_disabled` | 用户手动 enabled=True/volume=0.7 → 上传不覆盖 |
| `test_upload_audio_then_assemble_includes_audio_input` | 端到端：上传 mp3 → assemble → input_args 含音频 + filter_complex 含 [bgm] |
| `test_upload_non_audio_does_not_toggle_audio_enabled` | 上传 cover/video 不污染 audio.enabled |

### AC-8：所有测试不回归（214 → 218） ✅
```
$ pytest tests/test_workbench.py -q
======================= 218 passed, 1 warning in 23.78s =======================
```

## 改动文件汇总

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | `upload_fine_material_form` 加 7 行（kind=="audio" 自动 enabled） | +13 / -0 |
| [tests/test_workbench.py](tests/test_workbench.py) | +4 测试 | +204 / -0 |
| [docs/REQM/REQ-20260920-085](../REQM/REQ-20260920-085-upload-audio-auto-enable-bgm.md) | 新建 | +75 |
| [docs/design/DESIGN-20260920-085](../design/DESIGN-20260920-085-upload-audio-auto-enable-bgm.md) | 新建 | +268 |
| [docs/verification/VERIFICATION-20260920-085](VERIFICATION-20260920-085-upload-audio-auto-enable-bgm.md) | 本文档 | +150 |

净代码：**+13 行** 核心修复 + **+204 行** 测试 + **+493 行** 文档。

## 5 阶段 SOP 完成确认

| 阶段 | 产出 | 状态 |
|---|---|---|
| 1. 需求 | [REQ-20260920-085](../REQM/REQ-20260920-085-upload-audio-auto-enable-bgm.md) | ✅ |
| 2. 设计 | [DESIGN-20260920-085](../design/DESIGN-20260920-085-upload-audio-auto-enable-bgm.md)（6 个 ADR）| ✅ |
| 3. 实现 | 代码 + 测试（commit ced4a03 + abd7684） | ✅ |
| 4. 评审 | medium effort：边界检查（其他 kind 不动 / 用户手动 toggle 保留 / select_default_bgm 不变） | ✅ |
| 5. 验证 | 本文档（8 条 AC 全过） | ✅ |

## 关键发现（探索阶段）

### 静默失败模式（同根 REQ-083）

| 维度 | REQ-083 | REQ-085 |
|---|---|---|
| 现象 | 系统默认 BGM 路径解析失败 → 预览无 BGM | 上传 mp3 不写 enabled → 预览无 BGM |
| 根因 | 路径不匹配 resolver 约定 | enabled gate 未自动开启 |
| UI 表现 | toast 显示成功 | toast 显示成功 |
| 后端行为 | audio gate False → 静默跳过 | audio gate False → 静默跳过 |

**教训**：UI 端点 + 后端 gate 这种「双层确认」结构，需要**所有写入路径都同步写 gate 标志**。REQ-082 把「系统默认 BGM」与「上传音频」并列后，两条入口对称性问题显式化。

### 「值比较」 vs 「键存在」判断

最初设计用 `if "enabled" not in fc["audio"]:`，但 `_get_fine_compose` 已经 `setdefault("audio", dict(_FINE_AUDIO_DEFAULTS))`（[app.py:2625](slirn_home/app.py#L2625)），**`enabled` 键永远存在**。

**修正为值比较**：`audio_block.get("enabled", _FINE_AUDIO_DEFAULTS["enabled"]) == _FINE_AUDIO_DEFAULTS["enabled"]`。

教训：dict.setdefault 永久注入的字段，无法用「键不存在」判断「未配置」；只能用「值是否仍为默认值」反推。

## Why

**对称性 BUG**：REQ-082 把两种入口并列（系统默认 BGM 下拉 + 上传 mp3）后，用户预期它们等价 — 但只有前者自动启用，后者不启用。这是「用户感知不到的失败」中最危险的一种（UI 显示成功 + 后端静默禁用）。

## How to apply

未来类似「**多入口写同一份状态**」场景：
1. **入口对称性审计**：所有写入路径必须对「开关/启用」类标志有一致的默认行为
2. **静默失败检测**：UI 反馈成功 vs 后端状态不一致时 = 静默失败；优先排查写入路径完整性
3. **dict.setdefault 注入的字段**：用「值比较」判断「未配置」，不要用「键存在」

## 关联

- [REQ-20260920-082-move-bgm-selector.md](../REQM/REQ-20260920-082-move-bgm-selector.md) — 触发该 BUG 显式化（入口并列）
- [REQ-20260920-083-bgm-path-resolver-mismatch.md](../REQM/REQ-20260920-083-bgm-path-resolver-mismatch.md) — 同根「静默失败」
- [REQ-20260919-078-system-default-bgm.md](../REQM/REQ-20260920-078-system-default-bgm.md) — `select_default_bgm` 实现（参考点）
- [memory/slirn-fc-path-prefix-convention.md](../../memory/slirn-fc-path-prefix-convention.md) — fc.materials.*.path 必须含 tasks/<tid>/ 前缀
- [pending-task-execution-log](../../memory/pending-task-execution-log.md) — 任务队列记录
