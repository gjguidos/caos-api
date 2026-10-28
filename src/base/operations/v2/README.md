# OPS V2 — Contracts, Persistence and Execution Ownership

**Status:** OPS2-01 contracts are the accepted baseline for OPS2-02. The bounded repository implementation below does not establish a deployed OPS V2 runtime, approve a broader supervisor design, or accept/supersede an ADR.

**Scope:** Pydantic v2 snapshots, unchanged transition guards, OPS2-02 storage, isolated OPS2-03 ownership, OPS2-04B managed aggregate persistence and OPS2-04C.1 admission/discovery under `src/base/operations/v2/`. No runner, supervisor, worker, workflow scheduling, LLM or authorization logic; the only broker use is the optional OPS2-04C.1 dispatch-notice adapter over the existing Event V2 runtime. UTU remains responsible for authorization at future integration boundaries. These models neither mint nor deserialize execution permits.

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

## OPS2-03 — Distributed Execution Ownership and Fencing

**Status:** Implemented for architecture review, based on OPS2-01/02 at `cfd3c20`.
Their models, transitions, snapshot repositories and interfaces are unchanged.
The earlier OPS2-02 limitations describe that milestone; this section adds ownership
storage only, not a fenced executor or ownership-fenced operation-state commits.

### Implemented record and repository contracts

[OwnershipRecord and OwnershipRepository](ownership.py) define coordination, **not
authorization**. The frozen, extra-forbidding record requires every field:

- `operation_id`, `attempt_id`: distinct UUIDs. Attempt identity is supplied, not
  generated or verified against an operation by this repository.
- `owner_id`: nonblank opaque executor-instance/session identity; not an authenticated
  principal or permit. Callers should choose identities unique to an executor incarnation.
- `fencing_token`: positive, per-operation generation, bounded to signed BSON int64.
  First claim yields 1; every later claim increments it. Renewal/release retain it.
  Exhaustion fails closed rather than wrapping/resetting. There is no global counter.
- `state`: `active` (unreleased generation, possibly expired) or `released`.
  Expiration does not rewrite the document or reset its token.
- `created_at`: first claim time; `acquired_at`: current generation's claim time;
  `updated_at`, `lease_expires_at`, and nullable `released_at`. UTC-aware timestamps
  have explicit millisecond precision, with consistent ordering and release metadata.

Async API (all mutations return a validated record):

```python
claim(operation_id, *, attempt_id, owner_id, lease_ms)
renew(operation_id, *, owner_id, fencing_token, lease_ms)
release(operation_id, *, owner_id, fencing_token)
inspect(operation_id)
```

No caller `now` or expiration timestamp is accepted. Lease duration is a strict integer
from 1 to 86,400,000 milliseconds (a bounded contract, not a recommended renewal interval).
Renewal uses `max(existing_expiry, repository_now + duration)` and never shortens a lease.
The same owner cannot claim an unexpired lease; use renewal instead. Release requires
a matching **live** owner/token and preserves owner, attempt, expiry and token history.
Released generations cannot be renewed or released again; a new claim gets a new token.
Expiry is exclusive: a lease is live only when `lease_expires_at > repository_now`.

Typed failures: `OwnershipConflict` for a competing owner/concurrent change or repository
clock regression; `ObsoleteFencingToken` for a noncurrent token; `LeaseExpired`;
`OwnershipReleased`; `OwnershipNotFound`; `InvalidOwnershipRequest` for malformed IDs,
owners, tokens, durations or exhausted token space; `CorruptOwnership` for invalid stored
documents. Credential failure precedence is token, owner, released state, expiration,
then clock regression. Concurrent same-generation renewal can also lose exact-snapshot
CAS and report conflict; there is no automatic retry or idempotent command protocol.

`inspect` restores the last record, including expired/released ownership, or raises
`OwnershipNotFound`. It is diagnostic, not proof that the lease remains live when the
response arrives. All input/storage boundaries reject malformed records; no missing
identity, token or metadata is regenerated.

### Adapters and exact atomic strategy

[InMemoryOwnershipRepository](memory_ownership_repository.py) samples its repository-owned
clock and checks/mutates under one `asyncio.Lock`. It returns detached records. An injected
clock supports deterministic tests; there is no per-call clock override. This adapter is
process/event-loop local and not durable.

