"""本地任务队列、设置和Edge扩展连接服务。"""
from __future__ import annotations

import ctypes
import hmac
import json
import os
import queue
import re
import secrets
import threading
import time
import uuid
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from downloader import APP_ROOT, Cancelled, DownloadError, account_info, download_video, normalize_input, video_metadata

PORT = 17890
FINISHED = {"已完成", "已跳过", "失败", "已取消", "已中断"}


def checked_cookies(value):
    if not isinstance(value, dict) or len(value) > 200:
        raise ValueError("登录数据格式不正确")
    if any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z0-9_!#$%&'*+.^`|~-]{1,128}", k) or not isinstance(v, str) or len(v) > 16384 or re.search(r"[\x00-\x1f\x7f]", v) for k, v in value.items()):
        raise ValueError("登录数据格式不正确")
    return dict(value)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class Settings:
    def __init__(self, root=APP_ROOT):
        self.root = Path(root)
        self.data_dir = self.root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "settings.json"
        self.values = {"directory": str(self.root / "下载"), "quality": 0, "token": secrets.token_urlsafe(24)}
        self.error = ""
        if self.path.exists():
            try:
                self.values.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                self.error = "设置文件读取失败，已使用默认设置。原文件已保留。"
        if not self.path.exists():
            self.save()

    def save(self):
        atomic_json(self.path, self.values)

    def set(self, key, value):
        self.values[key] = value
        self.save()

    def cookies(self):
        path = self.data_dir / "login.dat"
        if not path.exists():
            return {}
        try:
            return json.loads(protect_data(path.read_bytes(), decrypt=True).decode("utf-8"))
        except Exception:
            self.error = "保存的登录信息无法读取，请重新导入Cookie。"
            return {}

    def save_cookies(self, cookies):
        path = self.data_dir / "login.dat"
        if cookies:
            payload = json.dumps(cookies).encode("utf-8")
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(protect_data(payload))
            temporary.replace(path)
        else:
            path.unlink(missing_ok=True)


def protect_data(payload, decrypt=False):
    """使用Windows DPAPI，登录信息只可由当前Windows用户解密。"""
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(payload)
    source = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


