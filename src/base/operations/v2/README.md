# OPS V2 — Contracts and Durable Repository

**Status:** OPS2-01 contracts are the accepted baseline for OPS2-02. The bounded repository implementation below does not establish a deployed OPS V2 runtime, approve a broader supervisor design, or accept/supersede an ADR.

**Scope:** Pydantic v2 snapshots, unchanged transition guards and OPS2-02 storage under `src/base/operations/v2/`. No runner, supervisor, broker, worker, workflow scheduling, LLM or authorization logic. UTU remains responsible for authorization at future integration boundaries. These models neither mint nor deserialize execution permits.

The OPS2-01 sections below record the original contract slice and its limitations at that stage. References there to missing persistence/CAS are superseded only by the bounded [OPS2-02 implementation](#ops2-02--durable-operation-repository), not by a recovery runtime or worker fence.

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

## OPS2-02 — Durable Operation Repository

### Implemented behavior

- [OperationRepository](repository.py) is an async, infrastructure-independent protocol:
  `create(snapshot)`, `get(operation_id)` and `commit(snapshot, expected_revision=...)`.
  Creation inserts any fully validated snapshot, including imported terminal history;
  it does not attest to that history's provenance or require a pending initial state.
  Existing identities raise `DuplicateOperation`; reads/commits of missing identities
  raise `OperationNotFound`; stale expectations raise `RevisionConflict`.
- Commits require a nonnegative integer expectation and exactly `expected_revision + 1`.
  Both adapters revalidate even Pydantic bypass-created inputs, reproduce one transition
  using the unchanged OPS2-01 functions and compare the complete result. Invalid
  snapshots, revision skips, metadata/history rewrites and terminal changes raise
  `InvalidSnapshot`. A matching duplicate cancellation helper result has no new revision
  and must not be committed as a new transition. Validation errors can precede lookup errors.
- [InMemoryOperationRepository](memory_repository.py) locks lookup/validation/replacement
  together within one adapter/event loop and returns detached frozen snapshots. It is
  a reference implementation, **not durable or cross-process storage**.
- [MongoOperationRepository](mongo_repository.py) uses the existing CAOS
  `MongoDBRepository.get_collection()` and DI-owned client. The separately named
  `Container.operations.operation_repository_v2` singleton uses
  `Container.database.mongodb_repository`; V1 providers are unchanged. No route,
  service or executor consumes V2 automatically. The adapter never closes the shared client.
- Dedicated `operations_v2` collection: one document contains the complete operation
  and attempts. `_id` is the canonical string UUID, `schema_version` is integer `1`,
  and all snapshot fields are explicitly serialized at the top level. UUIDs/enums are
  strings, attempts are arrays, nullable fields are present, timestamps are UTC ISO-8601
  strings retaining microseconds. This is JSON/BSON-compatible, not BSON datetime storage;
  future timestamp query design must account for that choice.
- The only required index is MongoDB's automatic unique `_id_` index, created with
  the collection. Identity lookup and CAS use it; no compound revision index, TTL,
  index bootstrap or speculative query index is needed. No listing API is added:
  this slice has no listing consumer and recovery discovery requirements are not defined.
- Creation uses `insert_one` with explicit duplicate-key translation. Commit reads and
  validates its predecessor, then calls `replace_one({"_id": id, "revision": expected},
  document, upsert=False)`. The conditional replacement, **not** the preliminary read,
  is the atomic concurrency boundary. Competing writes cannot both match the same
  revision. A zero match triggers one diagnostic read to distinguish absent data from
  a revision conflict, never an automatic write retry. This classification is observed
  at read time if outside actors delete/reinsert records; such mutation is unsupported.
- Restoration checks schema version, identity agreement, complete operation/attempt
  field sets and strict Pydantic JSON validation. Corruption raises `CorruptSnapshot`;
  there is no identity regeneration, silent metadata defaulting, migration or repair.
  Snapshot times, attempts, cancellation intent and actual completion outcomes survive
  storage unchanged. A losing cancellation/completion writer must reload and explicitly
  derive any next transition; completion after persisted intent retains that intent.
- Primary reads and acknowledged deployment-configured write concern are required;
  unacknowledged writes are rejected. Database failures propagate; the adapter has no
  retry loop. Existing driver/deployment bounded retry and timeout settings remain in force.
  Persistence survives adapter/process recreation; replica failover/power-loss durability
  depends on configured write concern, journaling, topology and backups. Majority/journal
  policy is not imposed here, and Cosmos Mongo API compatibility is not verified.
- Storage is trusted infrastructure, **not authority**. UUID/kind/producer/correlation
  metadata cannot authenticate a caller, authorize access or mint UTU permits. No
  authorization subsystem, AgentVerse dependency or revoke-to-cancel bridge is added.

### Tested behavior

On 2026-10-28, in the existing API container and configured MongoDB environment:

```sh
docker compose exec -T -e OPS2_MONGO_INTEGRATION=1 api python -m pytest -q src/base/operations/v2/tests src/domains/agentverse/tests/operations/test_operation_repository.py
```

Result: **266 passed, 2 skipped** (11 pre-existing Pydantic deprecation warnings).
The two skips are memory cases for Mongo-only corruption and independent-adapter race
tests; the corresponding Mongo cases ran. Without the environment opt-in, Mongo cases
skip, rather than presenting mocks as database verification.

[Repository tests](tests/test_repository.py) exercise both adapters for create/get,
duplicate creation including concurrent inserts, legal revisions, stale/conflicting
writes, malformed/bypass snapshots, revision skips, historical rewrites, missing data,
terminal snapshots, cancellation/completion precedence and JSON/BSON round trips.
An actual Mongo test synchronizes two independent adapters after both read revision 0:
exactly one conditional replacement succeeds, and a fresh adapter reads the winner.
Actual stored-document corruption is rejected. Mock tests separately check the exact
CAS filter/no-upsert and error propagation; they are not evidence of Mongo atomicity.
DI client sharing, V1 repository/service independence and the unchanged 180 OPS2-01
tests are included. Tests use UUID-isolated collections and drop only their own collection.

### Known limitations and proposed future recovery requirements

**Not implemented or tested:** restart/power-loss fault injection, replica failover,
leases, worker fencing, crash reconciliation, recovery scans, effect deduplication,
transactions with external effects, outbox/event delivery, distributed command IDs,
tenant/owner access APIs, retention/compaction, schema migration or Cosmos deployment.
Mongo's document size limit bounds retained attempt history; full-snapshot validation
and replacement cost grows with that history. External raw database writes can bypass
the contract; there is no database schema validator or tamper-proof audit history.

**Future decisions, not behavior:** approve deployment durability/read concerns and
backups; define recovery ownership, bounded discovery queries and lease/fence tokens;
reconcile lost workers and uncertain external effects before retrying; set retry budgets
and attempt retention; define schema upgrades and authorized import/resource access.
An ambiguous database error may follow a committed write: reload and reconcile rather
than blindly repeat effects. CAS protects snapshots only, not execution uniqueness,
quiescence, external side effects or UTU authority. Enlil supervision remains out of scope.
