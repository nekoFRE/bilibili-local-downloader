const $ = id => document.getElementById(id);
async function load() {
  const config = await chrome.storage.local.get({ token: "", directory: "", askSave: true, syncLogin: true, autoWake: true, extensionEnabled: true, floatingEnabled: true });
  $("token").value = config.token;
  $("ask-save").checked = config.askSave;
  $("sync-login").checked = config.syncLogin;
  $("auto-wake").checked = config.autoWake;
  $("extension-enabled").checked = config.extensionEnabled;
  $("floating-enabled").checked = config.floatingEnabled;
}
$("extension-enabled").addEventListener("change", async () => {
  await chrome.storage.local.set({ extensionEnabled: $("extension-enabled").checked });
  $("status").textContent = $("extension-enabled").checked ? "插件已启用。" : "插件已停用；需要下载时可重新打开此开关。";
  $("status").className = "";
});
$("floating-enabled").addEventListener("change", async () => {
  await chrome.storage.local.set({ floatingEnabled: $("floating-enabled").checked });
  $("status").textContent = $("floating-enabled").checked ? "视频页悬浮窗已启用。" : "视频页悬浮窗已停用。";
  $("status").className = "";
});
$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({ token: $("token").value.trim(), askSave: $("ask-save").checked, autoWake: $("auto-wake").checked });
  $("save").disabled = true;
  $("status").textContent = "正在连接桌面并配置自动唤醒…首次配置请先手动打开新版发行版。";
  $("status").className = "";
  try {
    const response = await chrome.runtime.sendMessage({ action: "connect" });
    if (!response?.ok) throw new Error(response?.error || "连接失败");
    $("status").textContent = "连接成功。" + response.data.wakeMessage + " " + response.data.loginMessage + " 桌面保存目录：" + response.data.directory;
    $("status").className = response.data.loginWarning || response.data.wakeWarning ? "error" : "";
  } catch (error) { $("status").textContent = error.message; $("status").className = "error"; }
  finally { $("save").disabled = false; }
});
$("sync-login").addEventListener("change", () => chrome.storage.local.set({ syncLogin: $("sync-login").checked }));
$("auto-wake").addEventListener("change", async () => {
  await chrome.storage.local.set({ autoWake: $("auto-wake").checked });
  $("status").textContent = $("auto-wake").checked ? "自动唤醒已开启；首次使用请点击“保存并检查连接”完成配置。" : "自动唤醒已关闭，仍可点击“打开桌面端”手动唤醒。";
  $("status").className = "";
});
$("sync-now").addEventListener("click", async () => {
  $("sync-now").disabled = true;
  try {
    const response = await chrome.runtime.sendMessage({ action: "syncLogin" });
    if (!response?.ok) throw new Error(response?.error || "同步失败");
    $("status").textContent = "桌面已登录：" + response.data.name + "（本次运行有效，未保存Cookie）。";
    $("status").className = "";
  } catch (error) { $("status").textContent = error.message; $("status").className = "error"; }
  finally { $("sync-now").disabled = false; }
});
$("ask-save").addEventListener("change", () => chrome.storage.local.set({ askSave: $("ask-save").checked }));
$("reset-dir").addEventListener("click", async () => {
  $("reset-dir").disabled = true;
  try {
    const response = await chrome.runtime.sendMessage({ action: "status" });
    if (!response?.ok) throw new Error(response?.error || "同步失败");
    $("status").textContent = "已同步桌面目录，后续新任务使用：" + response.data.directory;
    $("status").className = "";
  } catch (error) { $("status").textContent = error.message; $("status").className = "error"; }
  finally { $("reset-dir").disabled = false; }
});
load();
