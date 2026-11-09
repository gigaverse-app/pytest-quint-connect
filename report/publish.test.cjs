// node --test report/publish.test.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const publish = require('./publish.cjs');

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'quint-publish-'));
  t.after(() => fs.rmSync(dir, {recursive: true, force: true}));
  const file = path.join(dir, 'report.md');
  fs.writeFileSync(file, '### ✅ Quint contracts: 1 passed');
  const calls = [], comments = [], notices = [], summary = [];
  const state = {head: 'abc', forbidden: false};
  const github = {rest: {
    pulls: {get: async () => ({data: {head: {sha: state.head}}})},
    issues: {
      listComments: () => {},
      createComment: async args => { if (state.forbidden) throw {status: 403}; calls.push(['create', args]); },
      updateComment: async args => calls.push(['update', args]),
    },
  }};
  github.paginate = async () => comments;
  const context = {repo: {owner: 'owner', repo: 'app'}, eventName: 'pull_request',
    payload: {pull_request: {number: 12, head: {sha: 'abc', repo: {full_name: 'owner/app'}}}}};
  const core = {info: x => notices.push(x), warning: x => notices.push(x), notice: x => notices.push(x),
    summary: {addRaw(x) { summary.push(x); return this; }, async write() {}}};
  const env = {QUINT_REPORT_MARKDOWN: file, QUINT_REPORT_STICKY_KEY: 'quint-connect', GITHUB_RUN_ID: '100', GITHUB_RUN_ATTEMPT: '1'};
  return {calls, comments, notices, summary, state, context, env, file, run: () => publish({github, context, core}, env)};
}

const bot = body => ({id: 42, body, user: {login: 'github-actions[bot]', type: 'Bot'}});

test('the same key updates one bot-owned comment and records its source run', async t => {
  const f = fixture(t);
  assert.equal(await f.run(), true);
  assert.equal(f.calls[0][0], 'create');
  const body = f.calls[0][1].body;
  assert.ok(body.startsWith('### ✅ Quint contracts: 1 passed'));
  assert.ok(body.includes('"head":"abc","run":100,"attempt":1'));
  f.comments.push(bot(body));
  await f.run();
  assert.equal(f.calls[1][0], 'update');
  assert.equal(f.calls[1][1].comment_id, 42);
});

test('a human comment carrying the marker is never overwritten', async t => {
  const f = fixture(t);
  await f.run();
  f.comments.push({id: 42, body: f.calls[0][1].body, user: {login: 'someone', type: 'User'}});
  await f.run();
  assert.equal(f.calls[1][0], 'create');
});

test('a marker inside the report body cannot claim the comment', async t => {
  const f = fixture(t);
  await f.run();
  const marker = publish.markerFor('quint-connect');
  f.comments.push(bot(`${marker}\nwritten by something else`));
  await f.run();
  assert.equal(f.calls[1][0], 'create');
});

test('different sticky keys keep separate comments', async t => {
  const f = fixture(t);
  await f.run();
  f.comments.push(bot(f.calls[0][1].body));
  f.env.QUINT_REPORT_STICKY_KEY = 'nightly';
  await f.run();
  assert.equal(f.calls[1][0], 'create');
});

for (const change of ['head', 'run', 'attempt']) test(`an older ${change} never replaces fresher evidence`, async t => {
  const f = fixture(t);
  await f.run();
  f.comments.push(bot(f.calls[0][1].body));
  if (change === 'head') f.state.head = 'newer-head';
  if (change === 'run') f.env.GITHUB_RUN_ID = '99';
  if (change === 'attempt') f.comments[0].body = f.comments[0].body.replace('"attempt":1', '"attempt":2');
  assert.equal(await f.run(), false);
  assert.equal(f.calls.length, 1);
});

test('fork pull requests only leave a summary note', async t => {
  const f = fixture(t);
  f.context.payload.pull_request.head.repo.full_name = 'fork/app';
  assert.equal(await f.run(), false);
  assert.equal(f.calls.length, 0);
  assert.match(f.summary.join(''), /Fork pull request/);
});

test('a token without comment access warns instead of failing the job', async t => {
  const f = fixture(t);
  f.state.forbidden = true;
  assert.equal(await f.run(), false);
  assert.match(f.notices.join('\n'), /cannot comment/);
});

test('push runs publish nothing', async t => {
  const f = fixture(t);
  f.context.eventName = 'push';
  assert.equal(await f.run(), false);
  assert.equal(f.calls.length, 0);
});

test('the previous report is read from the bot-owned sticky only', async t => {
  const f = fixture(t);
  await f.run();
  const previous = path.join(path.dirname(f.file), 'previous.md');
  f.env.QUINT_REPORT_PREVIOUS = previous;
  const github = {rest: {issues: {listComments: () => {}}}, paginate: async () => f.comments};
  const core = {warning: () => {}};
  f.comments.push({id: 7, body: f.calls[0][1].body.replace('1 passed', 'a person'), user: {login: 'someone', type: 'User'}});
  assert.equal(await publish.readPrevious({github, context: f.context, core}, f.env), false);
  assert.equal(fs.readFileSync(previous, 'utf8'), '');
  f.comments.push(bot(f.calls[0][1].body));
  assert.equal(await publish.readPrevious({github, context: f.context, core}, f.env), true);
  assert.ok(fs.readFileSync(previous, 'utf8').startsWith('### ✅ Quint contracts: 1 passed'));
});

