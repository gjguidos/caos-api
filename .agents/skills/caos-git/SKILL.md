---
name: caos-git
description: Manage safe CAOS feature-branch Git workflows, including selective staging, conventional commits, rebasing, conflict resolution, and working-tree protection. Delegate branch promotion and releases to caos-release.
---

# CAOS Git Assistant

Use when asked to inspect Git status, create or switch feature branches, review changes, stage files, commit work, synchronize with another branch, rebase, resolve conflicts, or inspect local Git history.

This skill manages **development Git operations**, not release promotion.

## Sources of truth

- `AGENTS.md`
- `git status`, `git diff`, `git log`, `git branch`
- Repository-specific development and contribution documentation
- `.gitignore`, Git hooks and CI configuration
- `docs/RELEASE_WORKFLOW.md` when branch promotion is involved

Do not assume the current branch, remote tracking branch, clean working tree, or existence of local commits.

## Modes

### 1. Working-tree inspection

1. Identify repository root, current branch and HEAD.
2. Run `git status -sb`.
3. Inspect tracked changes, staged changes and untracked files separately.
4. Identify modifications unrelated to the requested task.
5. Report potential conflicts, secrets, generated artifacts or large files.
6. Never discard existing work without explicit authorization.

### 2. Feature branch creation

1. Check current branch, working-tree state and base branch.
2. Verify that the requested branch does not already exist.
3. Prefer feature branches named `feature/<topic>`, unless the user specifies another convention.
4. Preserve uncommitted changes; do not implicitly stash or discard them.
5. Create or switch branches only after confirming that existing work will remain safe.
6. Never push a new branch unless explicitly requested.

### 3. Selective staging and committing

1. Read `AGENTS.md` and inspect current Git state.
2. Identify the exact task or milestone to commit.
3. Review the relevant tracked and untracked files.
4. Stage only files belonging to the requested change.
5. Avoid `git add .` and `git add -A` unless the entire working tree has been explicitly reviewed and approved.
6. Inspect the complete staged diff using `git diff --cached`.
7. Run `git diff --cached --check`.
8. Check for credentials, tokens, secrets, temporary exports, local database dumps and unrelated files.
9. Run relevant targeted tests, lint or type checks when practical.
10. Create a conventional commit only when the user has requested a commit.
11. Report the commit hash, staged scope, verification and remaining uncommitted changes.

Prefer the following Conventional Commit types:

- `feat`: New functionality
- `fix`: Bug fix
- `refactor`: Internal restructuring without intended behavior changes
- `test`: Test additions or corrections
- `docs`: Documentation-only changes
- `chore`: Maintenance
- `perf`: Performance improvements

Use optional scopes when useful, for example:

`feat(governance): add explicit resource ownership`

`fix(crawler): preserve cancellation action metadata`

Do not create empty commits or amend existing commits unless explicitly requested.

### 4. Updating a feature branch

1. Inspect branch history, working-tree status and remote tracking configuration.
2. Fetch remote references only when needed.
3. Explain whether merge or rebase is more appropriate.
4. Prefer rebase for local, unpublished feature commits when history can safely be rewritten.
5. Avoid rebasing published/shared commits without explicit approval.
6. Do not assume `origin/master` or `origin/alpha` exists.
7. Preserve local modifications and explain conflicts.
8. Verify the result using Git history, status and relevant tests.
9. Never force-push automatically.

### 5. Conflict resolution

1. Identify the operation in progress: merge, rebase or cherry-pick.
2. Inspect conflict markers and both versions of each affected file.
3. Distinguish Git's `ours` and `theirs` semantics for the current operation.
4. Resolve conflicts by preserving intended behavior from both sides.
5. Do not blindly accept an entire side.
6. Run relevant tests after resolution.
7. Continue the operation only after conflicts are resolved and staged.
8. Never use destructive reset or abort an operation unless explicitly requested.

### 6. Repository hygiene

1. Review staged file names and file modes.
2. Detect accidental generated files, secrets and local artifacts.
3. Preserve intentional executable-file permissions.
4. Avoid unrelated formatting changes.
5. Never bypass Git hooks with `--no-verify` unless explicitly authorized.
6. Never rewrite history, delete branches, clean untracked files or force-push without explicit authorization.
7. Do not create version tags or release commits.

## CAOS-specific conventions

- CAOS and IAOS are separate systems. Never mix their changes unless explicitly requested.
- Preserve AOSA specifications, registry contracts and existing V1/V2 compatibility.
- Do not stage administrative tenant inventories containing sensitive metadata without review.
- Keep unfinished milestones clearly identified in commit summaries.
- Prefer small, reviewable commits with cohesive responsibilities.
- Preserve unrelated uncommitted work, including prior security and governance changes.
- If a task crosses into alpha/master promotion, delegate to `caos-release`.

## Authorization

Inspection and read-only Git commands may run without additional approval.

When the user requests a commit, that authorizes creating the local commit after the staged diff has been reviewed.

Do not treat commit authorization as permission to push, open a PR, merge, rebase, tag, publish or deploy.

Stop and explain any security-sensitive or destructive operation that requires separate approval.

## Output checklist

Report:

- Repository, branch and HEAD before the operation
- Files staged and committed
- Commit message and resulting SHA
- Tests and checks run, with actual results
- Excluded and remaining uncommitted changes
- Any warnings or blockers
- Next single recommended action

If the task concerns release promotion or publication, use `caos-release` instead.