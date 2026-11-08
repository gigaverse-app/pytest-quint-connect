/**
 * pytest-quint-connect's global counter: every disagreement between code and its Quint model that a contract caught
 * in a pull request, across every repository whose report action shares its totals.
 *
 *   POST /v1/pulls                     one merged pull request's tally; counted once per repository and number
 *   GET  /v1/totals[?key=]             the totals, as JSON: everyone's, or one repository's by its counter key
 *   GET  /v1/badge[?metric=&key=]      a shields.io endpoint badge: caught, fixed, pulls, repositories
 *   GET  /v1/sign.svg[?theme=dark&key=&name=]  the README sign; `name` only labels a repository's own sign
 *
 * One SQLite-backed Durable Object holds the counts: a contribution is inserted once (the repository and pull request
 * number are its key) and the running totals move only when a row is new, so a retried or repeated send changes
 * nothing. Reads are cached for a minute. The same design as dry-lint's counter.
 */

import { DurableObject } from "cloudflare:workers";
import { FIELDS, Invalid, TOTALED, badge, contribution, scope, sign } from "./tally.js";

const EMPTY = { pulls: 0, ...Object.fromEntries(TOTALED.map((field) => [field, 0])), since: null };

export class Counter extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS pulls (
        repository TEXT NOT NULL, pull INTEGER NOT NULL, ${FIELDS.map((f) => `${f} INTEGER DEFAULT 0`).join(", ")},
        version TEXT, at TEXT NOT NULL, PRIMARY KEY (repository, pull));
      CREATE TABLE IF NOT EXISTS totals (
        scope TEXT PRIMARY KEY, pulls INTEGER, ${TOTALED.map((f) => `${f} INTEGER DEFAULT 0`).join(", ")},
        since TEXT);`);
    // Tables made before a field existed gain it, at zero: the counter counts it from the day it was added.
    for (const [table, fields] of [["pulls", FIELDS], ["totals", TOTALED]]) {
      const present = new Set(this.sql.exec(`SELECT name FROM pragma_table_info('${table}')`).toArray().map((c) => c.name));
      for (const field of fields.filter((f) => !present.has(f))) {
        this.sql.exec(`ALTER TABLE ${table} ADD COLUMN ${field} INTEGER DEFAULT 0`);
      }
    }
  }

  /** Count one merged pull request; false when it was counted already. */
  record(entry) {
    const at = new Date().toISOString();
    const columns = ["repository", "pull", ...FIELDS, "version", "at"];
    const inserted = this.sql.exec(
      `INSERT OR IGNORE INTO pulls (${columns.join(", ")}) VALUES (${columns.map(() => "?").join(", ")})`,
      ...columns.map((column) => (column === "at" ? at : entry[column]))).rowsWritten > 0;
    if (inserted) {
      for (const scope of ["*", entry.repository]) {
        this.sql.exec(
          `INSERT INTO totals (scope, pulls, ${TOTALED.join(", ")}, since) VALUES (?, 1, ${TOTALED.map(() => "?").join(", ")}, ?)
           ON CONFLICT (scope) DO UPDATE SET pulls = pulls + 1,
             ${TOTALED.map((f) => `${f} = ${f} + excluded.${f}`).join(", ")}`,
          scope, ...TOTALED.map((field) => entry[field]), at);
      }
    }
    return inserted;
  }

  /** Totals for every repository ("*"), or for one by its key. */
  totals(scope = "*") {
    const [row] = this.sql.exec(`SELECT * FROM totals WHERE scope = ?`, scope).toArray();
    const repositories = scope === "*" ? this.sql.exec(`SELECT COUNT(*) AS n FROM totals`).one().n - (row ? 1 : 0) : 1;
    if (!row) return { ...EMPTY, repositories: 0 };
    const { scope: _, ...figures } = row;
    return { ...figures, repositories };
  }
}

function json(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });
}

async function read(request, env, ctx) {
  const cache = caches.default;
  const cached = await cache.match(request);
  if (cached) return cached;
  const url = new URL(request.url);
  let asked;
  try {
    asked = scope(url.searchParams);
  } catch (error) {
    if (error instanceof Invalid) return json({ error: error.message }, 400);
    throw error;
  }
  const counter = env.COUNTER.get(env.COUNTER.idFromName("global"));
  const totals = await counter.totals(asked.key);
  const caching = { "cache-control": "public, max-age=60" };
  let response;
  if (url.pathname === "/v1/totals") {
    response = json(totals, 200, caching);
  } else if (url.pathname === "/v1/badge") {
    response = json(badge(totals, url.searchParams.get("metric") ?? "caught"), 200, caching);
  } else {
    response = new Response(sign(totals, url.searchParams.get("theme") ?? "light", asked.label), {
      headers: { "content-type": "image/svg+xml; charset=utf-8", ...caching },
    });
  }
  ctx.waitUntil(cache.put(request, response.clone()));
  return response;
}

async function write(request, env) {
  if (Number(request.headers.get("content-length") ?? 0) > 4096) return json({ error: "too large" }, 413);
  let entry;
  try {
    entry = contribution(await request.json());
  } catch (error) {
    if (error instanceof Invalid || error instanceof SyntaxError) return json({ error: error.message }, 400);
    throw error;
  }
  const counter = env.COUNTER.get(env.COUNTER.idFromName("global"));
  return json({ counted: await counter.record(entry) });
}

export default {
  async fetch(request, env, ctx) {
    const { pathname } = new URL(request.url);
    if (request.method === "POST" && pathname === "/v1/pulls") return write(request, env);
    if (request.method === "GET" && ["/v1/totals", "/v1/badge", "/v1/sign.svg"].includes(pathname)) {
      return read(request, env, ctx);
    }
    if (request.method === "GET" && pathname === "/") {
      return Response.redirect("https://github.com/gigaverse-app/pytest-quint-connect#impact", 302);
    }
    return json({ error: "not found" }, 404);
  },
};
