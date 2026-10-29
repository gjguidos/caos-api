"""Reviewed V2 cognitive compositions, not invocation permissions.

Form presentation is independent of conversation. Only the approved Enki role
currently has a conversational Form composition; UTU grants still decide execution.
Deterministic system citizens do not become Cognitive Agents through this list.
"""


def supports_v2_conversation(agent_type: str | None, role_name: str | None) -> bool:
    return agent_type == "chat" or (agent_type, role_name) == ("form", "enki")
