# Publishing pytest-quint-connect

The distribution is `pytest-quint-connect`; imports use `pytest_quint_connect`.
The version has one source of truth: `[project].version` in `pyproject.toml`.

## One-time PyPI setup

The workflow uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
with GitHub OIDC, so no long-lived PyPI API token belongs in GitHub secrets.
A PyPI project owner must register this publisher (or a pending publisher for
the first release):

| Field | Value |
| --- | --- |
| PyPI project | `pytest-quint-connect` |
| GitHub owner | `gigaverse-app` |
| Repository | `pytest-quint-connect` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

Use [pending publishers](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
if the project does not exist. This registration happens in the owner's PyPI
account; committing a workflow does not create that trust relationship.

Create the GitHub `pypi` environment and restrict it to release tags (`v*`). Add
required reviewers according to your maintainer policy. Only the publishing job
receives `id-token: write`; it downloads already-tested artifacts and does not
check out or build source with that permission.

## Prepare and publish

1. Let Release Please open or update the release PR, which updates the version,
   changelog and the root package entry in `uv.lock`.
2. Require green CI: hooks, Linux/Windows/macOS tests with real Quint, minimum
   pytest, metadata, and the installed-wheel smoke run. Review the uploaded evidence.
3. Merge the release PR when ready to publish. After successful main CI,
   Release Please creates tag `v<version>` and publishes its GitHub release.
4. `release.yml` verifies that the tag exactly matches the package version,
   reruns the reusable CI workflow on that revision, and publishes its tested
   wheel and sdist with attestations through the `pypi` environment.
5. Verify the package page and install the exact released version in a clean
   environment: `pip install 'pytest-quint-connect==<version>'`. Run
   `examples/lru_cache` with that installation.

Manual dispatch is available for a version-matching **tag**, not a branch. The
workflow intentionally does not skip existing uploads: investigate a partial
release instead of silently treating it as successful. Published versions are
immutable; fix release defects with a new version and document any yanked release.

## Conventional Commits to PyPI

[Release Please](https://github.com/googleapis/release-please-action) owns version
calculation and the release PR. `.github/workflows/release-please.yml` runs after
successful push CI on the current `main` head. Ordinary feature/fix merges update
the pending release PR; merging that PR is the publishing decision.

| Commit header | Version change |
| --- | --- |
| `fix:`, `perf:`, `revert:` | Patch, such as 0.1.2 to 0.1.3 |
| `feat:` | Minor, such as 0.1.2 to 0.2.0 |
| `feat!:`, `fix!:`, or a `BREAKING CHANGE:` footer | Minor below 1.0; major at 1.0 and later |
| `docs:`, `style:`, `refactor:`, `test:`, `build:`, `ci:`, `chore:` without breaking changes | No release on their own |

Squash with the checked PR title and retain breaking-change details in the body.
No automatic publishing happens just because an ordinary feature PR merges.

```text
Conventional Commit -> successful main CI -> Release Please release PR
Maintainer merges release PR -> successful main CI -> tag + GitHub release
GitHub release -> Publish -> exact-tag full CI -> tested artifacts -> PyPI OIDC
```

The Python release strategy updates `pyproject.toml` and `CHANGELOG.md`; its
standard TOML extra-file updater changes only this project's version in
`uv.lock`. Dependency versions are not upgraded. The manifest starts at the
existing published version, and `bootstrap-sha` bounds the first release's
history to that release commit.

## Release automation setup and recovery

The `release-automation` environment permits only the `main` branch. Share the
existing organization `GV_CI_CROSS_REPO_TOKEN` secret with this repository;
the credential needs Contents and Pull requests write access. It creates release
PRs and GitHub releases so their events trigger the normal CI and Publish
workflows. Do not replace it with `GITHUB_TOKEN`, which suppresses those events.
The existing `pypi` environment remains restricted to `v*` tags.

PyPI authentication remains [Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/):
no PyPI API token is stored. The publisher identity remains this repository's
`release.yml` workflow and `pypi` environment. Only its isolated upload job
receives `id-token: write`.

If release preparation fails, fix the cause and rerun the successful main CI run
to trigger Release Please again. If publication fails after a tag was created,
rerun the failed Publish run, or dispatch Publish on that exact `v<version>`
tag. It rechecks the tag/version match and full CI before uploading. Never move
an existing full release tag or reuse a published version for changed contents.
Prereleases remain a deliberate manual version/tag/release path; this automation
proposes normal SemVer releases.
