/* EHR Media Intelligence — vanilla JS frontend.
   All dynamic text is inserted via textContent (no innerHTML) so untrusted
   clinical text can't inject markup. */

"use strict";

const state = {
  query: "",
  resourceType: "",
  dateFrom: "",
  dateTo: "",
  topK: 5,
  searched: false,
};

const els = {
  form: document.getElementById("search-form"),
  query: document.getElementById("query"),
  btn: document.getElementById("search-btn"),
  type: document.getElementById("filter-type"),
  from: document.getElementById("filter-from"),
  to: document.getElementById("filter-to"),
  clear: document.getElementById("clear-filters"),
  results: document.getElementById("results"),
  resultsMeta: document.getElementById("results-meta"),
  empty: document.getElementById("empty-state"),
  emptyTitle: document.getElementById("empty-title"),
  emptyBody: document.getElementById("empty-body"),
  loading: document.getElementById("loading"),
  statusDot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  drawer: document.getElementById("drawer"),
  drawerBackdrop: document.getElementById("drawer-backdrop"),
  drawerClose: document.getElementById("drawer-close"),
  drawerTitle: document.getElementById("drawer-title"),
  drawerSubtitle: document.getElementById("drawer-subtitle"),
  drawerBody: document.getElementById("drawer-body"),
};

/* ---------- helpers ---------- */

const TYPE_STYLES = {
  lab: "bg-blue-100 text-blue-800",
  imaging: "bg-purple-100 text-purple-800",
  discharge_summary: "bg-emerald-100 text-emerald-800",
  clinical_note: "bg-slate-200 text-slate-700",
};
const TYPE_LABELS = {
  lab: "Lab",
  imaging: "Imaging",
  discharge_summary: "Discharge",
  clinical_note: "Note",
};

function el(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = text;
  return n;
}

