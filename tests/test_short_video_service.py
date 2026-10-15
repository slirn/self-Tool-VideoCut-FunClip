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
    # REQ-20261013-100：7 阶段管线 UI（重编号 + 内容优化替换 3.5 核验）
    for stage in (1, 2, 3, 4, 5, 6, 7):
        assert f'data-stage="{stage}"' in html, f"Stage {stage} 卡片缺失"
    assert "选源视频" in html
    assert "提取字幕" in html
    assert "AI 拆条" in html
    assert "内容优化" in html
    assert "粗剪合成" in html
    assert "优化字幕" in html
    assert "精剪混编" in html
    # Stage 1 源下拉 + Stage 3 提示词文本域（REQ-099 修订：快速模板单选已移除）
    assert "slirn-stage1-source" in html
    assert 'id="slirn-stage3-prompt"' in html
    assert 'name="sv-stage3-template"' not in html
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


# ---------- REQ-20261013-100: Stage 4 内容优化（替换原 3.5 字幕一致性核验） ----------

def test_pipeline_key_migration_old_project(tmp_path):
    """旧 key 项目载入即迁移：旧 4/5/6 状态搬到新 5/6/7，stage3_verify 丢弃。"""
    import json as _json
    from slirn_home.short_video_service import (
        project_dir, save_project, load_project,
    )
    from tasklib import TaskManager

    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name="t", original_video=video)
    tid = task.task_id
    proj_dir = project_dir(tmp_path, tid, "sv_old")
    proj_dir.mkdir(parents=True, exist_ok=True)
    proj = {
        "task_id": tid, "id": "sv_old", "name": "旧项目", "kind": "split",
        "pipeline": {
            "stage1_source": {"status": "done"},
            "stage2_extract": {"status": "done"},
            "stage3_analyze": {"status": "done"},
            "stage3_verify": {"status": "done", "summary": {"ok": 3}},
            "stage4_coarse": {"status": "done", "count": 2},
            "stage5_refine": {"status": "done", "count": 2},
            "stage6_finalize": {"status": "done", "count": 2},
        },
    }
    save_project(tmp_path, proj)
    loaded = load_project(tmp_path, tid, "sv_old")
    pl = loaded["pipeline"]
    # 旧 key 的状态原样搬到新 key
    assert pl["stage5_coarse"] == {"status": "done", "count": 2}
    assert pl["stage6_refine"] == {"status": "done", "count": 2}
    assert pl["stage7_finalize"] == {"status": "done", "count": 2}
    # 旧 key 全部清除；verify 丢弃；新 optimize 补 pending
    for gone in ("stage3_verify", "stage4_coarse", "stage5_refine", "stage6_finalize"):
        assert gone not in pl, f"{gone} 应被迁移清除"
    assert pl["stage4_optimize"]["status"] == "pending"
    # 幂等：再次载入不回退
    again = load_project(tmp_path, tid, "sv_old")
    assert again["pipeline"]["stage5_coarse"]["status"] == "done"


def test_optimize_parse_result_ops_and_downgrade():
    """keep/fix/split 解析 + 缺席=删除 + 校验降级（宁可不改不可改错）。"""
    import json as _json
    from slirn_home.short_video_optimize import _parse_optimize_result

    src_lines = [
        {"no": 1, "src_index": 1, "text": "第一行"},
        {"no": 2, "src_index": 2, "text": "第二行长句子需要切分"},
        {"no": 3, "src_index": 3, "text": "第三行"},
        {"no": 4, "src_index": 4, "text": "第四行"},
    ]
    raw = _json.dumps({"lines": [
        {"no": 1, "op": "keep"},
        {"no": 2, "op": "split", "splits": ["第二行长句子", "需要切分"]},
        {"no": 3, "op": "fix", "text": "第三行（改写）"},
        {"no": 9, "op": "keep"},
        # no=4 缺席 = 删除
    ]}, ensure_ascii=False)
    ops, warns = _parse_optimize_result(raw, src_lines)
    assert ops[1] == {"op": "keep"}
    assert ops[2] == {"op": "split", "splits": ["第二行长句子", "需要切分"]}
    assert ops[3] == {"op": "fix", "text": "第三行（改写）"}
    assert 4 not in ops, "缺席编号 = 删除（无 op）"
    assert any("未知行号 9" in w for w in warns)

    # split 子句拼接与原文不一致 → 降级 keep（宁可不切不可错切）
    bad = _json.dumps({"lines": [
        {"no": 2, "op": "split", "splits": ["改写过的", "两段子句"]},
    ]}, ensure_ascii=False)
    ops2, warns2 = _parse_optimize_result(bad, src_lines)
    assert ops2[2] == {"op": "keep"}
    assert any("不一致" in w for w in warns2)

    # fix 空文本 → keep
    empty_fix = _json.dumps({"lines": [{"no": 3, "op": "fix", "text": "  "}]},
                            ensure_ascii=False)
    ops3, _ = _parse_optimize_result(empty_fix, src_lines)
    assert ops3[3] == {"op": "keep"}


