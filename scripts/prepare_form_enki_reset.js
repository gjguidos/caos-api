// Local mongosh maintenance proposal. Dry-run by default; NOT a CAOS capability.
// Execute the write branch only after explicit approval and a verified backup.
const target = db.getSiblingDB("caos");
const owner = "00000000-0000-4000-8000-000000000001";
const enki = "00000000-0000-4000-8000-000000000004";
const operation = "f4a54fca-0068-da12-b3e7-e206bf209596";
const filter = {
  id: enki,
  tenant_id: "caos-local",
  creator: owner,
  ownership: "tenant",
  runtime_version: "v2",
  type: "chat",
  role_name: "general",
  "store.idempotency_scope": "6915708b7d02b39d4c19b4b713feaf7a4796e65d228a2433c3dec8b1394e300f",
  "store.request_digest": "2f8040fb3d58ea0567777636270e580586fd8f481272ed137017254383a08df1",
};
// JOSHUA and Genesis are deterministic System Citizens resolved from the reviewed
// policy (system_citizens); these historical orchestration seeds are no longer read.
const citizens = [
  { id: "00000000-0000-4000-8000-000000000003", name: "Genesis" },
  { id: "00000000-0000-4000-8000-000000000005", name: "JOSHUA" },
].map(({ id, name }) => ({
  id, name, tenant_id: "caos-local", creator: owner, ownership: "tenant",
  runtime_version: "v2", type: "orchestration", genesis_local_seed: true,
}));
const approvals = {
  REMOVE_ONLY_REVIEWED_CHAT_ENKI: { citizens: false, remaining: 4 },
  REMOVE_REVIEWED_CHAT_ENKI_AND_CITIZEN_SEEDS: { citizens: true, remaining: 2 },
};
function requireState(condition, message) {
  if (!condition) throw new Error("Reset refused: " + message);
}
requireState(target.getName() === "caos", "unexpected database");
requireState(target.coll_agents.countDocuments({}) === 5, "Agent inventory changed");
requireState(target.coll_agents.countDocuments({ id: enki }) === 1, "Enki identity is missing or duplicated");
requireState(target.coll_agents.countDocuments(filter) === 1, "Enki differs from the reviewed chat snapshot");
for (const id of [
  "00000000-0000-4000-8000-000000000002",
  "00000000-0000-4000-8000-000000000003",
  "00000000-0000-4000-8000-000000000005",
]) {
  requireState(target.coll_agents.countDocuments({ id, tenant_id: "caos-local", creator: owner, genesis_local_seed: true }) === 1, "bootstrap identity changed");
}
for (const citizen of citizens) {
  requireState(target.coll_agents.countDocuments(citizen) === 1, citizen.name + " differs from the reviewed orchestration seed");
}
requireState(target.operations_v2.countDocuments({}) === 1, "operation inventory changed");
requireState(target.operations_v2.countDocuments({
  _id: operation, "snapshot.operation_id": operation,
  "snapshot.kind": "genesis.provision", "snapshot.state": "succeeded",
  "snapshot.revision": 2, "execution.status": "closed",
}) === 1, "historical operation is not the reviewed terminal operation");
requireState(target.coll_logs.countDocuments({}) === 4, "audit inventory changed");
requireState(target.coll_logs.countDocuments({ tenant_id: "caos-local", operation_id: operation }) === 4, "audit correlation changed");
const record = target.coll_agents.findOne(filter);
const approval = approvals[process.env.CAOS_RESET_APPROVAL];
print("Plan A (REMOVE_ONLY_REVIEWED_CHAT_ENKI): remove exactly one reviewed chat Enki definition; preserve Adam, Genesis, JOSHUA, other Agents, OPS, Seshat, Redis and indexes.");
print("Plan B (REMOVE_REVIEWED_CHAT_ENKI_AND_CITIZEN_SEEDS): Plan A plus the two reviewed Genesis/JOSHUA orchestration seeds; System Citizens do not need them.");
if (approval === undefined) {
  print("DRY RUN ONLY. No records modified.");
} else {
  requireState(process.env.CAOS_BACKUP_CONFIRMED === "yes", "verified backup not confirmed");
  requireState(process.env.CAOS_API_PAUSED_CONFIRMED === "yes", "API quiescence not confirmed");
  const removed = target.coll_agents.deleteOne({ ...filter, _id: record._id });
  requireState(removed.deletedCount === 1, "conditional deletion did not remove exactly one definition");
  if (approval.citizens) {
    for (const citizen of citizens) {
      const seed = target.coll_agents.deleteOne(citizen);
      requireState(seed.deletedCount === 1, "conditional deletion did not remove exactly one " + citizen.name + " seed");
    }
  }
  requireState(target.coll_agents.countDocuments({}) === approval.remaining, "unexpected remaining Agent count");
  requireState(target.operations_v2.countDocuments({}) === 1, "operation inventory changed");
  requireState(target.coll_logs.countDocuments({}) === 4, "audit inventory changed");
  print("Removed the reviewed records only. Recreate Enki through JOSHUA -> Genesis; never insert or patch the replacement manually.");
}
