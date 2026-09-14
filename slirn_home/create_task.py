"""新建任务 tab — REQ-C UI 实现。"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

import gradio as gr

from slirn_home.paths import ensure_tasklib_importable
from slirn_home.task_list import render_task_list

# 在 tasklib/video import 前确保 path 就位
_ensure = ensure_tasklib_importable

from tasklib import TaskManager, TimeSegment, VideoProcessingError  # noqa: E402
from tasklib.time_utils import segment_filename  # noqa: E402
from tasklib.video import get_video_duration  # noqa: E402

log = logging.getLogger(__name__)


def _get_temp_dir(repo_root: Path) -> Path:
    """slirn-standalone/.temp/ — 截取预览临时目录。"""
    d = repo_root / ".temp"
    d.mkdir(exist_ok=True)
    return d


def _to_repo_relative(p: Path, repo_root: Path) -> str:
    """Path → 相对仓库根的 POSIX 字符串。"""
    try:
        return str(p.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(p.resolve()).replace("\\", "/")


def on_upload_video(file_path: str | None, repo_root: Path) -> tuple[str, str]:
    """文件选择/拖拽后回调：显示文件名 + 时长。

    Returns: (filename_display, status_msg)
    """
    if not file_path:
        return "", "请先选择或拖拽视频文件"
    p = Path(file_path)
    if not p.exists():
        return "", f"文件不存在: {p}"

    duration = get_video_duration(p)
    size_mb = p.stat().st_size / 1024 / 1024

    if duration is None:
        info = f"📁 {p.name}（{size_mb:.1f} MB，时长未知）"
    else:
        mm, ss = divmod(int(duration), 60)
        hh, mm = divmod(mm, 60)
        info = f"📁 {p.name}（{size_mb:.1f} MB，{hh:02d}:{mm:02d}:{ss:02d}）"
    return info, "✅ 文件已加载"


def on_cut_preview(
    file_path: str | None,
    start_time: str,
    end_time: str,
    repo_root: Path,
) -> tuple[str, str | None, str, str]:
    """截取预览：ffmpeg 截取到临时目录，返回截取段路径给 Gradio Video 组件。

    Returns: (preview_path_or_None, status_msg, start_box_clear, end_box_clear)
    """
    if not file_path:
        return None, "⚠️ 请先选择视频文件", "", ""
    if not start_time.strip() or not end_time.strip():
        return None, "⚠️ 请填写开始和结束时间（或都留空表示不截取）", "", ""

    src = Path(file_path)
    if not src.exists():
        return None, f"❌ 原视频不存在: {src}", "", ""

    # 校验 start < end
    try:
        from tasklib.time_utils import validate_segment
        validate_segment(start_time.strip(), end_time.strip())
    except Exception as e:
        return None, f"❌ 时间校验失败: {e}", "", ""

    # 截取到临时目录
    temp_dir = _get_temp_dir(repo_root)
    temp_name = f"cut_{uuid.uuid4().hex[:8]}_{src.stem}{src.suffix}"
    dst = temp_dir / temp_name

    try:
        from tasklib.video import cut_video
        cut_video(src, dst, start_time.strip(), end_time.strip())
    except VideoProcessingError as e:
        return None, f"❌ 截取失败: {e}", "", ""

    cut_dur = get_video_duration(dst)
    if cut_dur is not None:
        mm, ss = divmod(int(cut_dur), 60)
        hh, mm = divmod(mm, 60)
        dur_str = f"{hh:02d}:{mm:02d}:{ss:02d}"
    else:
        dur_str = "未知"
    size_mb = dst.stat().st_size / 1024 / 1024

    msg = f"✅ 截取成功：{dst.name}（{size_mb:.1f} MB，{dur_str}）\n📁 临时路径：{dst}"
    return str(dst), msg, start_time, end_time


def on_create_task(
    file_path: str | None,
    task_name: str,
    start_time: str,
    end_time: str,
    preview_path: str | None,
    hotwords_text: str,
    repo_root: Path,
) -> tuple[list[list[str]], dict, str]:
    """创建任务：把截取段从 .temp/ 移到 task_dir，调 TaskManager.create()。

    Returns: (new_rows, new_meta, status_msg)
    """
    if not file_path:
        return [], {"当前任务数": 0}, "⚠️ 请先选择视频文件"

    src = Path(file_path)
    if not src.exists():
        return [], {"当前任务数": 0}, f"❌ 原视频不存在: {src}"

    # 默认任务名 = 文件名 stem
    final_name = task_name.strip() or src.stem

    # 解析热词（空格 / 换行分隔）
    hotwords = [
        w.strip() for w in hotwords_text.replace("\n", " ").split()
        if w.strip()
    ]

    # 处理截取段（如有）
    segment: TimeSegment | None = None
    segment_temp: Path | None = None
    if start_time.strip() and end_time.strip() and preview_path:
        # 截取段存在
        segment_temp = Path(preview_path)
        if not segment_temp.exists():
            return [], {"当前任务数": 0}, f"❌ 截取段文件丢失: {preview_path}"
        seg_filename = segment_filename(src.stem, start_time.strip(), end_time.strip(), src.suffix.lstrip(".") or "mp4")
        # 先创建任务拿到 task_id，再 move 文件
        segment = TimeSegment(
            start=start_time.strip(),
            end=end_time.strip(),
            path=Path("PLACEHOLDER"),  # 临时占位，create 后会被覆盖
        )

    mgr = TaskManager(repo_root)
    try:
        task = mgr.create(
            name=final_name,
            original_video=src,
            segment=segment,
            hotwords=hotwords,
        )
    except Exception as e:
        return [], {"当前任务数": 0}, f"❌ 创建失败: {e}"

    # 把截取段从 .temp/ 移到 task_dir/raw_input/
    if segment_temp and segment_temp.exists():
        final_segment_path = repo_root / "tasks" / task.task_id / "raw_input" / seg_filename
        try:
            shutil.move(str(segment_temp), str(final_segment_path))
            # 更新 metadata.json 中 segment.path
            import json

            from tasklib.schema import task_from_dict, task_to_dict
            meta_path = repo_root / "tasks" / task.task_id / "metadata.json"
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            # 重新构造 segment.path 为相对路径
            data["segment"]["path"] = _to_repo_relative(final_segment_path, repo_root)
            meta_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            log.warning("移动截取段失败: %s", e)

    rows, meta = render_task_list(mgr)
    return rows, meta, f"✅ 已创建任务 {task.task_id}: {final_name}"


def on_cancel_create(
    repo_root: Path,
) -> tuple[list[list[str]], dict, str, str, str, str, str, None]:
    """取消：清空所有输入 + 切回任务列表。"""
    mgr = TaskManager(repo_root)
    rows, meta = render_task_list(mgr)
    return rows, meta, "", "", "", "", "已取消", None


def build_create_task_components(mgr: TaskManager) -> dict:
    """构造新建任务 tab 的 Gradio 组件，返回 dict 供 app.py 引用。

    返回的 dict 含所有需要引用的组件 key。
    """
    repo_root = mgr.repo_root

    with gr.Column() as root_col:
        gr.Markdown("### ➕ 新建任务")

        # 文件上传
        gr.Markdown("**📁 视频文件**（点击选择或拖拽）")
        file_input = gr.File(
            label="选择视频",
            file_types=[".mp4", ".avi", ".mkv", ".mov", ".webm", ".ts", ".mpeg"],
            type="filepath",
        )
        file_info = gr.Markdown("")

        # 时间截取
        gr.Markdown("**⏱ 时间截取**（留空 = 不截取，使用完整视频）")
        with gr.Row():
            start_box = gr.Textbox(label="开始时间 (HH:MM:SS)", placeholder="00:00:00", value="")
            end_box = gr.Textbox(label="结束时间 (HH:MM:SS)", placeholder="00:00:00", value="")
        with gr.Row():
            cut_btn = gr.Button("🎬 截取预览", variant="secondary")

        gr.Markdown("**🎥 预览**（截取后显示）")
        preview_video = gr.Video(label="截取预览", visible=False)
        cut_msg = gr.Markdown("")

        # 任务元数据
        gr.Markdown("**📝 任务信息**")
        task_name_box = gr.Textbox(label="任务名（留空 = 用文件名）", placeholder="")

        gr.Markdown("**🔥 任务级热词**（手动输入 + 可从公共库挑选）")
        hotwords_box = gr.Textbox(
            label="热词",
            placeholder="例如：FunASR 张老师",
            lines=2,
        )

        with gr.Row():
            create_btn = gr.Button("✅ 创建任务", variant="primary")
            cancel_btn = gr.Button("❌ 取消", variant="stop")

    # ---------- 事件绑定 ----------

    file_input.upload(
        fn=lambda fp: on_upload_video(fp, repo_root),
        inputs=[file_input],
        outputs=[file_info, cut_msg],
    )

    file_input.change(
        fn=lambda fp: on_upload_video(fp, repo_root),
        inputs=[file_input],
        outputs=[file_info, cut_msg],
    )

    cut_btn.click(
        fn=lambda fp, s, e: on_cut_preview(fp, s, e, repo_root),
        inputs=[file_input, start_box, end_box],
        outputs=[preview_video, cut_msg, start_box, end_box],
    )

    create_btn.click(
        fn=lambda fp, tn, s, e, pv, hw: on_create_task(fp, tn, s, e, pv, hw, repo_root),
        inputs=[file_input, task_name_box, start_box, end_box, preview_video, hotwords_box],
        outputs=[],  # 由 app.py 在 switch tab 时重新拉取列表
    )

    return {
        "root_col": root_col,
        "file_input": file_input,
        "file_info": file_info,
        "start_box": start_box,
        "end_box": end_box,
        "cut_btn": cut_btn,
        "preview_video": preview_video,
        "cut_msg": cut_msg,
        "task_name_box": task_name_box,
        "hotwords_box": hotwords_box,
        "create_btn": create_btn,
        "cancel_btn": cancel_btn,
    }


def build_picker_after(mgr: TaskManager, hotwords_box) -> dict:
    """在 create_task 主组件构造完毕后，由 app.py 调用 — 在 root_col 内插入「从公共库选择」面板。

    Args:
        mgr: TaskManager
        hotwords_box: 上面已构造的任务热词 Textbox（用于接收选中词）

    Returns:
        dict 含 picker 组件 key
    """
    from slirn_home.hotword_ui import build_hotword_picker_components

    repo_root = mgr.repo_root
    return build_hotword_picker_components(repo_root, hotwords_box)
