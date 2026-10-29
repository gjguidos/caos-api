"""G1.4: real UTU resolver + CPBX + Genesis chain; isolated persistence ports."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from pymongo.errors import AutoReconnect

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.genesis.initiation import (
    INITIATION_EVENT,
    PROVIDER,
    SIGNAL,
    VERSION,
    GenesisHandoff,
    GenesisInitiation,
    register_genesis_initiation,
)
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentActivationApproval,
    AgentPrincipalResolver,
    SystemCitizen,
    VerifiedAgentSession,
)
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.models import Synapse, SynapticGrants
from src.domains.agentverse.security.revocation import policy_version
from src.domains.agentverse.security.utu import UTUAuthority
from src.domains.agentverse.tests.agents import test_genesis_provisioner as genesis

JOSHUA = "joshua"
HANDOFF = GenesisHandoff(
    tenant_id=genesis.TENANT,
    subject_id=genesis.OWNER,
    initiator_id=JOSHUA,
    executor_id=genesis.GENESIS,
    agent_id=genesis.ENKI,
    slot="enki",
)


async def environment(*, operations=None):
    s = genesis.stack(operations=operations)
    utu = s.exchange._authorization
    policy = SynapticAuthorization(
        grants={
            (genesis.TENANT, genesis.GENESIS): SynapticGrants(genesis.GRANTS),
            (genesis.TENANT, JOSHUA): SynapticGrants({SIGNAL}),
        }
    )
    session = VerifiedAgentSession(
        genesis.TENANT,
        genesis.OWNER,
        s.key.session_id,
        frozenset({JOSHUA, genesis.GENESIS}),
        True,
    )
    sessions = SimpleNamespace(
        get_session=AsyncMock(return_value=session),
        bind_revocation_handler=Mock(),
    )
    # JOSHUA and Genesis are deterministic System Citizens: no coll_agents definitions.
    definitions: dict[str, dict] = {}
    citizens = {
        (genesis.TENANT, a): SystemCitizen(genesis.TENANT, a, name)
        for a, name in ((JOSHUA, "JOSHUA"), (genesis.GENESIS, "Genesis"))
    }
    approvals = {
        (genesis.TENANT, a): AgentActivationApproval(genesis.TENANT, a, genesis.OWNER, True)
        for _, a in citizens
    }
    resolver = AgentPrincipalResolver(
        definitions=SimpleNamespace(find_by_id=AsyncMock(side_effect=definitions.get)),
        sessions=sessions,
        authorization=policy,
        approvals=approvals,
        citizens=citizens,
    )
    versions = (policy_version(policy), "incarnation", "0", "0")
    s.revocations.read.return_value = versions
    # Reuse the same authority object held by every existing tool and coordinator.
    UTUAuthority.__init__(utu, resolver=resolver, authorization=policy, revocations=s.revocations)
    key = replace(s.key, agent_id=JOSHUA)
    client = s.exchange.bind(await utu.resolve_agent_principal(key))
    adapter = GenesisInitiation(
        handoffs=(HANDOFF,),
        provisions={"enki": genesis.ENKI_SLOT},
        authority=utu,
        exchange=s.exchange,
        provisioner=s.provisioner,
        log=s.log,
    )
    register_genesis_initiation(s.exchange._registry, adapter)
    s.utu, s.resolver, s.sessions, s.definitions = utu, resolver, sessions, definitions
    s.initiator, s.initiating_key, s.adapter = client, key, adapter
    return s


async def initiate(s, **extra):
    return await s.initiator.request(SIGNAL, {"creation": genesis.creation(), **extra})


@pytest.mark.asyncio
async def test_authorized_initiation_has_distinct_verified_attribution_and_no_activation():
    s = await environment()
    response = await initiate(s)
    assert response["status"] == "provisioned" and response["agent_id"] == genesis.ENKI
    assert response["operation_id"] == response["correlation_id"]
    history = await s.log.history(genesis.TENANT, response["correlation_id"])
    assert len(history) == 4
    initiation, *execution = history
    assert initiation.event_type == INITIATION_EVENT
    assert initiation.actor.principal_id == JOSHUA
    assert initiation.actor.user_id == genesis.OWNER
    assert initiation.subject.resource_id == genesis.ENKI
    assert initiation.metadata["executor_id"] == genesis.GENESIS
    assert initiation.target.version == VERSION
    assert all(e.actor.principal_id == genesis.GENESIS for e in execution)
    assert all(e.actor.user_id == genesis.OWNER for e in execution)
    assert {str(e.operation_id) for e in history} == {response["operation_id"]}
    assert len(s.persistence.documents) == 1
    # No runtime/lifecycle/LLM is involved in either the adapter or provisioner.
    # Neither citizen resolution nor provisioning reads coll_agents through UTU.
    s.resolver._definitions.find_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_initiation_evidence_is_durable_before_the_provisioner_is_called():
    s = await environment()
    original = s.provisioner.provision

    async def provision(client, request):
        assert len(s.logs.documents) == 1
        assert next(iter(s.logs.documents.values()))["event_type"] == INITIATION_EVENT
        assert client.execution_permit.key.agent_id == genesis.GENESIS
        assert client.execution_permit != s.initiator.execution_permit
        return await original(client, request)

    s.provisioner.provision = provision
    assert (await initiate(s))["status"] == "provisioned"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("agent_id", "other"),
        ("tenant_id", "other"),
        ("user_id", "other"),
    ],
)
async def test_principal_tenant_and_user_mismatches_are_denied(field, value):
    s = await environment()
    key = replace(s.initiating_key, **{field: value})
    # Even a permit passing an external version check cannot select another handoff.
    from src.domains.agentverse.security.revocation import ExecutionPermit
    from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal

    client = s.exchange.bind(
        AuthorizedAgentPrincipal(
            key.agent_id, key.tenant_id, ExecutionPermit(key, s.initiator.execution_permit.versions)
        )
    )
    synapse = Synapse(
        SIGNAL, key.agent_id, PROVIDER, key.tenant_id, {"creation": genesis.creation()}
    )
    with pytest.raises(EventPermissionError):
        await s.adapter.handle(synapse, client)
    assert not s.logs.documents and not s.persistence.documents and not s.operations.records


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"principal_id": genesis.GENESIS},
        {"tenant_id": genesis.TENANT},
        {"subject_id": genesis.OWNER},
        {"permit": {}},
        {"authorization": {}},
        {"provenance": {}},
        {"slot": "enki"},
        {"executor_id": genesis.GENESIS},
    ],
)
async def test_forged_envelope_fields_are_rejected(extra):
    s = await environment()
    with pytest.raises(ValidationError):
        await initiate(s, **extra)
    assert not s.logs.documents and not s.operations.records


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "creator", "runtime_version", "authorization"])
async def test_nested_creation_cannot_override_trusted_identity(field):
    s = await environment()
    result = await s.initiator.request(SIGNAL, {"creation": genesis.creation(**{field: "forged"})})
    assert result["status"] == "rejected" and result["stage"] == "validation"
    assert not s.persistence.documents


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", [JOSHUA, genesis.GENESIS])
@pytest.mark.parametrize(
    "restriction", ["approval", "citizen", "membership", "revocation", "grant"]
)
async def test_current_approval_membership_grant_and_revocation_are_required(agent, restriction):
    s = await environment()
    scope = (genesis.TENANT, agent)
    if restriction == "approval":
        s.resolver._approvals[scope] = AgentActivationApproval(
            genesis.TENANT, agent, genesis.OWNER, False
        )
    elif restriction == "citizen":
        # Without its trusted citizen record there is no identity evidence; a
        # missing coll_agents definition never substitutes for it.
        s.resolver._citizens.pop(scope)
    elif restriction == "membership":
        session = s.sessions.get_session.return_value
        s.sessions.get_session.return_value = replace(
            session, allowed_agent_ids={JOSHUA, genesis.GENESIS} - {agent}
        )
    elif restriction == "revocation":
        s.revocations.revoke = AsyncMock(return_value=True)
        await s.utu.revoke(genesis.TENANT, agent_id=agent)
    else:
        grants = dict(s.utu._authorization._grants)
        grants[scope] = SynapticGrants({SIGNAL} if agent == genesis.GENESIS else set())
        s.utu.reload_policy(SynapticAuthorization(grants=grants))
    with pytest.raises(EventPermissionError):
        await initiate(s)
    assert not s.logs.documents and not s.operations.records


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", ["store.create", "store.create.provisioned", "data.validation"])
async def test_joshua_cannot_invoke_executor_capabilities(signal):
    s = await environment()
    with pytest.raises(EventPermissionError):
        await s.initiator.request(signal, {})
    assert s.utu.grants_for(await s.utu.resolve_agent_principal(s.initiating_key)).invoke == {
        SIGNAL
    }


@pytest.mark.asyncio
async def test_replay_and_concurrency_share_the_operation_and_initiation_evidence():
    s = await environment()
    responses = await asyncio.gather(*(initiate(s) for _ in range(6)))
    assert len({r["operation_id"] for r in responses}) == 1
    result = await initiate(s)
    assert result["status"] == "provisioned" and result["replayed"]
    assert len(s.operations.records) == len(s.persistence.documents) == 1
    assert len(s.logs.documents) == 4
    assert all(r["initiation_event_id"] == result["initiation_event_id"] for r in responses)


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_ack", [False, True])
async def test_initiation_audit_failure_never_starts_privileged_work_and_retries_once(lost_ack):
    s = await environment()
    if lost_ack:
        s.logs.commit_then_fail = True
    else:
        s.logs.insert_error = AutoReconnect("offline")
    first = await initiate(s)
    assert first["status"] == "audit_unavailable" and first["retryable"]
    assert not s.operations.records and not s.persistence.documents
    s.logs.commit_then_fail = False
    s.logs.insert_error = None
    again = await initiate(s)
    assert again["status"] == "provisioned" and again["operation_id"] == first["operation_id"]
    assert len(s.logs.documents) == 4


@pytest.mark.asyncio
async def test_approval_revoked_during_audit_denies_handoff():
    s = await environment()
    append = s.log.append

    async def revoke_after_append(*args, **kwargs):
        result = await append(*args, **kwargs)
        s.resolver._approvals.pop((genesis.TENANT, genesis.GENESIS))
        return result

    s.log.append = revoke_after_append
    with pytest.raises(EventPermissionError):
        await initiate(s)
    assert len(s.logs.documents) == 1 and not s.operations.records


@pytest.mark.asyncio
async def test_interrupted_attempt_is_reconciled_by_existing_provisioner():
    s = await environment()
    original = s.provisioner._operations.commit_outcome

    async def interrupted(*args, **kwargs):
        raise RuntimeError("lost process before outcome commit")

    s.provisioner._operations.commit_outcome = interrupted
    with pytest.raises(RuntimeError):
        await initiate(s)
    assert len(s.persistence.documents) == 1 and len(s.logs.documents) == 4
    s.provisioner._operations.commit_outcome = original
    # Advance beyond the managed lease; the existing reconciliation implementation owns recovery.
    from datetime import timedelta

    s.provisioner._clock.now += timedelta(minutes=3)
    result = await initiate(s)
    assert result["status"] == "provisioned"
    assert len(s.persistence.documents) == 1 and len(s.logs.documents) == 4
    operation = next(iter(s.operations.records.values()))
    assert len(operation.snapshot.attempts) == 2


def test_handoff_contract_rejects_identity_aliases_and_unknown_fields():
    for update in ({"executor_id": JOSHUA}, {"agent_id": JOSHUA}, {"permit": {}}):
        with pytest.raises(ValidationError):
            GenesisHandoff.model_validate({**HANDOFF.model_dump(), **update})


@pytest.mark.asyncio
async def test_registration_is_versioned_and_does_not_grant_execution():
    s = await environment()
    binding = s.exchange._registry.resolve(SIGNAL, VERSION)
    assert binding.provider == PROVIDER
    assert s.exchange._registry.get_info(SIGNAL).metadata["access"] == "system"


@pytest.mark.asyncio
async def test_terminal_replay_still_requires_both_current_approvals():
    s = await environment()
    assert (await initiate(s))["status"] == "provisioned"
    s.resolver._approvals.pop((genesis.TENANT, genesis.GENESIS))
    with pytest.raises(EventPermissionError):
        await initiate(s)
    assert len(s.logs.documents) == 4


@pytest.mark.asyncio
async def test_forged_client_without_a_cpbx_execution_context_is_denied():
    s = await environment()
    synapse = Synapse(SIGNAL, JOSHUA, PROVIDER, genesis.TENANT, {"creation": genesis.creation()})
    with pytest.raises(EventPermissionError):
        await s.adapter.handle(
            synapse, SimpleNamespace(execution_permit=s.initiator.execution_permit)
        )
    assert not s.logs.documents


@pytest.mark.asyncio
async def test_execution_audit_failure_returns_a_correlated_retryable_result():
    from src.domains.agentverse.audit.repository import LogUnavailable

    s = await environment()
    append = s.log.append

    async def fail_execution_audit(event, **kwargs):
        if event.event_type != INITIATION_EVENT:
            raise LogUnavailable("audit offline")
        return await append(event, **kwargs)

    s.log.append = fail_execution_audit
    failed = await initiate(s)
    assert failed["status"] == "audit_unavailable" and failed["retryable"]
    assert not s.persistence.documents and len(s.logs.documents) == 1
    s.log.append = append
    done = await initiate(s)
    assert done["status"] == "provisioned"
    assert done["operation_id"] == failed["operation_id"]
    assert done["initiation_event_id"] == failed["initiation_event_id"]


@pytest.mark.asyncio
async def test_handoff_must_match_the_policy_pinned_target():
    s = await environment()
    with pytest.raises(ValueError, match="provisioned identity"):
        GenesisInitiation(
            handoffs=(HANDOFF.model_copy(update={"agent_id": "rogue"}),),
            provisions={"enki": genesis.ENKI_SLOT},
            authority=s.utu,
            exchange=s.exchange,
            provisioner=s.provisioner,
            log=s.log,
        )


@pytest.mark.asyncio
async def test_reauthenticated_success_reuse_never_changes_identity_policy_or_facts():
    from src.domains.agentverse.agents.genesis import _same_success_after_reauthentication
    from src.domains.agentverse.audit.models import LogEvent

    s = await environment()
    response = await initiate(s)
    records = await s.log.history(genesis.TENANT, response["correlation_id"])
    record = next(r for r in records if r.event_type == "genesis.validation.completed")
    event = LogEvent(
        event_id=record.event_id,
        event_type=record.event_type,
        outcome=record.outcome,
        subject=record.subject,
        target=record.target,
        correlation={
            "correlation_id": record.correlation_id,
            "causation_id": record.causation_id,
            "operation_id": record.operation_id,
        },
        metadata=record.metadata,
    )
    fresh = record.authorization.model_copy(update={"evidence_digest": "f" * 64})
    assert _same_success_after_reauthentication(record, event, fresh)
    for field in ("tenant_id", "principal_id", "subject_id", "policy_version"):
        assert not _same_success_after_reauthentication(
            record, event, fresh.model_copy(update={field: "different"})
        )
    changed = event.model_copy(
        update={"metadata": {**event.metadata, "normalized_digest": "b" * 64}}
    )
    assert not _same_success_after_reauthentication(record, changed, fresh)


@pytest.mark.asyncio
async def test_initiation_evidence_has_a_closed_versioned_metadata_contract():
    from src.domains.agentverse.audit.models import LogEvent

    s = await environment()
    result = await initiate(s)
    record = await s.log.get(genesis.TENANT, result["initiation_event_id"])
    body = {
        "event_id": record.event_id,
        "event_type": INITIATION_EVENT,
        "outcome": "succeeded",
        "subject": record.subject,
        "target": record.target,
        "correlation": {"correlation_id": record.correlation_id},
        "metadata": record.metadata,
    }
    for update in ({"event_version": 2}, {"permit": {}}, {"authorizing_user": "forged"}):
        with pytest.raises(ValidationError):
            LogEvent.model_validate({**body, "metadata": {**record.metadata, **update}})
