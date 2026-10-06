import { normalizeInput, safeName, chooseVideo, qualityList, mediaUrl } from "./core.js";

const LOCAL = "http://127.0.0.1:17890";
const defaults = { token: "", directory: "", mode: "desktop", askSave: true, syncLogin: true };
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function settings() {
  return chrome.storage.local.get(defaults);
}

async function localRequest(path, body, timeout = 12000) {
  const config = await settings();
  if (!config.token) throw new Error("先在扩展设置中粘贴桌面程序的连接码。");
  let response;
  try {
    response = await fetch(LOCAL + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json", "X-Bili-Token": config.token },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeout),
    });
  } catch {
    throw new Error("无法连接本机下载器，请先打开桌面程序。");
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "本机请求失败");
  return result;
}

async function biliAPI(path, params = {}) {
  const url = new URL("https://api.bilibili.com" + path);
  Object.entries(params).forEach(([key, value]) => url.searchParams.set(key, value));
  const response = await fetch(url.href, { credentials: "include", signal: AbortSignal.timeout(25000) });
  if (!response.ok) throw new Error("B站网络请求失败：" + response.status);
  const result = await response.json();
  if (result.code !== 0) throw new Error(result.code === -101 ? "请先在Edge中登录B站。" : result.message || "B站接口暂时不可用");
  return result.data || {};
}

async function normalize(text) {
  const short = String(text).match(/https?:\/\/b23\.tv\/[A-Za-z0-9]+/);
  if (short && !/BV[0-9A-Za-z]{10}/i.test(text)) {
    const response = await fetch(short[0], { signal: AbortSignal.timeout(20000) });
    return normalizeInput(response.url);
  }
  return normalizeInput(text);
}

async function metadata(text) {
  const parsed = await normalize(text);
  const data = await biliAPI("/x/web-interface/view", { bvid: parsed.bvid });
  const pages = data.pages || [];
  if (!pages.some(p => p.page === parsed.page)) throw new Error("找不到选择的分P。");
  return { ...parsed, bvid: data.bvid || parsed.bvid, title: data.title, author: data.owner?.name || "", cover: data.pic, duration: data.duration, pages };
}

async function playback(meta, page, quality = 0) {
  const part = meta.pages.find(p => p.page === page);
  if (!part) throw new Error("分P编号无效。");
  let lastError;
  for (const qn of [...new Set([Number(quality) || 127, 112, 80, 64, 32, 16])]) {
    try {
      const data = await biliAPI("/x/player/playurl", { bvid: meta.bvid, cid: part.cid, qn, fnval: 4048, fourk: 1 });
      if (data.dash?.video?.length || data.durl?.length) return data;
    } catch (error) { lastError = error; }
  }
  throw lastError || new Error("无法取得播放信息，请确认当前账号有观看权限。");
}

async function inspect(text) {
  const meta = await metadata(text);
  const data = await playback(meta, meta.page);
  return { ...meta, qualities: qualityList(data) };
}

async function cookies() {
  const all = await chrome.cookies.getAll({ domain: "bilibili.com" });
  return Object.fromEntries(all.map(cookie => [cookie.name, cookie.value]));
}

async function syncLogin() {
  const loginCookies = await cookies();
  if (!loginCookies.SESSDATA) throw new Error("当前Edge尚未登录B站。请在安装本扩展的Edge配置中登录后再同步。");
  try {
    return await localRequest("/login", { cookies: loginCookies }, 35000);
  } catch (error) {
    if (error.message.includes("没有这个接口")) throw new Error("桌面程序版本较旧，请从托盘退出并打开新版，再同步登录。");
    throw error;
  }
}