def test_optimize_interpolate_split_timestamps():
    """切分子行时间戳按字符占比内插：覆盖父窗口、首尾对齐、src_index 继承。"""
    from slirn_home.short_video_optimize import _interpolate_split

    line = {"src_index": 7, "text": "一二三四五六", "start_ms": 1000, "end_ms": 4000}
    out = _interpolate_split(line, ["一二三", "四五六"])
    assert [x["text"] for x in out] == ["一二三", "四五六"]
    assert out[0]["start_ms"] == 1000 and out[0]["end_ms"] == 2500
    assert out[1]["start_ms"] == 2500 and out[1]["end_ms"] == 4000
    assert all(x["src_index"] == 7 for x in out)


def _seed_optimize_project(tmp_path):
    """split 项目 + highlights.json（1 条 4 行，行带时间戳）。返回 (tid, pid)。"""
    import json as _json
    from slirn_home.short_video_service import create_project, stage_highlights_path
    from tasklib import TaskManager

    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name="t", original_video=video)
    tid = task.task_id
    proj = create_project(tmp_path, tid, name="p", brief="")
    pid = proj["id"]
    doc = {
        "highlights": [{
            "index": 1, "id": "h1", "title": "测试标题",
            "start_ms": 1000, "end_ms": 8000, "total_ms": 7000,
            "subtitle_lines": [
                {"no": 1, "src_index": 1, "text": "大家好呀今天",
                 "start_ms": 1000, "end_ms": 4000},
                {"no": 2, "src_index": 2, "text": "我们来讲一个非常长的主题句子",
                 "start_ms": 4000, "end_ms": 8000},
                {"no": 3, "src_index": 3, "text": "嗯对",
                 "start_ms": 5000, "end_ms": 5400},
                {"no": 4, "src_index": 4, "text": "谢谢观看",
                 "start_ms": 5400, "end_ms": 6000},
            ],
        }],
    }
    hp = stage_highlights_path(tmp_path, tid, pid)
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_text(_json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return tid, pid


def test_optimize_run_end_to_end(tmp_path):
    """run_stage4_optimize：fix/split/删除落地 + no 重编 + 首次备份 + 下游重置
    + Stage 3 state 镜像同步。"""
    import json as _json
    from slirn_home import revision_service
    from slirn_home import short_video_service as svcs
    from slirn_home import short_video_optimize as opt
    from slirn_home.short_video_service import (
        load_project, save_project, stage_highlights_path,
    )

    tid, pid = _seed_optimize_project(tmp_path)
    # 下游先标 done，验证优化后全部重置
    proj = load_project(tmp_path, tid, pid)
    for key in ("stage5_coarse", "stage6_refine", "stage7_finalize"):
        proj["pipeline"][key] = {"status": "done"}
    save_project(tmp_path, proj)

    llm_out = _json.dumps({"lines": [
        {"no": 1, "op": "fix", "text": "大家好，今天"},
        {"no": 2, "op": "split", "splits": ["我们来讲一个", "非常长的主题句子"]},
        # no=3 缺席 = 删除（口水话）
        {"no": 4, "op": "keep"},
    ]}, ensure_ascii=False)
    captured = {}
    orig = revision_service._call_llm

    def fake(sys_p, user_p, **kw):
        captured["sys"] = sys_p
        captured["user"] = user_p
        return llm_out

    revision_service._call_llm = fake
    try:
        state = opt.run_stage4_optimize(tmp_path, tid, pid)
    finally:
        revision_service._call_llm = orig

    assert state["status"] == "done"
    hl_state = state["highlights"][0]
    assert hl_state["status"] == "ok"
    assert hl_state["fixed"] == 1 and hl_state["split"] == 1 and hl_state["deleted"] == 1
    assert hl_state["lines_before"] == 4
    assert hl_state["lines_after"] == 4  # 4 行：删 1、切分 +1

    # user payload 捕获了标题 + 编号字幕行
    assert "测试标题" in captured["user"]
    assert "1. 大家好呀今天" in captured["user"]

    # 写回 highlights.json：fix/split/删除落地 + no 按行序重编 1..4
    doc = _json.loads(stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8"))
    lines = doc["highlights"][0]["subtitle_lines"]
    assert [l["text"] for l in lines] == [
        "大家好，今天", "我们来讲一个", "非常长的主题句子", "谢谢观看"]
    assert [l["no"] for l in lines] == [1, 2, 3, 4]
    # 切分子行：src_index 继承父行、时间戳按占比内插（首=父 start，末=父 end）
    assert lines[1]["src_index"] == 2 and lines[2]["src_index"] == 2
    assert lines[1]["start_ms"] == 4000 and lines[2]["end_ms"] == 8000
    # 组窗口重算
    assert doc["highlights"][0]["start_ms"] == 1000
    assert doc["highlights"][0]["end_ms"] == 8000

    # 首次备份存在（备份原表）
    backup = svcs.stage4_optimize_dir(tmp_path, tid, pid) / "backup_highlights.json"
    assert backup.is_file()
    backup_doc = _json.loads(backup.read_text(encoding="utf-8"))
    assert backup_doc["highlights"][0]["subtitle_lines"][0]["text"] == "大家好呀今天"

    # 下游全部重置
    proj2 = load_project(tmp_path, tid, pid)
    for key in ("stage5_coarse", "stage6_refine", "stage7_finalize"):
        assert proj2["pipeline"][key]["status"] == "pending", key
    # Stage 3 state 镜像同步（页签渲染读这份）
    s3 = svcs.get_stage_state(proj2, "stage3_analyze")
    assert [l["text"] for l in s3["highlights"][0]["subtitle_lines"]] == [
        "大家好，今天", "我们来讲一个", "非常长的主题句子", "谢谢观看"]


def test_optimize_run_backup_once(tmp_path):
    """重复优化不覆盖首次备份（保住最初解析结果）。"""
    import json as _json
    from slirn_home import revision_service
    from slirn_home import short_video_optimize as opt
    from slirn_home.short_video_service import (
        stage4_optimize_dir, stage_highlights_path,
    )

    tid, pid = _seed_optimize_project(tmp_path)
    orig = revision_service._call_llm

    def reply(text):
        return lambda s, u, **kw: _json.dumps(
            {"lines": [{"no": 1, "op": "fix", "text": text}]}, ensure_ascii=False)

    revision_service._call_llm = reply("第一次改写")
    try:
        opt.run_stage4_optimize(tmp_path, tid, pid)
        bp = stage4_optimize_dir(tmp_path, tid, pid) / "backup_highlights.json"
        first = bp.read_text(encoding="utf-8")
        revision_service._call_llm = reply("第二次改写")
        opt.run_stage4_optimize(tmp_path, tid, pid)
        assert bp.read_text(encoding="utf-8") == first
        doc = _json.loads(
            stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8"))
        assert doc["highlights"][0]["subtitle_lines"][0]["text"] == "第二次改写"
    finally:
        revision_service._call_llm = orig


def test_optimize_run_all_deleted_keeps_original(tmp_path):
    """LLM 把全部行删光 → 保留原行（kept_original），highlights 不被写坏；
    唯一一条也是这一步 → 阶段标 failed（本次没有优化成任何一条）。"""
    import json as _json
    from slirn_home import revision_service
    from slirn_home import short_video_optimize as opt
    from slirn_home.short_video_service import stage_highlights_path

    tid, pid = _seed_optimize_project(tmp_path)
    orig = revision_service._call_llm
    revision_service._call_llm = lambda s, u, **kw: _json.dumps({"lines": []})
    try:
        state = opt.run_stage4_optimize(tmp_path, tid, pid)
    finally:
        revision_service._call_llm = orig

    hl = state["highlights"][0]
    assert hl["status"] == "kept_original"
    assert hl["deleted"] == 4 and hl["lines_after"] == 4
    assert state["status"] == "failed"
    doc = _json.loads(
        stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8"))
    texts = [l["text"] for l in doc["highlights"][0]["subtitle_lines"]]
    assert texts == ["大家好呀今天", "我们来讲一个非常长的主题句子", "嗯对", "谢谢观看"]


# ---------- REQ-20261004-stage3-restructure 新增测试 ----------


def test_validate_highlights_density_fallback():
    """_validate_highlights 应该自动补齐字幕密度到 ≥ 8 行（60s 视频）。

    输入 LLM 只输出 3 行时，应 fallback 用区间内 SRT 行补齐。
    """
    from slirn_home.short_video_analyze import _validate_highlights, SrtLine

    srt_lines = [
        SrtLine(index=i, start_ms=(i - 1) * 1000, end_ms=i * 1000, text=f"第{i}句")
        for i in range(1, 16)
    ]  # 15 行 SRT，每行 1s

    payload = {
        "highlights": [{
            "id": "h1",
            "title": "密度测试",
            "start_ms": 0,
            "end_ms": 15000,  # 15s 视频，密度要求 ≥ ceil(15/5)=3，但 prompt 要求 ≥ 8
            "subtitle_lines": [
                {"src_index": 1, "text": "开头钩子"},
                {"src_index": 2, "text": "展开"},
                {"src_index": 3, "text": "收束"},
            ],  # 只 3 行
        }]
    }
    validated, warnings = _validate_highlights(payload, srt_lines)
    assert len(validated) == 1
    hl = validated[0]
    # 应被 fallback 扩到 ≥ 8 行（10 行以上更稳）
    assert len(hl["subtitle_lines"]) >= 8, f"期望 ≥ 8 行，实际 {len(hl['subtitle_lines'])}"
    # fallback warnings 应有提示
    assert any("density" in w.lower() or "fallback" in w.lower() or "补齐" in w
               for w in warnings), f"应记录 warning，实际: {warnings}"


def test_reorder_subtitle_line_end_to_end(tmp_path):
    """reorder_subtitle_line：拖拽 src_index=2 到 src_index=4 之前。"""
    import json as _json
    from slirn_home.short_video_service import (
        load_project, save_project, create_project, stage_highlights_path,
        reorder_subtitle_line, _STAGE_STATE_KEYS, get_stage_state,
    )
    from tasklib import TaskManager

    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name="t", original_video=video)
    tid = task.task_id

    proj = create_project(tmp_path, tid, name="p", brief="")
    pid = proj["id"]

    # 写一个 highlights.json
    hl_doc = {
        "project_id": pid,
        "highlights": [{
            "index": 1,
            "id": "h1",
            "title": "排序测试",
            "start_ms": 0,
            "end_ms": 10000,
            "subtitle_lines": [
                {"src_index": 1, "text": "A"},
                {"src_index": 2, "text": "B"},
                {"src_index": 3, "text": "C"},
                {"src_index": 4, "text": "D"},
                {"src_index": 5, "text": "E"},
            ],
        }],
    }
    hp = stage_highlights_path(tmp_path, tid, pid)
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_text(_json.dumps(hl_doc, ensure_ascii=False), encoding="utf-8")

    # 把 highlights 同步到 project state（模拟 _inject_stage3_highlights）
    proj = load_project(tmp_path, tid, pid)
    s3 = get_stage_state(proj, "stage3_analyze")
    s3["highlights"] = hl_doc["highlights"]
    save_project(tmp_path, proj)

    # 把 src_index=2 移到 src_index=4 之前
    updated = reorder_subtitle_line(
        tmp_path, tid, pid,
        hl_index=1, src_index=2, before_src_index=4,
    )
    new_lines = updated["subtitle_lines"]
    indices = [int(sl["src_index"]) for sl in new_lines]
    # 期望：[A, B, C, D, E] → 移除 B → [A, C, D, E] → 在 D 之前插入 B → [A, C, B, D, E]
    assert indices == [1, 3, 2, 4, 5], f"排序错误：{indices}"

    # 重读 highlights.json 验证
    with open(hp, "r", encoding="utf-8") as f:
        saved = _json.load(f)
    saved_indices = [int(sl["src_index"]) for sl in saved["highlights"][0]["subtitle_lines"]]
    assert saved_indices == [1, 3, 2, 4, 5]

    # 验证下游 stage 被重置
    proj2 = load_project(tmp_path, tid, pid)
    pl = proj2.get("pipeline", {})
    for stage_key in ["stage4_optimize", "stage5_coarse",
                      "stage6_refine", "stage7_finalize"]:
        s = pl.get(stage_key, {})
        assert s.get("status") == "pending", f"{stage_key} 应被重置为 pending，实际 {s}"


def test_reorder_subtitle_line_to_end(tmp_path):
    """before_src_index=None 应移到末尾。"""
    import json as _json
    from slirn_home.short_video_service import (
        create_project, stage_highlights_path, reorder_subtitle_line,
    )
    from tasklib import TaskManager

    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name="t", original_video=video)
    tid = task.task_id

    proj = create_project(tmp_path, tid, name="p", brief="")
    pid = proj["id"]

    hl_doc = {
        "highlights": [{
            "index": 1,
            "id": "h1",
            "title": "t",
            "start_ms": 0,
            "end_ms": 5000,
            "subtitle_lines": [
                {"src_index": 1, "text": "A"},
                {"src_index": 2, "text": "B"},
                {"src_index": 3, "text": "C"},
            ],
        }],
    }
    hp = stage_highlights_path(tmp_path, tid, pid)
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_text(_json.dumps(hl_doc, ensure_ascii=False), encoding="utf-8")

    updated = reorder_subtitle_line(
        tmp_path, tid, pid,
        hl_index=1, src_index=1, before_src_index=None,
    )
    indices = [int(sl["src_index"]) for sl in updated["subtitle_lines"]]
    assert indices == [2, 3, 1], f"src_index=1 应被移到末尾，实际 {indices}"


def test_coarse_resolve_segments_from_subtitle_lines(tmp_path):
    """Stage 4 _resolve_segments 内嵌函数：把 subtitle_lines[].src_index → SRT 时间戳。

    只验证 segments 解析逻辑（不真跑 moviepy）。
    """
    import json as _json
    from slirn_home.short_video_service import (
        create_project, stage_raw_srt_path, stage_highlights_path,
    )
    from slirn_home.short_video_analyze import _parse_srt_lines
    from tasklib import TaskManager

    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name="t", original_video=video)
    tid = task.task_id

    proj = create_project(tmp_path, tid, name="p", brief="")
    pid = proj["id"]

    # 写 raw.srt（5 条字幕，时间不同）
    raw_srt = (
        "1\n00:00:01,000 --> 00:00:02,000\n第一句\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\n第二句\n\n"
        "3\n00:00:10,000 --> 00:00:11,500\n第三句\n\n"
        "4\n00:00:15,000 --> 00:00:16,000\n第四句\n\n"
        "5\n00:00:20,000 --> 00:00:21,000\n第五句\n\n"
    )
    rp = stage_raw_srt_path(tmp_path, tid, pid)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(raw_srt, encoding="utf-8")

    # 解析 SRT
    srt_lines = _parse_srt_lines(rp.read_text(encoding="utf-8"))
    srt_index_to_line = {ln.index: ln for ln in srt_lines}

    # highlight 跨源时间挑 3, 1（验证按 highlight.subtitle_lines 顺序拼）
    hl = {
        "subtitle_lines": [
            {"src_index": 3, "text": "第三句"},  # 10-11.5s
            {"src_index": 1, "text": "第一句"},  # 1-2s
        ],
    }

    # 模拟 _resolve_segments 逻辑（与 short_video_coarse.py 保持一致）
    def _resolve(hl_dict):
        out = []
        for sl in (hl_dict.get("subtitle_lines") or []):
            idx = int(sl.get("src_index") or 0)
            ln = srt_index_to_line.get(idx)
            if ln is None or ln.end_ms <= ln.start_ms:
                continue
            out.append((int(ln.start_ms), int(ln.end_ms)))
        return out

    segs = _resolve(hl)
    # 期望：[(10000, 11500), (1000, 2000)]
    assert segs == [(10000, 11500), (1000, 2000)], f"segments 解析错误: {segs}"

    # 也测空 subtitle_lines
    empty_segs = _resolve({"subtitle_lines": []})
    assert empty_segs == [], f"空 subtitle_lines 应得空 segments，实际: {empty_segs}"


# ---------- REQ-20261004-stage3-prompt-edit 新增测试 ----------


def test_custom_prompts_lifecycle(tmp_path):
    """save_custom_prompt → load → reset → load 应为空。"""
    import json as _json
    from slirn_home.short_video_service import (
        create_project,
        load_custom_prompts, save_custom_prompt, reset_custom_prompt,
        custom_prompts_path,
    )
    from tasklib import TaskManager

    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name="t", original_video=video)
    tid = task.task_id

    proj = create_project(tmp_path, tid, name="p", brief="")
    pid = proj["id"]

    # 初始：空
    assert load_custom_prompts(tmp_path, tid, pid) == {}

    # 保存 hook_first
    saved = save_custom_prompt(
        tmp_path, tid, pid,
        template="hook_first", prompt="我的自定义钩子优先提示词",
    )
    assert saved["prompt"] == "我的自定义钩子优先提示词"
    assert "updated_at" in saved

    # 再保存 topic_cluster
    save_custom_prompt(tmp_path, tid, pid, template="topic_cluster", prompt="主题聚类 v2")

    # load 应返回 2 个
    loaded = load_custom_prompts(tmp_path, tid, pid)
    assert "hook_first" in loaded and "topic_cluster" in loaded
    assert loaded["hook_first"]["prompt"] == "我的自定义钩子优先提示词"
    assert loaded["topic_cluster"]["prompt"] == "主题聚类 v2"

    # 验证文件存在且 JSON 合法
    cp = custom_prompts_path(tmp_path, tid, pid)
    assert cp.is_file()
    raw = _json.loads(cp.read_text(encoding="utf-8"))
    assert "hook_first" in raw and "topic_cluster" in raw

    # 重置 hook_first
    removed = reset_custom_prompt(tmp_path, tid, pid, template="hook_first")
    assert removed is True

    loaded2 = load_custom_prompts(tmp_path, tid, pid)
    assert "hook_first" not in loaded2
    assert "topic_cluster" in loaded2  # 不应误删其他模板

    # 重置 topic_cluster（最后一个） → 文件应被删除
    reset_custom_prompt(tmp_path, tid, pid, template="topic_cluster")
    assert load_custom_prompts(tmp_path, tid, pid) == {}
    assert not cp.exists(), f"全部重置后文件应被删除: {cp}"

    # 重置不存在的 key → 返回 False 不报错
    assert reset_custom_prompt(tmp_path, tid, pid, template="hook_first") is False


