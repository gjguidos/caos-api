---
name: caos-init
description: Initialize a coding-agent session for CAOS. Read the canonical AOSA documents, inspect the source contracts, identify the current milestone, separate implemented from planned architecture, report doc/source contradictions and summarize the constraints for the next task. Read-only; never regenerates documentation.
---

# CAOS Session Initialization

Use at the start of a session (`/caos-init` in Claude Code or OpenCode), or before an architectural task. Optional argument: the next development task, used to focus step 6.

## Rules

- **Read-only.** Do not edit, create, format or regenerate any file, documentation included. Do not commit, change branches or run fix-mode linters. Report drift instead of correcting it.
- Ground every claim in a file you read in this session. Label each claim **implemented**, **partial**, **proposed** or **unverified**.
- Prefer targeted reads (`grep`, line ranges) over whole large files. Do not run the test suite; cite tests as evidence entry points, not as executed.

## 1. Read the canonical documents

In order:

1. `AGENTS.md` (project instructions; `CLAUDE.md` only adds Claude-specific notes)
2. `docs/architecture/exoteric/AOSA_ENGINEERING_PRINCIPLES.md` (16 principles + review checklist)
3. `docs/architecture/exoteric/AOSA_ARCHITECTURE_CONTRACTS.md` (Agent, Tool/Action, Workflow, Validation, Governance)
4. `docs/architecture/IMPLEMENTATION_STATUS.md` (milestone snapshot + drift register)
5. `docs/architecture/README.md` (evidence index; consult rows relevant to the task)

## 2. Inspect the source contracts

Confirm the contracts against source (paths relative to `src/domains/agentverse/`):

| Contract | Check |
| --- | --- |
| Persisted agent | `entities/agent.py` — `Agent` fields incl. `runtime_version`, `tools: list[AgentToolRef]`, `workflows`, ownership fields |
| Core communication vs optional tools | `agents/factory.py` `CORE_TOOL_KEYS` + `_ensure_core_tools`; `services/divine_orchestration_service.py` + `services/agent_chat_service.py` `test_agent`; V2 `services/cognitive_response_v2.py`, `services/client_event_dispatcher_v2.py` |
| Tool and synaptic actions | `tools/spec.py` (`ToolSpec`, `SynapticToolSpec`, `SynapticCapabilitySpec`, `AgentToolRef`); `registries/synaptic_registry.py`; `ncsc/cpbx.py` |
| Assignment vs authorization | `ncsc/authorization.py` (`SynapticAuthorization.require_invoke`), `security/utu.py`, `entities/resource_ownership.py` |
| Workflow inputs and UI | `workflows/spec.py` (`WorkflowSpec.inputs`, `ui`, `WorkflowInputField`); `tools/workflow_tool/__init__.py` (`_resolve_workflow`, input handling) |
| Internal-only Store Tool | `tools/store_tool/` (`spec.py`, `__init__.py` `register_store_create`); confirm it is absent from `ToolRegistry`; `dependencies/genesis_bootstrap.py` registration gate |
| Lifecycle | `agents/lifecycle/khepri.py`, `agents/lifecycle/service.py`, `agents/lifecycle/agent_principal_resolver.py`, `dependencies/genesis_bootstrap.py`, `config/genesis.local.json` |

## 3. Identify the current milestone

Run `git branch --show-current`, `git log --oneline -10` and `git status --short`. Compare with the snapshot in `IMPLEMENTATION_STATUS.md`. Name the current milestone, whether it is committed, and any uncommitted work (which must be preserved).

## 4. Distinguish implemented from planned

State explicitly which of these exist in source: Store Tool, `data.validation`, Genesis workflow/Enki, UO2, OPS2-04C.2 workers, persisted UTU approval/grant provisioning, synaptic action bindings on `AgentToolRef`. Do not infer existence from documentation, branch names or plans.

## 5. Report contradictions

List every difference found between documents and source (including the drift register's open rows if still true, and any row now resolved). For each: document, claim, source evidence, suggested resolution. Do not fix them.

## 6. Summarize constraints for the next task

If a task was given, map it to the relevant principles and contracts. Always include:

- The persisted definition is the existing `Agent` model; no replacement schema.
- Every agent has a governed communication interface; V1 core tools (incl. Chat) and V2 `chat.response`/`chat.context` grants are different mechanisms.
- Tools expose synaptic actions (`signal@version`) via registered specs and bindings; persist references, never callables.
- Registration, discovery, exposure and attachment never authorize; only UTU permit + CPBX grant/trusted line authorizes invocation; activation is separate (Khepri).
- `WorkflowSpec.inputs` declares inputs, `ui` is presentation only; presentation never replaces server validation.
- `store.create` is internal-only, synaptic-only, idempotent and insert-only.
- Genesis (`JOSHUA → Genesis → data.validation → store.create → Khepri → Enki`) is the next agent-creation use case; list its missing prerequisites.
- Standing rules: Docker only (`docker compose exec -T api …`), no virtualenv, preserve V1, no unrelated refactors, no commits without approval, never persist permits/credentials/secrets.

## Output format

```text
CAOS INIT — <branch> @ <short sha> (<clean | N uncommitted paths>)
Milestone: <name> — <committed | uncommitted> — <one line>
Implemented: <bullets with file evidence>
Planned / not present: <bullets>
Contradictions: <numbered list, or "none found">
Constraints for <task | next work>: <bullets>
Unverified: <anything not checked>
```

Keep the report under roughly 60 lines.
