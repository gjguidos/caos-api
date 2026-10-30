"""``agent_creation_interview@1.0.0``: Enki's first Manuscript (published).

Guides discovery of purpose, identity, Type, Role and Soul, optional Tools, Workflows
and RulePacks, instructions and visibility, and review readiness. Every catalog answer
must be a reference the human may read; the completed artifact is a
``caos.agent_creation_draft`` creation, handed off later as a proposal through
``agent.draft.propose@1.0.0`` (declared here, never invoked by the Manuscript).
"""

from __future__ import annotations

from pathlib import Path

from src.domains.agentverse.entities.agent_request import NAME_PATTERN
from src.domains.agentverse.manuscripts.models import ManuscriptDefinition

GUIDANCE = (Path(__file__).with_suffix(".md")).read_text(encoding="utf-8")

ALWAYS = {"op": "always"}

AGENT_CREATION_INTERVIEW = ManuscriptDefinition.model_validate(
    {
        "contract": "caos.manuscript",
        "schema_version": 1,
        "id": "agent_creation_interview",
        "version": "1.0.0",
        "title": "Agent creation interview",
        "description": "Discover a complete, reviewable Agent definition with a human.",
        "artifact": {
            "contract": "caos.agent_creation_draft",
            "schema_version": 1,
            "resource_type": "agent",
        },
        # A session may start from the human's current draft content.
        "prefill": [
            "name",
            "agent_type",
            "role",
            "soul",
            "tools",
            "workflows",
            "rule_packs",
            "prompt",
            "visibility",
        ],
        "fields": [
            {
                "key": "purpose",
                "title": "Purpose",
                "type": "text",
                "min_length": 10,
                "max_length": 2000,
            },
            {
                "key": "name",
                "title": "Agent name",
                "type": "text",
                "target": "/creation/name",
                "min_length": 1,
                "max_length": 100,
                "pattern": NAME_PATTERN,
            },
            {
                "key": "agent_type",
                "title": "Agent Type",
                "type": "catalog_ref",
                "target": "/creation/type",
                "catalog": "agent",
            },
            {
                "key": "role",
                "title": "Role",
                "type": "catalog_ref",
                "target": "/creation/role_name",
                "catalog": "role",
            },
            {
                "key": "soul",
                "title": "Soul",
                "type": "catalog_ref",
                "target": "/creation/personality_key",
                "catalog": "personality",
            },
            {
                "key": "tools",
                "title": "Tools",
                "type": "catalog_ref_list",
                "target": "/creation/tools",
                "catalog": "tool",
                "max_items": 20,
                "shape": "tool_refs",
            },
            {
                "key": "workflows",
                "title": "Workflows",
                "type": "catalog_ref_list",
                "target": "/creation/workflows",
                "catalog": "workflow",
                "max_items": 20,
            },
            {
                "key": "rule_packs",
                "title": "RulePacks",
                "type": "catalog_ref_list",
                "target": "/creation/rule_pack_ids",
                "catalog": "rulepack",
                "max_items": 20,
            },
            {
                "key": "prompt",
                "title": "Instructions",
                "type": "text",
                "target": "/creation/prompt",
                "min_length": 10,
                "max_length": 20000,
            },
            {
                "key": "visibility",
                "title": "Visibility",
                "type": "enum",
                "target": "/creation/visibility",
                "options": ["private", "public"],
            },
            # Optional capabilities still need an explicit answer, never a silent skip.
            {"key": "capabilities_reviewed", "title": "Capabilities reviewed", "type": "boolean"},
            {"key": "review_ready", "title": "Ready for server review", "type": "boolean"},
        ],
        "stages": [
            {
                "key": "purpose",
                "title": "Purpose",
                "collects": ["purpose"],
                "required": ["purpose"],
                "transitions": [{"to": "identity", "when": ALWAYS}],
                "guidance": "purpose",
            },
            {
                "key": "identity",
                "title": "Identity",
                "collects": ["name"],
                "required": ["name"],
                "transitions": [{"to": "composition", "when": ALWAYS}],
                "guidance": "identity",
            },
            {
                "key": "composition",
                "title": "Type, Role and Soul",
                "collects": ["agent_type", "role", "soul"],
                "required": ["agent_type", "role", "soul"],
                "completion": {
                    "op": "all",
                    "of": [
                        {"op": "catalog_contains", "field": "agent_type"},
                        {"op": "catalog_contains", "field": "role"},
                        {"op": "catalog_contains", "field": "soul"},
                    ],
                },
                "transitions": [{"to": "capabilities", "when": ALWAYS}],
                "guidance": "composition",
            },
            {
                "key": "capabilities",
                "title": "Tools and Workflows",
                "collects": ["tools", "workflows", "rule_packs", "capabilities_reviewed"],
                "required": ["capabilities_reviewed"],
                "completion": {"op": "equals", "field": "capabilities_reviewed", "value": True},
                "transitions": [{"to": "configuration", "when": ALWAYS}],
                "guidance": "capabilities",
            },
            {
                "key": "configuration",
                "title": "Instructions and visibility",
                "collects": ["prompt", "visibility"],
                "required": ["prompt", "visibility"],
                "transitions": [{"to": "review", "when": ALWAYS}],
                "guidance": "configuration",
            },
            {
                "key": "review",
                "title": "Review readiness",
                "collects": ["review_ready"],
                "required": ["review_ready"],
                "completion": {"op": "equals", "field": "review_ready", "value": True},
                "guidance": "review",
            },
        ],
        "initial_stage": "purpose",
        "final_stage": "review",
        "completion": {
            "op": "all",
            "of": [
                {"op": "present", "field": "name"},
                {"op": "catalog_contains", "field": "agent_type"},
                {"op": "catalog_contains", "field": "role"},
                {"op": "catalog_contains", "field": "soul"},
                {"op": "present", "field": "prompt"},
                {"op": "in", "field": "visibility", "values": ["private", "public"]},
                {"op": "equals", "field": "review_ready", "value": True},
            ],
        },
        "catalog_dependencies": ["agent", "role", "personality", "tool", "workflow", "rulepack"],
        "handoff": {
            "signal": "agent.draft.propose",
            "version": "1.0.0",
            "purpose": "Submit the completed creation as a draft proposal for human acceptance",
        },
        "guidance": GUIDANCE,
    }
)
