# -*- coding: utf-8 -*-
"""REQ-20260930-092：流程配置·用户模板（flow_profiles）+ 面板自动保存。

红框内（流程配置面板①–⑥全部区段 + 运行模式/停止阶段）所有参数 — 含每个
复选框的选中/不选中 — 两种持久化：
  1) 存为命名模板（tasks/_global_flow_profiles.json，跨任务复用）
  2) 前端改动即自动写入本任务 pipeline.json（pipeline.js 自动保存）

本文件覆盖：模块单测（save/list/delete/重名/原子写/损坏回退/规范化保 False）、
端点（save/list/delete + 校验拒绝 + 无 config 兜底取任务配置）、JS 接线断言。
"""

import json
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


# ---------------- 模块单测 ----------------

def test_flow_profiles_save_and_list(tmp_path):
    """save → list 拿到；config 被 validate_config 规范化为完整 v5 形态。"""
    from slirn_home import flow_profiles as fpx

    assert fpx.list_profiles(tmp_path) == []
    saved = fpx.save_profile(
        tmp_path, "课程全自动",
        {
            "subtitle_generation": {"speaker_diarization": True},
            "subtitle_review": {"accept_all_suggestions": True, "rigor": "low"},
            "run_mode": "to_end",
            "stop_after": None,
        },
        task_id_origin="20260924-001",
    )
    assert saved["name"] == "课程全自动"
    assert saved["id"].startswith("fp_")
    assert saved["saved_at"]
    assert saved["task_id_origin"] == "20260924-001"
    # 规范化：六个阶段键 + 顶层 run_mode/stop_after 全在（缺省阶段被补默认）
    cfg = saved["config"]
    for k in ("subtitle_generation", "subtitle_review", "rough_cut",
              "rough_compose", "optimize", "fine_cut",
              "run_mode", "stop_after"):
        assert k in cfg, f"规范化后 config 应含 {k}，实际 keys={list(cfg)}"
    assert cfg["subtitle_review"]["rigor"] == "low"
    assert cfg["run_mode"] == "to_end"
    assert cfg["stop_after"] is None  # to_end 强制 stop_after=None

    profiles = fpx.list_profiles(tmp_path)
    assert len(profiles) == 1
    assert profiles[0]["id"] == saved["id"]


def test_flow_profiles_sanitize_preserves_explicit_false(tmp_path):
    """显式 False 的复选框值必须原样保留（不被 2026-09-30 全选默认覆盖）。

    用户明确存了「不勾」→ 套用模板时也必须是「不勾」。
    """
    from slirn_home import flow_profiles as fpx

    saved = fpx.save_profile(tmp_path, "人工精修", {
        "subtitle_generation": {"speaker_diarization": False},
        "subtitle_review": {"accept_all_suggestions": False,
                            "link_person_ids": False},
        "rough_cut": {"link_person_ids": False},
        "optimize": {"accept_all_replacements": False},
        "fine_cut": {"enabled": True, "range_enabled": False},
    })
    cfg = saved["config"]
    assert cfg["subtitle_generation"]["speaker_diarization"] is False
    assert cfg["subtitle_review"]["accept_all_suggestions"] is False
    assert cfg["subtitle_review"]["link_person_ids"] is False
    assert cfg["rough_cut"]["link_person_ids"] is False
    assert cfg["optimize"]["accept_all_replacements"] is False
    # radio 导出方式二选一同样要保 False：range_enabled=False（出整个片）
    assert cfg["fine_cut"]["range_enabled"] is False
    assert cfg["fine_cut"]["enabled"] is True  # enabled=False 不再是合法形态
    # （radio 模式语义：跳过精剪走顶层 stop_after，见 validate_config L187 注释）


def test_flow_profiles_delete(tmp_path):
    """save → delete → list 为空；二次删除返回 False 不报错。"""
    from slirn_home import flow_profiles as fpx

    p = fpx.save_profile(tmp_path, "tmp", {"run_mode": "stop_after"})
    assert len(fpx.list_profiles(tmp_path)) == 1
    assert fpx.delete_profile(tmp_path, p["id"]) is True
    assert fpx.list_profiles(tmp_path) == []
    assert fpx.delete_profile(tmp_path, p["id"]) is False


def test_flow_profiles_duplicate_name_appends_suffix(tmp_path):
    """同名模板自动加 `(2)` / `(3)` 后缀。"""
    from slirn_home import flow_profiles as fpx

    p1 = fpx.save_profile(tmp_path, "课程全自动", {"run_mode": "to_end"})
    p2 = fpx.save_profile(tmp_path, "课程全自动", {"run_mode": "stop_after"})
    p3 = fpx.save_profile(tmp_path, "课程全自动", {"run_mode": "to_end"})
    assert p1["name"] == "课程全自动"
    assert p2["name"] == "课程全自动 (2)"
    assert p3["name"] == "课程全自动 (3)"
    assert len(fpx.list_profiles(tmp_path)) == 3


