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


# ---------- REQ-20261004-bugfix: _strip_json_block 容错 ----------

def test_strip_json_block_single_object():
    """单对象（标准情况）原样返回。"""
    from slirn_home.short_video_analyze import _strip_json_block
    raw = '{"highlights": [{"id": "h1", "start_ms": 0, "end_ms": 1000}]}'
    out = _strip_json_block(raw)
    assert out == {"highlights": [{"id": "h1", "start_ms": 0, "end_ms": 1000}]}


def test_strip_json_block_strips_code_fence():
    """容错 Markdown 代码块标记。"""
    from slirn_home.short_video_analyze import _strip_json_block
    raw = '```json\n{"highlights": []}\n```'
    out = _strip_json_block(raw)
    assert out == {"highlights": []}


def test_strip_json_block_multiple_top_level_objects():
    """多个顶层对象用逗号分隔：合并 highlights 数组。

    REQ-20261004-bugfix：LLM 偶尔把多个 highlights 各包成一个对象再并列，
    老逻辑只截首尾 {} 会导致 "Extra data" 解析失败。
    """
    from slirn_home.short_video_analyze import _strip_json_block
    raw = (
        '{"highlights": [{"id": "h1", "start_ms": 0, "end_ms": 1000}]}, '
        '{"highlights": [{"id": "h2", "start_ms": 2000, "end_ms": 3000}]}'
    )
    out = _strip_json_block(raw)
    assert "highlights" in out
    assert len(out["highlights"]) == 2
    assert out["highlights"][0]["id"] == "h1"
    assert out["highlights"][1]["id"] == "h2"


def test_strip_json_block_strips_replacement_char():
    """U+FFFD（替换字符）从 httpx decode('replace' 产生）应被清掉。"""
    from slirn_home.short_video_analyze import _strip_json_block
    raw = '{"highlights": [{"id": "h1", "title": "a�b"}]}'
    out = _strip_json_block(raw)
    assert out["highlights"][0]["title"] == "ab"


def test_strip_json_block_position_context_on_failure():
    """解析失败给出 position + context 便于排查（REQ-20261004-bugfix）。"""
    import pytest
    from slirn_home.short_video_analyze import _strip_json_block
    from slirn_home.short_video_service import ShortVideoError
    # 含有效外层 {} 但内部 unescaped 控制字符（form feed \\x0c）触发 strict 失败
    raw = '{"highlights": [{"id": "h1", "text": "a' + '\x0c' + 'b"}]}'
    with pytest.raises(ShortVideoError) as ei:
        _strip_json_block(raw)
    msg = str(ei.value)
    assert "context" in msg, msg
    assert "pos " in msg, msg


def test_strip_json_block_top_level_array():
    """顶层直接是数组（少数 LLM 的输出风格）也兼容：合并为 highlights。"""
    from slirn_home.short_video_analyze import _strip_json_block
    raw = '[{"id": "h1"}, {"id": "h2"}]'
    out = _strip_json_block(raw)
    assert "highlights" in out
    assert len(out["highlights"]) == 2


def test_strip_json_block_no_json_raises():
    """完全没有 JSON 抛 ShortVideoError（不是 json.JSONDecodeError）。"""
    import pytest
    from slirn_home.short_video_analyze import _strip_json_block
    from slirn_home.short_video_service import ShortVideoError
    with pytest.raises(ShortVideoError):
        _strip_json_block("just some plain text, no braces")


# ---------- REQ-20261004-bugfix: _validate_highlights 字幕区间校验 ----------

