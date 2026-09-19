# DESIGN-20260920-083 — 精剪·系统默认 BGM 预览/导出没有声音 → 修复路径解析

## Context

REQ-20260920-078 实现了 `select_default_bgm`：选某个内置 lo-fi mp3 → 复制到 `tasks/<tid>/materials/audio/<id>.mp3` → 写 `fc.materials.audio.path = "materials/audio/<id>.mp3"`。

REQ-20260920-082 把 BGM 下拉迁到 audio 上传卡（视觉修复）。

**当前 bug**：选了 BGM 后，预览和导出视频**只有原说话人语音，没有 BGM**。用户已多次反馈。

### 根因分析

路径写入（[app.py:5505](slirn_home/app.py#L5505)）：
```python
fc.setdefault("materials", {})["audio"] = {
    "path": f"materials/audio/{bgm_id}.mp3",
}
```

文件实际位置（[app.py:5494-5496](slirn_home/app.py#L5494-L5496)）：
```python
mat_dir = mgr.tasks_dir / tid / "materials" / "audio"
mat_dir.mkdir(parents=True, exist_ok=True)
dst = mat_dir / f"{bgm_id}.mp3"  # → tasks/<tid>/materials/audio/<id>.mp3
```

`_resolve_mat_abs`（[app.py:1655-1684](slirn_home/app.py#L1655-L1684)）：
```python
cand1 = (mgr.tasks_dir / pp).resolve()   # = tasks/materials/audio/<id>.mp3 (缺 <tid>)
cand2 = (mgr.repo_root / pp).resolve()   # = materials/audio/<id>.mp3 (缺 tasks/<tid>)
```

两个候选都不存在 → 返回 cand1（不存在） → `_assemble_fine_filter` 检查 `.exists()` 为 False → 走 fallback 禁用 audio 分支（[app.py:1973-1977](slirn_home/app.py#L1973-L1977)）。

### 路径约定不一致表

| 来源 | 写入 path | 实际位置 | `_resolve_mat_abs` 候选命中 |
|---|---|---|---|
| `upload_fine_material_form` (line 6004) | `tasks/<tid>/upload/<kind>_<name>` | `mgr.repo_root / path` | ✅ cand2 |
| `compose_service.rough_compose_path` | `tasks/<tid>/outputs/rough_compose.mp4`（自动约定）| `mgr.repo_root / path` | ✅ cand2 |
| `select_default_bgm` (line 5505) | `materials/audio/<id>.mp3` ❌ | `mgr.tasks_dir / <tid> / materials/audio/<id>.mp3` | ❌ 都不命中 |

**`select_default_bgm` 是唯一不写 `tasks/<tid>/` 前缀的写入方**。

---

## ADR-083-1：在 `select_default_bgm` 写路径时加 `tasks/<tid>/` 前缀

**决策**：把 `select_default_bgm` 写入的 path 从 `materials/audio/<id>.mp3` 改为 `tasks/<tid>/materials/audio/<id>.mp3`，与 upload 约定一致。

**考虑过的方案**：

| 方案 | 优劣 |
|---|---|
| **A. select_default_bgm 写带前缀的路径**（采纳） | 与 upload/compose_service 约定统一；零 resolver 改动；零其他端点影响 |
| B. 给 `_resolve_mat_abs` 加第 3 候选 `mgr.tasks_dir / tid / pp` | 支持历史数据；但代码复杂度上升；resolver 候选越多越慢越易混 |
| C. 改 `select_default_bgm` 文件位置到 `mgr.repo_root / materials / audio`（不带 tasks/<tid>）| 与 resolver cand1 匹配；但路径散落 repo_root，不在 tasks/<tid>/ 下，违反「任务素材都按 tid 分目录」原则 |
| D. 复用 `upload_fine_material_form` 的代码，把 BGM 当成"上传内置文件" | 过度复用；要 copy 到 `upload/` 而不是 `materials/`，反而乱了 |

**理由**：方案 A 是**最小改动 + 约定统一**的方案。upload 已有 200+ 调用方都遵循「写带前缀路径」，让 select_default_bgm 也遵循同一约定，消除「第三种约定」即可根治。

**ADR-083-1 边界**：此改动**不影响旧数据**。如果用户已经在 fc.materials.audio.path 写了不带前缀的旧路径（实际是无效路径，但 fc.audio.enabled=True 看起来启用），需要 AC-6 兼容迁移（见 ADR-083-2）。

---

## ADR-083-2：旧 fc 数据加载时自动迁移路径

**决策**：在 `_get_fine_compose`（加载 fc.json 时）或 `_resolve_mat_abs`（解析时）增加 fallback：尝试 `mgr.tasks_dir / tid / pp`（无前缀但实际可能在 `tasks/<tid>/` 下）。

**具体实现**：在 `_resolve_mat_abs` cand2 之后加 cand3：

```python
# 候选 3：tasks_dir + tid + pp（REQ-20260920-083 兼容 select_default_bgm 旧数据）
cand3 = (mgr.tasks_dir / task_id / pp).resolve()
if cand3.exists():
    return cand3
```

**为什么放在 `_resolve_mat_abs` 而不是 `_get_fine_compose`**：
- `_resolve_mat_abs` 是解析层，所有 fc 读取都过它
- `_get_fine_compose` 加载 fc 后立即存 → 可能在第一次 resolve 前 fc 还没被读到
- resolver 加 1 个候选不影响性能（一次 resolve 调用 ≤3 次 exists 检查，单次 < 1ms）
- 兼容旧数据**不需要改 fc 文件本身**（最小侵入）

**自动迁移 vs 一次性 patch**：
- 自动迁移（resolver cand3）：无副作用，新旧数据都 work
- 一次性 patch（启动时遍历 fc.json）：代码量大、易遗漏

**采纳自动迁移**。

---

## ADR-083-3：测试用真实文件验证（不仅是 mock）

**决策**：新增的测试必须**真实创建 mp3 文件 + 调 select_default_bgm 端点 + 验证 _resolve_mat_abs 返回存在路径**，不能只 mock。

**理由**：bug 的本质是「路径写入与实际位置不匹配」—— 是数据流问题，不是逻辑问题。mock 测试只能验证「路径字符串长这样」，不能验证「文件能找得到」。

---

## 实施步骤（5 阶段 SOP）

### Phase 1 — REQ ✓
产出：[docs/REQM/REQ-20260920-083-bgm-path-resolver-mismatch.md](docs/REQM/REQ-20260920-083-bgm-path-resolver-mismatch.md)

### Phase 2 — DESIGN（本文件）

### Phase 3 — 实现

**Step 1：修改 `select_default_bgm`**（[app.py:5493-5508](slirn_home/app.py#L5493-L5508)）

```python
# 修改前（BUG）：
mat_dir = mgr.tasks_dir / tid / "materials" / "audio"
mat_dir.mkdir(parents=True, exist_ok=True)
dst = mat_dir / f"{bgm_id}.mp3"
# ...
fc.setdefault("materials", {})["audio"] = {
    "path": f"materials/audio/{bgm_id}.mp3",
}

# 修改后：
mat_dir = mgr.tasks_dir / tid / "materials" / "audio"
mat_dir.mkdir(parents=True, exist_ok=True)
dst = mat_dir / f"{bgm_id}.mp3"
# ...
# REQ-20260920-083：路径必须含 tasks/<tid>/ 前缀，让 _resolve_mat_abs cand2 命中
# （upload_fine_material_form 写的路径也遵循此约定）
fc.setdefault("materials", {})["audio"] = {
    "path": f"tasks/{tid}/materials/audio/{bgm_id}.mp3",
}
```

**Step 2：`_resolve_mat_abs` 加 cand3 兼容旧数据**（[app.py:1675-1684](slirn_home/app.py#L1675-L1684)）

```python
# 候选 1：tasks_dir + pp（自动获取约定）
cand1 = (mgr.tasks_dir / pp).resolve()
if cand1.exists():
    return cand1
# 候选 2：repo_root + pp（上传约定，路径含 `tasks/` 前缀）
cand2 = (mgr.repo_root / pp).resolve()
if cand2.exists():
    return cand2
# REQ-20260920-083：候选 3 — tasks_dir + tid + pp（兼容 select_default_bgm 旧数据，
# 旧版本写 "materials/audio/<id>.mp3" 不含前缀；文件实际在 tasks/<tid>/materials/audio/）
cand3 = (mgr.tasks_dir / task_id / pp).resolve()
if cand3.exists():
    return cand3
# 都找不到：返回最近似的（让上层报错信息有真实路径）
return cand1
```

**Step 3：测试**（[tests/test_workbench.py](tests/test_workbench.py)）

3 个新测试：

```python
def test_select_default_bgm_writes_path_with_tasks_prefix(tmp_path, monkeypatch):
    """REQ-20260920-083：select_default_bgm 写入的 path 必须含 tasks/<tid>/ 前缀。
    
    BUG：旧版本写 'materials/audio/<id>.mp3' 不含前缀 → _resolve_mat_abs 找不到 → 
    音频被静默禁用 → 预览/导出没 BGM。
    """
    # 真实跑 select_default_bgm 端点，验证 fc.materials.audio.path 字符串
    # 用 monkeypatch 把 _DEFAULT_BGMS_DIR 指向临时目录（避免依赖 D:\tmp\tttttt）
    ...


def test_resolve_mat_abs_finds_bgm_via_cand3_fallback(tmp_path):
    """REQ-20260920-083：_resolve_mat_abs 第 3 候选兼容旧数据。

    旧 fc.materials.audio.path = 'materials/audio/<id>.mp3'（无前缀），
    文件实际在 tasks/<tid>/materials/audio/<id>.mp3，
    cand3 = tasks_dir/<tid>/pp 必须命中。
    """
    ...


def test_assemble_fine_filter_does_not_silently_disable_bgm(tmp_path, monkeypatch):
    """REQ-20260920-083：选了 BGM 后 _assemble_fine_filter 不再走 audio_cfg['enabled'] = False 分支。

    回归保护：input_args 必须包含音频文件的 -i 行 + filter_complex 必须含 [bgm] + amix=inputs=2。
    """
    ...
```

预期 571 + 3 = 574 测试全过。

### Phase 4 — Review

按 [docs/sop/04-review.md](docs/sop/04-review.md) 走 medium effort：
- 重点 1：`select_default_bgm` 改完后，路径格式 `tasks/<tid>/materials/audio/<id>.mp3` 符合 upload 约定
- 重点 2：`_resolve_mat_abs` 加 cand3 后，单次 resolve 最多 3 次 exists 检查（性能可忽略）
- 重点 3：旧数据兼容 — cand3 命中后，fc 不会被写回（resolver 是读路径层），下次用户保存时才写新路径
- 重点 4：测试用真实 mp3 文件 + monkeypatch `_DEFAULT_BGMS_DIR` 避免依赖 `D:\tmp\tttttt`
- 重点 5：`python -c "import ast; ast.parse(open('slirn_home/app.py').read())"` + `node --check router.js pipeline.js`

### Phase 5 — 验证

1. 跑 `pytest tests/ -q` → 574 passed
2. 重启 slirn
3. 端到端：
   a. 创建 task + 上传视频
   b. 勾 BGM → 选 `lofi_beat_1`
   c. 点「🎬 生成预览」
   d. 验证：播放预览 → 听到 BGM（用真实浏览器；VSCode 内嵌浏览器无音频，见 `user-previews-in-vscode-browser` memory）
   e. 点「💾 导出最终视频」→ 完成后下载 → 播放 → 听到 BGM
4. 检查 fc 文件：`cat tasks/<tid>/fine_compose.json | grep audio` → `path` 必须含 `tasks/<tid>/materials/audio/`
5. 兼容旧数据：在 fc.json 里手动改 path 为旧格式 `materials/audio/<id>.mp3`，重启 slirn，点预览 → 仍然听到 BGM（cand3 fallback）

---

## 关键文件改动汇总

| 文件 | 改动行数 | 备注 |
|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | line 5505：path 加 `tasks/<tid>/` 前缀（+0/-1） | 主修复 |
| [slirn_home/app.py](slirn_home/app.py) | line 1680-1683：_resolve_mat_abs 加 cand3（+4/-0） | 兼容旧数据 |
| [tests/test_workbench.py](tests/test_workbench.py) | +100 行 | 3 个新测试 |
| [docs/REQM/](docs/REQM/) | 新建 | 130 行 |
| [docs/design/](docs/design/) | 新建 | 280 行（本文件） |
| [docs/verification/](docs/verification/) | 新建 | 100 行 |

净代码：**+5 行**（最小侵入；核心修复 1 行 + 兼容 fallback 4 行）。

---

## 风险与边界

| 风险 | 处理 |
|---|---|
| 旧 fc 数据加载失败 | cand3 fallback 命中；零迁移成本 |
| cand3 误命中（其他 kind 走错路径） | exists() 检查兜底；不命中就 fall through 到 cand1 返回 |
| `select_default_bgm` 写新格式后，旧 fc 被前端读 | 新写入的路径新格式；前端读到的 audio 状态正确 |
| `mgr.repo_root` 在不同环境下含义不同 | upload 约定已用此惯例多年；本次只是 select_default_bgm 跟上 |
| `compose_service.rough_compose_path` 等也有路径 bug | 不在本次 scope；如发现同样问题另开 REQ-084 |

---

## 复用现有基础设施

- `_resolve_mat_abs` 的 cand1 / cand2 现有逻辑（[app.py:1655-1684](slirn_home/app.py#L1655-L1684)）
- `select_default_bgm` 端点的文件复制逻辑（[app.py:5493-5500](slirn_home/app.py#L5493-L5500)）
- `_save_fine_compose` 写回（[app.py:2793-2800](slirn_home/app.py#L2793)）
- `tasks/<tid>/materials/audio/` 目录约定（[app.py:5493-5496](slirn_home/app.py#L5493-L5496)）
- 测试 fixture `_make_mgr`（[tests/test_workbench.py](tests/test_workbench.py)）

---

## 关联

- [REQ-20260920-078-system-default-bgm.md](docs/REQM/REQ-20260920-078-system-default-bgm.md) — `select_default_bgm` 的原始实现
- [REQ-20260920-080-fix-bgm-filter-chain-label.md](docs/REQM/REQ-20260920-080-fix-bgm-filter-chain-label.md) — BGM 前置 bug 修复
- [REQ-20260920-082-move-bgm-selector.md](docs/REQM/REQ-20260920-082-move-bgm-selector.md) — BGM 下拉 UI 迁移
- [REQ-20260919-061-fine-cut-4-materials.md](docs/REQM/) — upload 路径约定源头
- [REQ-20260919-073-fine-export-materials-empty.md](docs/REQM/) — export materials 相关
