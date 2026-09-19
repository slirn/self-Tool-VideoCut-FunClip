# REQ-20260920-083 — 精剪·系统默认 BGM 预览/导出没有声音 → 修复路径解析

## 背景

用户反馈：「生成预览时没有听到背景音乐」。

排查路径：
1. `/slirn/api/select_default_bgm` 选了 BGM → 写入 `fc.materials.audio.path = "materials/audio/lofi_beat_1.mp3"`，并把 mp3 复制到 `tasks/<tid>/materials/audio/lofi_beat_1.mp3`
2. `_assemble_fine_filter` 调用 `_resolve_mat_abs(mgr, tid, materials, "audio")` 解析路径
3. `_resolve_mat_abs` 候选路径：
   - **cand1 = `mgr.tasks_dir / pp` = `tasks/materials/audio/lofi_beat_1.mp3`**（缺 `<tid>` 段，NOT FOUND）
   - **cand2 = `mgr.repo_root / pp` = `materials/audio/lofi_beat_1.mp3`**（缺 `tasks/<tid>` 前缀，NOT FOUND）
4. 两个候选都不存在 → 走 `return cand1`（line 1684）→ 返回不存在的路径
5. `_assemble_fine_filter` line 1971 检查 `audio_path.exists()` → False → 走 fallback 分支（line 1973-1977）→ `audio_cfg["enabled"] = False`
6. 后续 `if audio_input_enabled and audio_idx >= 0:` 条件不满足 → 走 `[voice]anull[aout]` 路径 → **输出视频只有原说话人语音，没有 BGM**

### 根因

**路径约定不一致**：
- **上传**（`upload_fine_material_form`）写 `save_path.relative_to(repo_root)` = `tasks/<tid>/upload/<file>` （含 `tasks/<tid>` 前缀）
- **自动获取**（`compose_service.rough_compose_path` 等）写 `mgr.tasks_dir.relative_to(repo_root)` = `tasks/<tid>/outputs/...` （也含 `tasks/<tid>` 前缀，但拼错 — 实际是 `tasks/outputs/...`，不含 `<tid>`）
- **系统默认 BGM**（`select_default_bgm`）写 `materials/audio/<id>.mp3` —— **不含 `tasks/<tid>` 前缀** ❌

`_resolve_mat_abs` 只支持两种约定（无 `<tid>` 段 或 有完整 `tasks/<tid>/` 前缀），不支持第三种「`materials/...` 无前缀但文件实际在 `tasks/<tid>/materials/...`」的约定 → **找不到文件 → 静默禁用 audio**。

### 用户影响

- 选了系统默认 BGM → 「✅ 已选 BGM」提示成功
- 实际：预览和导出视频**只有原说话人语音，没有 BGM**
- fc.audio.enabled = True（看起来启用）
- 静默失败 —— 用户不知道为什么没声音

### 复现

```
1. 上传视频素材（确保有视频）
2. 勾选「启用背景音乐」
3. 展开上传区 → 「🎵 背景音乐」上传卡 → 「📦 系统默认 BGM」下拉 → 选某个 lo-fi
4. 看到「✅ 已选 BGM: ...」
5. 点「🎬 生成预览」
6. 预览播放 → 只有原说话人语音
```

## 目标

修复 `select_default_bgm` 写入的路径，让 `_resolve_mat_abs` 能正确解析到 `tasks/<tid>/materials/audio/<id>.mp3`。

## 影响范围

