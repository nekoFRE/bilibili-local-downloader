const $ = id => document.getElementById(id);
const duration = seconds => Math.floor((seconds || 0) / 60) + ":" + String((seconds || 0) % 60).padStart(2, "0");
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const emptyState = () => ({ items: [], selected: [], parts: {}, nextPage: 1, hasMore: true, total: 0, query: "", scroll: 0 });

export class FavoriteBrowser {
  constructor(send, report, danmaku) {
    this.send = send; this.report = report; this.danmaku = danmaku; this.folders = []; this.folderId = ""; this.state = emptyState(); this.states = new Map(); this.nodes = new Map(); this.generation = 0; this.scanMode = null;
    this.viewMode = "card";
    document.querySelectorAll("[data-favorite-view]").forEach(button => button.addEventListener("click", () => this.setViewMode(button.dataset.favoriteView)));
    chrome.storage.onChanged.addListener((changes, area) => { if (area === "local" && changes.favoriteView) this.setViewMode(changes.favoriteView.newValue, false); });
    this.images = new IntersectionObserver(entries => entries.forEach(entry => { if (entry.isIntersecting) { const image = entry.target; if (image.dataset.src) { image.src = image.dataset.src; delete image.dataset.src; } this.images.unobserve(image); } }), { root: $("favorite-scroll"), rootMargin: "220px" });
    this.sentinel = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) this.maybeMore(); }, { root: $("favorite-scroll"), rootMargin: "140px" });
    this.sentinel.observe($("favorite-sentinel"));
    $("browse-favorites").addEventListener("click", () => this.open());
    $("favorites-back").addEventListener("click", () => this.back());
    $("folders").addEventListener("click", () => this.loadFolders());
    $("folder").addEventListener("change", () => this.switchFolder($("folder").value));
    $("favorite-search").addEventListener("input", () => { this.state.query = $("favorite-search").value; this.render(); this.save(); });
    $("favorite-more").addEventListener("click", () => { this.pageError = false; this.nextPage(); });
    $("favorite-all").addEventListener("click", () => { this.state.selected = [...new Set([...this.state.selected, ...this.visible().filter(item => item.available).map(item => item.bvid)])]; this.render(); this.save(); });
    $("favorite-none").addEventListener("click", () => { this.state.selected = []; this.render(); this.save(); });
    $("favorite-whole").addEventListener("click", () => this.scan("select"));
    $("favorite-search-all").addEventListener("click", () => this.scan("search"));
    $("favorite-stop").addEventListener("click", () => { this.scanMode = null; this.renderCount(); this.report("已停止继续读取，保留已加载内容和原有勾选。"); });
    $("favorite-download").addEventListener("click", () => this.download());
    $("favorite-scroll").addEventListener("scroll", () => { this.state.scroll = $("favorite-scroll").scrollTop; this.save(); this.maybeMore(); }, { passive: true });
    $("favorite-choose-dir").addEventListener("click", async () => { try { const result = await this.send({ action: "chooseDirectory" }); this.report(result.directory ? "保存目录已更新，新任务跟随桌面目录。" : "已取消选择。"); } catch (error) { this.report(error.message, true); } });
    this.partsDialog = $("favorite-parts-dialog");
    $("favorite-parts-cancel").addEventListener("click", () => this.partsDialog.close());
    $("favorite-parts-all").addEventListener("change", event => this.partsDialog.querySelectorAll(".part input").forEach(input => { input.checked = event.target.checked; }));
    $("favorite-parts-apply").addEventListener("click", () => this.applyParts());
  }
  async init() {
    const config = await chrome.storage.local.get({ favoriteView: "card" });
    this.setViewMode(config.favoriteView, false);
    const stored = (await chrome.storage.session.get("favoriteBrowser")).favoriteBrowser;
    if (stored?.version === 1 && Array.isArray(stored.state?.items)) {
      this.folders = stored.folders || []; this.folderId = String(stored.folderId || ""); this.state = { ...emptyState(), ...stored.state };
      this.state.selected = this.state.selected.filter(id => this.state.items.some(item => item.bvid === id && item.available));
      this.populateFolders(); this.render();
      if (stored.open) await this.open();
    }
  }
  setViewMode(mode, persist = true) {
    mode = mode === "list" ? "list" : "card";
    const changed = this.viewMode !== mode, region = $("favorite-scroll").getBoundingClientRect();
    const anchor = changed ? [...this.nodes.values()].find(node => !node.hidden && node.getBoundingClientRect().bottom > region.top && node.getBoundingClientRect().top < region.bottom) : null;
    this.viewMode = mode;
    $("favorite-list").classList.toggle("list-view", mode === "list");
    document.querySelectorAll("[data-favorite-view]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.favoriteView === mode)));
    if (anchor) requestAnimationFrame(() => { if (anchor.isConnected) $("favorite-scroll").scrollTop += anchor.getBoundingClientRect().top - $("favorite-scroll").getBoundingClientRect().top; });
    if (persist) chrome.storage.local.set({ favoriteView: mode }).catch(() => {});
  }
  save() {
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => chrome.storage.session.set({ favoriteBrowser: { version: 1, folders: this.folders, folderId: this.folderId, state: this.state, open: !$('favorites-view').hidden } }).catch(() => {}), 120);
  }
  async open() {
    $("home-view").hidden = true; $("favorites-view").hidden = false; document.body.classList.add("browsing");
    $("favorite-scroll").scrollTop = this.state.scroll || 0;
    document.dispatchEvent(new Event("directory-view-change"));
    if (!this.folders.length) await this.loadFolders();
    else if (this.folderId && !this.state.items.length) await this.nextPage();
    this.save();
  }
  back() {
    this.state.scroll = $("favorite-scroll").scrollTop;
    $("home-view").hidden = false; $("favorites-view").hidden = true; document.body.classList.remove("browsing"); this.save();
    document.dispatchEvent(new Event("directory-view-change"));
  }
  populateFolders() {
    $("folder").replaceChildren(new Option("选择收藏夹", ""));
    for (const folder of this.folders) $("folder").add(new Option(folder.title + " · " + (folder.media_count || 0) + " 个", String(folder.id)));
    $("folder").value = this.folderId;
  }
  async loadFolders() {
    $("folders").disabled = true;
    try {
      this.folders = await this.send({ action: "folders" });
      if (this.folderId && !this.folders.some(folder => String(folder.id) === this.folderId)) this.switchFolder("");
      this.populateFolders(); this.report("已更新收藏夹列表。选择收藏夹即可浏览视频。"); this.save();
    } catch (error) { this.report(error.message, true); }
    finally { $("folders").disabled = false; }
  }
  switchFolder(id) {
    if (this.folderId) this.states.set(this.folderId, this.state);
    this.generation++; this.scanMode = null; this.loading = false; this.pageError = false; this.folderId = String(id); this.state = this.states.get(this.folderId) || emptyState();
    if (this.partsDialog.open) this.partsDialog.close();
    this.nodes.clear(); this.images.disconnect(); $("favorite-list").replaceChildren(); $("favorite-search").value = this.state.query;
    this.render(); $("favorite-scroll").scrollTop = this.state.scroll || 0; this.save();
    if (this.folderId && !this.state.items.length) this.nextPage();
  }
  visible() {
    const query = this.state.query.trim().toLowerCase();
    return this.state.items.filter(item => !query || (item.title + " " + item.author + " " + item.bvid).toLowerCase().includes(query));
  }
  maybeMore() {
    if (!$("favorites-view").hidden && !this.state.query.trim() && !this.scanMode && !this.pageError && $("favorite-scroll").scrollHeight - $("favorite-scroll").scrollTop - $("favorite-scroll").clientHeight < 180) this.nextPage();
  }
  async nextPage() {
    if (this.loading || !this.folderId || !this.state.hasMore) return false;
    const generation = this.generation, folderId = this.folderId;
    this.loading = true; this.renderCount();
    try {
      const data = await this.send({ action: "favoriteItems", folderId, page: this.state.nextPage });
      if (generation !== this.generation) return false;
      const known = new Set(this.state.items.map(item => item.key));
      this.state.items.push(...data.items.filter(item => !known.has(item.key)));
      this.state.nextPage = data.page + 1; this.state.hasMore = data.has_more; this.state.total = data.total || Math.max(this.state.total, this.state.items.length); this.pageError = false;
      this.render(); this.save(); return true;
    } catch (error) { if (generation === this.generation) { this.pageError = true; this.report(error.message + " 可点击“重试读取”。", true); } return false; }
    finally { if (generation === this.generation) { this.loading = false; this.renderCount(); if (!this.pageError) queueMicrotask(() => this.maybeMore()); } }
  }
  card(item) {
    const article = document.createElement("article"); article.className = "favorite-tile" + (!item.available ? " unavailable" : "");
    const label = document.createElement("label"); label.className = "favorite-pick";
    const input = document.createElement("input"); input.type = "checkbox"; input.disabled = !item.available; input.setAttribute("aria-label", "选择 " + item.title);
    input.addEventListener("change", () => {
      this.state.selected = input.checked ? [...new Set([...this.state.selected, item.bvid])] : this.state.selected.filter(id => id !== item.bvid);
      this.renderCount(); article.classList.toggle("selected", input.checked); this.save();
    });
    const cover = document.createElement("div"); cover.className = "favorite-cover";
    const fallback = document.createElement("span"); fallback.className = "cover-placeholder"; fallback.textContent = item.available ? "封面加载中" : "已失效";
    const image = document.createElement("img"); image.alt = ""; image.decoding = "async"; image.loading = "lazy"; image.referrerPolicy = "no-referrer";
    const retry = document.createElement("button"); retry.className = "light cover-retry"; retry.textContent = "重试封面"; retry.hidden = true;
    let coverURL;
    try { const parsed = new URL(item.cover); if (parsed.hostname.endsWith(".hdslb.com") && ["http:", "https:"].includes(parsed.protocol)) { parsed.protocol = "https:"; coverURL = parsed.href + (parsed.search || parsed.pathname.includes("@") ? "" : "@320w_180h_1c.webp"); } } catch {}
    image.addEventListener("load", () => { fallback.hidden = true; retry.hidden = true; image.hidden = false; });
    image.addEventListener("error", () => { fallback.textContent = "暂无封面"; fallback.hidden = false; image.hidden = true; retry.hidden = !coverURL; });
    retry.addEventListener("click", () => { retry.hidden = true; fallback.textContent = "重新加载…"; image.src = coverURL; });
    if (coverURL && item.available) { image.dataset.src = coverURL; this.images.observe(image); } else { image.hidden = true; fallback.textContent = item.available ? "暂无封面" : "已失效"; }
    const time = document.createElement("span"); time.className = "favorite-duration"; time.textContent = duration(item.duration);
    cover.append(fallback, image, time);
    const title = document.createElement("span"); title.className = "favorite-title"; title.textContent = item.title; title.title = item.title;
    const caption = document.createElement("div"); caption.className = "favorite-caption";
    const listAuthor = document.createElement("span"); listAuthor.className = "favorite-list-author"; listAuthor.textContent = item.author || item.bvid; listAuthor.title = item.author || item.bvid;
    caption.append(title, listAuthor); label.append(input, cover, caption);
    const foot = document.createElement("div"); foot.className = "favorite-meta";
    const author = document.createElement("span"); author.textContent = item.author || item.bvid; author.title = item.author || item.bvid;
    const parts = document.createElement("button"); parts.className = "light favorite-parts"; parts.disabled = !item.available; parts.addEventListener("click", () => this.openParts(item));
    foot.append(author, parts); article.append(label, foot, retry); return article;
  }
  render() {
    $("favorite-search").value = this.state.query;
    const visible = new Set(this.visible().map(item => item.key));
    for (const item of this.state.items) {
      let node = this.nodes.get(item.key);
      if (!node) { node = this.card(item); this.nodes.set(item.key, node); $("favorite-list").append(node); }
      node.hidden = !visible.has(item.key); node.classList.toggle("selected", this.state.selected.includes(item.bvid)); node.querySelector("input").checked = this.state.selected.includes(item.bvid);
      const pages = this.state.parts[item.bvid]; node.querySelector(".favorite-parts").textContent = item.available ? pages?.length ? "已选 " + pages.length + "P" : "选择分P" : "已失效";
    }
    $("favorite-empty").hidden = visible.size > 0;
    $("favorite-empty").textContent = !this.folderId ? "选择收藏夹后，视频会分批显示在这里。" : this.state.query ? "已加载视频中没有匹配项；可搜索整个收藏夹。" : this.loading ? "正在读取视频…" : "收藏夹暂时没有可显示的视频。";
    this.renderCount();
  }
  renderCount() {
    $("favorite-count").textContent = "已加载 " + this.state.items.length + " / " + (this.state.total || "—") + " 个 · 已选 " + this.state.selected.length + " 个" + (this.state.query ? " · 匹配 " + this.visible().length + " 个" : "") + (this.scanMode ? " · 正在读取完整清单…" : this.loading ? " · 加载中…" : "");
    $("favorite-all").textContent = this.state.query ? "全选匹配结果" : "全选已加载";
    $("favorite-download").textContent = "加入下载队列（" + this.state.selected.length + "）"; $("favorite-download").disabled = !this.state.selected.length || this.submitting;
    $("favorite-more").disabled = this.loading || !this.state.hasMore || !this.folderId || Boolean(this.scanMode);
    $("favorite-more").textContent = this.pageError ? "重试读取" : this.loading ? "加载中…" : !this.state.hasMore ? "已加载全部" : "继续加载";
    for (const id of ["favorite-whole", "favorite-search-all"]) $(id).disabled = !this.folderId || Boolean(this.scanMode);
    $("favorite-stop").hidden = !this.scanMode;
  }
  async scan(goal) {
    if (this.scanMode || !this.folderId) return;
    const generation = this.generation; this.scanMode = goal; this.pageError = false; this.renderCount();
    // 等待已发出的当前页，避免同时请求同一页。
    while (this.loading && generation === this.generation && this.scanMode === goal) await pause(60);
    while (generation === this.generation && this.scanMode === goal && this.state.hasMore) { if (!await this.nextPage()) break; await pause(160); }
    if (generation !== this.generation) return;
    if (this.scanMode === goal && !this.state.hasMore) {
      if (goal === "select") this.state.selected = this.state.items.filter(item => item.available).map(item => item.bvid);
      this.report(goal === "select" ? "已选择整个收藏夹中的可用视频；已知失效视频已跳过。" : "已读取完整清单，当前搜索范围覆盖整个收藏夹。");
    }
    this.scanMode = null; this.render(); this.save();
  }
  async openParts(item) {
    this.partsItem = item; this.partsGeneration = this.generation;
    $("favorite-parts-title").textContent = item.title; $("favorite-parts-list").textContent = "正在读取分P…"; $("favorite-parts-apply").disabled = true; $("favorite-parts-all").checked = false; this.partsDialog.showModal();
    try {
      const meta = await this.send({ action: "videoParts", url: item.bvid });
      if (!this.partsDialog.open || this.partsItem !== item || this.partsGeneration !== this.generation) return;
      $("favorite-parts-list").replaceChildren();
      const selected = this.state.parts[item.bvid] || meta.pages.map(part => part.page);
      for (const part of meta.pages) { const label = document.createElement("label"); label.className = "part"; const input = document.createElement("input"); input.type = "checkbox"; input.value = part.page; input.checked = selected.includes(part.page); const text = document.createElement("span"); text.textContent = "P" + part.page + " " + part.part + " · " + duration(part.duration); label.append(input, text); $("favorite-parts-list").append(label); }
      $("favorite-parts-apply").disabled = false;
    } catch (error) { if (this.partsDialog.open) $("favorite-parts-list").textContent = error.message; }
  }
  applyParts() {
    if (this.partsGeneration !== this.generation || !this.partsItem) return;
    const bvid = this.partsItem.bvid, pages = [...$("favorite-parts-list").querySelectorAll("input:checked")].map(input => Number(input.value));
    if (pages.length) { this.state.parts[bvid] = pages; this.state.selected = [...new Set([...this.state.selected, bvid])]; }
    else { delete this.state.parts[bvid]; this.state.selected = this.state.selected.filter(id => id !== bvid); }
    this.partsDialog.close(); this.render(); this.save();
  }
  async download() {
    if (!this.state.selected.length || this.submitting) return;
    const selected = new Set(this.state.selected), parts = structuredClone(this.state.parts);
    const items = this.state.items.filter(item => item.available && selected.has(item.bvid)).map(item => ({ bvid: item.bvid, title: item.title, ...(parts[item.bvid]?.length ? { pages: parts[item.bvid] } : {}) }));
    const allPages = $("favorite-all-pages").checked, quality = Number($("favorite-quality").value);
    this.submitting = true; this.renderCount();
    try {
      const danmaku = await this.danmaku.snapshot();
      this.report("正在提交已勾选的视频，设置将固定在本批任务中…");
      const result = await this.send({ action: "favoriteDownload", items, allPages, quality, danmaku }); this.report(result.message);
    } catch (error) { this.report(error.message, true); }
    finally { this.submitting = false; this.renderCount(); }
  }
}
