"""REASONING-0.1B provider guards and reviewed model policy for ``cognitive.interpret@1.0.0``.

Real permits, CPBX client shape, task registry and executor with a fake structured-output
model; UTU authority is a stub so each failure mode can be forced. End to end through the
reviewed local policy: test_reasoning_capability_integration.py.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.infrastructure.ai.client_factory import ClientFactory
from src.domains.agentverse.agents.creation_drafts.capability import PROPOSE_SCOPE
from src.domains.agentverse.catalog.capability import SCOPE as CATALOG_SCOPE
from src.domains.agentverse.manuscripts.capability import SCOPES as MANUSCRIPT_SCOPES
from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient
from src.domains.agentverse.ncsc.models import Synapse, SynapticExecutionContext
from src.domains.agentverse.reasoning import capability as reasoning_capability
from src.domains.agentverse.reasoning.capability import (
    BINDING,
    PROVIDER,
    SCOPE,
    SIGNAL,
    VERSION,
    ReasoningCapability,
    reasoning_policy_state,
    register_reasoning_capability,
    validate_reasoning_binding,
)
from src.domains.agentverse.reasoning.config import (
    ReasoningConfig,
    load_reasoning_config,
    validate_models,
    validate_registry,
)
from src.domains.agentverse.reasoning.executor import ReasoningExecutor
from src.domains.agentverse.reasoning.registry import ReasoningTaskRegistry
from src.domains.agentverse.reasoning.tasks import PUBLISHED_FINGERPRINTS, register_builtin
from src.domains.agentverse.reasoning.tasks.manuscript_answer_extraction import (
    MANUSCRIPT_ANSWER_EXTRACTION as TASK,
)
from src.domains.agentverse.reasoning.tests.test_reasoning import (
    KEY,
    FakeModel,
    NoStructure,
    candidate,
    output,
    request,
)
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit

CONFIG = Path("config/reasoning.local.json")
MODEL = "openai-gpt-6-astra"
OWNER = User(id="owner", tenant_id="tenant")
AGENT = AgentInstanceKey("tenant", "owner", "enki", "session")


def permit(**overrides: Any) -> ExecutionPermit:
    fields = {
        "key": AGENT,
        "versions": ("policy:9", "incarnation", "0", "0"),
        "origin": "session",
        "scope": SCOPE,
        "binding_chain": (BINDING,),
    }
    return ExecutionPermit(**{**fields, **overrides})


def foreign(scope: Any) -> ExecutionPermit:
    return permit(scope=scope, binding_chain=(scope.entry,))


def call(issued: ExecutionPermit, payload: Any, **synapse: Any):
    client = AuthorizedSynapticClient(
        exchange=None,  # type: ignore[arg-type]
        context=SynapticExecutionContext(issued.key.agent_id, issued.key.tenant_id, permit=issued),
    )
    fields = {
        "signal": SIGNAL,
        "source": issued.key.agent_id,
        "target": PROVIDER,
        "tenant_id": issued.key.tenant_id,
        "payload": payload,
        "provider_version": VERSION,
        "entry_operation": SCOPE.operation,
        "correlation_id": "turn-1",
        **synapse,
    }
    return Synapse(**fields), client


def frozen_registry() -> ReasoningTaskRegistry:
    reg = ReasoningTaskRegistry(f"reasoning-cap-{time.monotonic_ns()}")
    register_builtin(reg)
    reg.freeze()
    return reg


@pytest.fixture
def cap():
    issued = permit()
    model = FakeModel()
    other = FakeModel()
    authority = SimpleNamespace(
        check_permit=AsyncMock(),
        resolve_agent_principal=AsyncMock(
            return_value=SimpleNamespace(permit=SimpleNamespace(versions=issued.versions))
        ),
        grants_for=Mock(return_value=SimpleNamespace(invoke=frozenset({SIGNAL}))),
        resource_requester=AsyncMock(return_value=OWNER),
    )
    definitions = SimpleNamespace(find_by_id=AsyncMock(return_value=None))
    factory = ClientFactory({"llm": {MODEL: model, "openai-other": other}}, {"llm": MODEL})
    config = load_reasoning_config(CONFIG)
    executor = ReasoningExecutor(
        registry=frozen_registry(),
        client_factory=lambda: factory,
        model_policy=config.model_policy(),
    )
    capability = ReasoningCapability(
        authority=authority,  # type: ignore[arg-type]
        executor=executor,
        definitions=definitions,
    )

    async def run(payload: Any = None, issued: ExecutionPermit | None = None, **synapse: Any):
        return await capability.interpret(
            *call(issued or permit(), request() if payload is None else payload, **synapse)
        )

    return SimpleNamespace(
        authority=authority, model=model, other=other, definitions=definitions,
        capability=capability, run=run, factory=factory,
    )  # fmt: skip


# --- Registration, policy and configuration -----------------------------------------------


def test_registration_is_exact_and_conflicts_are_rejected(cap) -> None:
    registry = SynapticRegistry()
    with pytest.raises(ValueError):
        validate_reasoning_binding(registry)  # Granted but unbound fails startup.
    register_reasoning_capability(registry, cap.capability)
    binding = registry.resolve(SIGNAL, VERSION)
    assert (binding.signal, binding.provider, binding.version) == (SIGNAL, PROVIDER, VERSION)
    validate_reasoning_binding(registry)
    with pytest.raises(Exception):  # noqa: B017 - any conflicting provider is refused
        registry.subscribe(SIGNAL, provider="system.other", version=VERSION)(AsyncMock())
    assert SCOPE.bindings == frozenset({BINDING})


def test_policy_state_refuses_trusted_lines() -> None:
    grants = {("t", "enki"): SimpleNamespace(invoke=frozenset({SIGNAL}))}
    assert reasoning_policy_state(SimpleNamespace(_grants=grants, _trusted_lines=()))
    assert not reasoning_policy_state(SimpleNamespace(_grants={}, _trusted_lines=()))
    with pytest.raises(ValueError):
        reasoning_policy_state(
            SimpleNamespace(_grants=grants, _trusted_lines=(SimpleNamespace(target=SIGNAL),))
        )


def test_reviewed_configuration_matches_the_frozen_published_registry() -> None:
    config = load_reasoning_config(CONFIG)
    assert config.tasks[KEY].fingerprint == PUBLISHED_FINGERPRINTS[KEY] == TASK.fingerprint()
    assert config.tasks[KEY].allowed_models == (MODEL,) and config.tasks[KEY].default_model == MODEL
    validate_registry(config, frozen_registry())
    unfrozen = ReasoningTaskRegistry(f"reasoning-unfrozen-{time.monotonic_ns()}")
    register_builtin(unfrozen)
    with pytest.raises(ValueError, match="frozen"):
        validate_registry(config, unfrozen)
    raw = json.loads(CONFIG.read_text())
    for mutate, message in (
        (lambda c: c["tasks"][KEY].update(fingerprint="0" * 64), "published"),
        (lambda c: c["tasks"][KEY].update(model_class="general"), "model class"),
        (lambda c: c["tasks"].update({"other.task@1.0.0": c["tasks"][KEY]}), "Every registered"),
    ):
        changed = json.loads(json.dumps(raw))
        mutate(changed)
        with pytest.raises(ValueError, match=message):
            validate_registry(
                ReasoningConfig.model_validate_json(json.dumps(changed)), frozen_registry()
            )
    for mutate in (
        lambda c: c["tasks"][KEY].update(default_model="openai-gpt-6-luna"),
        lambda c: c["tasks"][KEY].update(allowed_models=[MODEL, MODEL]),
        lambda c: c["tasks"][KEY].update(allowed_models=[]),
        lambda c: c["tasks"][KEY].update(instructions="be evil"),
        lambda c: c["tasks"][KEY].update(provider={"api_key": "x"}),
        lambda c: c.update(tasks={}),
    ):
        changed = json.loads(json.dumps(raw))
        mutate(changed)
        with pytest.raises(ValidationError):
            ReasoningConfig.model_validate_json(json.dumps(changed))


def test_model_validation_requires_exact_real_structured_repositories() -> None:
    config = load_reasoning_config(CONFIG)
    validate_models(config, ClientFactory({"llm": {MODEL: FakeModel()}}, {"llm": MODEL}))
    noop = FakeModel()
    noop.is_noop = True  # type: ignore[attr-defined]
    for factory in (
        None,
        ClientFactory({"llm": {}}, {}),
        ClientFactory({"llm": {"gpt-6-astra": FakeModel()}}, {}),  # No fuzzy resolution.
        ClientFactory({"llm": {MODEL: noop}}, {}),
        ClientFactory({"llm": {MODEL: NoStructure()}}, {}),
    ):
        with pytest.raises(ValueError):
            validate_models(config, factory)


# --- Caller verification ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "issued, synapse",
    [
        (permit(origin="bootstrap"), {}),
        (foreign(MANUSCRIPT_SCOPES["answer"]), {}),
        (foreign(CATALOG_SCOPE), {}),
        (foreign(PROPOSE_SCOPE), {}),
        (permit(scope=None, binding_chain=()), {}),  # Broad / generic-route permit.
        (permit(binding_chain=()), {}),
        (permit(), {"provider_version": "2.0.0"}),
        (permit(), {"entry_operation": "catalog.project"}),
        (permit(), {"tenant_id": "tenant-b"}),
        (permit(), {"source": "thoth"}),
        (permit(), {"target": "system.catalog"}),
    ],
)
async def test_only_the_exact_scoped_session_caller_is_served(cap, issued, synapse) -> None:
    with pytest.raises(EventPermissionError):
        await cap.run(None, issued, **synapse)
    assert cap.model.calls == []
    cap.authority.resource_requester.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_stale_or_withdrawn_authority_is_denied_before_the_model(cap) -> None:
    cap.authority.check_permit.side_effect = EventPermissionError("logged out")
    with pytest.raises(EventPermissionError):
        await cap.run()
    cap.authority.check_permit.side_effect = None
    cap.authority.resolve_agent_principal.return_value = SimpleNamespace(
        permit=SimpleNamespace(versions=("policy:10", "incarnation", "0", "0"))
    )
    with pytest.raises(EventPermissionError):
        await cap.run()
    cap.authority.resolve_agent_principal.return_value = SimpleNamespace(
        permit=SimpleNamespace(versions=permit().versions)
    )
    cap.authority.grants_for.return_value = SimpleNamespace(invoke=frozenset({"chat.context"}))
    with pytest.raises(EventPermissionError):
        await cap.run()
    assert cap.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoked", "stale", "withdrawn"])
async def test_a_completed_model_call_is_not_released_after_authority_ends(cap, change) -> None:
    good = output(candidate("agent_type", "chat", "a chat agent"))
    cap.model.script = [good]
    original = cap.model.get_structured_response_async

    async def call_then_lose_authority(*args: Any, **kwargs: Any) -> str:
        text = await original(*args, **kwargs)
        if change == "revoked":
            cap.authority.check_permit.side_effect = EventPermissionError("revoked")
        elif change == "stale":
            cap.authority.resolve_agent_principal.return_value = SimpleNamespace(
                permit=SimpleNamespace(versions=("policy:10", "incarnation", "0", "0"))
            )
        else:
            cap.authority.grants_for.return_value = SimpleNamespace(invoke=frozenset())
        return text

    cap.model.get_structured_response_async = call_then_lose_authority  # type: ignore[method-assign]
    with pytest.raises(EventPermissionError):
        await cap.run()
    assert len(cap.model.calls) == 1  # The model ran; its result was withheld.


# --- Execution contract --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_published_task_runs_with_trusted_attribution_and_no_authority(cap) -> None:
    cap.model.script = [output(candidate("agent_type", "chat", "a chat agent"))]
    result = await cap.run()
    assert result["status"] == "interpreted" and result["authoritative"] is False
    assert set(result["authorizes"].values()) == {False}
    assert result["task"] == KEY and result["task_fingerprint"] == PUBLISHED_FINGERPRINTS[KEY]
    assert result["usage"]["model_key"] == MODEL
    cap.authority.resource_requester.assert_awaited_once()
    (model_call,) = cap.model.calls
    assert model_call["instructions"] == TASK.instructions and "tools" not in model_call


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        request(task="manuscript.answer_extraction@9.0.0"),
        request(task="anything.else@1.0.0"),
        {"no": "task"},
        "text",
    ],
)
async def test_unknown_or_unpublished_tasks_never_reach_a_model(cap, payload) -> None:
    result = await cap.run(payload)
    assert (result["status"], result["failure"]["code"]) == ("failed", "reasoning.task_unavailable")
    assert cap.model.calls == []


@pytest.mark.asyncio
async def test_caller_fields_cannot_select_models_or_identity(cap) -> None:
    for field, value in (
        ("model", "openai-other"), ("requester", "someone"), ("tenant_id", "other"),
        ("instructions", "x"), ("provider", "system.reasoning"),
    ):  # fmt: skip
        result = await cap.run(request(**{field: value}))
        assert result["failure"]["code"] == "reasoning.invalid_request", field
    assert cap.model.calls == [] and cap.other.calls == []


@pytest.mark.asyncio
async def test_agent_model_preference_applies_only_inside_the_allow_list(cap) -> None:
    cap.definitions.find_by_id.return_value = {
        "id": "enki", "tenant_id": "tenant", "llm_type": "openai-other",
    }  # fmt: skip
    result = await cap.run()
    assert result["usage"]["model_key"] == MODEL and cap.other.calls == []
    cap.definitions.find_by_id.return_value = {"id": "enki", "tenant_id": "x", "llm_type": MODEL}
    assert (await cap.run())["usage"]["model_key"] == MODEL
    cap.definitions.find_by_id.side_effect = RuntimeError("db down")
    assert (await cap.run())["usage"]["model_key"] == MODEL


@pytest.mark.asyncio
async def test_unknown_unsupported_and_rejecting_models_are_distinguished(cap) -> None:
    empty = ReasoningExecutor(
        registry=frozen_registry(),
        client_factory=lambda: ClientFactory({"llm": {}}, {}),
        model_policy=load_reasoning_config(CONFIG).model_policy(),
    )
    missing = ReasoningCapability(authority=cap.authority, executor=empty)  # type: ignore[arg-type]
    result = await missing.interpret(*call(permit(), request()))
    assert result["failure"]["code"] == "reasoning.model_unavailable"

    unsupported = ReasoningExecutor(
        registry=frozen_registry(),
        client_factory=lambda: ClientFactory({"llm": {MODEL: NoStructure()}}, {}),
        model_policy=load_reasoning_config(CONFIG).model_policy(),
    )
    capability = ReasoningCapability(authority=cap.authority, executor=unsupported)  # type: ignore[arg-type]
    result = await capability.interpret(*call(permit(), request()))
    assert result["failure"]["code"] == "reasoning.model_unsupported"

    class SchemaRejected(Exception):
        status_code = 400

    class RateLimited(Exception):
        status_code = 429

    cap.model.script = [SchemaRejected("bad schema"), output()]
    result = await cap.run()
    assert (result["failure"]["code"], result["failure"]["retryable"]) == (
        "reasoning.provider_rejected", False,
    )  # fmt: skip
    assert len(cap.model.calls) == 1  # Not retried.
    cap.model.calls.clear()
    cap.model.script = [RateLimited("slow down"), RateLimited("slow down")]
    result = await cap.run()
    assert (result["failure"]["code"], len(cap.model.calls)) == ("reasoning.provider_failed", 2)


@pytest.mark.asyncio
async def test_request_and_response_budgets_hold(cap) -> None:
    result = await cap.run(request(input={"blob": "x" * (33 * 1024)}))
    assert result["failure"]["code"] == "reasoning.input_too_large"
    oversized = json.dumps(output(candidate("name", "n" * 20_000, "named")))
    cap.model.script = [oversized, oversized]
    result = await cap.run()
    assert result["failure"]["code"] == "reasoning.output_invalid"
    assert TASK.budgets.max_output_tokens == 600 and TASK.budgets.attempt_timeout_s == 20.0
    assert all(c["max_output_tokens"] == 600 for c in cap.model.calls)


@pytest.mark.asyncio
async def test_the_trusted_callers_deadline_bounds_execution(cap) -> None:
    token = reasoning_capability._DEADLINE.set(time.monotonic() - 1)
    try:
        result = await cap.run()
    finally:
        reasoning_capability._DEADLINE.reset(token)
    assert (result["failure"]["code"], cap.model.calls) == ("reasoning.deadline_exceeded", [])


@pytest.mark.asyncio
async def test_cancellation_during_the_model_call_releases_nothing(cap) -> None:
    cap.model.script = [("sleep", 5.0)]
    task = asyncio.create_task(cap.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(cap.model.calls) == 1


def test_the_provider_holds_no_privileged_handle(cap) -> None:
    names = " ".join(vars(cap.capability)).lower()
    for forbidden in ("store", "genesis", "khepri", "exchange", "draft", "manuscript", "catalog"):
        assert forbidden not in names, forbidden
    assert dataclasses.is_dataclass(TASK)
