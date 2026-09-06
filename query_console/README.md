# Query Console

A local SQL workbench for `election_data.db` — the kind of console you get in a
data warehouse UI: write a query, watch it run, cancel it if it is going
nowhere, and scroll back through everything you have run before, including what
each query returned.

It is an **internal tool**. It is not part of `web/`, it is never deployed, and
it binds to `127.0.0.1` only. Nothing here ships to the site.

```bash
/usr/bin/python3 -m query_console
```

That starts the console on <http://127.0.0.1:8787/> and opens a browser tab.
Stdlib only — no packages beyond what the repo already installs.

## What it does

**Run.** ⌘⏎ (or Ctrl+Enter) runs the statement in the editor. The warehouse is
opened **read-only**, so nothing you type here can modify it. Two boxes in the
toolbar bound the run: **Rows** (default 100, blank for no limit) and **Stop
after** (default 30 minutes, blank for no limit).

**Work in tabs.** Each tab is an independent query — its own SQL, its own run,
its own result. Queries in different tabs run at the same time, each on its own
connection, so a slow aggregate in one tab does not block a quick lookup in
another; the dot on each tab shows that tab's state. **+** opens a tab, **×**
closes it (cancelling anything it was running), double-click renames it, and
**⌥←/⌥→** or **⌥1**–**⌥9** move between them. Open tabs and their SQL come back
when you reload.

**Read what you typed.** The editor colours SQL keywords, strings, numbers and
comments, and — because it knows the live schema — table names and column names
get their own colours too. A misspelt table simply stays uncoloured, which is
usually the fastest way to spot it.

**Complete.** Typing offers matching tables and columns, ranked so that columns
of the tables already in your `FROM`/`JOIN` come first. After `FROM` or `JOIN`
it offers tables only; after an alias and a dot (`sm.`) it resolves the alias and
offers just that table's columns. **Tab** accepts, **↑/↓** move, **Esc**
dismisses, **⌃Space** opens the list on demand. **Enter** stays a newline unless
you have actually moved the selection with the arrows.

**Watch.** While a query runs, the status bar shows elapsed time, rows fetched,
and SQLite's VM step counter — the honest progress signal for a query whose
total work is not knowable in advance. A query that scans `fact_casilla_vote`
ticks up millions of steps a second; one that has stalled does not.

**Stop.** The Run button becomes Cancel (Esc also works) and takes effect within
a few thousand VM steps. "Stop after" cancels on your behalf once the query has
run that many minutes.

**Look back.** Every run is logged to `query_console/.state/history.db` with its
SQL, status, duration, row count, and error text. That log is permanent — it is
text, it costs nothing, and it is searchable by SQL text in the **History** tab.
`Edit` puts the SQL back in the editor.

**Rows are session state.** The rows themselves are cached as CSV in
`.state/results/` only for the session that ran the query, and `Result` reopens
them from there. Starting the console wipes that directory, and so does stopping
it. Results are heavy in a way the log is not: a million rows out of
`fact_casilla_vote` is a 63 MB file, and that table holds 35 million rows.
Within a session the newest 50 result sets are kept (`--keep-results`).

So a query from last week shows up in History with its status, duration and row
count, and one click puts the SQL back in the editor — but to see its rows again
you re-run it. If you need a result to outlive the session, use *Download CSV*
while it is on screen.

**Take it away.** *Download CSV* gives the complete result — not just the rows
on screen. *Copy as Markdown* copies the visible rows as a Markdown table, ready
to paste into an article draft.

**Reclaim the width.** The arrow at the top left (or ⌘B / Ctrl+B) hides the
sidebar so the editor and results grid span the whole window. The choice
sticks across reloads.

**Browse the warehouse.** The Tables sidebar lists live tables and views; expand
one for its columns, types, and the English descriptions from
`web/public/data/dictionary.json`, with table purposes and row counts from
`documentation/table_dictionaries/overview.csv`. Click a column name to insert
it at the cursor; ⌥-click (or ⌘-click) a table name to insert
`SELECT * FROM <table> LIMIT 100`.

**Keep the good ones.** *Save…* writes the editor contents to
`documentation/queries/<slug>.sql`, next to the queries already there, and the
Saved tab loads any of them back. Those files are tracked in git, so a query
behind a published number stays reproducible.

## Limits worth knowing

- One statement per run. A trailing semicolon is fine; a script of several
  statements is not.
- The row limit (default 100) caps what is fetched. Hitting it is reported as a
  partial result rather than passed off as the whole answer. Clear the box for
  no cap — with 35 million rows in `fact_casilla_vote`, the 30-minute stop is
  then the only thing bounding a runaway `SELECT *`.
- The grid renders the first 2 000 rows; the CSV always holds everything fetched.
- A query cancelled mid-fetch keeps the rows it had already written, labelled as
  partial.

## Options

```
--db PATH             warehouse to open (default: election_data.db)
--port N              default 8787
--host HOST           default 127.0.0.1
--keep-results N      cached result sets to retain (default 50)
--no-browser          do not open a tab
```

## Layout

| File | Role |
| --- | --- |
| `__main__.py` | CLI entry point |
| `server.py` | JSON API and static file serving |
| `engine.py` | background execution, progress, cancellation, result caching |
| `store.py` | the query history database |
| `schema.py` | schema browsing, dictionary lookups, saved queries |
| `static/` | the single-page UI |
