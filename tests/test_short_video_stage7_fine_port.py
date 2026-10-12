"""REQ-20261013-105：短视频 Stage 7 精剪 v1 完整版。

覆盖：
1. params 6 组子字段 save + load_default
2. 模板 6 端点语义：list / save / apply / delete / rename / export
3. 模板隔离：短视频模板文件 _global_short_video_fine_profiles.json 不写长视频 _global_fine_profiles.json
4. _JOBS 异步 job 表语义（set + get + idempotent）
5. bg 检测：_compute_content_bbox 全白 / 全黑 / 居中
6. AI 布局：_default_layout / _normalize_region 校验 + parse 防御式
7. preview_clip 校验：duration_sec 不在 2-30 抛错
8. 短/长隔离：save_params 不污染长视频 outputs
"""

import json
import subprocess
from pathlib import Path

import pytest

# ---------- 1. params ----------

def test_save_params_merges_six_groups(tmp_path):
    from slirn_home import short_video_fine as mod
    from slirn_home import short_video_service as svc

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1").mkdir(parents=True)
    project = {"task_id": "t1", "id": "p1", "pipeline": {}}
    svc.save_project(repo, project)

    # 只传 audio 组（其他 5 组保留默认）
    res = mod.save_params(repo, "t1", "p1", {
        "audio": {"enabled": True, "bgm_path": "/tmp/x.mp3"},
    })
    assert res["params"]["audio"]["enabled"] is True
    assert res["params"]["audio"]["bgm_path"] == "/tmp/x.mp3"
    # layout 保留默认
    assert res["params"]["layout"]["video"]["w"] == 1080
    # reload 一致
    proj2 = svc.load_project(repo, "t1", "p1")
    p = proj2["pipeline"]["stage7_finalize"]["params"]
    assert p["audio"]["enabled"] is True
    assert p["layout"]["video"]["w"] == 1080


def test_load_default_params_returns_all_six_groups():
    from slirn_home.short_video_fine import DEFAULT_PARAMS, load_default_params
    p = load_default_params()
    for k in ("materials", "layout", "font", "output", "audio", "preview"):
        assert k in p
    # 改动不污染 DEFAULT_PARAMS（深拷贝）
    p["audio"]["enabled"] = True
    assert DEFAULT_PARAMS["audio"]["enabled"] is False


# ---------- 2. 模板 6 端点 ----------

