"""Pure, deterministic evaluation: manifest + observation → resources, findings, plan.

No I/O and no clock: equal inputs give byte-identical output. Every proposed action
names the existing System Citizen or service that must perform it and the authority it
needs; Nataraja itself executes nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, cast

from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.agents.creation_drafts.capability import (
    PROPOSE_PROVIDER,
    PROPOSE_SIGNAL,
    PROPOSE_VERSION,
)
from src.domains.agentverse.agents.genesis.initiation import PROVIDER as GENESIS_PROVIDER
from src.domains.agentverse.agents.genesis.initiation import SIGNALS as GENESIS_SIGNALS
from src.domains.agentverse.agents.genesis.initiation import VERSION as GENESIS_VERSION
from src.domains.agentverse.catalog.capability import PROVIDER as CATALOG_PROVIDER
from src.domains.agentverse.catalog.capability import SIGNAL as CATALOG_SIGNAL
from src.domains.agentverse.catalog.capability import VERSION as CATALOG_VERSION
from src.domains.agentverse.entities.gui_components import effective_gui_components
from src.domains.agentverse.manuscripts.capability import PROVIDER as MANUSCRIPT_PROVIDER
from src.domains.agentverse.manuscripts.capability import SIGNALS as MANUSCRIPT_SIGNALS
from src.domains.agentverse.manuscripts.capability import VERSION as MANUSCRIPT_VERSION
from src.domains.agentverse.nataraja.manifest import AgentExpectation, EnvironmentManifest
from src.domains.agentverse.nataraja.observe import Observation
from src.domains.agentverse.reasoning.capability import PROVIDER as REASONING_PROVIDER
from src.domains.agentverse.reasoning.capability import SIGNAL as REASONING_SIGNAL
from src.domains.agentverse.reasoning.capability import VERSION as REASONING_VERSION
from src.domains.agentverse.tools.store_tool.spec import (
    STORE_CREATE_APPROVED_SIGNAL,
    STORE_CREATE_APPROVED_VERSION,
    STORE_CREATE_PROVISIONED_SIGNAL,
    STORE_CREATE_PROVISIONED_VERSION,
    STORE_CREATE_SIGNAL,
    STORE_CREATE_VERSION,
    STORE_PROVIDER,
)
from src.domains.agentverse.tools.validation_tool.spec import (
    DATA_VALIDATION_PROVIDER,
    DATA_VALIDATION_SIGNAL,
    DATA_VALIDATION_VERSION,
)

# Provider contracts declared by source for every signal a policy may grant.

DECLARED_PROVIDERS: dict[str, tuple[str, str]] = {
    DATA_VALIDATION_SIGNAL: (DATA_VALIDATION_PROVIDER, DATA_VALIDATION_VERSION),
    STORE_CREATE_SIGNAL: (STORE_PROVIDER, STORE_CREATE_VERSION),
    STORE_CREATE_PROVISIONED_SIGNAL: (STORE_PROVIDER, STORE_CREATE_PROVISIONED_VERSION),
    STORE_CREATE_APPROVED_SIGNAL: (STORE_PROVIDER, STORE_CREATE_APPROVED_VERSION),
    **{signal: (GENESIS_PROVIDER, GENESIS_VERSION) for signal in GENESIS_SIGNALS.values()},
    PROPOSE_SIGNAL: (PROPOSE_PROVIDER, PROPOSE_VERSION),
    **{signal: (MANUSCRIPT_PROVIDER, MANUSCRIPT_VERSION) for signal in MANUSCRIPT_SIGNALS.values()},
    CATALOG_SIGNAL: (CATALOG_PROVIDER, CATALOG_VERSION),
    REASONING_SIGNAL: (REASONING_PROVIDER, REASONING_VERSION),
    "chat.context": ("tool.chat", "1.0.0"),
    "chat.response": ("cognitive.chat", "1.0.0"),
}

REPORT_CONTRACT = "caos.nataraja.report.v1"
Status = Literal["passed", "warning", "failed", "blocked", "deferred"]
_RANK = {"passed": 0, "deferred": 1, "warning": 2, "blocked": 3, "failed": 4}


def worst(statuses: Sequence[str]) -> Status:
    return cast(Status, max(statuses, key=_RANK.__getitem__, default="passed"))


class _Builder:
    def __init__(self) -> None:
        self.resources: list[dict[str, Any]] = []
        self.findings: list[dict[str, Any]] = []
        self.plan: list[dict[str, Any]] = []

    def resource(self, kind: str, identity: str, name: str, status: Status, **facts: Any) -> None:
        self.resources.append(
            {"kind": kind, "id": identity, "name": name, "status": status, **facts}
        )

    def finding(self, code: str, resource: str, status: Status, message: str, **facts: Any) -> None:
        self.findings.append(
            {"code": code, "resource": resource, "status": status, "message": message, **facts}
        )

    def action(
        self,
        resource: str,
        action: str,
        *,
        expected: Any,
        actual: Any,
        classification: str,
        responsible: str,
        authority: str,
        depends_on: tuple[str, ...] = (),
        destructive: bool = False,
        approval: bool = True,
    ) -> None:
        self.plan.append(
            {
                "id": f"{resource}:{action}",
                "resource": resource,
                "action": action,
                "expected": expected,
                "actual": actual,
                "classification": classification,
                "responsible": responsible,
                "authority": authority,
                "depends_on": list(depends_on),
                "destructive": destructive,
                "requires_human_approval": approval,
                "executed": False,
            }
        )


def evaluate(
    manifest: EnvironmentManifest, observation: Observation, *, mode: str
) -> dict[str, Any]:
    b = _Builder()
    o = observation
    env = o.environment
    if not (env.get("environment") == "development" and env.get("genesis_local_enabled")):
        b.finding(
            "environment.not_local_development",
            "environment",
            "blocked",
            "Nataraja runs only in local development with local Genesis enabled",
        )
    for target in o.unreachable:
        b.finding(f"{target}.unreachable", target, "blocked", f"{target} could not be read")

    policy = o.policy
    _policy(b, manifest, o)
    _citizens(b, manifest, policy)
    _services(b, manifest, o)
    agents = {document.get("id"): document for document in o.agents}
    for expected in manifest.agents:
        _agent(b, manifest, o, expected, agents.get(expected.agent_id))
    _unmanaged(b, manifest, o)
    _evidence(b, o)
    _manual_creation(b, manifest, o)
    _runtime(b, manifest, o)
    _providers(b, o)
    _startup(b, manifest, o, agents)

    # Plan references are other plan items or explicit ``resource:`` preconditions.
    ids = {item["id"] for item in b.plan}
    for item in b.plan:
        item["depends_on"] = sorted(
            dep for dep in item["depends_on"] if dep in ids or dep.startswith("resource:")
        )
    status = worst([r["status"] for r in b.resources] + [f["status"] for f in b.findings])
    body = {
        "contract": REPORT_CONTRACT,
        "mode": mode,
        "environment": manifest.environment,
        "status": status,
        "revisions": {
            "manifest_revision": manifest.revision,
            "manifest_digest": manifest.digest,
            "policy_file_digest": o.policy_file_digest,
            "policy_version": o.policy_version,
            "installed_policy_version": o.installed_policy_version,
            "runtime_policy_version": (o.runtime or {}).get("policy_version"),
            "runtime_boot_id": ((o.runtime or {}).get("process") or {}).get("boot_id"),
        },
        "targets": o.targets,
        "resources": sorted(b.resources, key=lambda r: (r["kind"], r["id"])),
        "findings": sorted(b.findings, key=lambda f: (f["resource"], f["code"])),
        "reconciliation_plan": _ordered(b.plan) if mode == "reconcile" else [],
    }
    return {**body, "report_digest": sha256_hex(body)}


def _ordered(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dependencies first; ties by ID, so the order is deterministic."""
    pending = {item["id"]: item for item in plan}
    done: set[str] = set()
    ordered: list[dict[str, Any]] = []
    while pending:
        ready = sorted(
            identity
            for identity, item in pending.items()
            if all(dep in done or dep not in pending for dep in item["depends_on"])
        ) or sorted(pending)  # a cycle cannot occur; never loop forever if it did
        ordered.append(pending.pop(ready[0]))
        done.add(ready[0])
    return ordered


