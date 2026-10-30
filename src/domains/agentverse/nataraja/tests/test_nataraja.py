"""Nataraja manifest, deterministic evaluation, read-only ports and safety guards."""

import json
import random
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from src.base.dependencies.di_container import Container
from src.domains.agentverse.nataraja import cli
from src.domains.agentverse.nataraja.manifest import EnvironmentManifest, load_manifest
from src.domains.agentverse.nataraja.observe import (
    AsyncReadOnlyRedis,
    MotorReadOnlyMongo,
    Observation,
    declared_services,
    observe,
    parse_policy,
    redact_target,
)
from src.domains.agentverse.nataraja.plan import evaluate
from src.domains.agentverse.nataraja.report import to_json, to_text
from src.domains.agentverse.nataraja.verify import (
    UnsafeVerificationTarget,
    require_disposable_redis,
)

ENKI = "00000000-0000-4000-8000-000000000004"
THOTH = "00000000-0000-4000-8000-000000000006"
GENESIS = "00000000-0000-4000-8000-000000000003"
OWNER = "00000000-0000-4000-8000-000000000001"
OPERATION = "831352a6-6454-4d2a-c841-4b5ee270e89c"


@pytest.fixture
def local_env(monkeypatch):
    for key, value in {
        "ENVIRONMENT": "development",
        "GENESIS_LOCAL_ENABLED": "true",
        "GENESIS_SESSIONS_ENABLED": "true",
        "GENESIS_SECURITY_POLICY_FILE": "config/genesis.local.json",
    }.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def manifest() -> EnvironmentManifest:
    return load_manifest()


def enki_document(**override: Any) -> dict[str, Any]:
    return {
        "id": ENKI,
        "name": "Enki",
        "type": "form",
        "role_name": "enki",
        "personality_key": "enki",
        "runtime_version": "v2",
        "tenant_id": "caos-local",
        "creator": OWNER,
        "ownership": "tenant",
        "gui_components": [
            {
                "type": "form",
                "key": "agent_configuration",
                "version": 1,
                "default": True,
                "config": {
                    "source": {
                        "kind": "contract",
                        "contract": "caos.agent_creation_draft",
                        "schema_version": 1,
                    }
                },
            },
            {"type": "chat", "key": "conversation", "version": 1, "default": False, "config": {}},
        ],
        "store": {
            "operation": "create",
            "origin": "bootstrap",
            "principal_id": GENESIS,
            "subject_id": OWNER,
            "scoped": True,
        },
        **override,
    }


def runtime_report(version: str, **override: Any) -> dict[str, Any]:
    from src.domains.agentverse.nataraja.plan import DECLARED_PROVIDERS

    return {
        "process": {"boot_id": "boot-1", "started_at": "2026-10-30T07:41:14Z"},
        "build": {"version": "0.0.0.dev0", "revision": "unknown", "channel": "development"},
        "policy_version": version,
        "handoffs": [
            {"slot": "enki", "agent_id": ENKI, "activation": "none"},
            {"slot": "thoth", "agent_id": THOTH, "activation": "none"},
        ],
        "registrations": [
            {"signal": signal, "provider": provider, "version": version_}
            for signal, (provider, version_) in DECLARED_PROVIDERS.items()
            if signal != "store.create"
        ],
        **override,
    }


def baseline(local_env, **override: Any) -> Observation:
    policy, error, digest = parse_policy(Container())
    assert error is None
    from src.domains.agentverse.security.revocation import policy_version

    version = policy_version(policy.authorization)
    values: dict[str, Any] = {
        "environment": {"environment": "development", "genesis_local_enabled": True},
        "targets": {"mongo": {"host": "mongo"}, "redis": {"host": "redis"}},
        "policy": policy,
        "policy_file_digest": digest,
        "policy_version": version,
        "installed_policy_version": version,
        "roles": frozenset({"enki", "thoth", "general"}),
        "souls": frozenset({"enki", "thoth", "empty_eva"}),
        "agent_types": frozenset({"chat", "form"}),
        "services": declared_services(),
        "agents": [enki_document()],
        "operations": [
            {
                "operation_id": OPERATION,
                "kind": "genesis.provision",
                "state": "succeeded",
                "attempts": 1,
            }
        ],
        "evidence": [
            {
                "event_id": "store-1",
                "event_type": "genesis.store.completed",
                "outcome": "succeeded",
                "resource_id": ENKI,
                "operation_id": OPERATION,
                "correlation_id": OPERATION,
                "causation_id": None,
                "principal_id": GENESIS,
                "origin": "bootstrap",
            }
        ],
        "collections": ["coll_agents", "coll_logs", "operations_v2"],
        "presence_count": 0,
        "runtime": runtime_report(version),
    }
    values.update(override)
    return Observation(**values)


