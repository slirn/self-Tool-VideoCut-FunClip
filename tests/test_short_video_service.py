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
    base_id = project["materials"][0]["id"]
    project = svc.update_project(tmp_path, task.task_id, project["id"],
                                 {"base_material_id": base_id})

    project = svc.generate_storyboard(tmp_path, task.task_id, project["id"])
    story = project["storyboard"]
    assert story["status"] == "ready"
    assert story["model"] == "local-rules"
    assert len(story["variants"]) == 3
    assert story["base_material_id"] == base_id
    for variant in story["variants"]:
        assert variant["title"]
        assert variant["hook"]
        assert variant["segments"]
        assert variant["subtitles"]
        # REQ-20261001-095：主片段全部来自用户选定的基础视频
        assert all(s["material_id"] == base_id for s in variant["segments"])


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
    assert mat1["name"] == "cover.png", "素材名应保留原始文件名"

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
    project = svc.update_project(tmp_path, task.task_id, project["id"],
                                 {"base_material_id": material_id})

    def fake_llm(_root, _project, _base_id):
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
    project = svc.update_project(tmp_path, task.task_id, project["id"],
                                 {"base_material_id": project["materials"][0]["id"]})
    html = ui.render_project(project)
    # REQ-20261003-098：6 阶段管线 UI（旧 mixcut 编辑器已被替换）
    for stage in (1, 2, 3, 4, 5, 6):
        assert f'data-stage="{stage}"' in html, f"Stage {stage} 卡片缺失"
    assert "选源视频" in html
    assert "提取字幕" in html
    assert "AI 拆条" in html
    assert "粗剪合成" in html
    assert "优化字幕" in html
    assert "精剪混编" in html
    # Stage 1 源下拉 + Stage 3 模板单选
    assert "slirn-stage1-source" in html
    assert 'name="sv-stage3-template"' in html
    # 已选基础视频应回显在 Stage 1 下拉
    assert project["base_material_id"] in html, "已选基础视频应回显在 Stage 1 下拉"
    assert 'data-action="sv-remove-material"' in html
    assert 'target="_blank"' not in html
    # REQ-20261001-097：素材卡重排 —— 徽章 + 单行名 + 操作按钮同一行；元信息中文化
    assert 'slirn-sv-material-actions' in html
    assert 'slirn-sv-kind-badge' in html
    assert '任务导入' in html


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


def test_short_video_remove_material(tmp_path):
    """REQ-20261001-096：删除素材 — 上传素材连文件删，任务导入素材保留源文件；引用全部清理。"""
    from slirn_home import short_video_service as svc

    _mgr, task = _make_task(tmp_path)
    project = svc.create_project(tmp_path, task.task_id, "删除素材", "", {
        "allow_external_llm": False,
    })
    base = project["materials"][0]
    assert base["source"] == "auto"
    img = tmp_path / "broll.png"
    img.write_bytes(b"fake")
    aud = tmp_path / "bgm.mp3"
    aud.write_bytes(b"fake")
    m_img = svc.add_material_path(tmp_path, task.task_id, project["id"], img)
    m_aud = svc.add_material_path(tmp_path, task.task_id, project["id"], aud)
    project = svc.update_project(tmp_path, task.task_id, project["id"], {
        "base_material_id": base["id"],
        "storyboard": {"variants": [{
            "id": "v1", "name": "v1",
            "segments": [{"material_id": base["id"], "start": 0, "duration": 2}],
            "overlays": [{"material_id": m_img["id"], "start": 0, "duration": 1,
                          "x": 0, "y": 0, "scale": 0.3}],
            "subtitles": [],
            "bgm_material_id": m_aud["id"],
        }]},
    })
    base_file = tmp_path / base["path"]

    # 1) 删除上传音频 → 条目 + 磁盘文件删除，变体 BGM 引用清空
    project = svc.remove_material(tmp_path, task.task_id, project["id"], m_aud["id"])
    assert m_aud["id"] not in [m["id"] for m in project["materials"]]
    assert not (tmp_path / m_aud["path"]).exists(), "上传素材的磁盘文件应一并删除"
    assert project["storyboard"]["variants"][0]["bgm_material_id"] == ""

    # 2) 删除上传图片 → overlay 引用行移除
    project = svc.remove_material(tmp_path, task.task_id, project["id"], m_img["id"])
    assert project["storyboard"]["variants"][0]["overlays"] == []
    assert not (tmp_path / m_img["path"]).exists()

    # 3) 删除任务自动导入的基础视频 → 只移除条目 / 引用，源文件保留（属于任务）
    project = svc.remove_material(tmp_path, task.task_id, project["id"], base["id"])
    assert project["base_material_id"] == ""
    assert project["storyboard"]["variants"][0]["segments"] == []
    assert base_file.exists(), "任务导入素材的源文件不应被删除"

    # 4) 不存在的素材 → 报错
    try:
        svc.remove_material(tmp_path, task.task_id, project["id"], "mat_not_exist")
        raise AssertionError("不存在的素材应报错")
    except svc.ShortVideoError:
        pass


