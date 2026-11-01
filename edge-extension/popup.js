import { DanmakuControls } from "./danmaku.js";
import { FavoriteBrowser } from "./favorites.js";

let meta = null;
let autoWake = true;
const $ = id => document.getElementById(id);
const defaults = { token: "", directory: "", mode: "desktop", askSave: true, autoWake: true, extensionEnabled: true, floatingEnabled: true };
const floating = new URLSearchParams(location.search).get("floating") === "1";
if (floating) { document.body.classList.add("floating"); $("floating").hidden = true; }
const status = (text, error = false) => { $("status").textContent = text; $("status").className = error ? "error" : ""; };
function applyFeatureState(config) {
  const enabled = config.extensionEnabled !== false;
  const floatingEnabled = config.floatingEnabled !== false;
  $("floating").disabled = !enabled || !floatingEnabled;
  $("quick-enabled").checked = enabled;
  $("quick-state").textContent = enabled ? "ON" : "OFF";
  $("quick-control").classList.toggle("off", !enabled);
  document.body.classList.toggle("extension-disabled", !enabled);
  if (!enabled) status("插件当前已停用，请打开设置重新启用。", true);
}
const duration = seconds => Math.floor((seconds || 0) / 60) + ":" + String((seconds || 0) % 60).padStart(2, "0");
const danmaku = new DanmakuControls(send, status);
danmaku.mount($("video-danmaku"), true);
danmaku.mount($("favorite-danmaku"));
const favorites = new FavoriteBrowser(send, status, danmaku);

async function send(message) {
  const response = await chrome.runtime.sendMessage(message);
  if (!response?.ok) throw new Error(response?.error || "操作未完成，请重试。");
  return response.data;
}

async function busy(id, action) {
  $(id).disabled = true;
  try { await action(); } catch (error) { status(error.message, true); }
  finally { $(id).disabled = false; }
}

async function inspect() {
  meta = null;
  $("download").disabled = true;
  await busy("inspect", async () => {
    status("正在解析视频…");
    meta = await send({ action: "inspect", url: $("url").value });
    $("title").textContent = meta.title;
    $("info").textContent = meta.author + " · " + duration(meta.duration) + " · " + meta.bvid;
    const cover = new URL(meta.cover);
    if (/\.hdslb\.com$/.test(cover.hostname)) { cover.protocol = "https:"; $("cover").src = cover.href; $("cover").hidden = false; }
    $("quality").replaceChildren(new Option("自动 · 最高可用", "0"));
    meta.qualities.forEach(item => $("quality").add(new Option(item.name, item.id)));
    $("pages").replaceChildren();
    meta.pages.forEach(page => {
      const label = document.createElement("label"); label.className = "part";
      const input = document.createElement("input"); input.type = "checkbox"; input.value = page.page; input.checked = page.page === meta.page;
      const text = document.createElement("span"); text.textContent = "P" + page.page + "  " + page.part + " · " + duration(page.duration);
      label.append(input, text); $("pages").append(label);
    });
    $("all-pages").checked = false;
    $("download").disabled = false;
    status("解析完成，选择画质和分P后开始下载。");
  });
}

function mode() { return document.querySelector('input[name="mode"]:checked').value; }
function updateMode() {
  const browser = mode() === "browser";
  $("choose-dir").disabled = browser;
  $("directory").hidden = browser;
  danmaku.setBrowserMode(browser);
  $("mode-note").textContent = browser
    ? "浏览器直下不生成弹幕版。DASH音视频会分别保存，可使用Edge保存对话框选择位置。"
    : autoWake ? "下载时自动唤醒桌面端，在后台合并MP4。首次请到设置完成连接。" : "自动唤醒已关闭。请先打开桌面端，或在设置中开启自动唤醒。";
}