def test_custom_prompts_invalid_template(tmp_path):
    """未知 template 应抛错，不写入文件。"""
    from slirn_home.short_video_service import (
        create_project, save_custom_prompt, custom_prompts_path,
    )
    from tasklib import TaskManager

    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name="t", original_video=video)
    tid = task.task_id

    proj = create_project(tmp_path, tid, name="p", brief="")
    pid = proj["id"]

    # custom_prompt 端点校验 template 名（app.py 校验）；service 层只接收合法 template
    # 但应当正确接受 "custom" 是不允许的（系统只有 3 个模板）
    # —— 测试 service 直接调：传合法模板应该 OK
    save_custom_prompt(tmp_path, tid, pid, template="story_arc", prompt="测试")
    cp = custom_prompts_path(tmp_path, tid, pid)
    assert cp.is_file()


def test_run_stage3_uses_custom_prompt(tmp_path):
    """run_stage3 应在有 custom_prompt 时用 custom，否则用默认。"""
    import json as _json
    from slirn_home.short_video_service import (
        create_project, save_custom_prompt,
        stage_raw_srt_path, stage_llm_raw_path,
    )
    from slirn_home import short_video_analyze as sva
    from tasklib import TaskManager
    import json as json_mod

    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name="t", original_video=video)
    tid = task.task_id

    proj = create_project(tmp_path, tid, name="p", brief="")
    pid = proj["id"]

    # 写 raw.srt（≥ 1 行）
    rp = stage_raw_srt_path(tmp_path, tid, pid)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n第一句\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\n第二句\n\n",
        encoding="utf-8",
    )

    # 写 custom_prompt
    CUSTOM_PROMPT = "CUSTOM-PROMPT-MARKER-12345"
    save_custom_prompt(tmp_path, tid, pid, template="hook_first", prompt=CUSTOM_PROMPT)

    # mock revision_service._call_llm 防止真打 LLM
    from slirn_home import revision_service
    captured = {"calls": []}
    def fake_call(sys_p, user_p, **kw):
        captured["calls"].append(sys_p)
        return json_mod.dumps({
            "highlights": [{
                "id": "h1",
                "title": "测试",
                "start_ms": 0,
                "end_ms": 30000,
                "subtitle_lines": [
                    {"src_index": 1, "text": "行 1"},
                    {"src_index": 2, "text": "行 2"},
                ],
            }],
        }, ensure_ascii=False)
    revision_service._call_llm = fake_call

    state = sva.run_stage3(tmp_path, tid, pid, template="hook_first", n_clips=4)
    assert state["status"] == "done"
    assert state.get("custom_prompt_used") is True
    # 应至少一次调用 fake_call 且系统提示词含 CUSTOM_PROMPT marker
    assert any(CUSTOM_PROMPT in c for c in captured["calls"]), \
        f"应使用自定义提示词，调用的 sys_p: {captured['calls']}"

    # 再跑一次用 story_arc（未自定义）→ 不应使用 custom
    captured2 = {"calls": []}
    def fake_call2(sys_p, user_p, **kw):
        captured2["calls"].append(sys_p)
        return json_mod.dumps({
            "highlights": [{
                "id": "h1",
                "title": "测试",
                "start_ms": 0,
                "end_ms": 30000,
                "subtitle_lines": [
                    {"src_index": 1, "text": "行 1"},
                    {"src_index": 2, "text": "行 2"},
                ],
            }],
        }, ensure_ascii=False)
    revision_service._call_llm = fake_call2
    state2 = sva.run_stage3(tmp_path, tid, pid, template="story_arc", n_clips=4)
    assert state2["status"] == "done"
    assert state2.get("custom_prompt_used") is False  # story_arc 没自定义
    # REQ-20261012-099 修订五：未自定义的模板 → 上游「大模型智能剪辑」默认提示词
    assert any("srt字幕分析剪辑器" in c for c in captured2["calls"]), \
        f"应使用上游大模型智能剪辑默认提示词"


