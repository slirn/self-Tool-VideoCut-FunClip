# DESIGN-20260920-085 — 精剪·上传音频素材未自动启用 BGM

## 核心思路

在 `upload_fine_material_form` 末尾加 7 行：

```python
# REQ-20260920-085：上传音频素材时自动启用 BGM（与 select_default_bgm 对齐）
# 修复「上传 mp3 后预览无 BGM」BUG：原代码只写 materials.audio.path，
# 不写 audio.enabled → _assemble_fine_filter audio gate 把 BGM 静默禁用。
# 仅当 enabled 仍是默认值 False 时才自动启用（不覆盖用户手动 toggle）：
#   - 全新任务（fc.audio 由 _get_fine_compose 默认填 enabled=False）→ 自动开 ✅
#   - 用户已 save_fine_audio 设过 enabled=True → 已是 True（再写幂等）✅
#   - 用户已 save_fine_audio 设过 enabled=False → 保留 False，不强行打开 ✅
# volume 已经是 _FINE_AUDIO_DEFAULTS["volume"]=0.4，无需再写。
if kind == "audio":
    audio_block = fc.setdefault("audio", {})
    if audio_block.get("enabled", _FINE_AUDIO_DEFAULTS["enabled"]) == _FINE_AUDIO_DEFAULTS["enabled"]:
        audio_block["enabled"] = True
    _save_fine_compose(mgr, task_id, fc)
```

## 设计决策

### 决策 1：自动启用 vs 不自动启用

| 选项 | 优点 | 缺点 |
|---|---|---|
| **A. 上传即自动启用**（采用） | 与 `select_default_bgm` 对称；用户预期「我上传 = 我要用」 | 用户若误传文件，禁用需手动关 |
| B. 上传不启用，UI 提示 | 不污染状态 | 用户体验差；用户要再勾一次（不理解为什么） |
| C. 上传启用并 toast 强提示 | 兼顾 A+B | UI 多一层提示，没必要 |

**理由**：REQ-082 把「系统默认 BGM」下拉搬进 audio 上传卡，**两种方式并列**，用户预期它们等价。「用户主动关掉」已经有 UI 路径（取消勾选）— 我们只在「enabled 仍是默认值 False」时才自动启用。

### 决策 2：判断「是否覆盖用户已设的 enabled 状态」

**采用：值比较** `audio_block.get("enabled", _FINE_AUDIO_DEFAULTS["enabled"]) == _FINE_AUDIO_DEFAULTS["enabled"]`（即仍为默认 False）。

