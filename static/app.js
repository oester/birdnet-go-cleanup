const el = (id) => document.getElementById(id);

const GROUPED_PAGE_SIZE = 20;

// Overwritten by loadConfig() from /api/config on startup — these are just
// fallbacks in case that fetch fails, and should match app.py's constants.
const config = {
  maxSelection: 500,
  deleteBatchSize: 25,
  deleteThrottleSeconds: 0.05,
};

const state = {
  mode: "normal", // "normal" (server-paginated) or "grouped" (species-count filtered, paginated client-side)
  filters: null,
  results: [],
  allResults: [], // grouped mode only: the full flattened, already-filtered set
  total: 0,
  pages: 1,
  currentPage: 1,
  selectedIds: new Set(),
  speciesById: new Map(), // id -> commonName, for any detection we've ever fetched full data for
  summaries: [],
  summarySort: { key: "count", asc: true },
};

function viewMode() {
  return document.querySelector('input[name="view-mode"]:checked').value;
}

function gatherFilters() {
  const confMin = Number(el("f-conf-min").value || 0) / 100;
  const confMax = Number(el("f-conf-max").value || 100) / 100;
  return {
    species: el("f-species").value.trim(),
    speciesScientific: [],
    dateStart: el("f-date-start").value || "",
    dateEnd: el("f-date-end").value || "",
    confidenceMin: Math.min(confMin, confMax),
    confidenceMax: Math.max(confMin, confMax),
    verifiedStatus: el("f-verified").value,
    lockedStatus: el("f-locked").value,
    deviceFilter: el("f-device").value.trim(),
    timeOfDay: el("f-tod").value,
    sortBy: el("f-sort").value,
  };
}

function gatherCountFilter() {
  const op = el("f-count-op").value;
  const raw = el("f-count-value").value;
  if (!op || raw === "") return null;
  const value = Math.floor(Number(raw));
  if (!Number.isFinite(value) || value < 0) return null;
  return { countOp: op, countValue: value };
}

function showToast(msg, ms = 4000) {
  const t = el("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => { t.hidden = true; }, ms);
}

async function runSearch(page = 1) {
  if (viewMode() === "summary") {
    await runSummarySearch();
    return;
  }
  state.filters = gatherFilters();
  const countFilter = gatherCountFilter();
  const btn = el("search-btn");
  btn.disabled = true;
  try {
    if (countFilter) {
      const resp = await fetch("/api/search_by_count", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...state.filters, ...countFilter }),
      });
      if (!resp.ok) throw new Error(`Search failed (${resp.status})`);
      const data = await resp.json();
      state.mode = "grouped";
      state.allResults = data.results || [];
      for (const r of state.allResults) state.speciesById.set(r.id, r.commonName);
      state.total = data.total || 0;
      state.pages = Math.max(1, Math.ceil(state.allResults.length / GROUPED_PAGE_SIZE));
      state.currentPage = Math.min(Math.max(1, page), state.pages);
      if (data.truncated) {
        showToast(
          "Swept the maximum number of pages without finishing — narrow the date range or other filters for complete results.",
          7000
        );
      }
      applyGroupedPage();
    } else {
      const resp = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...state.filters, page }),
      });
      if (!resp.ok) throw new Error(`Search failed (${resp.status})`);
      const data = await resp.json();
      state.mode = "normal";
      state.allResults = [];
      state.results = data.results || [];
      state.total = data.total || 0;
      state.pages = data.pages || 1;
      state.currentPage = data.currentPage || page;
      render();
    }
  } catch (err) {
    showToast(err.message);
  } finally {
    btn.disabled = false;
  }
}

function applyGroupedPage() {
  const start = (state.currentPage - 1) * GROUPED_PAGE_SIZE;
  state.results = state.allResults.slice(start, start + GROUPED_PAGE_SIZE);
  render();
}