def test_validate_highlights_filters_out_of_range_subtitles():
    """LLM 返回的 src_index 若对应 SRT 行不在 [start_ms, end_ms] 内，必须被剔除。

    复现场景：用户报告「AI拆条的字幕跟视频对不上」——根因就是 _validate_highlights
    只校验 src_index 存在，未校验时间区间，导致 LLM 把整段视频任意 SRT 行的
    src_index 都写进 highlight，UI 渲染时显示出来就跟实际播放内容对不上。
    """
    from slirn_home.short_video_analyze import _validate_highlights, SrtLine

    srt_lines = [
        SrtLine(index=1, start_ms=1000, end_ms=2000, text="第一句"),
        SrtLine(index=2, start_ms=3000, end_ms=4000, text="第二句"),
        SrtLine(index=3, start_ms=5000, end_ms=6000, text="第三句"),
        SrtLine(index=4, start_ms=7000, end_ms=8000, text="第四句（clip 外）"),
    ]
    payload = {
        "highlights": [
            {
                "id": "h1",
                "title": "测试片段",
                "start_ms": 1000,
                "end_ms": 6000,
                "subtitle_lines": [
                    {"src_index": 1, "text": "第一句"},   # in range
                    {"src_index": 2, "text": "第二句"},   # in range
                    {"src_index": 3, "text": "第三句"},   # in range
                    {"src_index": 4, "text": "第四句"},   # out of range → must be filtered
                ],
            },
        ],
    }
    validated, warnings = _validate_highlights(payload, srt_lines)
    assert len(validated) == 1
    subs = validated[0]["subtitle_lines"]
    src_indices = [s["src_index"] for s in subs]
    # 关键断言：src_index=4 (7000-8000ms) 不在 clip [1000-6000] 内，必须被剔除
    assert 4 not in src_indices, f"clip [1000,6000] 不应包含 7000-8000ms 的字幕行，但保留了: {subs}"
    assert src_indices == [1, 2, 3], f"应只保留 [1,2,3]，实际: {src_indices}"
    # 应有警告提示
    assert any("src_index=4" in w and "不在 clip" in w for w in warnings), \
        f"应有「不在 clip 内」警告，实际 warnings: {warnings}"


def test_validate_highlights_in_range_fallback_when_all_filtered():
    """LLM 给的 subtitle_lines 全部超界时，回退到区间内的 SRT 行。"""
    from slirn_home.short_video_analyze import _validate_highlights, SrtLine

    srt_lines = [
        SrtLine(index=1, start_ms=1000, end_ms=2000, text="第一句"),
        SrtLine(index=2, start_ms=3000, end_ms=4000, text="第二句"),
        SrtLine(index=3, start_ms=9000, end_ms=10000, text="第三句（clip 外）"),
    ]
    payload = {
        "highlights": [
            {
                "id": "h1",
                "title": "全错片段",
                "start_ms": 1000,
                "end_ms": 4000,
                "subtitle_lines": [
                    {"src_index": 3, "text": "第三句"},  # 全部超界
                ],
            },
        ],
    }
    validated, warnings = _validate_highlights(payload, srt_lines)
    assert len(validated) == 1
    subs = validated[0]["subtitle_lines"]
    src_indices = [s["src_index"] for s in subs]
    # 回退应填入 in-range 的 [1, 2]
    assert src_indices == [1, 2], f"回退应填 [1,2]，实际: {src_indices}"
    assert any("回退" in w for w in warnings), f"应有回退警告: {warnings}"


def test_validate_highlights_tolerance_200ms():
    """200ms 容差：边界 ±200ms 的字幕行仍被认为在区间内。

    - line1 [1000, 2000] 完全在 clip [2000, 3000] 之前 → 应被剔除
    - line2 [2150, 3000] 大部分在 clip 内，start_ms=2150 与 start=2000 差 150ms
      （< 200ms 容差）→ 应保留
    """
    from slirn_home.short_video_analyze import _validate_highlights, SrtLine

    srt_lines = [
        SrtLine(index=1, start_ms=1000, end_ms=2000, text="完全在 clip 前"),
        SrtLine(index=2, start_ms=2150, end_ms=3000, text="边界 +150ms"),
    ]
    payload = {
        "highlights": [
            {
                "id": "h1",
                "title": "边界测试",
                "start_ms": 2000,
                "end_ms": 3000,
                "subtitle_lines": [
                    {"src_index": 1, "text": "完全在 clip 前"},
                    {"src_index": 2, "text": "边界 +150ms"},
                ],
            },
        ],
    }
    validated, warnings = _validate_highlights(payload, srt_lines)
    subs = validated[0]["subtitle_lines"]
    src_indices = [s["src_index"] for s in subs]
    assert src_indices == [2], f"line1 在 clip 前应剔除、line2 在 200ms 容差内应保留，实际: {src_indices}"