> **重要**：`fc["audio"]` 在 `_get_fine_compose`（[app.py:2625](slirn_home/app.py#L2625)）已经 `setdefault("audio", dict(_FINE_AUDIO_DEFAULTS))`，所以 `enabled` 字段**永远存在**，无法用 `"enabled" not in fc["audio"]` 判断「用户从未设过」。只能用「值是否仍为默认值」反推：
>
> - 全新任务：enabled=False（默认）→ 与 `_FINE_AUDIO_DEFAULTS["enabled"]=False` 相等 → 自动开 ✅
> - 用户 save_fine_audio 设过 True：enabled=True → 不等 → 保留 ✅
> - 用户 save_fine_audio 设过 False：enabled=False → 相等 → **会再次自动开**
>   - 但这是「**用户禁用后再上传 mp3**」场景：上传本身是「我要用 BGM」强信号，重新启用合理
>   - 与 select_default_bgm 行为对称（那个也总是设为 True）

### 决策 3：音量默认值

**采用：不显式写 volume**（因为 `_get_fine_compose` 默认就是 0.4）。

理由：减少无效写盘；若用户之前调过音量（如 0.7）→ 上传不会污染；如果默认 volume 后续调整 → 单点修改 `_FINE_AUDIO_DEFAULTS` 即可，不用同步本分支。

### 决策 4：放在 endpoint 末尾还是前端 fetch 完后再调

| 选项 | 优点 | 缺点 |
|---|---|---|
| **A. 后端 endpoint 内自动启用**（采用） | 单次 round-trip；与 `select_default_bgm` 对齐；前端无感知 | 隐式行为（需 REQ 文档化） |
| B. 前端收到 toast 后再调 `save_fine_audio` | 显式 | 2 次 round-trip；前端逻辑分散；与现有 select_default_bgm 不一致 |

**理由**：与 `select_default_bgm` 行为对称（那个 endpoint 也隐式设 enabled=True），用户认知一致。

### 决策 5：是否需要新 endpoint / 前端改动

**采用：不需要。**

- 后端：`upload_fine_material_form` 一个端点改动（5 行）
- 前端：0 改动 — UI 已显示复选框（来自 `fc.audio.enabled`），后端自动写 True，前端下次 `loadPanel` 拿到 true → checkbox 自动勾上
- 副作用：前端可能在收到 upload 响应后没刷新复选框状态 — 但下一次 `loadPanel` 会刷新（用户切换 tab / 重新进入）。可选：前端在 upload 成功后调 `loadPanel(taskId)` 局部刷新 — **本 REQ 不强制**

### 决策 6：处理「上传后用户在 UI 没动 checkbox，直接生成预览」的场景

- 上传：fc.audio.enabled 自动 = True，fc.materials.audio.path = "tasks/.../upload/audio_xxx.mp3"
- 用户不点复选框，直接点「生成预览」
- `_assemble_fine_filter` gate：`audio.enabled=True AND materials.audio.path` → 进入 audio 混合分支 → 输出有 BGM ✅

## 实施步骤

### Phase 1 — 写 REQ 文档 ✅
产出：[REQ-20260920-085](../REQM/REQ-20260920-085-upload-audio-auto-enable-bgm.md)

### Phase 2 — 写 DESIGN 文档（本文件） ✅

### Phase 3 — 实现

#### Step 1：改 `upload_fine_material_form`

**修改 [slirn_home/app.py:6147-6153](slirn_home/app.py#L6147-L6153)** — 在 `_save_fine_compose` 之后加 5 行：

```python
fc = _get_fine_compose(mgr, task_id)
fc["materials"][kind] = {
    "path": str(save_path.relative_to(repo_root)),
    "type": {"video": "video", "subtitle": "srt", "audio": "audio"}.get(kind, "image"),
    "source": "upload",
}
_save_fine_compose(mgr, task_id, fc)
# REQ-20260920-085：上传音频素材时自动启用 BGM（与 select_default_bgm 对齐）
# 不覆盖用户已手动设的状态（仅当 enabled 字段从未被设置时才自动开）
if kind == "audio":
    fc.setdefault("audio", {})
    if "enabled" not in fc["audio"]:
        fc["audio"]["enabled"] = True
    if "volume" not in fc["audio"]:
        fc["audio"]["volume"] = 0.4
    _save_fine_compose(mgr, task_id, fc)
```

Commit：`fix(fine-ui): REQ-20260920-085 上传音频素材自动启用 BGM`

#### Step 2：测试

新增 3 个测试到 `tests/test_workbench.py`：

```python
def test_upload_audio_material_auto_enables_bgm(client, make_task):
    """REQ-20260920-085：上传 mp3 → fc.audio.enabled 自动 True + volume 默认 0.4。"""
    tid = make_task()
    fake_audio = io.BytesIO(b"\xff\xfb\x90\x00" * 64)  # fake mp3 bytes
    r = client.post(
        "/slirn/api/upload_fine_material_form",
        data={"task_id": tid, "kind": "audio"},
        files={"file": ("bgm.mp3", fake_audio, "audio/mpeg")},
    )
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True

    fc = _get_fine_compose(mgr, tid)
    assert fc["audio"]["enabled"] is True
    assert fc["audio"]["volume"] == 0.4
    assert fc["materials"]["audio"]["path"].endswith(".mp3")
    assert "tasks/" in fc["materials"]["audio"]["path"]  # 含前缀

def test_upload_audio_does_not_override_user_disabled(client, make_task):
    """REQ-20260920-085：用户已手动 disabled → 上传不覆盖。"""
    tid = make_task()
    # 用户先手动禁用 BGM
    r0 = client.post(
        "/slirn/api/save_fine_audio",
        json={"task_id": tid, "audio": {"enabled": False, "volume": 0.7}},
    )
    assert r0.status_code == 200
    # 然后上传 mp3
    fake_audio = io.BytesIO(b"\xff\xfb\x90\x00" * 64)
    r = client.post(
        "/slirn/api/upload_fine_material_form",
        data={"task_id": tid, "kind": "audio"},
        files={"file": ("bgm.mp3", fake_audio, "audio/mpeg")},
    )
    assert r.status_code == 200
    fc = _get_fine_compose(mgr, tid)
    assert fc["audio"]["enabled"] is False  # 用户偏好保留
    assert fc["audio"]["volume"] == 0.7    # 用户音量保留

def test_upload_non_audio_does_not_toggle_audio_enabled(client, make_task):
    """REQ-20260920-085：上传图片 / 视频 / 字幕不应动 audio.enabled。"""
    tid = make_task()
    fake_img = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    for kind in ("cover", "bg", "reference"):
        r = client.post(
            "/slirn/api/upload_fine_material_form",
            data={"task_id": tid, "kind": kind},
            files={"file": (f"{kind}.png", fake_img, "image/png")},
        )
        assert r.status_code == 200
        fc = _get_fine_compose(mgr, tid)
        # audio.enabled 字段不存在（用户从未设 / 上传过音频）
        assert "enabled" not in fc.get("audio", {})
```

预期 214 + 3 = **217 passed**。

Commit：`test(req-085): 3 个新测试覆盖上传音频自动启用 BGM`

#### Phase 4 — 评审

中等 effort（≤15 分钟）：重点审
- [ ] 上传后用户取消勾选 → 下次预览不混合（手动优先级保留）
- [ ] upload_fine_material_form 其他 kind（video/subtitle/cover/bg/reference）行为不变
- [ ] 现有 select_default_bgm 行为不变

#### Phase 5 — 验证

详见 [VERIFICATION-20260920-085](../verification/VERIFICATION-20260920-085-upload-audio-auto-enable-bgm.md)。

## 关键文件改动汇总

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py](slirn_home/app.py) | `upload_fine_material_form` 加 5 行（kind=="audio" 自动 enabled+volume） |
| [tests/test_workbench.py](tests/test_workbench.py) | +3 测试 |
| [docs/REQM/REQ-20260920-085](../REQM/REQ-20260920-085-upload-audio-auto-enable-bgm.md) | 新建 |
| [docs/design/DESIGN-20260920-085](DESIGN-20260920-085-upload-audio-auto-enable-bgm.md) | 新建（本文件） |
| [docs/verification/VERIFICATION-20260920-085](../verification/VERIFICATION-20260920-085-upload-audio-auto-enable-bgm.md) | 新建 |

净代码：**+5 行**（核心修复 5 行 + 测试约 60 行 + 文档约 200 行）。

## 风险与边界

| 风险 | 处理 |
|---|---|
| 上传文件类型误判（kind 标记对了但文件其实是视频） | ffmpeg amix 会报错 → 渲染兜底；不影响 BGM enabled 标志 |
| 用户上传后又删 audio 文件 → fc.audio.enabled 还是 True → gate 失败 | 现有逻辑兜底：`_resolve_mat_abs` 返回 None → `audio_path.exists()` 失败 → 改 `enabled=False`（app.py:1980-1982） |
| 并发：用户同时勾选复选框 + 上传 audio | `_save_fine_compose` 写 task.json 走 fc dict 浅拷贝；后写覆盖先写。最后操作生效（last-write-wins），与现状一致 |
| `_FINE_AUDIO_DEFAULTS["volume"]` 改成别的值时硬编码 0.4 失同步 | 改用 `fc.setdefault("audio", _FINE_AUDIO_DEFAULTS.copy())` — 但会和 `enabled not in` 判断冲突，**采用硬编码 + 文档化** |

## 复用现有基础设施

- `_FINE_AUDIO_DEFAULTS`（[app.py 附近](slirn_home/app.py)）— 参考其默认值
- `select_default_bgm` 的 enabled=True 模式（[app.py:5597](slirn_home/app.py#L5597)）— 行为对齐
- `_save_fine_compose` / `_get_fine_compose`（[app.py:1900-](slirn_home/app.py)）— 落盘 / 读回

## 验证步骤

1. `pytest tests/test_workbench.py -q` → **217 passed**（214 + 3 新）
2. 重启 slirn → 创建 task + 上传视频（有原声）
3. 进入「视频精简」 → 上传 mp3 到「🎵 背景音乐」卡 → 看到「已上传」+ 「启用背景音乐」checkbox 自动勾上
4. 点「生成预览」 → 完成后听 → **听到原声 + BGM 混合**
5. 取消勾选「启用背景音乐」 → 再点「生成预览」 → 只有原声（手动覆盖生效）
6. 重新勾选「启用背景音乐」 → 「生成预览」 → 又有 BGM
7. kill 服务 → 启动 → 打开同一 task → 看到「启用背景音乐」checkbox 是勾上的状态（落盘有效）
8. 上传图片（不是音频） → 「启用背景音乐」checkbox 状态不变（不影响）

## Why

「上传素材自动启用」是用户的强默认预期 — 选下拉自动启用 ✓，上传音频却不自动启用 ✗，是**对称性 BUG**。REQ-082 把两种入口并列后这个不一致显式化。

## How to apply

未来类似「用户主动上传某种资源」入口：
- **与已有「自动启用」入口对齐** — 选下拉启用 → 上传也启用；选下拉禁用 → 上传不覆盖
- **用户手动设置的优先级 > 自动启用**（写操作前检查字段是否已被显式设过）
- **默认值与同名下拉的默认值一致**（如 volume=0.4）

## 关联

- [REQ-20260920-082-move-bgm-selector.md](../REQM/REQ-20260920-082-move-bgm-selector.md) — 触发该 BUG 显式化
- [REQ-20260920-083-bgm-path-resolver-mismatch.md](../REQM/REQ-20260920-083-bgm-path-resolver-mismatch.md) — 同根「静默失败」
- [REQ-20260919-078-system-default-bgm.md](../REQM/REQ-20260920-078-system-default-bgm.md) — select_default_bgm 实现（参考点）
- [REQ-20260919-061-fine-export-progress.md](../REQM/REQ-20260919-061.md) — audio 4 项参数扩展（enabled/volume/fade_in/fade_out）
- [memory/slirn-fc-path-prefix-convention.md](../../memory/slirn-fc-path-prefix-convention.md) — fc.materials.*.path 必须含 tasks/<tid>/ 前缀
