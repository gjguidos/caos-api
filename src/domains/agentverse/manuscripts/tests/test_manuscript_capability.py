"""MANUSCRIPT-0.1A provider guards for the six ``manuscript.session.*@1.0.0`` signals.

Real permits, CPBX clients and Manuscript runtime; UTU authority is a stub so each
failure mode (revoked, changed or ungranted authority) can be forced. End to end through
the reviewed local policy: test_manuscript_capability_integration.py.
"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.manuscripts.capability import (
    BINDINGS,
    OPERATIONS,
    PROVIDER,
    SCOPES,
    SIGNALS,
    VERSION,
    ManuscriptCapability,
    gateway_visibility,
    register_manuscript_capability,
)
from src.domains.agentverse.manuscripts.definitions.agent_creation_interview import (
    AGENT_CREATION_INTERVIEW,
)
from src.domains.agentverse.manuscripts.models import ManuscriptDefinition
from src.domains.agentverse.manuscripts.sessions import SESSIONS_COLLECTION
from src.domains.agentverse.manuscripts.tests.test_manuscript_runtime import (  # noqa: F401
    FULL,
    MANUSCRIPT,
    runtime,
)
from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient
from src.domains.agentverse.ncsc.models import Synapse, SynapticExecutionContext
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit

OWNER = User(id="owner", tenant_id="tenant", roles=["agentverse-user"])
ENKI = AgentInstanceKey("tenant", "owner", "enki", "session")
GATEWAYS = [SimpleNamespace(tenant_id="tenant", subject_id="owner", source_agent_id="enki")]


def permit(op, **overrides) -> ExecutionPermit:
    fields = {
        "key": ENKI,
        "versions": ("policy:9", "incarnation", "0", "0"),
        "origin": "session",
        "scope": SCOPES[op],
        "binding_chain": (BINDINGS[op],),
    }
    return ExecutionPermit(**{**fields, **overrides})


def call(op, issued, payload, **synapse):
    client = AuthorizedSynapticClient(
        exchange=None,  # type: ignore[arg-type]
        context=SynapticExecutionContext(issued.key.agent_id, issued.key.tenant_id, permit=issued),
    )
    return (
        Synapse(
            **{
                "signal": SIGNALS[op],
                "source": issued.key.agent_id,
                "target": PROVIDER,
                "tenant_id": issued.key.tenant_id,
                "payload": payload,
                "provider_version": VERSION,
                "entry_operation": SCOPES[op].operation,
                **synapse,
            }
        ),
        client,
    )


@pytest.fixture
def capability(runtime):  # noqa: F811
    s = runtime
    issued = permit("status")
    s.authority = SimpleNamespace(
        check_permit=AsyncMock(),
        resolve_agent_principal=AsyncMock(
            return_value=SimpleNamespace(permit=SimpleNamespace(versions=issued.versions))
        ),
        grants_for=Mock(return_value=SimpleNamespace(invoke=frozenset(SIGNALS.values()))),
        resource_requester=AsyncMock(return_value=OWNER),
    )
    s.capability = ManuscriptCapability(
        authority=s.authority,  # type: ignore[arg-type]
        manuscripts=s.service,
        visible=gateway_visibility(GATEWAYS),
    )
    counter = iter(range(10**6))
    s.key = lambda: f"capability-key-{next(counter):08d}"

    async def run(op, payload, issued=None, **synapse):
        return await s.capability.handler(op)(*call(op, issued or permit(op), payload, **synapse))

    s.run = run
    return s


def start(key="capability-start-000001", **extra):
    return {
        "contract": "caos.manuscript_session.start",
        "schema_version": 1,
        "manuscript": MANUSCRIPT,
        "idempotency_key": key,
        **extra,
    }


def command(op, session_id, **extra):
    return {
        "contract": f"caos.manuscript_session.{op}",
        "schema_version": 1,
        "session_id": session_id,
        **extra,
    }


async def started(s):
    result = await s.run("start", start())
    assert result["ok"], result
    return result["session"]


# --- All six signals ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_six_signals_drive_a_complete_interview_under_their_own_scopes(capability):
    s = capability
    session = await started(s)
    assert session["manuscript"] == {
        "id": "agent_creation_interview",
        "version": "1.0.0",
        "fingerprint": AGENT_CREATION_INTERVIEW.fingerprint(),
    }
    sid, revision = session["session_id"], session["revision"]
    for answers in FULL:
        result = await s.run(
            "answer",
            command(
                "answer", sid, expected_revision=revision, answers=answers, request_key=s.key()
            ),
        )
        assert result["ok"] and result["outcome"]["status"] == "accepted", result
        revision = result["session"]["revision"]
    corrected = await s.run(
        "correct",
        command(
            "correct",
            sid,
            expected_revision=revision,
            stage="composition",
            answers={"role": "writer"},
            request_key=s.key(),
        ),
    )
    assert corrected["ok"] and corrected["session"]["answers"]["role"] == "writer"
    revision = corrected["session"]["revision"]
    status = await s.run("status", command("status", sid))
    assert status["session"]["ready_to_complete"] is True
    done = await s.run(
        "complete", command("complete", sid, expected_revision=revision, request_key=s.key())
    )
    assert done["ok"] and done["session"]["status"] == "completed"
    assert done["session"]["artifact"]["creation"]["role_name"] == "writer"
    restarted = await s.run("restart", command("restart", sid, request_key=s.key()))
    assert restarted["ok"] and restarted["session"]["restarted_from"] == sid
    # Only sessions were written; nothing proposed, drafted, approved or created.
    assert set(s.store.collections) == {SESSIONS_COLLECTION}
    assert done["session"]["authorizes"] == dict.fromkeys(
        ("draft_write", "approval", "creation", "activation"), False
    )


# --- Scope, origin, version and caller ----------------------------------------------------------------


@pytest.mark.parametrize("op", OPERATIONS)
@pytest.mark.asyncio
async def test_each_signal_refuses_every_other_signals_scope(capability, op):
    s = capability
    for other in OPERATIONS:
        if other == op:
            continue
        foreign = permit(other)  # Valid for its own signal only.
        with pytest.raises(EventPermissionError):
            await s.run(op, start(), issued=foreign)
        with pytest.raises(EventPermissionError):
            await s.run(op, start(), issued=foreign, entry_operation=SCOPES[other].operation)


@pytest.mark.parametrize(
    "issued, synapse",
    [
        (permit("start", scope=None, binding_chain=()), {}),  # Generic route / unscoped.
        (permit("start", origin="bootstrap"), {}),
        (permit("start"), {"provider_version": "1.0.1"}),
        (permit("start"), {"provider_version": None}),
        (permit("start"), {"target": "system.agent_drafts"}),
        (permit("start"), {"source": "thoth"}),
        (permit("start"), {"tenant_id": "other-tenant"}),
        (permit("start"), {"signal": "manuscript.session.status"}),
    ],
)
@pytest.mark.asyncio
async def test_unscoped_bootstrap_mismatched_callers_are_denied_before_any_work(
    capability, issued, synapse
):
    s = capability
    with pytest.raises(EventPermissionError):
        await s.run("start", start(), issued=issued, **synapse)
    assert s.store.documents(SESSIONS_COLLECTION) == {}


@pytest.mark.parametrize("failure", ["revoked", "changed", "ungranted"])
@pytest.mark.asyncio
async def test_revoked_expired_changed_or_ungranted_authority_is_denied(capability, failure):
    s = capability
    if failure == "revoked":  # Also session logout and permit expiry.
        s.authority.check_permit.side_effect = EventPermissionError("revoked")
    elif failure == "changed":
        s.authority.resolve_agent_principal.return_value = SimpleNamespace(
            permit=SimpleNamespace(versions=("policy:10", "incarnation", "0", "0"))
        )
    else:
        s.authority.grants_for.return_value = SimpleNamespace(
            invoke=frozenset(SIGNALS.values()) - {SIGNALS["start"]}
        )
    with pytest.raises(EventPermissionError):
        await s.run("start", start())
    assert s.store.documents(SESSIONS_COLLECTION) == {}


@pytest.mark.parametrize(
    "payload",
    [
        start(tenant_id="other"),
        start(owner_subject_id="x"),
        start(agent_id="thoth"),
        start(grants=["store.create"]),
        start(contract="caos.manuscript_session.status"),
        start(idempotency_key="short"),
        {**start(), "schema_version": 2},
    ],
)
@pytest.mark.asyncio
async def test_commands_carry_no_identity_or_authority(capability, payload):
    s = capability
    result = await s.run("start", payload)
    assert (result["ok"], result["code"]) == (False, "manuscript.invalid_request")


# --- Session references, visibility and pinning --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_session_id_never_acts_as_authority(capability):
    s = capability
    session = await started(s)
    sid = session["session_id"]
    for key, requester in (
        (replace(ENKI, user_id="intruder"), User(id="intruder", tenant_id="tenant")),
        (replace(ENKI, agent_id="thoth"), OWNER),
    ):
        s.authority.resource_requester.return_value = requester
        result = await s.run(
            "status", command("status", sid), issued=permit("status", key=key), source=key.agent_id
        )
        assert (result["ok"], result["code"]) == (False, "session.not_found")
        result = await s.run(
            "answer",
            command("answer", sid, expected_revision=1, answers=FULL[0], request_key=s.key()),
            issued=permit("answer", key=key),
            source=key.agent_id,
        )
        assert result["code"] == "session.not_found"
    s.authority.resource_requester.return_value = OWNER
    assert (await s.run("status", command("status", sid)))["session"]["revision"] == 1


@pytest.mark.asyncio
async def test_manuscripts_are_visible_only_to_bound_source_agents(capability):
    s = capability
    session = await started(s)
    hidden = ManuscriptCapability(
        authority=s.authority,  # type: ignore[arg-type]
        manuscripts=s.service,
        visible=gateway_visibility([]),
    )
    result = await hidden.handler("start")(
        *call("start", permit("start"), start(key="capability-start-000002"))
    )
    assert (result["ok"], result["code"]) == (False, "manuscript.unavailable")
    result = await hidden.handler("status")(
        *call("status", permit("status"), command("status", session["session_id"]))
    )
    assert (result["ok"], result["code"]) == (False, "session.not_found")


@pytest.mark.asyncio
async def test_sessions_pin_the_exact_registered_version(capability):
    s = capability
    for manuscript in (
        "agent_creation_interview@1.0.1",
        "agent_creation_interview@1.0",
        "../x@1.0.0",
    ):
        result = await s.run("start", start(key="capability-start-000003", manuscript=manuscript))
        assert (result["ok"], result["code"]) == (False, "manuscript.unavailable")


# --- Repeated and concurrent commands ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_and_concurrent_commands_apply_once(capability):
    s = capability
    session = await started(s)
    sid = session["session_id"]
    assert (await s.run("start", start()))["session"]["session_id"] == sid  # Replay.
    key = s.key()
    body = command("answer", sid, expected_revision=1, answers=FULL[0], request_key=key)
    first, again = await s.run("answer", body), await s.run("answer", body)
    assert first["session"] == again["session"] and first["session"]["revision"] == 2
    results = await asyncio.gather(
        *[
            s.run(
                "answer",
                command(
                    "answer",
                    sid,
                    expected_revision=2,
                    answers={"name": f"Concurrent Name {n}"},
                    request_key=s.key(),
                ),
            )
            for n in range(6)
        ]
    )
    assert sum(r["ok"] for r in results) == 1
    assert {r.get("code") for r in results if not r["ok"]} == {"session.revision_conflict"}
    assert {r.get("current_revision") for r in results if not r["ok"]} == {3}


# --- Registration and handoffs --------------------------------------------------------------------------


def test_registration_binds_six_exact_signals_and_refuses_conflicts(capability):
    registry = SynapticRegistry()
    bindings = register_manuscript_capability(registry, capability.capability)
    assert {(b.signal, b.provider, b.version) for b in bindings} == {
        (signal, "system.manuscript", "1.0.0") for signal in SIGNALS.values()
    }
    assert len({b.handler.__qualname__ for b in bindings}) == 6
    register_manuscript_capability(registry, capability.capability)  # Reload-safe.
    with pytest.raises(ValueError):
        registry.register(
            SIGNALS["answer"],
            version=VERSION,
            component=type(bindings[0])(
                SIGNALS["answer"], "system.other", bindings[2].handler, VERSION
            ),
        )


@pytest.mark.parametrize(
    "signal", ["store.create.approved", "manuscript.session.start", "agent.draft.propose"]
)
def test_definitions_cannot_declare_arbitrary_handoffs(signal):
    data = AGENT_CREATION_INTERVIEW.model_dump(mode="json")
    data["handoff"] = {
        "signal": signal,
        "version": "2.0.0" if signal == "agent.draft.propose" else "1.0.0",
        "purpose": "x",
    }
    with pytest.raises(ValidationError):
        ManuscriptDefinition.model_validate(data)


@pytest.mark.asyncio
async def test_completion_never_invokes_the_declared_handoff(capability):
    s = capability
    # Neither the provider nor the runtime holds a CPBX exchange, client or any draft,
    # proposal, Store, Genesis or Khepri handle through which a handoff could run.
    forbidden = ("_exchange", "_client", "_drafts", "_proposals", "_store", "_genesis", "_khepri")
    assert not any(hasattr(s.capability, name) for name in forbidden)
    assert not any(hasattr(s.service, name) for name in forbidden)
    session = await started(s)
    sid, revision = session["session_id"], session["revision"]
    for answers in FULL:
        result = await s.run(
            "answer",
            command(
                "answer", sid, expected_revision=revision, answers=answers, request_key=s.key()
            ),
        )
        revision = result["session"]["revision"]
    done = await s.run(
        "complete", command("complete", sid, expected_revision=revision, request_key=s.key())
    )
    assert done["ok"] and done["session"]["artifact"]["contract"] == "caos.agent_creation_draft"
    assert set(s.store.collections) == {SESSIONS_COLLECTION}
