# CAOS API — Branching, Versioning & Release Workflow

CAOS (Cognitive Agentic Operative System) uses **Conventional Commits**, **semantic-release**, **GitHub Actions**, and **GitHub Container Registry (GHCR)** to create traceable alpha and stable releases. The Python package version is set using `setuptools-scm`; the API exposes build metadata through `/api/v1/system/version`.

## Branch lifecycle

```text
feature/*
    │  Develop, commit and validate
    ▼
  alpha ───────────────────→ automatic prerelease (0.1.0-alpha.N)
    │
    │  Pull request: alpha → master
    │  Review, required CI checks, approval
    ▼
  master ──────────────────→ automatic stable release (e.g., 0.1.0)
```

| Branch | Role | Release behavior |
| --- | --- | --- |
| `feature/*` (including `caos-api-feature/*`) | Isolated development | No release |
| `alpha` | Integration and testing | Automatic semantic-release prereleases |
| `master` | Approved stable source | Automatic stable releases following a merged PR |

Do not merge a release branch into `master` without reviewing the resulting changes and confirming the expected stable version. A stable version number is **calculated**, not simply the last alpha version with the suffix stripped.

## Routine development

1. Branch from the appropriate integration base, normally `alpha`.
2. Develop and run local tests. Use Conventional Commit messages, e.g. `feat(events): introduce event authorization`, `fix(messaging): recover connection`, `chore(ci): update release tooling`.
3. Push the feature branch and open a PR into `alpha` (or merge it through the agreed review process).
4. Verify checks; merge. A push to `alpha` triggers `.github/workflows/release.yml`.
5. `semantic-release` analyzes commits since the latest applicable tag and, when warranted, creates an alpha Git tag and GitHub prerelease, updates the changelog, and publishes a versioned Docker image.

**Release rules are defined in `.releaserc.json`.** In the verified CAOS setup, `feat` produces a minor release; `fix` and `perf` produce patches; `chore` produces no release. Check the repository configuration for any other type overrides. Breaking changes may trigger majors.

## Promoting alpha to master

1. Confirm the target alpha release, tests, integration behavior, security review as applicable, and release notes.
2. Open a PR from **`alpha` into `master`**. Do not manually copy files across branches for promotion.
3. Review the complete diff and ensure the release workflow and project metadata are present on `master`.
4. Require CI checks, review approval, and no unresolved conversations. Preserve useful commit history; avoid promotion merge strategies that hide or unexpectedly rewrite release-relevant history.
5. Merge through the GitHub PR process. A push to `master` triggers semantic-release for the stable channel.
6. Verify the resulting version and published artifacts. **Do not assume** the stable release is automatically `0.1.0` just because the latest alpha is `0.1.0-alpha.N`—preview/verify the actual calculation.

Protect `master` against direct pushes and force pushes; require PRs and checks. Ensure the release bot's `@semantic-release/git` changelog push is compatible with branch rules. Keep alpha releases automated while guarding stable promotion with human review.

## Release automation

The release workflow lives in `.github/workflows/release.yml`. Its intended sequence is:

1. Checkout full Git history, install Node.js and release dependencies (`npm ci`).
2. Run `semantic-release` with GitHub Actions' `GITHUB_TOKEN`.
3. If a new version is released, expose `nextRelease.version` via `@semantic-release/exec` and the GitHub Actions step output.
4. Checkout the new release tag, not just the original triggering commit.
5. Build for **`linux/amd64` and `linux/arm64`** using QEMU and Docker Buildx.
6. Publish `ghcr.io/gjguidos/caos-api:<release-version>` to GHCR with `APP_VERSION`, `APP_REVISION`, and `APP_CHANNEL` build arguments.

On commits that do not warrant a release, the Docker publishing steps must be skipped.

**Current verified image:** `ghcr.io/gjguidos/caos-api:0.1.0-alpha.3`. This is a historical verified example; always inspect the current release tags and actions for later versions.

