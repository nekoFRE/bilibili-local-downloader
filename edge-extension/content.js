(() => {
  if (document.getElementById("bili-local-download-button")) return;
  const host = document.createElement("div");
  host.id = "bili-local-download-button";
  const shadow = host.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = ":host{position:fixed;right:24px;bottom:96px;z-index:2147483000}button{font:600 14px 'Microsoft YaHei',sans-serif;color:white;background:#00a2d6;border:0;border-radius:24px;padding:13px 20px;box-shadow:0 5px 18px #00a2d640;cursor:pointer}button:hover{background:#008fbe}";
  const button = document.createElement("button");
  button.textContent = "↓ 下载到本地";
  button.addEventListener("click", () => {
    chrome.runtime.sendMessage({ action: "open", url: location.href }, response => {
      if (chrome.runtime.lastError || !response?.ok) {
        button.textContent = "请刷新页面后重试";
        setTimeout(() => { button.textContent = "↓ 下载到本地"; }, 2500);
      }
    });
  });
  shadow.append(style, button);
  document.documentElement.append(host);
})();
