const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const $ = (selector) => document.querySelector(selector);
const state = { tab: "local", page: 1, items: [], selected: new Set(), snapshot: "", catalog: null, enabled: true, job: null, generation: 0, loading: false, applying: false, editId: "", poll: null, filters: { local: {}, remote: {} } };
Object.assign(state, { siteReady: false, catalogPage: 1, catalogGeneration: 0, packsPage: 1, packsGeneration: 0, pack: null, playAnimations: !matchMedia("(prefers-reduced-motion: reduce)").matches });
const previewData = new WeakMap();
const visibility = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    const data = previewData.get(entry.target);
    if (data) { data.visible = entry.isIntersecting; applyAnimation(entry.target, data); }
  }
}, { rootMargin: "120px" });
$("#play-animations").checked = state.playAnimations;

function notice(text, kind = "") {
  $("#notice").textContent = text;
  $("#notice").className = `notice ${kind}`;
}

function formValues(form) {
  const values = Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, value.trim()]));
  if (form.id === "remote-form") {
    for (const field of ["character", "artist"]) {
      const input = form.querySelector(`[name="${field}"]`);
      if (input.dataset.siteName === values[field] && input.dataset.siteSlug) values[field] = input.dataset.siteSlug;
    }
  }
  return values;
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

function setOptions(select, categories, all = false) {
  const previous = select.value;
  select.replaceChildren();
  if (all) select.add(new Option("全部用途", ""));
  for (const category of categories) select.add(new Option(category.name, category.name));
  if ([...select.options].some((option) => option.value === previous)) select.value = previous;
  else if (!all) select.value = "未分类";
}

function renderCatalog(data) {
  state.catalog = data.catalog;
  state.enabled = data.enabled;
  const catalog = data.catalog;
  $("#stat-count").textContent = catalog.count;
  $("#stat-limit").textContent = `最多 ${catalog.max_items} 张`;
  $("#stat-size").textContent = `${(catalog.bytes / 1048576).toFixed(1)} MB`;
  $("#stat-capacity").textContent = `容量 ${(catalog.max_bytes / 1048576).toFixed(0)} MiB`;
  const usage = catalog.categories.filter((category) => category.dimension !== "character");
  $("#stat-categories").textContent = usage.length;
  setOptions($("#local-category"), usage, true);
  setOptions($("#target-category"), usage);
  setOptions($("#edit-category"), usage);
  $("#characters").replaceChildren(...catalog.categories.filter((category) => category.dimension === "character").map((category) => new Option(category.name.slice(3))));
  if (!data.enabled) notice("本地缓存已关闭。你仍可浏览已有图库；请在插件配置中开启缓存后再收藏。", "warning");
  if (data.job && (!state.job || state.job.id === data.job.id)) renderJob(data.job);
  updateSelection();
}

async function loadOverview() {
  renderCatalog(await bridge.apiGet("admin/overview"));
}

function updateSelection() {
  const active = state.job?.state === "running";
  $("#selection-count").textContent = `已选 ${state.selected.size} 张`;
  const button = $("#apply-selection");
  button.textContent = state.tab === "remote" ? `缓存已选${state.selected.size ? `（${state.selected.size}）` : ""}` : "批量归类";
  button.disabled = !state.selected.size || state.loading || state.applying || (state.tab === "remote" && (!state.enabled || active));
  $("#select-all").disabled = !state.items.length || state.loading;
  $("#clear-selection").disabled = !state.selected.size;
  for (const card of document.querySelectorAll(".emoji-card")) {
    const selected = state.selected.has(card.dataset.id);
    card.classList.toggle("selected", selected);
    card.querySelector("input").checked = selected;
    const action = card.querySelector(".card-action");
    action.disabled = state.tab === "remote" && (!state.enabled || active || state.applying);
  }
  $("#batch-form button").disabled = !state.enabled || active || state.applying;
  $("#cache-pack").disabled = !state.pack || !state.enabled || active || state.applying;
}