async function runSummarySearch() {
  state.filters = gatherFilters();
  const countFilter = gatherCountFilter();
  const btn = el("search-btn");
  btn.disabled = true;
  try {
    const resp = await fetch("/api/search_summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...state.filters, ...(countFilter || {}) }),
    });
    if (!resp.ok) throw new Error(`Search failed (${resp.status})`);
    const data = await resp.json();
    state.summaries = data.summaries || [];
    if (data.truncated) {
      showToast(
        "Swept the maximum number of pages without finishing — narrow the date range or other filters for complete results.",
        7000
      );
    }
    renderSummary(data);
  } catch (err) {
    showToast(err.message);
  } finally {
    btn.disabled = false;
  }
}

function sortedSummaries() {
  const { key, asc } = state.summarySort;
  const rows = [...state.summaries];
  rows.sort((a, b) => {
    const av = a[key] ?? (typeof a[key] === "number" ? 0 : "");
    const bv = b[key] ?? (typeof b[key] === "number" ? 0 : "");
    if (av < bv) return asc ? -1 : 1;
    if (av > bv) return asc ? 1 : -1;
    return 0;
  });
  return rows;
}

function renderSummary(data) {
  el("results-section").hidden = true;
  el("summary-section").hidden = false;
  el("summary-info").textContent =
    `${data.speciesCount} species, ${data.totalDetections} detections matching the current filters`;

  const body = el("summary-body");
  body.innerHTML = "";
  for (const s of sortedSummaries()) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${s.commonName ?? ""} <em>(${s.scientificName ?? ""})</em></td>
      <td>${s.count}</td>
      <td>${fmtTime(s.firstSeen)}</td>
      <td>${fmtTime(s.lastSeen)}</td>
      <td>${Math.round((s.avgConfidence ?? 0) * 100)}%</td>
      <td><button type="button" class="view-detections-btn">View detections</button></td>
    `;
    tr.querySelector(".view-detections-btn").addEventListener("click", () => {
      viewSpeciesDetections(s.commonName);
    });
    body.appendChild(tr);
  }

  document.querySelectorAll("#summary-table th[data-sort]").forEach((th) => {
    th.textContent = th.textContent.replace(/ [▲▼]$/, "");
    if (th.dataset.sort === state.summarySort.key) {
      th.textContent += state.summarySort.asc ? " ▲" : " ▼";
    }
  });
}

function viewSpeciesDetections(commonName) {
  el("f-species").value = commonName;
  el("f-count-op").value = "";
  el("f-count-value").value = "";
  el("f-view-individual").checked = true;
  state.selectedIds.clear();
  runSearch(1);
}

document.querySelectorAll("#summary-table th[data-sort]").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    if (state.summarySort.key === key) {
      state.summarySort.asc = !state.summarySort.asc;
    } else {
      state.summarySort = { key, asc: true };
    }
    renderSummary({
      speciesCount: state.summaries.length,
      totalDetections: state.summaries.reduce((sum, s) => sum + s.count, 0),
    });
  });
});

function fmtTime(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function render() {
  el("results-section").hidden = false;
  el("summary-section").hidden = true;
  const body = el("results-body");
  body.innerHTML = "";
  for (const r of state.results) {
    state.speciesById.set(r.id, r.commonName);
    const tr = document.createElement("tr");
    tr.dataset.id = r.id;
    if (state.selectedIds.has(r.id)) tr.classList.add("selected");
    tr.innerHTML = `
      <td><input type="checkbox" class="row-check" ${state.selectedIds.has(r.id) ? "checked" : ""}></td>
      <td>${fmtTime(r.timestamp)}</td>
      <td>${r.commonName ?? ""}</td>
      <td><em>${r.scientificName ?? ""}</em></td>
      <td>${Math.round((r.confidence ?? 0) * 100)}%</td>
      <td>${r.verified ?? ""}</td>
      <td>${r.source ?? r.device ?? ""}</td>
      <td>${r.hasAudio ? "♪" : ""}</td>
      <td>${r.speciesCount ?? ""}</td>
    `;
    tr.querySelector(".row-check").addEventListener("change", (e) => {
      toggleSelection(r.id, e.target.checked);
    });
    body.appendChild(tr);
  }
  el("result-summary").textContent = `${state.total} matching detection${state.total === 1 ? "" : "s"}`;
  el("page-indicator").textContent = `Page ${state.currentPage} of ${state.pages}`;
  el("prev-page-btn").disabled = state.currentPage <= 1;
  el("next-page-btn").disabled = state.currentPage >= state.pages;
  updateSelectionUI();
}

function toggleSelection(id, checked) {
  if (checked) {
    if (state.selectedIds.size >= config.maxSelection) {
      showToast(`Selection is capped at ${config.maxSelection} — deselect something else first.`);
      const row = document.querySelector(`tr[data-id="${id}"]`);
      const cb = row?.querySelector(".row-check");
      if (cb) cb.checked = false;
      return;
    }
    state.selectedIds.add(id);
  } else {
    state.selectedIds.delete(id);
  }
  const row = document.querySelector(`tr[data-id="${id}"]`);
  if (row) row.classList.toggle("selected", checked);
  updateSelectionUI();
}

// Sets the selection to `ids`, capped at config.maxSelection. Used by
// "select all matching," which can otherwise pull in far more than the
// cap allows in one shot.
function applyCappedSelection(ids) {
  state.selectedIds = new Set(ids.slice(0, config.maxSelection));
  render();
  if (ids.length > config.maxSelection) {
    showToast(
      `Selected ${config.maxSelection} of ${ids.length} matching detections — ${config.maxSelection} is the maximum at once. Narrow your filters to handle the rest.`,
      7000
    );
  } else {
    showToast(`Selected ${state.selectedIds.size} matching detections`);
  }
}

function updateSelectionUI() {
  const n = state.selectedIds.size;
  el("selection-count").textContent = n > 0 ? `${n} selected` : "";
  el("delete-btn").disabled = n === 0;
  el("exclude-btn").disabled = n === 0;
}

function selectPage() {
  let hitCap = false;
  for (const r of state.results) {
    if (state.selectedIds.size >= config.maxSelection) {
      hitCap = true;
      break;
    }
    state.selectedIds.add(r.id);
  }
  render();
  if (hitCap) {
    showToast(`Selection is capped at ${config.maxSelection} — deselect something else first.`);
  }
}

async function selectAllMatching() {
  if (state.mode === "grouped") {
    applyCappedSelection(state.allResults.map((r) => r.id));
    return;
  }
  const btn = el("select-all-btn");
  btn.disabled = true;
  try {
    const resp = await fetch("/api/search_all_ids", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.filters),
    });
    if (!resp.ok) throw new Error(`Lookup failed (${resp.status})`);
    const data = await resp.json();
    for (const [id, name] of Object.entries(data.commonNames || {})) {
      if (name) state.speciesById.set(id, name);
    }
    applyCappedSelection(data.ids || []);
  } catch (err) {
    showToast(err.message);
  } finally {
    btn.disabled = false;
  }
}

function clearSelection() {
  state.selectedIds.clear();
  render();
}

let pendingConfirmAction = null;

function openConfirmDialog(text, action) {
  el("confirm-text").textContent = text;
  pendingConfirmAction = action;
  el("confirm-modal").hidden = false;
}

function closeConfirm() {
  el("confirm-modal").hidden = true;
  pendingConfirmAction = null;
}

const LARGE_DELETE_THRESHOLD = 50;

function openDeleteConfirm() {
  const n = state.selectedIds.size;
  if (n === 0) return;
  const s = n === 1 ? "" : "s";
  if (n < LARGE_DELETE_THRESHOLD) {
    openConfirmDialog(
      `Delete ${n} detection${s}? This also removes their audio clips/spectrograms and cannot be undone.`,
      doDelete
    );
    return;
  }
  const etaSeconds = Math.round(n * config.deleteThrottleSeconds);
  openConfirmDialog(
    `⚠️ Delete ${n} detections? This also removes their audio clips/spectrograms and cannot be undone. ` +
      `A batch this size will take at least ~${etaSeconds}s and generates real database and file I/O on ` +
      `your BirdNET-Go server, possibly alongside live detections being written at the same time — ` +
      `narrow your filters first if you don't need all ${n} gone right now.`,
    doDelete
  );
}