function badge(text, cls) {
  return el("span",
    `inline-block rounded-full px-2 py-0.5 text-xs font-medium ${cls}`,
    text);
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function setStatus(ok, text) {
  els.statusDot.className = `inline-block w-2.5 h-2.5 rounded-full ${ok ? "bg-emerald-500" : "bg-red-500"}`;
  els.statusText.textContent = text;
}

function showLoading(on) {
  els.loading.classList.toggle("hidden", !on);
  els.results.classList.toggle("hidden", on);
  els.empty.classList.toggle("hidden", on || !state.searched);
  els.btn.disabled = on;
}

function showEmpty(title, body) {
  els.results.classList.add("hidden");
  els.loading.classList.add("hidden");
  els.empty.classList.remove("hidden");
  els.emptyTitle.textContent = title;
  els.emptyBody.textContent = body;
}

/* ---------- search ---------- */

async function runSearch() {
  state.query = els.query.value.trim();
  state.resourceType = els.type.value;
  state.dateFrom = els.from.value;
  state.dateTo = els.to.value;
  state.searched = true;

  if (!state.query) {
    showEmpty("Enter a search query", "Describe what you're looking for in plain language.");
    els.resultsMeta.textContent = "";
    return;
  }

  showLoading(true);
  els.resultsMeta.textContent = "";
  try {
    const body = {
      query: state.query,
      top_k: state.topK,
      ...(state.resourceType && { resource_type: state.resourceType }),
      ...(state.dateFrom && { date_from: state.dateFrom }),
      ...(state.dateTo && { date_to: state.dateTo }),
    };
    const data = await api("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    renderResults(data);
  } catch (err) {
    showEmpty("Search failed", `Could not reach the search service (${err.message}). Is the backend running?`);
  } finally {
    showLoading(false);
    if (state.searched && els.results.children.length) {
      els.results.classList.remove("hidden");
      els.empty.classList.add("hidden");
    }
  }
}

function renderResults(data) {
  els.results.innerHTML = "";
  els.resultsMeta.textContent = data.count
    ? `${data.count} result${data.count === 1 ? "" : "s"} for “${data.query}”`
    : "";

  if (!data.count) {
    showEmpty("No matching records", "Try different terms or widen the date range / record type filter.");
    return;
  }

  data.results.forEach((r, i) => {
    const card = el("article",
      "bg-white rounded-xl border border-slate-200 p-4 shadow-sm hover:border-teal-400 hover:shadow transition cursor-pointer focus:outline-none focus:ring-2 focus:ring-teal-500",
    );
    card.setAttribute("role", "listitem");
    card.tabIndex = 0;
    card.setAttribute("aria-label",
      `${r.patient_name || "Patient"}, ${TYPE_LABELS[r.resource_type] || r.resource_type}, ${r.record_date}, relevance ${Math.round(r.score * 100)} percent`);

    // top row: name + badges
    const top = el("div", "flex flex-wrap items-center gap-2 mb-1.5");
    top.appendChild(el("span", "font-semibold text-slate-900", r.patient_name || "Unknown patient"));
    if (r.mrn) top.appendChild(badge(r.mrn, "bg-slate-100 text-slate-600"));
    top.appendChild(badge(TYPE_LABELS[r.resource_type] || r.resource_type,
      TYPE_STYLES[r.resource_type] || "bg-slate-200 text-slate-700"));
    top.appendChild(el("span", "text-xs text-slate-400 ml-auto", r.record_date || ""));

    // score bar
    const scoreWrap = el("div", "flex items-center gap-2 mb-2");
    const bar = el("div", "h-1.5 flex-1 bg-slate-100 rounded-full overflow-hidden");
    const fill = el("div", "h-full bg-teal-500 rounded-full");
    fill.style.width = `${Math.round(r.score * 100)}%`;
    bar.appendChild(fill);
    scoreWrap.appendChild(bar);
    scoreWrap.appendChild(el("span", "text-xs font-medium text-slate-500 w-10 text-right",
      `${Math.round(r.score * 100)}%`));

    // snippet
    const snippet = el("p", "text-sm text-slate-600 line-clamp-2", r.snippet || "");

    card.append(top, scoreWrap, snippet);
    card.addEventListener("click", () => openPatient(r.patient_key, r.patient_name));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openPatient(r.patient_key, r.patient_name); }
    });
    els.results.appendChild(card);
  });
}

/* ---------- patient drawer ---------- */

async function openPatient(key, name) {
  els.drawerTitle.textContent = name || "Patient";
  els.drawerSubtitle.textContent = "";
  els.drawerBody.innerHTML = "";
  els.drawerBody.appendChild(el("p", "text-sm text-slate-400", "Loading…"));
  showDrawer(true);

  try {
    const [summary, resources] = await Promise.all([
      api(`/patients/${encodeURIComponent(key)}`).catch(() => null),
      api(`/patients/${encodeURIComponent(key)}/resources`).catch(() => []),
    ]);
    renderDrawer(key, summary, resources);
  } catch (err) {
    els.drawerBody.innerHTML = "";
    els.drawerBody.appendChild(el("p", "text-sm text-red-600", `Failed to load: ${err.message}`));
  }
}

function section(title) {
  return el("h3", "text-xs font-semibold uppercase tracking-wide text-slate-400 mb-2", title);
}

