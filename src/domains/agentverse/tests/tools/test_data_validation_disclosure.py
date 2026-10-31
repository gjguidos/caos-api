"""VALIDATION-SEC-0.1: ``data.validation`` findings never reveal resources the requester
cannot read, and never distinguish hidden references from nonexistent ones.

The provider result is returned unscrubbed to its CPBX caller (including the generic
signal route on an awakened Genesis), so findings must be safe at their source. The
authoritative decision (validity, codes, paths, digests) is unchanged.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.domains.agentverse.tests.tools.test_data_validation_tool import (
    OTHER_TENANT,
    TENANT,
    creation,
    put,
    stack,  # noqa: F401
    validate,
)

HIDDEN = ("core", "core2", "unregistered", "foreign_tool", "others_private", "tenant_b_role")
GENERIC_ROLE_TOOL = "Role requires a tool that is unavailable to this requester"


def roles(s) -> None:
    regs = s.registries
    put(regs, "tool", "core2", ownership="system", exposure="internal")
    put(regs, "role", "guarded", capabilities={"required_tools": {"core": "Core"}})
    put(regs, "role", "guarded_twice", capabilities={"required_tools": {"core": "C", "core2": "C"}})
    put(regs, "role", "mixed", capabilities={"required_tools": {"core": "C", "probe": "P"}})
    put(regs, "role", "ghost", capabilities={"required_tools": {"unregistered": "U"}})
    put(regs, "role", "foreign_req", capabilities={"required_tools": {"foreign_tool": "F"}})
    put(regs, "role", "private_req", capabilities={"required_tools": {"others_private": "P"}})
    put(regs, "role", "tenant_b_role", ownership="tenant", tenant_id=OTHER_TENANT)


def issues(result: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(i["path"], i["code"], i["message"]) for i in result["issues"]]


def leaks(result: dict[str, Any]) -> list[str]:
    text = json.dumps(result["issues"])
    return [name for name in HIDDEN if name in text]


def base(**overrides: Any) -> dict[str, Any]:
    return creation(**{"tools": [], "workflows": [], "rule_pack_ids": [], **overrides})


@pytest.mark.asyncio
async def test_a_hidden_required_tool_is_never_named(stack) -> None:  # noqa: F811
    s = stack()
    roles(s)
    result = await validate(s, base(role_name="guarded"))
    assert result["valid"] is False
    assert issues(result) == [("tools", "composition.required_tool_missing", GENERIC_ROLE_TOOL)]
    assert leaks(result) == []


@pytest.mark.asyncio
async def test_several_hidden_requirements_collapse_without_revealing_a_count(stack) -> None:  # noqa: F811
    s = stack()
    roles(s)
    once = await validate(s, base(role_name="guarded"))
    twice = await validate(s, base(role_name="guarded_twice"))
    assert issues(once) == issues(twice)
    assert leaks(twice) == []


@pytest.mark.asyncio
async def test_an_authorized_required_tool_is_still_named(stack) -> None:  # noqa: F811
    s = stack()
    roles(s)
    writer = await validate(s, base(role_name="writer"))
    assert issues(writer) == [
        ("tools", "composition.required_tool_missing", "Role requires tool 'probe'")
    ]
    mixed = await validate(s, base(role_name="mixed"))
    assert issues(mixed) == [
        ("tools", "composition.required_tool_missing", GENERIC_ROLE_TOOL),
        ("tools", "composition.required_tool_missing", "Role requires tool 'probe'"),
    ]
    satisfied = await validate(s, base(role_name="mixed", tools=[{"key": "probe", "config": {}}]))
    assert issues(satisfied) == [("tools", "composition.required_tool_missing", GENERIC_ROLE_TOOL)]


@pytest.mark.asyncio
async def test_unknown_foreign_and_private_requirements_hide_the_role_like_a_missing_one(
    stack,  # noqa: F811
) -> None:
    s = stack()
    roles(s)
    missing = issues(await validate(s, base(role_name="no_such_role")))
    assert missing == [
        ("role_name", "reference.unavailable", "Unknown or unavailable role reference")
    ]
    for role in ("ghost", "foreign_req", "private_req", "tenant_b_role", "foreign_role"):
        result = await validate(s, base(role_name=role))
        assert issues(result) == missing, role
        assert leaks(result) == [], role


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, hidden, unknown",
    [
        ("personality_key", "foreign_soul", "no_such_soul"),
        ("type", "foreign_type", "no_such_type"),
    ],
)
async def test_hidden_and_unknown_direct_references_are_identical(
    stack,  # noqa: F811
    field,
    hidden,
    unknown,
) -> None:
    s = stack()
    roles(s)
    put(s.registries, "agent", "foreign_type", ownership="tenant", tenant_id=OTHER_TENANT)
    a = await validate(s, base(role_name="general", **{field: hidden}))
    b = await validate(s, base(role_name="general", **{field: unknown}))
    assert issues(a) == issues(b) and a["valid"] is b["valid"] is False
    assert hidden not in json.dumps(a["issues"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"rule_pack_ids": ["foreign_pack"]},
        {"tools": [{"key": "foreign_tool", "config": {}}]},
        {"tools": [{"key": "core", "config": {}}]},
        {"tools": [{"key": "others_private", "config": {}}]},
        {"workflows": ["foreign_flow"]},
    ],
)
async def test_hidden_list_references_match_unknown_ones(stack, overrides) -> None:  # noqa: F811
    s = stack()
    roles(s)
    field = next(iter(overrides))
    unknown = [{"key": "no_such_tool", "config": {}}] if field == "tools" else ["no_such_reference"]
    a = await validate(s, base(role_name="general", **overrides))
    b = await validate(s, base(role_name="general", **{field: unknown}))
    assert issues(a) == issues(b) and a["valid"] is b["valid"] is False
    assert leaks(a) == []


@pytest.mark.asyncio
async def test_rulepack_findings_name_only_readable_tools(stack) -> None:  # noqa: F811
    s = stack()
    roles(s)
    result = await validate(s, base(role_name="general", tools=[{"key": "audited", "config": {}}]))
    assert issues(result) == [
        (
            "rule_pack_ids",
            "composition.rulepacks_insufficient",
            "Tool 'audited' requires at least 2 rule pack(s)",
        )
    ]


@pytest.mark.asyncio
async def test_authoritative_outcomes_are_unchanged(stack) -> None:  # noqa: F811
    s = stack()
    roles(s)
    # Decision shape is unchanged: same validity, paths and codes; valid inputs keep
    # their normalized digest and resolved references.
    hidden = await validate(s, base(role_name="guarded"))
    assert (hidden["valid"], {(i["path"], i["code"]) for i in hidden["issues"]}) == (
        False,
        {("tools", "composition.required_tool_missing")},
    )
    assert hidden["normalized"] is None and hidden["resolved"] is None
    valid = await validate(s, creation())
    assert valid["valid"] is True and valid["issues"] == []
    again = await validate(s, creation())
    assert valid["normalized_digest"] == again["normalized_digest"]
    assert valid["resolved"]["role"] == {
        "key": "writer",
        "version": "1.0.0",
        "requires_tools": True,
    }
    assert TENANT  # fixture tenant is the requester's
