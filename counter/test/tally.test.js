// node --test (in counter/)
import { test } from "node:test";
import assert from "node:assert/strict";
import { Invalid, badge, contribution, count, percent, scope, sign } from "../src/tally.js";

const KEY = "a".repeat(64);
const valid = { repository: KEY, pull: 7, caught: 4, fixed: 3, open: 1, pushes: 5, version: "1.2.3" };

test("a contribution keeps only the fields the counter counts", () => {
  const entry = contribution({ ...valid, extra: "ignored", name: "acme/shop" });
  assert.deepEqual(entry, { repository: KEY, pull: 7, caught: 4, fixed: 3, open: 1, pushes: 5, version: "1.2.3" });
});

test("missing counts are zero, and the version is truncated", () => {
  const entry = contribution({ repository: KEY, pull: 1, version: "x".repeat(100) });
  assert.equal(entry.caught, 0);
  assert.equal(entry.version.length, 32);
});

for (const [why, body] of [
  ["a repository name instead of its key", { ...valid, repository: "acme/shop" }],
  ["an upper-case key", { ...valid, repository: "A".repeat(64) }],
  ["no pull request", { ...valid, pull: 0 }],
  ["a fractional count", { ...valid, caught: 1.5 }],
  ["a negative count", { ...valid, pushes: -1 }],
  ["an implausible count", { ...valid, caught: 1_001, fixed: 0, open: 0 }],
  ["more settled than caught", { ...valid, caught: 2, fixed: 2, open: 1 }],
  ["not an object", [valid]],
]) {
  test(`a contribution with ${why} is refused`, () => {
    assert.throws(() => contribution(body), Invalid);
  });
}

test("a repository is found by its key, never by its name", () => {
  assert.deepEqual(scope(new URLSearchParams("")), { key: "*", label: "" });
  assert.deepEqual(scope(new URLSearchParams(`key=${KEY}&name=acme/shop`)), { key: KEY, label: "acme/shop" });
  assert.deepEqual(scope(new URLSearchParams(`key=${KEY}`)), { key: KEY, label: "this repository" });
  assert.throws(() => scope(new URLSearchParams("repository=acme/shop")), Invalid);
  assert.throws(() => scope(new URLSearchParams("key=short")), Invalid);
});

test("badges name their metric", () => {
  const totals = { pulls: 12, caught: 1234, fixed: 1000, open: 3, pushes: 40, repositories: 2 };
  assert.equal(badge(totals).message, "1,234");
  assert.equal(badge(totals, "fixed").label, "fixed before merge");
  assert.equal(badge(totals, "unknown").label, "disagreements caught");
  assert.equal(badge(totals, "repositories").message, "2");
});

test("the sign says what was caught and fixed, in both themes", () => {
  const totals = { pulls: 12, caught: 8, fixed: 6, open: 1, pushes: 40, repositories: 3 };
  for (const theme of ["light", "dark"]) {
    const svg = sign(totals, theme);
    assert.match(svg, /^<svg xmlns="http:\/\/www.w3.org\/2000\/svg" width="1280" height="330" viewBox="0 0 1280 330"/);
    assert.match(svg, /8 disagreements between code and its Quint model caught in pull requests, 6 fixed before merge/);
    assert.match(svg, /75% of those caught/);
    assert.match(svg, /REPOSITORIES/);
    assert.match(svg, /data:image\/png;base64,/);
  }
  assert.notEqual(sign(totals, "light"), sign(totals, "dark"));
});

test("a repository's own sign names it, escaped", () => {
  const svg = sign({ pulls: 1, caught: 1, fixed: 1, open: 0, pushes: 2, repositories: 1 }, "light", "a<b>&c");
  assert.match(svg, /a&lt;b&gt;&amp;c/);
  assert.doesNotMatch(svg, /a<b>/);
  assert.match(svg, /1 disagreement between/);
});

test("numbers read as people write them", () => {
  assert.equal(count(1234567), "1,234,567");
  assert.equal(percent(1, 3), "33%");
  assert.equal(percent(0, 0), "–");
});
