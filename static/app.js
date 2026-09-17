const el = (id) => document.getElementById(id);

const GROUPED_PAGE_SIZE = 20;

const state = {
  mode: "normal", // "normal" (server-paginated) or "grouped" (species-count filtered, paginated client-side)
  filters: null,
  results: [],
  allResults: [], // grouped mode only: the full flattened, already-filtered set
  total: 0,
  pages: 1,
  currentPage: 1,
  selectedIds: new Set(),
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
  if (checked) state.selectedIds.add(id);
  else state.selectedIds.delete(id);
  const row = document.querySelector(`tr[data-id="${id}"]`);
  if (row) row.classList.toggle("selected", checked);
  updateSelectionUI();
}

function updateSelectionUI() {
  const n = state.selectedIds.size;
  el("selection-count").textContent = n > 0 ? `${n} selected` : "";
  el("delete-btn").disabled = n === 0;
}

function selectPage() {
  for (const r of state.results) state.selectedIds.add(r.id);
  render();
}

async function selectAllMatching() {
  if (state.mode === "grouped") {
    state.selectedIds = new Set(state.allResults.map((r) => r.id));
    render();
    showToast(`Selected ${state.selectedIds.size} matching detections`);
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
    state.selectedIds = new Set(data.ids || []);
    render();
    showToast(`Selected ${state.selectedIds.size} matching detections`);
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

function openConfirm() {
  const n = state.selectedIds.size;
  if (n === 0) return;
  el("confirm-text").textContent =
    `Delete ${n} detection${n === 1 ? "" : "s"}? This cannot be undone.`;
  el("confirm-modal").hidden = false;
}

function closeConfirm() {
  el("confirm-modal").hidden = true;
}

async function doDelete() {
  closeConfirm();
  const ids = Array.from(state.selectedIds);
  el("delete-btn").disabled = true;
  try {
    const resp = await fetch("/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    if (!resp.ok) throw new Error(`Delete failed (${resp.status})`);
    const data = await resp.json();
    state.selectedIds.clear();
    showToast(`Deleted ${data.deleted}, failed ${data.failed}`);
    if (state.mode === "grouped") {
      const failedSet = new Set((data.failed_ids || []).map(String));
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
    showToast(err.message);
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
});

el("select-page-btn").addEventListener("click", selectPage);
el("select-all-btn").addEventListener("click", selectAllMatching);
el("clear-selection-btn").addEventListener("click", clearSelection);
el("delete-btn").addEventListener("click", openConfirm);
el("confirm-cancel").addEventListener("click", closeConfirm);
el("confirm-ok").addEventListener("click", doDelete);
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

loadSources();