function applyAnimation(image, data) {
  const playing = data.animated && state.playAnimations && data.visible;
  const source = playing ? data.motion : data.still;
  if (image.getAttribute("src") !== source) image.src = source;
  if (data.animated) image.closest(".emoji-card").querySelector(".format-badge").textContent = playing ? "动态 · 播放中" : "动态 · 已暂停";
}

async function loadSiteCatalog(page = 1, refresh = false) {
  const generation = ++state.catalogGeneration;
  const values = formValues($("#site-catalog-form"));
  $("#catalog-status").textContent = "正在拉取小站分类…";
  $("#site-categories").replaceChildren();
  $("#catalog-previous").disabled = true;
  $("#catalog-next").disabled = true;
  try {
    const data = await bridge.apiGet("admin/site/catalog", { ...values, page, refresh: refresh ? "1" : "0" });
    if (generation !== state.catalogGeneration) return;
    state.siteReady = true;
    state.catalogPage = page;
    $("#catalog-status").textContent = `来自小站的${values.kind === "characters" ? "人物" : "画师"}分类 · 共 ${data.total} 项 · 第 ${page} 页`;
    const field = values.kind === "characters" ? "character" : "artist";
    for (const item of data.items) {
      const button = element("button", "site-category");
      button.type = "button";
      button.dataset.name = item.name;
      button.classList.toggle("selected", $(`#remote-form [name="${field}"]`).value === item.name);
      button.append(element("span", "", item.name), element("small", "", `${item.pack_count} 包 · ${item.count} 张`));
      button.addEventListener("click", async () => {
        const input = $(`#remote-form [name="${field}"]`);
        input.value = item.name;
        input.dataset.siteName = item.name;
        input.dataset.siteSlug = item.slug;
        $("#remote-form [name=pack]").value = "";
        state.pack = null;
        $("#selected-pack").hidden = true;
        for (const sibling of $("#site-categories").children) sibling.classList.toggle("selected", sibling === button);
        state.filters.remote = formValues($("#remote-form"));
        resetRemoteGallery("已选择小站分类", "下方已拉取对应表情包；选择一个包查看，或浏览该分类的全部表情。");
        await loadSitePacks();
      });
      $("#site-categories").append(button);
    }
    $("#catalog-previous").disabled = page <= 1;
    $("#catalog-next").disabled = !data.has_next;
    if (!data.items.length) $("#catalog-status").textContent += " · 没有匹配项";
  } catch (error) {
    if (generation === state.catalogGeneration) $("#catalog-status").textContent = `分类拉取失败：${error.message}，可点击“拉取分类”重试。`;
  }
}

async function loadSitePacks(page = 1) {
  const generation = ++state.packsGeneration;
  const filters = formValues($("#remote-form"));
  delete filters.pack;
  $("#site-packs-panel").hidden = false;
  $("#site-packs").replaceChildren();
  $("#packs-status").textContent = "正在拉取分类下的表情包…";
  $("#packs-previous").disabled = true;
  $("#packs-next").disabled = true;
  try {
    const data = await bridge.apiPost("admin/site/packs", { filters, page });
    if (generation !== state.packsGeneration) return;
    state.packsPage = page;
    $("#packs-status").textContent = `共 ${data.total} 个表情包 · 第 ${page} 页 · 点击表情包查看图片`;
    for (const pack of data.items) {
      const button = element("button", "site-pack");
      button.type = "button";
      button.dataset.pack = pack.id;
      button.append(element("strong", "", pack.title), element("small", "", `${pack.count} 张 · ${(pack.artists || []).map((artist) => artist.name).join("、") || "多人/未标注画师"}`));
      button.addEventListener("click", async () => {
        state.pack = pack;
        $("#remote-form [name=pack]").value = pack.id;
        $("#selected-pack-title").textContent = pack.title;
        $("#selected-pack-count").textContent = `小站目录 ${pack.count} 张 · 批量缓存本次最多 100 张`;
        $("#selected-pack").hidden = false;
        $("#site-directory").open = false;
        state.filters.remote = formValues($("#remote-form"));
        await loadItems();
      });
      $("#site-packs").append(button);
    }
    if (!data.items.length) $("#packs-status").textContent = "当前人物、画师或关键词条件下没有表情包，请调整条件。";
    $("#packs-previous").disabled = page <= 1;
    $("#packs-next").disabled = !data.has_next;
  } catch (error) {
    if (generation === state.packsGeneration) $("#packs-status").textContent = `表情包拉取失败：${error.message}`;
  }
}

