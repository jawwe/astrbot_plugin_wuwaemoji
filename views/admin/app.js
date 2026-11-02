const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const $ = (selector) => document.querySelector(selector);
const state = { tab: "local", page: 1, items: [], selected: new Set(), snapshot: "", catalog: null, enabled: true, job: null, generation: 0, loading: false, applying: false, editId: "", poll: null, filters: { local: {}, remote: {} } };

function notice(text, kind = "") {
  $("#notice").textContent = text;
  $("#notice").className = `notice ${kind}`;
}

function formValues(form) {
  return Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, value.trim()]));
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
}

function renderCards() {
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
    const badge = element("span", "format-badge", item.animated ? "动态 · 缩略图" : (item.format || "图片").toUpperCase());
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
    if (typeof data.data_url !== "string" || !data.data_url.startsWith("data:image/webp;base64,")) throw new Error("预览格式无效");
    const image = card.querySelector("img");
    image.onload = () => { status.hidden = true; image.classList.add("loaded"); };
    image.src = data.data_url;
    if (data.animated) card.querySelector(".format-badge").textContent = "动态 · 缩略图";
  } catch {
    if (generation !== state.generation || !card.isConnected) return;
    status.replaceChildren(document.createTextNode("暂时无法预览"));
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
  if (tab === "remote" && !Object.keys(state.filters.remote).length) {
    ++state.generation;
    state.page = 1;
    state.snapshot = "";
    $("#page-number").textContent = "第 1 页";
    state.items = [];
    state.selected.clear();
    state.loading = false;
    $("#gallery").setAttribute("aria-busy", "false");
    renderCards();
    $("#empty-title").textContent = "去小站挑选喜欢的表情";
    $("#empty-description").textContent = "填写人物或其他条件开始搜索，也可直接搜索浏览全部。";
    $("#previous").disabled = true;
    $("#next").disabled = true;
    $("#result-count").textContent = "尚未搜索";
    return;
  }
  await loadItems();
}

$("#local-form").addEventListener("submit", (event) => { event.preventDefault(); state.filters.local = formValues(event.currentTarget); loadItems(); });
$("#remote-form").addEventListener("submit", (event) => { event.preventDefault(); state.filters.remote = formValues(event.currentTarget); loadItems(); });
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
window.addEventListener("beforeunload", () => { clearTimeout(state.poll); ++state.generation; });

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
