"""slirn_home — Slirn 自定义首页 Gradio Blocks。"""

from slirn_home.app import build_app
from slirn_home.paths import (
    ensure_tasklib_importable,
    find_repo_root,
    find_slirn_standalone_root,
)

__all__ = [
    "build_app",
    "ensure_tasklib_importable",
    "find_repo_root",
    "find_slirn_standalone_root",
]