$("inspect").addEventListener("click", inspect);
$("url").addEventListener("keydown", event => { if (event.key === "Enter") inspect(); });
$("all-pages").addEventListener("change", () => document.querySelectorAll('#pages input').forEach(input => { input.checked = $("all-pages").checked; }));
document.querySelectorAll('input[name="mode"]').forEach(input => input.addEventListener("change", () => { chrome.storage.local.set({ mode: mode() }); updateMode(); }));
$("download").addEventListener("click", () => busy("download", async () => {
  if (!meta) throw new Error("请先解析视频。");
  const pages = [...document.querySelectorAll('#pages input:checked')].map(input => Number(input.value));
  if (!pages.length) throw new Error("请至少选择一个分P。");
  status(mode() === "browser" ? "正在提交浏览器下载…" : "正在连接桌面端并添加任务，首次唤醒可能需要几秒…");
  const result = await send({ action: "download", url: meta.url, pages, quality: Number($("quality").value), mode: mode(), danmaku: await danmaku.snapshot(mode() === "browser") });
  status(result.message);
}));
$("choose-dir").addEventListener("click", () => busy("choose-dir", async () => {
  status("请在桌面程序弹出的窗口中选择保存目录。");
  const result = await send({ action: "chooseDirectory" });
  status(result.directory ? "保存目录已更新。" : "已取消选择。");
}));
$("refresh").addEventListener("click", () => busy("refresh", async () => {
  const result = await send({ action: "status" });
  const active = result.tasks.filter(task => !["已完成", "已跳过", "失败", "已取消", "已中断"].includes(task.status)).length;
  status("本机已连接 · " + active + " 个进行中的任务");
}));
$("cancel-all-downloads").addEventListener("click", () => busy("cancel-all-downloads", async () => {
  status("正在取消本工具的未完成下载…");
  const result = await send({ action: "cancelAllDownloads", mode: mode() });
  status(result.message, result.warning);
}));
$("options").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("open-desktop").addEventListener("click", () => busy("open-desktop", async () => {
  status("正在唤醒并打开桌面端，请稍候…");
  await send({ action: "wakeDesktop" });
  status("桌面窗口已打开，可查看下载队列。");
}));
$("quick-enabled").addEventListener("change", async () => {
  const enabled = $("quick-enabled").checked;
  await chrome.storage.local.set({ extensionEnabled: enabled });
  status(enabled ? "插件已启用。" : "插件已停用。", !enabled);
});
$("floating").addEventListener("click", () => busy("floating", async () => {
  await send({ action: "toggleFloating", url: meta?.url || $("url").value });
  status("已在B站视频标签页打开悬浮面板。");
  if (!new URLSearchParams(location.search).has("video")) window.close();
}));
$("sync-login").addEventListener("click", () => busy("sync-login", async () => {
  status("正在同步Edge登录到桌面…");
  const account = await send({ action: "syncLogin" });
  status("桌面已登录：" + account.name + "（本次运行有效，未保存Cookie）。");
}));
$("expand").addEventListener("click", () => chrome.tabs.create({ url: chrome.runtime.getURL("popup.html") + ($("url").value ? "?video=" + encodeURIComponent($("url").value) : "") }));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.autoWake) { autoWake = changes.autoWake.newValue !== false; updateMode(); }
  if (area === "local" && (changes.extensionEnabled || changes.floatingEnabled)) chrome.storage.local.get(defaults).then(applyFeatureState);
});

(async () => {
  const config = await chrome.storage.local.get(defaults);
  autoWake = config.autoWake !== false;
  applyFeatureState(config);
  document.querySelector('input[name="mode"][value="' + (config.mode === "browser" ? "browser" : "desktop") + '"]').checked = true;
  updateMode();
  await favorites.init();
  await danmaku.init();
  if (config.extensionEnabled === false) return;
  const preset = new URLSearchParams(location.search).get("video");
  if (preset) { $("url").value = preset; await inspect(); return; }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.url && /https:\/\/(?:www\.)?bilibili\.com\/(?:video\/|(?:list|medialist\/play)\/).*BV[0-9A-Za-z]{10}/i.test(tab.url)) { $("url").value = tab.url; await inspect(); }
})().catch(error => status(error.message || "扩展初始化失败，请重新加载扩展后重试。", true));
if (floating) window.parent.postMessage({ type: "biliLocalFrameReady" }, "https://www.bilibili.com");
