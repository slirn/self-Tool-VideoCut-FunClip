"""路径解析 — 找 slirn-standalone 仓库根（含 tasklib）和当前 funclip-main 仓库根。"""

from __future__ import annotations

import sys
from pathlib import Path


def find_repo_root() -> Path:
    """funclip-main 仓库根（含 slirn_home/）。"""
    return Path(__file__).resolve().parent.parent


def find_slirn_standalone_root() -> Path:
    """slirn-standalone 仓库根（含 tasklib/）。

    查找顺序：
    1. 环境变量 SLIRN_STANDALONE_ROOT
    2. funclip-main 的 sibling 目录 ../slirn-standalone/
    3. funclip-main/slirn/（submodule mount，**仅作为后备**，通常不直接放 tasklib）
    """
    env = __import__("os").environ.get("SLIRN_STANDALONE_ROOT")
    if env:
        p = Path(env).resolve()
        if (p / "tasklib").is_dir():
            return p
        raise FileNotFoundError(f"SLIRN_STANDALONE_ROOT 指向 {p}，但缺少 tasklib/")

    funclip_root = find_repo_root()

    # sibling 目录
    sibling = funclip_root.parent / "slirn-standalone"
    if (sibling / "tasklib").is_dir():
        return sibling

    # funclip-main/slirn/（submodule mount）— 通常 tasklib 不在这里（tasklib 在 slirn-standalone/tasklib/）
    # 但保留这个 fallback 给测试环境
    submodule_mount = funclip_root / "slirn"
    if (submodule_mount / "tasklib").is_dir():
        return submodule_mount

    raise FileNotFoundError(
        f"找不到 slirn-standalone 仓库根（期望 {sibling} 或 $SLIRN_STANDALONE_ROOT）"
    )


def ensure_tasklib_importable() -> Path:
    """把 slirn-standalone 根加到 sys.path，返回该路径。"""
    root = find_slirn_standalone_root()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root