def test_short_video_upload_preserves_original_filename(tmp_path):
    """REQ-20261001-095：素材显示用户所选原始文件名（上传临时文件名是无意义字符串）。"""
    import tempfile

    from slirn_home import short_video_service as svc

    _mgr, task = _make_task(tmp_path)
    project = svc.create_project(tmp_path, task.task_id, "文件名", "", {})
    # 模拟上传端点：文件先落到系统临时文件（随机名），原始名走 display_name
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        tmp.write(b"fake-png")
        tmp_path_file = Path(tmp.name)
    try:
        mat = svc.add_material_path(
            tmp_path, task.task_id, project["id"], tmp_path_file,
            kind="image", display_name="我的封面 图片.png",
        )
    finally:
        tmp_path_file.unlink(missing_ok=True)
    assert mat["name"] == "我的封面 图片.png", "素材名必须是用户原始文件名"
    assert mat["kind"] == "image"
    assert (tmp_path / mat["path"]).exists(), "磁盘文件应落盘（安全化文件名）"
    assert "我的封面" not in Path(mat["path"]).name, "磁盘文件名走 ASCII 安全化"


def test_short_video_bg_image_kind_validation(tmp_path):
    """REQ-20261001-095：上传类型「背景图片」→ kind=bg_image；只接受图片扩展名。"""
    from slirn_home import short_video_service as svc

    _mgr, task = _make_task(tmp_path)
    project = svc.create_project(tmp_path, task.task_id, "bg", "", {})
    image = tmp_path / "bg.png"
    image.write_bytes(b"fake")
    audio = tmp_path / "bg.mp3"
    audio.write_bytes(b"fake")

    mat = svc.add_material_path(tmp_path, task.task_id, project["id"], image, "bg_image")
    assert mat["kind"] == "bg_image"
    try:
        svc.add_material_path(tmp_path, task.task_id, project["id"], audio, "bg_image")
        raise AssertionError("背景图片类型不应接受音频文件")
    except svc.ShortVideoError as e:
        assert "背景图片" in str(e)


def test_short_video_storyboard_requires_base_video(tmp_path):
    """REQ-20261001-095：生成分镜必须先选基础视频（不再 AI 自动挑）。"""
    from slirn_home import short_video_service as svc

    _mgr, task = _make_task(tmp_path)
    project = svc.create_project(tmp_path, task.task_id, "必选基础", "", {
        "allow_external_llm": False,
    })
    # 1) 未选 → 报错
    try:
        svc.generate_storyboard(tmp_path, task.task_id, project["id"])
        raise AssertionError("未选基础视频应报错")
    except svc.ShortVideoError as e:
        assert "基础视频" in str(e)
    # 2) 非法 id → 报错
    try:
        svc.generate_storyboard(tmp_path, task.task_id, project["id"],
                                base_material_id="mat_not_exist")
        raise AssertionError("非法基础视频 id 应报错")
    except svc.ShortVideoError:
        pass
    # 3) 非视频素材 → 报错
    image = tmp_path / "x.png"
    image.write_bytes(b"fake")
    img_mat = svc.add_material_path(tmp_path, task.task_id, project["id"], image)
    try:
        svc.update_project(tmp_path, task.task_id, project["id"],
                           {"base_material_id": img_mat["id"]})
        raise AssertionError("图片素材不能当基础视频")
    except svc.ShortVideoError:
        pass
    # 4) 显式传参可用（不依赖项目里保存的值）
    base_id = project["materials"][0]["id"]
    out = svc.generate_storyboard(tmp_path, task.task_id, project["id"],
                                  base_material_id=base_id)
    assert out["storyboard"]["base_material_id"] == base_id
    assert out["base_material_id"] == base_id, "选择应持久化到项目"