## Version sources

| Source | Example | Purpose |
| --- | --- | --- |
| Git tag | `v0.1.0-alpha.3` | Release identity |
| GHCR image tag | `0.1.0-alpha.3` | Image selection |
| `APP_VERSION` | `0.1.0-alpha.3` | Runtime build metadata |
| Python distribution | `0.1.0a3` | PEP 440-normalized package version |
| `APP_REVISION` | Short Git SHA | Source traceability |
| `APP_CHANNEL` | `alpha`, `stable`, or `development` | Deployment channel |

`setuptools-scm` derives the package version during the Docker build using `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CAOS_API`. `VersionService` reports injected build metadata through `GET /api/v1/system/version`. Local development images can use `0.0.0.dev0`; published images must receive the exact release version.

Do not depend on `.git` existing inside a production image. Prefer immutable GHCR digests for deployments needing stronger supply-chain traceability.

## Verification commands

Preview a release locally **without publishing**:

```bash
GH_TOKEN="$(gh auth token)" npx semantic-release --dry-run --no-ci
```

Review release history:

```bash
git fetch origin --tags
git tag --list 'v*' --sort=-version:refname
gh release list --repo gjguidos/caos-api --limit 10
```

Verify a published image (replace the version with an actual published tag):

```bash
docker buildx imagetools inspect ghcr.io/gjguidos/caos-api:0.1.0-alpha.3
docker pull ghcr.io/gjguidos/caos-api:0.1.0-alpha.3
docker run --rm --entrypoint python ghcr.io/gjguidos/caos-api:0.1.0-alpha.3 \
  -c "from importlib.metadata import version; print(version('caos-api'))"
docker image inspect ghcr.io/gjguidos/caos-api:0.1.0-alpha.3 \
  --format '{{json .Config.Labels}}'
```

For a running API:

```bash
curl http://localhost:8001/api/v1/system/version
```

## Troubleshooting

- **`ENOGHTOKEN` locally:** use `GH_TOKEN="$(gh auth token)"` for a dry run; never commit credentials. CI receives `GITHUB_TOKEN` via GitHub Actions.
- **Wrong repository URL:** verify `.releaserc.json` on the **actual release branch** (not merely your working branch), `package.json` and the workflow checkout. Repository: `https://github.com/gjguidos/caos-api`.
- **Release-notes writer mismatch:** keep `conventional-changelog-conventionalcommits` and its writer version compatible; update package lockfile and validate using `npm ci` + dry run.
- **No release for `chore`:** expected under current release rules; Docker publishing must be skipped.
- **No ARM64 manifest:** ensure Buildx `platforms: linux/amd64,linux/arm64` and QEMU setup; inspect using `docker buildx imagetools inspect`.
- **Version is `unknown`:** verify runtime `APP_*` environment variables and `VersionService`; confirm installed package version independently.
- **Release commit push rejected:** check branch protection, workflow permissions, remote divergence and concurrent runs before retrying; never force-push release branches.

## Governance and safety

- Publishing, merging, tagging, and deploying are CI or human-approved operations. Do not have agents force-push, move existing version tags, or skip required checks.
- Restrict `master` promotion to a reviewed PR. A successful alpha build is not automatic approval for stable deployment.
- Require Python tests/lint/security checks as appropriate before production publishing. If CI quality gates are not configured yet, consider this an **outstanding requirement**, not a completed safeguard.
- Preserve release artifacts and their digest/SHA linkage for rollback. Avoid rewriting published release history.

## Agent-assisted workflows

An optional agent skill can check branch state, commit messages, release rules, diff scope, CI status and image metadata. It may draft commands, PR descriptions and checklists. It must ask for approval before pushes, merges, stable promotion, deployment or any destructive operation, and must never replace semantic-release as the version authority.

See `.agents/skills/caos-release/SKILL.md` for a portable, repository-level skill draft.
