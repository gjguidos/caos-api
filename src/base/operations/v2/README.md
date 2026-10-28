# OPS2-01 — Operation and Attempt Contracts

**Status:** Proposed contracts implemented for review, not an approved state model or deployed OPS V2 runtime. No ADR is accepted or superseded by this slice.

**Scope:** Pure Pydantic v2 snapshots and transition guards under `src/base/operations/v2/`. No persistence implementation, runner, supervisor, broker, worker, workflow scheduling, LLM or authorization logic. UTU remains responsible for authorization at future integration boundaries. These models neither mint nor deserialize execution permits.

## Existing contracts preserved

- V1 [models](../models.py), [repository](../repository.py), [service](../service.py), [runner](../runner.py), [producer policies](../policies.py) and event names remain unchanged and separately wired. V2 imports none of them or AgentVerse. V1's default repository is in-memory; repository-shaped records are not proof of restart-safe storage.
- Producer cleanup retries in V1 `JobSupervisor` are not operation execution attempts. V2 does not change cleanup semantics or adopt that policy registry.
- [UTU/CPBX invocation cancellation](../../../domains/agentverse/ncsc/README.md#cancellation-boundaries-invocation-runtime-and-operation), permit revocation and managed-runtime draining remain separate. No automatic revoke-to-operation cancellation, side-effect rollback or authority transfer is introduced.
- [ADR-0001](../../../../docs/architecture/decisions/ADR-0001-thoth-privileged-knowledge.md) remains a THOTH proposal; [ADR-0002](../../../../docs/architecture/decisions/ADR-0002-exoteric-esoteric-knowledge.md) accepts documentation organization only. Neither approves these operation states. See the [architecture index](../../../../docs/architecture/README.md).

## Stable identity and snapshots

`OperationRecord.create(...)` creates a new UUID operation identity (or accepts an explicit UUID) and requires an aware caller-supplied timestamp. `start_attempt(...)` creates a distinct UUID attempt identity and the next contiguous, one-based attempt number. The operation ID, kind, producer, correlation/causation references and creation time survive all attempts.

Identity is **intended to span a durable operation lifetime**, not one invocation, session or worker. This slice does not persist anything: actual durability, global uniqueness enforcement, concurrent-writer fencing and crash recovery require a future store/executor. JSON round trips retain supplied identities; restoration requires IDs, revision and timestamps instead of silently replacing missing values.

Snapshots forbid unknown fields and are frozen, using tuples of frozen attempts rather than mutable metadata dictionaries. They include created/updated/finished times, per-attempt start/finish, outcome reasons, cancellation intent and a monotonically incremented revision. Times normalize to UTC, cannot move backwards, and may be equal when successive transitions share a clock tick. An initial snapshot's clocks agree, final success/failure matches the latest attempt's completion, and revision cannot undercount transitions implied by retained history. Revision is a contract field, **not an implemented CAS or distributed lock**. Typed kind/producer and correlation/causation strings are descriptive metadata, not identity authentication or permissions.

## Proposed operation transition matrix

| State | Permitted next states | Meaning |
| --- | --- | --- |
| `pending` | `running`, `failed`, `cancelled` | New operation; `failed` records preparation failure without starting an attempt |
| `running` | `succeeded`, `failed`, `retry_pending`, `cancel_requested` | Exactly one current running attempt |
| `retry_pending` | `running`, `cancelled` | Last attempt failed or was cancelled; controller explicitly selected retry intent; no running attempt or scheduled execution implied |
| `cancel_requested` | `succeeded`, `failed`, `cancelled` | Durable-operation cancellation intent recorded; current attempt still running until its actual outcome is reported |
| `succeeded`, `failed`, `cancelled` | None, including self-transitions | Terminal; never reopen this operation for another retry |

Attempts have a separate matrix: `running` → `succeeded`, `failed` or `cancelled`; all three outcomes are terminal. Attempts never reopen. Only the latest attempt ID can report an outcome. No overlapping attempts, reused attempt IDs, numbering gaps, foreign operation IDs or retry after success are allowed. Prior failed/cancelled attempt records are retained unchanged.

`finish_attempt(..., retry=True)` is permitted only for an unsuccessful attempt while operation cancellation has not been requested. It atomically describes the terminal attempt and nonterminal `retry_pending` operation snapshot. It does not schedule a retry, grant authority or evaluate a retry policy. The next `start_attempt()` allocates a new ID/number; a terminal `failed` operation cannot be retried. Budget, backoff, retryability classification and recovery decisions remain outside this slice.

## Cancellation and completion races

- `request_cancellation()` on `pending` or `retry_pending` terminates immediately because that snapshot has no running attempt. A future executor/store must ensure this snapshot is current; these pure guards cannot establish external quiescence.
- On `running`, the request records `cancel_requested` without finishing the attempt or cancelling a Python task. Repeated requests are rejected in this proposal, not silently treated as idempotent events.
- If the executor subsequently reports a confirmed `cancelled` attempt, the operation becomes `cancelled`. Success/failure may instead win the race: preserve the actual outcome and cancellation-request metadata. A request is not proof that committed effects were prevented.
- An invocation's cancellation **without** operation cancellation intent does not imply terminal operation cancellation. Recording a cancelled attempt yields `failed`, or `retry_pending` when retry is explicitly selected. A retry is forbidden once operation cancellation is pending.

Transition functions validate complete new snapshots rather than using unchecked `model_copy(update=...)`. Callers must not use Pydantic's trusted bypass APIs (`model_construct`, unchecked copies) as lifecycle acceptance boundaries. Snapshot validation checks structural consistency; it cannot prove a reconstructed record's history or reject stale/concurrent writes without a store comparing the prior revision. Terminal immutability applies to existing snapshots and the guarded transition API, not hostile Python code.

## Verification

Deterministic tests in [tests/test_contracts.py](tests/test_contracts.py) cover IDs, both matrices, retries, stale attempt reports, cancellation races, immutable terminal states, timestamps, JSON restoration, invalid construction and V1 independence. Run in the existing API container:

```sh
docker compose exec -T api python -m pytest -q src/base/operations/v2/tests
```

Test presence is not a claimed run result or deployment guarantee. There is no DI/route integration or persistence adapter in this package.

## Decisions to review before OPS2-02

1. Accept or revise these enum names/matrices, pre-execution failure representation, retry intent and terminal semantics; decide whether pending/queued/admitted distinctions are needed.
2. Approve success/failure versus cancellation precedence and command idempotency. Define who can assert quiescence and how executor/invocation outcomes map without conflating authority revocation with operation intent.
3. Select future persistence, atomic revision/attempt allocation, fencing, stale-writer rejection, recovery ownership and clock assumptions. Define retention/compaction for attempt history.
4. Define retry budgets/backoff and uncertain/timed-out/lost-worker outcomes without treating cleanup retries or ambiguous external effects as automatic execution retries.
5. Review schema versioning, V1 ID/status adapters, result/error references and ownership/tenant metadata. Authorization stays in UTU; integration must not trust descriptive record fields as authority.

These are open architectural questions, not OPS2-02 implementation or accepted decisions.

## OPS2-01 review refinements

- `fail_before_start()` records a failed **already-created** operation with no attempt if
  preparation fails before an executor starts. Input/authentication/admission rejection
  should occur before the operation is created, and is not an operation failure.
- `request_cancellation()` remains a strict pure transition. The opt-in
  `ensure_cancellation_requested()` helper accepts matching duplicate requests without
  changing revision or timestamps; conflicting reasons are rejected. It is **not**
  distributed command-ID deduplication and does not replace storage-side CAS.
- Report cancelled operations with the original attempt history: no attempt means
  cancelled before start, a failed/cancelled last attempt followed by cancellation
  during retry_pending means cancellation while idle, and a cancelled active attempt
  after cancellation intent means cancellation was confirmed by the executor.
- Future stores must use atomic expected-revision writes and worker fencing; model
  revision alone does not provide safety. Retry budget and attempt compaction remain
  OPS2-02+ decisions, not invented behavior in this contract slice.