def _policy(b: _Builder, manifest: EnvironmentManifest, o: Observation) -> None:
    if o.policy is None:
        b.resource("policy", "genesis-policy", "Genesis security policy", "failed")
        b.finding("policy.invalid", "genesis-policy", "failed", o.policy_error or "No policy")
        return
    installed = o.installed_policy_version
    if installed is None:
        status: Status = "warning"
        b.finding(
            "policy.not_installed",
            "genesis-policy",
            "warning",
            "UTU has no installed tenant policy; the API has not started with this policy",
        )
    elif installed != o.policy_version:
        status = "warning"
        b.finding(
            "policy.revision_drift",
            "genesis-policy",
            "warning",
            "The running API enforces a different policy revision than the checked-in file",
        )
        b.action(
            "genesis-policy",
            "restart_api_with_checked_in_policy",
            expected=o.policy_version,
            actual=installed,
            classification="drift",
            responsible="UTU (startup install_policy)",
            authority="Operator restart of the development API; startup policy CAS",
        )
    else:
        status = "passed"
    b.resource(
        "policy", "genesis-policy", "Genesis security policy", status, tenant_id=manifest.tenant_id
    )


def _citizens(b: _Builder, manifest: EnvironmentManifest, policy: Any) -> None:
    citizens = getattr(policy, "citizens", {}) or {}
    approvals = getattr(policy, "approvals", {}) or {}
    for expected in manifest.system_citizens:
        scope = (manifest.tenant_id, expected.principal_id)
        citizen = citizens.get(scope)
        approval = approvals.get(scope)
        ok = (
            citizen is not None
            and citizen.name == expected.name
            and approval is not None
            and approval.enabled is True
            and approval.owner_user_id == manifest.owner_id
        )
        status: Status = (
            "passed" if ok else ("failed" if expected.requirement == "required" else "warning")
        )
        b.resource(
            "system_citizen",
            expected.principal_id,
            expected.name,
            status,
            evidence="policy system_citizens + enabled UTU approval (no Agent document)",
        )
        if not ok:
            b.finding(
                "citizen.unregistered",
                expected.principal_id,
                status,
                f"{expected.name} is not a registered, approved System Citizen",
            )