| 文件 | 改动 | 原因 |
|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | `select_default_bgm` line 5505：路径从 `materials/audio/<id>.mp3` 改为 `tasks/<tid>/materials/audio/<id>.mp3`（匹配 upload 约定） | 路径前缀约定统一 |
| [tests/test_workbench.py](tests/test_workbench.py) | 新增 2-3 个测试覆盖 select_default_bgm 路径正确性 + `_resolve_mat_abs` 能解析 + assemble_fine_filter 不静默禁用 audio | 回归保护 |
| [docs/REQM/REQ-20260920-083-bgm-path-resolver-mismatch.md](docs/REQM/REQ-20260920-083-bgm-path-resolver-mismatch.md) | 本文档 | 5 阶段 SOP 第 1 阶段 |
| [docs/design/DESIGN-20260920-083-bgm-path-resolver-mismatch.md](docs/design/DESIGN-20260920-083-bgm-path-resolver-mismatch.md) | 设计文档 | 第 2 阶段 |
| [docs/verification/VERIFICATION-20260920-083-bgm-path-resolver-mismatch.md](docs/verification/VERIFICATION-20260920-083-bgm-path-resolver-mismatch.md) | 验证文档 | 第 5 阶段 |

## 验收标准（10 条）

- **AC-1**：调 `/slirn/api/select_default_bgm` 后，`fc.materials.audio.path` 以 `tasks/<tid>/` 开头
- **AC-2**：调 `select_default_bgm` 后，`_resolve_mat_abs(mgr, tid, fc.materials, "audio")` 返回的 `Path.exists()` 为 True
- **AC-3**：调 `select_default_bgm` 后，`_assemble_fine_filter(...)` 返回的 `input_args` 包含 `-i` + 实际 mp3 绝对路径（**不再走 audio_cfg["enabled"] = False 分支**）
- **AC-4**：`_assemble_fine_filter(...)` 返回的 `filter_complex` 包含 `[bgm]` label + `amix=inputs=2`
- **AC-5**：ffmpeg 实际跑一遍 → 输出 mp4 有 audio stream（ffprobe 验证）
- **AC-6**：旧 fc（用户已选过 BGM 的任务）升级兼容：路径不带 `tasks/<tid>/` 前缀的旧数据，加载时**自动迁移**到新约定（**不破坏现有任务**）
- **AC-7**：现有所有测试 + 新增 2-3 个测试全过（571 → 573-574）
- **AC-8**：手动选 BGM → 预览 → 听到 BGM（端到端 e2e 验证）
- **AC-9**：手动选 BGM → 导出 → 听到 BGM（端到端 e2e 验证）
- **AC-10**：手动上传 mp3 → 预览 → 听到上传的 mp3（**不变回归**）

## 不做的事

- ❌ 不改 `_resolve_mat_abs` 的 fallback 链（保持现状；只改 select_default_bgm 写入符合现有约定的路径）
- ❌ 不改 upload 路径约定（`upload_fine_material_form` 已正确）
- ❌ 不改 compose_service 路径约定（不在本次 scope；如果有同样问题另开 REQ-084）
- ❌ 不做破坏性数据迁移（旧 fc.materials.audio.path 通过 AC-6 自动迁移，不改原文件位置）

## 命名约定

| 项 | 值 |
|---|---|
| REQ 文档 | `REQ-20260920-083-bgm-path-resolver-mismatch.md` |
| DESIGN 文档 | `DESIGN-20260920-083-bgm-path-resolver-mismatch.md` |
| VERIFICATION 文档 | `VERIFICATION-20260920-083-bgm-path-resolver-mismatch.md` |
| 修复后的 path 格式 | `tasks/<tid>/materials/audio/<bgm_id>.mp3` |
| 旧 path 兼容 | `_get_fine_compose` / `_resolve_mat_abs` 自动 fallback：尝试 `tasks/<tid>/<pp>` 候选 |

## Why

`services/mgr.tasks_dir` 与 `mgr.repo_root` 的相对路径有两种约定（自动获取 vs 上传），但 `_resolve_mat_abs` 只覆盖两种约定。`select_default_bgm` 写入的路径属于「第三种约定」（无前缀但文件实际在 `tasks/<tid>/` 下），导致 resolver 找不到 → audio 被静默禁用 → 用户没声音。

## How to apply

未来类似「写入 fc 素材路径」的功能复用本模式：
- 路径必须含 `tasks/<tid>/` 前缀（与 upload 约定一致）
- 文件实际位置 = `mgr.repo_root / path`
- `_resolve_mat_abs` 自动解析（cand2 命中）
- 不需要写「第 3 种约定」的代码