function distinctSelectedSpecies() {
  const names = new Set();
  for (const id of state.selectedIds) {
    const name = state.speciesById.get(id);
    if (name) names.add(name);
  }
  return Array.from(names);
}

function openExcludeConfirm() {
  const names = distinctSelectedSpecies();
  const unresolved = [...state.selectedIds].filter((id) => !state.speciesById.has(id)).length;
  if (names.length === 0) {
    showToast("Could not determine the species for the current selection.");
    return;
  }
  const label = names.length === 1 ? `"${names[0]}"` : `${names.length} species (${names.join(", ")})`;
  const gap = unresolved > 0
    ? ` (${unresolved} selected detection${unresolved === 1 ? "" : "s"} had an unknown species and will be skipped)`
    : "";
  openConfirmDialog(
    `Add ${label} to the server's excluded-species list?${gap} This stops new detections of ` +
      `${names.length === 1 ? "it" : "them"} from being recorded going forward — it does not delete anything.`,
    doExcludeSelected
  );
}

async function doExcludeSelected() {
  const names = distinctSelectedSpecies();
  if (names.length === 0) return;
  el("exclude-btn").disabled = true;
  try {
    const resp = await fetch("/api/exclude_species", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ commonNames: names }),
    });
    if (!resp.ok) throw new Error(`Exclude failed (${resp.status})`);
    const data = await resp.json();
    const parts = [];
    if (data.added?.length) parts.push(`excluded: ${data.added.join(", ")}`);
    if (data.alreadyExcluded?.length) parts.push(`already excluded: ${data.alreadyExcluded.join(", ")}`);
    if (data.failed?.length) parts.push(`failed: ${data.failed.join(", ")}`);
    showToast(parts.join(" · ") || "No changes", 6000);
  } catch (err) {
    showToast(err.message);
  } finally {
    updateSelectionUI();
  }
}