def test_short_video_normalize_variant_bg_material_id(tmp_path):
    """REQ-20261001-095：变体背景图只接受 bg_image 素材，普通 image（B-roll）清空。"""
    from slirn_home import short_video_service as svc

    _mgr, task = _make_task(tmp_path)
    project = svc.create_project(tmp_path, task.task_id, "bg选择", "", {})
    base_id = project["materials"][0]["id"]
    broll = tmp_path / "broll.png"
    broll.write_bytes(b"fake")
    bgimg = tmp_path / "bg.png"
    bgimg.write_bytes(b"fake")
    m_broll = svc.add_material_path(tmp_path, task.task_id, project["id"], broll)
    m_bg = svc.add_material_path(tmp_path, task.task_id, project["id"], bgimg, "bg_image")

    def _variant(bg_id):
        return {
            "id": "v1", "name": "v1",
            "segments": [{"material_id": base_id, "start": 0, "duration": 3}],
            "bg_material_id": bg_id,
        }

    updated = svc.update_project(tmp_path, task.task_id, project["id"], {
        "storyboard": {"variants": [_variant(m_bg["id"]), _variant(m_broll["id"])]},
    })
    variants = updated["storyboard"]["variants"]
    assert variants[0]["bg_material_id"] == m_bg["id"], "bg_image 素材应保留"
    assert variants[1]["bg_material_id"] == "", "普通 image 素材不能当背景图"


def test_short_video_render_command_bg_image_background(tmp_path):
    """REQ-20261001-095：背景图渲染分支 —— 不走 gblur，图片 cover 铺满画布。"""
    from slirn_home import short_video_render as render
    from slirn_home import short_video_service as svc

    v1 = tmp_path / "v1.mp4"
    bgimg = tmp_path / "bg.png"
    v1.write_bytes(b"fake")
    bgimg.write_bytes(b"fake")

    def _project():
        return {
            "id": "sv_bg", "task_id": "t1", "name": "bg",
            "config": svc.default_config(),
            "materials": [
                {"id": "m1", "kind": "video", "path": str(v1), "has_audio": True, "duration": 5},
                {"id": "bgm", "kind": "bg_image", "path": str(bgimg), "duration": 0},
            ],
        }

    base_variant = {
        "id": "v1", "name": "v1", "title": "t", "hook": "h", "cta": "c",
        "segments": [{"material_id": "m1", "start": 0, "duration": 5, "transition": "fade"}],
        "overlays": [], "subtitles": [],
        "bgm_material_id": "", "bgm_volume_db": -16,
    }
    # 无背景图 → 默认模糊填充分支
    built = render.build_render_command(tmp_path, _project(), dict(base_variant),
                                        tmp_path / "o1.mp4")
    graph = built["cmd"][built["cmd"].index("-filter_complex") + 1]
    assert "gblur" in graph
    Path(built["ass_path"]).unlink(missing_ok=True)

    # 有背景图 → bg 分支（crop 铺满 + 无 gblur），每段多一个图片输入
    variant_bg = dict(base_variant, bg_material_id="bgm")
    built = render.build_render_command(tmp_path, _project(), variant_bg,
                                        tmp_path / "o2.mp4")
    cmd = built["cmd"]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "gblur" not in graph, "有背景图时不应再用模糊填充"
    assert f"crop={built['width']}:{built['height']}" in graph
    assert str(bgimg) in cmd, "背景图应作为输入"
    Path(built["ass_path"]).unlink(missing_ok=True)


def test_short_video_render_smoke_9x16(tmp_path):
    """真实 FFmpeg 短渲染闭环：转场 + B-roll + ASS + BGM + 竖屏。"""
    import subprocess
    import time

    from slirn_home import short_video_render as render
    from slirn_home import short_video_service as svc

    v1 = tmp_path / "red.mp4"
    v2 = tmp_path / "blue.mp4"
    overlay = tmp_path / "overlay.png"
    bgimg = tmp_path / "bg.png"
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
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=green:s=540x960:d=1",
        "-frames:v", "1", str(bgimg),
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
    mbg = svc.add_material_path(tmp_path, task.task_id, project["id"], bgimg, "bg_image")
    assert mbg["kind"] == "bg_image" and mbg["name"] == "bg.png"
    mb = svc.add_material_path(tmp_path, task.task_id, project["id"], bgm, "audio")
    project = svc.load_project(tmp_path, task.task_id, project["id"])
    project["storyboard"] = {"status": "ready", "variants": [{
        "id": "v1", "name": "v1", "title": "标题", "hook": "Hook", "cta": "关注",
        "bgm_material_id": mb["id"], "bgm_volume_db": -18,
        "bg_material_id": mbg["id"],
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