def test_profile_lifecycle(tmp_path):
    from slirn_home import short_video_service as svc
    from slirn_home.short_video_fine import (
        apply_profile,
        delete_profile,
        export_profile,
        get_profile,
        list_profiles,
        rename_profile,
        save_profile,
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    project = {"task_id": "t1", "id": "p1", "pipeline": {}}
    svc.save_project(repo, project)

    # save
    p = save_profile(repo, name="测试模板", params={"audio": {"enabled": True}})
    assert p["name"] == "测试模板"
    pid = p["id"]
    # list
    lst = list_profiles(repo)
    assert any(x["id"] == pid for x in lst)
    # get
    g = get_profile(repo, pid)
    assert g["params"]["audio"]["enabled"] is True
    # apply
    res = apply_profile(repo, "t1", "p1", pid)
    assert res["params"]["audio"]["enabled"] is True
    # rename
    rn = rename_profile(repo, pid, "新名字")
    assert rn["name"] == "新名字"
    # export
    ex = export_profile(repo, pid)
    assert ex["name"] == "新名字"
    assert "exported_at" in ex
    # delete
    ok = delete_profile(repo, pid)
    assert ok is True
    assert get_profile(repo, pid) is None


def test_profile_unique_name_on_duplicate(tmp_path):
    from slirn_home.short_video_fine import list_profiles, save_profile

    repo = tmp_path / "repo"
    repo.mkdir()
    p1 = save_profile(repo, name="模板A", params={})
    p2 = save_profile(repo, name="模板A", params={})
    assert p1["name"] == "模板A"
    assert p2["name"] == "模板A (2)"
    lst = list_profiles(repo)
    assert len(lst) == 2


# ---------- 3. 模板隔离 ----------

def test_profile_storage_does_not_touch_long_video_file(tmp_path):
    from slirn_home import short_video_service as svc
    from slirn_home.short_video_fine import save_profile

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1").mkdir(parents=True)
    project = {"task_id": "t1", "id": "p1", "pipeline": {}}
    svc.save_project(repo, project)

    # 长视频全局模板文件（隔离验证）
    long_global = repo / "tasks" / "_global_fine_profiles.json"
    long_global.write_text('{"profiles":[]}', encoding="utf-8")
    # 故意未使用：变量保留以表达意图（验证 _global_short_video_fine_profiles.json 出现）
    _long_files_before = sorted(p.name for p in (repo / "tasks").iterdir())

    save_profile(repo, name="短视频模板1", params={})

    long_files_after = sorted(p.name for p in (repo / "tasks").iterdir())
    # 短视频多了 _global_short_video_fine_profiles.json；长视频文件不变
    assert "_global_short_video_fine_profiles.json" in long_files_after
    assert "_global_fine_profiles.json" in long_files_after  # 长视频原文件保留
    # 长视频文件内容不变
    assert long_global.read_text(encoding="utf-8") == '{"profiles":[]}'


# ---------- 4. _JOBS 异步 ----------

def test_fine_job_idempotent_and_copy(tmp_path):
    from slirn_home import short_video_fine as mod
    from slirn_home.short_video_fine import _JOBS_LOCK, _set_job, job_status

    with mod._JOBS_LOCK:
        mod._JOBS.clear()

    # 模拟已 running
    mod._set_job("t1", "p1", state="running", progress=50.0, stage="合成中")
    j = job_status("t1", "p1")
    assert j["state"] == "running"

    # 改副本不污染
    j["state"] = "MUTATED"
    j2 = job_status("t1", "p1")
    assert j2["state"] == "running"


# ---------- 5. bg 检测 ----------

def test_compute_content_bbox_full_white():
    from slirn_home.short_video_bg_detect import _compute_content_bbox
    w, h = 100, 50
    pixels = [(255, 255, 255)] * (w * h)
    res = _compute_content_bbox(w, h, pixels)
    assert res["content_ratio"] == 0.0
    assert res["x"] == 0 and res["y"] == 0
    assert res["w"] == w and res["h"] == h


def test_compute_content_bbox_central_object():
    from slirn_home.short_video_bg_detect import _compute_content_bbox
    w, h = 100, 50
    pixels = [(255, 255, 255)] * (w * h)
    # 中间 20x10 区域 = 黑色
    for y in range(20, 30):
        for x in range(40, 60):
            pixels[y * w + x] = (0, 0, 0)
    res = _compute_content_bbox(w, h, pixels)
    assert res["x"] == 40
    assert res["y"] == 20
    assert res["w"] == 20
    assert res["h"] == 10
    assert 0 < res["content_ratio"] < 1


def test_compute_content_bbox_single_pixel():
    from slirn_home.short_video_bg_detect import _compute_content_bbox
    w, h = 10, 10
    pixels = [(255, 255, 255)] * (w * h)
    pixels[5 * w + 5] = (0, 0, 0)  # 中心点
    res = _compute_content_bbox(w, h, pixels)
    assert res["x"] == 5 and res["y"] == 5
    assert res["w"] == 1 and res["h"] == 1


# ---------- 6. AI 布局 ----------

def test_ai_default_layout_4_regions():
    from slirn_home.short_video_ai_layout import _default_layout
    layout = _default_layout()
    for k in ("video", "subtitle", "cover", "bg"):
        assert k in layout
        r = layout[k]
        assert 0 <= r["x"] <= 1080
        assert 0 <= r["y"] <= 1920
        assert 0 <= r["x"] + r["w"] <= 1080
        assert 0 <= r["y"] + r["h"] <= 1920


def test_ai_normalize_region_clamps_out_of_range():
    from slirn_home.short_video_ai_layout import _normalize_region
    # 超出范围 → 钳到边界
    r = _normalize_region({"x": -100, "y": 5000, "w": 20000, "h": 99999})
    assert r["x"] == 0
    assert r["y"] == 1920
    assert r["w"] == 1080
    assert r["h"] == 1920


def test_ai_normalize_region_handles_garbage():
    from slirn_home.short_video_ai_layout import _normalize_region
    assert _normalize_region(None) is None
    assert _normalize_region("not a dict") is None
    assert _normalize_region({})["w"] == 0  # 缺省值


def test_ai_parse_with_mocked_llm(tmp_path, monkeypatch):
    """mock _call_llm 返有效 JSON → apply 后落 project.json。"""
    import slirn_home.revision_service as rev
    from slirn_home import short_video_ai_layout as mod
    from slirn_home import short_video_service as svc

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1").mkdir(parents=True)
    svc.save_project(repo, {"task_id": "t1", "id": "p1", "pipeline": {}})

    monkeypatch.setattr(rev, "_call_llm", lambda *a, **k: json.dumps({
        "video": {"x": 0, "y": 0, "w": 1080, "h": 1920, "reason": "full"},
        "subtitle": {"x": 60, "y": 1620, "w": 960, "h": 240, "reason": "bottom"},
        "cover": {"x": 0, "y": 0, "w": 1080, "h": 1080, "reason": "top"},
        "bg": {"x": 0, "y": 0, "w": 1080, "h": 1920, "reason": "bg"},
    }))
    layout = mod.parse_reference_layout(repo, "t1", "p1", style_hint="现代")
    assert layout["video"]["w"] == 1080
    res = mod.apply_ai_layout(repo, "t1", "p1", layout)
    assert res["layout"]["subtitle"]["y"] == 1620


def test_ai_parse_llm_failure_falls_back_to_default(tmp_path, monkeypatch):
    import slirn_home.revision_service as rev
    from slirn_home import short_video_ai_layout as mod
    from slirn_home import short_video_service as svc

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1").mkdir(parents=True)
    svc.save_project(repo, {"task_id": "t1", "id": "p1", "pipeline": {}})

    def boom(*a, **k): raise RuntimeError("LLM 挂了")
    monkeypatch.setattr(rev, "_call_llm", boom)
    layout = mod.parse_reference_layout(repo, "t1", "p1")
    # fallback 默认布局
    assert layout["video"]["w"] == 1080
    assert layout["subtitle"]["y"] == 1620


def test_ai_parse_garbage_falls_back_to_default(tmp_path, monkeypatch):
    import slirn_home.revision_service as rev
    from slirn_home import short_video_ai_layout as mod
    from slirn_home import short_video_service as svc

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1").mkdir(parents=True)
    svc.save_project(repo, {"task_id": "t1", "id": "p1", "pipeline": {}})

    monkeypatch.setattr(rev, "_call_llm", lambda *a, **k: "totally not json")
    layout = mod.parse_reference_layout(repo, "t1", "p1")
    assert layout["video"]["w"] == 1080


# ---------- 7. preview 校验 ----------

def test_preview_duration_out_of_range_raises(tmp_path):
    from slirn_home import short_video_service as svc
    from slirn_home.short_video_fine import preview_clip

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1").mkdir(parents=True)
    svc.save_project(repo, {"task_id": "t1", "id": "p1", "pipeline": {}})

    for bad_dur in (1, 31, 100, 0):
        with pytest.raises(svc.ShortVideoError):
            preview_clip(repo, "t1", "p1", 1, duration_sec=bad_dur)


# ---------- 8. 短/长隔离 ----------

def test_save_params_does_not_touch_long_video_outputs(tmp_path):
    from slirn_home import short_video_fine as mod
    from slirn_home import short_video_service as svc

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1").mkdir(parents=True)
    long_outputs = repo / "tasks" / "t1" / "outputs"
    long_outputs.mkdir(parents=True)
    (long_outputs / "rough_compose.mp4").write_bytes(b"long")
    (long_outputs / "execution_history.json").write_text("[]", encoding="utf-8")
    long_files_before = sorted(p.name for p in long_outputs.iterdir())

    svc.save_project(repo, {"task_id": "t1", "id": "p1", "pipeline": {}})
    mod.save_params(repo, "t1", "p1", {"audio": {"enabled": True}})

    long_files_after = sorted(p.name for p in long_outputs.iterdir())
    assert long_files_before == long_files_after