[MongoOwnershipRepository](mongo_ownership_repository.py) reuses CAOS's configured
`MongoDBRepository.get_collection()`, never constructs/closes a client, and is separately
provided as `Container.operations.ownership_repository_v2`. Existing V1 and OPS2-02
providers are unchanged. No route, runner, supervisor or lifecycle hook consumes it.

Mongo storage uses one document per operation in `operation_ownership_v2`: canonical UUID
strings, string owner/state, integer token/schema version (`1`), and aware BSON dates.
The built-in unique `_id_` index enforces identity; there are no other indexes, no TTL,
no deletion API and no expiring counter documents. Restoration requires the complete
schema and strict Pydantic validation. Native MongoDB 5.0+ (`hello`, aggregation updates
and `$$NOW`) is required; the test deployment is MongoDB 8.0. Cosmos Mongo API compatibility
is unverified and there is no weaker fallback.

1. **Initial claim:** a validating read finds no record. The adapter samples the primary's
   `hello.localTime`, constructs token 1 and calls `insert_one`. The unique `_id` insertion,
   **not the preceding read**, is the atomic first-claim gate. Concurrent absent reads
   cannot overwrite each other: one insert wins; duplicate key becomes ownership conflict.
   An initial lease starts at the sampled server time, so network/write latency consumes
   its duration. After insertion, the adapter rereads persisted ownership with database
   time and requires the inserted owner/token to remain live and unreleased. Expiration
   raises `LeaseExpired`; a superseding generation raises `ObsoleteFencingToken`.
   The record/token history is retained, with no deletion, reset or automatic claim retry.
   Success verifies validity **at that observation point only**; ownership may expire,
   be released or be superseded afterward, including before the response arrives.
   No app clock is used.
2. **Expired/released takeover:** a validating pre-read is followed by
   `find_one_and_update(..., upsert=False, return_document=AFTER)`. Its predicate matches
   the entire prior snapshot (including operation, attempt, owner and token) **and**
   requires `released OR lease_expires_at <= $$NOW` and `$$NOW >= updated_at`.
   The same atomic pipeline increments the retained token and sets the new owner,
   attempt, active state and server-derived timestamps. Concurrent takeover candidates
   cannot both match the old generation. An intervening renewal/release can cause conflict;
   nothing blindly replaces the pre-read snapshot. No upsert resets expired ownership.
3. **Renew/release:** validating pre-read establishes typed errors, but is not sufficient
   to grant the write. Exact prior-snapshot CAS includes matching owner/token/attempt,
   active state and the atomic predicates `lease_expires_at > $$NOW` and
   `$$NOW >= updated_at`. The update uses the **same command's** server time. Expiration,
   takeover or release between validation and mutation prevents the write. A failed CAS
   performs at most one diagnostic read for error classification, never a write retry.

Mongo uses primary reads, majority read concern and ownership-specific
`w=majority, j=true, wtimeout=5000` writes. These settings do not alter OPS2-02's write
concern contract. They prevent ordinary acknowledged ownership-token rollback under
normal durable replica-set failover assumptions; they are not evidence that failover or
power-loss tests were run. Database/write-concern errors propagate and may have ambiguous
outcomes. Existing driver retryable-write settings remain in force, but no application
retry loop is added. Inspect/reconcile before explicitly issuing a new request.

Repository-authoritative time removes caller-clock skew, not server wall-clock jumps.
Backward-time mutations are rejected until timestamp ordering permits them; forward jumps
can expire leases early. Deployment clock discipline, durable replica topology and latency
budgets remain requirements. Raw document deletion, manual token edits, database rollback
or restoring an older backup can destroy monotonic history: do not use them as release
or recovery. A disaster-restore protocol must preserve a high-water mark or invalidate the
old fencing namespace before accepting effects.

### Tested behavior

Executed in the API container against the existing real MongoDB deployment:

```sh
docker compose exec -T -e OPS2_MONGO_INTEGRATION=1 api python -m pytest -q src/base/operations/v2/tests src/domains/agentverse/tests/operations/test_operation_repository.py
```