async function toggleFloating(request) {
  const activeTabs = await chrome.tabs.query({ active: true });
  const focused = await chrome.windows.getLastFocused();
  const active = activeTabs.find(tab => tab.windowId === focused.id) || activeTabs[0];
  const isVideo = tab => /^https:\/\/(?:www\.)?bilibili\.com\/video\//i.test(tab?.url || "");
  const candidates = (await chrome.tabs.query({})).filter(isVideo);
  let preferred;
  try { preferred = normalizeInput(request.url || "").bvid; } catch {}
  const matching = candidates.filter(candidate => {
    try { return normalizeInput(candidate.url).bvid === preferred; } catch { return false; }
  });
  const tab = isVideo(active) ? active : (matching.length ? matching : candidates).sort((a, b) => (b.lastAccessed || b.id) - (a.lastAccessed || a.id))[0];
  if (!tab) throw new Error("没有找到已打开的B站视频标签页。请先在这个Edge配置中打开视频页。");
  try {
    const response = await chrome.tabs.sendMessage(tab.id, { action: "toggleFloating" });
    if (!response?.ok) throw new Error("页面脚本尚未就绪");
  } catch {
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
      const response = await chrome.tabs.sendMessage(tab.id, { action: "toggleFloating" });
      if (!response?.ok) throw new Error("页面脚本未响应");
    } catch {
      throw new Error("无法在视频页加载悬浮面板。请允许扩展访问B站，刷新视频页后重试。");
    }
  }
  if (tab.id !== active?.id) {
    await chrome.tabs.update(tab.id, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
  }
  return { opened: true };
}

async function browserFile(url, filename, askSave) {
  // 规则只匹配这个下载地址，为B站媒体请求设置Referer。
  const rules = await chrome.declarativeNetRequest.getSessionRules();
  let ruleId = Math.floor(Math.random() * 1000000000) + 1;
  while (rules.some(rule => rule.id === ruleId)) ruleId++;
  await chrome.declarativeNetRequest.updateSessionRules({
    addRules: [{
      id: ruleId, priority: 1,
      action: { type: "modifyHeaders", requestHeaders: [{ header: "Referer", operation: "set", value: "https://www.bilibili.com/" }] },
      condition: { urlFilter: "|" + url + "|", resourceTypes: ["other", "media", "xmlhttprequest"] },
    }],
  });
  try {
    const id = await chrome.downloads.download({ url, filename, saveAs: askSave, conflictAction: "uniquify" });
    const key = "rule_" + id;
    await chrome.storage.session.set({ [key]: ruleId });
    return id;
  } catch (error) {
    await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: [ruleId] });
    throw error;
  }
}

chrome.downloads.onChanged.addListener(async delta => {
  if (!["complete", "interrupted"].includes(delta.state?.current)) return;
  const key = "rule_" + delta.id;
  const stored = await chrome.storage.session.get(key);
  if (stored[key]) {
    await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: [stored[key]] });
    await chrome.storage.session.remove(key);
  }
});

async function downloadBrowser(meta, pages, quality, askSave) {
  const ids = [];
  for (const page of pages) {
    const data = await playback(meta, page, quality);
    const part = meta.pages.find(p => p.page === page);
    const base = safeName(meta.title, 60) + " [" + meta.bvid + "]" + (meta.pages.length > 1 ? " P" + String(page).padStart(2, "0") + " " + safeName(part.part, 35) : "");
    if (data.dash?.video?.length) {
      const video = chooseVideo(data.dash.video, quality);
      const audio = [...(data.dash.audio || [])].sort((a, b) => (b.bandwidth || 0) - (a.bandwidth || 0))[0];
      if (!audio) throw new Error("没有可用的音频轨道。");
      ids.push(await browserFile(mediaUrl(video), "B站视频/" + base + "_video.m4s", askSave));
      ids.push(await browserFile(mediaUrl(audio), "B站视频/" + base + "_audio.m4s", askSave));
    } else {
      for (const [i, segment] of (data.durl || []).entries()) {
        ids.push(await browserFile(mediaUrl(segment), "B站视频/" + base + (data.durl.length > 1 ? "_片段" + (i + 1) : "") + ".flv", askSave));
      }
    }
  }
  return { message: "已启动 " + ids.length + " 个文件下载。音视频分离时，请使用桌面程序的本地合并工具。", ids };
}