function setDeleteControlsDisabled(disabled) {
  for (const id of ["search-btn", "reset-btn", "select-page-btn", "select-all-btn",
                     "clear-selection-btn", "exclude-btn", "delete-btn",
                     "prev-page-btn", "next-page-btn"]) {
    el(id).disabled = disabled;
  }
  if (!disabled) updateSelectionUI(); // restores delete/exclude's normal enabled-when-selected state
}

function showDeleteProgress(done, total, failed) {
  const box = el("delete-progress");
  box.hidden = false;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  el("delete-progress-fill").style.width = `${pct}%`;
  el("delete-progress-text").textContent =
    `Deleting ${done} of ${total}${failed > 0 ? ` (${failed} failed)` : ""}…`;
}

function hideDeleteProgress() {
  el("delete-progress").hidden = true;
}

// Sends the selected ids to the server in config.deleteBatchSize chunks
// (server-side, each chunk is also throttled between individual deletes —
// see DELETE_THROTTLE_SECONDS in app.py) instead of one giant request, so:
// the UI can show real progress instead of freezing until everything is
// done, and an interruption (closed tab, network drop) only loses the
// in-flight batch rather than silently losing track of the whole operation.
async function doDelete() {
  const ids = Array.from(state.selectedIds);
  setDeleteControlsDisabled(true);
  let deleted = 0;
  let failed = 0;
  const failedIds = [];
  showDeleteProgress(0, ids.length, 0);
  try {
    for (let i = 0; i < ids.length; i += config.deleteBatchSize) {
      const batch = ids.slice(i, i + config.deleteBatchSize);
      const resp = await fetch("/api/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: batch }),
      });
      if (!resp.ok) throw new Error(`Delete failed (${resp.status}) after ${deleted} of ${ids.length}`);
      const data = await resp.json();
      deleted += data.deleted || 0;
      failed += data.failed || 0;
      failedIds.push(...(data.failed_ids || []));
      showDeleteProgress(deleted + failed, ids.length, failed);
    }
    state.selectedIds.clear();
    showToast(`Deleted ${deleted}, failed ${failed}`, 6000);
    if (state.mode === "grouped") {
      const failedSet = new Set(failedIds.map(String));
      const attempted = new Set(ids);
      state.allResults = state.allResults.filter(
        (r) => !attempted.has(r.id) || failedSet.has(r.id)
      );
      state.total = state.allResults.length;
      state.pages = Math.max(1, Math.ceil(state.allResults.length / GROUPED_PAGE_SIZE));
      state.currentPage = Math.min(state.currentPage, state.pages);
      applyGroupedPage();
    } else {
      await runSearch(state.currentPage);
    }
  } catch (err) {
    showToast(`${err.message} — ${deleted} deleted so far, stopped early.`, 8000);
  } finally {
    hideDeleteProgress();
    setDeleteControlsDisabled(false);
  }
}

