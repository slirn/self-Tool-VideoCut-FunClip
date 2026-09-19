# REQ-20260920-085 — 精剪·上传音频素材未自动启用 BGM

## 背景

用户在「视频精简」阶段直接上传 MP3/WAV/M4A 作为背景音乐，点「生成预览」后**最终视频里没有 BGM**。

### 现象

- 上传 mp3 文件 → UI toast「🎵 背景音乐已上传」✅
- 点「生成预览」 → 视频生成成功
- 听结果 → **没有 BGM，只有原说话人语音**

### 复现步骤

1. 创建 task + 上传视频（要求有原声）
2. 进入「视频精简」面板
3. 在「🎵 背景音乐」上传卡选 mp3 文件上传 → 看到「已上传」
4. 勾选 / 不勾选 「启用背景音乐」复选框
5. 点「生成预览」→ 等待完成
6. **期望**：听到原声 + BGM 混合；**实际**：只有原声

### 业务影响

- 用户感知不到「为什么我上传的 BGM 没生效」
- 同一任务路径下「系统默认 BGM」dropdown 走 `select_default_bgm` endpoint，自动启用 → 能听见 BGM
- 用户用上传方式就听不到 → 体验不一致
- 静默失败（UI 显示成功 + 后端禁用）= 最危险的失败模式（与 REQ-083 同根）

## 根因

[slirn_home/app.py:6147-6153](slirn_home/app.py#L6147-L6153) `upload_fine_material_form` 只写 `materials.audio.path`，**不设置 `audio.enabled = True`**：

```python
fc = _get_fine_compose(mgr, task_id)
fc["materials"][kind] = {
    "path": str(save_path.relative_to(repo_root)),
    "type": {"video": "video", "subtitle": "srt", "audio": "audio"}.get(kind, "image"),
    "source": "upload",
}
_save_fine_compose(mgr, task_id, fc)
# ↑ 缺少：fc["audio"]["enabled"] = True  (当 kind == "audio")
```

下游 `_assemble_fine_filter`（[app.py:1970-1974](slirn_home/app.py#L1970-L1974)）的 gate：

```python
audio_cfg = fc.get("audio") or _FINE_AUDIO_DEFAULTS  # 默认 enabled=False
audio_input_enabled = (
    audio_cfg.get("enabled")           # ← False（用户没手动勾）
    and (materials.get("audio") or {}).get("path")
)
```

→ `audio_input_enabled = False` → 跳过 `-i audio.mp3` → 后续 `amix` / `aloop` 标签不接 → 输出只有原声。

对比 `select_default_bgm`（[app.py:5589-5597](slirn_home/app.py#L5589-L5597)）会主动设置 `audio.enabled=True`：

```python
fc.setdefault("materials", {})["audio"] = {
    "path": f"tasks/{tid}/materials/audio/{bgm_id}.mp3",
}
fc.setdefault("audio", {})["enabled"] = True   # ← 自动启用
```

### 为什么之前没发现

- 之前测试用 `select_default_bgm` 选系统默认 BGM → 走自动启用分支 → BGM 有效
- 用户上传 MP3 是个「次要路径」（UI 提示「或上传 mp3/wav/m4a 文件」），早期没专门测
- REQ-082 把「系统默认 BGM」按钮搬到 audio 上传卡 → 现在两种路径**对用户行为并列** → 静默失败更显眼

## 验收标准

| ID | 描述 | 优先级 |
|---|---|---|
| AC-1 | 用户上传 mp3 后，`fc["audio"]["enabled"]` 自动变 True | P0 |
| AC-2 | 上传后点「生成预览」，**输出视频包含 BGM**（与原声混合） | P0 |
| AC-3 | 上传后如果用户**主动取消勾选**「启用背景音乐」，预览应不混合 BGM（手动优先级 > 自动） | P0 |
| AC-4 | 上传非音频文件（如图片）**不应**自动启用 BGM（不污染状态） | P0 |
| AC-5 | 上传 mp3 时不破坏已有音量值（默认 `volume=0.4` 由 `_get_fine_compose` 兜底） | P1 |
| AC-6 | 现有 `select_default_bgm` 行为不变（路径解析、enabled=True） | P0 |
| AC-7 | 新增 1+ 测试覆盖「上传 → enabled=True + 预览有 BGM」端到端 | P0 |
| AC-8 | 所有现有测试不回归（214 → ≥215） | P0 |

## 范围

- 改：`upload_fine_material_form`（[app.py:6121-6174](slirn_home/app.py#L6121-L6174)）
- 加测试：`tests/test_workbench.py`
- 文档：本 REQ + DESIGN + VERIFICATION
- **不改**：前端 UI（无需新控件）、`_assemble_fine_filter`（已支持 enabled gate）、`save_fine_audio`（保留手动覆盖路径）

## 不做什么

- ❌ 不强制覆盖用户手动选择：若用户已勾「禁用」，上传不强行打开
- ❌ 不改 `_FINE_AUDIO_DEFAULTS`：默认值仍是 disabled（需用户/上传显式启用）
- ❌ 不增加新 API：复用 `upload_fine_material_form` 一个端点

## 关联

- [REQ-20260920-082-move-bgm-selector.md](REQ-20260920-082-move-bgm-selector.md) — 触发该 BUG 显式化（默认 BGM 按钮搬过来）
- [REQ-20260920-083-bgm-path-resolver-mismatch.md](REQ-20260920-083-bgm-path-resolver-mismatch.md) — 同根「静默失败」教训
- [REQ-20260919-078-system-default-bgm.md](REQ-20260920-078-system-default-bgm.md) — 系统默认 BGM 实现（对比自动启用逻辑）
- [memory/slirn-fc-path-prefix-convention.md](../../memory/slirn-fc-path-prefix-convention.md) — fc.materials.*.path 必须含 tasks/<tid>/ 前缀
