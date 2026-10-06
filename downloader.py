"""B站解析和下载核心。界面、命令行输入和浏览器代码都不放在这里。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
QUALITY_NAMES = {127: "8K", 126: "杜比视界", 125: "HDR", 120: "4K", 116: "1080P 60帧", 112: "1080P 高码率", 80: "1080P", 74: "720P 60帧", 64: "720P", 32: "480P", 16: "360P"}


class DownloadError(Exception):
    pass


class Cancelled(DownloadError):
    pass


def safe_name(text, limit=100):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(text)).strip(" .")
    if not text:
        text = "未命名视频"
    if text.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        text = "_" + text
    return text[:limit].rstrip(" .")


def parse_cookies(content):
    content = content.strip()
    if not content:
        return {}
    if content.startswith(("{", "[")):
        value = json.loads(content)
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
        return {str(x["name"]): str(x["value"]) for x in value if isinstance(x, dict) and "name" in x and "value" in x and (not x.get("domain") or x["domain"].lstrip(".") == "bilibili.com" or x["domain"].endswith(".bilibili.com"))}
    result = {}
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue
        if "\t" in line:
            fields = line.split("\t")
            if len(fields) >= 7 and fields[0].lstrip(".") in ("bilibili.com", "www.bilibili.com", "api.bilibili.com"):
                result[fields[5]] = fields[6]
        else:
            for item in line.split(";"):
                if "=" in item:
                    name, value = item.split("=", 1)
                    if name.strip():
                        result[name.strip()] = value.strip()
    return result


def session_for(cookies=None):
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"})
    for key, value in (cookies or {}).items():
        session.cookies.set(str(key), str(value), domain=".bilibili.com", path="/")
    return session


def normalize_input(text):
    text = str(text).strip()
    match = re.search(r"BV[0-9A-Za-z]{10}(?![0-9A-Za-z])", text, re.I)
    if match:
        p_match = re.search(r"[?&]p=(\d+)", text)
        page = max(1, int(p_match.group(1))) if p_match else 1
        bv = "BV" + match.group(0)[2:]
        return bv, page, f"https://www.bilibili.com/video/{bv}?p={page}"
    short = re.search(r"https?://b23\.tv/[A-Za-z0-9]+", text)
    if short:
        with session_for() as session:
            response = session.get(short.group(0), timeout=(8, 25))
            response.raise_for_status()
            final = response.url
        if urlparse(final).hostname not in ("www.bilibili.com", "bilibili.com", "m.bilibili.com"):
            raise DownloadError("这个短链接没有指向B站视频。")
        return normalize_input(final)
    raise DownloadError("请输入B站视频链接、BV号，或b23.tv短链接。")


def api_get(session, path, params=None):
    response = session.get("https://api.bilibili.com" + path, params=params, timeout=(8, 30))
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError:
        raise DownloadError("B站返回了非预期响应，请稍后重试。")
    code = data.get("code", -1)
    if code != 0:
        messages = {-101: "登录已失效，请重新导入Cookie。", -404: "视频不存在或已失效。", -403: "当前账号没有访问权限。", -412: "请求暂时受到限制，请稍后重试。", -62002: "视频暂时无法播放。"}
        raise DownloadError(messages.get(code, f"B站请求失败：{data.get('message', code)}"))
    return data.get("data") or {}


def video_metadata(text, cookies=None):
    bv, page, url = normalize_input(text)
    with session_for(cookies) as session:
        data = api_get(session, "/x/web-interface/view", {"bvid": bv})
    pages = data.get("pages") or []
    if page > len(pages) or not pages:
        raise DownloadError("找不到所选择的分P。")
    return {
        "bvid": data.get("bvid", bv), "title": data.get("title", bv),
        "author": data.get("owner", {}).get("name", ""), "cover": data.get("pic", ""),
        "duration": data.get("duration", 0), "page": page, "url": url,
        "pages": [{"page": int(p["page"]), "cid": int(p["cid"]), "part": p.get("part", ""), "duration": p.get("duration", 0)} for p in pages],
    }


def embedded_json(html, name):
    match = re.search(re.escape(name) + r"\s*=\s*", html)
    if match:
        try:
            return json.JSONDecoder().raw_decode(html[match.end():].lstrip())[0]
        except ValueError:
            pass
    return None


def play_data(meta, page, cookies=None, quality=0):
    selected = next((p for p in meta["pages"] if p["page"] == page), None)
    if selected is None:
        raise DownloadError("分P编号无效。")
    with session_for(cookies) as session:
        error = None
        for qn in dict.fromkeys([int(quality) or 127, 112, 80, 64, 32, 16]):
            try:
                data = api_get(session, "/x/player/playurl", {"bvid": meta["bvid"], "cid": selected["cid"], "qn": qn, "fnval": 4048, "fourk": 1})
                if data.get("dash", {}).get("video") or data.get("durl"):
                    return data
            except (DownloadError, requests.RequestException) as exc:
                error = exc
        response = session.get(f'https://www.bilibili.com/video/{meta["bvid"]}?p={page}', timeout=(8, 30))
        response.raise_for_status()
        info = embedded_json(response.text, "window.__playinfo__")
        data = (info or {}).get("data") or {}
        if data.get("dash", {}).get("video") or data.get("durl"):
            return data
    raise DownloadError(f"无法取得播放信息，请检查登录状态和视频权限。{error or ''}")


def available_qualities(data):
    ids = {int(v.get("id", 0)) for v in data.get("dash", {}).get("video", [])}
    if not ids:
        ids = {int(data.get("quality", 0))}
    descriptions = dict(zip(data.get("accept_quality", []), data.get("accept_description", [])))
    return [{"id": q, "name": descriptions.get(q) or QUALITY_NAMES.get(q, str(q))} for q in sorted(ids, reverse=True) if q]


def inspect_video(text, cookies=None):
    meta = video_metadata(text, cookies)
    meta["qualities"] = available_qualities(play_data(meta, meta["page"], cookies))
    return meta


def account_info(cookies):
    with session_for(cookies) as session:
        data = api_get(session, "/x/web-interface/nav")
    if not data.get("isLogin"):
        raise DownloadError("当前Cookie未登录或已失效。")
    return {"name": data.get("uname", ""), "mid": data.get("mid"), "vip": bool(data.get("vipStatus"))}


def favorite_folders(cookies):
    account = account_info(cookies)
    with session_for(cookies) as session:
        data = api_get(session, "/x/v3/fav/folder/created/list-all", {"up_mid": account["mid"]})
    return data.get("list") or []


def favorite_videos(folder_id, cookies):
    result, page = [], 1
    with session_for(cookies) as session:
        while True:
            data = api_get(session, "/x/v3/fav/resource/list", {"media_id": int(folder_id), "pn": page, "ps": 20})
            medias = data.get("medias") or []
            for item in medias:
                if item.get("bvid"):
                    result.append({"bvid": item["bvid"], "title": item.get("title", ""), "author": item.get("upper", {}).get("name", ""), "duration": item.get("duration", 0)})
            if not data.get("has_more") or not medias:
                break
            page += 1
    return result


def ffmpeg_path():
    for path in [APP_ROOT / "tools" / "ffmpeg.exe", RESOURCE_ROOT / "tools" / "ffmpeg.exe"]:
        if path.is_file():
            return str(path)
    return shutil.which("ffmpeg")


def ensure_running(cancel):
    if cancel.is_set():
        raise Cancelled("已取消")


def transfer(urls, target, progress, cancel):
    last = None
    for url in dict.fromkeys(urls):
        ensure_running(cancel)
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (host.endswith(".bilivideo.com") or host.endswith(".bilivideo.cn") or host.endswith(".bilibili.com") or host.endswith(".akamaized.net")):
            last = DownloadError("播放地址的来源不受支持。")
            continue
        try:
            with session_for() as session, session.get(url, stream=True, timeout=(8, 30)) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length") or 0)
                downloaded, started, last_tick = 0, time.monotonic(), 0.0
                with open(target, "wb") as file:
                    for chunk in response.iter_content(256 * 1024):
                        ensure_running(cancel)
                        if chunk:
                            file.write(chunk)
                            downloaded += len(chunk)
                            now = time.monotonic()
                            if now - last_tick >= .2:
                                progress(downloaded, total, downloaded / max(.01, now - started))
                                last_tick = now
                if not downloaded or (total and downloaded != total):
                    raise DownloadError("文件传输不完整，请重试。")
                progress(downloaded, total or downloaded, downloaded / max(.01, time.monotonic() - started))
                return
        except Cancelled:
            raise
        except (requests.RequestException, OSError, DownloadError) as exc:
            last = exc
    raise DownloadError(f"文件下载失败：{type(last).__name__}。请检查网络后重试。")


def stream_urls(stream):
    return [x.replace("http://", "https://", 1) for x in [stream.get("baseUrl") or stream.get("base_url") or stream.get("url"), *(stream.get("backupUrl") or stream.get("backup_url") or [])] if x]


def run_ffmpeg(args, cancel):
    executable = ffmpeg_path()
    if not executable:
        raise DownloadError("未找到FFmpeg，请在设置中查看运行状态。")
    process = subprocess.Popen([executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    while process.poll() is None:
        if cancel.wait(.1):
            process.kill()
            process.communicate()
            raise Cancelled("已取消")
    _, stderr = process.communicate()
    if process.returncode:
        raise DownloadError("合并失败：" + stderr.decode("utf-8", errors="replace")[-700:])


def download_video(spec, cookies, notify, cancel):
    ensure_running(cancel)
    if not ffmpeg_path():
        raise DownloadError("未找到FFmpeg。请把FFmpeg放进tools目录后重试。")
    notify("解析中", 0, "获取最新播放地址")
    meta = video_metadata(spec["url"], cookies)
    page = int(spec.get("page") or meta["page"])
    part = next((p for p in meta["pages"] if p["page"] == page), None)
    if not part:
        raise DownloadError("选择的分P不存在。")
    data = play_data(meta, page, cookies, spec.get("quality", 0))
    destination = Path(spec["directory"]).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    multi = len(meta["pages"]) > 1
    folder = destination / safe_name(meta["title"], 70) if multi else destination
    folder.mkdir(parents=True, exist_ok=True)
    name = f'{safe_name(meta["title"], 65)} [{meta["bvid"]}]'
    if multi:
        name += f' P{page:02d} {safe_name(part["part"], 45)}'
    output = folder / (name + ".mp4")
    # Preserve existing files, including files from other versions or different qualities.
    suffix = 2
    while output.exists():
        output = folder / f"{name} ({suffix}).mp4"
        suffix += 1
    temporary = Path(tempfile.mkdtemp(prefix=".bili-", dir=folder))
    actual_quality = 0
    try:
        videos = data.get("dash", {}).get("video") or []
        if videos:
            desired = int(spec.get("quality") or 0)
            ids = {int(v.get("id", 0)) for v in videos}
            selected_id = desired if desired in ids else max((q for q in ids if not desired or q <= desired), default=min(ids))
            candidates = [v for v in videos if int(v.get("id", 0)) == selected_id]
            avc = [v for v in candidates if int(v.get("codecid", 0)) == 7 or str(v.get("codecs", "")).startswith("avc")]
            video = max(avc or candidates, key=lambda v: v.get("bandwidth", 0))
            audios = data.get("dash", {}).get("audio") or []
            if not audios:
                raise DownloadError("这个视频没有可用的音频轨道。")
            audio = max(audios, key=lambda a: a.get("bandwidth", 0))
            actual_quality = selected_id
            def update_video(done, total, speed):
                notify("下载视频", 5 + (done / total * 75 if total else 0), f"{format_bytes(speed)}/s · {format_bytes(done)} / {format_bytes(total)}")
            def update_audio(done, total, speed):
                notify("下载音频", 80 + (done / total * 15 if total else 0), f"{format_bytes(speed)}/s · {format_bytes(done)} / {format_bytes(total)}")
            transfer(stream_urls(video), temporary / "video.m4s", update_video, cancel)
            transfer(stream_urls(audio), temporary / "audio.m4s", update_audio, cancel)
            notify("合并中", 96, "生成MP4")
            run_ffmpeg(["-i", str(temporary / "video.m4s"), "-i", str(temporary / "audio.m4s"), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", str(temporary / "result.mp4")], cancel)
        else:
            segments = data.get("durl") or []
            if not segments:
                raise DownloadError("没有可下载的视频轨道。")
            for i, segment in enumerate(segments):
                transfer(stream_urls(segment), temporary / f"segment{i}.flv", lambda done, total, speed, i=i: notify("下载视频", 5 + (i + (done / total if total else 0)) / len(segments) * 90, f"{format_bytes(speed)}/s"), cancel)
            notify("合并中", 96, "封装为MP4")
            if len(segments) == 1:
                args = ["-i", str(temporary / "segment0.flv")]
            else:
                (temporary / "concat.txt").write_text("".join(f"file 'segment{i}.flv'\n" for i in range(len(segments))), encoding="utf-8")
                args = ["-f", "concat", "-safe", "0", "-i", str(temporary / "concat.txt")]
            run_ffmpeg([*args, "-c", "copy", "-movflags", "+faststart", str(temporary / "result.mp4")], cancel)
            actual_quality = int(data.get("quality", 0))
        ensure_running(cancel)
        result = temporary / "result.mp4"
        if not result.is_file() or result.stat().st_size == 0:
            raise DownloadError("没有生成完整的MP4文件。")
        result.replace(output)
        return {"path": str(output), "quality": actual_quality, "cid": part["cid"], "title": meta["title"], "bvid": meta["bvid"]}
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def format_bytes(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024


def merge_local_files(video, audio, output, cancel):
    video, audio, output = Path(video), Path(audio), Path(output)
    if not video.is_file() or not audio.is_file():
        raise DownloadError("请选择存在的视频和音频文件。")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".bili-merge-", dir=output.parent))
    try:
        result = temporary / "result.mp4"
        run_ffmpeg(["-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", str(result)], cancel)
        ensure_running(cancel)
        result.replace(output)
        return str(output)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