async function favoriteItems(folderId) {
  const items = [];
  for (let page = 1; ; page++) {
    const data = await biliAPI("/x/v3/fav/resource/list", { media_id: Number(folderId), pn: page, ps: 20 });
    items.push(...(data.medias || []).filter(item => item.bvid));
    if (!data.has_more || !data.medias?.length) break;
    if (items.length >= 500) throw new Error("收藏夹超过500个视频，请用桌面版分批添加。");
  }
  return items;
}

async function handle(request, sender) {
  // 网页内容脚本只能打开扩展界面，不能读取登录信息或添加后台任务。
  if (sender.tab && !sender.url?.startsWith(chrome.runtime.getURL("")) && request.action !== "open") {
    throw new Error("请使用扩展窗口操作。");
  }
  switch (request.action) {
    case "open": {
      const parsed = normalizeInput(request.url);
      await chrome.tabs.create({ url: chrome.runtime.getURL("popup.html") + "?video=" + encodeURIComponent(parsed.url) });
      return {};
    }
    case "inspect": return inspect(request.url);
    case "status": return localRequest("/status");
    case "toggleFloating": return toggleFloating(request);
    case "syncLogin": return syncLogin();
    case "connect": {
      const result = await localRequest("/status");
      const config = await settings();
      if (config.syncLogin) {
        try {
          const account = await syncLogin();
          result.loginMessage = "已同步Edge登录：" + account.name + "（本次运行有效，未保存Cookie）。";
        } catch (error) {
          result.loginMessage = "本机连接成功，但登录未同步：" + error.message;
          result.loginWarning = true;
        }
      } else result.loginMessage = "已关闭自动同步登录；需要时可手动同步。";
      return result;
    }
    case "chooseDirectory": {
      const begin = await localRequest("/choose-directory", {});
      for (let i = 0; i < 180; i++) {
        await pause(500);
        const result = await localRequest("/directory-result?id=" + encodeURIComponent(begin.id));
        if (!result.pending) {
          if (result.directory) await chrome.storage.local.set({ directory: result.directory });
          return result;
        }
      }
      throw new Error("选择目录超时，请重试。");
    }
    case "download": {
      const meta = await metadata(request.url);
      const pages = [...new Set((request.pages || [meta.page]).map(Number))];
      if (!pages.length || pages.length > 500 || pages.some(page => !meta.pages.some(p => p.page === page))) throw new Error("请选择有效分P。");
      const config = await settings();
      if (request.mode === "browser") return downloadBrowser(meta, pages, Number(request.quality) || 0, config.askSave);
      const result = await localRequest("/tasks", {
        tasks: pages.map(page => ({ url: meta.url, title: meta.title, page, quality: Number(request.quality) || 0, directory: config.directory || undefined })),
        cookies: await cookies(),
      });
      return { message: "已加入 " + result.added + " 个本地任务。可以关闭扩展窗口，桌面程序会继续下载。" };
    }
    case "folders": {
      const account = await biliAPI("/x/web-interface/nav");
      if (!account.isLogin) throw new Error("请先在Edge中登录B站。");
      const result = await biliAPI("/x/v3/fav/folder/created/list-all", { up_mid: account.mid });
      return result.list || [];
    }
    case "favoriteItems": return favoriteItems(request.folderId);
    case "favoriteDownload": {
      const allItems = await favoriteItems(request.folderId);
      const selected = new Set((request.selectedBvids || []).map(String));
      const items = selected.size ? allItems.filter(item => selected.has(item.bvid)) : allItems;
      if (!items.length) throw new Error("收藏夹为空。");
      const config = await settings();
      const result = await localRequest("/tasks", {
        tasks: items.map(item => ({ url: item.bvid, title: item.title, page: 1, quality: 0, all_pages: !!request.allPages, directory: config.directory || undefined })),
        cookies: await cookies(),
      });
      return { message: "已加入 " + result.added + " 个收藏夹任务，详见桌面下载队列。" };
    }
    default: throw new Error("未知操作");
  }
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  handle(request, sender).then(data => sendResponse({ ok: true, data })).catch(error => sendResponse({ ok: false, error: error.message }));
  return true;
});
