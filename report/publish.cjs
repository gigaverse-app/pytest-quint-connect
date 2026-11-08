// Publishes the Markdown this action's renderer wrote, as one comment per pull request and sticky key.
// Modeled on pytest-obligation's report publisher: the same ownership, ordering and head checks.
// Only that rendered file crosses this boundary; nothing here runs project or pull-request code.
const fs = require('node:fs');
const crypto = require('node:crypto');

// GitHub refuses comments over 65,536 characters; the renderer already truncates below this.
const BUDGET = 62000;
// The publisher always ends a sticky with its snapshot and marker. Only that trailer is read,
// so text rendered inside the report can never stand in for either.
const TRAILER = /\r?\n<!-- pytest-quint-connect-snapshot:(.*?) -->\r?\n(<!-- pytest-quint-connect:[0-9a-f]{24} -->)\s*$/;

function positive(value, what) {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 1) throw new Error(`A positive ${what} is required.`);
  return number;
}

function markerFor(stickyKey) {
  const key = crypto.createHash('sha256').update(stickyKey || 'quint-connect').digest('hex').slice(0, 24);
  return `<!-- pytest-quint-connect:${key} -->`;
}

// The bot-owned comment that ends with this key's trailer, with that trailer's match. Only the trailer is
// read, and only from a github-actions[bot] comment, so a person's comment can never stand in for it.
async function findSticky({github, context}, number, stickyKey) {
  const marker = markerFor(stickyKey);
  const comments = await github.paginate(github.rest.issues.listComments, {...context.repo, issue_number: number, per_page: 100});
  for (const comment of comments) {
    const match = (comment.body || '').match(TRAILER);
    if (comment.user?.login === 'github-actions[bot]' && comment.user?.type === 'Bot' && match?.[2] === marker) {
      return {comment, match};
    }
  }
  return null;
}

// The renderer extends the previous comment's ledger, so this run counts what earlier pushes caught.
// Writes the previous comment's body (or nothing) to QUINT_REPORT_PREVIOUS; never fails the job.
async function readPrevious({github, context, core}, env = process.env) {
  const target = env.QUINT_REPORT_PREVIOUS;
  fs.writeFileSync(target, '');
  const pr = context.payload.pull_request;
  if (context.eventName !== 'pull_request' || !pr) return false;
  try {
    const found = await findSticky({github, context}, pr.number, env.QUINT_REPORT_STICKY_KEY);
    if (!found) return false;
    fs.writeFileSync(target, found.comment.body);
    return true;
  } catch (error) {
    core.warning(`The previous report could not be read (${error.status || error.message}); this run starts a new tally.`);
    return false;
  }
}

// One bot comment per pull request and key. An older run or attempt never replaces a newer one,
// and evidence for a head the pull request has moved past never replaces the current report.
async function publishSticky({github, context, core}, {pullRequest, headSha, runId, runAttempt, stickyKey, markdownPath}) {
  const number = positive(pullRequest, 'pull request number');
  const marker = markerFor(stickyKey);
  const snapshot = {head: headSha, run: positive(runId, 'run ID'), attempt: positive(runAttempt, 'run attempt')};
  const body = fs.readFileSync(markdownPath, 'utf8');
  if (body.length > BUDGET) throw new Error('The rendered report exceeds the comment budget.');
  const metadata = `<!-- pytest-quint-connect-snapshot:${JSON.stringify(snapshot)} -->`;
  try {
    const {data: current} = await github.rest.pulls.get({...context.repo, pull_number: number});
    if (current.head.sha !== snapshot.head) {
      core.info('The pull request moved to a newer head; this report stays in the summary and artifact.');
      return false;
    }
    const found = await findSticky({github, context}, number, stickyKey);
    const existing = found?.comment;
    if (found) {
      let previous;
      try { previous = JSON.parse(found.match[1]); } catch { /* Repair malformed bot-owned metadata. */ }
      if (previous && (previous.run > snapshot.run || (previous.run === snapshot.run && previous.attempt > snapshot.attempt))) {
        core.info('A newer run or attempt already owns this comment.');
        return false;
      }
    }
    // Recheck after pagination. GitHub's comment API has no compare-and-swap; callers that report
    // from several jobs use distinct sticky keys.
    const {data: latest} = await github.rest.pulls.get({...context.repo, pull_number: number});
    if (latest.head.sha !== snapshot.head) return false;
    const payload = {...context.repo, body: `${body}\n\n${metadata}\n${marker}`};
    if (existing) await github.rest.issues.updateComment({...payload, comment_id: existing.id});
    else await github.rest.issues.createComment({...payload, issue_number: number});
    return true;
  } catch (error) {
    if ([403, 404].includes(error.status)) {
      core.warning('This token cannot comment on the pull request; the report is in the job summary and artifact.');
      return false;
    }
    throw error;
  }
}

