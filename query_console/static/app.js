"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US"));

const state = {
  sheets: [],      // editor tabs, each with its own SQL, job and result
  activeId: null,
  poll: null,      // one timer polls every running sheet
  columns: [],     // grid currently on screen, for "Copy as Markdown"
  rows: [],
};

/* ── api ─────────────────────────────────────────────────────────────── */

async function api(path, options) {
  const res = await fetch(path, options);
  const text = await res.text();
  let payload = {};
  try { payload = text ? JSON.parse(text) : {}; } catch (e) { payload = { error: text }; }
  if (!res.ok) throw new Error(payload.error || res.statusText);
  return payload;
}

const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });

/* ── tabs ────────────────────────────────────────────────────────────── */

document.querySelectorAll(".tabs").forEach((bar) => {
  bar.addEventListener("click", (ev) => {
    const tab = ev.target.closest(".tab");
    if (!tab) return;
    const group = bar.parentElement;
    bar.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    bar.querySelectorAll(".tab").forEach((t) => {
      const pane = group.querySelector("#" + t.dataset.pane);
      if (pane) pane.classList.toggle("active", t === tab);
    });
    if (tab.dataset.pane === "historyPane") refreshHistory();
  });
});

/* ── theme ───────────────────────────────────────────────────────────── */

const savedTheme = localStorage.getItem("qc-theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
$("themeToggle").onclick = () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("qc-theme", next);
};

/* ── sidebar ─────────────────────────────────────────────────────────── */

const layout = document.querySelector(".layout");

function setSidebar(hidden) {
  layout.classList.toggle("collapsed", hidden);
  const btn = $("sidebarToggle");
  btn.textContent = hidden ? "⇥" : "⇤";
  btn.title = (hidden ? "Show" : "Hide") + " the sidebar (⌘B)";
  localStorage.setItem("qc-sidebar", hidden ? "hidden" : "shown");
}

setSidebar(localStorage.getItem("qc-sidebar") === "hidden");
$("sidebarToggle").onclick = () => setSidebar(!layout.classList.contains("collapsed"));

/* ── editor sheets ───────────────────────────────────────────────────── */

const sqlBox = $("sql");
let sheetSeq = 0;

const activeSheet = () => state.sheets.find((s) => s.id === state.activeId) || null;

function persistSheets() {
  localStorage.setItem("qc-sheets", JSON.stringify({
    activeId: state.activeId,
    sheets: state.sheets.map((s) => ({ id: s.id, name: s.name, sql: s.sql })),
  }));
}

function newSheet(sql, name) {
  sheetSeq += 1;
  const sheet = {
    id: "s" + sheetSeq + "-" + Date.now().toString(36),
    name: name || "Query " + sheetSeq,
    sql: sql || "",
    jobId: null,
    status: "idle",
    statusText: "",
    counters: "",
    resultId: null,
    error: null,
  };
  state.sheets.push(sheet);
  activateSheet(sheet.id);
  sqlBox.focus();
  return sheet;
}

function closeSheet(id) {
  const index = state.sheets.findIndex((s) => s.id === id);
  if (index < 0) return;
  const [sheet] = state.sheets.splice(index, 1);
  // A query the tab started keeps no owner once the tab is gone.
  if (sheet.jobId && sheet.status === "running") post(`/api/query/${sheet.jobId}/cancel`);
  if (!state.sheets.length) {
    sheetSeq = 0;
    newSheet("");
    return;
  }
  if (state.activeId === id) activateSheet(state.sheets[Math.max(0, index - 1)].id);
  else { renderSheetTabs(); persistSheets(); }
}

function activateSheet(id) {
  const sheet = state.sheets.find((s) => s.id === id);
  if (!sheet) return;
  state.activeId = id;
  sqlBox.value = sheet.sql;
  QCEditor.render();
  renderSheetTabs();
  persistSheets();
  restoreStatus(sheet);
  restoreResult(sheet);
}

function shiftSheet(delta) {
  const index = state.sheets.findIndex((s) => s.id === state.activeId);
  if (index < 0) return;
  activateSheet(state.sheets[(index + delta + state.sheets.length) % state.sheets.length].id);
  sqlBox.focus();
}