def test_flow_profiles_empty_name_becomes_unnamed(tmp_path):
    """空名兜底为「未命名」。"""
    from slirn_home import flow_profiles as fpx

    p = fpx.save_profile(tmp_path, "   ", {})
    assert p["name"] == "未命名"


def test_flow_profiles_atomic_write_and_no_tmp_leftover(tmp_path):
    """连续 save → 文件始终合法 JSON，无 .tmp 残留。"""
    from slirn_home import flow_profiles as fpx

    fpx.save_profile(tmp_path, "first", {})
    fpx.save_profile(tmp_path, "second", {})
    raw = (tmp_path / fpx.GLOBAL_FLOW_PROFILES_REL).read_text(encoding="utf-8")
    data = json.loads(raw)
    assert isinstance(data["profiles"], list)
    assert len(data["profiles"]) == 2
    assert not (tmp_path / fpx.GLOBAL_FLOW_PROFILES_REL).with_suffix(".json.tmp").exists()


def test_flow_profiles_corrupt_file_falls_back_to_empty(tmp_path):
    """全局文件损坏 → 回退空注册表（不抛异常）。"""
    from slirn_home import flow_profiles as fpx

    target = tmp_path / fpx.GLOBAL_FLOW_PROFILES_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{ 这不是合法 JSON", encoding="utf-8")
    assert fpx.list_profiles(tmp_path) == []
    # 回退后仍可正常保存（不因损坏文件卡死）
    p = fpx.save_profile(tmp_path, "恢复后", {})
    assert fpx.get_profile(tmp_path, p["id"]) is not None


# ---------------- 端点（TestClient） ----------------

def _mk_task(tmp_path, name="flow-tpl-task"):
    from tasklib import TaskManager

    video = tmp_path / "test.mp4"
    video.write_bytes(b"fake-video")
    mgr = TaskManager(tmp_path)
    t = mgr.create(name=name, original_video=video)
    return mgr, t


def test_save_list_delete_flow_profile_endpoints(tmp_path):
    """save_flow_profile → list_flow_profiles → delete_flow_profile 全链路。"""
    from fastapi.testclient import TestClient

    from slirn_home import flow_profiles as fpx
    from slirn_home.app import build_app

    mgr, t = _mk_task(tmp_path)
    built = build_app(tmp_path)
    client = TestClient(built.app)

    cfg = {
        "subtitle_generation": {"speaker_diarization": False},
        "subtitle_review": {"accept_all_suggestions": False, "rigor": "high"},
        "run_mode": "stop_after", "stop_after": "rough_cut",
    }
    resp = client.post("/slirn/api/save_flow_profile",
                       json={"task_id": t.task_id, "name": "口播精修", "config": cfg})
    body = resp.json()
    assert body["ok"] is True, f"保存模板应成功：{body}"
    pid = body["profile"]["id"]
    assert body["profile"]["name"] == "口播精修"
    assert body["profile"]["task_id_origin"] == t.task_id
    # 显式 False 必须原样存进模板
    assert body["profile"]["config"]["subtitle_generation"]["speaker_diarization"] is False
    assert body["profile"]["config"]["subtitle_review"]["accept_all_suggestions"] is False

    resp = client.post("/slirn/api/list_flow_profiles", json={})
    lst = resp.json()
    assert lst["ok"] is True
    assert len(lst["profiles"]) == 1
    assert lst["profiles"][0]["id"] == pid
    assert "config" in lst["profiles"][0], "list 必须带完整 config（前端套用直接喂 renderPanel）"

    resp = client.post("/slirn/api/delete_flow_profile",
                       json={"profile_id": pid})
    assert resp.json()["ok"] is True
    assert fpx.list_profiles(tmp_path) == []


def test_save_flow_profile_defaults_to_task_pipeline_config(tmp_path):
    """不带 config 调 save_flow_profile → 兜底取任务 pipeline.json 的 config。"""
    from fastapi.testclient import TestClient

    from slirn_home import flow_profiles as fpx
    from slirn_home import pipeline_service
    from slirn_home.app import build_app

    mgr, t = _mk_task(tmp_path, name="defaults-task")
    outputs_dir = mgr.tasks_dir / t.task_id / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    pipeline_service.save_pipeline(outputs_dir, {
        "subtitle_review": {"rigor": "low"},
        "run_mode": "to_end",
    })

    built = build_app(tmp_path)
    client = TestClient(built.app)
    resp = client.post("/slirn/api/save_flow_profile",
                       json={"task_id": t.task_id, "name": "取任务配置"})
    body = resp.json()
    assert body["ok"] is True, body
    cfg = body["profile"]["config"]
    assert cfg["subtitle_review"]["rigor"] == "low"
    assert cfg["run_mode"] == "to_end"
    # 与磁盘上的任务配置一致
    assert fpx.get_profile(tmp_path, body["profile"]["id"])["config"] == cfg


def test_save_flow_profile_endpoint_rejects_bad_input(tmp_path):
    """缺 task_id / 缺 name / 名字超长 / 任务不存在 → 明确报错。"""
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    mgr, t = _mk_task(tmp_path, name="reject-task")
    built = build_app(tmp_path)
    client = TestClient(built.app)

    r = client.post("/slirn/api/save_flow_profile", json={"name": "x"})
    assert r.json()["ok"] is False and "task_id" in r.json()["error"]

    r = client.post("/slirn/api/save_flow_profile", json={"task_id": t.task_id})
    assert r.json()["ok"] is False and "模板名" in r.json()["error"]

    r = client.post("/slirn/api/save_flow_profile",
                    json={"task_id": t.task_id, "name": "超" * 31})
    assert r.json()["ok"] is False and "30" in r.json()["error"]

    r = client.post("/slirn/api/save_flow_profile",
                    json={"task_id": "20990101-999", "name": "幽灵任务"})
    assert r.json()["ok"] is False and "任务不存在" in r.json()["error"]


def test_delete_flow_profile_endpoint_rejects_missing_id(tmp_path):
    from fastapi.testclient import TestClient

    from slirn_home.app import build_app

    built = build_app(tmp_path)
    client = TestClient(built.app)
    r = client.post("/slirn/api/delete_flow_profile", json={})
    assert r.json()["ok"] is False and "profile_id" in r.json()["error"]
    r = client.post("/slirn/api/delete_flow_profile",
                    json={"profile_id": "fp_nope"})
    assert r.json()["ok"] is False and "不存在" in r.json()["error"]


# ---------------- 前端接线（pipeline.js 源码断言） ----------------

def _pipeline_js() -> str:
    return (_REPO / "slirn_home" / "static" / "pipeline.js").read_text(
        encoding="utf-8")


def test_pipeline_js_has_flow_template_ui():
    """模板下拉含「另存为模板」项 + 删除按钮（含 handler 分支与端点）。"""
    src = _pipeline_js()
    assert 'value="fp:_save_as"' in src, "模板下拉应有「💾 将当前配置存为模板…」固定项"
    assert 'data-action="pipe-flow-tpl-del"' in src, "应渲染模板删除按钮"
    assert "action === 'pipe-flow-tpl-del'" in src, "click 委托应有删除分支"
    # 端点经 SLIRN_API（'/slirn/api'）前缀拼接调用
    assert "SLIRN_API + '/save_flow_profile'" in src
    assert "SLIRN_API + '/list_flow_profiles'" in src
    assert "SLIRN_API + '/delete_flow_profile'" in src


def test_pipeline_js_autosave_wiring():
    """自动保存：防抖调度 + 立即保存 + change/input 双监听 + 套用即落盘。"""
    src = _pipeline_js()
    assert "function _autoSaveSchedule" in src
    assert "function _autoSaveNow" in src
    assert "/pipeline_save" in src
    # change 委托的通用分支（面板内任意控件）+ input 监听（文本类）都接调度
    assert src.count("_autoSaveSchedule();") >= 3, (
        "run_mode 分支 / change 通用分支 / input 监听 至少三处调用防抖调度")
    # 套用模板（内置 + 用户）都立即落盘
    assert src.count("_autoSaveNow();") >= 2
    # 渲染后填模板下拉（loadPanel / 套用 / 重置 全覆盖）
    assert "_populateFlowTemplates()" in src


def test_pipeline_js_save_config_uses_saved_at():
    """修复：手动保存后时间戳读 r.saved_at（后端实际返回字段）。"""
    src = _pipeline_js()
    assert "r.saved_at || r.updated_at" in src, (
        "saveConfig 应回退读 saved_at（此前误读 updated_at → 时间戳从不更新）")


def test_app_py_has_flow_profile_endpoints():
    """app.py 挂载三个端点 + 引入 flow_profiles 模块。"""
    src = (_REPO / "slirn_home" / "app.py").read_text(encoding="utf-8")
    assert '"/slirn/api/list_flow_profiles"' in src
    assert '"/slirn/api/save_flow_profile"' in src
    assert '"/slirn/api/delete_flow_profile"' in src
    assert "flow_profiles" in src
