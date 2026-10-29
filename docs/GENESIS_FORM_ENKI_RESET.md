# Form-first Enki V2 — guarded local reset proposal

**Status:** Commands prepared, NOT approved or executed. Existing local Mongo/Redis
data is preserved. Development API is paused to prevent automatic bootstrap during
source changes. Tests use isolated databases and disposable Redis, never the `caos`
database or its Redis state.

## Implemented contract

The local slot pins Enki's stable ID `00000000-0000-4000-8000-000000000004` to
`type="form"`, `runtime_version="v2"`. Register role `enki` (operational), retain
`empty_eva`, and keep only the existing `chat.context`/`chat.response` grants.
The shared V2 cognitive-composition predicate accepts `chat` or the explicit
`form/enki` pair; other Form roles remain unsupported. Role compatibility, real
backend availability, UTU, CPBX, Store and Khepri boundaries remain enforced.

JOSHUA initiation checks the current pinned name/type and the reviewed form role
and personality before opening or replaying an operation. An old chat creation
request cannot return its historical success
as a successful request against the new form pin. Historical OPS/Seshat facts are
not modified or re-attributed.

Client route `/v2/forms/{agentId}` reuses dynamic form widgets and offers an
independent `/v2/conversations?agent={agentId}` link. It requires a readable
V2 `form/enki` definition. Draft contract `caos.agent_creation_draft`, version 1,
contains `resource_type="agent"` and an Agent Creation Input. The first UI supports
a minimal input subset (name, chat/form type, role, personality, prompt, visibility).
Zod validates structural constraints locally; registry readability/compatibility
and execution authorization still require future server-side validation.
No draft persistence, V1 command transport, Genesis submission, LLM call or
permission provisioning occurs when editing/previewing the form.

Next milestone needs an explicitly approved draft-submission capability/authority
contract and correlated result/error mapping. The draft contract itself grants
nothing. No such submission is implemented here.

## Reviewed data and preservation

The configured target was verified local, but the entire database was NOT declared
disposable. Reviewed `caos` inventory: 5 Agent documents, 1 OPS V2 operation, 4 Seshat
records, 3 Agent run reports, 1 legacy operation log. The reset proposal removes
only the old chat/general Enki Store definition, matching its recorded scope and
digest. It preserves all other documents, every index, the historical terminal
operation `f4a54fca-0068-da12-b3e7-e206bf209596` and all four audit facts.

Redis inventory: 46 revoked operator sessions, 0 unexpired active sessions, 0 scoped
presence/socket leases, 0 pending scoped expiry entries, and 11 operator
conversations (none containing Enki). All are preserved. Do not flush Redis or
delete revocation tombstones. Use a fresh verified operator session after reset.
Zero leases do not establish process quiescence: keep the API paused and check
for other writers before the conditional deletion.

The legacy Adam bootstrap is removed: the local policy no longer approves or grants
Adam and bootstrap seeds no Agent at all. JOSHUA and Genesis are
deterministic System Citizens declared in the reviewed policy (`system_citizens`).
UTU resolves them without `coll_agents`, and bootstrap no longer seeds them. Their
historical `type="orchestration"` records are unused; bootstrap still refuses to
start if they differ from the trusted seed. Removing them is optional (Plan B).

## Exact commands — approval required before the write command

Run from the `caos-api` repository. These are proposals, not instructions to execute
without the owner's approval. Do not replace them with a database drop, an Agent
field update, a Redis flush, or a manual replacement insertion.

1. Verify the configured API target still resolves to local Mongo database `caos`
   and local Redis, and verify no other process can write the reviewed records.
   Verify the development API remains paused:

```bash
docker inspect --format '{{.State.Paused}}' caos-api
```

Expected: `true`. If not, stop and establish quiescence through the existing
lifecycle/shutdown procedures before reset. Do not revoke or awaken Agents merely
to run this maintenance operation.

2. Create a protected backup outside the repository (replace the path if needed):

```bash
umask 077
BACKUP="/private/var/folders/3g/s16h3rgj1vl7cb7wgy1dt0g40000gn/T/opencode/caos-before-form-enki-$(date +%Y%m%dT%H%M%S).archive.gz"
docker compose exec -T mongo mongodump --db caos --archive --gzip > "$BACKUP"
test -s "$BACKUP"
shasum -a 256 "$BACKUP"
```

