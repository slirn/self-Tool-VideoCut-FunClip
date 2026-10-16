"""REQ-20261016-134 followup — Stage 6 按钮端点回归保护。

用户原话：「关于字幕优化阶段的功能，你自己先点一下试试，看看页面上的按钮能不能使用」。

实际 E2E 验证发现 3 个 REQ-134 之前就存在但从未被触发过的 bug
（用户点按钮才暴露）：

1. ``app.py`` 的 ``short_video_stage5_run`` 被定义两次；
   FastAPI 命中第一个，调用 ``_sv_coarse.run_stage4``（未导入 → NameError）
   → 重新 ASR 按钮永远 500。
2. ``short_video_extract.py`` 调 ``svc.stage6_refined_srt_path``，
   但 service 里只有 ``stage5_refined_srt_path``（命名残留）→ AttributeError。
3. ``short_video_opt_review.py`` 调 ``_call_llm(..., timeout=...)``，
   但 ``_call_llm`` 不接受 timeout → TypeError。

修复均为收口到正确名称；本测试只验「代码里不再出现错误符号」，
避免后续改动再次引入同样问题。
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
APP_PY = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
EXTRACT_PY = (FUNCLIP_ROOT / "slirn_home" / "short_video_extract.py").read_text(
    encoding="utf-8"
)
OPT_REVIEW_PY = (FUNCLIP_ROOT / "slirn_home" / "short_video_opt_review.py").read_text(
    encoding="utf-8"
)


def test_stage5_run_endpoint_defined_only_once():
    """短/长工作台 /short_video_stage5_run 必须只定义一次（FastAPI 取首个）。"""
    # 两次会触发 FastAPI 路由竞争 → 调错函数（REQ-103 提交时漏删旧实现）
    n = APP_PY.count('@app.app.post("/slirn/api/short_video_stage5_run")')
    assert n == 1, f"short_video_stage5_run 被定义了 {n} 次（应为 1），存在路由竞争"


def test_stage5_run_does_not_call_undefined_sv_coarse():
    """stage5_run 端点内部不能再调 _sv_coarse.run_stage4（_sv_coarse 未导入）。"""
    # 取第一个 stage5_run 函数体，检查不含 _sv_coarse 引用
    start = APP_PY.find('@app.app.post("/slirn/api/short_video_stage5_run")')
    # 函数体到下一个 "@app.app." 之前
    next_decor = APP_PY.find("@app.app.", start + 1)
    body = APP_PY[start:next_decor if next_decor > 0 else start + 3000]
    assert "_sv_coarse" not in body, (
        "stage5_run 端点不能再引用 _sv_coarse（_sv_coarse 未导入，"
        "REQ-103 提交时漏删旧实现）"
    )


def test_extract_uses_correct_refined_srt_path():
    """extract.py 调 svc.stage5_refined_srt_path（不是 stage6_）。"""
    # 不应再出现 stage6_refined_srt_path（命名残留）
    assert "stage6_refined_srt_path" not in EXTRACT_PY, (
        "short_video_extract.py 还在调 stage6_refined_srt_path（应为 stage5_）"
    )
    # 应有 stage5_refined_srt_path（其他文件如 fine.py 都在用这个）
    assert "stage5_refined_srt_path" in EXTRACT_PY, (
        "short_video_extract.py 应调 stage5_refined_srt_path"
    )


def test_opt_review_does_not_pass_timeout_to_call_llm():
    """opt_review.py 调 _call_llm 不再传 timeout（_call_llm 无此参数）。"""
    # 取 _call_llm 调用行
    line_idx = OPT_REVIEW_PY.find("_call_llm(")
    assert line_idx > 0, "opt_review.py 应有 _call_llm 调用"
    # 截到下一个 \n 之前
    nl = OPT_REVIEW_PY.find("\n", line_idx)
    call_line = OPT_REVIEW_PY[line_idx:nl]
    assert "timeout=" not in call_line, (
        f"_call_llm 调用不应传 timeout（_call_llm 不支持），当前：{call_line.strip()}"
    )
