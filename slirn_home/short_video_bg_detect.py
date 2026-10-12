"""REQ-20261013-105：短视频精剪 bg 白底检测（v1 完整版）。

启发式算法（无 OpenCV 依赖；用 stdlib + ffmpeg）：
1. ffmpeg 抽 1 帧 PNG（首帧）
2. 读 PNG（用 struct 解码，避开 PIL 依赖 — 短视频可能没装 Pillow）
3. 扫描所有像素：白底 = RGB(255,255,255) 或接近；非白底 = 内容区
4. 计算内容区 bounding box（min/max x/y）→ 落 detected_region

短/长隔离：detected_region 落 project.json → stage7_finalize.params；
不写长视频 fine_compose.json。

v1 简化：仅支持 PNG 解码（ffmpeg -frames:v 1 必输出 PNG）；
不做多帧采样、不做透视校正。
"""
from __future__ import annotations

import logging
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any

from slirn_home import short_video_service as svc

log = logging.getLogger(__name__)

# 白色判定阈值（RGB 三通道都 > 240 视为白）
_WHITE_THRESHOLD = 240


def _extract_first_frame(video: Path, out_png: Path) -> bool:
    """ffmpeg 抽首帧 PNG。"""
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-frames:v", "1", "-f", "image2",
        str(out_png),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return proc.returncode == 0 and out_png.is_file() and out_png.stat().st_size > 0


def _decode_png_to_rgb(png: Path) -> tuple[int, int, list[tuple[int, int, int]]]:
    """最小 PNG 解码器：只支持 8-bit RGB / RGBA（非隔行）。无外部依赖。

    返回 (w, h, [(r,g,b), ...] 长度=w*h)。失败抛 ValueError。
    """
    data = png.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos = 8
    width = height = 0
    bit_depth = color_type = 0
    idat = b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 8 + length + 4  # skip CRC
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
    if bit_depth != 8 or color_type not in (2, 6):  # RGB / RGBA only
        raise ValueError(f"不支持的 PNG 格式 bit_depth={bit_depth} color_type={color_type}")
    raw = zlib.decompress(idat)
    bpp = 3 if color_type == 2 else 4
    stride = 1 + width * bpp  # filter byte + pixel bytes
    pixels: list[tuple[int, int, int]] = []
    prev = bytes(width * bpp)
    for y in range(height):
        row_start = y * stride
        filt = raw[row_start]
        line = bytearray(raw[row_start + 1:row_start + stride])
        # 应用 filter（仅支持 None=0 / Sub=1 / Up=2 / Avg=3 / Paeth=4）
        if filt == 0:
            cur = bytes(line)
        elif filt == 1:  # Sub
            cur = bytearray(line)
            for i in range(bpp, len(line)):
                cur[i] = (cur[i] + cur[i - bpp]) & 0xFF
            cur = bytes(cur)
        elif filt == 2:  # Up
            cur = bytes((a + b) & 0xFF for a, b in zip(line, prev))
        elif filt == 3:  # Avg
            cur = bytearray(line)
            for i in range(len(line)):
                left = cur[i - bpp] if i >= bpp else 0
                cur[i] = (cur[i] + (left + prev[i]) // 2) & 0xFF
            cur = bytes(cur)
        elif filt == 4:  # Paeth
            cur = bytearray(line)
            for i in range(len(line)):
                a = cur[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                cur[i] = (cur[i] + pr) & 0xFF
            cur = bytes(cur)
        else:
            raise ValueError(f"未知 PNG filter={filt}")
        for x in range(width):
            r = cur[x * bpp]
            g = cur[x * bpp + 1]
            b = cur[x * bpp + 2]
            pixels.append((r, g, b))
        prev = cur
    return width, height, pixels


def _compute_content_bbox(w: int, h: int, pixels: list[tuple[int, int, int]]) -> dict:
    """扫描所有像素：非白底 → 内容区；返回 {x, y, w, h, content_ratio}。"""
    min_x, min_y, max_x, max_y = w, h, -1, -1
    non_white = 0
    for i, (r, g, b) in enumerate(pixels):
        if not (r > _WHITE_THRESHOLD and g > _WHITE_THRESHOLD and b > _WHITE_THRESHOLD):
            x = i % w
            y = i // w
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
            non_white += 1
    if max_x < 0:
        # 全白
        return {"x": 0, "y": 0, "w": w, "h": h, "content_ratio": 0.0}
    return {"x": min_x, "y": min_y, "w": max_x - min_x + 1, "h": max_y - min_y + 1,
            "content_ratio": round(non_white / max(1, len(pixels)), 4)}


def detect_bg_white_area(repo_root: Path | str, task_id: str, project_id: str,
                         *, source_video: str = "") -> dict:
    """bg 检测：从 source_video 抽首帧 → 算非白底 bbox → 落 params.detected_region。

    source_video 缺省用 stage5_coarse.highlights[0].coarse_mp4。
    """
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    if not source_video:
        s5 = svc.get_stage_state(project, "stage5_coarse")
        hls = s5.get("highlights") or []
        if not hls:
            raise svc.ShortVideoError("没有粗剪视频可检测")
        source_video = str(hls[0].get("coarse_mp4") or "")
    src = (root / source_video).resolve() if not Path(source_video).is_absolute() else Path(source_video)
    if not src.is_file():
        raise svc.ShortVideoError(f"源视频不存在: {src}")

    tmp = svc.stage6_dir(root, task_id, project_id) / "_bg_detect.png"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not _extract_first_frame(src, tmp):
            raise svc.ShortVideoError("ffmpeg 抽帧失败")
        w, h, pixels = _decode_png_to_rgb(tmp)
        region = _compute_content_bbox(w, h, pixels)
        # 落 project.json
        from slirn_home import short_video_fine as _fine
        current = _fine._get_params(project)
        current["detected_region"] = region
        s = svc.get_stage_state(project, "stage7_finalize")
        s["params"] = current
        svc.set_stage_state(project, "stage7_finalize", s)
        svc.save_project(root, project)
        return region
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def apply_detected_to_layout(repo_root: Path | str, task_id: str, project_id: str) -> dict:
    """把 detected_region 填充到 layout.video 的 x/y/w/h（裁剪到内容区）。"""
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    from slirn_home import short_video_fine as _fine
    params = _fine._get_params(project)
    region = params.get("detected_region")
    if not region or not isinstance(region, dict):
        raise svc.ShortVideoError("没有检测结果；先调用 detect_bg_white_area")
    # 把 region 写入 layout.video
    layout = params.get("layout") or {}
    layout["video"] = {
        "x": int(region.get("x") or 0),
        "y": int(region.get("y") or 0),
        "w": int(region.get("w") or 0),
        "h": int(region.get("h") or 0),
    }
    params["layout"] = layout
    s = svc.get_stage_state(project, "stage7_finalize")
    s["params"] = params
    svc.set_stage_state(project, "stage7_finalize", s)
    svc.save_project(root, project)
    return {"layout": layout["video"]}