function renderDrawer(key, summary, resources) {
  els.drawerBody.innerHTML = "";

  if (summary) {
    els.drawerSubtitle.textContent = summary.model ? `AI summary · ${summary.model}` : "";

    // disclaimer — always visible
    const disc = el("div", "rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800", summary.disclaimer);
    els.drawerBody.appendChild(disc);

    const wrap = el("div", "space-y-4 mt-4");
    if (summary.chief_concern) {
      wrap.appendChild(field("Chief concern", summary.chief_concern));
    }
    if (summary.key_diagnoses?.length) {
      wrap.appendChild(chipList("Key diagnoses", summary.key_diagnoses));
    }
    if (summary.recent_media?.length) {
      wrap.appendChild(chipList("Recent media", summary.recent_media));
    }
    if (summary.anomalies?.length) {
      wrap.appendChild(chipList("Flagged anomalies", summary.anomalies, "bg-red-50 text-red-700 border-red-200"));
    }
    const sumTitle = section("AI summary");
    els.drawerBody.append(sumTitle, wrap, el("p", "text-sm text-slate-700 leading-relaxed mt-2", summary.summary_text));
  } else {
    els.drawerSubtitle.textContent = "";
    els.drawerBody.appendChild(el("p", "text-sm text-slate-500", "No AI summary available for this patient."));
  }

  // resources
  els.drawerBody.appendChild(section(`Linked FHIR resources (${resources.length})`));
  if (!resources.length) {
    els.drawerBody.appendChild(el("p", "text-sm text-slate-400", "None found."));
    return;
  }
  const list = el("ul", "space-y-2");
  resources.forEach((r) => {
    const li = el("li", "rounded-lg border border-slate-200 p-3");
    const head = el("div", "flex items-center gap-2 mb-1");
    head.appendChild(badge(TYPE_LABELS[r.resource_type_label] || r.resource_type,
      TYPE_STYLES[r.resource_type_label] || "bg-slate-200 text-slate-700"));
    head.appendChild(el("span", "text-xs text-slate-400", r.record_date || ""));
    if (r.author) head.appendChild(el("span", "text-xs text-slate-400 ml-auto", r.author));
    li.appendChild(head);
    if (r.content) {
      const p = el("p", "text-sm text-slate-600 line-clamp-4 whitespace-pre-wrap", r.content);
      li.appendChild(p);
    }
    list.appendChild(li);
  });
  els.drawerBody.appendChild(list);
}

function field(label, value) {
  const d = el("div");
  d.appendChild(el("div", "text-xs font-semibold uppercase tracking-wide text-slate-400 mb-1", label));
  d.appendChild(el("p", "text-sm text-slate-700", value));
  return d;
}

function chipList(title, items, cls = "bg-teal-50 text-teal-800 border-teal-200") {
  const d = el("div");
  d.appendChild(el("div", "text-xs font-semibold uppercase tracking-wide text-slate-400 mb-1.5", title));
  const wrap = el("div", "flex flex-wrap gap-1.5");
  items.forEach((t) => wrap.appendChild(badge(t, `${cls} border`)));
  d.appendChild(wrap);
  return d;
}

function showDrawer(open) {
  els.drawer.classList.toggle("hidden", !open);
  els.drawerBackdrop.classList.toggle("hidden", !open);
  // trigger slide animation
  requestAnimationFrame(() => {
    els.drawer.classList.toggle("translate-x-full", !open);
    els.drawer.classList.toggle("translate-x-0", open);
  });
  document.body.style.overflow = open ? "hidden" : "";
}

function closeDrawer() {
  showDrawer(false);
  els.query.focus();
}

/* ---------- wiring ---------- */

els.form.addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });

let debounceTimer;
els.query.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => { if (els.query.value.trim()) runSearch(); }, 400);
});

[els.type, els.from, els.to].forEach((n) =>
  n.addEventListener("change", () => { if (state.searched) runSearch(); }));

els.clear.addEventListener("click", () => {
  els.type.value = els.from.value = els.to.value = "";
  if (state.searched) runSearch();
});

els.drawerClose.addEventListener("click", closeDrawer);
els.drawerBackdrop.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDrawer();
});

/* ---------- boot ---------- */

(async function init() {
  try {
    await api("/patients");
    setStatus(true, "connected");
  } catch {
    setStatus(false, "offline");
  }
  showEmpty("Search patient records", "Try: “chest pain”, “anemia”, “mammogram”, “antibiotics”…");
  els.query.focus();
})();
