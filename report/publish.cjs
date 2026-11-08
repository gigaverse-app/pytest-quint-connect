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
    const comments = await github.paginate(github.rest.issues.listComments, {...context.repo, issue_number: number, per_page: 100});
    const trailer = comment => (comment.body || '').match(TRAILER);
    const existing = comments.find(c => c.user?.login === 'github-actions[bot]' && c.user?.type === 'Bot' && trailer(c)?.[2] === marker);
    if (existing) {
      let previous;
      try { previous = JSON.parse(trailer(existing)[1]); } catch { /* Repair malformed bot-owned metadata. */ }
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

module.exports = publish;
module.exports.publish = publish;
module.exports.publishSticky = publishSticky;
module.exports.markerFor = markerFor;
