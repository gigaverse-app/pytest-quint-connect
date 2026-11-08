/**
 * The counter's rules, free of Cloudflare: what a contribution may say, and how the totals are shown.
 *
 * A contribution is one merged pull request's tally, sent once by the report action on its repository's default
 * branch. A *disagreement* is a history where the code reached a different state than the Quint model, or a mutant
 * no history caught; the pull request's comment counted the ones it caught, how many were fixed before merge, and
 * how many were still failing when it merged. The repository arrives as its counter key, the SHA-256 of its numeric
 * GitHub id, never its name: pseudonymous, since a name alone does not lead to the key. Counts are self-reported, so
 * each is bounded. Modeled on dry-lint's counter (github.com/gigaverse-app/dry-lint/tree/main/counter).
 */

import { QUINTY } from "./quinty.js";

export const LIMITS = { caught: 1_000, fixed: 1_000, open: 1_000, pushes: 10_000 };
export const FIELDS = Object.keys(LIMITS);
/** The fields the totals sum. */
export const TOTALED = FIELDS;
export const PURPLE = "6E56CF";

export class Invalid extends Error {}

/** A contribution, checked and reduced to the fields the counter keeps; throws Invalid otherwise. */
export function contribution(body) {
  if (typeof body !== "object" || body === null || Array.isArray(body)) throw new Invalid("expected a JSON object");
  if (typeof body.repository !== "string" || !/^[0-9a-f]{64}$/.test(body.repository)) {
    throw new Invalid("repository must be the repository's counter key: 64 lower-case hex characters");
  }
  if (!Number.isInteger(body.pull) || body.pull < 1 || body.pull > 10_000_000) {
    throw new Invalid("pull must be a pull request number");
  }
  const entry = { repository: body.repository, pull: body.pull };
  for (const field of FIELDS) {
    const value = body[field] ?? 0;
    if (!Number.isInteger(value) || value < 0 || value > LIMITS[field]) {
      throw new Invalid(`${field} must be a whole number from 0 to ${LIMITS[field]}`);
    }
    entry[field] = value;
  }
  if (entry.fixed + entry.open > entry.caught) throw new Invalid("fixed and open cannot exceed caught");
  entry.version = typeof body.version === "string" ? body.version.slice(0, 32) : "";
  return entry;
}

/**
 * Which totals a read asks for: everyone's, or one repository's by its counter key, which the action prints on the
 * repository's default-branch runs. A name never finds a repository: names can be guessed, keys cannot. `name` only
 * labels the sign, for a repository that chooses to show its own.
 */
export function scope(params) {
  if (params.has("repository")) {
    throw new Invalid("a repository is found by its counter key (?key=), printed by the report action on its default branch");
  }
  const key = params.get("key") ?? "";
  if (!key) return { key: "*", label: "" };
  if (!/^[0-9a-f]{64}$/.test(key)) throw new Invalid("key must be 64 lower-case hex characters");
  return { key, label: (params.get("name") ?? "").slice(0, 100) || "this repository" };
}

export function count(value) {
  return Math.round(value).toLocaleString("en-US");
}

/** A whole-number share, or a dash when there is nothing to share. */
export function percent(part, whole) {
  return whole ? `${Math.round((100 * part) / whole)}%` : "–";
}

const METRICS = {
  caught: (t) => ["disagreements caught", count(t.caught)],
  fixed: (t) => ["fixed before merge", count(t.fixed)],
  pulls: (t) => ["pull requests checked", count(t.pulls)],
  repositories: (t) => ["repositories", count(t.repositories)],
};

/** pytest-quint-connect's mark for a shields.io badge: a replayed trace, model states over matched code states. */
const MINT = "#86EFAC";
const LOGO = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M4 7h16" stroke="#C4B5FD" stroke-width="2"/><circle cx="5" cy="7" r="2.4" fill="#C4B5FD"/><circle cx="12" cy="7" r="2.4" fill="#C4B5FD"/><circle cx="19" cy="7" r="2.4" fill="#C4B5FD"/><rect x="3" y="15" width="4" height="4" rx="1" fill="${MINT}"/><rect x="10" y="15" width="4" height="4" rx="1" fill="${MINT}"/><rect x="17" y="15" width="4" height="4" rx="1" fill="${MINT}"/></svg>`;

/** A shields.io endpoint badge: https://shields.io/badges/endpoint-badge */
export function badge(totals, metric = "caught") {
  const [label, message] = (METRICS[metric] ?? METRICS.caught)(totals);
  return { schemaVersion: 1, label, message, color: PURPLE, labelColor: "24292F", logoSvg: LOGO, cacheSeconds: 300 };
}

