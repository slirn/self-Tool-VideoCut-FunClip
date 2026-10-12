"""REQ-20261012-099：Stage 3「解析短视频内容」改版测试。

覆盖：全局提示词两级存储（默认 + 最新 10 条历史）、截图存取、
run_stage3 的 prompt 优先级与多模态路径、单片段重新解析、相关 API 端点。
"""

import json
from pathlib import Path

from slirn_home import revision_service
from slirn_home import short_video_analyze as sva
from slirn_home import short_video_service as svc


def _make_project(tmp_path: Path):
    """建任务 + split 项目 + 两行 raw.srt；返回 (tid, pid)。"""
    from tasklib import TaskManager

    video = tmp_path / "src.mp4"
    video.write_bytes(b"fake")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name="t", original_video=video)
    tid = task.task_id
    proj = svc.create_project(tmp_path, tid, name="p", brief="")
    pid = proj["id"]
    rp = svc.stage_raw_srt_path(tmp_path, tid, pid)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n第一句\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\n第二句\n\n",
        encoding="utf-8",
    )
    return tid, pid


_FAKE_LLM_JSON = json.dumps({
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


# ---------- 全局提示词两级存储 ----------

def test_prompt_settings_default_and_history(tmp_path):
    # 空结构
    assert svc.load_prompt_settings(tmp_path) == {
        "default_prompt": None, "history": [],
    }

    # 默认提示词：保存 + 覆盖
    entry = svc.save_default_prompt(tmp_path, "你是解析助手 A")
    assert entry["prompt"] == "你是解析助手 A"
    assert svc.load_prompt_settings(tmp_path)["default_prompt"]["prompt"] == "你是解析助手 A"
    svc.save_default_prompt(tmp_path, "你是解析助手 B")
    assert svc.load_prompt_settings(tmp_path)["default_prompt"]["prompt"] == "你是解析助手 B"

    # 空提示词拒绝
    try:
        svc.save_default_prompt(tmp_path, "  ")
        raise AssertionError("空提示词应抛错")
    except svc.ShortVideoError:
        pass

    # 历史：填满 10 条
    for i in range(10):
        r = svc.save_prompt_history(tmp_path, f"提示词 {i}")
        assert r["status"] == "saved"
    hist = svc.load_prompt_settings(tmp_path)["history"]
    assert len(hist) == 10
    assert hist[0]["prompt"] == "提示词 9"  # 最新在前

    # 第 11 条 → need_confirm；confirm=True → 覆盖最旧
    r = svc.save_prompt_history(tmp_path, "提示词 10")
    assert r["status"] == "need_confirm"
    assert len(svc.load_prompt_settings(tmp_path)["history"]) == 10  # 未写入
    r = svc.save_prompt_history(tmp_path, "提示词 10", confirm=True)
    assert r["status"] == "saved"
    hist = svc.load_prompt_settings(tmp_path)["history"]
    assert len(hist) == 10
    assert hist[0]["prompt"] == "提示词 10"
    assert all(h["prompt"] != "提示词 0" for h in hist), "最旧一条应被丢弃"

    # 已存在 → 置顶 + 不需确认
    r = svc.save_prompt_history(tmp_path, "提示词 3")
    assert r["status"] == "saved"
    hist = svc.load_prompt_settings(tmp_path)["history"]
    assert len(hist) == 10
    assert hist[0]["prompt"] == "提示词 3"
    assert sum(1 for h in hist if h["prompt"] == "提示词 3") == 1

    # 默认与历史互不影响
    assert svc.load_prompt_settings(tmp_path)["default_prompt"]["prompt"] == "你是解析助手 B"


# ---------- Stage 3 截图存取 ----------

def test_stage3_image_save_remove_current(tmp_path):
    tid, pid = _make_project(tmp_path)

    # 无图 → current None
    assert svc.stage3_current_image(tmp_path, tid, pid) is None

    # 非法扩展名拒绝
    bad = tmp_path / "x.txt"
    bad.write_text("no")
    try:
        svc.save_stage3_image(tmp_path, tid, pid, bad)
        raise AssertionError("非法扩展名应抛错")
    except svc.ShortVideoError:
        pass

    # 保存 png → state + 文件
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    entry = svc.save_stage3_image(tmp_path, tid, pid, img)
    assert entry["name"] == "input_image.png"
    cur = svc.stage3_current_image(tmp_path, tid, pid)
    assert cur and cur["name"] == "input_image.png"

    # 换 jpg → png 被清掉，只留一张
    img2 = tmp_path / "shot.jpg"
    img2.write_bytes(b"\xff\xd8-fake")
    svc.save_stage3_image(tmp_path, tid, pid, img2)
    stage_dir = svc.stage3_dir(tmp_path, tid, pid)
    assert {p.name for p in stage_dir.glob("input_image.*")} == {"input_image.jpg"}
    assert svc.stage3_current_image(tmp_path, tid, pid)["name"] == "input_image.jpg"

    # 移除
    assert svc.remove_stage3_image(tmp_path, tid, pid) is True
    assert svc.stage3_current_image(tmp_path, tid, pid) is None
    assert not list(stage_dir.glob("input_image.*"))
    assert svc.remove_stage3_image(tmp_path, tid, pid) is False  # 再移除 → False

    # 悬空引用（state 有记录但文件没了）→ current None
    proj = svc.load_project(tmp_path, tid, pid)
    s3 = svc.get_stage_state(proj, "stage3_analyze")
    s3["image"] = {"name": "input_image.png", "updated_at": "x"}
    svc.set_stage_state(proj, "stage3_analyze", s3)
    svc.save_project(tmp_path, proj)
    assert svc.stage3_current_image(tmp_path, tid, pid) is None


# ---------- run_stage3：prompt 优先级 ----------

def test_run_stage3_user_prompt_priority(tmp_path):
    tid, pid = _make_project(tmp_path)
    svc.save_custom_prompt(tmp_path, tid, pid, template="hook_first", prompt="OVERRIDE-MARKER")

    captured = {"sys": []}
    orig = revision_service._call_llm

    def fake(sys_p, user_p, **kw):
        captured["sys"].append(sys_p)
        return _FAKE_LLM_JSON

    revision_service._call_llm = fake
    try:
        # 1) prompt 参数最优先（盖过模板自定义覆盖）
        state = sva.run_stage3(
            tmp_path, tid, pid, template="hook_first", prompt="USER-PROMPT-MARKER",
        )
        assert state["status"] == "done"
        assert state["prompt_source"] == "user"
        assert captured["sys"] and captured["sys"][-1] == "USER-PROMPT-MARKER"
        doc = json.loads(
            svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
        )
        assert doc["prompt"] == "USER-PROMPT-MARKER"

        # 2) 无 prompt → 模板自定义覆盖生效
        state = sva.run_stage3(tmp_path, tid, pid, template="hook_first")
        assert state["prompt_source"] == "custom_override"
        assert captured["sys"][-1] == "OVERRIDE-MARKER"
    finally:
        revision_service._call_llm = orig


def test_run_stage3_use_image_errors(tmp_path):
    tid, pid = _make_project(tmp_path)
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    svc.save_stage3_image(tmp_path, tid, pid, img)

    # 无图 + use_image → 报错
    svc.remove_stage3_image(tmp_path, tid, pid)
    try:
        sva.run_stage3(tmp_path, tid, pid, use_image=True)
        raise AssertionError("无图 use_image 应抛错")
    except svc.ShortVideoError as e:
        assert "截图" in str(e)
    svc.save_stage3_image(tmp_path, tid, pid, img)

    # 有图 + 非 vision entry → 报错且提示多模态
    non_vision = {"id": "deepseek-chat", "provider": "x", "protocol": "openai"}
    try:
        sva.run_stage3(tmp_path, tid, pid, use_image=True, entry=non_vision)
        raise AssertionError("非 vision + use_image 应抛错")
    except svc.ShortVideoError as e:
        assert "多模态" in str(e) or "图片" in str(e)

    # use_image=False + 非 vision → 不报错（走纯文本）
    orig = revision_service._call_llm
    revision_service._call_llm = lambda s, u, **kw: _FAKE_LLM_JSON
    try:
        state = sva.run_stage3(
            tmp_path, tid, pid, use_image=False, entry=non_vision, prompt="p",
        )
        assert state["status"] == "done"
    finally:
        revision_service._call_llm = orig


def test_run_stage3_vision_call(tmp_path):
    tid, pid = _make_project(tmp_path)
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    svc.save_stage3_image(tmp_path, tid, pid, img)
    vision_entry = {"id": "qwen-vl-max", "provider": "dashscope", "protocol": "openai"}

    captured = {}
    orig_llm = revision_service._call_llm
    orig_vis = revision_service._call_llm_vision

    def fake_vision(sys_p, user_text, image_paths, entry=None, **kw):
        captured["sys"] = sys_p
        captured["user"] = user_text
        captured["images"] = list(image_paths)
        captured["entry"] = entry
        return _FAKE_LLM_JSON

    revision_service._call_llm_vision = fake_vision
    revision_service._call_llm = lambda s, u, **kw: (_ for _ in ()).throw(
        AssertionError("有图时不应走纯文本调用"))
    try:
        state = sva.run_stage3(
            tmp_path, tid, pid, prompt="VISION-PROMPT", use_image=True, entry=vision_entry,
        )
        assert state["status"] == "done"
        assert captured["sys"] == "VISION-PROMPT"
        assert captured["entry"]["id"] == "qwen-vl-max"
        assert len(captured["images"]) == 1
        assert captured["images"][0].name == "input_image.png"
        # user payload 含 SRT 与格式约束
        assert "第一句" in captured["user"]
        assert "highlights" in captured["user"]
        # 产物元数据记录截图
        doc = json.loads(
            svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
        )
        assert doc["image"] == "input_image.png"
        assert state["image_used"].endswith("input_image.png")
    finally:
        revision_service._call_llm = orig_llm
        revision_service._call_llm_vision = orig_vis


# ---------- 单片段重新解析 ----------

def _seed_highlights(tmp_path, tid, pid):
    """先整跑一次产 highlights.json（2 条），mock 返回两条。"""
    two = json.dumps({
        "highlights": [
            {"id": "h1", "title": "主题一", "start_ms": 0, "end_ms": 30000,
             "subtitle_lines": [{"src_index": 1, "text": "行 1"},
                                {"src_index": 2, "text": "行 2"}]},
            {"id": "h2", "title": "主题二", "start_ms": 0, "end_ms": 30000,
             "subtitle_lines": [{"src_index": 2, "text": "行 2"}]},
        ],
    }, ensure_ascii=False)
    orig = revision_service._call_llm
    revision_service._call_llm = lambda s, u, **kw: two
    try:
        state = sva.run_stage3(tmp_path, tid, pid, prompt="整跑提示词")
        assert state["status"] == "done"
    finally:
        revision_service._call_llm = orig
    # 把下游标记成 done，验证 rerun 后会被重置
    proj = svc.load_project(tmp_path, tid, pid)
    for key in ("stage4_coarse", "stage5_refine", "stage6_finalize"):
        svc.get_stage_state(proj, key)["status"] = "done"
    svc.save_project(tmp_path, proj)


def test_run_stage3_format_constraint_and_unchanged_prompt(tmp_path):
    """评审修复回归：user payload 的 JSON schema 用单花括号；prompt 与模板
    生效文本相同时不算 "user"（badge/doc 溯源不失真）。"""
    tid, pid = _make_project(tmp_path)
    captured = {"sys": [], "user": []}
    orig = revision_service._call_llm

    def fake(sys_p, user_p, **kw):
        captured["sys"].append(sys_p)
        captured["user"].append(user_p)
        return _FAKE_LLM_JSON

    revision_service._call_llm = fake
    try:
        # prompt 留空 → 模板路径；user 侧 schema 必须是单花括号
        state = sva.run_stage3(tmp_path, tid, pid, template="hook_first")
        assert state["status"] == "done"
        assert state["prompt_source"] == "template"
        assert '"highlights"' in captured["user"][-1]
        assert "{{" not in captured["user"][-1].split("输出严格 JSON")[1], \
            "格式约束里不应有 .format 转义的双花括号"
        doc = json.loads(
            svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
        )
        assert doc["prompt"] == ""

        # prompt 恰好等于模板生效文本 → 归类为模板路径
        tpl_text = sva._PROMPT_TEMPLATES["hook_first"]
        state = sva.run_stage3(tmp_path, tid, pid, template="hook_first", prompt=tpl_text)
        assert state["prompt_source"] == "template"
    finally:
        revision_service._call_llm = orig


def test_rerun_stage3_one_brace_title(tmp_path):
    """评审修复回归：title 含 { } 时 .format 不再抛 KeyError。"""
    tid, pid = _make_project(tmp_path)
    _seed_highlights(tmp_path, tid, pid)
    # 把片段 1 的 title 改成含花括号
    hp = svc.stage_highlights_path(tmp_path, tid, pid)
    doc = json.loads(hp.read_text(encoding="utf-8"))
    doc["highlights"][0]["title"] = "{前3秒} 钩子"
    hp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    captured = {"sys": []}
    orig = revision_service._call_llm
    revision_service._call_llm = lambda s, u, **kw: (
        captured["sys"].append(s) or _FAKE_LLM_JSON)
    try:
        state = sva.rerun_stage3_one(tmp_path, tid, pid, 1, prompt="p")
        assert state["status"] == "done"
        assert "{前3秒} 钩子" in captured["sys"][0]
    finally:
        revision_service._call_llm = orig


def test_rerun_stage3_one_image_mirror(tmp_path):
    """评审修复回归：rerun 的截图/提示词来源镜像与 doc.image 同步。"""
    tid, pid = _make_project(tmp_path)
    _seed_highlights(tmp_path, tid, pid)
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    svc.save_stage3_image(tmp_path, tid, pid, img)
    vision_entry = {"id": "qwen-vl-max", "provider": "dashscope", "protocol": "openai"}

    orig_vis = revision_service._call_llm_vision
    orig_llm = revision_service._call_llm
    revision_service._call_llm_vision = lambda s, u, ips, entry=None, **kw: _FAKE_LLM_JSON
    revision_service._call_llm = lambda s, u, **kw: _FAKE_LLM_JSON
    try:
        state = sva.rerun_stage3_one(
            tmp_path, tid, pid, 1, prompt="p", use_image=True, entry=vision_entry,
        )
        assert state["status"] == "done"
        assert state["image_used"].endswith("input_image.png")
        doc = json.loads(
            svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
        )
        assert doc["image"] == "input_image.png"
        # 再跑一次不带图 → 镜像清空（多入口状态对称）
        state = sva.rerun_stage3_one(
            tmp_path, tid, pid, 1, prompt="p", use_image=False, entry=vision_entry,
        )
        assert state["status"] == "done"
        assert "image_used" not in state
        doc = json.loads(
            svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
        )
        assert doc["image"] == ""
    finally:
        revision_service._call_llm_vision = orig_vis
        revision_service._call_llm = orig_llm


def test_stage3_image_traversal_guard(tmp_path):
    """评审修复回归：state 里被手改出路径分隔符的 image.name 视为无图。"""
    tid, pid = _make_project(tmp_path)
    proj = svc.load_project(tmp_path, tid, pid)
    s3 = svc.get_stage_state(proj, "stage3_analyze")
    s3["image"] = {"name": "../../secret.png", "updated_at": "x"}
    svc.set_stage_state(proj, "stage3_analyze", s3)
    svc.save_project(tmp_path, proj)
    assert svc.stage3_current_image(tmp_path, tid, pid) is None
    # 解析入口也不应把穿越路径发给 LLM
    try:
        sva.run_stage3(tmp_path, tid, pid, use_image=True)
        raise AssertionError("穿越路径应被拒绝")
    except svc.ShortVideoError:
        pass


def test_rerun_stage3_one(tmp_path):
    tid, pid = _make_project(tmp_path)
    _seed_highlights(tmp_path, tid, pid)

    replaced = json.dumps({
        "highlights": [{
            "id": "h1", "title": "", "start_ms": 1000, "end_ms": 6000,
            "subtitle_lines": [{"src_index": 1, "text": "新行 1"},
                               {"src_index": 2, "text": "新行 2"}],
        }],
    }, ensure_ascii=False)
    captured = {"sys": [], "user": []}
    orig = revision_service._call_llm

    def fake(sys_p, user_p, **kw):
        captured["sys"].append(sys_p)
        captured["user"].append(user_p)
        return replaced

    revision_service._call_llm = fake
    try:
        state = sva.rerun_stage3_one(tmp_path, tid, pid, 1, prompt="重解析提示词")
        assert state["status"] == "done"
    finally:
        revision_service._call_llm = orig

    # 定向指令进了 system；user 含 SRT + 单条要求
    assert "主题一" in captured["sys"][0]
    assert "重解析提示词" in captured["sys"][0]
    assert "第一句" in captured["user"][0]

    doc = json.loads(
        svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
    )
    hls = {int(h["index"]): h for h in doc["highlights"]}
    # 片段 1 被替换：title 为空 → 保留旧 title；时间/字幕更新；index 不变
    assert hls[1]["title"] == "主题一"
    assert hls[1]["start_ms"] == 1000 and hls[1]["end_ms"] == 6000
    assert hls[1]["subtitle_lines"][0]["text"] == "新行 1"
    # 片段 2 不变
    assert hls[2]["title"] == "主题二"
    # LLM 原文落独立文件
    assert (svc.stage3_dir(tmp_path, tid, pid) / "llm_raw_hl1.txt").is_file()
    # 下游重置
    proj = svc.load_project(tmp_path, tid, pid)
    for key in ("stage4_coarse", "stage5_refine", "stage6_finalize"):
        assert svc.get_stage_state(proj, key).get("status") == "pending", key


def test_rerun_stage3_one_missing(tmp_path):
    tid, pid = _make_project(tmp_path)
    _seed_highlights(tmp_path, tid, pid)
    try:
        sva.rerun_stage3_one(tmp_path, tid, pid, 99)
        raise AssertionError("不存在的片段应抛错")
    except svc.ShortVideoError:
        pass


# ---------- API 端点 ----------

def test_api_prompt_settings_endpoints(tmp_path):
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    client = TestClient(build_app(tmp_path).app)

    r = client.post("/slirn/api/short_video_save_default_prompt",
                    json={"prompt": "API 默认提示词"}).json()
    assert r["ok"] is True, r

    # 历史填满
    for i in range(10):
        r = client.post("/slirn/api/short_video_prompt_history_save",
                        json={"prompt": f"p{i}"}).json()
        assert r["status"] == "saved"
    # 第 11 条 → need_confirm + toast 提示覆盖
    r = client.post("/slirn/api/short_video_prompt_history_save",
                    json={"prompt": "p10"}).json()
    assert r["ok"] is True and r["status"] == "need_confirm"
    assert "覆盖" in r["toast"]
    # confirm 重发 → saved
    r = client.post("/slirn/api/short_video_prompt_history_save",
                    json={"prompt": "p10", "confirm": True}).json()
    assert r["status"] == "saved" and len(r["history"]) == 10

    # 空 prompt 拒绝
    r = client.post("/slirn/api/short_video_save_default_prompt",
                    json={"prompt": " "}).json()
    assert r["ok"] is False

    # UI 注入：项目页应含默认提示词 + 历史条目
    r = client.post("/slirn/api/short_video_get",
                    json={"task_id": tid, "project_id": pid}).json()
    assert r["ok"] is True
    assert "API 默认提示词" in r["html"]
    assert "p10" in r["html"]
    # 改版 UI 元素
    assert "解析短视频内容" in r["html"]
    assert 'id="slirn-stage3-prompt"' in r["html"]
    assert "🔍 解析" in r["html"]


def test_api_stage3_image_endpoints(tmp_path):
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    client = TestClient(build_app(tmp_path).app)

    png = b"\x89PNG-fake-bytes"
    r = client.post(
        "/slirn/api/short_video_stage3_upload_image",
        data={"task_id": tid, "project_id": pid},
        files={"file": ("shot.png", png, "image/png")},
    ).json()
    assert r["ok"] is True, r
    assert r["image"]["name"] == "input_image.png"

    # 页面回显缩略 + 发送开关
    r = client.post("/slirn/api/short_video_get",
                    json={"task_id": tid, "project_id": pid}).json()
    assert "slirn-stage3-image-thumb" in r["html"]
    assert 'id="slirn-stage3-use-image"' in r["html"]

    # kind=stage3 文件访问
    resp = client.get("/slirn/api/short_video_file", params={
        "task_id": tid, "project_id": pid, "kind": "stage3", "name": "input_image.png",
    })
    assert resp.status_code == 200
    assert resp.content == png

    # 非法扩展名
    r = client.post(
        "/slirn/api/short_video_stage3_upload_image",
        data={"task_id": tid, "project_id": pid},
        files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
    ).json()
    assert r["ok"] is False and "格式" in r["error"]

    # 移除
    r = client.post("/slirn/api/short_video_stage3_remove_image",
                    json={"task_id": tid, "project_id": pid}).json()
    assert r["ok"] is True and r["removed"] is True
    r = client.post("/slirn/api/short_video_get",
                    json={"task_id": tid, "project_id": pid}).json()
    assert "slirn-stage3-image-thumb" not in r["html"]


def test_api_rerun_one_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    _seed_highlights(tmp_path, tid, pid)

    replaced = json.dumps({
        "highlights": [{"id": "h2", "title": "新主题二", "start_ms": 1000,
                        "end_ms": 6000,
                        "subtitle_lines": [{"src_index": 2, "text": "替换行"}]}],
    }, ensure_ascii=False)
    orig = revision_service._call_llm
    revision_service._call_llm = lambda s, u, **kw: replaced
    try:
        r = client = TestClient(build_app(tmp_path).app)
        resp = client.post("/slirn/api/short_video_stage3_rerun_one", json={
            "task_id": tid, "project_id": pid, "hl_index": 2,
            "prompt": "只重解析第二条", "use_image": False,
        }).json()
        assert resp["ok"] is True, resp
        assert resp["stage3"]["status"] == "done"

        doc = json.loads(
            svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
        )
        h2 = next(h for h in doc["highlights"] if int(h["index"]) == 2)
        assert h2["title"] == "新主题二"

        # 无效 hl_index
        resp = client.post("/slirn/api/short_video_stage3_rerun_one", json={
            "task_id": tid, "project_id": pid, "hl_index": 0,
        }).json()
        assert resp["ok"] is False
    finally:
        revision_service._call_llm = orig
