// 弹窗、悬浮面板和设置页共用目录显示；仅在目录可见时询问已运行的桌面端。
(() => {
  const output = document.getElementById("directory");
  const outputs = [output, document.getElementById("favorite-directory")].filter(Boolean);
  let visible = false, pending = false;
  const render = directory => { outputs.forEach(element => { element.textContent = "保存目录：" + (directory || "跟随桌面程序"); }); };
  chrome.storage.local.get({ directory: "" }).then(config => render(config.directory)).catch(() => {});
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.directory) render(changes.directory.newValue);
  });
  async function sync() {
    if (!visible || pending || document.visibilityState !== "visible" ||
        document.body.classList.contains("extension-disabled") ||
        document.getElementById("extension-enabled")?.checked === false) return;
    pending = true;
    try {
      const response = await chrome.runtime.sendMessage({ action: "directory" });
      if (response?.ok) render(response.data.directory);
    } catch {} // 未连接时保留缓存，不打断视频解析，也不唤醒桌面端。
    finally { pending = false; }
  }
  const visibility = new Map();
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => visibility.set(entry.target, entry.isIntersecting));
    visible = [...visibility.values()].some(Boolean);
    if (visible) sync();
  });
  outputs.forEach(element => observer.observe(element));
  document.addEventListener("directory-view-change", sync);
  setInterval(sync, 2000);
  document.addEventListener("visibilitychange", sync);
  window.addEventListener("focus", sync);
})();
