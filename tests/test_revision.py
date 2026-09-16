"""测试字幕修订服务 — REQ-20260915-005。

单元层（LLM 全 mock，不打网络）：解析防御、决策合并、后台 job 落盘、
渲染三态、阶段状态推进。真实 LLM 链路走 E2E（work/REQ-20260915-005-subtitle-review/）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in sys.path:
    sys.path.insert(0, str(SLIRN_STANDALONE))


def _make_mgr(tmp_path: Path):
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake-video")
    return TaskManager(tmp_path), video


def _write_subtitle(mgr, tid: str, segs: list[dict]) -> Path:
    outputs = mgr.tasks_dir / tid / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "subtitle.json").write_text(
        json.dumps({"version": 1, "segments": segs}, ensure_ascii=False), encoding="utf-8"
    )
    return outputs


SEGS = [
    {"i": 1, "start_ms": 0, "end_ms": 1500, "start": "00:00:00.000", "end": "00:00:01.500", "text": "嗯嗯大家好"},
    {"i": 2, "start_ms": 1500, "end_ms": 4000, "start": "00:00:01.500", "end": "00:00:04.000", "text": "今天讲第一课"},
    {"i": 3, "start_ms": 4000, "end_ms": 7000, "start": "00:00:04.000", "end": "00:00:07.000", "text": "那个那个就是说我们开始吧"},
]


# ---------- parse_llm_suggestions 防御 ----------

def test_parse_basic_with_fence():
    from slirn_home.revision_service import parse_llm_suggestions

    raw = """```json
    [{"i": 1, "category": "split", "keep_text": "大家好", "note": "连续「嗯」为语气词"},
     {"i": 2, "category": "keep", "keep_text": null, "note": "正常内容"},
     {"i": 3, "category": "delete", "note": "口头禅「那个」「就是说」"}]
    ```"""
    out = parse_llm_suggestions(raw, SEGS)
    assert [o["i"] for o in out] == [1, 2, 3]
    assert out[0]["category"] == "split" and out[0]["keep_text"] == "大家好"
    assert out[1]["category"] == "keep" and out[1]["keep_text"] is None
    assert out[2]["category"] == "delete"


def test_parse_defensive_fills_and_normalizes():
    from slirn_home.revision_service import parse_llm_suggestions

    raw = json.dumps([
        {"i": 1, "category": "maybe_delete", "note": ""},   # 未知类别 → review；空说明 → 兜底
        {"i": 99, "category": "keep", "note": "未知段丢弃"},
        {"category": "keep", "note": "缺 i 丢弃"},
        {"i": 3, "category": "split", "keep_text": "  ", "note": "split 无有效保留文本"},
    ])
    out = parse_llm_suggestions(raw, SEGS)
    assert len(out) == 3  # 条目数 == 段数（漏答回填）
    assert out[0]["category"] == "review" and out[0]["note"] == "模型未给出说明"
    assert out[1]["category"] == "review" and out[1]["note"] == "模型未返回该段，请人工复核"
    assert out[2]["category"] == "split" and out[2]["keep_text"] is None


def test_parse_fix_category():
    """内容更正（REQ-20260916-006）：keep_text 保留更正文本；无有效更正文本 → 降级人工复核。"""
    from slirn_home.revision_service import parse_llm_suggestions

    raw = json.dumps([
        {"i": 1, "category": "fix", "keep_text": "神经网络入门", "note": "「神精」应为「神经」"},
        {"i": 2, "category": "fix", "keep_text": "   ", "note": "有错字但更正文本为空白"},
        {"i": 3, "category": "fix", "note": "缺 keep_text 字段"},
    ])
    out = parse_llm_suggestions(raw, SEGS)
    assert out[0]["category"] == "fix" and out[0]["keep_text"] == "神经网络入门"
    assert out[0]["note"] == "「神精」应为「神经」", "note 即原文对照说明"
    # 更正无更正文本 → 无从执行，降级 review 并保留模型发现的问题（前缀提示人工修改）
    for o in (out[1], out[2]):
        assert o["category"] == "review" and o["keep_text"] is None, o
        assert o["note"].startswith("模型发现文字错误但未给出更正文本"), o["note"]
    assert "有错字但更正文本为空白" in out[1]["note"], "模型发现的问题保留在说明中"


def test_parse_invalid_json_raises():
    from slirn_home.revision_service import parse_llm_suggestions

    try:
        parse_llm_suggestions("抱歉我不能输出 JSON", SEGS)
    except ValueError as e:
        assert "JSON" in str(e)
    else:
        raise AssertionError("应抛 ValueError")


def test_parse_tolerates_surrounding_text():
    from slirn_home.revision_service import parse_llm_suggestions

    raw = '分析结果如下：\n[{"i": 1, "category": "keep", "note": "x"}]\n以上。'
    out = parse_llm_suggestions(raw, SEGS)
    assert out[0]["category"] == "keep"


# ---------- 决策合并 ----------

def test_merge_decisions_apply_and_skip():
    from slirn_home.revision_service import merge_decisions

    rev = {"entries": [
        {"i": 1, "decision": "pending", "user_note": ""},
        {"i": 2, "decision": "pending", "user_note": ""},
        {"i": 3, "decision": "pending", "user_note": ""},
    ]}
    rev2, applied = merge_decisions(rev, [
        {"i": 1, "decision": "accept", "user_note": "同意删语气词"},
        {"i": 2, "decision": "fix", "user_note": "已核对更正文本"},
        {"i": 3, "decision": "wrong", "user_note": "非法类别跳过"},
        {"i": 9, "decision": "keep", "user_note": "未知段跳过"},
        {"decision": "keep"},  # 缺 i 跳过
    ])
    assert applied == 2
    assert rev2["entries"][0]["decision"] == "accept"
    assert rev2["entries"][0]["user_note"] == "同意删语气词"
    assert rev2["entries"][1]["decision"] == "fix", "手动改判内容更正（REQ-20260916-006）"
    assert rev2["entries"][1]["user_note"] == "已核对更正文本"
    assert rev2["entries"][2]["decision"] == "pending"


def test_all_decided():
    from slirn_home.revision_service import all_decided

    assert not all_decided({"entries": []})
    assert not all_decided({"entries": [{"decision": "pending"}, {"decision": "keep"}]})
    assert all_decided({"entries": [{"decision": "accept"}, {"decision": "delete"}]})


# ---------- 后台 job（mock LLM）----------

ENTRY = {
    "id": "qwen-max", "provider": "阿里云百炼",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key_env": "DASHSCOPE_API_KEY",
}


def test_start_job_writes_revision(tmp_path: Path, monkeypatch):
    from slirn_home import revision_service

    outputs = tmp_path / "outputs"
    captured: dict = {}

    def _fake_llm(system, user, entry=None, retries=2):
        captured["system"] = system
        return json.dumps([
            {"i": 1, "category": "delete", "note": "语气词"},
            {"i": 2, "category": "keep", "note": "保留"},
            {"i": 3, "category": "split", "keep_text": "我们开始吧", "note": "去重复"},
        ])

    monkeypatch.setattr(revision_service, "_call_llm", _fake_llm)
    assert revision_service.start_job(
        "t1", SEGS, "任务A", ["热词"], outputs, entry=ENTRY, rigor="medium",
    )
    # 等 job 结束
    import time
    for _ in range(100):
        j = revision_service.job_status("t1")
        if j and j["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["state"] == "done", j
    # 级别注入系统提示词（REQ-20260916-003）
    assert "中（意思正确即可）" in captured["system"], captured["system"][:120]
    rev = revision_service.load_revision(outputs)
    assert rev is not None and rev["segments_count"] == 3
    assert rev["model"] == "qwen-max" and rev["provider"] == "阿里云百炼"  # meta 留痕（REQ-20260915-008）
    assert rev["rigor"] == "medium"  # 级别留痕（REQ-20260916-003）
    entries = rev["entries"]
    # 每条齐备：建议 + 说明 + 用户决策字段（AC-2）
    for e, seg in zip(entries, SEGS):
        assert e["text"] == seg["text"] and e["category"] and e["note"]
        assert e["decision"] == "pending"
    # split 行分析时自动预填建议文本到「切分修剪后内容」（REQ-20260916-010）；其余类别留空
    assert entries[0]["user_note"] == "" and entries[1]["user_note"] == ""
    assert entries[2]["user_note"] == "我们开始吧"
    assert entries[2]["keep_text"] == "我们开始吧"


def test_start_job_llm_error(tmp_path: Path, monkeypatch):
    """LLM 失败（如未配置 Key）→ job error 态，不落盘。"""
    import time

    from slirn_home import revision_service

    def _boom(*a, **k):
        raise RuntimeError("未配置环境变量 DEEPSEEK_API_KEY（DeepSeek 的 API Key）")

    outputs = tmp_path / "outputs"
    monkeypatch.setattr(revision_service, "_call_llm", _boom)
    assert revision_service.start_job("t2", SEGS, "任务B", [], outputs)
    for _ in range(100):
        j = revision_service.job_status("t2")
        if j and j["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["state"] == "error" and "DEEPSEEK_API_KEY" in j["error"], j
    assert not (outputs / "revision.json").exists()


# ---------- 严谨性级别（REQ-20260916-003）----------

def test_build_system_prompt_rigor_levels():
    """三档级别各自注入对应的严格度指令；公共输出格式不变；未知级别回退高。"""
    from slirn_home.revision_service import RIGOR_LEVELS, build_system_prompt

    assert list(RIGOR_LEVELS) == ["high", "medium", "low"]
    hi = build_system_prompt("high")
    assert "高（严格打磨）" in hi and "成品口播稿" in hi
    assert "语气词（嗯、啊、呃、那个、就是说、然后等）" in hi, "高档保持原有最严判定标准"
    mid = build_system_prompt("medium")
    assert "中（意思正确即可）" in mid and "小语气词" in mid and "明显口误" in mid
    lo = build_system_prompt("low")
    assert "低（只去严重问题）" in lo and "最大限度保留原文" in lo
    assert "过多重复" in lo and "意思混乱" in lo, "低档只放过严重问题（用户原话）"
    for p in (hi, mid, lo):  # 输出格式与全段覆盖要求三档一致
        assert '"category": "keep|delete|split|fix|review"' in p
        assert "必须覆盖输入的每一个 i" in p
        # 内容更正（REQ-20260916-006）：错字别字与级别无关，任何档都必须判
        assert '"fix"' in p and "同音字误识别" in p and "对照说明" in p
    assert build_system_prompt("bogus") == hi  # 防御：未知回退高


def test_start_job_rejects_unknown_rigor(tmp_path: Path):
    """非法级别直接拒绝（不发 LLM 请求）。"""
    from slirn_home import revision_service

    try:
        revision_service.start_job("t9", SEGS, "任务E", [], tmp_path / "o", rigor="bogus")
    except ValueError as e:
        assert "严谨性" in str(e), e
    else:
        raise AssertionError("应抛 ValueError")
    assert revision_service.job_status("t9") is None, "不应创建 job"


# ---------- 自定义严谨性（REQ-20260916-007）----------

def test_resolve_system_prompt_custom():
    """resolve：custom 原样使用（空白回退底稿）；三档按级别组装；未知拒绝。
    底稿 = 高档完整提示词（含 fix 更正与 JSON 输出要求 — 用户在完整底稿上修改）。"""
    from slirn_home import revision_service as rs

    base = rs.default_custom_prompt()
    assert base == rs.build_system_prompt("high"), "底稿取高档（判定标准最完整）"
    assert '"fix"' in base and "输出要求" in base and "必须覆盖输入的每一个 i" in base
    my_prompt = "我的自定义修订标准：专业术语保留英文原文。\n（输出要求照旧）"
    assert rs.resolve_system_prompt("custom", my_prompt) == my_prompt
    assert rs.resolve_system_prompt("custom", "   ") == base, "空白回退默认底稿"
    assert rs.resolve_system_prompt("custom", None) == base
    assert rs.resolve_system_prompt("medium") == rs.build_system_prompt("medium")
    try:
        rs.resolve_system_prompt("bogus")
    except ValueError as e:
        assert "严谨性" in str(e), e
    else:
        raise AssertionError("应抛 ValueError")


def test_start_job_custom_rigor(tmp_path: Path, monkeypatch):
    """custom 档：提示词原样注入 LLM；meta 留痕 rigor=custom + 实际使用全文。"""
    import time

    from slirn_home import revision_service

    outputs = tmp_path / "outputs"
    captured: dict = {}

    def _fake_llm(system, user, entry=None, retries=2):
        captured["system"] = system
        return json.dumps([
            {"i": 1, "category": "keep", "note": "ok"},
            {"i": 2, "category": "keep", "note": "ok"},
            {"i": 3, "category": "keep", "note": "ok"},
        ])

    monkeypatch.setattr(revision_service, "_call_llm", _fake_llm)
    my_prompt = "自定义标准：保留所有语气词，仅修正错字。"
    assert revision_service.start_job(
        "tc", SEGS, "任务自定义", [], outputs, entry=ENTRY,
        rigor="custom", custom_prompt=my_prompt,
    )
    for _ in range(100):
        j = revision_service.job_status("tc")
        if j and j["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["state"] == "done", j
    assert captured["system"] == my_prompt, "用户提示词原样注入（不拼接级别模板）"
    rev = revision_service.load_revision(outputs)
    assert rev["rigor"] == "custom"
    assert rev["custom_prompt"] == my_prompt, "实际使用提示词全文留痕"

    # 空白提示词 → 回退默认底稿（meta 留痕的也是底稿）
    captured.clear()
    assert revision_service.start_job(
        "tc2", SEGS, "任务自定义2", [], outputs / "b", entry=ENTRY,
        rigor="custom", custom_prompt="   ",
    )
    for _ in range(100):
        j2 = revision_service.job_status("tc2")
        if j2 and j2["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j2["state"] == "done", j2
    assert captured["system"] == revision_service.default_custom_prompt()
    rev2 = revision_service.load_revision(outputs / "b")
    assert rev2["custom_prompt"] == revision_service.default_custom_prompt()
    # 三档级别 meta 不写 custom_prompt（None，保持向后兼容的键位）


# ---------- _chat_completion 响应硬校验（REQ-20260915-007 / 008 OpenAI 兼容层）----------

def _fake_resp(status_code=200, error=None, request_id=None,
               content=None, finish_reason="stop"):
    """构造 OpenAI 兼容响应替身（业务失败时非 200 + error 对象，不抛异常）。"""
    import json as _json
    import types

    body: dict = {}
    if error is not None:
        body["error"] = error
    if content is not None:
        body["choices"] = [{"finish_reason": finish_reason,
                            "message": {"role": "assistant", "content": content}}]
    text = _json.dumps(body)
    return types.SimpleNamespace(
        status_code=status_code, text=text,
        headers={"x-request-id": request_id} if request_id else {},
        json=lambda: _json.loads(text),
    )


def test_call_llm_business_error_readable(monkeypatch):
    """欠费（400/Arrearage）→ 可读错误（厂商/模型/中文原因/request_id），而非 NoneType 下标错。"""
    from slirn_home import revision_service

    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setattr(revision_service.time, "sleep", lambda s: None)
    monkeypatch.setattr("httpx.post", lambda *a, **k: _fake_resp(
        status_code=400,
        error={"code": "Arrearage", "message": "Access denied, please top up."},
        request_id="rid-007",
    ))
    try:
        revision_service._call_llm("sys", "user", entry=ENTRY)
    except RuntimeError as e:
        msg = str(e)
        assert "Arrearage" in msg and "欠费" in msg and "request_id=rid-007" in msg, msg
        assert "[qwen-max]" in msg and "阿里云百炼" in msg, "错误应带模型名与厂商（REQ-20260915-008）"
    else:
        raise AssertionError("应抛 RuntimeError")


def test_chat_completion_missing_env_readable(monkeypatch):
    """entry 指定的 Key 环境变量未设置 → 可读错误（含环境变量名与厂商）。"""
    from slirn_home import revision_service

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    entry = dict(ENTRY, id="deepseek-chat", provider="DeepSeek", api_key_env="DEEPSEEK_API_KEY")
    try:
        revision_service._chat_completion(entry, [])
    except RuntimeError as e:
        assert "未配置环境变量 DEEPSEEK_API_KEY" in str(e) and "DeepSeek" in str(e), e
    else:
        raise AssertionError("应抛 RuntimeError")


def test_call_llm_retries_throttle_then_ok(monkeypatch):
    """限流（429/Throttling）→ 退避重试，第 3 次成功。"""
    from slirn_home import revision_service

    calls = []

    def _fake(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            return _fake_resp(status_code=429,
                              error={"code": "Throttling.RequestsThrottled",
                                     "message": "Requests rate limit exceeded"})
        return _fake_resp(content='[{"i":1,"category":"keep","note":"ok"}]')

    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setattr(revision_service.time, "sleep", lambda s: None)
    monkeypatch.setattr("httpx.post", _fake)
    out = revision_service._call_llm("sys", "user", entry=ENTRY)
    assert "keep" in out and len(calls) == 3


def test_call_llm_truncated_raises_valueerror_no_retry(monkeypatch):
    """输出截断（finish_reason=length）→ ValueError 且不重试（同批重试无意义）。"""
    from slirn_home import revision_service

    calls = []

    def _fake(*a, **k):
        calls.append(1)
        return _fake_resp(content='[{"i":1,"categ', finish_reason="length")

    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setattr("httpx.post", _fake)
    try:
        revision_service._call_llm("sys", "user", entry=ENTRY)
    except ValueError as e:
        assert "截断" in str(e), e
    else:
        raise AssertionError("应抛 ValueError")
    assert len(calls) == 1


# ---------- Anthropic 协议（REQ-20260916-001）----------

ANTHROPIC_ENTRY = {
    "id": "claude-sonnet-4-5", "provider": "Anthropic",
    "base_url": "https://api.anthropic.com",
    "api_key_env": "ANTHROPIC_API_KEY", "protocol": "anthropic",
}


def _fake_anthropic_resp(status_code=200, error=None, request_id=None,
                         contents=None, stop_reason="end_turn"):
    """构造 Anthropic 协议响应替身（content 分块 / stop_reason / error 对象）。"""
    import json as _json
    import types

    body: dict = {}
    if error is not None:
        body = {"type": "error", "error": error}
    elif contents is not None:
        body = {"content": contents, "stop_reason": stop_reason}
    text = _json.dumps(body)
    return types.SimpleNamespace(
        status_code=status_code, text=text,
        headers={"request-id": request_id} if request_id else {},
        json=lambda: _json.loads(text),
    )


def test_anthropic_request_build(monkeypatch):
    """anthropic 协议 → POST {base}/v1/messages + x-api-key 头 + system 顶层 + max_tokens 必填。"""
    from slirn_home import revision_service

    captured: dict = {}

    def _fake(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return _fake_anthropic_resp(contents=[{"type": "text", "text": "ok"}])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr("httpx.post", _fake)
    revision_service._call_llm("你是助手", "你好", entry=ANTHROPIC_ENTRY)
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["headers"]["anthropic-version"]
    assert "Authorization" not in captured["headers"], "anthropic 不用 Bearer 头"
    body = captured["json"]
    assert body["system"] == "你是助手" and body["messages"] == [{"role": "user", "content": "你好"}]
    assert body["model"] == "claude-sonnet-4-5" and body["max_tokens"] > 0


def test_anthropic_base_url_with_v1_not_duplicated(monkeypatch):
    """base 已带 /v1 → 拼 /messages 而非 /v1/v1/messages（兼容网关前缀写法）。"""
    from slirn_home import revision_service

    captured: dict = {}

    def _fake(url, **k):
        captured["url"] = url
        return _fake_anthropic_resp(contents=[{"type": "text", "text": "ok"}])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr("httpx.post", _fake)
    entry = dict(ANTHROPIC_ENTRY, base_url="https://gw.example.com/anthropic/v1")
    revision_service._call_llm("s", "u", entry=entry)
    assert captured["url"] == "https://gw.example.com/anthropic/v1/messages"


def test_anthropic_error_readable(monkeypatch):
    """401/authentication_error → 可读错误（厂商/模型/中文原因），Key 无效提示检查环境变量。"""
    from slirn_home import revision_service

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-bad")
    monkeypatch.setattr(revision_service.time, "sleep", lambda s: None)
    monkeypatch.setattr("httpx.post", lambda *a, **k: _fake_anthropic_resp(
        status_code=401,
        error={"type": "authentication_error", "message": "invalid x-api-key"},
        request_id="req-ant-1",
    ))
    try:
        revision_service._call_llm("s", "u", entry=ANTHROPIC_ENTRY)
    except RuntimeError as e:
        msg = str(e)
        assert "[claude-sonnet-4-5]" in msg and "Anthropic" in msg, msg
        assert "authentication_error" in msg and "Key 无效" in msg, msg
        assert "request_id=req-ant-1" in msg, msg
    else:
        raise AssertionError("应抛 RuntimeError")


def test_anthropic_truncated_no_retry(monkeypatch):
    """stop_reason=max_tokens 截断 → ValueError 且不重试（上层减半）。"""
    from slirn_home import revision_service

    calls = []

    def _fake(*a, **k):
        calls.append(1)
        return _fake_anthropic_resp(contents=[{"type": "text", "text": "[{"}], stop_reason="max_tokens")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr("httpx.post", _fake)
    try:
        revision_service._call_llm("s", "u", entry=ANTHROPIC_ENTRY)
    except ValueError as e:
        assert "截断" in str(e), e
    else:
        raise AssertionError("应抛 ValueError")
    assert len(calls) == 1


def test_anthropic_content_parts_joined(monkeypatch):
    """content 多分块（含非 text）→ 只拼 text 分块。"""
    from slirn_home import revision_service

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr("httpx.post", lambda *a, **k: _fake_anthropic_resp(contents=[
        {"type": "text", "text": "前半"},
        {"type": "tool_use", "id": "x", "name": "t"},
        {"type": "text", "text": "后半"},
    ]))
    out = revision_service._call_llm("s", "u", entry=ANTHROPIC_ENTRY)
    assert out == "前半后半"


# ---------- 批处理减半 / 回填（REQ-20260915-007）----------

def test_start_job_halves_on_parse_failure(tmp_path: Path, monkeypatch):
    """批解析失败 → 减半递归重试，最终全部段拿到建议，job done。"""
    import re
    import time

    from slirn_home import revision_service

    batch_sizes = []

    def _fake_llm(system, user, entry=None, retries=2):
        n = user.count('"i":')
        batch_sizes.append(n)
        if n > 1:  # 多段批 → 返回非法 JSON
            return "抱歉，我无法输出 JSON"
        m = re.search(r'"i": (\d+)', user)
        return json.dumps([{"i": int(m.group(1)), "category": "keep", "note": "ok"}])

    monkeypatch.setattr(revision_service, "_call_llm", _fake_llm)
    outputs = tmp_path / "outputs"
    assert revision_service.start_job("t3", SEGS, "任务C", [], outputs)
    for _ in range(100):
        j = revision_service.job_status("t3")
        if j and j["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["state"] == "done", j
    # 3 段 → 失败 → 减半 [1] + [2,3] → [2,3] 再减半 [2] + [3]
    assert batch_sizes[0] == 3, batch_sizes
    rev = revision_service.load_revision(outputs)
    assert rev["segments_count"] == 3
    assert all(e["category"] == "keep" for e in rev["entries"])


def test_start_job_backfills_on_total_parse_failure(tmp_path: Path, monkeypatch):
    """所有批都解析失败 → 单段回填 review（note 含「请人工复核」），job done 不崩。"""
    import time

    from slirn_home import revision_service

    def _always_bad(system, user, entry=None, retries=2):
        raise ValueError("模型输出不是合法 JSON: bad")

    monkeypatch.setattr(revision_service, "_call_llm", _always_bad)
    outputs = tmp_path / "outputs"
    assert revision_service.start_job("t4", SEGS, "任务D", [], outputs)
    for _ in range(100):
        j = revision_service.job_status("t4")
        if j and j["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["state"] == "done", j
    rev = revision_service.load_revision(outputs)
    assert rev["segments_count"] == 3
    for e in rev["entries"]:
        assert e["category"] == "review" and "请人工复核" in e["note"], e


# ---------- 渲染三态 ----------

def test_render_revision_zone_states(tmp_path: Path, monkeypatch):
    from slirn_home import llm_config
    from slirn_home.app import _render_revision_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="修订任务", original_video=video)
    tid = t.task_id

    # 状态 1：无字幕 → 引导
    h1 = _render_revision_zone(tid, m.get(tid), m)
    assert "请先完成上一阶段「字幕生成」" in h1
    assert 'data-action="wb-stage" data-pane="subtitle"' in h1

    # 状态 2：有字幕无建议 → 分析按钮 + 类别说明 + 当前生效模型（REQ-20260915-008）
    monkeypatch.delenv("SLIRN_LLM_MODEL", raising=False)
    llm_config.add_model(  # repo_root = mgr.tasks_dir.parent = tmp_path
        tmp_path, "qwen-max", "阿里云百炼",
        "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY",
    )
    llm_config.use_model(tmp_path, "qwen-max")
    _write_subtitle(m, tid, SEGS)
    h2 = _render_revision_zone(tid, m.get(tid), m)
    assert "🤖 大模型分析字幕" in h2 and 'data-action="revise-subtitle"' in h2
    assert "整行删除" in h2 and "完整保留" in h2 and "切分修剪" in h2 and "人工复核" in h2
    assert "内容更正" in h2 and "原文对照" in h2, "状态2介绍新增内容更正类别（REQ-20260916-006）"
    assert "qwen-max" in h2, "状态2提示应显示当前生效模型"
    # 严谨性级别单选卡（REQ-20260916-003 / 007）：必选、四档（高/中/低/自定义）、不预选
    assert "分析严谨性级别" in h2 and "必选" in h2
    assert h2.count('name="slirn-rev-rigor"') == 4
    assert 'value="high"' in h2 and 'value="medium"' in h2 and 'value="low"' in h2
    assert 'value="custom"' in h2 and "自定义严谨性" in h2, "第四档自定义（REQ-20260916-007）"
    assert "可在高/中/低任一底稿" in h2, "自定义档说明可在三档底稿上改（REQ-20260916-009）"
    assert "严格打磨" in h2 and "意思正确即可" in h2 and "只去严重问题" in h2
    assert h2.count("例：") == 4, "四档各带一个例子给操作者体感"
    assert "checked" not in h2, "服务端不预选 — 用户必须主动选择"
    # 自定义提示词编辑区：服务端隐藏（选中自定义由 JS 展开）、textarea 置空（JS 预填草稿/高档底稿）
    assert 'id="slirn-rigor-custom" style="display:none;"' in h2
    assert h2.count('class="slirn-textarea slirn-rigor-custom-text"') == 1
    assert 'placeholder="点右侧底稿载入参考后直接修改…"' in h2
    # 高/中/低三个底稿按钮 + 三档完整提示词随属性下发（REQ-20260916-009），
    # 默认底稿 = 高档（与服务端 default_custom_prompt 回退同源）
    assert h2.count('data-action="rigor-prompt-preset" data-preset="') == 3
    assert 'data-preset="high"' in h2 and 'data-preset="medium"' in h2 and 'data-preset="low"' in h2
    assert 'data-prompt-high="' in h2 and 'data-prompt-medium="' in h2 and 'data-prompt-low="' in h2
    assert "data-default-prompt" not in h2, "旧单底稿属性退役（统一 data-prompt-*）"

    # 状态 3：有建议 → 行式列表（与字幕生成列表同列布局 — REQ-20260916-002）
    outputs = m.tasks_dir / tid / "outputs"
    (outputs / "revision.json").write_text(json.dumps({
        "version": 1, "model": "qwen-plus", "created_at": "2026-09-15T10:00:00",
        "saved_at": None, "segments_count": 3,
        "entries": [
            {"i": 1, "start_ms": 0, "end_ms": 1500, "start": "00:00:00.000", "end": "00:00:01.500",
             "text": "嗯嗯大家好", "category": "split", "keep_text": "大家好",
             "note": "「嗯」为语气词", "decision": "pending", "user_note": ""},
            {"i": 2, "start_ms": 1500, "end_ms": 4000, "start": "00:00:01.500", "end": "00:00:04.000",
             "text": "今天讲第一课", "category": "keep", "keep_text": None,
             "note": "正常内容", "decision": "accept", "user_note": "同意"},
            {"i": 3, "start_ms": 4000, "end_ms": 7000, "start": "00:00:04.000", "end": "00:00:07.000",
             "text": "那个那个就是说我们开始吧", "category": "delete", "keep_text": None,
             "note": "口头禅", "decision": "pending", "user_note": ""},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    h3 = _render_revision_zone(tid, m.get(tid), m)
    assert 'id="slirn-rev-list"' in h3
    # 主行 ×3：序号|时间|文本+徽章|决策下拉|展开钮 同一行
    assert h3.count("slirn-rev-line") == 3
    assert h3.count("slirn-rev-row") == 3
    # 过滤 data 属性（REQ-20260916-010）：建议 / 决策 / 实质类别（accept=采纳建议类别）
    assert 'data-sugg="split"' in h3 and 'data-sugg="keep"' in h3 and 'data-sugg="delete"' in h3
    assert h3.count('data-decision="pending"') == 2 and 'data-decision="accept"' in h3
    assert 'data-final="keep"' in h3, "accept + 建议 keep → 实质=keep（与 _final_kind 同源）"
    assert h3.count('data-final=""') == 2, "pending ×2 → 实质未定（空）"
    # 状态过滤条（REQ-20260916-010）：建议/决策两维 + chips 容器（计数由 JS 按行 data 属性现算）
    assert 'id="slirn-rev-filter"' in h3 and h3.count("data-rev-filter-dim=") == 2
    assert 'data-rev-filter-dim="sugg"' in h3 and 'data-rev-filter-dim="dec"' in h3
    assert 'id="slirn-rev-filter-chips"' in h3 and 'id="slirn-rev-filter-count"' in h3
    # 有手动说明的行默认展开，其余收起（保持列表紧凑）
    assert h3.count('class="slirn-rev-row open"') == 1
    assert h3.count('class="slirn-rev-row"') == 2
    assert h3.count(">▴</button>") == 1 and h3.count(">▾</button>") == 2
    # 徽章内联在文本前，与字幕列表列对齐
    assert 'slirn-rev-badge split">切分修剪</span>嗯嗯大家好' in h3
    # 详情块：模型分析 + 建议保留 + 手动说明输入（点 ▾ 展开）
    assert '<div class="slirn-rev-note">🤖 「嗯」为语气词</div>' in h3
    assert "建议保留：「大家好」" in h3
    assert h3.count('data-action="rev-detail"') == 3
    # 手动决策下拉 ×3 + 手动说明输入 ×3（预填已保存的决策）
    assert h3.count('class="slirn-rev-select" data-i=') == 3
    assert h3.count('class="slirn-rev-note-input" data-i=') == 3
    # 未决策行默认选「采纳建议」（REQ-20260916-003）：pending×2 默认 accept + 已存 accept×1
    assert h3.count('<option value="accept" selected>采纳建议</option>') == 3
    assert 'value="pending" selected' not in h3, "不再有默认「未决策」（选项保留可手动改回）"
    assert '<option value="pending">未决策</option>' in h3
    assert '<option value="fix">内容更正</option>' in h3, "决策下拉可手动改判内容更正（REQ-20260916-006）"
    assert 'value="同意"' in h3
    # split 行「切分修剪后内容」自动预填建议文本（REQ-20260916-010）：
    # 存量任务 user_note 空 → 渲染时预填 keep_text；已手填（keep 行「同意」）不覆盖
    assert 'value="大家好"' in h3, "split 预填建议的修剪后文本"
    assert "已决策 <b>1/3</b>" in h3, "统计口径仍是已保存决策（保存后生效）"
    assert "决策列默认「采纳建议」" in h3
    assert "点击行定位播放" in h3 and "展开模型分析与切分修剪后内容" in h3
    # 快捷键提示条（REQ-20260916-004）：键帽芯片 + 动作说明；选中态由 JS 挂 kbsel，不预渲染
    assert 'class="slirn-rev-kbhint"' in h3 and h3.count("<kbd>") == 8
    for kw in ("上一条 / 下一条", "播放 / 暂停", "重播本行", "<kbd>K</kbd> 保留",
               "<kbd>D</kbd> 删除", "<kbd>S</kbd> 切分", "<kbd>Esc</kbd> 退出输入框"):
        assert kw in h3, f"提示条缺少：{kw}"
    assert "kbsel" not in h3, "选中行高亮由 JS 动态添加，服务端不预渲染"
    # 快捷键自定义入口（REQ-20260916-005）：服务端渲染默认键位 + 入口；
    # 自定义弹窗/键位图由 JS 端 localStorage 驱动，不预渲染
    assert 'data-action="revkeys-open"' in h3 and "⚙ 自定义" in h3
    assert "slirn-revkeys-modal" not in h3, "换绑弹窗由 JS 按需创建"
    assert 'data-action="save-revision"' in h3
    assert 'data-action="revise-subtitle"' in h3 and 'data-has-revision="1"' in h3
    # 重新分析的等级选择收进 <details>；旧数据无 rigor → 统计行不显示严谨性
    assert 'class="slirn-rigor-box"' in h3 and h3.count('name="slirn-rev-rigor"') == 4
    assert " · 严谨性 " not in h3

    # meta 带 rigor → 统计行显示级别（正向用例）
    rev_data = json.loads((outputs / "revision.json").read_text(encoding="utf-8"))
    rev_data["rigor"] = "medium"
    (outputs / "revision.json").write_text(json.dumps(rev_data, ensure_ascii=False), encoding="utf-8")
    h3b = _render_revision_zone(tid, m.get(tid), m)
    assert " · 严谨性 中（意思正确即可）" in h3b

    # meta rigor=custom → 统计行显示「自定义」（REQ-20260916-007）
    rev_data["rigor"] = "custom"
    rev_data["custom_prompt"] = "我的自定义标准"
    (outputs / "revision.json").write_text(json.dumps(rev_data, ensure_ascii=False), encoding="utf-8")
    h3c = _render_revision_zone(tid, m.get(tid), m)
    assert " · 严谨性 自定义" in h3c


def test_render_fix_row_and_input_rename(tmp_path: Path):
    """内容更正行渲染 + 输入区改名（REQ-20260916-006）。

    fix 行：蓝色徽章、note 即原文对照说明、「✏️ 更正后」块收进详情；
    与 split 一致不默认展开。输入区 placeholder 由「手动处理说明」改为
    「切分修剪后内容」（用户原话）。
    """
    from slirn_home.app import _render_revision_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="更正任务", original_video=video)
    tid = t.task_id
    _write_subtitle(m, tid, SEGS)
    outputs = m.tasks_dir / tid / "outputs"
    (outputs / "revision.json").write_text(json.dumps({
        "version": 1, "model": "qwen-plus", "created_at": "2026-09-16T10:00:00",
        "saved_at": None, "segments_count": 2,
        "entries": [
            {"i": 1, "start_ms": 0, "end_ms": 1500, "start": "00:00:00.000", "end": "00:00:01.500",
             "text": "神精网络入门", "category": "fix", "keep_text": "神经网络入门",
             "note": "「神精」应为「神经」（同音误识别）", "decision": "pending", "user_note": ""},
            {"i": 2, "start_ms": 1500, "end_ms": 4000, "start": "00:00:01.500", "end": "00:00:04.000",
             "text": "今天讲第一课", "category": "keep", "keep_text": None,
             "note": "正常内容", "decision": "pending", "user_note": ""},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    h = _render_revision_zone(tid, m.get(tid), m)
    # 徽章内联文本前 + note 对照说明 + 更正后文本（详情块内，独立于切分的建议保留块）
    assert 'slirn-rev-badge fix">内容更正</span>神精网络入门' in h
    assert "🤖 「神精」应为「神经」（同音误识别）" in h
    assert '<div class="slirn-rev-keeptext fix">✏️ 更正后：「神经网络入门」</div>' in h
    assert "✂️ 建议保留" not in h
    # 统计行含更正计数；fix 行与 split 一致不默认展开（保持列表紧凑）
    assert "更正 1 / 复核 0" in h
    assert h.count('class="slirn-rev-row open"') == 0 and h.count(">▾</button>") == 2
    # 未决策默认「采纳建议」（fix 行同样，采纳即采用更正文本）
    assert h.count('<option value="accept" selected>采纳建议</option>') == 2
    # 输入区改名（用户原话）：手动处理说明 → 切分修剪后内容
    assert h.count('placeholder="切分修剪后内容（可空）"') == 2
    assert "手动处理说明" not in h


def test_render_split_autofill_and_final_kind(tmp_path: Path):
    """split 自动预填优先级 + 决策实质类别（REQ-20260916-010）。

    - 手动填写过「切分修剪后内容」→ 不被建议文本覆盖；fix 行不预填（需求只提切分）
    - 实质类别 = 手动改判优先；accept = 模型建议；pending = 空（与 _final_kind 同源）
    """
    from slirn_home.app import _render_revision_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="预填任务", original_video=video)
    tid = t.task_id
    _write_subtitle(m, tid, SEGS)
    outputs = m.tasks_dir / tid / "outputs"
    (outputs / "revision.json").write_text(json.dumps({
        "version": 1, "model": "qwen-plus", "created_at": "2026-09-16T11:00:00",
        "saved_at": None, "segments_count": 3,
        "entries": [
            {"i": 1, "start_ms": 0, "end_ms": 1500, "start": "00:00:00.000", "end": "00:00:01.500",
             "text": "嗯嗯大家好", "category": "split", "keep_text": "大家好",
             "note": "「嗯」为语气词", "decision": "split", "user_note": "我的版本"},
            {"i": 2, "start_ms": 1500, "end_ms": 4000, "start": "00:00:01.500", "end": "00:00:04.000",
             "text": "神精网络入门", "category": "fix", "keep_text": "神经网络入门",
             "note": "同音误识别", "decision": "accept", "user_note": ""},
            {"i": 3, "start_ms": 4000, "end_ms": 7000, "start": "00:00:04.000", "end": "00:00:07.000",
             "text": "那个就是说我们开始吧", "category": "review", "keep_text": None,
             "note": "拿不准", "decision": "pending", "user_note": ""},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    h = _render_revision_zone(tid, m.get(tid), m)
    # split：手填优先（值=「我的版本」而非建议「大家好」）
    assert 'value="我的版本"' in h and 'value="大家好"' not in h
    # fix：不预填（需求只提切分；更正文本已收进详情块「✏️ 更正后」）
    assert 'value="神经网络入门"' not in h and "✏️ 更正后：「神经网络入门」" in h
    # 实质类别：手动 split → split；accept+建议 fix → fix；pending+复核 → 空
    assert 'data-final="split"' in h and 'data-final="fix"' in h
    assert h.count('data-final=""') == 1
    assert 'data-sugg="review"' in h and 'data-decision="pending"' in h


def test_wb_stage_states_with_revision(tmp_path: Path):
    """revision.json 落盘 → 前三阶段 done，切分修剪 current。"""
    from slirn_home.app import _wb_stage_states

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t", original_video=video)
    tid = t.task_id
    outputs = m.tasks_dir / tid / "outputs"  # create 时已建
    (outputs / "subtitle.json").write_text(json.dumps({"version": 1, "segments": SEGS}), encoding="utf-8")
    (outputs / "revision.json").write_text(
        json.dumps({"version": 1, "entries": [{"i": 1, "decision": "accept"}]}), encoding="utf-8"
    )
    assert _wb_stage_states(m.get(tid)) == ["done", "done", "done", "current"] + ["pending"] * 4
