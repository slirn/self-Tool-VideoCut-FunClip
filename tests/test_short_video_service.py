"""短视频混剪服务、UI 和 FFmpeg 命令构造测试。"""

from pathlib import Path


def _make_task(tmp_path: Path, name: str = "短视频源任务"):
    from tasklib import TaskManager

    video = tmp_path / "source.mp4"
    video.write_bytes(b"fake-video")
    mgr = TaskManager(tmp_path)
    return mgr, mgr.create(name=name, original_video=video)


def test_short_video_create_and_local_storyboard(tmp_path):
    from slirn_home import short_video_service as svc

    _mgr, task = _make_task(tmp_path)
    project = svc.create_project(
        tmp_path, task.task_id, "课程混剪",
        "开场讲清问题，展示三个卖点，最后引导关注。",
        {"allow_external_llm": False, "variants": 3},
    )
    assert project["id"].startswith("sv_")
    assert project["task_id"] == task.task_id
    assert project["materials"], "应自动导入任务原始视频"

    project = svc.generate_storyboard(tmp_path, task.task_id, project["id"])
    story = project["storyboard"]
    assert story["status"] == "ready"
    assert story["model"] == "local-rules"
    assert len(story["variants"]) == 3
    for variant in story["variants"]:
        assert variant["title"]
        assert variant["hook"]
        assert variant["segments"]
        assert variant["subtitles"]
        assert all(s["material_id"] for s in variant["segments"])


def test_short_video_material_upload_and_update(tmp_path):
    from slirn_home import short_video_service as svc

    _mgr, task = _make_task(tmp_path)
    project = svc.create_project(tmp_path, task.task_id, "上传测试", "测试", {})
    image = tmp_path / "cover.png"
    image.write_bytes(b"not-a-real-png")
    audio = tmp_path / "bgm.mp3"
    audio.write_bytes(b"not-a-real-mp3")

    mat1 = svc.add_material_path(tmp_path, task.task_id, project["id"], image)
    mat2 = svc.add_material_path(tmp_path, task.task_id, project["id"], audio)
    assert mat1["kind"] == "image"
    assert mat2["kind"] == "audio"

    project = svc.load_project(tmp_path, task.task_id, project["id"])
    assert len(project["materials"]) >= 3
    updated = svc.update_project(tmp_path, task.task_id, project["id"], {
        "name": "新名字",
        "brief": "新 Brief",
        "config": {"variants": 2, "allow_external_llm": False},
    })
    assert updated["name"] == "新名字"
    assert updated["config"]["variants"] == 2
    assert updated["config"]["allow_external_llm"] is False


def test_short_video_external_storyboard_is_normalized(tmp_path, monkeypatch):
    from slirn_home import short_video_service as svc

    _mgr, task = _make_task(tmp_path)
    project = svc.create_project(tmp_path, task.task_id, "LLM 测试", "卖点短视频", {
        "allow_external_llm": True,
        "variants": 1,
    })
    material_id = project["materials"][0]["id"]

    def fake_llm(_root, _project):
        return ({
            "variants": [{
                "id": "v1",
                "name": "LLM 版本",
                "title": "三分钟看懂",
                "hook": "先看这个关键点",
                "cta": "留言获取清单",
                "segments": [{
                    "material_id": material_id,
                    "start": 1,
                    "duration": 4,
                    "transition": "wipeleft",
                }],
                "overlays": [],
                "subtitles": [{"start": 0, "end": 2, "text": "外部模型字幕"}],
                "bgm_material_id": "",
                "bgm_volume_db": -14,
            }]
        }, "mock-llm")

    monkeypatch.setattr(svc, "_llm_storyboard", fake_llm)
    project = svc.generate_storyboard(tmp_path, task.task_id, project["id"])
    assert project["storyboard"]["model"] == "mock-llm"
    variant = project["storyboard"]["variants"][0]
    assert variant["title"] == "三分钟看懂"
    assert variant["segments"][0]["transition"] == "wipeleft"


def test_short_video_ui_contains_editor_and_materials(tmp_path):
    from slirn_home import short_video_service as svc
    from slirn_home import short_video_ui as ui

    _mgr, task = _make_task(tmp_path)
    project = svc.create_project(tmp_path, task.task_id, "UI 测试", "开场 Hook", {
        "allow_external_llm": False,
    })
    project = svc.generate_storyboard(tmp_path, task.task_id, project["id"])
    html = ui.render_project(project)
    assert "版本编辑与渲染" in html
    assert "data-action=\"sv-render\"" in html
    assert "data-action=\"sv-save\"" in html
    assert "data-action=\"sv-ai\"" in html
    assert "data-action=\"sv-upload\"" in html