const THEMES = {
  light: {
    background: "#FFFFFF", wash: "#F3ECFD", border: "#D1D9E0", text: "#1F2328", muted: "#59636E", accent: "#6E56CF",
    rule: "#E4DAF5", tile: "#FFFFFF", strip: "#FAF7FF",
  },
  dark: {
    background: "#0D1117", wash: "#1A1030", border: "#30363D", text: "#F0F6FC", muted: "#9198A1", accent: "#C4B5FD",
    rule: "#2D2440", tile: "#161B22", strip: "#120D1F",
  },
};
const FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif";
const FIXED = "#3FB950", OPEN = "#F87171";

function text(x, y, size, weight, fill, content, extra = "") {
  return `<text x="${x}" y="${y}" font-family="${FONT}" font-size="${size}" font-weight="${weight}" fill="${fill}"${extra}>${content}</text>`;
}

/** One benefit: a large number, what it counts, and a note. The number shrinks to fit a tile. */
function tile(c, x, color, value, label, note) {
  const size = Math.min(60, Math.floor(272 / (0.58 * value.length)));
  return `<rect x="${x}" y="28" width="330" height="182" rx="16" fill="${c.tile}" stroke="${c.border}"/>
  <rect x="${x}" y="46" width="6" height="146" rx="3" fill="${color}"/>
  ${text(x + 28, 110, size, 800, c.text, value, ' letter-spacing="-1"')}
  ${text(x + 28, 144, 17, 700, c.text, label)}
  ${text(x + 28, 170, 14, 400, c.muted, note)}`;
}

/**
 * The sign: what pytest-quint-connect's contracts caught in pull requests, in three tiles, over a strip of what it
 * checked to get there. For a README, in a <picture> by theme; it scales to any width. `scope` is "" for every
 * repository, or how a repository's own sign names it.
 */
export function sign(totals, theme = "light", scope = "") {
  const c = THEMES[theme] ?? THEMES.light;
  const one = (n, singular, many) => (n === 1 ? singular : many);
  const shown = scope.length > 30 ? `${scope.slice(0, 29)}…` : scope;
  const strip = [
    [count(totals.pulls), one(totals.pulls, "PULL REQUEST CHECKED", "PULL REQUESTS CHECKED")],
    [count(totals.pushes), one(totals.pushes, "PUSH REPLAYED", "PUSHES REPLAYED")],
    scope ? [escape(shown), "REPOSITORY"]
      : [count(totals.repositories), one(totals.repositories, "REPOSITORY", "REPOSITORIES")],
  ];
  const label = escape(
    `${count(totals.caught)} ${one(totals.caught, "disagreement", "disagreements")} between code and its Quint model `
      + `caught in ${scope || "pull requests"}, ${count(totals.fixed)} fixed before merge, `
      + `across ${count(totals.pulls)} ${one(totals.pulls, "pull request", "pull requests")}`,
  );
  const width = 1278 / strip.length;
  const cells = strip.map(([value, name], i) => {
    const x = 1 + width * i + width / 2;
    return `${text(x, 278, i === 2 && scope ? 20 : 26, 800, c.text, value, ' text-anchor="middle"')}
  ${text(x, 302, 12, 700, c.muted, name, ' text-anchor="middle" letter-spacing="1.2"')}${i ? `
  <line x1="${1 + width * i}" y1="256" x2="${1 + width * i}" y2="312" stroke="${c.rule}" stroke-width="2"/>` : ""}`;
  });
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="330" viewBox="0 0 1280 330" role="img" aria-label="${label}">
  <title>${label}</title>
  <defs>
    <linearGradient id="wash" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c.background}"/><stop offset="1" stop-color="${c.wash}"/></linearGradient>
    <clipPath id="card"><rect x="1" y="1" width="1278" height="328" rx="22"/></clipPath>
  </defs>
  <rect x="1" y="1" width="1278" height="328" rx="22" fill="${c.background}"/>
  <rect x="1" y="1" width="1278" height="328" rx="22" fill="url(#wash)"/>
  <circle cx="122" cy="120" r="92" fill="${c.accent}" opacity="0.14"/>
  <image href="${QUINTY}" x="30" y="28" width="184" height="184"/>
  ${tile(c, 236, c.accent, count(totals.caught), one(totals.caught, "disagreement caught", "disagreements caught"),
    "code vs. its model, in pull requests")}
  ${tile(c, 580, FIXED, count(totals.fixed), "fixed before merge", `${percent(totals.fixed, totals.caught)} of those caught`)}
  ${tile(c, 924, OPEN, count(totals.open), "merged still failing", "known, and reported on the PR")}
  <rect x="1" y="236" width="1278" height="93" fill="${c.strip}" clip-path="url(#card)"/>
  <line x1="1" y1="236" x2="1279" y2="236" stroke="${c.border}" stroke-width="2"/>
  ${cells.join("\n  ")}
  <rect x="1" y="1" width="1278" height="328" rx="22" fill="none" stroke="${c.border}" stroke-width="2"/>
</svg>
`;
}

function escape(text) {
  return text.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
}