def _services(b: _Builder, manifest: EnvironmentManifest, o: Observation) -> None:
    for expected in manifest.system_services:
        declared = o.services.get(expected.name, False)
        observed: list[str] = []
        if expected.name == "UTU" and o.installed_policy_version:
            observed.append("tenant policy installed in Redis")
        if expected.name == "Seshat" and "coll_logs" in o.collections and not o.corrupt_evidence:
            observed.append("coll_logs readable and every record verified")
        if expected.name == "CPBX" and o.policy is not None:
            observed.append("grant policy parsed")
        status: Status = (
            "passed"
            if declared
            else ("failed" if expected.requirement == "required" else "warning")
        )
        b.resource(
            "system_service",
            expected.name,
            expected.name,
            status,
            evidence=["DI provider declared" if declared else "DI provider missing", *observed],
            note="Trusted runtime service, not a System Citizen principal",
        )


def _agent(
    b: _Builder,
    manifest: EnvironmentManifest,
    o: Observation,
    expected: AgentExpectation,
    actual: dict[str, Any] | None,
) -> None:
    rid = expected.agent_id
    policy = o.policy
    statuses: list[Status] = []

    def note(code: str, status: Status, message: str, **facts: Any) -> None:
        statuses.append(status)
        b.finding(code, rid, status, message, **facts)

    # Registries resolve the declared Role and Soul.
    if expected.role_name not in o.roles:
        note("agent.role_unregistered", "failed", f"Role {expected.role_name} is not registered")
    if expected.personality_key not in o.souls:
        note(
            "agent.soul_unregistered",
            "failed",
            f"Soul {expected.personality_key} is not registered",
        )
    if expected.type not in o.agent_types:
        note("agent.type_unregistered", "failed", f"Agent type {expected.type} is not registered")

    # Genesis policy pins the identity and the governed creation path.
    provision = (getattr(policy, "provisions", {}) or {}).get(expected.creation.slot)
    pinned = provision is not None and (
        provision.agent_id,
        provision.type,
        provision.role_name,
        provision.personality_key,
        provision.runtime_version,
    ) == (
        rid,
        expected.type,
        expected.role_name,
        expected.personality_key,
        expected.runtime_version,
    )
    if policy is not None and not pinned:
        note("agent.policy_pin_mismatch", "failed", "Genesis policy does not pin this identity")
    handoff = next(
        (h for h in getattr(policy, "handoffs", ()) or () if h.slot == expected.creation.slot), None
    )
    grants = getattr(policy, "authorization", None)
    initiator_grants = (
        grants.grants_for(_Principal(manifest.tenant_id, expected.creation.initiator)).invoke
        if grants is not None
        else frozenset()
    )
    path_ok = (
        handoff is not None
        and handoff.initiator_id == expected.creation.initiator
        and expected.creation.signal in initiator_grants
    )
    if policy is not None and not path_ok:
        note(
            "agent.creation_path_unavailable",
            "failed" if expected.requirement == "required" else "blocked",
            "No reviewed handoff/grant lets the initiator request this creation",
        )
    if handoff is not None and handoff.activation != "none":
        note(
            "agent.activation_coupled",
            "warning",
            "The reviewed handoff activates the Agent through Khepri in the creation request; "
            "the manifest expects independent activation",
            handoff_activation=handoff.activation,
        )

    if actual is None:
        if expected.requirement == "required":
            status: Status = "failed"
            classification = "error"
        elif expected.requirement == "deferred":
            status, classification = "deferred", "deferred"
        else:
            status, classification = "passed", "optional_absence"
        # Absence is classified on its own; prerequisite problems stay findings.
        b.resource(
            "agent", rid, expected.name, status, present=False, requirement=expected.requirement
        )
        if expected.requirement != "manual":
            _creation_action(b, manifest, expected, classification)
        return

    mismatched = {
        field: {"expected": want, "actual": actual.get(field)}
        for field, want in (
            ("name", expected.name),
            ("type", expected.type),
            ("role_name", expected.role_name),
            ("personality_key", expected.personality_key),
            ("runtime_version", expected.runtime_version),
            ("tenant_id", manifest.tenant_id),
            ("creator", manifest.owner_id),
            ("ownership", "tenant"),
        )
        if actual.get(field) != want
    }
    try:
        effective = effective_gui_components(actual)
    except (ValueError, TypeError, KeyError):
        effective = []
    gui = [{"key": c.key, "type": c.type, "default": c.default} for c in effective]
    expected_gui = [c.model_dump() for c in expected.gui]
    if gui != expected_gui:
        mismatched["gui_components"] = {"expected": expected_gui, "actual": gui}
    if mismatched:
        note("agent.definition_drift", "failed", "Persisted definition differs", fields=mismatched)
        b.action(
            rid,
            "replace_definition_through_genesis",
            expected=expected.model_dump(mode="json"),
            actual=sorted(mismatched),
            classification="error",
            responsible="Genesis (Store is insert-only)",
            authority="A reviewed reset proposal; requires the not-yet-implemented Great Reset",
            destructive=True,
        )
    store = actual.get("store")
    genesis = next((c.principal_id for c in manifest.system_citizens if c.name == "Genesis"), None)
    if not store or store.get("principal_id") != genesis or not store.get("scoped"):
        note(
            "agent.not_store_created", "failed", "Definition was not persisted by Genesis via Store"
        )
    else:
        want = "bootstrap" if expected.creation.via == "genesis_bootstrap" else "session"
        if store.get("origin") != want:
            note(
                "agent.creation_origin",
                "warning",
                f"Store authority origin is {store.get('origin')}, expected {want}",
            )
    stored = [
        e
        for e in o.evidence
        if e["event_type"] == "genesis.store.completed" and e["resource_id"] == rid
    ]
    if not stored:
        note("agent.store_evidence_missing", "failed", "No Seshat store evidence for this Agent")
    else:
        operations = {op["operation_id"]: op for op in o.operations}
        for evidence in stored:
            op = operations.get(evidence["operation_id"] or "")
            if op is None or op["state"] != "succeeded":
                note(
                    "agent.operation_unconfirmed",
                    "failed",
                    "The Genesis operation for the store evidence is not succeeded",
                )
    # Runtime presence leases are session-scoped digests; any lease is reported.
    if o.presence_count:
        note(
            "agent.runtime_presence",
            "warning",
            "Runtime presence leases exist; an Agent may be awake (lifecycle state, not drift)",
            leases=o.presence_count,
        )
    b.resource(
        "agent",
        rid,
        expected.name,
        worst(statuses) if statuses else "passed",
        present=True,
        lifecycle="sleeping" if not o.presence_count else "possibly_active",
    )


