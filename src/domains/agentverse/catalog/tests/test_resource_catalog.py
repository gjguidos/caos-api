"""CATALOG-0.1: requester-scoped discovery and resolution never become authority."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from src.base.entities.user import User
from src.domains.agentverse.catalog import (
    CatalogCandidate,
    CatalogListRequest,
    CatalogRequester,
    CatalogResolveRequest,
    ResourceCatalog,
)
from src.domains.agentverse.catalog.models import (
    CATALOG_KINDS,
    REGISTRY_KINDS,
    AttachmentDecision,
    CatalogDecisions,
    CompatibilityDecision,
)
from src.domains.agentverse.catalog.service import fallback_icon, gateway_manuscript_visibility
from src.domains.agentverse.manuscripts.catalog import GovernedManuscriptCatalog
from src.domains.agentverse.manuscripts.definitions import register_builtin
from src.domains.agentverse.manuscripts.registry import PUBLISHED_FINGERPRINTS, ManuscriptRegistry
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.tool_registry import ToolRegistry
from src.domains.agentverse.services.registry_service import RegistryService
from src.domains.agentverse.services.workflow_service import WorkflowService
from src.domains.agentverse.tests.tools.test_data_validation_tool import (
    OTHER_TENANT,
    REQUESTER,
    TENANT,
    FakeWorkflowDB,
    put,
    workflow_rows,
)
from src.domains.agentverse.tests.tools.test_data_validation_tool import (
    catalog as validator_catalog,
)
from src.domains.agentverse.tools.manuscript_tool import ManuscriptTool
from src.domains.agentverse.tools.manuscript_tool.spec import manuscript_tool

ME = User(id=REQUESTER, tenant_id=TENANT)
STRANGER = User(id="stranger", tenant_id=OTHER_TENANT)
SOURCE_AGENT = "enki-agent"
MANUSCRIPT = "agent_creation_interview@1.0.0"


def registries() -> dict[str, Registry[Any]]:
    r = validator_catalog()
    # A role whose type disagrees, and one whose requirement the requester cannot see.
    put(r, "role", "operator", agent_group="interactive_ui", agent_subclass="operational")
    put(r, "role", "guarded", capabilities={"required_tools": {"core": "Core"}})
    put(r, "agent", "foreign_type", ownership="tenant", tenant_id=OTHER_TENANT)
    put(r, "tool", "stranger_tool", ownership="tenant", tenant_id=OTHER_TENANT)
    return r


def manuscripts() -> ManuscriptRegistry:
    registry = ManuscriptRegistry("catalog-test-manuscripts")
    register_builtin(registry)
    registry.freeze()
    return registry


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(Registry, "_instances", dict(Registry._instances))
    from src.domains.agentverse.registries.registries import registries as global_registries

    regs = registries()
    for kind, registry in regs.items():  # WorkflowService reads the module catalog.
        monkeypatch.setitem(global_registries, kind, registry)
    db = FakeWorkflowDB(workflow_rows())
    binding = SimpleNamespace(tenant_id=TENANT, subject_id=REQUESTER, source_agent_id=SOURCE_AGENT)
    catalog = ResourceCatalog(
        registries=regs,
        workflow_service=WorkflowService(db),
        manuscripts=manuscripts(),
        manuscript_visible=gateway_manuscript_visibility([binding]),
    )
    return SimpleNamespace(regs=regs, db=db, catalog=catalog)


def who(user: Any = ME, agent: str | None = None) -> CatalogRequester:
    return CatalogRequester(user=user, source_agent_id=agent)


async def keys(
    env, kind: str, requester: CatalogRequester | None = None, **query: Any
) -> list[str]:
    result = await env.catalog.list(requester or who(), CatalogListRequest(kind=kind, **query))
    return [entry.ref.key for entry in result.entries]


async def resolve(env, kind: str, value: str, requester=None, **query: Any):
    return await env.catalog.resolve(
        requester or who(), CatalogResolveRequest(kind=kind, value=value, **query)
    )


# --- Consistency with the authoritative registries ----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(REGISTRY_KINDS))
async def test_listing_equals_the_existing_client_catalog_for_the_same_requester(env, kind):
    expected = sorted(RegistryService(env.regs).list_for_client(REGISTRY_KINDS[kind], ME))
    assert await keys(env, kind) == expected
    for user in (ME, STRANGER):
        for key in list(env.regs[REGISTRY_KINDS[kind]].keys()):
            visible = key in RegistryService(env.regs).list_for_client(REGISTRY_KINDS[kind], user)
            got = await resolve(env, kind, key, who(user))
            assert (got.status == "resolved") is visible, (kind, key, user.id)


@pytest.mark.asyncio
async def test_workflows_follow_the_workflow_read_policy(env):
    readable = sorted(doc.key for doc in await WorkflowService(env.db).list_workflows(ME))
    assert await keys(env, "workflow") == readable
    assert "foreign_flow" not in readable
    entry = (await resolve(env, "workflow", "Flow")).entry
    assert entry.ref.key == "flow" and entry.ref.version == "1.2.0"
    # Operative steps/commands are never part of the presentation.
    assert "command" not in entry.model_dump_json() and "@plain" not in entry.model_dump_json()


# --- Requester and tenant isolation; hidden resources -------------------------------------


@pytest.mark.asyncio
async def test_tenant_isolation_in_both_directions(env):
    mine, theirs = await keys(env, "tool"), await keys(env, "tool", who(STRANGER))
    assert {"tenant_tool", "own_private"} <= set(mine)
    assert {"foreign_tool", "stranger_tool"}.isdisjoint(mine)
    assert {"foreign_tool", "stranger_tool"} <= set(theirs)
    assert {"tenant_tool", "own_private", "others_private"}.isdisjoint(theirs)


@pytest.mark.asyncio
async def test_hidden_foreign_private_and_unknown_resources_are_indistinguishable(env):
    outcomes = [
        (await resolve(env, "tool", key)).model_dump()
        for key in ("core", "others_private", "foreign_tool", "does_not_exist")
    ]
    assert all(outcome == {**outcomes[0]} for outcome in outcomes)
    assert outcomes[0]["status"] == "unavailable" and outcomes[0]["entry"] is None
    # Even a wrong version on a hidden resource does not reveal that it exists.
    assert (await resolve(env, "tool", "core", version="0.0.1")).status == "unavailable"
    assert (await resolve(env, "role", "foreign_role")).status == "unavailable"
    assert (await resolve(env, "soul", "foreign_soul")).status == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user", [None, User(), User(id=""), {"id": REQUESTER, "tenant_id": TENANT}]
)
async def test_missing_or_request_shaped_requesters_see_nothing(env, user):
    for kind in CATALOG_KINDS:
        assert (
            await env.catalog.list(who(user, SOURCE_AGENT), CatalogListRequest(kind=kind))
        ).entries == ()
    assert (await resolve(env, "tool", "plain", who(user))).status == "unavailable"
    assert await env.catalog.resolve_ref(who(user), "tool", "plain") is None


def test_requests_carry_no_identity_tenant_grant_or_provider():
    for field in ("tenant_id", "requester", "user", "grants", "provider", "signal", "permit"):
        with pytest.raises(ValidationError):
            CatalogResolveRequest(kind="tool", value="plain", **{field: "x"})
        with pytest.raises(ValidationError):
            CatalogListRequest(kind="tool", **{field: "x"})
    with pytest.raises(ValidationError):
        CatalogListRequest(kind="synaptic")  # Not a catalog kind.
    with pytest.raises(ValidationError):
        CatalogListRequest(kind="tool", limit=1000)


# --- Exact versions -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exact_version_resolution(env):
    ok = await resolve(env, "tool", "Probe", version="1.0.0")
    assert ok.status == "resolved" and ok.entry.ref.address == "probe@1.0.0"
    assert (await resolve(env, "tool", "probe", version="9.9.9")).status == "version_unavailable"
    assert (await resolve(env, "workflow", "flow", version="1.2.0")).status == "resolved"
    assert (await resolve(env, "workflow", "flow", version="1.0.0")).status == "version_unavailable"


# --- Compatibility, attachment and the absence of execution authority ---------------------


def candidate(**fields: Any) -> CatalogCandidate:
    return CatalogCandidate(
        **{k: tuple(v) if isinstance(v, list) else v for k, v in fields.items()}
    )


@pytest.mark.asyncio
async def test_role_compatibility_with_a_candidate(env):
    chat = candidate(type="chat")
    entries = {
        e.ref.key: e.decisions.candidate_compatibility
        for e in (
            await env.catalog.list(who(), CatalogListRequest(kind="role", candidate=chat))
        ).entries
    }
    assert entries["general"].status == "compatible"
    assert entries["operator"].status == "incompatible"
    assert entries["operator"].issues[0].code == "composition.role_type_mismatch"
    assert entries["writer"].status == "requires_changes"
    assert entries["writer"].issues[0].model_dump() == {
        "code": "composition.required_tool_missing",
        "blocking": False,
        "subject": "probe",
    }
    # A requirement the requester cannot read is blocking and never named.
    assert entries["guarded"].status == "incompatible"
    assert entries["guarded"].issues[0].subject is None
    assert "core" not in json.dumps([d.model_dump() for d in entries.values()])

    with_probe = candidate(type="chat", tools=["probe"])
    writer = (await resolve(env, "role", "writer", candidate=with_probe)).entry
    assert writer.decisions.candidate_compatibility.status == "compatible"

    compatible = await keys(env, "role", compatible_only=True, candidate=chat)
    assert "operator" not in compatible and "guarded" not in compatible
    assert {"general", "writer"} <= set(compatible)


@pytest.mark.asyncio
async def test_agent_type_tool_and_workflow_compatibility(env):
    form = (
        await resolve(env, "agent_type", "form", candidate=candidate(role_name="general"))
    ).entry
    assert form.decisions.candidate_compatibility.status == "incompatible"
    audited = (await resolve(env, "tool", "audited", candidate=candidate())).entry
    assert audited.decisions.candidate_compatibility.status == "requires_changes"
    packed = candidate(rule_pack_ids=["gdpr", "green"])
    audited = (await resolve(env, "tool", "audited", candidate=packed)).entry
    assert audited.decisions.candidate_compatibility.status == "compatible"
    flow = (await resolve(env, "workflow", "audited_flow", candidate=candidate())).entry
    assert flow.decisions.candidate_compatibility.issues[0].code == (
        "composition.rulepacks_insufficient"
    )
    flow = (await resolve(env, "workflow", "audited_flow", candidate=packed)).entry
    assert flow.decisions.candidate_compatibility.status == "compatible"
    # A stale stored tool reference already fails the existing Workflow read policy.
    assert (await resolve(env, "workflow", "ghost_flow")).status == "unavailable"
    plain = (await resolve(env, "soul", "empty_eva", candidate=candidate())).entry
    assert plain.decisions.candidate_compatibility.status == "compatible"


@pytest.mark.asyncio
async def test_hidden_candidate_parts_never_yield_an_unconditional_compatible(env):
    hidden = candidate(type="foreign_type", tools=["foreign_tool", "core"])
    unknown = candidate(type="no_such_type", tools=["no_such_tool", "nor_this"])
    findings = []
    for parts in (hidden, unknown):
        general = (await resolve(env, "role", "general", candidate=parts)).entry
        decision = general.decisions.candidate_compatibility
        assert decision.status == "requires_changes" and decision.advisory
        # The type check could not run, and the findings name candidate fields only.
        assert decision.unevaluated == ("agent_type",)
        findings.append(decision.model_dump())
    # Hidden and unknown references are indistinguishable, without names or counts.
    assert findings[0] == findings[1]
    assert [i["subject"] for i in findings[0]["issues"]] == ["tools", "type"]
    text = json.dumps(findings)
    assert not any(name in text for name in ("foreign_type", "foreign_tool", "core"))
    tool = (await resolve(env, "tool", "plain", candidate=hidden)).entry
    assert tool.decisions.candidate_attachment.status == "not_attached"
    assert tool.decisions.candidate_compatibility.status == "requires_changes"


@pytest.mark.asyncio
async def test_incomplete_candidates_are_conditional_not_compatible(env):
    role = (await resolve(env, "role", "general", candidate=candidate())).entry
    assert role.decisions.candidate_compatibility.status == "conditional"
    assert role.decisions.candidate_compatibility.unevaluated == ("agent_type",)
    chat = (await resolve(env, "agent_type", "chat", candidate=candidate())).entry
    assert chat.decisions.candidate_compatibility.unevaluated == ("role",)
    complete = candidate(type="chat", role_name="general")
    assert (
        await resolve(env, "role", "general", candidate=complete)
    ).entry.decisions.candidate_compatibility.status == "compatible"
    # A requirement on an unreadable Tool is reported once, however many there are.
    put(env.regs, "tool", "core2", ownership="system", exposure="internal")
    put(env.regs, "role", "twice", capabilities={"required_tools": {"core": "C", "core2": "C"}})
    twice = (await resolve(env, "role", "twice", candidate=complete)).entry
    assert [i.code for i in twice.decisions.candidate_compatibility.issues] == [
        "composition.required_tool_unavailable"
    ]


@pytest.mark.asyncio
async def test_duplicate_candidate_references_are_reported(env):
    dup = candidate(type="chat", tools=["probe", "Probe"])
    entry = (await resolve(env, "tool", "plain", candidate=dup)).entry
    issue = entry.decisions.candidate_compatibility.issues[0]
    assert (issue.code, issue.subject) == ("candidate.reference_duplicate", "tools")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", CATALOG_KINDS)
async def test_list_and_resolve_agree_for_every_visible_resource(env, kind):
    for requester in (who(ME, SOURCE_AGENT), who(STRANGER, SOURCE_AGENT)):
        draft = candidate(type="chat", tools=["probe"])
        listed = await env.catalog.list(
            requester, CatalogListRequest(kind=kind, candidate=draft, limit=100)
        )
        for entry in listed.entries:
            got = await env.catalog.resolve(
                requester,
                CatalogResolveRequest(
                    kind=kind, value=entry.ref.key, version=entry.ref.version, candidate=draft
                ),
            )
            assert got.status == "resolved" and got.entry == entry, (kind, entry.ref.key)


@pytest.mark.asyncio
async def test_attachment_is_reported_separately_from_compatibility(env):
    draft = candidate(type="chat", role_name="general", tools=["probe"], rule_pack_ids=["gdpr"])
    result = await env.catalog.list(who(), CatalogListRequest(kind="tool", candidate=draft))
    status = {e.ref.key: e.decisions.candidate_attachment.status for e in result.entries}
    assert status["probe"] == "attached" and status["plain"] == "not_attached"
    bare = await env.catalog.list(who(), CatalogListRequest(kind="tool"))
    assert {e.decisions.candidate_attachment.status for e in bare.entries} == {"not_evaluated"}
    assert {e.decisions.candidate_compatibility.status for e in bare.entries} == {"not_evaluated"}


@pytest.mark.asyncio
async def test_discovery_never_grants_execution(env):
    for kind in CATALOG_KINDS:
        result = await env.catalog.list(
            who(ME, SOURCE_AGENT),
            CatalogListRequest(kind=kind, candidate=candidate(tools=["probe"])),
        )
        for entry in result.entries:
            decisions = entry.decisions
            assert decisions.execution_authorization == "not_evaluated"
            assert (decisions.existence, decisions.requester_visibility) == ("exists", "visible")
    for value in ("granted", "authorized", True):
        with pytest.raises(ValidationError):
            CatalogDecisions(
                candidate_compatibility=CompatibilityDecision(status="compatible"),
                candidate_attachment=AttachmentDecision(status="attached"),
                execution_authorization=value,
            )


@pytest.mark.asyncio
async def test_catalog_holds_no_privileged_handle_and_writes_nothing(env):
    before = {
        kind: copy.deepcopy([i.model_dump() for i in r.items()]) for kind, r in env.regs.items()
    }
    for kind in CATALOG_KINDS:
        await env.catalog.list(
            who(ME, SOURCE_AGENT), CatalogListRequest(kind=kind, candidate=candidate())
        )
    after = {kind: [i.model_dump() for i in r.items()] for kind, r in env.regs.items()}
    assert before == after
    assert set(env.db.reads) <= {"find_one", "find_all"}  # FakeWorkflowDB rejects writes.
    names = " ".join(vars(env.catalog)).lower()
    for forbidden in (
        "store",
        "genesis",
        "khepri",
        "exchange",
        "authority",
        "permit",
        "draft",
        "llm",
    ):
        assert forbidden not in names, forbidden


# --- Safe, stable presentation metadata ---------------------------------------------------


@pytest.fixture
def real_tool(monkeypatch):
    monkeypatch.setattr(Registry, "_instances", dict(Registry._instances))
    tools = ToolRegistry()
    tools.bind_spec(manuscript_tool)(ManuscriptTool)
    raw = tools.get_info("manuscript").metadata
    raw["private_definition"] = {"guidance": "PRIVATE_SENTINEL"}
    raw["credentials"] = "PRIVATE_SENTINEL"
    raw["config"]["secret"] = "PRIVATE_SENTINEL"
    return ResourceCatalog(
        registries={"tool": tools}, workflow_service=WorkflowService(FakeWorkflowDB([]))
    )


@pytest.mark.asyncio
async def test_tool_card_metadata_is_safe_and_deterministic(real_tool):
    first = (await resolve(SimpleNamespace(catalog=real_tool), "tool", "manuscript")).entry
    second = (await resolve(SimpleNamespace(catalog=real_tool), "tool", "manuscript")).entry
    assert first == second and first.descriptor_digest == second.descriptor_digest
    text = first.model_dump_json()
    for leaked in (
        "PRIVATE_SENTINEL",
        "manuscript.session",
        "system.manuscript",
        '"synaptic"',
        '"provider"',
        "src.domains",
        "execution_mode",
        "tenant_id",
        "creator",
        "trusted_lines",
    ):
        assert leaked not in text, leaked
    details = first.details
    assert details.kind == "tool" and details.configuration.configurable
    assert [f.key for f in details.configuration.fields] == ["default_manuscript"]
    assert set(details.configuration.config_schema["properties"]) == {"default_manuscript"}
    assert first.identity.media is None
    assert first.identity.fallback_icon == fallback_icon("tool", "manuscript", "Manuscript")
    assert first.identity.fallback_icon.text == "M" and 0 <= first.identity.fallback_icon.hue < 360


@pytest.mark.asyncio
async def test_pagination_is_deterministic(env):
    every = await keys(env, "tool", limit=100)
    page = await env.catalog.list(who(), CatalogListRequest(kind="tool", limit=2))
    assert [e.ref.key for e in page.entries] == every[:2] and page.next_after == every[1]
    rest = await keys(env, "tool", limit=100, after=page.next_after)
    assert rest == every[2:]


# --- Manuscripts --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manuscripts_follow_reviewed_gateway_visibility(env):
    assert await keys(env, "manuscript") == []  # No acting Agent: nothing visible.
    assert await keys(env, "manuscript", who(ME, "other-agent")) == []
    assert await keys(env, "manuscript", who(STRANGER, SOURCE_AGENT)) == []
    listed = await env.catalog.list(who(ME, SOURCE_AGENT), CatalogListRequest(kind="manuscript"))
    (entry,) = listed.entries
    assert entry.ref.address == MANUSCRIPT
    assert entry.ref.fingerprint == PUBLISHED_FINGERPRINTS[MANUSCRIPT]
    assert entry.details.artifact_contract == "caos.agent_creation_draft"
    assert [s.key for s in entry.details.stages][:1] == ["purpose"]
    assert "guidance" not in entry.model_dump_json()


@pytest.mark.asyncio
async def test_manuscript_resolution_is_exact_version_only(env):
    me = who(ME, SOURCE_AGENT)
    assert (await resolve(env, "manuscript", MANUSCRIPT, me)).status == "resolved"
    by_parts = await resolve(env, "manuscript", "agent_creation_interview", me, version="1.0.0")
    assert by_parts.status == "resolved"
    assert (
        await resolve(env, "manuscript", "agent_creation_interview", me)
    ).status == "unavailable"
    wrong = await resolve(env, "manuscript", "agent_creation_interview", me, version="2.0.0")
    assert wrong.status == "version_unavailable"
    hidden = await resolve(
        env, "manuscript", "agent_creation_interview", who(STRANGER, SOURCE_AGENT), version="2.0.0"
    )
    assert hidden.status == "unavailable"


def test_manuscript_definition_fingerprints_are_unchanged():
    registry = manuscripts()
    assert registry.resolve(MANUSCRIPT).fingerprint() == PUBLISHED_FINGERPRINTS[MANUSCRIPT]


@pytest.mark.asyncio
async def test_manuscript_port_delegates_to_the_governed_catalog(env):
    port = GovernedManuscriptCatalog(env.catalog)
    assert await port.resolve("agent", "Chat", ME) == "chat"
    assert await port.resolve("personality", "empty_eva", ME) == "empty_eva"
    assert await port.resolve("tool", "own_private", ME) == "own_private"
    assert await port.resolve("workflow", "flow", ME) == "flow"
    for kind, value in (
        ("role", "foreign_role"),
        ("tool", "core"),
        ("tool", "others_private"),
        ("workflow", "foreign_flow"),
        ("manuscript", MANUSCRIPT),  # Not a Manuscript field catalog kind.
        ("agent_type", "chat"),  # Port kinds are registry names, not catalog kinds.
        ("synaptic", "x"),
    ):
        assert await port.resolve(kind, value, ME) is None, (kind, value)
    assert await port.resolve("tool", "plain", {"id": REQUESTER}) is None
