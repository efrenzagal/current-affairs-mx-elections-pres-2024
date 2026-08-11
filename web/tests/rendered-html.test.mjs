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
  for (const href of [
    "/visualizaciones/trayectoria",
    "/visualizaciones/perfiles",
    "/visualizaciones/votaciones",
    "/visualizaciones/diputados",
    "/visualizaciones/senado",
    "/articulos/espectro-politico.html",
  ]) {
    assert.ok(html.includes(`href="${href}"`), `header menu links to ${href}`);
  }
  assert.ok(
    html.includes('<details class="nav-item has-menu" name="site-navigation">'),
    "dashboard and article menus support touch disclosure",
  );
  assert.match(html, /og\.png/);
  // The front door carries no dataset, so it must never ship a loading state.
  assert.doesNotMatch(html, /Preparando el pleno/);
  assert.doesNotMatch(html, /codex-preview|SkeletonPreview|Your site is taking shape/i);
});

test("server-renders the electoral and congressional dashboards and their index", async () => {
  const [index, trayectoria, diputados, senado, perfiles, votaciones] = await Promise.all([
    render("/visualizaciones"),
    render("/visualizaciones/trayectoria"),
    render("/visualizaciones/diputados"),
    render("/visualizaciones/senado"),
    render("/visualizaciones/perfiles"),
    render("/visualizaciones/votaciones"),
  ]);
  assert.equal(index.status, 200);
  assert.equal(trayectoria.status, 200);
  assert.equal(diputados.status, 200);
  assert.equal(senado.status, 200);
  assert.equal(perfiles.status, 200);
  assert.equal(votaciones.status, 200);
  const indexHtml = await index.text();
  assert.match(indexHtml, /Visualizaciones interactivas/);
  assert.match(indexHtml, /Perfiles legislativos/);
  assert.match(indexHtml, /Geografía electoral/);
  assert.match(indexHtml, /Buscador de votaciones/);
  // Each explorer loads only its own chamber, so both open on the same shell.
  assert.match(await diputados.text(), /Preparando el pleno/);
  assert.match(await senado.text(), /Preparando el pleno/);
  assert.match(await perfiles.text(), /Preparando los perfiles/);
  assert.match(await trayectoria.text(), /Preparando el mapa/);
  assert.match(await votaciones.text(), /Preparando las votaciones/);
});