# ---------- REQ-20261015-122：Stage 2 去掉模型/语言选择 + 热词半角逗号分隔 ----------


def test_stage2_ui_removes_model_lang_selectors(tmp_path):
    from slirn_home import short_video_service as svc
    from slirn_home import short_video_ui as ui

    _mgr, task = _make_task(tmp_path)
    project = svc.create_project(tmp_path, task.task_id, "UI 测试", "开场 Hook", {
        "allow_external_llm": False,
    })
    project = svc.update_project(tmp_path, task.task_id, project["id"],
                                 {"base_material_id": project["materials"][0]["id"]})
    html = ui.render_project(project)
    # 模型/语言选择已移除（含历史坏选项 seaco_paraformer）
    assert 'id="slirn-stage2-model"' not in html
    assert 'id="slirn-stage2-lang"' not in html
    assert "seaco_paraformer" not in html
    assert "SenseVoiceSmall" not in html
    # 热词框保留，提示改半角逗号分隔
    assert 'id="slirn-stage2-hotwords"' in html
    assert "半角逗号分隔，提升专有名词识别" in html
    assert "空格分隔，提升专有名词识别" not in html


def test_stage2_js_drops_model_lang_keeps_hotwords():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    js = (root / "slirn_home" / "static" / "short_video.js").read_text(encoding="utf-8")
    start = js.index("function stage2Run")
    end = js.index("function", start + 10)
    body = js[start:end]
    assert "slirn-stage2-model" not in body
    assert "slirn-stage2-lang" not in body
    assert "slirn-stage2-hotwords" in body
    assert "model:" not in body and "lang:" not in body
    # 整个文件不再引用已删除的元素
    assert "slirn-stage2-model" not in js
    assert "slirn-stage2-lang" not in js