class TaskManager:
    def __init__(self, settings, download_function=download_video):
        self.settings = settings
        self.download_function = download_function
        self.lock = threading.RLock()
        self.wakeup = threading.Event()
        self.closed = threading.Event()
        self.cancel = threading.Event()
        self.paused = False
        self.active_id = None
        self.cookies = settings.cookies()
        self.login_revision = 0
        self.cookie_source = "本地保存" if self.cookies else ""
        self.login_updates = queue.Queue(maxsize=1)
        self.task_cookies = {}
        self.tasks = []
        self.history_path = settings.data_dir / "tasks.json"
        self.directory_requests = queue.Queue()
        self.window_requests = queue.Queue(maxsize=1)
        if self.history_path.exists():
            try:
                self.tasks = json.loads(self.history_path.read_text(encoding="utf-8"))
                for task in self.tasks:
                    if task["status"] not in FINISHED:
                        task.update(status="已中断", detail="上次程序退出，请点击重试继续", progress=0)
            except (ValueError, OSError, KeyError, TypeError):
                self.tasks = []
        self.worker = threading.Thread(target=self._work, daemon=True, name="download-worker")
        self.worker.start()

    def _save(self):
        atomic_json(self.history_path, self.tasks)

    def snapshot(self):
        with self.lock:
            return [dict(task) for task in self.tasks]

    def add(self, specs, cookies=None):
        if not specs or len(specs) > 500:
            raise ValueError("一次请添加1到500个任务。")
        added = []
        with self.lock:
            for spec in specs:
                bv, url_page, url = normalize_input(spec["url"])
                page = int(spec.get("page") or url_page)
                if not 1 <= page <= 10000:
                    raise ValueError("分P编号无效。")
                directory = str(Path(spec.get("directory") or self.settings.values["directory"]).expanduser().resolve())
                quality = int(spec.get("quality") or 0)
                identity = f"{bv}|{page}|{quality}|{os.path.normcase(directory)}" + ("|all" if spec.get("all_pages") else "")
                duplicate = next((t for t in self.tasks if t["identity"] == identity and t["status"] not in {"失败", "已取消", "已中断"} and (t["status"] not in {"已完成", "已跳过"} or Path(t.get("path", "")).is_file())), None)
                if duplicate:
                    continue
                task = {"id": uuid.uuid4().hex, "identity": identity, "url": url, "bvid": bv, "page": page, "quality": quality, "directory": directory, "title": spec.get("title") or bv, "status": "等待中", "progress": 0, "detail": "等待下载", "path": "", "created": time.strftime("%Y-%m-%d %H:%M:%S"), "all_pages": bool(spec.get("all_pages"))}
                self.tasks.append(task)
                if cookies:
                    self.task_cookies[task["id"]] = dict(cookies)
                added.append(task["id"])
            self._save()
        self.wakeup.set()
        return added

    def set_cookies(self, cookies, persist=True, source="本地保存"):
        with self.lock:
            if persist:
                self.settings.save_cookies(cookies)
            self.cookies = dict(cookies)
            self.cookie_source = source if cookies else ""
            self.login_revision += 1
            return self.login_revision

    def sync_login(self, cookies, account):
        with self.lock:
            revision = self.set_cookies(cookies, persist=False, source="Edge临时同步")
            try:
                self.login_updates.get_nowait()
            except queue.Empty:
                pass
            self.login_updates.put_nowait({"account": dict(account), "revision": revision})

    def cancel_task(self, task_id):
        with self.lock:
            task = next((t for t in self.tasks if t["id"] == task_id), None)
            if not task:
                return
            if task_id == self.active_id:
                self.cancel.set()
            elif task["status"] == "等待中":
                task.update(status="已取消", detail="已取消")
                self.task_cookies.pop(task_id, None)
                self._save()

    def retry(self, task_id):
        with self.lock:
            task = next((t for t in self.tasks if t["id"] == task_id), None)
            if task and task["status"] in {"失败", "已取消", "已中断"}:
                task.update(status="等待中", detail="等待重试", progress=0)
                self._save()
        self.wakeup.set()

    def clear_finished(self):
        with self.lock:
            self.tasks = [t for t in self.tasks if t["status"] not in FINISHED]
            self._save()

    def _work(self):
        while not self.closed.is_set():
            with self.lock:
                task = next((t for t in self.tasks if t["status"] == "等待中"), None) if not self.paused else None
                if task:
                    self.active_id = task["id"]
                    self.cancel.clear()
                    task.update(status="解析中", detail="获取视频信息")
                    cookies = dict(self.task_cookies.get(task["id"], self.cookies))
            if not task:
                self.wakeup.wait(.5)
                self.wakeup.clear()
                continue
            def notify(status, progress, detail):
                with self.lock:
                    task.update(status=status, progress=round(progress, 1), detail=detail)
            try:
                if task.get("all_pages"):
                    meta = video_metadata(task["url"], cookies)
                    extras = [{"url": task["url"], "page": p["page"], "directory": task["directory"], "quality": task["quality"], "title": meta["title"]} for p in meta["pages"] if p["page"] != task["page"]]
                    if extras:
                        self.add(extras, cookies)
                    task["all_pages"] = False
                result = self.download_function(task, cookies, notify, self.cancel)
                with self.lock:
                    task.update(status="已完成", progress=100, detail="下载完成", **result)
            except Cancelled:
                with self.lock:
                    task.update(status="已取消", detail="已取消，临时文件已清理")
            except Exception as exc:
                with self.lock:
                    message = str(exc)
                    # Do not include secret values or signed playback URLs in persisted errors.
                    message = re.sub(r"https?://\S+", "[请求地址]", message)
                    for value in cookies.values():
                        if len(str(value)) > 4:
                            message = message.replace(str(value), "[已隐藏]")
                    task.update(status="失败", detail=message[:900])
            finally:
                with self.lock:
                    self.active_id = None
                    self.task_cookies.pop(task["id"], None)
                    self._save()
            self.closed.wait(.6)

    def shutdown(self):
        self.closed.set()
        self.cancel.set()
        self.wakeup.set()
        self.worker.join(timeout=2)