Require successful dump completion and verify the archive can be restored into a
separate disposable database before confirming the backup. Never restore over
the source database or commit/publish backups. Do not add tokens or permits to
the maintenance manifest. This Mongo backup
does not back up Redis; no Redis records are removed by this proposal.

3. Inspect the exact predicates without writing:

```bash
docker compose exec -T mongo mongosh --quiet --file /dev/stdin < scripts/prepare_form_enki_reset.js
```

Expected: `DRY RUN ONLY. No records modified.` Every inventory/identity/scope/digest
predicate must match. A mismatch requires a fresh investigation, not a weaker filter.

4. **Only after explicit approval, verified backup, quiescence and successful dry run:**

```bash
if test "$(docker inspect --format '{{.State.Paused}}' caos-api)" = true && test -s "$BACKUP"; then
  docker compose exec -T \
    -e CAOS_RESET_APPROVAL=REMOVE_ONLY_REVIEWED_CHAT_ENKI \
    -e CAOS_BACKUP_CONFIRMED=yes \
    -e CAOS_API_PAUSED_CONFIRMED=yes \
    mongo mongosh --quiet --file /dev/stdin < scripts/prepare_form_enki_reset.js
else
  echo 'Reset refused: API is not paused or backup is missing.' >&2
fi
```

For Plan B, substitute `CAOS_RESET_APPROVAL=REMOVE_REVIEWED_CHAT_ENKI_AND_CITIZEN_SEEDS` (separate approval).
The conditional refuses to run the write command if either shell check fails.
The environment markers are operator confirmations, not
CAOS permissions or substitutes for explicit approval. Plan B additionally
deletes the two exact Genesis/JOSHUA orchestration seeds, leaving 2 Agent
documents (Adam, Escher); it requires its own approval. Plan A expected effect: exactly
one conditional deletion, leaving 4 Agent documents, 1 historical operation and
4 historical audit records. No Agent insertion/update, OPS/Seshat deletion, index
change or Redis write is performed.

5. After separate restart approval, resume the development API:

```bash
docker compose unpause api
```

Bootstrap seeds no Agent: not Adam, not Enki, and never Genesis/JOSHUA definitions (System Citizens). Through the
existing authenticated JOSHUA entry point, request the following creation input:

```json
{
  "creation": {
    "name": "Enki",
    "type": "form",
    "role_name": "enki",
    "personality_key": "empty_eva",
    "prompt": "You are Enki. Prepare structured Agent creation drafts for review. Conversation is independent; do not claim to create Agents or grant permissions.",
    "visibility": "private"
  }
}
```

Do not post this from Enki's draft-only UI. Existing JOSHUA session/runtime setup
remains necessary; Enki receives no creation signal. Successful governed recreation
returns the same Enki ID with a new operation identity and new initiation plus
validation/composition/Store evidence. Original records remain historical.
Enki remains unactivated; this proposal does not authorize Khepri awakening.

## Clean development reset (alternative to Plans A/B)

Executed on 2026-10-29 under explicit authorization, local `caos` only:

1. Confirm the API targets `DB_NAME=caos` on the local compose Mongo and runs this code.
2. `docker compose stop api` (background workers run in-process).
3. `docker compose exec -T mongo mongodump --db caos --archive --gzip > ../backups/caos-pre-genesis-reset-<ts>.archive.gz`;
   verify by restoring into a scratch DB (`--nsFrom='caos.*' --nsTo='caos_backup_verify.*'`), comparing
   counts, indexes and content, then dropping the scratch DB.
4. Drop only `caos`. Redis is not flushed: the UTU policy key is reinstalled by CAS at startup,
   revocation tombstones and conversations are preserved.
5. `docker compose start api`. Since the automatic bootstrap, startup itself creates Enki through
   the policy `bootstrap` lease (JOSHUA → Genesis → Store, Seshat `origin: bootstrap`); no
   manual request is needed. Startup fails, without writing, if Enki cannot be confirmed in time.
6. (Historical, 2026-10-29 reset) Enki was created through
   `POST /api/v2/agentverse/agents/{JOSHUA}/signals/genesis.enki.request` by an operator session.

Expected result: `coll_agents` holds exactly Enki (`form/enki/v2/empty_eva`, Store provenance,
sleeping); `operations_v2` holds one `genesis.provision` operation; `coll_logs` holds four
records (initiation by JOSHUA; validation, composition, store by Genesis). Restore with
`mongorestore --archive --gzip --drop` from the backup if needed.
