"""REQ-20260922-NNN 标记删除行 → 优化成片剪辑 — 端点与接线测试。

覆盖：
- /save_optimize_subtitle 保存钩子（kick 剪辑 / running 不二启 / 空标记清产物）
- /optimize_cut_status 服务重启后的磁盘兜底
- /optimized_srt base=rough|cut 两种时间基
- _fine_upstream_path / _effective_fine_video_path：优化成片优先 + 失效回退
- _assemble_fine_filter：auto 字幕素材内存直供（时间基跟随实际视频）
- 渲染：🗑️ 按钮 / 徽章 / 统计 / 剪辑状态条
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in sys.path:
    sys.path.insert(0, str(SLIRN_STANDALONE))

from slirn_home import compose_service, optimize_service

SEGS = [
    {"i": 1, "start_ms": 0, "end_ms": 2000, "start": "00:00:00,000",
     "end": "00:00:02,000", "text": "第一行废话"},
    {"i": 2, "start_ms": 3000, "end_ms": 5000, "start": "00:00:03,000",
     "end": "00:00:05,000", "text": "第二行正经"},
    {"i": 3, "start_ms": 6000, "end_ms": 8000, "start": "00:00:06,000",
     "end": "00:00:08,000", "text": "第三行正经"},
]


def _make_task(tmp_path: Path):
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake-video")
    m = TaskManager(tmp_path)
    t = m.create(name="opt-cut", original_video=video)
    outputs = m.tasks_dir / t.task_id / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    return m, t, outputs


def _write_optimize(outputs: Path, *, saved=True, marks=None):
    data = {
        "version": 1, "video": "rough_compose.mp4", "model": "m", "provider": "p",
        "protocol": "openai", "created_at": "2026-09-22T10:00:00",
        "saved_at": "2026-09-22T11:00:00" if saved else None,
        "segments": SEGS, "occurrences": [],
        "words": [], "mapping": [], "stats": {"lines": 3, "occurrences": 0},
    }
    if marks is not None:
        data["line_marks"] = marks
    (outputs / optimize_service.OPTIMIZE_JSON).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _mk_rough(outputs: Path):
    rough = compose_service.rough_compose_path(outputs)
    rough.write_bytes(b"rough")
    return rough


def _mk_cut_artifacts(outputs: Path, marks, *, newer_than_rough=True):
    """造一套「就绪」的优化成片产物（mp4 + srt + sidecar）。"""
    import os

    rough = _mk_rough(outputs)
    mp4 = compose_service.optimize_compose_path(outputs)
    mp4.write_bytes(b"cut")
    (outputs / "optimize_compose.srt").write_text("1\nx", encoding="utf-8")
    (outputs / compose_service.OPTIMIZE_CUT_JSON).write_text(json.dumps(
        {"marks": sorted(marks), "deleted_ms": 2000, "deleted_sec": 2.0,
         "kept_sec": 6.0, "src": "rough_compose.mp4",
         "src_mtime": 0, "created_at": "2026-09-22T11:30:00", "elapsed": 3.0},
        ensure_ascii=False), encoding="utf-8")
    base = rough.stat().st_mtime
    os.utime(rough, (base, base))
    os.utime(mp4, (base, base + (10 if newer_than_rough else -10)))
    return mp4


# ---------- _fine_upstream_path / _effective_fine_video_path ----------

def test_fine_upstream_path_prefers_ready_cut(tmp_path):
    from slirn_home.app import _fine_upstream_path

    m, t, outputs = _make_task(tmp_path)
    _write_optimize(outputs, marks=[1])
    _mk_cut_artifacts(outputs, [1])
    p = _fine_upstream_path(t.task_id, "video", m)
    assert p is not None and p.name == "optimize_compose.mp4"
    # 字幕上游也换成同一时间基文件（排除标记行 + 前移）
    srt_p = _fine_upstream_path(t.task_id, "subtitle", m)
    assert srt_p is not None and srt_p.suffix == ".srt"
    text = srt_p.read_text(encoding="utf-8")
    assert "第一行废话" not in text  # 标记行剔除
    assert "00:00:01,000 --> 00:00:03,000" in text  # 3s/5s 前移 2s


def test_fine_upstream_path_falls_back_to_rough(tmp_path):
    """产物失效（marks 变了 / rough 更新 / 产物缺失）→ 回退 rough_compose.mp4。"""
    from slirn_home.app import _fine_upstream_path

    m, t, outputs = _make_task(tmp_path)
    _write_optimize(outputs, marks=[1, 2])  # marks 已变（用户又标记了一行）
    _mk_cut_artifacts(outputs, [1])
    assert _fine_upstream_path(t.task_id, "video", m).name == "rough_compose.mp4"
    # 无标记 / 无产物 → 同样回退
    _write_optimize(outputs, marks=[])
    assert _fine_upstream_path(t.task_id, "video", m).name == "rough_compose.mp4"


def test_effective_fine_video_path_manual_upload_wins(tmp_path):
    """手动上传的素材不被 auto 覆盖（用户显式选择优先）。"""
    from slirn_home.app import _effective_fine_video_path

    m, t, outputs = _make_task(tmp_path)
    _write_optimize(outputs, marks=[1])
    _mk_cut_artifacts(outputs, [1])
    up_dir = m.tasks_dir / t.task_id / "upload"
    up_dir.mkdir(parents=True, exist_ok=True)
    up = up_dir / "my.mp4"
    up.write_bytes(b"mine")
    mats = {"video": {"path": f"tasks/{t.task_id}/upload/my.mp4", "source": "upload"}}
    p = _effective_fine_video_path(m, t.task_id, mats)
    assert p is not None and p.name == "my.mp4"


# ---------- /save_optimize_subtitle 剪辑钩子 ----------

def _client(tmp_path):
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    built = build_app(tmp_path)
    return TestClient(built.app)


def test_save_kicks_cut_when_marks_present(tmp_path, monkeypatch):
    m, t, outputs = _make_task(tmp_path)
    _mk_rough(outputs)
    _write_optimize(outputs, saved=False)
    calls = []
    monkeypatch.setattr(compose_service, "start_optimize_cut",
                        lambda *a, **k: calls.append(a) or True)
    c = _client(tmp_path)
    r = c.post("/slirn/api/save_optimize_subtitle",
               json={"task_id": t.task_id, "decisions": [],
                     "line_marks": [1, 2]}).json()
    assert r["ok"] is True, r
    assert r["cut"]["state"] == "started" and r["cut"]["marks"] == [1, 2]
    assert len(calls) == 1 and calls[0][0] == t.task_id
    # 落盘核对
    assert json.loads((outputs / optimize_service.OPTIMIZE_JSON).read_text(
        encoding="utf-8"))["line_marks"] == [1, 2]


def test_save_reports_running_when_job_busy(tmp_path, monkeypatch):
    m, t, outputs = _make_task(tmp_path)
    _mk_rough(outputs)
    _write_optimize(outputs, saved=False, marks=[3])
    compose_service._OPT_CUT_JOBS[t.task_id] = {
        "state": "running", "progress": 5.0, "stage": "剪辑中", "error": None,
        "started_at": 0.0, "finished_at": None, "result": None}
    try:
        monkeypatch.setattr(compose_service, "start_optimize_cut", lambda *a, **k: False)
        c = _client(tmp_path)
        r = c.post("/slirn/api/save_optimize_subtitle",
                   json={"task_id": t.task_id, "decisions": [],
                         "line_marks": [3]}).json()
        assert r["ok"] is True and r["cut"]["state"] == "running"
    finally:
        compose_service._OPT_CUT_JOBS.pop(t.task_id, None)


def test_save_clears_artifacts_when_marks_empty(tmp_path):
    m, t, outputs = _make_task(tmp_path)
    _mk_cut_artifacts(outputs, [1])
    _write_optimize(outputs, marks=[1])
    c = _client(tmp_path)
    r = c.post("/slirn/api/save_optimize_subtitle",
               json={"task_id": t.task_id, "decisions": [],
                     "line_marks": []}).json()
    assert r["ok"] is True and r["cut"]["state"] == "cleared"
    assert not compose_service.optimize_compose_path(outputs).exists()
    assert not (outputs / compose_service.OPTIMIZE_CUT_JSON).exists()


def test_save_rejects_bad_line_marks(tmp_path):
    m, t, outputs = _make_task(tmp_path)
    _write_optimize(outputs, saved=False)
    c = _client(tmp_path)
    r = c.post("/slirn/api/save_optimize_subtitle",
               json={"task_id": t.task_id, "decisions": [],
                     "line_marks": "1,2"}).json()
    assert r["ok"] is False and "line_marks" in r["error"]


# ---------- /optimize_cut_status 磁盘兜底 ----------

def test_cut_status_endpoint_fallback(tmp_path):
    m, t, outputs = _make_task(tmp_path)
    _write_optimize(outputs, marks=[1])
    c = _client(tmp_path)
    r = c.post("/slirn/api/optimize_cut_status",
               json={"task_id": t.task_id}).json()
    assert r["ok"] is True and r["job"]["state"] == "idle"
    assert r["ready"]["exists"] is False
    # sidecar 就绪 → done + ready
    _mk_cut_artifacts(outputs, [1])
    r2 = c.post("/slirn/api/optimize_cut_status",
                json={"task_id": t.task_id}).json()
    assert r2["job"]["state"] == "done" and r2["ready"]["exists"] is True


# ---------- /optimized_srt base 参数 ----------

def test_optimized_srt_bases(tmp_path):
    m, t, outputs = _make_task(tmp_path)
    _write_optimize(outputs, marks=[1])
    c = _client(tmp_path)
    r = c.post("/slirn/api/optimized_srt",
               json={"task_id": t.task_id, "base": "rough"}).json()
    assert r["ok"] is True and r["base"] == "rough"
    assert "第一行废话" not in r["srt"]  # 标记行剔除
    assert "00:00:03,000 --> 00:00:05,000" in r["srt"]  # 时间不动
    r2 = c.post("/slirn/api/optimized_srt",
                json={"task_id": t.task_id, "base": "cut"}).json()
    assert r2["ok"] is True
    assert "00:00:01,000 --> 00:00:03,000" in r2["srt"]  # 前移 2000ms
    r3 = c.post("/slirn/api/optimized_srt",
                json={"task_id": t.task_id, "base": "bogus"}).json()
    assert r3["ok"] is False


# ---------- _assemble_fine_filter：auto 字幕内存直供（R4） ----------

def test_assemble_auto_subtitle_follows_effective_video(tmp_path):
    """auto 字幕素材不读快照文件 — 时间基跟随实时解析出的视频（优化成片优先）。"""
    from slirn_home.app import _assemble_fine_filter

    m, t, outputs = _make_task(tmp_path)
    _write_optimize(outputs, marks=[1])
    _mk_cut_artifacts(outputs, [1])  # 生效视频 = optimize_compose.mp4 → 前移时间基
    # 素材快照指向一个「过期」的 SRT（内容是垃圾，若被使用会解析为空走回退分支）
    stale = m.tasks_dir / t.task_id / "tmp" / "optimized_subs.srt"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("GARBAGE 不是 SRT", encoding="utf-8")
    fc_path = m.tasks_dir / t.task_id / "fine_compose.json"
    fc_path.write_text(json.dumps({
        "layout": {"subtitle": {"enabled": True}},
        "materials": {
            "video": {"path": f"{t.task_id}\\outputs\\rough_compose.mp4",
                      "type": "video", "source": "auto"},
            "subtitle": {"path": f"{t.task_id}\\tmp\\optimized_subs.srt",
                         "type": "subtitle", "source": "auto"},
        },
    }), encoding="utf-8")
    asm = _assemble_fine_filter(t.task_id, m, None, 0.0)
    assert asm["ok"] is True, asm.get("error")
    # 内存直供生效 → ass= 滤镜分支（而不是垃圾文件回退 subtitles= 分支）
    assert "ass=" in asm["filter_complex"], asm["filter_complex"]
    # 生成的 ASS 内容：保留行文本在、标记行文本不在
    ass_txt = Path(asm["sub_input_tmp"]).read_text(encoding="utf-8")
    assert "第二行正经" in ass_txt and "第三行正经" in ass_txt
    assert "第一行废话" not in ass_txt
    # 时间基 = 优化成片（行 2 从 3s 前移到 1s）：ASS Dialogue 里应出现 0:00:01 起
    assert "0:00:01" in ass_txt
    # 内存直供的 SRT 临时文件登记进清理列表
    assert any(p.name.startswith("slirn_fine_subs_")
               for p in asm.get("image_tmp_paths") or [])


# ---------- 渲染：标记删除 UI ----------

def test_render_optimize_zone_marks_ui(tmp_path):
    from slirn_home.app import _render_optimize_zone

    m, t, outputs = _make_task(tmp_path)
    _mk_rough(outputs)
    _write_optimize(outputs, saved=True, marks=[2])
    html = _render_optimize_zone(t.task_id, m.get(t.task_id), m)
    # 行级：类 + data-deleted + 按钮（标记行 ✚ 取消 / 未标记行 🗑️）
    assert 'line-deleted" data-id="2"' in html
    assert 'data-deleted="1"' in html and 'data-deleted="0"' in html
    assert 'data-action="opt-line-delete"' in html
    assert ">✚</button>" in html and ">🗑️</button>" in html
    assert '<span class="slirn-opt-line-badge del">🗑️ 已标记删除</span>' in html
    # 统计 + 剪辑状态条（有标记无产物 → pending 提示重存）
    assert "标记删除 <b>1</b> 行" in html
    assert 'id="slirn-opt-cut-status"' in html and 'data-state="pending"' in html
    # 产物就绪 → done 状态条（含保留秒数）
    _mk_cut_artifacts(outputs, [2])
    html2 = _render_optimize_zone(t.task_id, m.get(t.task_id), m)
    assert 'data-state="done"' in html2 and "优化成片已生成" in html2
    # 无标记 → 无状态条 + 无删除徽章（hint 常驻文案含「已标记删除」字样，
    # 故断言徽章容器而非裸子串）
    _write_optimize(outputs, marks=[])
    html3 = _render_optimize_zone(t.task_id, m.get(t.task_id), m)
    assert "slirn-opt-cut-status" not in html3
    assert 'slirn-opt-line-badge del' not in html3


def test_render_optimize_zone_play_skip_hint(tmp_path):
    """播放跳过提示：hint 说明播放自动跳过已标记删除片段（成片效果预览）。"""
    from slirn_home.app import _render_optimize_zone

    m, t, outputs = _make_task(tmp_path)
    _mk_rough(outputs)
    _write_optimize(outputs, saved=True, marks=[2])
    html = _render_optimize_zone(t.task_id, m.get(t.task_id), m)
    assert "播放时也会自动跳过已标记删除的片段" in html, (
        "hint 必须说明播放自动跳过删除片段")


def test_render_optimize_zone_deleted_stats_and_buttons(tmp_path):
    """删除统计（行数 + 总时长）+ 只看已删除行筛选 + 查看最终字幕按钮。"""
    from slirn_home.app import _render_optimize_zone

    m, t, outputs = _make_task(tmp_path)
    _mk_rough(outputs)
    _write_optimize(outputs, saved=True, marks=[2])  # 行 2：3s-5s = 2.0s
    html = _render_optimize_zone(t.task_id, m.get(t.task_id), m)
    # 统计：行数 + 总时长
    assert "标记删除 <b>1</b> 行 · 共 2.0s" in html, "统计必须含删除行数 + 总时长"
    # 筛选按钮（有标记才渲染）
    assert 'data-action="opt-filter-deleted"' in html and "只看已删除的行（1）" in html
    # 最终字幕按钮（已确认 → 可用；未确认 → disabled）
    assert 'data-action="opt-final-view"' in html
    assert 'disabled title="确认保存后可查看"' not in html
    # 无标记 → 筛选按钮不渲染；未确认 → 最终字幕按钮 disabled
    _write_optimize(outputs, saved=False, marks=[])
    html2 = _render_optimize_zone(t.task_id, m.get(t.task_id), m)
    assert "opt-filter-deleted" not in html2
    assert 'disabled title="确认保存后可查看"' in html2
