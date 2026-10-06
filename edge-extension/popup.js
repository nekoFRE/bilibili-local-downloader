let meta = null;
const $ = id => document.getElementById(id);
const defaults = { token: "", directory: "", mode: "desktop", askSave: true };
const floating = new URLSearchParams(location.search).get("floating") === "1";
if (floating) { document.body.classList.add("floating"); $("floating").hidden = true; }
const status = (text, error = false) => { $("status").textContent = text; $("status").className = error ? "error" : ""; };
const duration = seconds => Math.floor((seconds || 0) / 60) + ":" + String((seconds || 0) % 60).padStart(2, "0");

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
  $("mode-note").textContent = browser
    ? "使用Edge保存对话框选择文件位置。DASH音视频会分别保存，需要本地合并。"
    : "打开桌面程序，扩展会使用Edge中的B站登录状态。";
}

$("inspect").addEventListener("click", inspect);
$("url").addEventListener("keydown", event => { if (event.key === "Enter") inspect(); });
$("all-pages").addEventListener("change", () => document.querySelectorAll('#pages input').forEach(input => { input.checked = $("all-pages").checked; }));
document.querySelectorAll('input[name="mode"]').forEach(input => input.addEventListener("change", () => { chrome.storage.local.set({ mode: mode() }); updateMode(); }));
$("download").addEventListener("click", () => busy("download", async () => {
  if (!meta) throw new Error("请先解析视频。");
  const pages = [...document.querySelectorAll('#pages input:checked')].map(input => Number(input.value));
  if (!pages.length) throw new Error("请至少选择一个分P。");
  status("正在添加下载任务…");
  const result = await send({ action: "download", url: meta.url, pages, quality: Number($("quality").value), mode: mode() });
  status(result.message);
}));
$("choose-dir").addEventListener("click", () => busy("choose-dir", async () => {
  status("请在桌面程序弹出的窗口中选择保存目录。");
  const result = await send({ action: "chooseDirectory" });
  if (result.directory) $("directory").textContent = "保存目录：" + result.directory;
  status(result.directory ? "保存目录已更新。" : "已取消选择。");
}));
$("refresh").addEventListener("click", () => busy("refresh", async () => {
  const result = await send({ action: "status" });
  const config = await chrome.storage.local.get(defaults);
  $("directory").textContent = "保存目录：" + (config.directory || result.directory);
  const active = result.tasks.filter(task => !["已完成", "已跳过", "失败", "已取消", "已中断"].includes(task.status)).length;
  status("本机已连接 · " + active + " 个进行中的任务");
}));
$("options").addEventListener("click", () => chrome.runtime.openOptionsPage());
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
$("folders").addEventListener("click", () => busy("folders", async () => {
  status("正在加载收藏夹…");
  const folders = await send({ action: "folders" });
  $("folder").replaceChildren(new Option("选择收藏夹", ""));
  folders.forEach(folder => $("folder").add(new Option(folder.title + " · " + (folder.media_count || 0) + " 个", folder.id)));
  status("已加载 " + folders.length + " 个收藏夹。");
}));
$("favorite-download").addEventListener("click", () => busy("favorite-download", async () => {
  if (!$("folder").value) throw new Error("先选择收藏夹。");
  status("正在读取收藏夹并加入本地队列…");
  const result = await send({ action: "favoriteDownload", folderId: $("folder").value, allPages: $("favorite-all-pages").checked });
  status(result.message);
}));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.directory) $("directory").textContent = "保存目录：" + (changes.directory.newValue || "桌面程序的默认目录");
});

(async () => {
  const config = await chrome.storage.local.get(defaults);
  document.querySelector('input[name="mode"][value="' + (config.mode === "browser" ? "browser" : "desktop") + '"]').checked = true;
  $("directory").textContent = "保存目录：" + (config.directory || "桌面程序的默认目录");
  updateMode();
  const preset = new URLSearchParams(location.search).get("video");
  if (preset) { $("url").value = preset; await inspect(); return; }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.url && /https:\/\/www\.bilibili\.com\/video\//.test(tab.url)) { $("url").value = tab.url; await inspect(); }
})().catch(error => status(error.message || "扩展初始化失败，请重新加载扩展后重试。", true));
if (floating) window.parent.postMessage({ type: "biliLocalFrameReady" }, "https://www.bilibili.com");