function resetRemoteGallery(title, description) {
  ++state.generation;
  state.page = 1;
  state.snapshot = "";
  state.items = [];
  state.selected.clear();
  state.loading = false;
  $("#page-number").textContent = "第 1 页";
  $("#gallery").setAttribute("aria-busy", "false");
  renderCards();
  $("#empty-title").textContent = title;
  $("#empty-description").textContent = description;
  $("#previous").disabled = true;
  $("#next").disabled = true;
  $("#result-count").textContent = "尚未读取图片";
}

function renderCards() {
  visibility.disconnect();
  const gallery = $("#gallery");
  gallery.replaceChildren();
  state.items.forEach((item) => {
    const card = element("article", "emoji-card");
    card.dataset.id = item.id;
    const checkLabel = element("label", "card-check");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.setAttribute("aria-label", `选择 ${item.title}`);
    check.addEventListener("change", () => {
      if (check.checked) state.selected.add(item.id); else state.selected.delete(item.id);
      updateSelection();
    });
    checkLabel.append(check);
    const area = element("div", "image-area");
    const image = document.createElement("img");
    image.alt = item.title;
    const preview = element("div", "preview-status", "正在准备预览…");
    area.append(image, preview);
    const badge = element("span", "format-badge", item.animated ? "动态" : (item.format || "图片").toUpperCase());
    const body = element("div", "card-body");
    const title = element("p", "card-title", item.description || item.title);
    title.title = item.description || item.title;
    const tags = element("div", "card-tags");
    for (const character of (item.characters || []).slice(0, 2)) tags.append(element("span", "tag character", character.name));
    for (const category of (item.categories || []).filter((name) => !name.startsWith("角色：")).slice(0, 2)) tags.append(element("span", "tag", category));
    const action = element("button", "secondary card-action", state.tab === "remote" ? "缓存此图" : "编辑用途");
    action.type = "button";
    action.addEventListener("click", () => state.tab === "remote" ? startSelectedJob([item.id]) : openEditor(item));
    body.append(title, tags, action);
    card.append(area, checkLabel, badge, body);
    gallery.append(card);
  });
  $("#empty").hidden = Boolean(state.items.length);
  gallery.hidden = !state.items.length;
  if (!state.items.length) {
    $("#empty-title").textContent = state.tab === "local" ? "这里还没有匹配的表情" : "没有找到匹配的表情";
    $("#empty-description").textContent = state.tab === "local" ? "试试调整筛选，或前往“小站搜索”收藏喜欢的图片。" : "换个人物、画师或关键词再试试。";
  }
  updateSelection();
  loadPreviews(state.generation);
}

async function previewOne(item, generation) {
  const card = [...document.querySelectorAll(".emoji-card")].find((node) => node.dataset.id === item.id);
  if (!card) return;
  const status = card.querySelector(".preview-status");
  try {
    const data = await bridge.apiGet("admin/preview", { source: state.tab === "local" ? "local" : "remote", id: item.id, snapshot: state.snapshot });
    if (generation !== state.generation || !card.isConnected) return;
    if (typeof data.data_url !== "string" || !/^data:image\/(webp|gif|png);base64,/.test(data.data_url)) throw new Error("预览格式无效");
    if (data.animated && (typeof data.poster_url !== "string" || !data.poster_url.startsWith("data:image/webp;base64,"))) throw new Error("动图封面无效");
    const image = card.querySelector("img");
    image.onload = () => { status.hidden = true; image.classList.add("loaded"); };
    const sources = { motion: data.data_url, still: data.poster_url || data.data_url, animated: data.animated, visible: true };
    previewData.set(image, sources);
    applyAnimation(image, sources);
    visibility.observe(image);
  } catch (error) {
    if (generation !== state.generation || !card.isConnected) return;
    status.replaceChildren(document.createTextNode(String(error.message || "暂时无法预览").slice(0, 180)));
    const retry = element("button", "text-button", "重试预览");
    retry.type = "button";
    retry.addEventListener("click", () => { status.textContent = "正在准备预览…"; previewOne(item, generation); });
    status.append(retry);
  }
}

