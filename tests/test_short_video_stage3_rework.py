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

    # 默认提示词：保存 + 覆盖。修订后保存默认会尽力同步进历史
    # （这样历史列表里能看到哪一条是默认，前端按文本相等标 ⭐）。
    entry = svc.save_default_prompt(tmp_path, "你是解析助手 A")
    assert entry["prompt"] == "你是解析助手 A"
    st = svc.load_prompt_settings(tmp_path)
    assert st["default_prompt"]["prompt"] == "你是解析助手 A"
    assert [h["prompt"] for h in st["history"]] == ["你是解析助手 A"]
    svc.save_default_prompt(tmp_path, "你是解析助手 B")
    st = svc.load_prompt_settings(tmp_path)
    assert st["default_prompt"]["prompt"] == "你是解析助手 B"
    assert [h["prompt"] for h in st["history"]] == ["你是解析助手 B", "你是解析助手 A"]

    # 空提示词拒绝
    try:
        svc.save_default_prompt(tmp_path, "  ")
        raise AssertionError("空提示词应抛错")
    except svc.ShortVideoError:
        pass

    # 历史：再补 8 条填满 10（默认同步已占 2 条）
    for i in range(8):
        r = svc.save_prompt_history(tmp_path, f"提示词 {i}")
        assert r["status"] == "saved"
    hist = svc.load_prompt_settings(tmp_path)["history"]
    assert len(hist) == 10
    assert hist[0]["prompt"] == "提示词 7"  # 最新在前

    # 第 11 条 → need_confirm；confirm=True → 覆盖最旧
    r = svc.save_prompt_history(tmp_path, "提示词 10")
    assert r["status"] == "need_confirm"
    assert len(svc.load_prompt_settings(tmp_path)["history"]) == 10  # 未写入
    r = svc.save_prompt_history(tmp_path, "提示词 10", confirm=True)
    assert r["status"] == "saved"
    hist = svc.load_prompt_settings(tmp_path)["history"]
    assert len(hist) == 10
    assert hist[0]["prompt"] == "提示词 10"
    assert all(h["prompt"] != "你是解析助手 A" for h in hist), "最旧一条应被丢弃"

    # 已存在 → 置顶 + 不需确认
    r = svc.save_prompt_history(tmp_path, "提示词 3")
    assert r["status"] == "saved"
    hist = svc.load_prompt_settings(tmp_path)["history"]
    assert len(hist) == 10
    assert hist[0]["prompt"] == "提示词 3"
    assert sum(1 for h in hist if h["prompt"] == "提示词 3") == 1

    # 列表已满时设默认为「不在列表」的新提示词 → REQ-20261016-128：
    # 强制覆盖最旧（让默认进历史 → 前端 ⭐ 可显示），不再静默跳过。
    svc.save_default_prompt(tmp_path, "全新默认 Z")
    st = svc.load_prompt_settings(tmp_path)
    assert st["default_prompt"]["prompt"] == "全新默认 Z"
    # 新默认应入列（置顶），且总数仍为 10（最旧的「提示词 10」被覆盖）
    assert st["history"][0]["prompt"] == "全新默认 Z"
    assert len(st["history"]) == 10
    assert sum(1 for h in st["history"] if h["prompt"] == "全新默认 Z") == 1

    # 设默认为「已在列表」的提示词 → 置顶（历史第一眼就能看到哪条是默认）
    svc.save_default_prompt(tmp_path, "提示词 5")
    st = svc.load_prompt_settings(tmp_path)
    assert st["default_prompt"]["prompt"] == "提示词 5"
    assert st["history"][0]["prompt"] == "提示词 5"
    assert len(st["history"]) == 10


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
    """修订六：有图 → vision 调用只做 OCR；主解析调用纯文本但 payload 带参考方向。"""
    tid, pid = _make_project(tmp_path)
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    svc.save_stage3_image(tmp_path, tid, pid, img)
    vision_entry = {"id": "qwen-vl-max", "provider": "dashscope", "protocol": "openai"}

    captured = {"vision": 0, "llm": 0}
    orig_llm = revision_service._call_llm
    orig_vis = revision_service._call_llm_vision

    def fake_vision(sys_p, user_text, image_paths, entry=None, **kw):
        captured["vision"] += 1
        captured["vis_entry"] = entry
        captured["images"] = list(image_paths)
        assert "提取" in user_text or sys_p  # OCR 专用提示词
        return "课程重点：第一讲选课"

    def fake_llm(sys_p, user_p, **kw):
        captured["llm"] += 1
        captured["sys"] = sys_p
        captured["user"] = user_p
        return _FAKE_LLM_JSON

    revision_service._call_llm_vision = fake_vision
    revision_service._call_llm = fake_llm
    try:
        state = sva.run_stage3(
            tmp_path, tid, pid, prompt="VISION-PROMPT", use_image=True, entry=vision_entry,
        )
        assert state["status"] == "done"
        # OCR 一次 + 主文本调用一次；system 用用户提示词
        assert captured["vision"] == 1 and captured["llm"] == 2, (
    "REQ-20261015-119：短 SRT 必触发一轮时长反馈重试（第二次 LLM 调用）"
)
        assert captured["sys"] == "VISION-PROMPT"
        assert captured["vis_entry"]["id"] == "qwen-vl-max"
        assert len(captured["images"]) == 1
        assert captured["images"][0].name == "input_image.png"
        # 主调用 payload：OCR 参考方向 + SRT + 上游前缀
        assert "【课程重点参考" in captured["user"]
        assert "课程重点：第一讲选课" in captured["user"]
        assert "第一句" in captured["user"]
        assert "这是待裁剪的视频srt字幕" in captured["user"]
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
    for key in ("stage5_coarse", "stage6_refine", "stage7_finalize"):
        svc.get_stage_state(proj, key)["status"] = "done"
    svc.save_project(tmp_path, proj)


