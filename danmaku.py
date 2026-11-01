"""弹幕配置、分段 protobuf 读取和 ASS 排布；不访问浏览器或保存登录数据。"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata


DEFAULTS = {
    "enabled": False, "source": "web", "font": 25, "opacity": 70,
    "area": 50, "duration": 8, "scroll": True, "top": True, "bottom": True,
    "reserve": True, "density": 80, "keywords": "", "save_ass": False,
    "keep_original": False,
}
WEB_FIELDS = ("font", "opacity", "area", "duration", "scroll", "top", "bottom", "reserve")


def normalize_profile(value=None):
    if value is None:
        return dict(DEFAULTS)
    if not isinstance(value, dict):
        raise ValueError("弹幕设置格式不正确。")
    result = dict(DEFAULTS)
    for key in ("enabled", "scroll", "top", "bottom", "reserve", "save_ass", "keep_original"):
        if key in value:
            if not isinstance(value[key], bool):
                raise ValueError("弹幕开关格式不正确。")
            result[key] = value[key]
    for key, low, high in (("font", 16, 48), ("opacity", 10, 100), ("duration", 4, 20), ("density", 10, 200)):
        raw = value.get(key, result[key])
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            raise ValueError("弹幕数值格式不正确。")
        result[key] = max(low, min(high, round(raw)))
    area = value.get("area", result["area"])
    if isinstance(area, bool) or area not in (25, 50, 75, 100):
        raise ValueError("弹幕显示区域无效。")
    result["area"] = int(area)
    result["source"] = "custom" if value.get("source") == "custom" else "web"
    keywords = value.get("keywords", "")
    if not isinstance(keywords, str) or len(keywords) > 2000:
        raise ValueError("屏蔽关键词请控制在2000字以内。")
    result["keywords"] = keywords.strip()
    return result


def normalize_reference(value):
    if not isinstance(value, dict) or not isinstance(value.get("values"), dict):
        return None
    fields = [key for key in WEB_FIELDS if key in value.get("fields", []) and key in value["values"]]
    if not fields:
        return None
    values = normalize_profile({key: value["values"][key] for key in fields})
    return {"values": {key: values[key] for key in fields}, "fields": fields}


def resolve_profile(profile, reference=None):
    result = normalize_profile(profile)
    reference = normalize_reference(reference)
    if result["source"] == "web":
        for key in WEB_FIELDS:
            result[key] = reference["values"][key] if reference and key in reference["fields"] else DEFAULTS[key]
    return result


def profile_identity(profile):
    profile = normalize_profile(profile)
    if not profile["enabled"]:
        return ""
    payload = {key: value for key, value in profile.items() if key != "source"}
    return "|dm:" + hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()[:20]


def protobuf_fields(data):
    """只读 protobuf wire 格式，跳过未知字段；拒绝截断和超长 varint。"""
    offset = 0

    def varint():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(data):
                raise ValueError("弹幕数据不完整。")
            byte = data[offset]
            offset += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
        raise ValueError("弹幕数据格式异常。")

    while offset < len(data):
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("弹幕数据格式异常。")
        if wire == 0:
            yield number, varint()
        elif wire in (1, 2, 5):
            size = varint() if wire == 2 else 8 if wire == 1 else 4
            if size > len(data) - offset:
                raise ValueError("弹幕数据不完整。")
            value = data[offset:offset + size]
            offset += size
            yield number, value
        else:
            raise ValueError("不支持的弹幕数据格式。")


def parse_segment(data):
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("单个弹幕分包过大。")
    result = []
    for number, value in protobuf_fields(data):
        if number != 1 or not isinstance(value, bytes):
            continue
        fields = dict(protobuf_fields(value))
        if any(not isinstance(fields.get(key, 0), int) for key in (1, 2, 3, 5)):
            raise ValueError("弹幕字段类型异常。")
        text = fields.get(7, b"")
        if not isinstance(text, bytes):
            continue
        result.append({
            "id": fields.get(1, 0), "time": fields.get(2, 0) / 1000,
            "mode": fields.get(3, 0), "color": fields.get(5, 0),
            "text": text.decode("utf-8", errors="replace"),
        })
    return result


def fetch_comments(session, cid, seconds, notify, check_cancel):
    count = max(1, math.ceil(float(seconds or 0) / 360))
    result, seen = [], set()
    for index in range(1, count + 1):
        check_cancel()
        notify("读取弹幕", 96, f"分包 {index} / {count}")
        response = session.get("https://api.bilibili.com/x/v2/dm/web/seg.so", params={"type": 1, "oid": int(cid), "segment_index": index}, timeout=(8, 30))
        response.raise_for_status()
        payload = response.content
        if payload.lstrip().startswith((b"{", b"<")):
            raise ValueError("弹幕接口暂时不可用，请检查登录、权限或稍后重试。")
        for item in parse_segment(payload):
            key = item["id"] or (item["time"], item["mode"], item["text"])
            if key not in seen:
                seen.add(key)
                result.append(item)
        if len(result) > 500000:
            raise ValueError("弹幕数量过大，请换一个较短的分P。")
    return sorted(result, key=lambda item: item["time"])


def ass_time(seconds):
    value = max(0, round(seconds * 100))
    return f"{value // 360000}:{value // 6000 % 60:02d}:{value // 100 % 60:02d}.{value % 100:02d}"


def ass_text(text):
    # 弹幕正文不能注入 ASS 样式、移动指令或换行。
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(text)).replace("\\", "＼").replace("{", "｛").replace("}", "｝").strip()[:150]


def write_ass(path, comments, profile, check_cancel=lambda: None):
    profile = normalize_profile(profile)
    width, height = 1920, 1080
    size = profile["font"] * 2
    line = math.ceil(size * 1.3)
    bottom = min(height * profile["area"] / 100, height * (.85 if profile["reserve"] else 1)) - 16
    rows = max(1, int((bottom - 16) / line))
    fixed_rows = max(1, rows // 4) if profile["scroll"] else max(1, rows // 2)
    top_rows = min(fixed_rows, rows) if profile["top"] else 0
    bottom_rows = min(fixed_rows, max(0, rows - top_rows)) if profile["bottom"] else 0
    scroll_rows = max(0, rows - top_rows - bottom_rows) if profile["scroll"] else 0
    scrolling = [None] * scroll_rows
    top = [0.0] * top_rows
    fixed_bottom = [0.0] * bottom_rows
    alpha = round(255 * (1 - profile["opacity"] / 100))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,{size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,-1,0,0,0,100,100,0,0,1,1.5,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    words = [word.casefold() for word in re.split(r"[\n,，]+", profile["keywords"]) if word.strip()]
    events, buckets = [], {}
    for index, item in enumerate(comments):
        if index % 256 == 0:
            check_cancel()
        text = ass_text(item.get("text", ""))
        start = item.get("time", 0)
        if not isinstance(start, (int, float)) or not math.isfinite(start) or start < 0:
            continue
        mode = item.get("mode", 1)
        if not text or any(word.strip() in text.casefold() for word in words):
            continue
        bucket = int(start // 10)
        if buckets.get(bucket, 0) >= profile["density"]:
            continue
        duration = profile["duration"] if mode in (1, 2, 3, 6) else min(5, profile["duration"])
        position = None
        if mode in (1, 2, 3, 6) and profile["scroll"]:
            text_width = sum(1 if unicodedata.east_asian_width(c) in "WF" else .58 for c in text) * size + 18
            speed = (width + text_width) / duration
            for lane, previous in enumerate(scrolling):
                free = previous is None or start >= previous["time"] + previous["duration"]
                if not free and previous["mode"] == mode:
                    elapsed = start - previous["time"]
                    old_speed = (width + previous["width"]) / previous["duration"]
                    free = old_speed * elapsed >= previous["width"] + 18 and speed * (previous["duration"] - elapsed) <= width - 18
                if free:
                    y = 16 + (top_rows + lane) * line
                    x1, x2 = (-text_width, width) if mode == 6 else (width, -text_width)
                    position = f"\\an7\\move({round(x1)},{y},{round(x2)},{y})"
                    scrolling[lane] = {"time": start, "width": text_width, "duration": duration, "mode": mode}
                    break
        elif mode in (4, 5) and profile["bottom" if mode == 4 else "top"]:
            lanes = fixed_bottom if mode == 4 else top
            for lane, until in enumerate(lanes):
                if start >= until:
                    y = round(bottom - lane * line) if mode == 4 else 16 + lane * line
                    position = f"\\an{2 if mode == 4 else 8}\\pos({width // 2},{y})"
                    lanes[lane] = start + duration + .1
                    break
        if position is None:
            continue
        color = int(item.get("color", 0xFFFFFF)) & 0xFFFFFF
        bgr = f"{color & 255:02X}{(color >> 8) & 255:02X}{color >> 16:02X}"
        style = "{" + position + f"\\alpha&H{alpha:02X}&\\c&H{bgr}&" + "}"
        events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(start + duration)},Default,,0,0,0,,{style}{text}\n")
        buckets[bucket] = buckets.get(bucket, 0) + 1
    path.write_text(header + "".join(events), encoding="utf-8-sig")
    return {"rendered": len(events), "received": len(comments)}
