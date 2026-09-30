"""短视频混剪 API 测试。"""

from pathlib import Path


def _client_and_task(tmp_path: Path):
    from fastapi.testclient import TestClient
    from tasklib import TaskManager

    from slirn_home.app import build_app

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

    r = client.post("/slirn/api/short_video_ai_storyboard", json={
        "task_id": task.task_id,
        "project_id": pid,
        "brief": "开场讲问题，中间讲方案，最后引导关注",
        "config": {"allow_external_llm": False, "variants": 3},
    }).json()
    assert r["ok"] is True, r
    assert r["project"]["storyboard"]["model"] == "local-rules"
    assert len(r["project"]["storyboard"]["variants"]) == 3


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