def test_clean_highlight_subtitles_removes_out_of_range():
    """app.py 的存量清洗：磁盘上的旧 highlights.json 也应被清洗一遍。"""
    from slirn_home.short_video_analyze import clean_highlight_subtitles, SrtLine

    srt_index_to_line = {
        1: SrtLine(index=1, start_ms=1000, end_ms=2000, text="在 clip 内"),
        2: SrtLine(index=2, start_ms=5000, end_ms=6000, text="在 clip 外"),
    }
    h = {
        "start_ms": 1000,
        "end_ms": 3000,
        "subtitle_lines": [
            {"src_index": 1, "text": "在 clip 内"},
            {"src_index": 2, "text": "在 clip 外（hallucinated）"},
            {"src_index": 99, "text": "不存在"},
        ],
    }
    cleaned, warns = clean_highlight_subtitles(h, srt_index_to_line)
    assert [s["src_index"] for s in cleaned] == [1], f"应只保留 src=1，实际: {cleaned}"
    assert any("不在 clip" in w for w in warns), f"应有「不在 clip」警告: {warns}"
    assert any("不存在" in w for w in warns), f"应有「不存在」警告: {warns}"


# ---------- REQ-20261004-verify: Stage 3.5 字幕一致性核验 ----------

def test_verify_char_similarity_identical():
    """完全相同文本相似度 = 1.0。"""
    from slirn_home.short_video_verify import _char_similarity
    assert _char_similarity("你好世界", "你好世界") == 1.0


def test_verify_char_similarity_whitespace_normalized():
    """空格/换行差异不影响相似度。"""
    from slirn_home.short_video_verify import _char_similarity
    # 字幕可能是 "你好 世界"（funasr 输出可能是 "你好世界"）
    assert _char_similarity("你好 世界", "你好世界") == 1.0


def test_verify_char_similarity_empty_strings():
    """空串 vs 空串 = 1.0；空串 vs 非空 = 0.0。"""
    from slirn_home.short_video_verify import _char_similarity
    assert _char_similarity("", "") == 1.0
    assert _char_similarity("", "x") == 0.0
    assert _char_similarity("x", "") == 0.0


def test_verify_align_subtitle_to_asr_exact_match():
    """字幕与 ASR 文本完全相同 → ok。"""
    from slirn_home.short_video_verify import _align_subtitle_to_asr
    asr_lines = [
        {"idx": 1, "start_ms": 1100, "end_ms": 1900, "text": "你好世界"},
    ]
    ml = _align_subtitle_to_asr("你好世界", 5, asr_lines, 1000, 2000)
    assert ml.status == "ok"
    assert ml.similarity == 1.0
    assert ml.suggestion == "无操作"


def test_verify_align_subtitle_to_asr_partial_match():
    """部分相似 (0.4-0.7) → partial。"""
    from slirn_home.short_video_verify import _align_subtitle_to_asr
    asr_lines = [
        {"idx": 1, "start_ms": 1100, "end_ms": 1900, "text": "你好世界"},
    ]
    # "你好" 与 "你好世界" 相似度 ~0.5
    ml = _align_subtitle_to_asr("你好", 5, asr_lines, 1000, 2000)
    assert ml.status == "partial"
    assert 0.4 <= ml.similarity < 0.7


def test_verify_align_subtitle_to_asr_mismatch():
    """相似度 < 0.4 → mismatch，建议替换为 ASR 文本。"""
    from slirn_home.short_video_verify import _align_subtitle_to_asr
    asr_lines = [
        {"idx": 1, "start_ms": 1100, "end_ms": 1900, "text": "完全不同的音频"},
    ]
    ml = _align_subtitle_to_asr("你好世界", 5, asr_lines, 1000, 2000)
    assert ml.status == "mismatch"
    assert ml.similarity < 0.4
    assert "ASR 文本" in ml.suggestion


