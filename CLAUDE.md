# CAOS — Claude Code Instructions

`AGENTS.md` is the canonical project instruction file shared with OpenCode. It is imported here so Claude Code loads the same instructions; keep project rules there, not in this file.

@AGENTS.md

## Claude Code specifics

- Start an architectural or implementation session with `/caos-init [next task]` (`.claude/commands/caos-init.md`). It runs the shared, read-only procedure in `.agents/skills/caos-init/SKILL.md`.
- Project skills live in `.agents/skills/` (OpenCode links them from `.opencode/skills/`). Claude Code does not auto-discover that directory: read the relevant `SKILL.md` before specialized work.
- Run commands inside Docker (`docker compose exec -T api …`). Do not introduce a virtual environment.
- Do not commit, push or change branches without explicit approval.