def _creation_action(
    b: _Builder, manifest: EnvironmentManifest, expected: AgentExpectation, classification: str
) -> None:
    rid = expected.agent_id
    if expected.creation.via == "genesis_bootstrap":
        b.action(
            rid,
            "bootstrap_through_joshua_and_genesis",
            expected="present, sleeping",
            actual="absent",
            classification=classification,
            responsible="JOSHUA → Genesis → Store (EnkiBootstrap at API startup)",
            authority="Policy bootstrap lease for the pinned slot",
            depends_on=("genesis-policy:restart_api_with_checked_in_policy",),
        )
        return
    initiator = next(
        (a for a in manifest.agents if a.agent_id == expected.creation.initiator), None
    )
    initiator_name = initiator.name if initiator else expected.creation.initiator
    b.action(
        rid,
        f"activate_{initiator_name.lower()}_through_khepri",
        expected=f"{initiator_name} runtime awake for an operator session",
        actual="unknown (sleeping by default)",
        classification="lifecycle_state",
        responsible="Khepri",
        authority=f"Operator session + enabled UTU approval for {initiator_name}",
        depends_on=(f"resource:{expected.creation.initiator}",),
    )
    b.action(
        rid,
        f"request_{expected.creation.signal}",
        expected="present (created exactly once by Genesis)",
        actual="absent",
        classification=classification,
        responsible=f"{initiator_name} → Genesis → Store (persists sleeping; never activates)",
        authority=f"{initiator_name} grant {expected.creation.signal} + live operator session",
        depends_on=(
            f"{rid}:activate_{initiator_name.lower()}_through_khepri",
            "genesis-policy:restart_api_with_checked_in_policy",
        ),
    )
    b.action(
        rid,
        f"activate_{expected.name.lower()}_through_khepri",
        expected=f"{expected.name} awake only when an operator asks for it",
        actual="absent",
        classification="lifecycle_state",
        responsible=f"Khepri ({expected.lifecycle.activation_operation})",
        authority=(
            f"Operator session + enabled UTU approval for {expected.name}; names the "
            "creating Genesis operation"
        ),
        depends_on=(f"{rid}:request_{expected.creation.signal}",),
    )


