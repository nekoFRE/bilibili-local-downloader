"""Edge的轻量唤醒助手：只启动同目录的下载器，不接收Cookie或任意命令。"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

HOST_NAME = "com.bili_local.downloader"
HELPER_NAME = "browser-bridge.exe"
APP_NAME = "B站视频下载器.exe"
MANIFEST_NAME = "native-host.json"
ORIGIN_PATTERN = r"chrome-extension://[a-p]{32}/"


def register_host(root, extension_id, origin=None):
    """由已验证连接码的本机接口调用，只注册当前Windows用户。"""
    if not isinstance(extension_id, str) or not re.fullmatch(r"[a-p]{32}", extension_id):
        raise ValueError("扩展ID格式不正确")
    allowed = f"chrome-extension://{extension_id}/"
    if origin and origin.rstrip("/") != allowed.rstrip("/"):
        raise ValueError("扩展来源与ID不一致")
    root = Path(root).resolve()
    helper = root / HELPER_NAME
    if not helper.is_file() or not (root / APP_NAME).is_file():
        raise ValueError("自动唤醒需要新版完整发行版，请从发行版打开下载器后重新保存连接码。")
    manifest_path = root / "data" / MANIFEST_NAME
    origins = {allowed}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            origins.update(item for item in existing.get("allowed_origins", [])
                           if isinstance(item, str) and re.fullmatch(ORIGIN_PATTERN, item))
        except (ValueError, OSError, AttributeError, TypeError):
            pass
    if len(origins) > 32:
        raise ValueError("已配置过多扩展，请清理data/native-host.json后重新配置。")
    manifest = {"name": HOST_NAME, "description": "B站本地下载器唤醒助手",
                "path": str(helper), "type": "stdio", "allowed_origins": sorted(origins)}
    try:
        import winreg
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, manifest_path)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST_NAME}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
    except OSError:
        raise ValueError("无法注册Edge唤醒助手，请确认发行版目录可写后重试。") from None
    return {"configured": True}


def wake(root, origin, message):
    root = Path(root).resolve()
    if not re.fullmatch(ORIGIN_PATTERN, origin):
        raise ValueError("不允许的扩展来源")
    manifest = json.loads((root / "data" / MANIFEST_NAME).read_text(encoding="utf-8"))
    if origin not in manifest.get("allowed_origins", []):
        raise ValueError("请先在扩展设置中保存连接码，配置自动唤醒。")
    if message != {"action": "wake"}:
        raise ValueError("不支持的操作")
    executable = root / APP_NAME
    if not executable.is_file():
        raise ValueError("找不到下载器，请保留完整发行版文件夹。")
    subprocess.Popen([str(executable), "--background"], cwd=str(root),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                     close_fds=True)
    return {"ok": True}


def read_exact(stream, size):
    value = bytearray()
    while len(value) < size:
        part = stream.read(size - len(value))
        if not part:
            raise ValueError("唤醒请求不完整")
        value.extend(part)
    return bytes(value)


def main():
    import msvcrt
    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    try:
        size = struct.unpack("<I", read_exact(sys.stdin.buffer, 4))[0]
        if not 0 < size <= 8192:
            raise ValueError("唤醒请求大小不正确")
        message = json.loads(read_exact(sys.stdin.buffer, size))
        root = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
        result = wake(root, sys.argv[1] if len(sys.argv) > 1 else "", message)
    except ValueError as error:
        result = {"ok": False, "error": str(error)[:160]}
    except (OSError, TypeError, AttributeError):
        result = {"ok": False, "error": "唤醒失败，请打开完整发行版并在扩展设置中重新保存连接码。"}
    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(payload)) + payload)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