const FORK_NOTE = 'Fork pull request: this run\'s token cannot comment, so the contract report is in the job '
  + 'summary and the quint-connect artifact.';

async function publish({github, context, core}, env = process.env) {
  const pr = context.payload.pull_request;
  if (context.eventName !== 'pull_request' || !pr) {
    core.info('No pull request; the report is in the job summary and artifact.');
    return false;
  }
  if (pr.head.repo?.full_name !== `${context.repo.owner}/${context.repo.repo}`) {
    core.notice(FORK_NOTE);
    if (core.summary) await core.summary.addRaw(`\n> [!NOTE]\n> ${FORK_NOTE}\n`).write();
    return false;
  }
  return publishSticky({github, context, core}, {
    pullRequest: pr.number,
    headSha: pr.head.sha,
    runId: env.GITHUB_RUN_ID,
    runAttempt: env.GITHUB_RUN_ATTEMPT,
    stickyKey: env.QUINT_REPORT_STICKY_KEY,
    markdownPath: env.QUINT_REPORT_MARKDOWN,
  });
}

// The global counter (counter/ in this repository): what every repository's contracts caught in pull requests.
const COUNTER = 'https://pytest-quint-connect-counter.gigaverse-oss.workers.dev';
const LEDGER = /<!-- pytest-quint-connect-ledger (\{.*?\}) -->/s;
// The most recently updated merged pull requests are re-sent on every default-branch run: the counter counts each
// repository and number once, so a resend changes nothing and a run that failed is caught up by the next.
const RECENT = 30;

// The counter key: pseudonymous, so a repository's pull requests are tied together but its name never leads to it.
function counterKey(repositoryId) {
  return crypto.createHash('sha256').update(`github-repository:${repositoryId}`).digest('hex');
}

// One merged pull request's tally, from the ledger its sticky carried: what the counter is sent, and nothing else.
function contribution(key, number, body, version) {
  const match = (body || '').match(LEDGER);
  if (!match) return null;
  let ledger;
  try { ledger = JSON.parse(match[1]); } catch { return null; }
  if (!ledger || ledger.schema !== 1) return null;
  const caught = Object.values(ledger.caught || {});
  return {
    repository: key, pull: number, caught: caught.length,
    fixed: caught.filter(entry => entry.state === 'fixed').length,
    open: caught.filter(entry => entry.state === 'open').length,
    pushes: (ledger.pushes || []).length, version: String(version || '').slice(0, 32),
  };
}

// On a push to the default branch, send the counter each recently merged pull request's tally. Never fails the job:
// a send that fails is sent again by the next default-branch run. `share-totals: "false"` sends nothing.
async function share({github, context, core}, env = process.env, post = fetch) {
  const branch = context.payload.repository?.default_branch;
  if (context.eventName !== 'push' || !branch || context.ref !== `refs/heads/${branch}`) return 0;
  if (['false', '0', 'no'].includes(String(env.QUINT_REPORT_SHARE_TOTALS || 'true').toLowerCase())) return 0;
  if (!env.GITHUB_REPOSITORY_ID) return 0;
  const key = counterKey(env.GITHUB_REPOSITORY_ID);
  const counter = env.QUINT_CONNECT_COUNTER || COUNTER;
  let counted = 0;
  try {
    const {data: pulls} = await github.rest.pulls.list({
      ...context.repo, state: 'closed', base: branch, sort: 'updated', direction: 'desc', per_page: RECENT,
    });
    for (const pull of pulls.filter(p => p.merged_at)) {
      const found = await findSticky({github, context}, pull.number, env.QUINT_REPORT_STICKY_KEY);
      const entry = found && contribution(key, pull.number, found.comment.body, env.QUINT_REPORT_VERSION);
      if (!entry) continue;
      const response = await post(`${counter}/v1/pulls`, {
        method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(entry),
      });
      if (response.ok && (await response.json()).counted) counted += 1;
    }
  } catch (error) {
    core.warning(`The counter could not be updated (${error.status || error.message}); the next default-branch run retries.`);
  }
  core.info(`pytest-quint-connect: ${counted} newly counted pull request(s). This repository's counter key is ${key}; `
    + `its sign is ${counter}/v1/sign.svg?key=${key}`);
  return counted;
}

module.exports = publish;
module.exports.share = share;
module.exports.counterKey = counterKey;
module.exports.contribution = contribution;
module.exports.publish = publish;
module.exports.publishSticky = publishSticky;
module.exports.markerFor = markerFor;
module.exports.readPrevious = readPrevious;
