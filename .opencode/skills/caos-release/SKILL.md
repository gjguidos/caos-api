---
name: caos-release
description: Guide CAOS API feature-to-alpha-to-master promotion, release readiness checks, and GitHub/GHCR verification without bypassing approval or CI.
---

# CAOS Release Assistant

Use when asked to prepare a feature branch for alpha, inspect a release, open or review an `alpha` → `master` promotion, investigate semantic-release/GHCR failures, or verify version metadata.

## Sources of truth

- `docs/RELEASE_WORKFLOW.md`
- `.releaserc.json`
- `.github/workflows/release.yml`
- `pyproject.toml`, `Dockerfile`, Git state, and actual GitHub release artifacts

Do not assume the latest version based on a remembered release. Discover existing tags and published release state before proposing commands.

## Modes

### Feature-to-alpha readiness
1. Read current branch and worktree (`git status -sb`, `git branch --show-current`).
2. Inspect diff, conventional commits and whether the feature is tested.
3. Identify missing tests/permissions or other blockers.
4. Draft a feature → alpha PR or explain the reviewed merge path.
5. Require human approval before merge/push.

### Alpha-release verification
1. Read `.releaserc.json` from `alpha` and check remote/tag ancestry.
2. If locally available, run `GH_TOKEN="$(gh auth token)" npx semantic-release --dry-run --no-ci`.
3. Distinguish the *proposed* version from published tags.
4. After publishing, inspect GitHub Release, GHCR manifest platforms, OCI labels, package version and `/api/v1/system/version`.
5. Report failures with precise evidence; do not invent success.

### Stable promotion
1. Identify the actual alpha tag and source SHA to promote.
2. Compare `alpha` against `master`; summarize changes, migration risks and rollback plan.
3. Verify required CI status, reviews, and branch protection.
4. Draft PR `alpha` → `master`, with release summary, validation evidence and deployment caveats.
5. Explain that the stable version is determined by semantic-release on `master` and must be verified, not assumed.
6. Ask for explicit approval before opening a PR on the user's behalf, merging, pushing, tagging, publishing or deploying.

### Incident / rollback support
1. Identify GitHub Release, immutable container digest, and affected environment.
2. Assess impact and propose reversible actions.
3. Never delete tags, rewrite `master`, force-push or deploy another version without explicit authorization.

## Guardrails

- GitHub Actions and semantic-release own version calculation and publication.
- Prefer commands and dry-runs for checking. Do not treat a successful command as verification of a separate downstream step.
- No tokens or secrets in logs, commits, or generated drafts.
- Do not bypass branch protection, CI, tests or approval requirements.
- Treat the latest documented image `0.1.0-alpha.3` as an example, not necessarily the latest available release.
- Keep V1/IAOS changes isolated from CAOS unless explicitly requested.

## Output checklist

Produce concise release-readiness status covering: source branch and SHA, target branch, proposed version (if verifiable), changes, CI status, image architectures, blockers, and the next **single** action.
