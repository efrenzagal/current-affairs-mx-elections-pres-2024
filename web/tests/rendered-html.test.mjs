import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render(path = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${path}`, { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the landing page", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /<title>current affairs mx — política mexicana, con datos<\/title>/i);
  assert.match(html, /decisiones públicas/);
  // The three sections must always be reachable from the front door.
  for (const section of ["Visualizaciones interactivas", "Artículos", "Datos"]) {
    assert.ok(html.includes(section), `landing links to ${section}`);
  }
  assert.match(html, /og\.png/);
  // The front door carries no dataset, so it must never ship a loading state.
  assert.doesNotMatch(html, /Preparando el pleno/);
  assert.doesNotMatch(html, /codex-preview|SkeletonPreview|Your site is taking shape/i);
});

test("server-renders both chamber dashboards, member profiles, and their index", async () => {
  const [index, diputados, senado, perfiles] = await Promise.all([
    render("/visualizaciones"),
    render("/visualizaciones/diputados"),
    render("/visualizaciones/senado"),
    render("/visualizaciones/perfiles"),
  ]);
  assert.equal(index.status, 200);
  assert.equal(diputados.status, 200);
  assert.equal(senado.status, 200);
  assert.equal(perfiles.status, 200);
  const indexHtml = await index.text();
  assert.match(indexHtml, /Visualizaciones interactivas/);
  assert.match(indexHtml, /Perfiles legislativos/);
  // Each explorer loads only its own chamber, so both open on the same shell.
  assert.match(await diputados.text(), /Preparando el pleno/);
  assert.match(await senado.text(), /Preparando el pleno/);
  assert.match(await perfiles.text(), /Preparando los perfiles/);
});

test("ships real LXVI data for both chambers and no starter dependency", async () => {
  const [packageJson, data, senateData] = await Promise.all([
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../public/data/legislature-66.json", import.meta.url), "utf8"),
    readFile(new URL("../public/data/senate-66.json", import.meta.url), "utf8"),
  ]);
  const payload = JSON.parse(data);
  const senate = JSON.parse(senateData);
  assert.equal(payload.manifest.seatCount, 500);
  assert.equal(payload.manifest.linkedSeats, 500);
  assert.equal(payload.manifest.voteCount, 295);
  assert.equal(payload.seats.length, 500);
  const mrSeats = payload.seats.filter((seat) => seat.seatType === "MR");
  const rpSeats = payload.seats.filter((seat) => seat.seatType === "RP");
  assert.equal(mrSeats.length, 300);
  assert.equal(rpSeats.length, 200);
  assert.ok(mrSeats.every((seat) => seat.winningVotes > 0 && seat.winningPct > 0));
  assert.ok(rpSeats.every((seat) => seat.winningVotes === null && seat.winningPct === null));
  assert.equal(senate.manifest.seatCount, 128);
  assert.equal(senate.manifest.linkedSeats, 128);
  assert.equal(senate.manifest.voteCount, 378);
  assert.equal(senate.seats.filter((seat) => seat.seatType === "MR").length, 64);
  assert.equal(senate.seats.filter((seat) => seat.seatType === "FM").length, 32);
  assert.equal(senate.seats.filter((seat) => seat.seatType === "RP").length, 32);
  assert.equal(senate.seats.filter((seat) => seat.winningPct > 0).length, 96);
  assert.ok(Object.values(senate.histories).every((history) => history.length > 0));
  assert.ok(
    new Set(senate.votes.map((vote) => vote.topic)).size > 1,
    "Senate profiles ship classified topics rather than one generic chamber label",
  );
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
});

test("carries the current occupancy overlay, not only the 2024 winners", async () => {
  const [data, senateData, summaryData] = await Promise.all([
    readFile(new URL("../public/data/legislature-66.json", import.meta.url), "utf8"),
    readFile(new URL("../public/data/senate-66.json", import.meta.url), "utf8"),
    readFile(new URL("../public/data/visualizaciones.json", import.meta.url), "utf8"),
  ]);
  const summary = JSON.parse(summaryData);

  for (const [slug, payload] of [
    ["diputados", JSON.parse(data)],
    ["senado", JSON.parse(senateData)],
  ]) {
    const { manifest, seats, histories } = payload;
    assert.equal(manifest.schemaVersion, 2, `${slug} exports the occupancy schema`);
    assert.equal(manifest.chamber, slug);
    assert.ok(manifest.roster.observedAt, `${slug} records its roster cutoff`);
    assert.ok(manifest.roster.sourceUrl.startsWith("https://"));
    assert.deepEqual(summary[slug], manifest, `${slug} digest matches its manifest`);

    // The whole point of the overlay: some seats are held by someone other than
    // the person INE recorded winning them. If this ever hits zero, the roster
    // ingest has silently stopped overriding anything.
    assert.ok(manifest.substitutedSeats > 0, `${slug} has substituted seats`);
    assert.equal(
      seats.filter((seat) => seat.currentPersonId !== seat.electedPersonId).length,
      manifest.substitutedSeats,
    );

    const statuses = new Set(seats.map((seat) => seat.currentStatus));
    for (const status of statuses) {
      assert.ok(
        ["en_funciones", "licencia", "vacante", "sin_directorio"].includes(status),
        `${slug} status ${status} is a known state`,
      );
    }
    // A vacancy is the only case allowed to have no occupant name.
    assert.ok(
      seats.every((seat) => seat.currentName !== null || seat.currentStatus === "vacante"),
      `${slug} names every non-vacant occupant`,
    );

    // Histories are person-keyed. Every identity a seat can resolve to under
    // either view must be present, or the explorer renders an empty panel.
    for (const seat of seats) {
      for (const personId of [seat.electedPersonId, seat.currentPersonId]) {
        if (personId === null) continue;
        assert.ok(
          personId in histories,
          `${slug} seat ${seat.id} resolves person ${personId} to a history`,
        );
      }
    }
    assert.ok(
      !(seats[0].id in histories),
      `${slug} histories are keyed by person, not by seat`,
    );

    // One party vocabulary across the page. The Gaceta writes MRN for MORENA,
    // so without canonicalization the legend and the vote breakdown disagree.
    const seatParties = new Set(
      seats.flatMap((seat) => [seat.currentParty, seat.electedParty]),
    );
    const votePartyNames = new Set(
      Object.values(payload.partyVotes).flatMap((byParty) => Object.keys(byParty)),
    );
    const orphaned = [...votePartyNames].filter((party) => !seatParties.has(party));
    assert.deepEqual(orphaned, [], `${slug} vote parties all appear as seat benches`);

    // Collapsing two source keys into one bench must sum, not overwrite.
    const votesById = new Map(payload.votes.map((vote) => [vote.id, vote]));
    for (const [voteId, byParty] of Object.entries(payload.partyVotes)) {
      const vote = votesById.get(voteId);
      if (!vote) continue;
      const tally = {};
      for (const counts of Object.values(byParty)) {
        for (const [choice, n] of Object.entries(counts)) {
          tally[choice] = (tally[choice] ?? 0) + n;
        }
      }
      assert.equal(tally.Favor ?? 0, vote.favor, `${slug} ${voteId} favor totals agree`);
      assert.equal(tally.Contra ?? 0, vote.contra, `${slug} ${voteId} contra totals agree`);
    }
  }
});

test("server-renders the data dictionary route", async () => {
  const response = await render("/datos");
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Abriendo el diccionario/);
});

test("server-renders the articles index", async () => {
  const response = await render("/articulos");
  assert.equal(response.status, 200);
  assert.match(await response.text(), /Artículos/);
});

test("publishes the article without duplicated or failing payload", async () => {
  const [index, article] = await Promise.all([
    readFile(new URL("../public/data/articles.json", import.meta.url), "utf8"),
    readFile(new URL("../public/articulos/espectro-politico.html", import.meta.url), "utf8"),
  ]);

  const entries = JSON.parse(index);
  assert.ok(entries.length > 0, "at least one article is listed");
  for (const entry of entries) {
    for (const field of ["slug", "href", "title", "subtitle", "author", "published", "summary"]) {
      assert.ok(entry[field], `article ${entry.slug} has ${field}`);
    }
  }

  // Quarto inlines the 4.8 MB Plotly bundle once per figure and ships MathJax
  // even with no math; the publisher strips both. Regressions here quietly
  // triple the page weight, so assert on the shape rather than the byte count.
  assert.equal(
    (article.match(/plotly\.js v/g) ?? []).length,
    1,
    "exactly one Plotly bundle survives de-duplication",
  );
  assert.ok(article.includes("window.Plotly = Plotly"), "the surviving bundle defines Plotly");
  assert.ok(article.includes('class="plotly-graph-div"'), "figures are present");
  assert.doesNotMatch(article, /cdn\.plot\.ly\/plotly-[\d.]+\.min"/, "no failing CDN import");
  assert.doesNotMatch(article, /\/MathJax\.js/, "no MathJax in an article without math");

  // The injected chrome is what makes it a page of this site rather than a
  // loose Quarto document.
  assert.ok(article.includes('class="ca-header"'), "site header injected");
  assert.ok(article.includes('class="ca-footer"'), "site footer injected");
  assert.ok(article.includes("ca-expand"), "figure expand control injected");
});

test("ships a dictionary snapshot consistent with the CSV sources", async () => {
  const dictionary = JSON.parse(
    await readFile(new URL("../public/data/dictionary.json", import.meta.url), "utf8"),
  );

  const warehouse = Object.entries(dictionary.tables).filter(([, table]) => !table.raw);
  const rawReferences = Object.entries(dictionary.tables).filter(([, table]) => table.raw);
  assert.equal(warehouse.length, dictionary.warehouseTableCount);
  assert.equal(rawReferences.length, dictionary.rawReferenceCount);

  // Every table must be reachable from the sidebar, and every sidebar entry must exist.
  const grouped = dictionary.groups.flatMap((group) => group.tables);
  assert.deepEqual(
    [...grouped].sort(),
    Object.keys(dictionary.tables).sort(),
    "every table belongs to exactly one navigation group",
  );
  assert.equal(new Set(grouped).size, grouped.length, "no table appears in two groups");

  // The site renders Spanish by default, so that column must never be blank.
  for (const [name, table] of warehouse) {
    assert.ok(table.columns.length > 0, `${name} has documented columns`);
    assert.ok(table.overview, `${name} has an overview row`);
    for (const column of table.columns) {
      assert.ok(
        column["Description (SPA)"]?.trim(),
        `${name}.${column["Column Name"]} has a Spanish description`,
      );
    }
  }

  assert.ok(dictionary.coverage.length > 0);
  assert.ok(dictionary.legislativeCoverage.deputies.length > 0);
  assert.ok(dictionary.legislativeCoverage.senate.length > 0);

  // A concurrent SQLite reader once produced a snapshot where every example was
  // blank, and it shipped. The exporter now refuses to write that, but assert it
  // here too: this is the file the site actually serves. Tables documented ahead
  // of being built are exempt — they are flagged, not silently empty.
  const built = warehouse.filter(([, table]) => table.inWarehouse !== false);
  const withSamples = built.filter(([, table]) =>
    Object.values(table.examples ?? {}).some((values) => values.length > 0),
  );
  assert.ok(built.length > 0, "at least one documented table exists in the warehouse");
  assert.equal(
    withSamples.length,
    built.length,
    "every table that exists in the warehouse ships real column examples",
  );
});