def test_norm_hotwords_comma_to_space():
    from slirn_home.short_video_extract import _norm_hotwords

    assert _norm_hotwords("智能体,Agent，工作流") == "智能体 Agent 工作流"
    assert _norm_hotwords("智能体 Agent") == "智能体 Agent"  # 旧空格输入原样通过
    assert _norm_hotwords("  a , , b ， ") == "a b"
    assert _norm_hotwords("") == ""


def test_run_funasr_extract_model_whitelist_and_hotwords_norm(tmp_path, monkeypatch):
    """白名单外的模型（历史 seaco_paraformer）回默认；热词逗号归一为空格。"""
    from slirn_home import short_video_extract as ex

    # _resolve_extract_subtitle 要求脚本存在（候选第一项 = slirn/skill/...）
    skill_dir = tmp_path / "slirn" / "skill" / "video-subtitle-extractor" / "scripts"
    skill_dir.mkdir(parents=True)
    (skill_dir / "extract_subtitle.py").write_text("# fake", encoding="utf-8")

    captured = {}

    class _Proc:
        returncode = 1
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(ex.subprocess, "run", fake_run)
    ex._run_funasr_extract(
        input_video=tmp_path / "in.mp4", output_srt=tmp_path / "out.srt",
        repo_root=tmp_path, model="seaco_paraformer", lang="zh",
        hotwords="智能体,Agent",
    )
    cmd = captured["cmd"]
    assert cmd[cmd.index("--model") + 1] == "paraformer"
    assert cmd[cmd.index("--hotwords") + 1] == "智能体 Agent"