def by(report: dict[str, Any], kind: str) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in report[kind]} if kind == "resources" else {}


def codes(report: dict[str, Any]) -> set[str]:
    return {f["code"] for f in report["findings"]}


# --- 1. Manifest --------------------------------------------------------------------------


def test_manifest_is_versioned_and_strict(manifest):
    assert (manifest.contract, manifest.revision) == ("caos.nataraja.manifest.v1", 2)
    assert {a.lifecycle.activation_operation for a in manifest.agents} == {
        "agent.lifecycle.activate"
    }
    assert manifest.digest == load_manifest().digest
    data = manifest.model_dump(mode="json")
    invalid = []
    extra = deepcopy(data)
    extra["seed"] = True
    invalid.append(extra)
    dup = deepcopy(data)
    dup["agents"][1]["agent_id"] = ENKI
    invalid.append(dup)
    two_primary = deepcopy(data)
    two_primary["agents"][0]["gui"][1]["default"] = True
    invalid.append(two_primary)
    citizen_agent = deepcopy(data)
    citizen_agent["system_citizens"][0]["principal_id"] = ENKI
    invalid.append(citizen_agent)
    bad = deepcopy(data)
    bad["agents"][1]["requirement"] = "eventually"
    invalid.append(bad)
    service = deepcopy(data)
    service["system_services"].append({"name": "Ptah", "requirement": "required"})
    invalid.append(service)
    for value in invalid:
        with pytest.raises(ValidationError):
            EnvironmentManifest.model_validate_json(json.dumps(value))


# --- 2-3. Required/deferred resources, citizens vs services ------------------------------


def test_baseline_with_deferred_thoth_is_not_a_failure(local_env, manifest):
    report = evaluate(manifest, baseline(local_env), mode="reconcile")
    resources = by(report, "resources")
    assert resources[ENKI]["status"] == "passed"
    assert resources[THOTH]["status"] == "deferred"
    # Genesis/Khepri separation: no coupling, every provider runtime-confirmed.
    assert report["status"] == "deferred"
    assert codes(report) == set()
    providers = [r for r in report["resources"] if r["kind"] == "provider"]
    assert providers and {r["runtime"] for r in providers} == {"confirmed"}
    actions = [p["action"] for p in report["reconciliation_plan"]]
    assert actions == [  # dependency order: awaken Enki, create, then activate Thoth
        "activate_enki_through_khepri",
        "request_genesis.thoth.request",
        "activate_thoth_through_khepri",
    ]
    plan = {p["action"]: p for p in report["reconciliation_plan"]}
    # Creation never activates: Thoth's activation is its own later Khepri step.
    assert plan["activate_thoth_through_khepri"]["depends_on"] == [
        f"{THOTH}:request_genesis.thoth.request"
    ]
    assert all(
        p["requires_human_approval"] and not p["destructive"] for p in report["reconciliation_plan"]
    )
    assert all(p["executed"] is False for p in report["reconciliation_plan"])


def test_missing_required_enki_fails_and_proposes_genesis_bootstrap(local_env, manifest):
    report = evaluate(manifest, baseline(local_env, agents=[], evidence=[]), mode="reconcile")
    assert report["status"] == "failed"
    (action,) = [p for p in report["reconciliation_plan"] if p["resource"] == ENKI]
    assert action["action"] == "bootstrap_through_joshua_and_genesis"
    assert "Genesis" in action["responsible"] and action["destructive"] is False