Result after first-claim timing finalization: **372 passed, 9 skipped**, with 11 existing
Pydantic deprecation warnings.
The skips are memory-adapter cases for Mongo-only tests; their Mongo counterparts ran.
Without the explicit environment opt-in, Mongo cases skip rather than silently mock storage.

[Ownership tests](tests/test_ownership.py) cover first claim, unexpired exclusion, renewal,
wrong owner/token, expiry, takeover, obsolete-owner rejection, strict repeated-release
handling, retained tokens, malformed records/requests, BSON/JSON restoration, token
exhaustion, repository clock boundaries, stored corruption and DI independence. Independent
Mongo adapters restore persisted ownership. Barrier tests force concurrent first claimers
to observe absence and concurrent takeover candidates to observe the same expired token:
exactly one succeeds in each case. Separate real-Mongo tests let leases expire **after**
validation but **before** renewal/release writes, proving the write-side expiration guard
rather than only application prechecks. Initial-insert timing tests deliberately delay a
real insert beyond its lease: expired success is rejected, the record is retained, and a
later claim receives token 2, which rejects token 1 renewal/release. A second case supersedes
the inserted generation before its post-insert observation and rejects the original claim.
A mocked failure test checks lack of adapter retries;
it is not used as evidence of database atomicity. Test collections have UUID-isolated names;
cleanup removes only the test's own collection. All unchanged OPS2-01/02 tests and the V1
repository test are included in this run.

**Not tested:** replica failover, clock jumps across primary changes, power loss, backup
restore, Cosmos, high-load latency, or end-to-end execution/side-effect fencing.

### Fencing boundary and required OPS2-04 contract (proposed, not implemented)

**OPS2-03 is not exactly-once execution.** It rejects obsolete mutations to ownership
records only. It cannot kill stale executors, prove quiescence, roll back effects, authorize
work, validate the supplied attempt against a live operation, or prohibit claims against
terminal/nonexistent operation IDs. No execution or recovery is started by a claim.

**OPS2-02 commits are not ownership-fenced.** Reading this lease and then separately
calling `OperationRepository.commit` leaves a race with expiry/takeover. Snapshot revision
CAS is not a substitute: an obsolete executor with a current revision can still commit
through OPS2-02. No distributed transaction or hidden OPS2-02 contract change is introduced.

Before enabling an executor, OPS2-04 must provide and review an authoritative mutation
boundary with these properties:

1. Validate operation identity, current attempt, owner incarnation, exact current fencing
   token, unreleased/unexpired lease under authoritative time, expected snapshot revision,
   and the accepted lifecycle transition **at the same atomic linearization point** as the
   execution-state change. A takeover and a stale-owner commit must not both be accepted
   against the old generation. A separate lease precheck does not satisfy this contract.
2. Select an atomic storage layout/protocol that satisfies that property; current separate
   documents and OPS2-02 alone do not. Define consistent claim/start/finish/release ordering,
   including pending/terminal states and allocation of the current attempt. No storage
   layout migration or transaction implementation is part of this milestone.
3. Define outcome/error reconciliation after ambiguous writes and crashes. Lease expiry
   is loss of coordination authority, **not proof that prior work stopped** or permission
   to blindly retry external effects. Specify retry/recovery policy separately.
4. Require participating effect sinks to atomically enforce appropriate fencing generations
   and/or idempotency keys with the effect. Tokens here are scoped per operation, not
   globally across unrelated operations/resources. Without sink cooperation, old executors
   can still perform effects despite failing lease/state writes.
5. Integrate trusted UTU/NCSC principal, tenant, permit/revocation and lineage checks at
   authorized entry/effect boundaries. Persisted `owner_id` is not a permission grant;
   renewal must not revive revoked authority. Decide background-work/session ownership,
   cross-instance cancellation, shutdown draining and recovery readiness without assuming
   that a WebSocket connection or absence of a local task proves execution ownership.

V1 remains process-local and separately wired, including existing crawler supervision and
cleanup behavior. No executor loop, scheduling, automatic recovery, RabbitMQ, WebSocket,
worker management, agent lifecycle or authorization-service changes are included.

## OPS2-04B — Managed Aggregate and Atomic Fencing