function renderSheetTabs() {
  const bar = $("sheetTabs");
  bar.innerHTML = "";
  state.sheets.forEach((sheet) => {
    const tab = document.createElement("div");
    tab.className = "sheet-tab" + (sheet.id === state.activeId ? " active" : "");
    tab.onclick = () => activateSheet(sheet.id);
    tab.ondblclick = () => {
      const name = prompt("Name this tab", sheet.name);
      if (name && name.trim()) {
        sheet.name = name.trim().slice(0, 40);
        renderSheetTabs();
        persistSheets();
      }
    };

    const dot = document.createElement("span");
    dot.className = "status-dot";
    dot.style.background = STATUS_COLORS[sheet.status] || "transparent";
    dot.title = sheet.status;
    if (sheet.status === "running") dot.classList.add("blink");
    tab.appendChild(dot);

    const name = document.createElement("span");
    name.className = "sheet-name";
    name.textContent = sheet.name;
    name.title = sheet.name + " — double-click to rename";
    tab.appendChild(name);

    const close = document.createElement("button");
    close.className = "sheet-close";
    close.textContent = "×";
    close.title = "Close tab";
    close.onclick = (ev) => { ev.stopPropagation(); closeSheet(sheet.id); };
    tab.appendChild(close);

    bar.appendChild(tab);
  });

  const add = document.createElement("button");
  add.className = "sheet-add";
  add.textContent = "+";
  add.title = "New query tab";
  add.onclick = () => newSheet("");
  bar.appendChild(add);
}

function restoreStatus(sheet) {
  if (sheet.status === "idle") {
    setStatus("idle", "Write a query and press ⌘⏎ / Ctrl+Enter.", "");
  } else {
    setStatus(sheet.status, sheet.statusText, sheet.counters);
  }
}

function restoreResult(sheet) {
  if (sheet.status === "error" && sheet.error) renderError(sheet.error);
  else if (sheet.resultId) showResult(sheet.resultId, sheet.status !== "ok");
  else clearResults();
}

function clearResults() {
  $("resultsPane").innerHTML = '<div class="empty">No results yet.</div>';
  $("resultActions").hidden = true;
  state.columns = [];
  state.rows = [];
}

/* ── editor ──────────────────────────────────────────────────────────── */

let draftTimer;
sqlBox.addEventListener("input", () => {
  const sheet = activeSheet();
  if (!sheet) return;
  sheet.sql = sqlBox.value;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(persistSheets, 250);
});
sqlBox.addEventListener("keydown", (ev) => {
  if (ev.key === "Tab") {
    ev.preventDefault();
    insertAtCursor("  ");
  } else if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
    ev.preventDefault();
    runQuery();
  }
});
document.addEventListener("keydown", (ev) => {
  const sheet = activeSheet();
  if (ev.key === "Escape" && sheet && sheet.jobId && sheet.status === "running") cancelQuery();
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "b") {
    ev.preventDefault();
    setSidebar(!layout.classList.contains("collapsed"));
  }
  if (ev.altKey && (ev.key === "ArrowRight" || ev.key === "ArrowLeft")) {
    ev.preventDefault();
    shiftSheet(ev.key === "ArrowRight" ? 1 : -1);
  }
  if (ev.altKey && /^[1-9]$/.test(ev.key)) {
    const target = state.sheets[Number(ev.key) - 1];
    if (target) { ev.preventDefault(); activateSheet(target.id); sqlBox.focus(); }
  }
});

function insertAtCursor(text) {
  sqlBox.focus();
  // execCommand keeps the native undo stack and fires `input`, which repaints
  // the highlight layer and records the draft.
  if (!document.execCommand("insertText", false, text)) {
    const start = sqlBox.selectionStart;
    const end = sqlBox.selectionEnd;
    setSql(sqlBox.value.slice(0, start) + text + sqlBox.value.slice(end));
    sqlBox.selectionStart = sqlBox.selectionEnd = start + text.length;
  }
}

/** Set the active tab's contents from code, keeping highlighting in step. */
function setSql(text) {
  sqlBox.value = text;
  const sheet = activeSheet();
  if (sheet) sheet.sql = text;
  persistSheets();
  QCEditor.render();
}

$("clearBtn").onclick = () => {
  setSql("");
  sqlBox.focus();
};

/* ── run / poll / cancel ─────────────────────────────────────────────── */