def test_citizens_come_from_policy_and_services_from_di(local_env, manifest):
    report = evaluate(manifest, baseline(local_env), mode="inspect")
    kinds = {(r["kind"], r["name"]) for r in report["resources"]}
    assert {("system_citizen", "JOSHUA"), ("system_citizen", "Genesis")} <= kinds
    for service in ("UTU", "CPBX", "Khepri", "Seshat"):
        assert ("system_service", service) in kinds
        assert ("system_citizen", service) not in kinds
    observation = baseline(local_env)
    observation.policy.citizens.pop(("caos-local", "00000000-0000-4000-8000-000000000005"))
    report = evaluate(manifest, observation, mode="inspect")
    assert report["status"] == "failed" and "citizen.unregistered" in codes(report)


# --- Drift and classification ------------------------------------------------------------


def test_drift_unmanaged_and_manual_agents_are_classified(local_env, manifest):
    drifted = baseline(
        local_env,
        agents=[
            enki_document(role_name="general"),
            {"id": "legacy", "name": "Legacy", "type": "chat", "tenant_id": "caos-local"},
            {
                "id": "manual",
                "name": "Nabu",
                "type": "chat",
                "tenant_id": "caos-local",
                "store": {"origin": "session", "principal_id": GENESIS, "scoped": True},
            },
        ],
        installed_policy_version="older",
    )
    report = evaluate(manifest, drifted, mode="reconcile")
    resources = by(report, "resources")
    assert resources[ENKI]["status"] == "failed"
    assert resources["legacy"]["status"] == "warning"
    assert resources["manual"]["status"] == "passed"
    assert {"agent.definition_drift", "agent.unmanaged", "policy.revision_drift"} <= codes(report)
    plan = {p["action"]: p for p in report["reconciliation_plan"]}
    assert plan["replace_definition_through_genesis"]["destructive"] is True
    assert plan["restart_api_with_checked_in_policy"]["requires_human_approval"] is True
    assert resources["agent.creation_draft.create"]["status"] == "blocked"


def test_corrupt_evidence_and_unfinished_operations_are_reported(local_env, manifest):
    observation = baseline(local_env, corrupt_evidence=2)
    observation.operations[0]["state"] = "running"
    report = evaluate(manifest, observation, mode="inspect")
    assert "seshat.corrupt_records" in codes(report)
    assert "agent.operation_unconfirmed" in codes(report)
    assert report["status"] == "failed"


# --- 6-7. Determinism and redaction ------------------------------------------------------


def test_reports_are_deterministic_and_order_independent(local_env, manifest):
    first = evaluate(manifest, baseline(local_env), mode="reconcile")
    shuffled = baseline(local_env)
    shuffled.agents = [
        enki_document(),
        {"id": "b", "name": "B", "tenant_id": "caos-local"},
        {"id": "a", "name": "A", "tenant_id": "caos-local"},
    ]
    random.Random(7).shuffle(shuffled.agents)
    again = baseline(local_env)
    again.agents = list(reversed(shuffled.agents))
    one, two = (evaluate(manifest, o, mode="reconcile") for o in (shuffled, again))
    assert to_json(one) == to_json(two) and one["report_digest"] == two["report_digest"]
    assert evaluate(manifest, baseline(local_env), mode="reconcile") == first
    assert "generated_at" not in first
    assert to_text(first).startswith("NATARAJA RECONCILE — caos-local: DEFERRED")


def test_targets_never_expose_credentials():
    target = redact_target("mongodb://admin:hunter2@mongo:27017/caos?authSource=admin")
    assert target == {
        "configured": True,
        "scheme": "mongodb",
        "host": "mongo",
        "port": 27017,
        "database": "caos",
    }
    assert "hunter2" not in json.dumps(redact_target("redis://:s3cret@redis:6379/0"))


# --- 4. Read-only ports ------------------------------------------------------------------


