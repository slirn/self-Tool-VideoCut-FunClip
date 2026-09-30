# -*- coding: utf-8 -*-
"""使用说明书截图驱动 — Task F（2026-09-30）

通过 Edge headless + Chrome DevTools Protocol 驱动 7861 服务截图，
产出 docs/images/manual/*.png 供 docs/使用说明书.md 引用。

用法（服务 7861 已启动）：
    ./.venv/Scripts/python.exe tools/take_manual_screenshots.py

要点：
- 零新依赖：websockets(sync) + requests 均已在 venv
- 登录：先无 cookie 打开（截登录弹窗），再 AuthStore.create_session('admin')
  注入 slirn_session cookie 后刷新
- 交互走真实 JS 点击（data-action 元素），符合「E2E 必须真实点击」的项目经验
- 两种截法：viewport（页面/弹窗）；element clip（长面板取元素区）
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import requests
from websockets.sync.client import connect

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

BASE = "http://127.0.0.1:7861"
CDP_PORT = 9333
IMG_DIR = REPO / "docs" / "images" / "manual"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
TARGET_TID = "20260924-001"  # 全流程数据齐全的示范任务（1h11m 课程视频）

# (工作台 data-pane 键, 标签, 截图文件名)。注意 data-pane 用的是 _WB_STAGES 的
# 键（subtitle / fine_review…），不是 pipeline 的阶段键（subtitle_generation /
# optimize…）— 首版驱动用错键导致两张图拍成别的 pane（点击失败 + 等待超时后
# 拍了当前视口，与相邻图字节级相同）。
STAGE_PANES = [
    ("subtitle", "字幕生成", "05-stage-subtitle_generation"),
    ("subtitle_review", "字幕修订", "05-stage-subtitle_review"),
    ("rough_cut", "切分修剪", "05-stage-rough_cut"),
    ("rough_compose", "粗剪合成", "05-stage-rough_compose"),
    ("fine_review", "优化字幕", "05-stage-optimize"),
    ("fine_cut", "精剪合成", "05-stage-fine_cut"),
]

# ---------------------------------------------------------------- helpers


def make_admin_session() -> str:
    from slirn_home.auth import AuthStore
    from slirn_home.paths import find_slirn_standalone_root

    store = AuthStore(find_slirn_standalone_root())
    return store.create_session("admin", ttl_days=1)


class CDP:
    """极简 CDP 同步客户端：一个 page target 上的 request/response。"""

    def __init__(self, ws):
        self.ws = ws
        self._id = 0

    def send(self, method: str, params: dict | None = None, timeout: float = 30.0):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv(timeout=max(0.5, deadline - time.time()))
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error']}")
                return msg.get("result", {})
            # 事件（Page.loadEventFired 等）直接跳过
        raise TimeoutError(f"CDP {method} 无响应")

    # ---- 便捷封装 ----
    def js(self, expr: str, await_promise: bool = False):
        r = self.send("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        return r.get("result", {}).get("value")

    def wait_js(self, expr: str, timeout: float = 15.0, interval: float = 0.4) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.js(expr):
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    def click(self, sel: str) -> bool:
        ok = self.js(
            f"(function(){{var e=document.querySelector({sel!r});"
            f"if(!e)return false;e.click();return true;}})()"
        )
        return bool(ok)

    def navigate(self, url: str):
        self.send("Page.navigate", {"url": url})
        time.sleep(1.5)  # loadEventFired + 骨架渲染

    def shot_viewport(self, name: str, scroll_sel: str | None = None):
        if scroll_sel:
            # 与 shot_element 同理：scrollIntoView 在部分状态下不滚动，
            # 用页坐标换算 + 显式 scrollTo
            page_y = self.js(
                f"(function(){{var e=document.querySelector({scroll_sel!r});"
                f"if(!e)return null;return Math.max(0,"
                f"e.getBoundingClientRect().y + window.scrollY);}})()"
            )
            if page_y is not None:
                self.js(f"window.scrollTo(0, Math.max(0, {page_y} - 20))")
            time.sleep(0.5)
        r = self.send("Page.captureScreenshot", {"format": "png"})
        _save(name, r["data"])
        print(f"  [ok] {name}.png (viewport)")

    def shot_element(self, name: str, sel: str, pad: int = 12):
        """元素截图：scrollTo 对准 → 视口整图截取 → PIL 按 rect 裁剪。

        不用 CDP clip（Page.captureScreenshot 的 clip 参数在本版 Edge
        headless 下渲染空白表面，无论是否 captureBeyondViewport）；
        视口整图截取被反复验证有内容，裁剪在客户端做，确定性最高。
        """
        import io

        from PIL import Image

        for attempt in range(2):
            page_y = self.js(
                f"(function(){{var e=document.querySelector({sel!r});if(!e)return null;"
                f"var r=e.getBoundingClientRect();"
                f"return Math.max(0, r.y + window.scrollY);}})()"
            )
            if page_y is None:
                print(f"  [skip] {name}: 找不到 {sel}")
                return
            self.js(f"window.scrollTo(0, Math.max(0, {page_y} - 120))")
            time.sleep(0.6)
            rect = self.js(
                f"(function(){{var e=document.querySelector({sel!r});if(!e)return null;"
                f"var r=e.getBoundingClientRect();var vh=window.innerHeight;"
                f"if(r.y > vh || r.y + Math.min(r.height, vh-40) < 0) return null;"
                f"return JSON.stringify({{x:r.x,y:r.y,w:r.width,h:r.height,"
                f"iw:window.innerWidth}});}})()"
            )
            if not rect:
                print(f"  [retry {attempt}] {name}: 滚动后元素不在视口，重试")
                continue
            box = json.loads(rect)
            r = self.send("Page.captureScreenshot", {"format": "png"})
            im = Image.open(io.BytesIO(base64.b64decode(r["data"])))
            scale = im.width / box["iw"]
            vh_px = im.height
            left = max(0, int((box["x"] - pad) * scale))
            top = max(0, int((box["y"] - pad) * scale))
            right = min(im.width, int((box["x"] + box["w"] + pad) * scale))
            bottom = min(vh_px, int((box["y"] + min(box["h"], vh_px / scale) + pad) * scale))
            im.crop((left, top, right, bottom)).save(
                IMG_DIR / f"{name}.png", optimize=True)
            print(f"  [ok] {name}.png (crop {right-left}x{bottom-top})")
            return
        print(f"  [fail] {name}: 两次尝试均未对准元素")


def _save(name: str, b64: str):
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    (IMG_DIR / f"{name}.png").write_bytes(base64.b64decode(b64))


def close_modals(cdp: CDP):
    """关闭所有弹窗（overlay hidden + .slirn-modal display none）。"""
    cdp.js(
        "(function(){document.querySelectorAll('.slirn-modal-overlay:not([hidden])')"
        ".forEach(function(m){m.hidden=true;});"
        "document.querySelectorAll('.slirn-modal').forEach(function(m){"
        "m.style.display='none';});})()"
    )
    time.sleep(0.3)


# ---------------------------------------------------------------- main


def main():
    # 0) 前置检查
    try:
        r = requests.get(BASE + "/slirn/api/health", timeout=5)
        print(f"[1/6] 服务健康检查：{r.status_code}")
        if r.status_code != 200:
            print("  ⚠ 非 200，继续尝试（health 可能不在白名单）")
    except Exception as e:
        sys.exit(f"服务 {BASE} 不可达：{e}")

    token = make_admin_session()
    print(f"[2/6] admin 会话已创建（token 前 8 位 {token[:8]}…）")

    # 1) 启动 Edge headless
    user_data = Path(__file__).parent / "_edge_cdp_profile"
    proc = subprocess.Popen([
        EDGE,
        "--headless=new",
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={user_data}",
        # 视口/缩放全部用启动参数设定：Emulation.setDeviceMetricsOverride
        # 在 headless 下会把 window 滚动钉死（scrollY 恒 0、scrollIntoView
        # 无效），导致 scroll_sel/元素截图全部停在页顶拍 → 用窗口尺寸 +
        # 强制 DSF 替代（实测 scrollTo 正常）。
        "--window-size=1440,1000",
        "--force-device-scale-factor=1.5",
        "--hide-scrollbars",
        "--no-first-run",
        "--disable-gpu",
        "about:blank",
    ])
    try:
        ws_url = None
        for _ in range(40):
            time.sleep(0.5)
            try:
                targets = requests.get(
                    f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2).json()
                pages = [t for t in targets if t.get("type") == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                continue
        if not ws_url:
            sys.exit("Edge CDP 未就绪（40 次轮询无 page target）")
        print(f"[3/6] Edge headless 已就绪（CDP {CDP_PORT}）")

        with connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            cdp.send("Page.enable")
            cdp.send("Network.enable")
            print("[4/6] 开始截图序列")

            # ---- 00 登录弹窗（无 cookie）----
            cdp.navigate(BASE + "/")
            cdp.wait_js("!!document.getElementById('slirn-login-modal')", 10)
            if not cdp.js(
                    "getComputedStyle(document.getElementById('slirn-login-modal'))"
                    ".display !== 'none'"):
                cdp.click('[data-action="show-login"]')
            time.sleep(0.6)
            cdp.shot_viewport("00-login-modal")

            # ---- 注入会话 cookie 并刷新 ----
            cdp.send("Network.setCookie", {
                "name": "slirn_session", "value": token,
                "url": BASE + "/",
            })
            cdp.navigate(BASE + "/")
            cdp.wait_js("!!document.querySelector('[data-action=\"goto-tasks\"]')", 15)
            time.sleep(1.5)  # 首页统计异步拉取
            cdp.shot_viewport("01-dashboard")

            # ---- 02 任务列表 ----
            cdp.click('[data-action="goto-tasks"]')
            cdp.wait_js(
                "getComputedStyle(document.getElementById('slirn-tab-tasks'))"
                ".display !== 'none'", 10)
            cdp.wait_js("!!document.querySelector('.slirn-task-card')", 10)
            time.sleep(0.8)
            cdp.shot_viewport("02-task-list")

            # ---- 03 任务详情（示范任务）----
            if not cdp.click(
                    f'.slirn-task-card[data-task-id="{TARGET_TID}"]'):
                print(f"  [warn] 任务卡 {TARGET_TID} 未找到，改点第一张卡")
                cdp.click(".slirn-task-card")
            cdp.wait_js(
                "getComputedStyle(document.getElementById('slirn-tab-detail'))"
                ".display !== 'none'", 10)
            time.sleep(0.8)
            cdp.shot_viewport("03-task-detail")

            # ---- 04 工作台总览 ----
            cdp.click('[data-action="open-workbench"]')
            if not cdp.wait_js(
                    "!!document.querySelector('#slirn-tab-workbench .slirn-wb-stage')",
                    20):
                print("  [warn] 工作台未加载出 stage 条")
            time.sleep(2.0)  # workbench 数据拉取 + 增强渲染
            cdp.shot_viewport("04-workbench-overview")

            # ---- 05~10 六个阶段 pane ----
            for key, label, shot_name in STAGE_PANES:
                if not cdp.click(f'.slirn-wb-stage[data-pane="{key}"]'):
                    print(f"  [warn] 阶段卡 data-pane={key} 未找到")
                if not cdp.wait_js(
                        f"getComputedStyle(document.getElementById("
                        f"'slirn-wb-pane-{key}')).display !== 'none'", 10):
                    print(f"  [warn] pane {key} 等待超时（键名可能不对）")
                time.sleep(1.2)
                cdp.shot_viewport(shot_name, scroll_sel=f"#slirn-wb-pane-{key}")

            # ---- 11 执行日志 pane ----
            cdp.click('.slirn-wb-stage[data-pane="logs"], .slirn-wb-stage-logs')
            cdp.wait_js(
                "!!document.getElementById('slirn-wb-pane-logs')", 10)
            time.sleep(1.0)
            cdp.shot_viewport("11-logs", scroll_sel="#slirn-wb-pane-logs")

            # ---- 12 查看最终字幕（搜索确认）----
            cdp.click('.slirn-wb-stage[data-pane="optimize"]')
            time.sleep(0.8)
            if cdp.click('[data-action="opt-final-view"]'):
                if cdp.wait_js("!!document.querySelector('.slirn-opt-final-card')", 10):
                    # 输入搜索词触发高亮（中文高频字，示范「确认更改生效」用法）
                    cdp.js(
                        "(function(){var q=document.getElementById("
                        "'slirn-opt-final-q');if(q){q.value='的';"
                        "q.dispatchEvent(new Event('input',{bubbles:true}));}})()")
                    time.sleep(0.9)  # 200ms 防抖 + 渲染
                    cdp.shot_viewport("12-opt-final-view")
                close_modals(cdp)
            else:
                print("  [skip] 12-opt-final-view：无最终字幕按钮")

            # ---- 13 批量替换面板 ----
            if cdp.click('[data-action="opt-batch-open"]'):
                if cdp.wait_js("!!document.querySelector('.slirn-opt-batchrep')", 8):
                    cdp.js(
                        "(function(){var i=document.getElementById("
                        "'slirn-opt-batch-q');if(i){i.value='测试';"
                        "i.dispatchEvent(new Event('input',{bubbles:true}));}})()")
                    time.sleep(0.9)
                    cdp.shot_viewport("13-opt-batch")
                close_modals(cdp)
            else:
                print("  [skip] 13-opt-batch：无批量替换按钮")

            # ---- 14 流程配置面板（复选框默认全选示范）----
            cdp.click('[data-action="pipe-open"]')
            if cdp.wait_js("!!document.querySelector('.slirn-pipe-section')", 15):
                # renderPanel 的外层 <details> 默认收起（面板仅 ~102px），
                # 真实用户点 summary 头展开 — 这里等价设置 open=true
                cdp.js(
                    "(function(){var d=document.querySelector("
                    "'.slirn-pipe-panel-details');if(d)d.open=true;})()")
                time.sleep(1.0)
                cdp.shot_viewport("14-pipe-panel", scroll_sel="#slirn-pipe-panel")
                # 精剪合成区段（radio 卡片 + HH:MM:SS）
                cdp.shot_element(
                    "14b-pipe-fine-cut",
                    '.slirn-pipe-section[data-pipe-section="fine_cut"]')
                # ---- 15 产物浏览器 ----
                cdp.click('[data-action="pipe-outputs-refresh"]')
                if cdp.wait_js("!!document.querySelector('[data-pipe-outputs-item]')", 10):
                    time.sleep(0.8)
                    cdp.shot_element("15-pipe-outputs", "[data-pipe-outputs-panel]")
                else:
                    print("  [skip] 15-pipe-outputs：产物列表未加载")
            else:
                print("  [skip] 14-pipe-panel：流程配置未加载")

            # ---- 16 LLM 设置 ----
            if cdp.click('[data-action="open-llm-settings"]'):
                if cdp.wait_js(
                        "getComputedStyle(document.getElementById('slirn-llm-modal'))"
                        ".display !== 'none'", 8):
                    time.sleep(0.6)
                    cdp.shot_viewport("16-llm-settings")
                close_modals(cdp)

            # ---- 17 用户管理（admin）----
            if cdp.click('[data-action="users-open"]'):
                if cdp.wait_js(
                        "getComputedStyle(document.getElementById('slirn-users-modal'))"
                        ".display !== 'none'", 8):
                    time.sleep(0.6)
                    cdp.shot_viewport("17-users")
                close_modals(cdp)

            # ---- 18 新建任务 ----
            cdp.click('[data-action="goto-create"]')
            cdp.wait_js(
                "getComputedStyle(document.getElementById('slirn-tab-create'))"
                ".display !== 'none'", 10)
            cdp.wait_js("!!document.getElementById('slirn-hotwords-manual')", 10)
            time.sleep(0.8)
            cdp.shot_viewport("18-create-task")

            # ---- 19 热词库 ----
            cdp.click('[data-action="goto-hotwords"]')
            cdp.wait_js(
                "getComputedStyle(document.getElementById('slirn-tab-hotwords'))"
                ".display !== 'none'", 10)
            time.sleep(0.8)
            cdp.shot_viewport("19-hotwords")

        print("[5/6] 截图序列完成")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        print("[6/6] Edge 已退出")

    # 校验产物
    files = sorted(IMG_DIR.glob("*.png"))
    total = sum(f.stat().st_size for f in files)
    print(f"\n产出 {len(files)} 张 / {total / 1024:.0f} KB → {IMG_DIR}")
    for f in files:
        kb = f.stat().st_size / 1024
        flag = " ⚠ 过小" if kb < 10 else ""
        print(f"  {f.name:36s} {kb:8.0f} KB{flag}")


if __name__ == "__main__":
    main()