async function loadPreviews(generation) {
  const queue = [...state.items];
  await Promise.all(Array.from({ length: 2 }, async () => {
    while (queue.length && generation === state.generation) await previewOne(queue.shift(), generation);
  }));
}

async function loadItems(page = 1) {
  const generation = ++state.generation;
  state.loading = true;
  state.selected.clear();
  state.items = [];
  $("#gallery").replaceChildren();
  $("#gallery").setAttribute("aria-busy", "true");
  $("#empty").hidden = false;
  $("#empty-title").textContent = "正在读取表情…";
  $("#empty-description").textContent = "请稍候";
  $("#previous").disabled = true;
  $("#next").disabled = true;
  updateSelection();
  try {
    const data = state.tab === "local"
      ? await bridge.apiGet("admin/library", { ...state.filters.local, page })
      : await bridge.apiPost("admin/search", { filters: state.filters.remote, page });
    if (generation !== state.generation) return;
    state.page = page;
    state.items = data.items;
    state.snapshot = data.snapshot || "";
    $("#page-number").textContent = `第 ${page} 页`;
    $("#result-count").textContent = `共 ${data.total} 张 · 本页 ${data.items.length} 张`;
    $("#previous").disabled = page <= 1;
    $("#next").disabled = !data.has_next;
    renderCards();
    return true;
  } catch (error) {
    if (generation !== state.generation) return;
    $("#empty-title").textContent = "暂时无法读取表情";
    $("#empty-description").textContent = error.message;
    notice(`读取失败：${error.message}`, "error");
    return false;
  } finally {
    if (generation === state.generation) {
      state.loading = false;
      $("#gallery").setAttribute("aria-busy", "false");
      updateSelection();
    }
  }
}

function renderJob(job) {
  const wasRunning = state.job?.id === job.id && state.job.state === "running";
  state.job = job;
  $("#job-panel").hidden = false;
  const names = { running: job.cancel_requested ? "正在取消，等待当前图片处理完成" : "正在缓存", completed: job.failed ? "已完成，部分图片未成功" : "缓存完成", cancelled: "任务已取消", failed: "任务中断，已成功的图片已保留" };
  $("#job-state").textContent = names[job.state] || job.state;
  $("#job-counts").textContent = `已处理 ${job.done} 张（上限 ${job.total}） · 成功 ${job.success} · 失败 ${job.failed}`;
  $("#job-progress").value = job.state === "completed" ? 100 : Math.min(100, job.done / Math.max(1, job.total) * 100);
  $("#cancel-job").disabled = job.state !== "running" || job.cancel_requested;
  $("#job-errors").replaceChildren(...job.errors.map((message) => element("li", "", message)));
  clearTimeout(state.poll);
  if (job.state === "running") state.poll = setTimeout(() => pollJob(job.id), 1500);
  else if (wasRunning) refreshAfterWrite(`任务结束：成功 ${job.success} 张，失败 ${job.failed} 张。`);
  updateSelection();
}

async function pollJob(id) {
  try { renderJob(await bridge.apiGet("admin/jobs/status", { id })); }
  catch (error) {
    notice(`暂时无法读取任务进度：${error.message}。任务可能仍在运行，可刷新重新连接。`, "warning");
    state.poll = setTimeout(() => pollJob(id), 5000);
  }
}

async function refreshAfterWrite(message) {
  notice(message, "success");
  try {
    await loadOverview();
    if (state.tab === "local" && !await loadItems(state.page)) notice(`${message} 刷新图库失败，请稍后手动刷新。`, "warning");
  } catch {
    notice(`${message} 刷新失败，请稍后手动刷新图库。`, "warning");
  }
}