def test_verify_align_subtitle_to_asr_time_miss():
    """ASR 行不在 highlight 时间窗口 ±容差内 → missing。"""
    from slirn_home.short_video_verify import _align_subtitle_to_asr
    asr_lines = [
        # 这个 ASR 行在 [5000, 6000]，离 highlight [1000, 2000] 太远
        {"idx": 1, "start_ms": 5000, "end_ms": 6000, "text": "你好世界"},
    ]
    ml = _align_subtitle_to_asr("你好世界", 5, asr_lines, 1000, 2000)
    assert ml.status == "missing"
    assert "找不到对应音频" in ml.suggestion


def test_verify_parse_re_asr_srt_with_offset():
    """_parse_re_asr_srt 把 ASR 内部时间 + offset 转回源视频时间轴。"""
    from slirn_home.short_video_verify import _parse_re_asr_srt

    srt = """1
00:00:01,000 --> 00:00:02,000
你好世界
"""
    out = _parse_re_asr_srt(srt, offset_ms=30000)
    assert len(out) == 1
    assert out[0]["start_ms"] == 31000
    assert out[0]["end_ms"] == 32000
    assert out[0]["text"] == "你好世界"


def test_verify_stage_runs_end_to_end(tmp_path):
    """完整跑 Stage 3.5：模拟一个完整 pipeline（Stage 1-3 + verify）。

    用静默 funasr/ffmpeg 替代：直接准备 highlights.json + SRT + 假 source 视频。
    跳过 ffmpeg/funasr 路径，只验证状态机写入 + summary 报告。
    """
    from slirn_home import short_video_verify
    from slirn_home import short_video_service as svc
    import json

    root = tmp_path
    tid = "20261004-001"
    pid = "sv_test"

    # 直接手工搭项目结构（绕过 create_project 的 task 检查）
    proj_dir = svc.project_dir(root, tid, pid)
    proj_dir.mkdir(parents=True, exist_ok=True)
    fake_video = proj_dir / "fake.mp4"
    fake_video.write_bytes(b"\x00" * 1024)  # 假视频（仅用于通过路径检查）
    assets_dir = proj_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    proj = {
        "task_id": tid,
        "id": pid,
        "name": "测试",
        "kind": "split",
        "materials": [{
            "id": "mat_fake",
            "kind": "video",
            "name": "fake.mp4",
            "path": str(fake_video),
            "source": "auto",
        }],
        "base_material_id": "mat_fake",
        "pipeline": {
            "stage1_source": {"status": "done", "source_material_id": "mat_fake"},
            "stage2_extract": {"status": "pending"},
            "stage3_analyze": {"status": "pending"},
            "stage3_verify": {"status": "pending"},
            "stage4_coarse": {"status": "pending"},
            "stage5_refine": {"status": "pending"},
            "stage6_finalize": {"status": "pending"},
        },
    }
    svc.save_project(root, proj)

    # 写一个 highlights.json
    hl = {
        "highlights": [
            {
                "index": 1,
                "id": "h1",
                "title": "测试片段",
                "start_ms": 0,
                "end_ms": 5000,
                "subtitle_lines": [{"src_index": 1, "text": "假字幕"}],
            },
        ],
    }
    sp = svc.stage_highlights_path(root, tid, pid)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(hl, ensure_ascii=False), encoding="utf-8")

    # 跑 verify（会因 ffmpeg 在 fake.mp4 上失败）
    try:
        short_video_verify.run_stage3_verify(root, tid, pid)
    except svc.ShortVideoError:
        # 预期失败 —— fake.mp4 不是真视频
        pass

    # 至少 verify_dir 应被创建（核验初始化成功）
    verify_dir = svc.stage3_verify_dir(root, tid, pid)
    assert verify_dir.is_dir(), f"verify 目录应被创建: {verify_dir}"
