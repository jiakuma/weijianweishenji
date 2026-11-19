"""一键登录知乎：打开本机浏览器登录窗口，登录后自动同步 Cookie。

纯增量模块：只在用户主动点击时启动；不修改任何现有导入逻辑。
只有确实检测到 z_c0（登录态）才写入 zhihuCookie 设置；未登录、超时、
取消或浏览器启动失败都不会覆盖现有配置。Cookie 不保存在模块状态里，
也不出现在任何接口响应中。
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from .db import SessionLocal, save_settings

BROWSERS = [
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
]
LOGIN_URL = "https://www.zhihu.com/signin"
TIMEOUT_SEC = 300

_lock = threading.Lock()
_state = {"state": "idle", "message": ""}
_proc: subprocess.Popen | None = None
_profile: str | None = None
_port = 0


def status() -> dict:
    with _lock:
        return dict(_state)


def _set(state: str, message: str = "") -> None:
    with _lock:
        _state["state"] = state
        _state["message"] = message


def _cleanup() -> None:
    global _proc, _profile
    proc, _proc = _proc, None
    profile, _profile = _profile, None
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    if profile:
        shutil.rmtree(profile, ignore_errors=True)


def _browser() -> str | None:
    for path in BROWSERS:
        if Path(path).exists():
            return path
    return None


def _read_cookie(port: int) -> str | None:
    import websocket  # 延迟导入：缺包也不影响后端启动

    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2))
    page = next((t for t in targets if t.get("type") == "page"), None)
    if not page:
        return None
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=5)
    try:
        seq = 0

        def cmd(method: str, params: dict | None = None) -> dict:
            nonlocal seq
            seq += 1
            ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
            while True:
                reply = json.loads(ws.recv())
                if reply.get("id") == seq:
                    return reply

        cmd("Network.enable")
        cookies = cmd("Network.getAllCookies").get("result", {}).get("cookies", [])
    finally:
        ws.close()

    zhihu = [c for c in cookies if "zhihu.com" in str(c.get("domain", ""))]
    if not any(c.get("name") == "z_c0" for c in zhihu):
        return None
    pairs, seen = [], set()
    for c in zhihu:
        name = c.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name}={c.get('value', '')}")
    return "; ".join(pairs)


def _poll(port: int) -> None:
    deadline = time.time() + TIMEOUT_SEC
    while time.time() < deadline:
        if status()["state"] != "waiting":
            return
        time.sleep(1.5)
        try:
            cookie = _read_cookie(port)
        except Exception:
            continue
        if not cookie:
            continue
        try:
            with SessionLocal() as s:
                save_settings(s, {"zhihuCookie": cookie})
            _set("ok", "已同步知乎登录态")
        except Exception as exc:
            _set("error", f"保存失败：{type(exc).__name__}")
        _cleanup()
        return
    if status()["state"] == "waiting":
        _set("timeout", "登录超时，可重新点击")
    _cleanup()


def start() -> dict:
    global _proc, _profile, _port
    if status()["state"] == "waiting":
        return {"ok": False, "message": "登录窗口已打开"}
    browser = _browser()
    if not browser:
        _set("error", "未找到本机 Chrome / Edge")
        return {"ok": False, "message": _state["message"]}
    try:
        import websocket  # noqa: F401  （提前确认依赖可用）
    except ImportError:
        _set("error", "缺少 websocket-client 依赖")
        return {"ok": False, "message": _state["message"]}

    _cleanup()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        _port = s.getsockname()[1]
    _profile = tempfile.mkdtemp(prefix="kb_zhihu_login_")
    try:
        _proc = subprocess.Popen(
            [
                browser,
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
                "--remote-allow-origins=*",
                f"--remote-debugging-port={_port}",
                f"--user-data-dir={_profile}",
                "--window-size=960,720",
                LOGIN_URL,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        _cleanup()
        _set("error", f"启动浏览器失败：{type(exc).__name__}")
        return {"ok": False, "message": _state["message"]}
    _set("waiting", "已打开登录窗口，请在窗口中登录知乎")
    threading.Thread(target=_poll, args=(_port,), daemon=True).start()
    return {"ok": True, "message": _state["message"]}


def cancel() -> dict:
    _set("idle", "")
    _cleanup()
    return {"ok": True}