def test_run_stage3_format_constraint_and_unchanged_prompt(tmp_path):
    """修订五回归：默认走上游「大模型智能剪辑」链路 —— user payload = 上游
    前缀 + raw.srt + 编号列表格式约束；prompt 与生效默认相同不算 "user"。"""
    tid, pid = _make_project(tmp_path)
    captured = {"sys": [], "user": []}
    orig = revision_service._call_llm

    def fake(sys_p, user_p, **kw):
        captured["sys"].append(sys_p)
        captured["user"].append(user_p)
        return _FAKE_LLM_JSON

    revision_service._call_llm = fake
    try:
        # prompt 留空 → 上游默认提示词（模板路径）
        state = sva.run_stage3(tmp_path, tid, pid, template="hook_first")
        assert state["status"] == "done"
        assert state["prompt_source"] == "template"
        assert "srt字幕分析剪辑器" in captured["sys"][-1]
        assert "这是待裁剪的视频srt字幕" in captured["user"][-1]
        assert "00:00:01,000 --> 00:00:02,000" in captured["user"][-1], \
            "上游链路应发送原始 SRT 文本（非逐行重组格式）"
        assert "[开始时间-结束时间]" in captured["user"][-1]
        doc = json.loads(
            svc.stage_highlights_path(tmp_path, tid, pid).read_text(encoding="utf-8")
        )
        assert doc["prompt"] == ""

        # prompt 恰好等于生效默认（上游提示词）→ 归类为模板路径
        default_system = sva._UPSTREAM_CLIP_SYSTEM_PROMPT.format(
            n=4, **sva._duration_prompt_ctx(60.0, 90.0))
        state = sva.run_stage3(
            tmp_path, tid, pid, template="hook_first", prompt=default_system,
        )
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
    for key in ("stage5_coarse", "stage6_refine", "stage7_finalize"):
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

    # 设默认 → 返回 history_html 片段（前端原地刷新历史下拉），默认条目带 ⭐
    r = client.post("/slirn/api/short_video_save_default_prompt",
                    json={"prompt": "API 默认提示词"}).json()
    assert r["ok"] is True, r
    assert "API 默认提示词" in r["history_html"]
    assert "⭐ 默认" in r["history_html"]

    # 历史填满（默认同步已占 1 条，再补 9 条）
    for i in range(9):
        r = client.post("/slirn/api/short_video_prompt_history_save",
                        json={"prompt": f"p{i}"}).json()
        assert r["status"] == "saved"
    # 第 11 条 → need_confirm + toast 提示覆盖
    r = client.post("/slirn/api/short_video_prompt_history_save",
                    json={"prompt": "p9"}).json()
    assert r["ok"] is True and r["status"] == "need_confirm"
    assert "覆盖" in r["toast"]
    # confirm 重发 → saved + history_html
    r = client.post("/slirn/api/short_video_prompt_history_save",
                    json={"prompt": "p9", "confirm": True}).json()
    assert r["status"] == "saved" and len(r["history"]) == 10
    assert "p9" in r["history_html"]

    # 设默认为已在列表的条目 → 历史置顶 + ⭐
    r = client.post("/slirn/api/short_video_save_default_prompt",
                    json={"prompt": "p9"}).json()
    assert r["ok"] is True
    assert "⭐ 默认" in r["history_html"]
    assert r["history"][0]["prompt"] == "p9"

    # 空 prompt 拒绝
    r = client.post("/slirn/api/short_video_save_default_prompt",
                    json={"prompt": " "}).json()
    assert r["ok"] is False

    # UI 注入：项目页含默认提示词（文本域初始填充）+ 历史条目
    r = client.post("/slirn/api/short_video_get",
                    json={"task_id": tid, "project_id": pid}).json()
    assert r["ok"] is True
    assert "API 默认提示词" in r["html"] or "p9" in r["html"]
    assert "p9" in r["html"]
    # 改版 UI 元素
    assert "解析短视频内容" in r["html"]
    assert 'id="slirn-stage3-prompt"' in r["html"]
    assert "🔍 解析" in r["html"]
    # 修订：快速模板 radio 已移除
    assert 'name="sv-stage3-template"' not in r["html"]
    # 修订：截图区块标题（无论是否已传图都渲染）
    assert "智能纪要课程重点截图：" in r["html"]
    # 修订：历史条目可展开查看完整内容 + 默认标记
    assert 'data-action="sv-stage3-toggle-hist"' in r["html"]
    assert "slirn-stage3-hist-full" in r["html"]
    assert "⭐ 默认" in r["html"]


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

    # 页面回显缩略 + 发送开关（修订：复选框前有语义 label）
    r = client.post("/slirn/api/short_video_get",
                    json={"task_id": tid, "project_id": pid}).json()
    assert "slirn-stage3-image-thumb" in r["html"]
    assert 'id="slirn-stage3-use-image"' in r["html"]
    assert "智能纪要课程重点截图" in r["html"]

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


