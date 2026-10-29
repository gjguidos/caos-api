"""Minimal operational Enki role. Draft preparation confers no execution authority."""

from src.domains.agentverse.agents.roles.role_spec import RolePolicy, RoleSpec

role_spec = RoleSpec(
    ownership="global",
    key="enki",
    name="Enki",
    version="1.0.0",
    policy=RolePolicy(name="enki"),
    agent_group="interactive_ui",
    agent_subclass="operational",
    title="Agent creation draft preparation",
    summary="Prepare structured Agent creation drafts and converse independently.",
    description=(
        "Enki prepares drafts for human review. Drafts are neither authorized nor executed. "
        "Conversation never submits a draft, creates an Agent or grants capabilities."
    ),
    capabilities={"required_tools": {}},
    default_tools=[],
    default_config={
        "draft_contract": "caos.agent_creation_draft",
        "draft_schema_version": 1,
        "submission_enabled": False,
    },
)