**Implemented for review**, following the accepted OPS2-04A direction. This is a
persistence API, not an executor. OPS2-01 models/transitions and OPS2-03 adapters are
unchanged. Earlier OPS2-03 statements about unfenced OPS2-02 commits still apply to
schema-v1 records; managed schema-v2 records have the separate boundary described here.

### Schema, authority and API

[ManagedOperation](managed.py) is a frozen, strictly restored envelope in the existing
`operations_v2` collection, addressed by the operation UUID string:

```text
_id
schema_version: 2
snapshot: complete unchanged OperationRecord JSON
execution:
  generation, owner_id, attempt_id, status, mode
  acquired_at, updated_at, lease_expires_at, released_at
last_state_change:
  command_id, action, request_hash, generation, attempt_id
  previous_revision, resulting_revision
```

There is **no top-level revision**. Snapshot timestamps remain ISO strings retaining
microseconds; execution clocks are UTC BSON dates with millisecond precision. Required
fields, unknown fields, identities, epoch/snapshot consistency, and the receipt are
validated on restoration; missing values are not repaired. The only required index is
the automatic unique `_id_` index. No TTL, deletion, global counter or migration is added.

`ExecutionEpoch` retains a signed-int64 generation (zero only before first activation),
opaque owner-incarnation identity and current attempt binding. Status is `idle`, `active`,
`released` or `closed`; mode is `execute` or `reconcile` after acquisition. Every acquisition
increments the generation; renewal/release/outcomes never reset it. Exhaustion fails closed.
An active record can still be expired. Lease duration uses OPS2-03's strict 1..86,400,000ms
bound. Owner/token metadata coordinates trusted callers; it authenticates nobody and
grants no UTU permission.

[ManagedOperationRepository](managed.py) and
[MongoManagedOperationRepository](mongo_managed_repository.py) expose:

- `create(snapshot)`: unique insertion of a **new pending** operation with idle epoch.
  No adoption/import of schema-v1, running or terminal records.
- `activate(id, expected_revision, expected_generation, attempt_id, owner_id, lease_ms,
  at, command_id)`: atomically apply the existing `start_attempt` and acquire an execute
  epoch. Pending and explicit retry-pending intent are eligible; numbering/history stay
  governed by OPS2-01. `at` is a lifecycle timestamp, never the lease clock.
- `renew(id, attempt_id, owner_id, generation, lease_ms)`: extend using
  `max(current_expiry, $$NOW + duration)`, without changing lifecycle revision or receipt.
- `commit_outcome(snapshot, expected_revision, attempt_id, owner_id, generation,
  command_id)`: accept only a legal attempt outcome successor in execute mode. Atomically
  store the snapshot/receipt and retire authority: terminal -> `closed`, retry-pending ->
  `released`. No task or retry is started.
- `release(id, attempt_id, owner_id, generation)`: require live matching authority;
  retain generation, snapshot and current unresolved attempt. A released running attempt
  cannot be replayed through `activate`.
- `takeover(id, expected_revision, expected_generation, attempt_id, owner_id, lease_ms)`:
  acquire expired/released **unresolved** work with a new generation in reconcile mode;
  preserve the operation revision, attempt and receipt. It cannot start a new attempt or
  commit execution outcomes. Reconciliation can renew/release its own lease only in this
  slice; verified resolution is deferred to OPS2-04C.
- `inspect(id)`: restore authoritative snapshot and epoch, not a promise of future validity.

The separate singleton `Container.operations.managed_operation_repository_v2` reuses
CAOS's DI-owned Mongo client. Nothing starts it as a runner or consumes it from a route.
The separate OPS2-03 collection is **not consulted or updated** for managed authority.
Its legacy API remains unchanged and can still store unrelated ownership records;
those records are not grants against a managed aggregate. There are no dual-authority
writes or ownership mirroring.

### Exact conditional-write boundary