# ---------- REQ-20261013-100：Stage 4 内容优化端点 + 阶段重编号 UI ----------

def test_api_stage4_optimize_endpoint(tmp_path, monkeypatch):
    """端点 plumbing：调 run_stage4_optimize（prompt 透传），返回 state + toast。"""
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app
    from slirn_home import short_video_optimize as opt

    tid, pid = _make_project(tmp_path)
    called = {}

    def fake_run(root, t, p, **kw):
        called["args"] = (t, p, kw)
        return {"status": "done", "highlights": [{"index": 1, "status": "ok"}]}

    monkeypatch.setattr(opt, "run_stage4_optimize", fake_run)
    client = TestClient(build_app(tmp_path).app)
    r = client.post("/slirn/api/short_video_stage4_optimize", json={
        "task_id": tid, "project_id": pid, "prompt": "更口语化",
    }).json()
    assert r["ok"] is True, r
    assert r["stage4_optimize"]["status"] == "done"
    assert "内容优化" in r["toast"]
    assert called["args"][:2] == (tid, pid)
    assert called["args"][2].get("prompt") == "更口语化"

    # 缺参数 → 报错
    r = client.post("/slirn/api/short_video_stage4_optimize", json={}).json()
    assert r["ok"] is False and "task_id" in r["error"]


def test_render_project_seven_stages_and_optimize_card(tmp_path):
    """阶段重编号 UI：nav 7 条（4=内容优化、5=粗剪、6=字幕优化、7=精剪混编）
    + Stage 4 卡（运行按钮 / 统计行 / 告警）；旧 3.5 核验 UI 不再出现。"""
    from slirn_home import short_video_ui as ui

    tid, pid = _make_project(tmp_path)
    proj = svc.load_project(tmp_path, tid, pid)
    proj["pipeline"]["stage3_analyze"] = {
        "status": "done",
        "highlights": [{"index": 1, "title": "T", "start_ms": 0, "end_ms": 1000,
                        "subtitle_lines": [{"no": 1, "src_index": 1, "text": "x",
                                            "start_ms": 0, "end_ms": 1000}]}],
    }
    proj["pipeline"]["stage4_optimize"] = {
        "status": "done",
        "highlights": [{"index": 1, "status": "ok", "lines_before": 3,
                        "lines_after": 4, "kept": 1, "fixed": 1, "split": 1,
                        "deleted": 1}],
        "warnings": ["#1 split 子句拼接与原文不一致，保留原行（宁可不切不可错切）"],
    }
    html = ui.render_project(proj)
    # nav：新 key 顺序（stage4 内容优化 → stage7 精剪混编）
    for frag in ('data-stage-key="stage1"', 'data-stage-key="stage4"',
                 'data-stage-key="stage5"', 'data-stage-key="stage6"',
                 'data-stage-key="stage7"'):
        assert frag in html, frag
    assert "内容优化" in html and "精剪混编" in html
    # Stage 4 卡：done 后按钮变「重新优化」+ 统计行 + 告警列表
    assert 'data-action="sv-stage4-optimize-run"' in html
    assert "✨ 重新优化" in html
    assert "slirn-stage4-opt-row" in html
    assert "slirn-stage4-opt-warns" in html
    # 旧 3.5 核验 UI 彻底移除
    assert "sv-stage3-verify" not in html
    assert "字幕一致性" not in html


def test_short_video_js_stage4_optimize_wiring():
    """前端接线：dispatch 指向 stage4OptimizeRun(btn)；新端点 URL / 旧 verify 调用清除。"""
    js_path = Path(__file__).resolve().parent.parent / "slirn_home" / "static" / "short_video.js"
    js = js_path.read_text(encoding="utf-8")
    # REQ-20261013-111：dispatcher 必须把 btn 传给 handler（防重入 + 按钮 disabled）
    assert "sv-stage4-optimize-run') return stage4OptimizeRun(btn)" in js
    assert "'/short_video_stage4_optimize'" in js
    assert "stage4OptimizeRun" in js
    assert "stage3VerifyRun" not in js
    assert "'/short_video_stage3_verify'" not in js
    # stage6Run 读新 pipeline key（stage5_coarse / stage6_refine）
    assert ".stage5_coarse" in js and ".stage6_refine" in js
    assert ".stage4_coarse" not in js and ".stage5_refine" not in js