# ---------- REQ-20261015-123：Stage 2 热词自动保存（落盘 + 回填） ----------


def test_stage2_hotwords_saved_and_rendered_back(tmp_path, monkeypatch):
    from slirn_home import short_video_extract as ex
    from slirn_home import short_video_service as svc
    from slirn_home import short_video_ui as ui

    _mgr, task = _make_task(tmp_path)
    project = svc.create_project(tmp_path, task.task_id, "热词测试", "开场 Hook", {
        "allow_external_llm": False,
    })
    project = svc.update_project(tmp_path, task.task_id, project["id"],
                                 {"base_material_id": project["materials"][0]["id"]})

    srt_path = svc.stage_raw_srt_path(tmp_path, task.task_id, project["id"])
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\n测试字幕行\n", encoding="utf-8")

    def fake_extract(**kwargs):
        return ex.FunasrRunResult(
            srt_path=srt_path, raw_json=None, elapsed_sec=1,
            stderr_tail="", returncode=0,
        )

    monkeypatch.setattr(ex, "_run_funasr_extract", fake_extract)
    state = ex.run_stage2(
        tmp_path, task.task_id, project["id"],
        hotwords="智能体,Agent，工作流",
    )
    assert state["status"] == "done"
    # 存用户原始输入（逗号归一在 _run_funasr_extract 内做，不在这里）
    assert state["hotwords"] == "智能体,Agent，工作流"

    # 重新加载 + 渲染：热词应回填输入框
    reloaded = svc.load_project(tmp_path, task.task_id, project["id"])
    html = ui.render_project(reloaded)
    assert 'id="slirn-stage2-hotwords"' in html
    assert 'value="智能体,Agent，工作流"' in html