All updates are one `find_one_and_update` with an update pipeline, `upsert=False` and
return-after. All predicates include `_id`, integer `schema_version=2`, matching
`snapshot.operation_id`, and absence of a top-level `revision`. Since
[OPS2-04C.1](#ops2-04c1--durable-admission-and-discovery) the version predicate is
`schema_version ∈ {2, 3}`, so these commands also fence admitted aggregates; schema 1
still never matches.

| Command | Additional atomic predicates | Atomic changes |
| --- | --- | --- |
| Activate | expected snapshot revision and epoch generation; snapshot pending/retry-pending; epoch idle/released; epoch update time null or <= `$$NOW` | validated successor snapshot, generation +1, new execute lease, receipt |
| Renew/release | matching owner, generation, epoch attempt and snapshot's latest attempt; unresolved snapshot; active epoch; expiry > `$$NOW`; epoch update time <= `$$NOW` | only dotted execution lease/status fields; no snapshot replacement |
| Outcome | all renew/release predicates plus expected snapshot revision and `mode=execute` | validated outcome snapshot, retired epoch, receipt |
| Takeover | expected snapshot revision/generation; matching epoch/latest attempt; unresolved snapshot; active/released epoch; released OR expiry <= `$$NOW`; epoch update time <= `$$NOW` | generation +1, new reconciliation lease, no snapshot revision change |

Pre-reads validate snapshots and produce typed failures, **not authorization to write**.
For state changes, existing OPS2-01 successor validation is bound to its exact predecessor
by lifecycle revision CAS. Authority, expiry, attempt, generation and revision are checked
again in the actual update predicate. Outcome updates do not overwrite a concurrent
renewal's expiry; heartbeat writes do not contend on lifecycle revision. No global lock
serializes distinct operations, although writes for one aggregate share its document.

If completion wins before takeover, lifecycle revision and epoch retirement change
together: a takeover based on the old state fails and must inspect the accepted outcome.
If takeover wins, the old outcome/renew/release generation cannot match, even though
lifecycle revision has not changed. Renewal-first prevents an expiry takeover from using
an obsolete expiry; takeover-first prevents stale renewal. Release-first rejects later
execution outcomes but preserves unresolved work rather than permitting duplicate runs.

Creation is an atomic unique insert of an idle record, not acquisition. Acquisition and
renewal responses additionally observe persisted authority with database time; an expired,
released or superseded grant is rejected without undoing the write, resetting history or
retrying. This establishes validity at observation only, not after return. A delayed
activation can therefore fail its response while its started attempt remains unresolved.

### Durability, failures and feature requirements

The managed adapter uses primary/majority reads and `w=majority, j=true, wtimeout=5000`
writes. It never closes the shared client, creates transactions, or retries a command.
Native MongoDB update pipelines, `$expr`, `$$NOW`, `$arrayElemAt`, date arithmetic and
atomic find-and-update are required. The supported target is the existing native MongoDB
5.0+ baseline; tests run against MongoDB 8.0. Unsupported commands fail closed; no weaker
client-clock or multi-document fallback exists. Cosmos compatibility is **unverified**:
API/version, conditional pipeline support, server time, partitioning and durability must
be proven for the actual deployment before using this adapter.

`$$NOW` is the server-time value for the command, stable through the pipeline. It is not
a continuously advancing clock or a guarantee at acknowledgement. Server clock jumps,
queued/stalled commands and failover latency still require operational qualification;
generation predicates fence supersession independently of caller clocks.

Typed failures include existing missing/duplicate/revision/obsolete-token/owner/expired/
released exceptions plus `AttemptConflict`, `ReconciliationOnly`, `InvalidManagedRequest`,
and `CorruptManagedOperation`. A failed conditional write may use one diagnostic read;
it never weakens the predicate or automatically retries.

A driver error during a write raises `AmbiguousManagedWrite`, carrying operation ID and
state-command ID when supplied, with the original exception chained. This conservatively
includes write-concern errors and lost responses; acceptance is not assumed to have failed.
Inspect snapshot/history, generation and `last_state_change` before any further action.
The bounded receipt contains the retained command UUID and SHA-256 canonical request hash,
action, attempt, generation and resulting revision. Lease mutations preserve it, but later
state mutations replace it. It is not an unlimited deduplication/audit ledger; missing or
overwritten proof leaves uncertainty, never permission to replay effects. No application
retry is added; infrastructure driver settings remain in force.

### Legacy isolation and deployment cutover

OPS2-02's protocol and schema-v1 snapshots remain unchanged. Its only behavioral hardening
is an explicit replacement predicate requiring `schema_version=1`, the expected numeric
top-level revision, and **no `snapshot` or `execution` fields**. Its exact-filter unit test
is updated accordingly. Unsupported managed reads fail closed rather than being flattened.

Even an already-running **pre-upgrade** writer filtering only `_id` and numeric top-level
`revision` cannot match the new envelope, which has no such revision. Never introduce a
compatibility revision alias. An `_id`-only administrative replacement/deletion or raw
database mutation can still bypass the protocol; these APIs are trusted infrastructure,
not a database access-control system.

No records are converted: shared `_id` uniqueness rejects managed creation over schema v1
and legacy creation over schema v2. Existing schema-v1 records remain unmanaged. Old
binaries cannot read managed records; deploy/route managed work only to upgraded consumers.
Any future migration must explicitly exclude old writers, preserve lifecycle history and
generation high-water marks, and reconcile unresolved work. Do not implement cutover by
deleting/reinserting records or copying legacy leases while their writers remain active.
Rollback must retain envelopes, not flatten them. V1 operation/crawler providers and
behavior are unchanged.

### Executed verification

```sh
docker compose exec -T -e OPS2_MONGO_INTEGRATION=1 api python -m pytest -q src/base/operations/v2/tests src/domains/agentverse/tests/operations/test_operation_repository.py
```

Result: **423 passed, 9 skipped**, with 11 existing Pydantic deprecation warnings. The
skips are unchanged memory cases for Mongo-only tests; their Mongo counterparts ran.
[Managed tests](tests/test_managed_repository.py) use isolated collections and independent
adapters for competing activation/outcomes, completion/takeover orderings, renewal/takeover,
release/completion, expiration after prevalidation, stale credentials at an unchanged
lifecycle revision, terminal exclusion, attempt validation, history/retry numbering,
independent renewal while another operation is blocked, and heartbeat/outcome interleaving.
They also exercise real old/hardened replacement predicates against managed envelopes,
strict restoration/corruption, separate-ownership nonauthority, retained generation
exhaustion, delayed grant observation and an actually applied Mongo outcome followed by
an injected lost-response error. Test-only raw cutover/corruption setup is not a migration
feature. Fault injection does not prove real replica-failover or power-loss behavior.

### OPS2-04C responsibilities (not implemented)

No executor loop, scheduling, recovery, external effects, messaging, WebSockets or agent
lifecycle changes are included. This milestone does not expose generic managed snapshot
updates, cancellation/control-plane commands, or reconciliation outcome assertions.
Those need narrowly authorized interfaces and truthful outcome/recovery policy; existing
OPS2-01 functions and cancellation semantics remain unchanged.

OPS2-04C must decide dispatch/start acknowledgement, uncertain-effect reconciliation,
verified outcome admission, cancellation/UTU revocation integration, shutdown draining,
receipt retention/idempotency policy, backups/disaster fencing namespaces and managed
migration. Old executors can still perform external effects: sinks require appropriate
fencing/idempotency enforcement and trusted UTU/NCSC context. Per-operation generations
are not global resource fences. Expiration/release proves neither quiescence nor exactly-once
execution. Large attempt histories retain Mongo's document-size/write-cost limits; heartbeat
frequency, contention, failure timing and production durability need workload/fault testing.

## OPS2-04C.1 — Durable Admission and Discovery

**Implemented for review** on `feature/ops-v2` after `bd2da90`. Admission and discovery
only: no worker, executor, capability execution, recovery orchestration, channel runtime
or new authority. OPS2-01 transitions and OPS2-03 ownership are unchanged; OPS2-04B
fencing commands are unchanged except that they also match schema-3 aggregates.

### Contracts

[admission.py](admission.py) defines frozen, extra-forbidding, versioned contracts:

- `ExecutionDescriptor` (`descriptor_version: 1`): `CapabilityRef` (exact signal, exact
  version, provider, executable `module:qualname`), validated input, trusted `tenant_id`/
  `requester_id`, `AuthorizationProvenance`, `RecoveryPolicyRef`, correlation/causation.
  Identity must equal the provenance identity. It grants nothing.
- Input is either `InlineInput` (a JSON object stored as immutable canonical text with a
  SHA-256 digest, at most 64 KiB) or `InputReference` (content-addressed URI + digest +
  size; userinfo, query strings and fragments are rejected so signed URLs cannot be stored).
  The service admits a reference only through a configured `InputReferenceVerifier` that
  confirms existence, tenant access, digest/size and retention in an approved immutable
  store. None exists yet, so references are **rejected** (fail closed); only inline input
  is admissible today.
- `AuthorizationProvenance` (`authority: "utu"`) records tenant, principal, subject, UTU
  policy version and a one-way `evidence_digest` of the verified permit scope, versions and
  signal. Session IDs, permit versions, permits, JWTs and credentials are **not** persisted;
  the digest cannot be reconstructed into an `ExecutionPermit`.
- `MANUAL_REVIEW_V1` (`ops.recovery.manual_review@1`) is the only approved recovery policy:
  unresolved work is reconciled or reviewed, never blindly replayed. No V1 producer policy
  is adopted.

### Schema 3

`ManagedOperation.schema_version` is `2` or `3`. Schema 3 is the unchanged OPS2-04B
envelope plus a required `admission` object (`descriptor`, `idempotency_scope`,
`discovery_key`). Schema 2 forbids `admission` and its stored documents keep their exact
OPS2-04B shape. Validation binds `snapshot.kind` to the capability signal, `producer` to
`ops.admission`, correlation/causation to the descriptor, and `discovery_key` to
`created_at`/`operation_id`. Restoration checks the complete field set at every nesting
level and recomputes the inline digest. Nothing is migrated: schema-2 records stay schema 2.

Legacy isolation: OPS2-02 reads/commits reject schema 3 (`schema_version=1` predicate and
restoration); pre-04C.1 managed binaries filter `schema_version=2` and cannot match;
there is still no top-level `revision`. Old binaries cannot read schema 3, so route
admitted work only to upgraded consumers.

### Admission

`OperationAdmissionService.admit(request, authorization=...)` resolves the capability via
the `CapabilityCatalog` port, rejects a resolved version that differs from the request,
checks the recovery policy, builds the descriptor and calls
`MongoOperationAdmissionRepository.admit`. That is one `insert_one` of a complete pending
schema-3 aggregate with an idle epoch: operation, descriptor and idempotency scope become
visible together or not at all (`w=majority, j=true`).

Scoped idempotency: `idempotency_scope = sha256(tenant, requester, key)` under a unique
partial index. A duplicate with the same request digest (requested signal@version, input
digest, recovery policy; not the resolved provider/executable, so a retry across a redeploy
still matches and keeps the original binding) returns the existing aggregate with `created=False`; correlation,
causation and provenance differences do not change the digest. A different request raises
`IdempotencyConflict`. A duplicate `_id` remains `DuplicateOperation`. A driver error raises
`AmbiguousManagedWrite`; repeating with the same key resolves it. The raw key is not stored.
The adapter calls `ensure_indexes()` before its first insert and fails closed if an index
exists with conflicting options.

The service trusts only the `AuthorizationProvenance` it is given. The AgentVerse adapter
[`UTUAdmissionAuthority`](../../../domains/agentverse/operations/admission.py) produces it
after `UTUAuthority.check_permit` and a root-grant `require_invoke` for the target signal.
[`SynapticCapabilityCatalog`](../../../domains/agentverse/operations/admission.py) resolves
through `SynapticRegistry.resolve(signal, version)`; no registry or policy store is added.

### Discovery

`discover(DiscoveryRequest(limit<=100, after=key), tenant_id=None|tenant)` returns
`DispatchCandidate` references (operation, tenant, signal/version, correlation/causation,
expected revision/generation) oldest first, with keyset pagination over `discovery_key`.
`next_after` is `None` once a page is not full: that sweep is complete and the next one
restarts from the oldest key, so work admitted behind a cursor (caller clock skew) is
reached on the following sweep. Dispatchers must sweep pages, not poll only the first:
pending work that can never activate stays at the head until C.2 adds a terminal path.
Queries are index-hinted, so a missing index fails rather than scanning. Results can be
stale and are duplicated across callers by design; only the fenced OPS2-04B `activate`
decides eligibility, and exactly one concurrent activator wins. Activation or terminal
failure removes the operation from the partial indexes. Retry-pending work is **not**
discovered: that is recovery, not admission.

Registered capability: `operations.dispatch.discover@1.0.0`, provider `system.enlil`,
via [`register_dispatch_discovery`](../../../domains/agentverse/operations/discovery.py) on
the existing `SynapticRegistry`. CPBX/UTU govern invocation; the handler always scopes to
`synapse.tenant_id` and rejects payload tenants. Trusted bootstrap calls the same repository
method directly, so discovery never requires an Enlil-managed operation (kernel exception).
Nothing registers it at startup yet.

### Dispatch notices

[dispatch.py](dispatch.py): Event V2 contract `operations.dispatch.available` v1 with
`OperationDispatchNotice` (operation/tenant/signal/version/correlation/causation only).
`CognitiveEventDispatchNotifier` publishes through `CognitiveEventRuntime` (default-deny
publish policy, HMAC signature, envelope tenant = operation tenant, causation = operation
ID), which uses the existing RabbitMQ publisher-confirm/mandatory channel. Notices are hints:
duplicates are expected, publication failure is logged and reported as `notified=False`
without failing admission, and no notification state is stored. No outbox is needed
because discovery, not the broker, recovers missed notices. Not wired at startup: the
contract and a publish grant for the system principal must be configured explicitly.

### Indexes (`operations_v2`)

| Name | Keys | Options |
| --- | --- | --- |
| `ops_v2_admission_idempotency` | `admission.idempotency_scope` | unique, partial `$exists` |
| `ops_v2_pending_dispatch` | `admission.discovery_key` | partial `schema_version=3, snapshot.state=pending` |
| `ops_v2_pending_dispatch_tenant` | `admission.descriptor.tenant_id, admission.discovery_key` | same partial filter |

Schema 1/2 documents are outside all three. Index size follows the pending backlog.

### Executed verification

```sh
docker compose exec -T -e OPS2_MONGO_INTEGRATION=1 api python -m pytest -q src/base/operations/v2/tests src/domains/agentverse/tests/operations
```

Result on 2026-10-29 after the acceptance audit: **488 passed, 9 skipped** (423 previous +
65 new; the skips are the unchanged memory cases). The audit added: admission preserved
through activate/renew/release/takeover/commit_outcome (retry and terminal) on schema 3;
600 admitted records across 3 tenants plus v1/v2/activated noise, swept globally and per
tenant, with `explain()` of the exact production cursors showing one `IXSCAN`, no
`COLLSCAN`/`SORT`, 25 documents and at most 26 keys examined per 25-row page; sweep
wrap-around for skewed admissions; replay across an executable redeploy; fail-closed
references; and discovery through CPBX with the real `UTUAuthority` policy (no permit,
forged tenant/agent or superseded permit are denied before any query). [test_admission.py](tests/test_admission.py) uses real MongoDB 8.0
for atomic insertion, fresh-adapter reconstruction, concurrent same-key and conflicting
admissions, lost-response ambiguity followed by replay, legacy/old-binary predicates,
v2 shape preservation, concurrent discovery followed by competing fenced activations,
`explain()` checks for index use with no `COLLSCAN` and bounded docs/keys examined,
tenant isolation, fail-closed missing/conflicting indexes and recovery of a missed
publication after a simulated restart. Adapter tests exercise a real `UTUAuthority`,
`SynapticRegistry` and `SynapticExchange`.

### Limitations and OPS2-04C.2 blockers

- **Background authority:** UTU permits are session-bound. The provenance proves an
  admission-time decision only; UTU must define how detached work re-establishes authority
  and observes revocation before any worker executes a descriptor.
- No typed per-capability input schema exists on `SynapticBinding`; input is validated
  as bounded plain JSON only.
- Executable identity is recorded, not retained: executors must refuse a mismatched
  provider/executable, never substitute.
- Discovery ordering uses the caller clock (fairness only). There is no admission TTL,
  expiry, retention or cancellation command for pending work.
- Notices and discovery registration are not wired into lifespan; no consumer exists.
- Mongo-specific: partial indexes, `hint`, update pipelines and `$$NOW`. Cosmos
  compatibility, failover and high-volume behaviour are unverified.