async function startJob(body) {
  if (state.applying) return;
  state.applying = true;
  updateSelection();
  try {
    const requestId = [...crypto.getRandomValues(new Uint8Array(16))].map((value) => value.toString(16).padStart(2, "0")).join("");
    const job = await bridge.apiPost("admin/jobs/start", { ...body, category: $("#target-category").value, request_id: requestId });
    renderJob(job);
    notice("缓存任务已开始，进度会自动更新。", "success");
    $("#job-heading").focus({ preventScroll: true });
    $("#job-panel").scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  } catch (error) { notice(`无法开始缓存：${error.message}`, "error"); }
  finally { state.applying = false; updateSelection(); }
}

function startSelectedJob(ids) {
  return startJob({ mode: "selected", snapshot: state.snapshot, ids });
}

function openEditor(item) {
  state.editId = item.id;
  $("#edit-title").textContent = item.title;
  $("#edit-form textarea").value = item.description;
  $("#edit-form .form-error").textContent = "";
  $("#edit-dialog").showModal();
}

async function classify(ids, category, description = "") {
  const result = await bridge.apiPost("admin/classify", { ids, category, description });
  return `已整理 ${result.saved} 张表情。${result.error ? ` 部分未保存：${result.error}` : ""}`;
}

async function changeTab(tab) {
  state.tab = tab;
  for (const button of document.querySelectorAll("[data-tab]")) {
    const current = button.dataset.tab === tab;
    button.setAttribute("aria-selected", String(current));
    button.tabIndex = current ? 0 : -1;
  }
  $("#local-panel").hidden = tab !== "local";
  $("#remote-panel").hidden = tab !== "remote";
  $("#source-note").textContent = tab === "local" ? "人物与用途，可以一起筛选" : "挑选喜欢的表情，再存入本地";
  if (tab === "remote" && !state.siteReady) await loadSiteCatalog();
  if (state.tab !== tab) return;
  if (tab === "remote" && !Object.keys(state.filters.remote).length) {
    resetRemoteGallery("去小站挑选喜欢的表情", "先选人物或画师分类，再选择表情包；也可填写条件直接搜索图片。");
    return;
  }
  await loadItems();
}

