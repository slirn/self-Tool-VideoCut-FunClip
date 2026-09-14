"""Gradio Blocks 构造 — REQ-B §3.1 / §3.2 / §3.5。"""

from __future__ import annotations

from pathlib import Path

import gradio as gr

from slirn_home.paths import ensure_tasklib_importable
from slirn_home.task_list import (
    arm_delete,
    cancel_delete,
    confirm_delete,
    placeholder_action,
    render_task_list,
    show_detail,
)

# 确保 tasklib 可导入（在 tasklib import 之前调用）
_ensure = ensure_tasklib_importable

from tasklib import TaskManager  # noqa: E402 — 必须在 ensure_tasklib_importable 之后

_DELETE_ARMED = "⚠️ 确认删除？"
_DELETE_IDLE = "🗑️ 删除"


def build_app(repo_root: Path | None = None) -> gr.Blocks:
    """构造 slirn 自定义首页 Gradio Blocks。

    Args:
        repo_root: slirn-standalone 仓库根（默认通过 paths.find_slirn_standalone_root 解析）

    Returns:
        gr.Blocks 实例，调用 .launch() 启动
    """
    _ensure()
    if repo_root is None:
        from slirn_home.paths import find_slirn_standalone_root
        repo_root = find_slirn_standalone_root()

    mgr = TaskManager(repo_root)

    with gr.Blocks(title="Slirn — 自定义首页") as app:
        # 顶部 tab/导航（D1：新标签页链接）
        gr.Markdown(
            "## 🎬 Slirn 自定义首页\n\n"
            "[📋 任务列表](#tasks) | "
            "[🚀 上游首页](http://127.0.0.1:7860/){target=\"_blank\"}\n\n"
            f"**仓库根**: `{repo_root}`"
        )

        status_msg = gr.Markdown("")

        with gr.Row():
            refresh_btn = gr.Button("🔄 刷新任务列表", variant="secondary")
            new_task_btn = gr.Button("➕ 新建任务（REQ-C）", variant="primary")

        with gr.Tabs():
            with gr.TabItem("📋 任务列表", id="tasks"):
                task_df = gr.Dataframe(
                    headers=["ID", "任务名", "视频", "状态", "创建", "修改"],
                    interactive=False,
                    wrap=True,
                )
                task_meta = gr.Label(label="统计")

                with gr.Row():
                    task_id_input = gr.Textbox(
                        label="选中的任务 ID",
                        placeholder="从表格复制 ID 粘到这里",
                    )
                    view_btn = gr.Button("📄 查看详情")
                    delete_btn = gr.Button(_DELETE_IDLE, variant="stop")
                    cancel_btn = gr.Button("取消", visible=False)

                with gr.Row():
                    start_btn = gr.Button("▶️ 开始剪辑")
                    pause_btn = gr.Button("⏸️ 暂停剪辑")
                    stop_btn = gr.Button("⏹️ 停止剪辑")
                    edit_btn = gr.Button("✏️ 编辑任务信息")

                with gr.Accordion("任务详情", open=False, visible=False) as detail_acc:
                    detail_md = gr.Markdown("")

        # ---------- 事件绑定 ----------

        def on_refresh():
            rows, meta = render_task_list(mgr)
            return rows, meta, "已刷新"

        refresh_btn.click(
            fn=on_refresh,
            inputs=[],
            outputs=[task_df, task_meta, status_msg],
        )

        def on_new_task():
            return "⏳ 新建任务功能在 REQ-20260914-001-C 实现"

        new_task_btn.click(
            fn=on_new_task,
            inputs=[],
            outputs=[status_msg],
        )

        # 查看详情（D4）
        def on_view(task_id: str):
            if not task_id.strip():
                return gr.update(visible=False), "⚠️ 请先在「选中的任务 ID」中输入 ID"
            md = show_detail(mgr, task_id.strip())
            return gr.update(visible=True, open=True), md

        view_btn.click(
            fn=on_view,
            inputs=[task_id_input],
            outputs=[detail_acc, detail_md],
        )

        # 删除（D5：单一绑定 + 状态判断 — 双步确认）
        def on_delete_click(task_id: str, current_btn_text: str):
            """第一次点击 → arm；第二次点击（按钮文字变成 _DELETE_ARMED）→ 确认。"""
            if current_btn_text == _DELETE_ARMED:
                # 确认删除
                if not task_id.strip():
                    rows, meta = render_task_list(mgr)
                    return (
                        rows, meta,
                        "⚠️ 请先在「选中的任务 ID」中输入 ID",
                        gr.update(value=_DELETE_IDLE),
                        gr.update(value="", visible=False),
                    )
                rows, meta, msg, _, cancel_vis = confirm_delete(mgr, task_id.strip())
                return (
                    rows, meta, msg,
                    gr.update(value=_DELETE_IDLE),
                    gr.update(value="", visible=not cancel_vis),
                )
            else:
                # 第一次点击：arm
                return (
                    *render_task_list(mgr)[0:2],
                    "",
                    gr.update(value=_DELETE_ARMED),
                    gr.update(value="取消", visible=True),
                )

        delete_btn.click(
            fn=on_delete_click,
            inputs=[task_id_input, delete_btn],
            outputs=[task_df, task_meta, status_msg, delete_btn, cancel_btn],
        )

        # 取消删除
        def on_cancel_delete():
            return (
                gr.update(value=_DELETE_IDLE),
                gr.update(value="", visible=False),
                "",
            )

        cancel_btn.click(
            fn=on_cancel_delete,
            inputs=[],
            outputs=[delete_btn, cancel_btn, status_msg],
        )

        # 占位按钮（REQ-C/D 实现）
        for btn, feature in [
            (start_btn, "开始剪辑"),
            (pause_btn, "暂停剪辑"),
            (stop_btn, "停止剪辑"),
            (edit_btn, "编辑任务信息"),
        ]:
            btn.click(
                fn=lambda f=feature: placeholder_action(f),
                inputs=[],
                outputs=[status_msg],
            )

        # 初始加载
        app.load(
            fn=lambda: render_task_list(mgr),
            inputs=[],
            outputs=[task_df, task_meta],
        )

    return app