class _Only:
    """Any attribute outside ``allowed`` fails the test: proof of read-only access."""

    def __init__(self, allowed: dict[str, Any]) -> None:
        self._allowed = allowed
        self.used: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name not in self._allowed:
            raise AssertionError(f"Inspection used {name}")
        self.used.append(name)
        return self._allowed[name]


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length):
        return self.rows[:length]


@pytest.mark.asyncio
async def test_observation_uses_read_operations_only(local_env, manifest):
    rows = {
        "coll_agents": [{**enki_document(), "store": {"authorization": {"principal_id": GENESIS}}}]
    }

    def collection(name, codec_options=None):
        return _Only({"find": lambda query, projection=None: _Cursor(rows.get(name, []))})

    async def names():
        return sorted(rows)

    database = _Only({"get_collection": collection, "list_collection_names": names})

    async def hget(key, field):
        return b"uuid:installed"

    async def scan_iter(match, count):
        for value in ():
            yield value

    redis = _Only({"hget": hget, "scan_iter": scan_iter})
    observation = await observe(
        tenant_id="caos-local",
        mongo=MotorReadOnlyMongo(database),
        redis=AsyncReadOnlyRedis(redis),
        container=Container(),
        targets={},
    )
    assert set(database.used) <= {"get_collection", "list_collection_names"}
    assert set(redis.used) <= {"hget", "scan_iter"}
    assert observation.installed_policy_version == "installed"
    assert observation.agents[0]["id"] == ENKI and "prompt" not in observation.agents[0]
    assert not observation.unreachable


# --- 5. Isolation and development-only guards ---------------------------------------------


def test_verification_refuses_live_or_unspecified_redis():
    live = "redis://:pw@redis:6379/0"
    for url in (None, "", "redis://redis:6379/9", "http://other:6379/0", "redis://:x@redis:6379/0"):
        with pytest.raises(UnsafeVerificationTarget):
            require_disposable_redis(url, live)
    assert require_disposable_redis("redis://throwaway:6379/0", live) == "redis://throwaway:6379/0"


def test_cli_is_development_only_and_never_executes_reconciliation(monkeypatch, local_env):
    assert cli.main(["reconcile"]) == 2
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(SystemExit):
        cli.main(["inspect"])


def test_runtime_observations_distinguish_declared_confirmed_and_unavailable(local_env, manifest):
    unavailable = evaluate(manifest, baseline(local_env, runtime=None), mode="inspect")
    providers = {r["id"]: r for r in unavailable["resources"] if r["kind"] == "provider"}
    assert {r["runtime"] for r in providers.values()} == {"unavailable"}
    assert {r["status"] for r in providers.values()} == {"warning"}
    assert providers["store.create.approved"]["declared_version"] == "1.0.0"
    assert "runtime.unavailable" in codes(unavailable)

    observation = baseline(local_env)
    registrations = observation.runtime["registrations"]
    observation.runtime["registrations"] = [
        {**r, "version": "2.0.0"} if r["signal"] == "data.validation" else r
        for r in registrations
        if r["signal"] != "genesis.thoth.request"
    ]
    observation.runtime["policy_version"] = "stale"
    observation.runtime["handoffs"][1]["activation"] = "requester_session"
    report = evaluate(manifest, observation, mode="inspect")
    providers = {r["id"]: r for r in report["resources"] if r["kind"] == "provider"}
    assert providers["data.validation"]["runtime"] == "mismatch"
    assert providers["genesis.thoth.request"]["runtime"] == "not_registered"
    assert {"runtime.policy_drift", "runtime.activation_coupled"} <= codes(report)
    assert report["status"] == "failed"
    assert report["revisions"]["runtime_boot_id"] == "boot-1"


def test_startup_effects_report_an_armed_bootstrap_and_never_activation(local_env, manifest):
    present = evaluate(manifest, baseline(local_env), mode="inspect")
    (startup,) = [r for r in present["resources"] if r["kind"] == "startup"]
    assert startup["status"] == "passed" and "activates no Agent" in startup["effects"]
    absent = evaluate(manifest, baseline(local_env, agents=[], evidence=[]), mode="inspect")
    assert "startup.bootstrap_armed" in codes(absent)
