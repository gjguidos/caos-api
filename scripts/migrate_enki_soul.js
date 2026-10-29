// Development migration: give the existing local Enki its Enki Soul (personality "enki").
//
// Approach: remove exactly the legacy Store-created Enki definition so the next API
// start recreates it through the governed bootstrap (JOSHUA -> Genesis -> Store), with
// the policy's current creation input. An in-place $set is deliberately NOT used: it
// would leave Store's request digest describing a definition that no longer exists and
// would bypass Genesis validation, composition and Seshat evidence.
//
// Preserved: Enki's stable ID, UTU grants and approvals (policy), every Seshat record and
// OPS V2 operation (history), Redis sessions/conversations, all other Agents.
//
// Procedure (local development database `caos` only):
//   1. docker compose stop api                       (no runtime may hold Enki)
//   2. mongodump --db caos --archive --gzip > backup  (and verify by scratch restore)
//   3. dry run:  docker compose exec -T mongo mongosh caos --quiet < scripts/migrate_enki_soul.js
//   4. apply:    { echo 'var APPLY="REMOVE_LEGACY_ENKI_FOR_SOUL_RECREATION";'; \
//                  cat scripts/migrate_enki_soul.js; } | docker compose exec -T mongo mongosh caos --quiet
//   5. docker compose start api                       (bootstrap recreates Enki via Genesis)
//   6. remove the temporary "compatible_personality_keys" allowance (done 2026-10-29)
//
// Dry run is the default. Nothing is written unless APPLY matches exactly.

const DB_NAME = "caos";
const ENKI = "00000000-0000-4000-8000-000000000004";
const GENESIS = "00000000-0000-4000-8000-000000000003";
const OWNER = "00000000-0000-4000-8000-000000000001";
const TENANT = "caos-local";
const TOKEN = "REMOVE_LEGACY_ENKI_FOR_SOUL_RECREATION";

if (db.getName() !== DB_NAME) {
  throw new Error(`Refusing to run against ${db.getName()}; expected ${DB_NAME}`);
}

const legacy = {
  id: ENKI,
  name: "Enki",
  type: "form",
  role_name: "enki",
  personality_key: "empty_eva",
  runtime_version: "v2",
  ownership: "tenant",
  tenant_id: TENANT,
  creator: OWNER,
  "store.operation": "create",
  "store.authorization.principal_id": GENESIS,
  "store.authorization.subject_id": OWNER,
};

const all = db.coll_agents.countDocuments({ id: ENKI });
const matching = db.coll_agents.countDocuments(legacy);
print(`Enki definitions with this ID: ${all}; matching the legacy predicate: ${matching}`);
if (all !== 1 || matching !== 1) {
  throw new Error("Enki is not exactly one legacy Store-created definition; nothing to do safely");
}
const others = db.coll_agents.countDocuments({ id: { $ne: ENKI } });
print(`Other Agents (preserved): ${others}`);
print(`Seshat records (preserved): ${db.coll_logs.countDocuments({})}`);
print(`OPS V2 operations (preserved): ${db.operations_v2.countDocuments({})}`);

const apply = typeof APPLY !== "undefined" && APPLY === TOKEN;
if (!apply) {
  print("DRY RUN ONLY. No records modified.");
} else {
  const result = db.coll_agents.deleteOne(legacy);
  if (result.deletedCount !== 1) {
    throw new Error("Conditional delete did not remove exactly one legacy Enki");
  }
  print("Removed the legacy Enki definition. Start the API to recreate it through Genesis.");
}