test('a failed read starts a new tally instead of failing the job', async t => {
  const f = fixture(t);
  const previous = path.join(path.dirname(f.file), 'previous.md');
  f.env.QUINT_REPORT_PREVIOUS = previous;
  const warnings = [];
  const github = {rest: {issues: {listComments: () => {}}}, paginate: async () => { throw {status: 403}; }};
  assert.equal(await publish.readPrevious({github, context: f.context, core: {warning: x => warnings.push(x)}}, f.env), false);
  assert.equal(fs.readFileSync(previous, 'utf8'), '');
  assert.match(warnings[0], /starts a new tally/);
});

function pushFixture({ledgerBody, mergedAt = '2026-11-08T00:00:00Z', ref = 'refs/heads/main'} = {}) {
  const posted = [];
  const comments = ledgerBody === undefined ? [] : [bot(ledgerBody)];
  const github = {rest: {
    pulls: {list: async () => ({data: [{number: 3, merged_at: mergedAt}, {number: 4, merged_at: null}]})},
    issues: {listComments: () => {}},
  }};
  github.paginate = async () => comments;
  const context = {repo: {owner: 'owner', repo: 'app'}, eventName: 'push', ref, payload: {repository: {default_branch: 'main'}}};
  const notes = [];
  const core = {info: x => notes.push(x), warning: x => notes.push(x)};
  const env = {GITHUB_REPOSITORY_ID: '42', QUINT_REPORT_STICKY_KEY: 'quint-connect', QUINT_REPORT_VERSION: '1.0.0'};
  const post = async (url, request) => {
    posted.push([url, JSON.parse(request.body)]);
    return {ok: true, json: async () => ({counted: true})};
  };
  return {github, context, core, env, post, posted, notes, run: () => publish.share({github, context, core}, env, post)};
}

function stickyWith(ledger) {
  const marker = publish.markerFor('quint-connect');
  const snapshot = '<!-- pytest-quint-connect-snapshot:{"head":"abc","run":1,"attempt":1} -->';
  return `### report\n<!-- pytest-quint-connect-ledger ${JSON.stringify(ledger)} -->\n\n${snapshot}\n${marker}`;
}

const LEDGER = {schema: 1, pushes: [{push: 1}, {push: 2}], caught: {
  a: {found: 1, kind: 'mismatch', state: 'fixed'}, b: {found: 1, kind: 'survived', state: 'open'},
}};

test('a default-branch push sends each merged pull request\'s tally, keyed by the repository id hash', async () => {
  const f = pushFixture({ledgerBody: stickyWith(LEDGER)});
  assert.equal(await f.run(), 1);
  const [[url, entry]] = f.posted;
  assert.equal(url, 'https://pytest-quint-connect-counter.gigaverse-oss.workers.dev/v1/pulls');
  assert.deepEqual(entry, {repository: publish.counterKey('42'), pull: 3, caught: 2, fixed: 1, open: 1, pushes: 2, version: '1.0.0'});
  assert.match(publish.counterKey('42'), /^[0-9a-f]{64}$/);
  assert.match(f.notes.join('\n'), /counter key is [0-9a-f]{64}/);
});

test('nothing is sent off the default branch, when opted out, or without a ledger', async () => {
  assert.equal(await pushFixture({ledgerBody: stickyWith(LEDGER), ref: 'refs/heads/feature'}).run(), 0);
  const optedOut = pushFixture({ledgerBody: stickyWith(LEDGER)});
  optedOut.env.QUINT_REPORT_SHARE_TOTALS = 'false';
  assert.equal(await optedOut.run(), 0);
  assert.equal(optedOut.posted.length, 0);
  const none = pushFixture();
  assert.equal(await none.run(), 0);
  assert.equal(none.posted.length, 0);
});

test('a counter that cannot be reached never fails the job', async () => {
  const f = pushFixture({ledgerBody: stickyWith(LEDGER)});
  const failing = async () => { throw new Error('offline'); };
  assert.equal(await publish.share({github: f.github, context: f.context, core: f.core}, f.env, failing), 0);
  assert.match(f.notes.join('\n'), /next default-branch run retries/);
});

test('the action pins every action it runs to a commit, so a consumer that pins by SHA pins the whole chain', () => {
  // A moving tag (`@v7`) inside a composite action defeats a caller's SHA pin of this action.
  const action = fs.readFileSync(path.join(__dirname, 'action.yml'), 'utf8');
  const uses = [...action.matchAll(/^\s*(?:-\s+)?uses:\s*(.+)$/gm)].map(match => match[1].trim());
  assert.ok(uses.length >= 3, uses.join('\n'));
  for (const reference of uses) {
    assert.match(reference, /^[\w.-]+\/[\w./-]+@[0-9a-f]{40} {2}# v\d+\.\d+\.\d+$/, reference);
  }
});