class Bridge:
    def __init__(self, manager, port=PORT):
        self.manager = manager
        self.port = port
        self.error = ""
        self.directory_results = {}
        bridge = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def allowed_origin(self):
                origin = self.headers.get("Origin", "")
                return not origin or bool(re.fullmatch(r"chrome-extension://[a-p]{32}", origin))

            def valid_host(self):
                return self.headers.get("Host") in {f"127.0.0.1:{bridge.port}", f"localhost:{bridge.port}"}

            def reply(self, status, data):
                payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                origin = self.headers.get("Origin", "")
                if origin and self.allowed_origin():
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Bili-Token")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def authenticate(self):
                if not self.valid_host() or not self.allowed_origin():
                    self.reply(403, {"error": "来源不允许"})
                    return False
                if not hmac.compare_digest(self.headers.get("X-Bili-Token", ""), bridge.manager.settings.values["token"]):
                    self.reply(401, {"error": "连接码不正确，请在桌面程序设置页复制连接码。"})
                    return False
                return True

            def do_OPTIONS(self):
                self.reply(200 if self.allowed_origin() and self.valid_host() else 403, {})

            def do_GET(self):
                if self.path == "/health" and self.valid_host() and self.allowed_origin():
                    self.reply(200, {"app": "bili-local-downloader", "version": "1.2.0"})
                elif self.authenticate():
                    if self.path == "/status":
                        self.reply(200, {"version": "1.2.0", "directory": bridge.manager.settings.values["directory"], "tasks": bridge.manager.snapshot()[-100:]})
                    elif urlparse(self.path).path == "/directory-result":
                        request_id = parse_qs(urlparse(self.path).query).get("id", [""])[0]
                        request = bridge.directory_results.get(request_id)
                        if request is None:
                            self.reply(404, {"error": "目录选择请求不存在或已结束"})
                        elif request["event"].is_set():
                            self.reply(200, {"pending": False, "directory": request["directory"]})
                            bridge.directory_results.pop(request_id, None)
                        else:
                            self.reply(200, {"pending": True})
                    else:
                        self.reply(404, {"error": "没有这个接口"})

            def do_POST(self):
                if not self.authenticate():
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 1024 * 1024:
                        raise ValueError("请求大小不正确")
                    self.connection.settimeout(10)
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise ValueError("请求格式不正确")
                    if self.path == "/tasks":
                        cookies = checked_cookies(body.get("cookies") or {})
                        ids = bridge.manager.add(body.get("tasks") or [], cookies)
                        self.reply(200, {"added": len(ids), "ids": ids})
                    elif self.path == "/login":
                        cookies = checked_cookies(body.get("cookies") or {})
                        if not cookies.get("SESSDATA"):
                            raise ValueError("没有获取到B站登录信息，请先在Edge中登录B站。")
                        try:
                            account = account_info(cookies)
                        except Exception:
                            # 不返回网络库异常，它们可能包含登录请求头。
                            raise ValueError("B站登录验证失败，请确认Edge已登录、网络正常，然后重新同步。") from None
                        bridge.manager.sync_login(cookies, account)
                        self.reply(200, {"synced": True, "name": account["name"], "vip": account["vip"], "persisted": False})
                    elif self.path == "/show-window":
                        try:
                            bridge.manager.window_requests.put_nowait(True)
                        except queue.Full:
                            pass
                        self.reply(200, {"shown": True})
                    elif self.path == "/choose-directory":
                        request = {"event": threading.Event(), "directory": None}
                        request_id = uuid.uuid4().hex
                        # Bound outstanding requests if an extension is closed mid-selection.
                        if len(bridge.directory_results) >= 20:
                            raise ValueError("还有目录选择窗口未处理")
                        bridge.directory_results[request_id] = request
                        bridge.manager.directory_requests.put(request)
                        self.reply(200, {"id": request_id})
                    else:
                        self.reply(404, {"error": "没有这个接口"})
                except (ValueError, KeyError, TypeError, OSError, DownloadError) as exc:
                    self.reply(400, {"error": str(exc)[:300]})
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            self.port = self.server.server_port
            self.server.daemon_threads = True
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="edge-bridge")
            self.thread.start()
        except OSError:
            self.server = None
            self.error = f"端口{port}已被占用。桌面下载仍可使用；请先关闭其他下载器，再重新打开。"

    def shutdown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