def _unmanaged(b: _Builder, manifest: EnvironmentManifest, o: Observation) -> None:
    declared = {a.agent_id for a in manifest.agents}
    for document in o.agents:
        identity = document.get("id")
        if identity in declared:
            continue
        store = document.get("store") or {}
        manual = bool(store.get("scoped")) and manifest.manual_agents == "allowed"
        status: Status = "passed" if manual else "warning"
        b.resource(
            "agent",
            str(identity),
            str(document.get("name")),
            status,
            present=True,
            managed="manual (Store-created)" if manual else "unmanaged",
        )
        if not manual:
            b.finding(
                "agent.unmanaged",
                str(identity),
                "warning",
                "Agent is outside the manifest and was not created through Store",
            )


def _evidence(b: _Builder, o: Observation) -> None:
    if o.corrupt_evidence:
        b.finding(
            "seshat.corrupt_records",
            "Seshat",
            "failed",
            "Stored audit records failed integrity verification",
            count=o.corrupt_evidence,
        )
    if o.corrupt_operations:
        b.finding(
            "ops.corrupt_operations",
            "operations",
            "failed",
            "Stored OPS V2 operations failed verification",
            count=o.corrupt_operations,
        )
    events = {e["event_id"] for e in o.evidence}
    dangling = sorted(
        e["event_id"]
        for e in o.evidence
        if e["causation_id"]
        and e["causation_id"] not in events
        and e["causation_id"] != e["correlation_id"]
    )
    if dangling:
        b.finding(
            "seshat.dangling_causation",
            "Seshat",
            "warning",
            "Some records name a cause that is not recorded",
            events=dangling,
        )
    for op in o.operations:
        status: Status = (
            "passed"
            if op["state"] == "succeeded"
            else ("warning" if op["state"] in ("running", "retry_pending", "pending") else "failed")
        )
        b.resource("operation", op["operation_id"], op["kind"], status, state=op["state"])


def _manual_creation(b: _Builder, manifest: EnvironmentManifest, o: Observation) -> None:
    """G3.4 readiness: scope-only grant and binding configured *and* enforced live."""
    policy = o.policy
    genesis = next((c.principal_id for c in manifest.system_citizens if c.name == "Genesis"), None)
    grants = getattr(policy, "authorization", None)
    configured = (
        grants is not None
        and "store.create.approved"
        in grants.grants_for(_Principal(manifest.tenant_id, str(genesis))).invoke
        and bool(getattr(policy, "creation_gateways", ()))
    )
    live = configured and o.installed_policy_version == o.policy_version
    status: Status = "passed" if live else ("blocked" if configured else "deferred")
    b.resource(
        "capability",
        "agent.creation_draft.create",
        "G3.4 governed manual creation",
        status,
        configured=configured,
        enforced_by_running_api=live,
    )
    if configured and not live:
        b.finding(
            "capability.not_live",
            "agent.creation_draft.create",
            "blocked",
            "Governed creation is configured but the running API has not loaded its policy",
        )