function setStatus(status, text, counters) {
  const pill = $("statusPill");
  pill.className = "pill " + status;
  pill.textContent = status;
  $("statusText").textContent = text || "";
  $("counters").textContent = counters || "";
  const run = $("runBtn");
  if (status === "running") {
    run.textContent = "Cancel";
    run.classList.add("danger");
  } else {
    run.innerHTML = 'Run <kbd>⌘⏎</kbd>';
    run.classList.remove("danger");
  }
}

$("runBtn").onclick = () => {
  const sheet = activeSheet();
  return sheet && sheet.status === "running" ? cancelQuery() : runQuery();
};

/** Blank means "no limit"; anything unparseable falls back to the default. */
function numericField(id, fallback) {
  const raw = $(id).value.trim();
  if (raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 1 ? Math.floor(value) : fallback;
}

async function runQuery() {
  const sheet = activeSheet();
  if (!sheet) return;
  const sql = sqlBox.value.trim();
  if (!sql) return;
  const minutes = numericField("timeout", 30);

  sheet.status = "running";
  sheet.statusText = "Submitting…";
  sheet.counters = "";
  sheet.error = null;
  sheet.resultId = null;
  restoreStatus(sheet);
  renderSheetTabs();
  showPane("resultsPane");
  $("resultActions").hidden = true;

  try {
    const job = await post("/api/run", {
      sql,
      row_limit: numericField("rowLimit", 100),
      timeout_s: minutes === null ? null : minutes * 60,
    });
    sheet.jobId = job.query_id;
    ensurePolling();
    refreshHistory();
  } catch (err) {
    sheet.status = "error";
    sheet.error = String(err.message);
    sheet.statusText = "Query failed.";
    renderSheetTabs();
    if (sheet.id === state.activeId) {
      restoreStatus(sheet);
      renderError(sheet.error);
    }
  }
}

async function cancelQuery() {
  const sheet = activeSheet();
  if (!sheet || !sheet.jobId) return;
  sheet.statusText = "Cancelling…";
  restoreStatus(sheet);
  await post(`/api/query/${sheet.jobId}/cancel`);
}

function ensurePolling() {
  if (state.poll) return;
  pollAll();
  state.poll = setInterval(pollAll, 300);
}

async function pollAll() {
  const running = state.sheets.filter((s) => s.jobId && s.status === "running");
  if (!running.length) {
    clearInterval(state.poll);
    state.poll = null;
    return;
  }
  let finished = false;
  await Promise.all(running.map(async (sheet) => {
    let job;
    try {
      job = await api(`/api/query/${sheet.jobId}`);
    } catch (err) {
      sheet.status = "error";
      sheet.error = String(err.message);
      sheet.statusText = "Lost track of this query.";
      finished = true;
      return;
    }
    const secs = (job.duration_ms / 1000).toFixed(1);
    sheet.counters = `${secs}s · ${fmt(job.rows_fetched || job.row_count || 0)} rows · ${fmt(job.steps)} steps`;

    if (job.status === "running") {
      sheet.statusText = "Scanning the warehouse… press Esc to cancel.";
    } else if (job.status === "ok") {
      sheet.status = "ok";
      sheet.statusText = `Finished in ${secs}s.`;
      sheet.resultId = job.query_id;
      finished = true;
    } else if (job.status === "cancelled" || job.status === "interrupted") {
      sheet.status = job.status;
      sheet.statusText = job.error || "Stopped.";
      sheet.resultId = job.query_id;
      finished = true;
    } else {
      sheet.status = "error";
      sheet.error = job.error || "unknown error";
      sheet.statusText = "Query failed.";
      finished = true;
    }

    if (sheet.id === state.activeId) {
      restoreStatus(sheet);
      if (job.status !== "running") restoreResult(sheet);
    }
  }));

  renderSheetTabs();
  if (finished) refreshHistory();
}

/* ── results ─────────────────────────────────────────────────────────── */

function showPane(id) {
  const bar = document.querySelector(".results-tabs");
  bar.querySelectorAll(".tab").forEach((t) => {
    const on = t.dataset.pane === id;
    t.classList.toggle("active", on);
    document.getElementById(t.dataset.pane).classList.toggle("active", on);
  });
}

async function showResult(queryId, partial) {
  state.viewing = queryId;
  const pane = $("resultsPane");
  let payload;
  try {
    payload = await api(`/api/query/${queryId}/rows`);
  } catch (err) {
    renderError(err.message);
    return;
  }
  state.columns = payload.columns || [];
  state.rows = payload.rows || [];

  pane.innerHTML = "";
  if (payload.note) pane.appendChild(noteEl(payload.note));
  if (!state.columns.length) {
    pane.appendChild(noteEl("The statement returned no result set."));
    $("resultActions").hidden = true;
    return;
  }
  const notes = [];
  if (payload.shown < payload.row_count)
    notes.push(`Showing the first ${fmt(payload.shown)} of ${fmt(payload.row_count)} rows — download the CSV for all of them.`);
  if (payload.truncated)
    notes.push("The row limit was reached, so this is a partial result.");
  if (partial && payload.row_count)
    notes.push("Partial result: the query was stopped before it finished.");
  if (notes.length) pane.appendChild(noteEl(notes.join(" ")));

  pane.appendChild(buildGrid(state.columns, state.rows));
  $("csvLink").href = `/api/query/${queryId}/csv`;
  $("resultActions").hidden = false;
  showPane("resultsPane");
}

function noteEl(text) {
  const div = document.createElement("div");
  div.className = "note";
  div.textContent = text;
  return div;
}

function buildGrid(columns, rows) {
  const wrap = document.createElement("div");
  wrap.className = "grid-wrap";
  const table = document.createElement("table");
  table.className = "grid";

  const thead = document.createElement("thead");
  const hrow = document.createElement("tr");
  hrow.appendChild(th("#"));
  columns.forEach((c) => hrow.appendChild(th(c)));
  thead.appendChild(hrow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  rows.forEach((row, i) => {
    const tr = document.createElement("tr");
    const idx = document.createElement("td");
    idx.className = "rownum";
    idx.textContent = i + 1;
    tr.appendChild(idx);
    row.forEach((value) => {
      const td = document.createElement("td");
      if (value === null) {
        td.className = "null";
        td.textContent = "NULL";
      } else {
        if (typeof value === "number" || (typeof value === "string" && value !== "" && !isNaN(value)))
          td.className = "num";
        td.textContent = String(value);
        td.title = String(value);
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function th(text) {
  const cell = document.createElement("th");
  cell.textContent = text;
  return cell;
}

function renderError(message) {
  const pane = $("resultsPane");
  pane.innerHTML = "";
  const box = document.createElement("div");
  box.className = "error-box";
  box.textContent = message;
  pane.appendChild(box);
  $("resultActions").hidden = true;
  showPane("resultsPane");
}

$("copyMdBtn").onclick = () => {
  const head = `| ${state.columns.join(" | ")} |`;
  const rule = `| ${state.columns.map(() => "---").join(" | ")} |`;
  const body = state.rows
    .map((r) => `| ${r.map((v) => (v === null ? "" : String(v).replace(/\|/g, "\\|"))).join(" | ")} |`)
    .join("\n");
  navigator.clipboard.writeText([head, rule, body].join("\n"));
  const btn = $("copyMdBtn");
  btn.textContent = `Copied ${fmt(state.rows.length)} rows`;
  setTimeout(() => (btn.textContent = "Copy as Markdown"), 1600);
};

/* ── query plan ──────────────────────────────────────────────────────── */

$("explainBtn").onclick = async () => {
  const sql = sqlBox.value.trim();
  if (!sql) return;
  const pane = $("planPane");
  pane.innerHTML = "";
  try {
    const plan = await post("/api/explain", { sql });
    pane.appendChild(buildGrid(plan.columns, plan.rows));
  } catch (err) {
    const box = document.createElement("div");
    box.className = "error-box";
    box.textContent = err.message;
    pane.appendChild(box);
  }
  showPane("planPane");
};

/* ── history ─────────────────────────────────────────────────────────── */

const STATUS_COLORS = {
  idle: "transparent", ok: "var(--ok)", running: "var(--run)", error: "var(--err)",
  cancelled: "var(--warn)", interrupted: "var(--warn)",
};

async function refreshHistory() {
  const search = $("historySearch").value.trim();
  const list = $("historyList");
  let entries;
  try {
    entries = (await api(`/api/history?limit=200&q=${encodeURIComponent(search)}`)).entries;
  } catch (err) {
    return;
  }
  list.innerHTML = "";
  if (!entries.length) {
    list.appendChild(noteEl(search ? "No past query matches that text." : "No queries yet."));
    return;
  }
  entries.forEach((entry) => list.appendChild(historyRow(entry)));
}

function historyRow(entry) {
  const row = document.createElement("div");
  row.className = "hrow";

  const dot = document.createElement("span");
  dot.className = "status-dot";
  dot.style.background = STATUS_COLORS[entry.status] || "var(--text-dim)";
  dot.title = entry.status;
  row.appendChild(dot);

  const sql = document.createElement("span");
  sql.className = "sql";
  sql.textContent = entry.sql_text.replace(/\s+/g, " ").slice(0, 240);
  sql.title = entry.sql_text;
  row.appendChild(sql);

  const facts = document.createElement("span");
  facts.className = "facts";
  const bits = [relTime(entry.started_at)];
  if (entry.duration_ms != null) bits.push(`${(entry.duration_ms / 1000).toFixed(1)}s`);
  if (entry.row_count != null && entry.status === "ok") bits.push(`${fmt(entry.row_count)} rows`);
  if (entry.status !== "ok") bits.push(entry.status);
  facts.textContent = bits.join(" · ");
  facts.title = entry.error || "";
  row.appendChild(facts);

  const acts = document.createElement("span");
  acts.className = "acts";
  acts.appendChild(button("Edit", (ev) => {
    ev.stopPropagation();
    setSql(entry.sql_text);
    sqlBox.focus();
  }));
  if (entry.has_result) {
    acts.appendChild(button("Result", (ev) => {
      ev.stopPropagation();
      adoptHistory(entry);
    }));
  }
  if (entry.status === "error") {
    acts.appendChild(button("Error", (ev) => {
      ev.stopPropagation();
      renderError(entry.error || "unknown error");
    }));
  }
  row.appendChild(acts);

  row.onclick = () => {
    setSql(entry.sql_text);
    if (entry.has_result) adoptHistory(entry);
  };
  return row;
}

/** Show a past query's result in the active tab, so a tab switch keeps it. */
function adoptHistory(entry) {
  const sheet = activeSheet();
  if (sheet) {
    sheet.status = entry.status;
    sheet.statusText = `Query from ${relTime(entry.started_at)} · ${entry.status}.`;
    sheet.counters = `${((entry.duration_ms || 0) / 1000).toFixed(1)}s · ${fmt(entry.row_count)} rows`;
    sheet.resultId = entry.query_id;
    sheet.error = entry.status === "error" ? entry.error : null;
    renderSheetTabs();
    restoreStatus(sheet);
  }
  showResult(entry.query_id, entry.status !== "ok");
}

function button(label, onClick) {
  const b = document.createElement("button");
  b.className = "ghost";
  b.textContent = label;
  b.onclick = onClick;
  return b;
}

function relTime(iso) {
  const then = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  const secs = (Date.now() - then.getTime()) / 1000;
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  if (secs < 7 * 86400) return `${Math.floor(secs / 86400)}d ago`;
  return then.toLocaleDateString();
}

let historyTimer;
$("historySearch").addEventListener("input", () => {
  clearTimeout(historyTimer);
  historyTimer = setTimeout(refreshHistory, 200);
});

/* ── schema sidebar ──────────────────────────────────────────────────── */

async function loadSchema() {
  const tree = $("tableTree");
  const { objects } = await api("/api/schema");
  tree.innerHTML = "";
  objects.forEach((obj) => tree.appendChild(schemaNode(obj)));
}

function schemaNode(obj) {
  const node = document.createElement("div");
  node.className = "node";
  node.dataset.name = obj.name;

  const head = document.createElement("div");
  head.className = "node-head";
  const name = document.createElement("span");
  name.className = "node-name";
  name.textContent = obj.name;
  head.appendChild(name);
  if (obj.type === "view") {
    const kind = document.createElement("span");
    kind.className = "node-kind";
    kind.textContent = "view";
    head.appendChild(kind);
  }
  if (obj.approx_rows) {
    const rows = document.createElement("span");
    rows.className = "node-rows";
    rows.textContent = obj.approx_rows;
    rows.title = "Approx. rows: " + obj.approx_rows;
    head.appendChild(rows);
  }
  node.appendChild(head);

  const body = document.createElement("div");
  body.className = "node-body";
  node.appendChild(body);

  head.onclick = async (ev) => {
    if (ev.altKey || ev.metaKey) {
      insertAtCursor(`SELECT *\nFROM ${obj.name}\nLIMIT 100`);
      return;
    }
    node.classList.toggle("open");
    if (node.classList.contains("open") && !body.dataset.loaded) {
      body.dataset.loaded = "1";
      if (obj.purpose) {
        const p = document.createElement("div");
        p.className = "node-purpose";
        p.textContent = obj.purpose;
        body.appendChild(p);
      }
      const { columns } = await api(`/api/schema/${obj.name}`);
      columns.forEach((col) => body.appendChild(columnRow(obj.name, col)));
    }
  };
  return node;
}

function columnRow(table, col) {
  const row = document.createElement("div");
  row.className = "col";
  const name = document.createElement("span");
  name.className = "col-name";
  name.textContent = col.name;
  name.title = "Click to insert into the editor";
  name.onclick = () => insertAtCursor(col.name);
  row.appendChild(name);
  if (col.pk) {
    const pk = document.createElement("span");
    pk.className = "badge";
    pk.textContent = "PK";
    row.appendChild(pk);
  }
  const type = document.createElement("span");
  type.className = "col-type";
  type.textContent = col.type;
  row.appendChild(type);
  if (col.description) {
    const desc = document.createElement("span");
    desc.className = "col-desc";
    desc.textContent = col.description.slice(0, 90);
    desc.title = col.description;
    row.appendChild(desc);
  }
  return row;
}

$("tableSearch").addEventListener("input", (ev) => {
  const needle = ev.target.value.toLowerCase();
  document.querySelectorAll("#tableTree .node").forEach((node) => {
    node.style.display = node.dataset.name.toLowerCase().includes(needle) ? "" : "none";
  });
});

/* ── saved queries ───────────────────────────────────────────────────── */

async function loadSaved() {
  const list = $("savedList");
  const { queries } = await api("/api/saved");
  list.innerHTML = "";
  if (!queries.length) {
    list.appendChild(noteEl("Nothing saved yet. Use “Save…” to keep a query."));
    return;
  }
  queries.forEach((q) => {
    const node = document.createElement("div");
    node.className = "node";
    const head = document.createElement("div");
    head.className = "node-head";
    const name = document.createElement("span");
    name.className = "node-name";
    name.textContent = q.name;
    head.appendChild(name);
    node.appendChild(head);
    if (q.title) {
      const desc = document.createElement("div");
      desc.className = "node-purpose";
      desc.style.padding = "0 8px 6px";
      desc.textContent = q.title;
      node.appendChild(desc);
    }
    head.onclick = () => {
      setSql(q.sql);
      sqlBox.focus();
    };
    list.appendChild(node);
  });
}

const modal = $("saveModal");
$("saveBtn").onclick = () => {
  modal.hidden = false;
  $("saveName").focus();
};
$("saveCancel").onclick = () => (modal.hidden = true);
$("saveConfirm").onclick = async () => {
  try {
    await post("/api/saved", { name: $("saveName").value, sql: sqlBox.value });
    modal.hidden = true;
    $("saveName").value = "";
    loadSaved();
  } catch (err) {
    alert(err.message);
  }
};

/* ── boot ────────────────────────────────────────────────────────────── */

(async function boot() {
  try {
    const meta = await api("/api/meta");
    const gb = (meta.size_bytes / 1e9).toFixed(2);
    $("dbMeta").textContent = `${meta.database} · ${gb} GB · read-only · updated ${meta.modified.replace("T", " ")}`;
  } catch (err) {
    $("dbMeta").textContent = "database unavailable";
  }
  QCEditor.attach(sqlBox);
  QCEditor.loadSchema();
  restoreSheets();
  loadSchema();
  loadSaved();
  refreshHistory();
  sqlBox.focus();
})();

/** Reopen last session's tabs; fall back to the pre-tabs single draft. */
function restoreSheets() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("qc-sheets") || "null"); } catch (err) { saved = null; }
  const rows = (saved && Array.isArray(saved.sheets) ? saved.sheets : []).filter((s) => s && s.id);
  if (!rows.length) {
    const legacy = localStorage.getItem("qc-draft") || "";
    localStorage.removeItem("qc-draft");
    newSheet(legacy);
    return;
  }
  state.sheets = rows.map((row, i) => ({
    id: row.id,
    name: row.name || "Query " + (i + 1),
    sql: row.sql || "",
    jobId: null, status: "idle", statusText: "", counters: "",
    resultId: null, error: null,
  }));
  sheetSeq = state.sheets.length;
  activateSheet(state.sheets.some((s) => s.id === saved.activeId) ? saved.activeId : state.sheets[0].id);
}
