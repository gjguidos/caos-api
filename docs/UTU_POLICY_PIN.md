# UTU Policy Pin (POLICY-PIN-0.1)

**Status:** implemented, committed in `7db5350`, pushed, integrated and startup-verified
(2026-11-03; startup logs `UTU policy pin verified`, approved and installed `88cdfa80…`). Security finding:
[STARTUP-POLICY-1](SECURITY_FINDINGS.md). **Audience:** operators of the managed local
deployment and reviewers of UTU policy changes.

Managed local startup (`GENESIS_LOCAL_ENABLED=true`) and **every Uvicorn reload** run
`initialize_local`. Before this increment, that installed whatever checked-in policy the
process loaded. So an accidental container start, or a reload triggered by any `.py`
edit in the mounted checkout, turned an unreviewed `config/genesis.local.json` edit into
installed authority. Startup now accepts only an explicitly approved policy version.

## 1. The pin

`config/genesis.local.pin.json` (next to the policy file: `<policy>.pin.json`, or
`GENESIS_POLICY_PIN_FILE`):

```json
{
  "contract": "caos.utu_policy_pin",
  "schema_version": 1,
  "tenant_id": "caos-local",
  "approved_policy_version": "<64-hex policy_version>",
  "transition_from": null,
  "provision_unprovisioned": false
}
```

- **Versions:** `approved_policy_version` is the existing UTU `policy_version` digest
  (grants and trusted lines; `security/revocation.py`). No other hash exists.
- **Strict:** the file is validated strictly: exact 64-character lowercase hex, no extra
  fields, `transition_from` must differ from the approved version, and
  `provision_unprovisioned` is a strict boolean. It carries no authority; grants still
  come only from the policy file.
- **First-install consent:** `provision_unprovisioned` (default `false`) allows a first
  install only when the installed policy is absent. A managed tenant that already
  exists keeps it `false`: there, a missing policy means loss, corruption or the wrong
  Redis, and startup must refuse. Disposable test fixtures use a copy of the pin that
  opts in. Consent never bypasses the tenant, source-policy or transition checks, and
  never overwrites an installed policy.
- **Read every time:** the pin is read on every startup and reload, never cached.

## 2. Startup behavior (`security/policy_pin.py`, `enforce_policy_pin`)

| Checked-in policy | Installed tenant policy | Result |
| --- | --- | --- |
| ≠ approved pin | anything | **Refused**, no Redis write |
| = pin | = pin | Starts; nothing is written (`verified`) |
| = pin | none, `provision_unprovisioned: false` | **Refused**, no Redis write |
| = pin | none, `provision_unprovisioned: true` | Installs the pinned version (`provisioned`, CAS on "absent"; if only the `policy` field was lost, the tenant incarnation and revocations are kept) |
| = pin | = `transition_from` | Installs the pinned version (`transitioned`, CAS on the exact stored value) |
| = pin | anything else | **Refused**, no Redis write; never overwritten |

Pin problems also refuse startup:
- a missing or unreadable pin;
- an invalid pin;
- a pin for another tenant;
- no policy file in managed local mode.

What installation preserves:
- the tenant incarnation (`HSETNX tenant`);
- every agent and session revocation, and a tenant-level revocation (a revoked tenant
  stays revoked);
- the existing permit rule that old permits never survive a version change.

A concurrent change between the read and the compare-and-set (or UTU being
unavailable) is reported as an actionable `PolicyPinError`, and the prior authority is
kept.

A refused startup aborts the application lifespan with a `PolicyPinError` that names
both short digests and the remedy. In reload mode only the worker stops: check the logs
or Nataraja, not the container status.

**Logging:** each accepted startup logs one INFO line:
`UTU policy pin verified|provisioned|transitioned: tenant=… installed=… approved=…`
(short digests only). This is operational observability, **not** durable authorization
provenance. UTU remains the sole policy authority, and no audit record is created.

**Nataraja** predicts the outcome read-only, in the guard's order:
- `policy.pin_invalid`
- `policy.pin_tenant_mismatch`
- `policy.pin_mismatch`
- `policy.unprovisioned_refused`
- `policy.transition_stale` (warning)
- `policy.installed_not_pinned`
- `policy.transition_armed` (warning)

Its drift action is `approve_policy_pin_transition`, never a plain restart.

## 3. Approved transition procedure

A restart is never approval. Approval is a reviewed change to the pin.

1. **Prepare:** prepare the policy change outside the mounted checkout, for example in a
   separate worktree. Compute the new `policy_version`: Nataraja `inspect` on that
   checkout reports it as `revisions.policy_version`.
2. **Edit the pin in the same reviewed change:**
   - `approved_policy_version` = the new version;
   - `transition_from` = the exact currently installed version (Nataraja
     `revisions.installed_policy_version`).
3. **Check before the window:** take backups and confirm no revision application or other
   authority-dependent operation is in flight.
4. **Window:**
   - stop the API;
   - bring the reviewed change into the mounted checkout;
   - Nataraja `inspect` must show `policy.transition_armed`;
   - `docker start caos-api`.
5. **Verify:** Nataraja shows source = installed = runtime = the approved version.
6. **Disarm:** set `transition_from` back to `null` in a follow-up reviewed change. A stale
   value authorizes nothing once the installed version equals the pin, but leaving it
   armed is not tidy.

**Rollback** is the same procedure in reverse: pin the old version with
`transition_from` = the new one. **If the transition is refused or raced**, the prior
installed authority stays in place; restore the previous pin and policy, then restart.