def _runtime(b: _Builder, manifest: EnvironmentManifest, o: Observation) -> None:
    runtime = o.runtime
    if runtime is None:
        b.resource(
            "runtime", "api-process", "Running API process", "warning", observation="unavailable"
        )
        b.finding(
            "runtime.unavailable",
            "api-process",
            "warning",
            "The running API did not report its runtime; provider versions are declared only",
        )
        return
    statuses: list[Status] = []
    if runtime.get("policy_version") != o.policy_version:
        statuses.append("warning")
        b.finding(
            "runtime.policy_drift",
            "api-process",
            "warning",
            "The running API loaded a different policy revision than the checked-in file",
        )
    loaded = {h.get("slot"): h.get("activation") for h in runtime.get("handoffs") or []}
    for expected in manifest.agents:
        activation = loaded.get(expected.creation.slot)
        if expected.lifecycle.activation == "khepri_independent" and activation not in (
            None,
            "none",
        ):
            statuses.append("warning")
            b.finding(
                "runtime.activation_coupled",
                expected.agent_id,
                "warning",
                "The running API couples this Agent's creation with Khepri activation",
                handoff_activation=activation,
            )
    process = runtime.get("process") or {}
    build = runtime.get("build") or {}
    b.resource(
        "runtime",
        "api-process",
        "Running API process",
        worst(statuses) if statuses else "passed",
        observation="confirmed",
        boot_id=process.get("boot_id"),
        started_at=process.get("started_at"),
        build_version=build.get("version"),
        build_revision=build.get("revision"),
    )


def _providers(b: _Builder, o: Observation) -> None:
    """Policy-declared provider versions vs runtime-confirmed registrations."""
    grants = getattr(o.policy, "authorization", None)
    granted = (
        sorted({s for g in getattr(grants, "_grants", {}).values() for s in g.invoke})
        if grants is not None
        else []
    )
    registered = {
        r.get("signal"): (r.get("provider"), r.get("version"))
        for r in (o.runtime or {}).get("registrations") or []
    }
    for signal in granted:
        declared = DECLARED_PROVIDERS.get(signal)
        facts: dict[str, Any] = {
            "declared_provider": declared[0] if declared else None,
            "declared_version": declared[1] if declared else None,
        }
        if o.runtime is None:
            status: Status = "warning"
            facts["runtime"] = "unavailable"
        elif signal not in registered:
            status = "failed"
            facts["runtime"] = "not_registered"
        else:
            provider, version = registered[signal]
            facts.update(runtime_provider=provider, runtime_version=version)
            confirmed = declared is not None and (provider, version) == declared
            status = "passed" if confirmed else "failed"
            facts["runtime"] = "confirmed" if confirmed else "mismatch"
        if declared is None:
            status = worst([status, "warning"])
            b.finding(
                "provider.undeclared",
                signal,
                "warning",
                "Granted signal has no source-declared provider contract",
            )
        elif status == "failed":
            b.finding(
                f"provider.{facts['runtime']}",
                signal,
                "failed",
                "Runtime registration differs from the declared provider contract",
            )
        b.resource("provider", signal, signal, status, **facts)


def _startup(
    b: _Builder, manifest: EnvironmentManifest, o: Observation, agents: dict[Any, Any]
) -> None:
    """What the next API start or source reload would do (it never activates Agents)."""
    bootstrap = {entry.slot for entry in getattr(o.policy, "bootstrap", ()) or ()}
    armed = [
        a.name
        for a in manifest.agents
        if a.creation.via == "genesis_bootstrap"
        and a.creation.slot in bootstrap
        and a.agent_id not in agents
    ]
    if armed:
        b.finding(
            "startup.bootstrap_armed",
            "startup",
            "warning",
            "The next API start or reload will create "
            + ", ".join(armed)
            + " through JOSHUA → Genesis (trusted bootstrap)",
        )
    b.resource(
        "startup",
        "startup",
        "API start / source reload effects",
        "warning" if armed else "passed",
        effects=(
            ["installs the checked-in UTU policy when its revision changed"]
            + [f"creates {name} through Genesis" for name in armed]
            + ["verifies existing bootstrap Agents read-only", "activates no Agent"]
        ),
    )


class _Principal:
    def __init__(self, tenant_id: str, agent_id: str) -> None:
        self.tenant_id = tenant_id
        self.agent_id = agent_id