test("ships complete state and national trajectories for all federal contests", async () => {
  const [trajectoryData, stateGeoJson] = await Promise.all([
    readFile(new URL("../public/data/electoral-trajectory.json", import.meta.url), "utf8"),
    readFile(new URL("../public/data/electoral-states.geojson", import.meta.url), "utf8"),
  ]);
  const trajectory = JSON.parse(trajectoryData);
  const states = JSON.parse(stateGeoJson);
  assert.equal(trajectory.schemaVersion, 2);
  assert.equal(trajectory.states.length, 32);
  assert.equal(states.features.length, 32);
  const expectedYears = {
    PRE: [1994, 2000, 2006, 2012, 2018, 2024],
    SEN: [2000, 2006, 2012, 2018, 2024],
    DIP: [2000, 2006, 2012, 2015, 2018, 2021, 2024],
  };
  for (const [contestKey, years] of Object.entries(expectedYears)) {
    const contest = trajectory.contests[contestKey];
    assert.deepEqual(contest.years, years);
    assert.equal(Object.keys(contest.geographies).length, 33);
    assert.equal(Object.keys(contest.maps).length, years.length);
    assert.ok(Object.values(contest.maps).every((rows) => rows.length === 32));
    for (const geography of Object.values(contest.geographies)) {
      assert.equal(geography.trajectory.length, years.length);
      assert.equal(Object.keys(geography.elections).length, years.length);
      for (const election of Object.values(geography.elections)) {
        assert.ok(election.totalVotes > 0);
        assert.ok(election.candidacies.length > 0);
        assert.ok(election.parties.length > 0);
        if (contestKey !== "PRE") {
          const rawVotes = election.candidacies.reduce((sum, row) => sum + row.votes, 0);
          const splitVotes = election.parties.reduce((sum, row) => sum + row.votes, 0);
          assert.ok(Math.abs(rawVotes - splitVotes) <= 2, "raw and split votes reconcile");
        }
      }
    }
  }
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
    const { manifest, seats, formerMembers, histories } = payload;
    assert.equal(manifest.schemaVersion, 3, `${slug} exports the occupancy and former-member schema`);
    assert.equal(manifest.chamber, slug);
    assert.ok(manifest.roster.observedAt, `${slug} records its roster cutoff`);
    assert.ok(manifest.roster.sourceUrl.startsWith("https://"));
    assert.deepEqual(summary[slug], manifest, `${slug} digest matches its manifest`);
    assert.ok(formerMembers.length > 0, `${slug} exports interim former legislators`);
    assert.ok(
      formerMembers.every((member) => histories[member.personId]?.length > 0),
      `${slug} gives every former legislator a voting history`,
    );

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

/**
 * Pull the keys of one label map out of `votes.ts`.
 *
 * The maps are TypeScript and this runner cannot import them, so they are read
 * as source. The parse is asserted to have found something before the keys are
 * used: a regex that silently stops matching would otherwise turn the coverage
 * check below into a test that always passes.
 */
function labelKeys(source, name) {
  const block = source.match(
    new RegExp(`export const ${name}: Record<string, string> = \\{([\\s\\S]*?)\\n\\};`),
  );
  assert.ok(block, `votes.ts still declares ${name}`);
  const keys = [...block[1].matchAll(/^\s{2}([A-Za-z_][A-Za-z0-9_]*):/gm)].map((m) => m[1]);
  assert.ok(keys.length > 3, `${name} parsed into real keys`);
  return new Set(keys);
}

test("ships both chambers' roll calls with a labelled, verifiable breakdown", async () => {
  const [votesData, summaryData, votesSource] = await Promise.all([
    readFile(new URL("../public/data/votes-66.json", import.meta.url), "utf8"),
    readFile(new URL("../public/data/visualizaciones.json", import.meta.url), "utf8"),
    readFile(new URL("../app/visualizaciones/votes.ts", import.meta.url), "utf8"),
  ]);
  const payload = JSON.parse(votesData);
  const { manifest, votes, partyVotes } = payload;

  assert.equal(manifest.voteCount, 673);
  assert.equal(manifest.chambers.diputados, 295);
  assert.equal(manifest.chambers.senado, 378);
  assert.equal(votes.length, 673);
  assert.deepEqual(JSON.parse(summaryData).votaciones, manifest, "votaciones digest matches");

  // This payload is the one place the two chambers' vote IDs meet, and they are
  // chamber-local: `5115` is a real Senado vote and a plausible future Camara
  // one. Every key stays namespaced, and every vote resolves through it.
  for (const key of Object.keys(partyVotes)) {
    assert.match(key, /^(diputados|senado):/, `partyVotes key ${key} is namespaced`);
  }
  for (const vote of votes) {
    assert.ok(
      partyVotes[`${vote.chamber}:${vote.id}`],
      `${vote.chamber} ${vote.id} has a party breakdown`,
    );
  }

  // The squares are drawn by expanding these counts, so a breakdown that does
  // not sum to the chamber tally would render a grid of the wrong size.
  for (const vote of votes) {
    const byParty = partyVotes[`${vote.chamber}:${vote.id}`];
    const tally = {};
    for (const counts of Object.values(byParty)) {
      for (const [choice, n] of Object.entries(counts)) tally[choice] = (tally[choice] ?? 0) + n;
    }
    const where = `${vote.chamber} ${vote.id}`;
    assert.equal(tally.Favor ?? 0, vote.favor, `${where} favor totals agree`);
    assert.equal(tally.Contra ?? 0, vote.contra, `${where} contra totals agree`);
    assert.equal(tally["Abstención"] ?? 0, vote.abstention, `${where} abstention totals agree`);
    assert.equal(tally.Ausente ?? 0, vote.absent, `${where} absence totals agree`);
  }

  // Quorum and the majority thresholds key off `total` meaning the whole
  // chamber, which only the Camara tally reports. Exporting them for the Senado
  // would compute a quorum floor against a denominator that already excludes
  // the absent, so their absence there is the invariant, not an omission.
  const camara = votes.filter((vote) => vote.chamber === "diputados");
  const senado = votes.filter((vote) => vote.chamber === "senado");
  assert.ok(camara.every((vote) => vote.thresholds !== null), "Camara votes carry thresholds");
  assert.ok(senado.every((vote) => vote.thresholds === null), "Senado votes carry none");
  assert.ok(
    camara.every((vote) => vote.thresholds.present === vote.favor + vote.contra + vote.abstention + vote.presentNoVote),
    "present equals the members who registered any choice",
  );
  // The daily-issue link is derived from `gaceta_date`, which two LXVI votes do
  // not carry, so its absence is legitimate and the panel omits the link. What
  // must not happen is a malformed one, or the Senado growing a Camara URL.
  assert.ok(
    camara.filter((vote) => vote.gacetaUrl).length >= camara.length - 5,
    "nearly every Camara vote links its Gaceta issue",
  );
  assert.ok(
    camara.every((vote) => vote.gacetaUrl === null || /^https:\/\/gaceta\.diputados\.gob\.mx\//.test(vote.gacetaUrl)),
    "Camara Gaceta links are well formed",
  );
  assert.ok(senado.every((vote) => vote.gacetaUrl === null), "Senado has no Gaceta issue");

  // Every classification code the payload can render must have a written
  // Spanish label. The codes are unaccented snake_case at the source, so a
  // missing one ships as "Organizacion y regimen del congreso" — this is what
  // makes a future classification pass fail the build instead of the page.
  for (const [field, mapName] of [
    ["topic", "TOPIC_LABELS"],
    ["stage", "STAGE_LABELS"],
    ["origin", "ORIGIN_LABELS"],
    ["instrument", "INSTRUMENT_LABELS"],
  ]) {
    const labelled = labelKeys(votesSource, mapName);
    const used = new Set(votes.map((vote) => vote[field]).filter(Boolean));
    assert.ok(used.size > 0, `votes carry a ${field}`);
    assert.deepEqual(
      [...used].filter((code) => !labelled.has(code)),
      [],
      `every ${field} code has a Spanish label`,
    );
  }

  // One concept, two source spellings. The Senado writes `dictamen_de_comisiones`
  // and the Camara `dictamen_de_comision`; uncollapsed they render as two chips
  // meaning the same thing. The minuta codes are genuinely distinct and stay so.
  const origins = new Set(votes.map((vote) => vote.origin));
  assert.ok(!origins.has("dictamen_de_comisiones"), "dictamen spelling is canonicalized");
  assert.ok(origins.has("dictamen_de_comision"));
  assert.ok(
    origins.has("minuta_del_senado") && origins.has("minuta_de_camara_de_diputados"),
    "the two minuta origins stay distinct",
  );

  // Only the Camara ran the deterministic review pass. The Senado must not
  // borrow a status it never earned — an unreviewed label reading as
  // "verificada" is the failure this guards.
  assert.ok(camara.every((vote) => vote.review.status));
  assert.ok(senado.every((vote) => vote.review.status === null));
  // The Senado classification carries a model `confianza` score. It is not
  // published: a number the reader cannot act on reads as precision the label
  // does not have.
  assert.ok(
    votes.every((vote) => !("confidence" in vote.review)),
    "no model confidence reaches the client",
  );
});

test("names every square from the roll call without resizing the grid", async () => {
  const [votesData, ballotData] = await Promise.all([
    readFile(new URL("../public/data/votes-66.json", import.meta.url), "utf8"),
    readFile(new URL("../public/data/vote-ballots-66.json", import.meta.url), "utf8"),
  ]);
  const { partyVotes } = JSON.parse(votesData);
  const { manifest, names, ballots } = JSON.parse(ballotData);

  assert.ok(names.length > 500, "a name dictionary for both chambers");
  assert.equal(new Set(names).size, names.length, "names are deduplicated");
  assert.ok(names.every((name) => name && !/^sen\./i.test(name)), "honorifics stripped");

  // The counts are authoritative and the names only decorate them. Every name
  // list must fit inside the official count for its party and choice: a longer
  // one would draw squares the chamber's own tally does not contain.
  let squares = 0;
  let named = 0;
  for (const [key, byParty] of Object.entries(partyVotes)) {
    for (const [party, counts] of Object.entries(byParty)) {
      for (const [choice, count] of Object.entries(counts)) {
        if (count <= 0) continue;
        squares += count;
        const people = ballots[key]?.[party]?.[choice] ?? [];
        assert.ok(
          people.length <= count,
          `${key} ${party} ${choice}: ${people.length} names for ${count} squares`,
        );
        assert.ok(
          people.every((index) => names[index] !== undefined),
          `${key} ${party} ${choice} resolves every name index`,
        );
        named += people.length;
      }
    }
  }
  assert.equal(squares, manifest.squares);
  assert.equal(named, manifest.namedSquares);

  // Six LXVI Camara roll calls file an independent under `SP` in the per-deputy
  // table and `IND` in the official summary. Those squares stay unnamed rather
  // than being moved to another bench, so coverage is very high but not total —
  // and a collapse in naming (a broken join) must fail rather than degrade.
  assert.ok(named / squares > 0.999, `square naming coverage is ${named}/${squares}`);
  assert.ok(squares - named < 50, "only the known label mismatches go unnamed");
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
  assert.ok(article.includes('class="ca-nav-menu"'), "header dropdowns injected");
  assert.ok(
    article.includes('name="site-navigation"'),
    "article header dropdowns support touch disclosure",
  );
  assert.ok(
    article.includes('href="/visualizaciones/trayectoria"'),
    "article header links to dashboards",
  );
  assert.ok(
    article.includes('href="/articulos/espectro-politico.html"'),
    "article header links to published articles",
  );
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