el("filters").addEventListener("submit", (e) => {
  e.preventDefault();
  state.selectedIds.clear();
  runSearch(1);
});

el("reset-btn").addEventListener("click", () => {
  el("filters").reset();
  el("f-conf-min").value = 0;
  el("f-conf-max").value = 100;

  state.mode = "normal";
  state.filters = null;
  state.results = [];
  state.allResults = [];
  state.summaries = [];
  state.total = 0;
  state.pages = 1;
  state.currentPage = 1;
  state.selectedIds.clear();

  el("results-section").hidden = true;
  el("summary-section").hidden = true;
  el("results-body").innerHTML = "";
  el("summary-body").innerHTML = "";
});

el("select-page-btn").addEventListener("click", selectPage);
el("select-all-btn").addEventListener("click", selectAllMatching);
el("clear-selection-btn").addEventListener("click", clearSelection);
el("delete-btn").addEventListener("click", openDeleteConfirm);
el("exclude-btn").addEventListener("click", openExcludeConfirm);
el("confirm-cancel").addEventListener("click", closeConfirm);
el("confirm-ok").addEventListener("click", () => {
  const action = pendingConfirmAction;
  closeConfirm();
  if (action) action();
});
el("prev-page-btn").addEventListener("click", () => {
  if (state.mode === "grouped") {
    state.currentPage = Math.max(1, state.currentPage - 1);
    applyGroupedPage();
  } else {
    runSearch(state.currentPage - 1);
  }
});
el("next-page-btn").addEventListener("click", () => {
  if (state.mode === "grouped") {
    state.currentPage = Math.min(state.pages, state.currentPage + 1);
    applyGroupedPage();
  } else {
    runSearch(state.currentPage + 1);
  }
});

async function loadConfig() {
  try {
    const resp = await fetch("/api/config");
    if (!resp.ok) throw new Error(`Config failed (${resp.status})`);
    const data = await resp.json();
    Object.assign(config, data);
  } catch (err) {
    showToast(`Couldn't load config, using defaults: ${err.message}`);
  }
}

async function loadSources() {
  try {
    const resp = await fetch("/api/sources");
    if (!resp.ok) throw new Error(`Sources failed (${resp.status})`);
    const data = await resp.json();
    const select = el("f-device");
    for (const name of data.sources || []) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    }
  } catch (err) {
    showToast(`Couldn't load sources: ${err.message}`);
  }
}

loadConfig();
loadSources();
