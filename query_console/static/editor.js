"use strict";

/* SQL syntax highlighting and schema-aware completion for a plain <textarea>.
 *
 * A textarea cannot render coloured text, so a <pre> layer is painted behind a
 * transparent-text textarea with identical metrics, re-rendered on input and
 * scrolled in lockstep. Completions are positioned with an offscreen mirror of
 * the textarea, the usual way to find where the caret actually sits. */

window.QCEditor = (function () {
  const KEYWORDS = new Set(("select from where group by order having limit offset join left right full inner outer" +
    " cross on as and or not in is null like glob between exists case when then else end union all except intersect" +
    " insert into values update set delete create table view index trigger drop alter add column primary key foreign" +
    " references unique check default constraint distinct with recursive over partition window filter asc desc" +
    " collate escape cast using natural pragma explain query plan begin commit rollback").split(" "));

  const FUNCTIONS = new Set(("count sum avg min max abs round coalesce ifnull nullif length lower upper substr trim" +
    " ltrim rtrim replace instr printf format group_concat date time datetime julianday strftime unixepoch" +
    " row_number rank dense_rank ntile lag lead first_value last_value cume_dist percent_rank total iif" +
    " typeof random hex quote json json_extract json_array json_object cast").split(" "));

  const TOKEN = /(--[^\n]*)|(\/\*[\s\S]*?\*\/)|('(?:''|[^'])*')|("(?:""|[^"])*")|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_$]*)|([-+*/%<>=!|&~^,;:().]+)/g;

  const schema = { tables: {}, names: [], columns: new Map() };  // column -> [tables]

  let ta = null, layer = null, code = null, mirror = null, menu = null;
  let items = [], active = 0, range = null, open = false, navigated = false;

  /* ── highlighting ──────────────────────────────────────────────────── */

  function esc(text) {
    return text.replace(/[&<>]/g, (c) => (c === "&" ? "&amp;" : c === "<" ? "&lt;" : "&gt;"));
  }

  function classify(word, after) {
    const lower = word.toLowerCase();
    if (KEYWORDS.has(lower)) return "kw";
    if (after === "(" && FUNCTIONS.has(lower)) return "fn";
    if (Object.prototype.hasOwnProperty.call(schema.tables, word)) return "tbl";
    if (schema.columns.has(word)) return "col";
    return "";
  }

  function tokenize(text) {
    let html = "";
    let last = 0;
    let match;
    TOKEN.lastIndex = 0;
    while ((match = TOKEN.exec(text)) !== null) {
      if (match.index > last) html += esc(text.slice(last, match.index));
      const value = match[0];
      let cls = "";
      if (match[1] || match[2]) cls = "cmt";
      else if (match[3]) cls = "str";
      else if (match[4]) cls = "idq";
      else if (match[5]) cls = "num";
      else if (match[6]) cls = classify(value, (text.slice(match.index + value.length).match(/^\s*(.)/) || [])[1]);
      else if (match[7]) cls = "op";
      html += cls ? '<span class="t-' + cls + '">' + esc(value) + "</span>" : esc(value);
      last = match.index + value.length;
    }
    html += esc(text.slice(last));
    return html + "\n";
  }

  function render() {
    code.innerHTML = tokenize(ta.value);
    syncScroll();
  }

  function syncScroll() {
    layer.scrollTop = ta.scrollTop;
    layer.scrollLeft = ta.scrollLeft;
  }

  /* ── caret position ────────────────────────────────────────────────── */

  function caretOffset(index) {
    mirror.textContent = ta.value.slice(0, index);
    const marker = document.createElement("span");
    marker.textContent = "​";
    mirror.appendChild(marker);
    const top = marker.offsetTop;
    const left = marker.offsetLeft;
    mirror.textContent = "";
    return { top: top - ta.scrollTop, left: left - ta.scrollLeft };
  }

  /* ── completion sources ────────────────────────────────────────────── */

  function aliasMap(text) {
    const map = {};
    const re = /\b(?:from|join)\s+"?([A-Za-z_][A-Za-z0-9_$]*)"?(?:\s+as)?(?:\s+([A-Za-z_][A-Za-z0-9_$]*))?/gi;
    let match;
    while ((match = re.exec(text)) !== null) {
      const table = match[1];
      if (!Object.prototype.hasOwnProperty.call(schema.tables, table)) continue;
      map[table] = table;
      const alias = match[2];
      if (alias && !KEYWORDS.has(alias.toLowerCase())) map[alias] = table;
    }
    return map;
  }

  function contextAt(value, pos) {
    const before = value.slice(0, pos);
    const qualified = /([A-Za-z_][A-Za-z0-9_$]*)\.([A-Za-z0-9_$]*)$/.exec(before);
    if (qualified) {
      return { kind: "qualified", qualifier: qualified[1], word: qualified[2],
               start: pos - qualified[2].length };
    }
    const word = (/[A-Za-z_][A-Za-z0-9_$]*$/.exec(before) || [""])[0];
    const wantsTable = /\b(?:from|join|into|update|table)\s+[A-Za-z0-9_$]*$/i.test(before);
    return { kind: wantsTable ? "table" : "any", word, start: pos - word.length };
  }

  function candidates(ctx, text) {
    const aliases = aliasMap(text);
    const out = [];
    const push = (label, kind, detail) => out.push({ label, kind, detail });

    if (ctx.kind === "qualified") {
      const table = aliases[ctx.qualifier] || (schema.tables[ctx.qualifier] ? ctx.qualifier : null);
      if (table) (schema.tables[table] || []).forEach((c) => push(c, "column", table));
      else schema.columns.forEach((tables, column) => push(column, "column", tables[0]));
      return out;
    }
    if (ctx.kind === "table") {
      schema.names.forEach((t) => push(t, "table", String((schema.tables[t] || []).length) + " cols"));
      return out;
    }
    // Columns of the tables this query already mentions come first.
    const inScope = new Set(Object.values(aliases));
    inScope.forEach((table) =>
      (schema.tables[table] || []).forEach((c) => push(c, "column", table)));
    schema.names.forEach((t) => push(t, "table", String((schema.tables[t] || []).length) + " cols"));
    schema.columns.forEach((tables, column) => {
      if (!inScope.has(tables[0])) push(column, "column", tables.length > 1 ? tables.length + " tables" : tables[0]);
    });
    KEYWORDS.forEach((k) => push(k.toUpperCase(), "keyword", ""));
    FUNCTIONS.forEach((f) => push(f.toUpperCase() + "()", "function", ""));
    return out;
  }

  function rank(list, word) {
    if (!word) return list.slice(0, 14);
    const needle = word.toLowerCase();
    const starts = [], contains = [];
    const seen = new Set();
    for (const item of list) {
      const key = item.kind + ":" + item.label;
      if (seen.has(key)) continue;
      const label = item.label.toLowerCase();
      if (label.startsWith(needle)) { seen.add(key); starts.push(item); }
      else if (label.includes(needle)) { seen.add(key); contains.push(item); }
      if (starts.length >= 14) break;
    }
    return starts.concat(contains).slice(0, 14);
  }

  /* ── menu ──────────────────────────────────────────────────────────── */

  function showMenu(force) {
    const pos = ta.selectionStart;
    if (pos !== ta.selectionEnd) return hideMenu();
    const ctx = contextAt(ta.value, pos);
    if (!force && !ctx.word && ctx.kind !== "qualified") return hideMenu();
    const list = rank(candidates(ctx, ta.value), ctx.word);
    if (!list.length) return hideMenu();

    items = list;
    active = 0;
    range = { start: ctx.start, end: pos };
    menu.innerHTML = "";
    list.forEach((item, i) => {
      const row = document.createElement("div");
      row.className = "ac-item" + (i === 0 ? " active" : "");
      row.innerHTML = '<span class="ac-label t-' + (item.kind === "table" ? "tbl" : item.kind === "column" ? "col" : "kw") +
        '"></span><span class="ac-kind"></span>';
      row.firstChild.textContent = item.label;
      row.lastChild.textContent = item.detail ? item.kind + " · " + item.detail : item.kind;
      row.onmousedown = (ev) => { ev.preventDefault(); accept(i); };
      menu.appendChild(row);
    });

    const point = caretOffset(ctx.start);
    const lineHeight = parseFloat(getComputedStyle(ta).lineHeight) || 18;
    menu.hidden = false;
    open = true;
    navigated = false;

    // The editor pane is short, so the menu is allowed to hang past it and
    // float over the results. Fit it to the viewport, not to the textarea.
    const box = ta.getBoundingClientRect();
    const caretTop = box.top + point.top;
    const spaceBelow = window.innerHeight - (caretTop + lineHeight) - 16;
    const spaceAbove = caretTop - 16;
    const dropUp = spaceBelow < 150 && spaceAbove > spaceBelow;
    menu.style.maxHeight = Math.max(90, Math.min(232, dropUp ? spaceAbove : spaceBelow)) + "px";

    const maxLeft = ta.clientWidth - menu.offsetWidth - 8;
    menu.style.left = Math.max(4, Math.min(point.left, maxLeft)) + "px";
    menu.style.top = (dropUp ? point.top - menu.offsetHeight - 2 : point.top + lineHeight + 4) + "px";
  }

  function hideMenu() {
    menu.hidden = true;
    open = false;
    items = [];
  }

  function move(delta) {
    navigated = true;
    const rows = menu.children;
    rows[active].classList.remove("active");
    active = (active + delta + items.length) % items.length;
    rows[active].classList.add("active");
    rows[active].scrollIntoView({ block: "nearest" });
  }

  function accept(index) {
    const item = items[index === undefined ? active : index];
    if (!item) return;
    ta.focus();
    ta.setSelectionRange(range.start, range.end);
    // execCommand keeps the browser's native undo stack intact.
    if (!document.execCommand("insertText", false, item.label)) {
      const value = ta.value;
      ta.value = value.slice(0, range.start) + item.label + value.slice(range.end);
      ta.setSelectionRange(range.start + item.label.length, range.start + item.label.length);
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    }
    if (item.kind === "function") ta.setSelectionRange(ta.selectionStart - 1, ta.selectionStart - 1);
    hideMenu();
    render();
  }

  /* ── wiring ────────────────────────────────────────────────────────── */

  function attach(textarea) {
    ta = textarea;
    layer = document.getElementById("hl");
    code = document.getElementById("hlCode");
    menu = document.getElementById("acMenu");

    mirror = document.createElement("div");
    mirror.className = "hl mirror";
    mirror.setAttribute("aria-hidden", "true");
    ta.parentNode.appendChild(mirror);

    ta.addEventListener("input", () => { render(); showMenu(false); });
    ta.addEventListener("scroll", () => { syncScroll(); if (open) hideMenu(); });
    ta.addEventListener("blur", hideMenu);
    ta.addEventListener("click", hideMenu);

    // Capture on the document so the menu claims Tab/Enter/arrows before the
    // editor's own shortcuts see them.
    document.addEventListener("keydown", (ev) => {
      if (ev.target !== ta) return;
      if ((ev.ctrlKey || ev.metaKey) && ev.code === "Space") {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        showMenu(true);
        return;
      }
      if (!open) return;
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        move(ev.key === "ArrowDown" ? 1 : -1);
      } else if (ev.key === "Tab") {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        accept();
      } else if (ev.key === "Enter") {
        // Enter only commits a suggestion the user actually picked; otherwise
        // it stays a newline, which is what you want mid-query.
        if (!navigated) return hideMenu();
        ev.preventDefault();
        ev.stopImmediatePropagation();
        accept();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        hideMenu();
      }
    }, true);

    render();
  }

  async function loadSchema() {
    try {
      const payload = await fetch("/api/schema/all").then((r) => r.json());
      schema.tables = payload.tables || {};
      schema.names = Object.keys(schema.tables);
      schema.columns = new Map();
      schema.names.forEach((table) => {
        (schema.tables[table] || []).forEach((column) => {
          if (!schema.columns.has(column)) schema.columns.set(column, []);
          schema.columns.get(column).push(table);
        });
      });
      render();
    } catch (err) {
      /* highlighting still works for keywords, strings and numbers */
    }
  }

  return { attach, loadSchema, render: () => render() };
})();
