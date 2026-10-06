// 与浏览器API无关的小函数，便于检查分P和画质选择。
export function normalizeInput(text) {
  const bv = String(text).match(/BV[0-9A-Za-z]{10}(?![0-9A-Za-z])/i);
  if (!bv) throw new Error("请输入B站视频链接或BV号。");
  const page = Math.max(1, Number(String(text).match(/[?&]p=(\d+)/)?.[1] || 1));
  const bvid = "BV" + bv[0].slice(2);
  return { bvid, page, url: "https://www.bilibili.com/video/" + bvid + "?p=" + page };
}

export function safeName(text, limit = 90) {
  let name = String(text).replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/^[ .]+|[ .]+$/g, "");
  if (!name) name = "未命名视频";
  if (/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(name)) name = "_" + name;
  return name.slice(0, limit).replace(/[ .]+$/g, "");
}

export function chooseVideo(videos, quality = 0) {
  if (!videos?.length) throw new Error("没有可下载的视频轨道。");
  const ids = [...new Set(videos.map(v => Number(v.id)))].sort((a, b) => b - a);
  const id = ids.includes(Number(quality)) ? Number(quality) : ids.find(q => !quality || q <= quality) || ids.at(-1);
  const candidates = videos.filter(v => Number(v.id) === id);
  const avc = candidates.filter(v => v.codecid === 7 || String(v.codecs || "").startsWith("avc"));
  return [...(avc.length ? avc : candidates)].sort((a, b) => (b.bandwidth || 0) - (a.bandwidth || 0))[0];
}

export function qualityList(data) {
  const names = new Map((data.accept_quality || []).map((id, i) => [id, data.accept_description?.[i]]));
  const ids = data.dash?.video?.length ? [...new Set(data.dash.video.map(v => v.id))] : [data.quality];
  return ids.filter(Boolean).sort((a, b) => b - a).map(id => ({ id, name: names.get(id) || String(id) }));
}

export function mediaUrl(stream) {
  const url = stream.baseUrl || stream.base_url || stream.url;
  const parsed = new URL(url);
  if (!["http:", "https:"].includes(parsed.protocol) || !/\.(bilivideo\.com|bilivideo\.cn|bilibili\.com)$/.test(parsed.hostname)) {
    throw new Error("播放地址的来源不受支持。");
  }
  parsed.protocol = "https:";
  return parsed.href;
}