$("#local-form").addEventListener("submit", (event) => { event.preventDefault(); state.filters.local = formValues(event.currentTarget); loadItems(); });
$("#remote-form").addEventListener("submit", (event) => { event.preventDefault(); state.filters.remote = formValues(event.currentTarget); loadItems(); });
$("#site-catalog-form").addEventListener("submit", (event) => { event.preventDefault(); loadSiteCatalog(); });
$("#site-catalog-form [name=kind]").addEventListener("change", () => loadSiteCatalog());
$("#catalog-refresh").addEventListener("click", () => loadSiteCatalog(state.catalogPage, true));
$("#catalog-previous").addEventListener("click", () => loadSiteCatalog(state.catalogPage - 1));
$("#catalog-next").addEventListener("click", () => loadSiteCatalog(state.catalogPage + 1));
$("#packs-previous").addEventListener("click", () => loadSitePacks(state.packsPage - 1));
$("#packs-next").addEventListener("click", () => loadSitePacks(state.packsPage + 1));
$("#browse-category").addEventListener("click", () => {
  $("#remote-form [name=pack]").value = "";
  state.pack = null;
  $("#selected-pack").hidden = true;
  state.filters.remote = formValues($("#remote-form"));
  loadItems();
});
$("#clear-pack").addEventListener("click", () => {
  state.pack = null;
  $("#remote-form [name=pack]").value = "";
  $("#selected-pack").hidden = true;
  $("#site-directory").open = true;
  state.filters.remote = formValues($("#remote-form"));
  resetRemoteGallery("请选择表情包", "从当前分类的表情包列表中选取，或浏览分类全部表情。");
});
$("#remote-form [name=pack]").addEventListener("input", () => { state.pack = null; $("#selected-pack").hidden = true; updateSelection(); });
$("#cache-pack").addEventListener("click", () => {
  if (state.pack) startJob({ mode: "pack", pack: state.pack.id, maximum: Math.min(100, Math.max(1, Number(state.pack.count) || 100)) });
});
$("#play-animations").addEventListener("change", (event) => {
  state.playAnimations = event.target.checked;
  for (const image of document.querySelectorAll(".image-area img")) {
    const data = previewData.get(image);
    if (data) applyAnimation(image, data);
  }
});
for (const button of document.querySelectorAll("[data-tab]")) {
  button.addEventListener("click", () => changeTab(button.dataset.tab));
  button.addEventListener("keydown", (event) => { if (["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); const other = state.tab === "local" ? "remote" : "local"; $(`#tab-${other}`).focus(); changeTab(other); } });
}
$("#select-all").addEventListener("click", () => { state.items.forEach((item) => state.selected.add(item.id)); updateSelection(); });
$("#clear-selection").addEventListener("click", () => { state.selected.clear(); updateSelection(); });
$("#previous").addEventListener("click", () => loadItems(state.page - 1));
$("#next").addEventListener("click", () => loadItems(state.page + 1));
$("#refresh").addEventListener("click", async () => { try { await loadOverview(); if (await loadItems(state.page) && state.enabled) notice("图库已刷新。"); } catch (error) { notice(`刷新失败：${error.message}`, "error"); } });
$("#apply-selection").addEventListener("click", async () => {
  const ids = [...state.selected];
  if (state.tab === "remote") return startSelectedJob(ids);
  state.applying = true;
  updateSelection();
  try { await refreshAfterWrite(await classify(ids, $("#target-category").value)); }
  catch (error) { notice(`归类失败：${error.message}`, "error"); }
  finally { state.applying = false; updateSelection(); }
});
$("#batch-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const values = formValues(event.currentTarget);
  startJob({ mode: "query", filters: formValues($("#remote-form")), start_page: Number(values.start_page), pages: Number(values.pages), maximum: Number(values.maximum) });
});
$("#cancel-job").addEventListener("click", async () => {
  try { renderJob(await bridge.apiPost("admin/jobs/cancel", { id: state.job.id })); }
  catch (error) { notice(`取消请求失败：${error.message}`, "error"); }
});
$("#open-category").addEventListener("click", () => { $("#category-form").reset(); $("#category-form .form-error").textContent = ""; $("#category-dialog").showModal(); });
for (const button of document.querySelectorAll(".close-dialog")) button.addEventListener("click", () => button.closest("dialog").close());
$("#category-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    await bridge.apiPost("admin/categories", formValues(event.currentTarget));
    $("#category-dialog").close();
    await refreshAfterWrite("用途分类已保存，AstrBot 可通过说明理解其使用场景。");
  } catch (error) { $("#category-form .form-error").textContent = error.message; }
  finally { button.disabled = false; }
});
$("#edit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    const values = formValues(event.currentTarget);
    const message = await classify([state.editId], values.category, values.description);
    $("#edit-dialog").close();
    await refreshAfterWrite(message);
  } catch (error) { $("#edit-form .form-error").textContent = error.message; }
  finally { button.disabled = false; }
});
window.addEventListener("beforeunload", () => { clearTimeout(state.poll); ++state.generation; visibility.disconnect(); });

try {
  if (!bridge) throw new Error("请在 AstrBot 插件详情中打开“表情图库”管理视图。");
  const context = await bridge.ready();
  document.documentElement.dataset.theme = context.isDark ? "dark" : "light";
  bridge.onContext?.((next) => { document.documentElement.dataset.theme = next.isDark ? "dark" : "light"; });
  await loadOverview();
  await loadItems();
  if (state.enabled) notice("图库已就绪。前往“小站搜索”，可单张收藏或批量缓存。");
} catch (error) {
  notice(error.message, "error");
  $("#empty-title").textContent = "暂未连接到 AstrBot";
  $("#empty-description").textContent = "请从 AstrBot 插件详情页进入此视图，并确认已登录。";
}
