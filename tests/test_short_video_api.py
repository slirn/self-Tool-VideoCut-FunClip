"""短视频混剪 API 测试。"""

from pathlib import Path


def _client_and_task(tmp_path: Path):
    # 先 import slirn_home（其 paths 模块把 slirn-standalone 加进 sys.path），
    # 否则本文件单独运行时 tasklib 不可导入（全量跑被其他文件铺垫过路径）
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app
    from tasklib import TaskManager

    video = tmp_path / "source.mp4"
    video.write_bytes(b"fake-video")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name="API 源任务", original_video=video)
    client = TestClient(build_app(tmp_path).app)
    return client, task


def test_short_video_create_list_get_save_ai_endpoints(tmp_path):
    client, task = _client_and_task(tmp_path)

    r = client.post("/slirn/api/short_video_create", json={
        "task_id": task.task_id,
        "name": "种草混剪",
        "brief": "突出三个卖点",
        "config": {"allow_external_llm": False, "variants": 3},
    }).json()
    assert r["ok"] is True, r
    project = r["project"]
    pid = project["id"]

    r = client.post("/slirn/api/short_video_list", json={}).json()
    assert r["ok"] is True
    assert "种草混剪" in r["html"]

    r = client.post("/slirn/api/short_video_get", json={
        "task_id": task.task_id, "project_id": pid,
    }).json()
    assert r["ok"] is True
    assert "素材库" in r["html"]

    r = client.post("/slirn/api/short_video_save", json={
        "task_id": task.task_id,
        "project_id": pid,
        "brief": "新的 Brief",
        "config": {"allow_external_llm": False},
    }).json()
    assert r["ok"] is True
    assert r["project"]["brief"] == "新的 Brief"

    # REQ-20261001-095：未选基础视频 → 明确报错，不自动挑
    r = client.post("/slirn/api/short_video_ai_storyboard", json={
        "task_id": task.task_id,
        "project_id": pid,
        "brief": "开场讲问题，中间讲方案，最后引导关注",
        "config": {"allow_external_llm": False, "variants": 3},
    }).json()
    assert r["ok"] is False and "基础视频" in r["error"], r

    base_id = project["materials"][0]["id"]
    r = client.post("/slirn/api/short_video_ai_storyboard", json={
        "task_id": task.task_id,
        "project_id": pid,
        "brief": "开场讲问题，中间讲方案，最后引导关注",
        "config": {"allow_external_llm": False, "variants": 3},
        "base_material_id": base_id,
    }).json()
    assert r["ok"] is True, r
    assert r["project"]["storyboard"]["model"] == "local-rules"
    assert r["project"]["storyboard"]["base_material_id"] == base_id
    assert len(r["project"]["storyboard"]["variants"]) == 3
    # REQ-20261003-098：6 阶段管线 UI（替换原 sv-base-video）
    assert "slirn-stage1-source" in r["html"], "6 阶段管线应回显 Stage 1 源视频下拉"


def test_short_video_upload_and_delete_endpoints(tmp_path):
    client, task = _client_and_task(tmp_path)
    created = client.post("/slirn/api/short_video_create", json={
        "task_id": task.task_id, "name": "上传删除", "brief": "x",
    }).json()
    pid = created["project"]["id"]

    up = client.post(
        "/slirn/api/short_video_upload_material",
        data={"task_id": task.task_id, "project_id": pid, "kind": "image"},
        files={"file": ("broll.png", b"fake-image", "image/png")},
    ).json()
    assert up["ok"] is True, up
    assert up["material"]["kind"] == "image"
    # REQ-20261001-095：显示用户所选原始文件名，不是服务端生成的随机串
    assert up["material"]["name"] == "broll.png"
    file_name = Path(up["material"]["path"]).name
    served = client.get("/slirn/api/short_video_file", params={
        "task_id": task.task_id,
        "project_id": pid,
        "kind": "asset",
        "name": file_name,
    })
    assert served.status_code == 200
    assert served.content == b"fake-image"

    deleted = client.post("/slirn/api/short_video_delete", json={
        "task_id": task.task_id, "project_id": pid,
    }).json()
    assert deleted["ok"] is True