def test_short_video_render_command_contains_transition_overlay_ass_and_bgm(tmp_path):
    from slirn_home import short_video_render as render
    from slirn_home import short_video_service as svc

    v1 = tmp_path / "v1.mp4"
    v2 = tmp_path / "v2.mp4"
    ov = tmp_path / "ov.png"
    bgm = tmp_path / "bgm.mp3"
    for p in (v1, v2, ov, bgm):
        p.write_bytes(b"fake")
    project = {
        "id": "sv_test",
        "task_id": "t1",
        "name": "test",
        "config": svc.default_config(),
        "materials": [
            {"id": "m1", "kind": "video", "path": str(v1), "has_audio": True, "duration": 5},
            {"id": "m2", "kind": "video", "path": str(v2), "has_audio": True, "duration": 5},
            {"id": "img", "kind": "image", "path": str(ov), "duration": 0},
            {"id": "bgm", "kind": "audio", "path": str(bgm), "duration": 30},
        ],
    }
    variant = {
        "id": "v1",
        "name": "版本 1",
        "title": "标题",
        "hook": "Hook",
        "cta": "关注",
        "segments": [
            {"material_id": "m1", "start": 0, "duration": 5, "transition": "fade"},
            {"material_id": "m2", "start": 0, "duration": 5, "transition": "fade"},
        ],
        "overlays": [
            {"material_id": "img", "start": 2, "duration": 3, "x": 100, "y": 200, "scale": 0.3}
        ],
        "subtitles": [{"start": 0, "end": 2, "text": "第一句"}],
        "bgm_material_id": "bgm",
        "bgm_volume_db": -16,
    }
    built = render.build_render_command(tmp_path, project, variant, tmp_path / "out.mp4")
    cmd = " ".join(built["cmd"])
    filter_graph = built["cmd"][built["cmd"].index("-filter_complex") + 1]
    assert "xfade" in filter_graph
    assert "overlay=" in filter_graph
    assert "ass=" in filter_graph
    assert "amix=inputs=2" in filter_graph
    assert "-map [vfinal]" in cmd
    assert str(built["ass_path"]).endswith(".ass")
    Path(built["ass_path"]).unlink(missing_ok=True)


def test_short_video_job_status_is_json_serializable():
    import json

    from slirn_home import short_video_render as render

    job_id = "svjob_test_json"
    with render._JOB_LOCK:
        render._JOBS[job_id] = {
            "job_id": job_id,
            "state": "running",
            "proc": object(),
            "cancel_requested": False,
        }
    try:
        status = render.job_status(job_id)
        assert status is not None
        assert "proc" not in status
        json.dumps(status)
    finally:
        with render._JOB_LOCK:
            render._JOBS.pop(job_id, None)


def test_short_video_delete_is_scoped_to_task_dir(tmp_path):
    from slirn_home import short_video_service as svc

    _mgr, task = _make_task(tmp_path)
    project = svc.create_project(tmp_path, task.task_id, "delete", "", {})
    assert svc.delete_project(tmp_path, task.task_id, project["id"]) is True
    assert svc.delete_project(tmp_path, task.task_id, project["id"]) is False


def test_short_video_render_smoke_9x16(tmp_path):
    """真实 FFmpeg 短渲染闭环：转场 + B-roll + ASS + BGM + 竖屏。"""
    import subprocess
    import time

    from slirn_home import short_video_render as render
    from slirn_home import short_video_service as svc

    v1 = tmp_path / "red.mp4"
    v2 = tmp_path / "blue.mp4"
    overlay = tmp_path / "overlay.png"
    bgm = tmp_path / "bgm.wav"
    for path, color, freq in ((v1, "red", 440), (v2, "blue", 660)):
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"color=c={color}:s=320x180:d=1",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-t", "1", str(path),
        ], check=True, capture_output=True)
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=yellow:s=100x80:d=1",
        "-frames:v", "1", str(overlay),
    ], check=True, capture_output=True)
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
        "-c:a", "pcm_s16le", str(bgm),
    ], check=True, capture_output=True)

    from tasklib import TaskManager

    mgr = TaskManager(tmp_path)
    task = mgr.create(name="render-smoke", original_video=v1)
    project = svc.create_project(
        tmp_path, task.task_id, "render-smoke", "字幕",
        {"allow_external_llm": False, "variants": 1},
    )
    m1 = next(m for m in project["materials"] if Path(m["name"]).name == "red.mp4")
    m2 = svc.add_material_path(tmp_path, task.task_id, project["id"], v2, "video")
    mi = svc.add_material_path(tmp_path, task.task_id, project["id"], overlay, "image")
    mb = svc.add_material_path(tmp_path, task.task_id, project["id"], bgm, "audio")
    project = svc.load_project(tmp_path, task.task_id, project["id"])
    project["storyboard"] = {"status": "ready", "variants": [{
        "id": "v1", "name": "v1", "title": "标题", "hook": "Hook", "cta": "关注",
        "bgm_material_id": mb["id"], "bgm_volume_db": -18,
        "segments": [
            {"id": "s1", "material_id": m1["id"], "start": 0, "duration": 0.5, "transition": "fade"},
            {"id": "s2", "material_id": m2["id"], "start": 0, "duration": 0.5, "transition": "fade"},
        ],
        "overlays": [
            {"id": "o1", "material_id": mi["id"], "start": 0.2, "duration": 0.3,
             "x": 500, "y": 1500, "scale": 1.0,
            },
        ],
        "subtitles": [{"start": 0, "end": 0.8, "text": "字幕"}],
    }]}
    svc.save_project(tmp_path, project)
    job = render.start_render(tmp_path, task.task_id, project["id"], ["v1"])
    status = None
    for _ in range(80):
        time.sleep(0.25)
        status = render.job_status(job["job_id"])
        if status and status.get("state") in ("done", "failed", "cancelled"):
            break
    assert status and status["state"] == "done", status
    out = Path(status["outputs"][0]["path"])
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out),
    ], check=True, capture_output=True, text=True)
    assert probe.stdout.strip() == "1080,1920"
