(() => {
  if (document.getElementById("bili-local-download-panel")) return;
  const host = document.createElement("div");
  host.id = "bili-local-download-panel";
  const shadow = host.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = `
    :host{all:initial;position:fixed;inset:0;z-index:2147483000;pointer-events:none}
    *{box-sizing:border-box}button{font:500 13px 'Microsoft YaHei UI',sans-serif;cursor:pointer;border:0}
    [hidden]{display:none!important}.launcher{position:fixed;right:20px;bottom:96px;pointer-events:auto;background:#00a2d6;color:white;border-radius:24px;padding:12px 18px;box-shadow:0 5px 20px #17334a28;touch-action:none}
    .panel{position:fixed;width:min(440px,calc(100vw - 24px));height:min(720px,calc(100vh - 24px));left:max(12px,calc(100vw - 460px));top:12px;display:flex;flex-direction:column;pointer-events:auto;background:#f3f5f8;border:1px solid #dbe4ed;border-radius:16px;box-shadow:0 12px 40px #17203330;overflow:hidden}
    .bar{display:flex;align-items:center;gap:8px;padding:10px 12px;background:white;border-bottom:1px solid #e3e8f0;color:#253047;font:600 13px 'Microsoft YaHei UI',sans-serif;cursor:move;touch-action:none;user-select:none}
    .bar span{flex:1}.bar button{background:#f3f6fb;color:#526077;border-radius:6px;width:30px;height:28px}.bar button:hover{background:#e1edf5}
    iframe{border:0;width:100%;flex:1;min-height:0;background:#f3f5f8}
  `;
  const launcher = document.createElement("button");
  launcher.className = "launcher";
  launcher.textContent = "↓ 下载视频";
  launcher.title = "打开悬浮下载面板；可拖动位置";
  const panel = document.createElement("section");
  panel.className = "panel";
  panel.hidden = true;
  panel.setAttribute("aria-label", "B站视频悬浮下载面板");
  const bar = document.createElement("div");
  bar.className = "bar";
  const title = document.createElement("span");
  title.textContent = "本地下载 · 拖动这里移动";
  const minimize = document.createElement("button");
  minimize.textContent = "−";
  minimize.title = "收起面板";
  const close = document.createElement("button");
  close.textContent = "×";
  close.title = "关闭面板，下载任务继续";
  const frame = document.createElement("iframe");
  frame.title = "B站视频下载器";
  bar.append(title, minimize, close);
  panel.append(bar, frame);
  shadow.append(style, launcher, panel);
  document.documentElement.append(host);
  let frameVideo = "", currentVideo = "", dragMoved = false, toolbarHost;

  function videoURL() {
    const match = location.pathname.match(/^\/video\/(BV[0-9A-Za-z]{10})/i);
    if (!match) return "";
    const page = Number(new URLSearchParams(location.search).get("p")) || 1;
    return "https://www.bilibili.com/video/" + match[1] + "/?p=" + Math.max(1, page);
  }
  function loadVideo() {
    const video = videoURL();
    if (video && frameVideo !== video) {
      frameVideo = video;
      frame.src = chrome.runtime.getURL("popup.html") + "?floating=1&video=" + encodeURIComponent(video);
    }
  }
  function openPanel() {
    if (!videoURL()) return;
    panel.hidden = false;
    launcher.hidden = true;
    loadVideo();
    clamp(panel);
  }
  function collapse() { panel.hidden = true; launcher.hidden = !videoURL(); }
  launcher.addEventListener("click", () => { if (!dragMoved) openPanel(); });
  minimize.addEventListener("click", collapse);
  close.addEventListener("click", collapse);
  panel.addEventListener("keydown", event => { if (event.key === "Escape") collapse(); });

  function clamp(element) {
    const rect = element.getBoundingClientRect();
    const x = Math.max(8, Math.min(rect.left, innerWidth - rect.width - 8));
    const y = Math.max(8, Math.min(rect.top, innerHeight - rect.height - 8));
    element.style.left = x + "px"; element.style.top = y + "px";
    element.style.right = "auto"; element.style.bottom = "auto";
  }
  function draggable(handle, element, key) {
    let drag, moved = false;
    handle.addEventListener("pointerdown", event => {
      if (event.button !== 0 || event.target.closest(".bar button")) return;
      const rect = element.getBoundingClientRect();
      drag = { x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
      moved = false;
      if (element === launcher) dragMoved = false;
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener("pointermove", event => {
      if (!drag) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (!moved && Math.abs(dx) + Math.abs(dy) < 5) return;
      moved = true;
      if (element === launcher) dragMoved = true;
      element.style.left = drag.left + dx + "px"; element.style.top = drag.top + dy + "px";
      element.style.right = "auto"; element.style.bottom = "auto";
      clamp(element);
    });
    const finish = () => {
      if (!drag) return;
      drag = null;
      if (moved) {
        const rect = element.getBoundingClientRect();
        chrome.storage.local.set({ [key]: { x: rect.left, y: rect.top } }).catch(() => {});
      }
      if (element === launcher) setTimeout(() => { dragMoved = false; }, 0);
    };
    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
  }
  draggable(bar, panel, "floatingPanelPosition");
  draggable(launcher, launcher, "floatingButtonPosition");
  chrome.storage.local.get(["floatingPanelPosition", "floatingButtonPosition"]).then(config => {
    for (const [element, key] of [[panel, "floatingPanelPosition"], [launcher, "floatingButtonPosition"]]) {
      const position = config[key];
      if (Number.isFinite(position?.x) && Number.isFinite(position?.y)) {
        element.style.left = position.x + "px"; element.style.top = position.y + "px";
        element.style.right = "auto"; element.style.bottom = "auto";
        if (!element.hidden) clamp(element);
      }
    }
  }).catch(() => {});
  window.addEventListener("resize", () => { if (!panel.hidden) clamp(panel); if (!launcher.hidden) clamp(launcher); });

  function refreshPage() {
    const video = videoURL();
    if (video !== currentVideo) {
      currentVideo = video;
      if (!video) { panel.hidden = true; launcher.hidden = true; toolbarHost?.remove(); }
      else { launcher.hidden = !panel.hidden; if (!panel.hidden) loadVideo(); }
    }
    if (!video) return;
    const toolbar = document.querySelector("#arc_toolbar_report .video-toolbar-left, .video-toolbar-left, .video-toolbar-container, .video-info-detail");
    if (!toolbar) return;
    if (toolbarHost?.isConnected) {
      if (toolbarHost.parentNode !== toolbar) toolbar.append(toolbarHost);
      return;
    }
    toolbarHost = document.createElement("span");
    toolbarHost.id = "bili-local-inline-download";
    toolbarHost.style.cssText = "display:inline-flex;align-items:center;margin-left:14px;flex-shrink:0";
    const inlineShadow = toolbarHost.attachShadow({ mode: "closed" });
    const inlineStyle = document.createElement("style");
    inlineStyle.textContent = "button{font:500 13px 'Microsoft YaHei UI',sans-serif;background:#e5f6fc;color:#008fbe;border:1px solid #b9e7f5;border-radius:7px;padding:7px 12px;cursor:pointer;white-space:nowrap}button:hover{background:#cceefa}";
    const button = document.createElement("button");
    button.textContent = "↓ 下载视频";
    button.title = "选择画质和分P，在当前页面下载";
    button.addEventListener("click", openPanel);
    inlineShadow.append(inlineStyle, button);
    toolbar.append(toolbarHost);
  }
  let scheduled;
  const observer = new MutationObserver(() => {
    if (!scheduled) scheduled = setTimeout(() => { scheduled = null; refreshPage(); }, 300);
  });
  observer.observe(document.body || document.documentElement, { childList: true, subtree: true });
  setInterval(refreshPage, 800);
  refreshPage();
  chrome.runtime.onMessage.addListener((request, _sender, respond) => {
    if (request.action === "toggleFloating") { panel.hidden ? openPanel() : collapse(); respond({ ok: true }); }
  });
})();
