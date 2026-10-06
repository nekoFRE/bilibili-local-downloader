const $ = id => document.getElementById(id);
async function load() {
  const config = await chrome.storage.local.get({ token: "", directory: "", askSave: true });
  $("token").value = config.token;
  $("ask-save").checked = config.askSave;
  $("directory").textContent = "保存目录：" + (config.directory || "跟随桌面程序");
}
$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({ token: $("token").value.trim(), askSave: $("ask-save").checked });
  try {
    const response = await chrome.runtime.sendMessage({ action: "status" });
    if (!response?.ok) throw new Error(response?.error || "连接失败");
    $("status").textContent = "连接成功。桌面保存目录：" + response.data.directory;
    $("status").className = "";
  } catch (error) { $("status").textContent = error.message; $("status").className = "error"; }
});
$("ask-save").addEventListener("change", () => chrome.storage.local.set({ askSave: $("ask-save").checked }));
$("reset-dir").addEventListener("click", async () => { await chrome.storage.local.set({ directory: "" }); await load(); $("status").textContent = "扩展新任务将跟随桌面默认目录。"; });
load();
