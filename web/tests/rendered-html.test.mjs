import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the Brújula Legislativa shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /<title>Brújula Legislativa — Así vota el pleno<\/title>/i);
  assert.match(html, /Preparando el pleno/);
  assert.match(html, /og\.png/);
  assert.doesNotMatch(html, /codex-preview|SkeletonPreview|Your site is taking shape/i);
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
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
});
