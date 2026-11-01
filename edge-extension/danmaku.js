export const DM_DEFAULTS = Object.freeze({ enabled: false, source: "web", font: 25, opacity: 70, area: 50, duration: 8, scroll: true, top: true, bottom: true, reserve: true, density: 80, keywords: "", save_ass: false, keep_original: false });
export const WEB_FIELDS = ["font", "opacity", "area", "duration", "scroll", "top", "bottom", "reserve"];

export function normalizeProfile(value = {}) {
  const result = { ...DM_DEFAULTS };
  for (const key of ["enabled", "scroll", "top", "bottom", "reserve", "save_ass", "keep_original"]) if (typeof value[key] === "boolean") result[key] = value[key];
  for (const [key, min, max] of [["font", 16, 48], ["opacity", 10, 100], ["duration", 4, 20], ["density", 10, 200]]) if (Number.isFinite(value[key])) result[key] = Math.max(min, Math.min(max, Math.round(value[key])));
  result.area = [25, 50, 75, 100].includes(value.area) ? value.area : 50;
  result.source = value.source === "custom" ? "custom" : "web";
  result.keywords = String(value.keywords || "").slice(0, 2000);
  return result;
}

// 此函数由 scripting 序列化到 B 站标签。只读取固定播放器键，不调用播放器、不写回网页。
export function readWebSettings() {
  let settings;
  try {
    const raw = localStorage.getItem("bilibili_player_settings");
    if (!raw || raw.length > 1024 * 1024) return null;
    settings = JSON.parse(raw)?.setting_config;
  } catch { return null; }
  if (!settings || typeof settings !== "object") return null;
  const values = {}, fields = [];
  const add = (key, value) => { values[key] = value; fields.push(key); };
  const number = key => {
    const raw = settings[key];
    if (typeof raw !== "number" && typeof raw !== "string") return null;
    const value = Number(raw);
    return raw !== "" && Number.isFinite(value) ? value : null;
  };
  const opacity = number("opacity"), font = number("fontsize"), speed = number("speedplus"), area = number("danmakuArea") ?? number("dmarea");
  if (opacity !== null && opacity >= 0 && opacity <= 100) add("opacity", Math.max(10, Math.round(opacity <= 1 ? opacity * 100 : opacity)));
  if (font !== null && font > 0 && font <= 48) add("font", Math.max(16, Math.min(48, Math.round(font <= 4 ? font * 25 : font))));
  if (speed !== null && speed > 0 && speed <= 4) add("duration", Math.max(4, Math.min(20, Math.round(8 / speed))));
  if (area !== null) {
    const percent = area > 0 && area <= 1 ? area * 100 : area === 0 ? 100 : area;
    if ([25, 50, 75, 100].includes(percent)) add("area", percent);
  }
  for (const [key, names] of [["scroll", ["type_scroll", "blockscroll"]], ["top", ["type_top", "blocktop"]], ["bottom", ["type_bottom", "blockbottom"]], ["reserve", ["preventshade"]]]) {
    for (const name of names) if (typeof settings[name] === "boolean") { add(key, settings[name]); break; }
  }
  return fields.length ? { values, fields } : null;
}