def test_short_video_upload_bg_image_and_chinese_name(tmp_path):
    """REQ-20261001-095：背景图片类型 + 中文原始文件名保留。"""
    client, task = _client_and_task(tmp_path)
    created = client.post("/slirn/api/short_video_create", json={
        "task_id": task.task_id, "name": "背景图", "brief": "x",
    }).json()
    pid = created["project"]["id"]

    up = client.post(
        "/slirn/api/short_video_upload_material",
        data={"task_id": task.task_id, "project_id": pid, "kind": "bg_image"},
        files={"file": ("直播间背景.png", b"fake-image", "image/png")},
    ).json()
    assert up["ok"] is True, up
    assert up["material"]["kind"] == "bg_image"
    assert up["material"]["name"] == "直播间背景.png", "中文原始文件名应原样保留"

    # 素材卡 / 变体背景图下拉应出现中文名与背景图片标签
    page = client.post("/slirn/api/short_video_get", json={
        "task_id": task.task_id, "project_id": pid,
    }).json()
    assert page["ok"] is True
    assert "直播间背景.png" in page["html"]
    assert "背景图片" in page["html"]

    # 背景图片类型拒绝非图片文件
    bad = client.post(
        "/slirn/api/short_video_upload_material",
        data={"task_id": task.task_id, "project_id": pid, "kind": "bg_image"},
        files={"file": ("bg.mp3", b"fake-audio", "audio/mpeg")},
    ).json()
    assert bad["ok"] is False and "背景图片" in bad["error"]


def test_short_video_preview_popup_and_remove_material(tmp_path):
    """REQ-20261001-096：查看按钮为页内弹窗（无新页签）+ 素材删除端点 + 按 id 取素材文件。"""
    client, task = _client_and_task(tmp_path)
    created = client.post("/slirn/api/short_video_create", json={
        "task_id": task.task_id, "name": "预览删除", "brief": "x",
    }).json()
    pid = created["project"]["id"]

    up = client.post(
        "/slirn/api/short_video_upload_material",
        data={"task_id": task.task_id, "project_id": pid, "kind": "image"},
        files={"file": ("封面.png", b"fake-image", "image/png")},
    ).json()
    assert up["ok"] is True, up
    mid = up["material"]["id"]

    # 素材卡：查看为弹窗按钮（带 url / kind），删除按钮可用，页面无新页签链接
    page = client.post("/slirn/api/short_video_get", json={
        "task_id": task.task_id, "project_id": pid,
    }).json()
    assert page["ok"] is True
    assert 'data-action="sv-preview"' in page["html"]
    assert 'data-kind="image"' in page["html"]
    assert 'data-action="sv-remove-material"' in page["html"]
    assert 'target="_blank"' not in page["html"]

    # kind=material：按素材 id 取文件（自动导入的任务视频同样走这条链路）
    served = client.get("/slirn/api/short_video_file", params={
        "task_id": task.task_id, "project_id": pid, "kind": "material", "name": mid,
    })
    assert served.status_code == 200
    assert served.content == b"fake-image"
    auto_id = created["project"]["materials"][0]["id"]
    served_auto = client.get("/slirn/api/short_video_file", params={
        "task_id": task.task_id, "project_id": pid, "kind": "material", "name": auto_id,
    })
    assert served_auto.status_code == 200
    bad = client.get("/slirn/api/short_video_file", params={
        "task_id": task.task_id, "project_id": pid, "kind": "material", "name": "mat_no",
    }).json()
    assert bad["ok"] is False

    # 删除端点：素材移除、页面刷新后素材消失
    removed = client.post("/slirn/api/short_video_remove_material", json={
        "task_id": task.task_id, "project_id": pid, "material_id": mid,
    }).json()
    assert removed["ok"] is True, removed
    assert mid not in [m["id"] for m in removed["project"]["materials"]]
    assert "封面.png" not in removed["html"]

    missing = client.post("/slirn/api/short_video_remove_material", json={
        "task_id": task.task_id, "project_id": pid, "material_id": "mat_no",
    }).json()
    assert missing["ok"] is False
    noargs = client.post("/slirn/api/short_video_remove_material", json={
        "task_id": task.task_id, "project_id": pid,
    }).json()
    assert noargs["ok"] is False and "material_id" in noargs["error"]


def test_short_video_endpoints_validate_ids(tmp_path):
    client, _task = _client_and_task(tmp_path)
    r = client.post("/slirn/api/short_video_create", json={}).json()
    assert r["ok"] is False and "task_id" in r["error"]
    r = client.post("/slirn/api/short_video_get", json={
        "task_id": "x", "project_id": "y",
    }).json()
    assert r["ok"] is False
    r = client.post("/slirn/api/short_video_render_status", json={}).json()
    assert r["ok"] is False and "job_id" in r["error"]