export class DanmakuControls {
  constructor(send, report) {
    this.send = send; this.report = report; this.profile = { ...DM_DEFAULTS }; this.reference = null; this.rows = []; this.browserMode = false;
    this.dialog = document.createElement("dialog"); this.dialog.className = "dm-dialog"; this.dialog.setAttribute("aria-label", "弹幕设置与预览");
    this.dialog.innerHTML = `<div class="row"><h2>弹幕设置与预览</h2><button type="button" data-dm-action="close" class="light">关闭</button></div>
      <div class="row dm-source"><label class="check"><input type="checkbox" data-dm-follow>跟随网页设置</label><span data-dm-badge class="source-badge"></span></div>
      <p class="dm-isolation">这里的修改<strong>仅影响下载</strong>，不会修改B站网页。</p>
      <div class="dm-reference"><b>网页参考 · 只读</b><p data-dm-reference></p><button type="button" data-dm-action="refresh" class="light">重新读取网页</button></div>
      <div class="dm-preview" role="img" aria-label="弹幕样式示意，非最终视频预览"><span class="dm-sample dm-sample-top" data-dm-sample="top">精彩的瞬间</span><span class="dm-sample dm-sample-scroll" data-dm-sample="scroll">这段值得再看一遍</span><span class="dm-sample dm-sample-bottom" data-dm-sample="bottom">下次一定</span><span class="dm-reserve">字幕留白区</span><small>样式示意 · 示例弹幕</small></div>
      <div class="dm-grid">
        <label>字号 <output data-dm-output="font"></output><input type="range" data-dm-key="font" min="16" max="48"></label>
        <label>不透明度 <output data-dm-output="opacity"></output><input type="range" data-dm-key="opacity" min="10" max="100"></label>
        <label>显示区域<select data-dm-key="area"><option value="25">上1/4屏</option><option value="50">上半屏</option><option value="75">上3/4屏</option><option value="100">全屏</option></select></label>
        <label>滚动时长 <output data-dm-output="duration"></output><input type="range" data-dm-key="duration" min="4" max="20"></label>
      </div>
      <details class="dm-advanced"><summary>更多设置</summary><div class="row wrap"><label class="check"><input type="checkbox" data-dm-key="scroll">滚动</label><label class="check"><input type="checkbox" data-dm-key="top">顶部</label><label class="check"><input type="checkbox" data-dm-key="bottom">底部</label><label class="check"><input type="checkbox" data-dm-key="reserve">字幕留白</label></div>
        <label class="dm-field">每10秒最多弹幕条数<input type="number" data-dm-key="density" min="10" max="200"></label>
        <label class="dm-field">屏蔽关键词（逗号或换行分隔）<textarea data-dm-key="keywords" maxlength="2000" rows="2"></textarea></label>
        <label class="check"><input type="checkbox" data-dm-key="save_ass">额外保存ASS弹幕文件</label><label class="check"><input type="checkbox" data-dm-key="keep_original">同时保留普通原视频</label>
      </details>
      <p class="hint">未读取的项目采用下载器默认值。智能防遮挡、BAS等特殊效果暂不支持。滚动时长越大，速度越慢。</p>
      <p class="hint">弹幕版需要重新编码，耗时更长；写入画面后无法单独关闭弹幕。ASS文件可供兼容播放器单独加载。</p>
      <div class="row dm-dialog-actions"><button type="button" data-dm-action="close">取消</button><button type="button" data-dm-action="apply" class="primary">应用到下载</button></div>`;
    document.body.append(this.dialog);
    this.dialog.querySelectorAll('[data-dm-action="close"]').forEach(button => button.addEventListener("click", () => this.dialog.close()));
    this.dialog.addEventListener("close", () => { this.draft = null; });
    this.dialog.querySelector('[data-dm-action="apply"]').addEventListener("click", async () => { this.profile = normalizeProfile(this.draft); await this.persist(); this.render(); this.dialog.close(); });
    this.dialog.querySelector('[data-dm-action="refresh"]').addEventListener("click", () => this.refreshReference().catch(error => this.report(error.message, true)));
    this.dialog.querySelector("[data-dm-follow]").addEventListener("change", event => { this.draft.source = event.target.checked ? "web" : "custom"; this.draft = this.resolve(this.draft); this.renderDialog(); });
    this.dialog.querySelectorAll("[data-dm-key]").forEach(input => input.addEventListener(input.type === "range" ? "input" : "change", () => {
      const key = input.dataset.dmKey;
      this.draft[key] = input.type === "checkbox" ? input.checked : key === "keywords" ? input.value : Number(input.value);
      this.draft.source = "custom"; this.draft = normalizeProfile(this.draft); this.renderDialog();
    }));
  }
  async init() {
    const config = await chrome.storage.local.get({ danmaku: DM_DEFAULTS });
    this.profile = normalizeProfile(config.danmaku); this.render();
    chrome.storage.onChanged.addListener((changes, area) => { if (area === "local" && changes.danmaku) { this.profile = normalizeProfile(changes.danmaku.newValue); this.render(); } });
    await this.refreshReference();
  }
  mount(host, video = false) {
    host.classList.add("dm-entry");
    host.innerHTML = `<label class="check"><input type="checkbox">带弹幕</label><span class="hint"></span><button type="button" class="light">设置与预览</button>`;
    const input = host.querySelector("input"), button = host.querySelector("button");
    this.rows.push({ host, input, button, video });
    input.addEventListener("change", async () => { this.profile.enabled = input.checked; await this.persist(); if (input.checked && this.profile.source === "web") await this.refreshReference(); this.render(); });
    button.addEventListener("click", () => this.open()); this.render();
  }
  async persist() { await chrome.storage.local.set({ danmaku: normalizeProfile(this.profile) }); }
  resolve(profile) {
    const result = normalizeProfile(profile);
    if (result.source === "web") for (const key of WEB_FIELDS) result[key] = this.reference?.fields?.includes(key) ? this.reference.values[key] : DM_DEFAULTS[key];
    return normalizeProfile(result);
  }
  async refreshReference() {
    if (this.pending) return this.pending;
    this.pending = (async () => {
      try { this.reference = await this.send({ action: "danmakuReference", url: document.getElementById("url")?.value || "" }); }
      catch { this.reference = null; }
      this.render(); if (this.draft) { this.draft = this.resolve(this.draft); this.renderDialog(); }
    })().finally(() => { this.pending = null; });
    return this.pending;
  }
  async snapshot(browser = false) {
    if (this.profile.enabled && this.profile.source === "web" && !browser) await this.refreshReference();
    return { ...this.resolve(this.profile), enabled: browser ? false : this.profile.enabled };
  }
  setBrowserMode(value) { this.browserMode = value; this.render(); }
  render() {
    for (const row of this.rows) {
      const blocked = row.video && this.browserMode;
      row.input.checked = this.profile.enabled; row.input.disabled = blocked; row.button.disabled = blocked;
      row.host.querySelector("span").textContent = blocked ? "仅桌面合并模式" : this.profile.source === "custom" ? "下载专用" : this.reference ? "跟随网页" : "未读取网页 · 采用默认";
    }
  }
  open() { this.draft = this.resolve(this.profile); this.renderDialog(); this.dialog.showModal(); this.refreshReference(); }
  renderDialog() {
    if (!this.draft) return;
    const profile = this.draft;
    this.dialog.querySelector("[data-dm-follow]").checked = profile.source === "web";
    this.dialog.querySelector("[data-dm-badge]").textContent = profile.source === "custom" ? "已自定义 · 仅影响下载" : this.reference ? "跟随网页" : "采用下载器默认值";
    const labels = { font: "字号", opacity: "不透明度", area: "显示区域", duration: "滚动秒数", scroll: "滚动", top: "顶部", bottom: "底部", reserve: "字幕留白" };
    const reference = this.reference;
    this.dialog.querySelector("[data-dm-reference]").textContent = reference ? reference.fields.map(key => labels[key] + " " + (typeof reference.values[key] === "boolean" ? reference.values[key] ? "开" : "关" : reference.values[key] + (["opacity", "area"].includes(key) ? "%" : ""))).join(" · ") + (reference.fields.length < WEB_FIELDS.length ? "；其余项目未读取" : "") : "未读取到网页设置；请打开B站视频页后重新读取。当前采用下载器默认值。";
    this.dialog.querySelectorAll("[data-dm-key]").forEach(input => { const value = profile[input.dataset.dmKey]; if (input.type === "checkbox") input.checked = value; else if (document.activeElement !== input) input.value = value; });
    for (const key of ["font", "opacity", "duration"]) this.dialog.querySelector('[data-dm-output="' + key + '"]').textContent = profile[key] + (key === "opacity" ? "%" : key === "duration" ? " 秒" : "");
    this.dialog.querySelectorAll("[data-dm-sample]").forEach(sample => { sample.hidden = !profile[sample.dataset.dmSample]; sample.style.opacity = profile.opacity / 100; sample.style.fontSize = Math.max(11, profile.font * .58) + "px"; });
    this.dialog.querySelector(".dm-sample-bottom").style.top = profile.area <= 50 ? "38%" : profile.reserve ? "65%" : "76%";
    this.dialog.querySelector(".dm-reserve").hidden = !profile.reserve;
  }
}
