"""Unit tests for the terminal CreateResult envelope contract."""

from __future__ import annotations

from src.domains.agentverse.events.socket_contract import CreateResult, CreateStatus


def test_ok_envelope_shape():
    payload = CreateResult.ok(
        agent_id="agent-123",
        agent_system_name="my_new_agent",
        response="EVA unit created and deployed successfully.",
    ).to_payload()

    assert payload["status"] == "OK"
    assert payload["agent"] == {"agent_id": "agent-123", "agent_system_name": "my_new_agent"}
    assert payload["response"] == "EVA unit created and deployed successfully."


def test_error_envelope_shape():
    payload = CreateResult.error("Service temporarily unavailable.").to_payload()

    assert payload["status"] == "ERROR"
    assert payload["agent"] is None
    assert payload["response"] == "Service temporarily unavailable."


def test_success_and_failure_share_the_same_keys():
    ok = CreateResult.ok(agent_id="a", agent_system_name="b", response="c").to_payload()
    err = CreateResult.error("boom").to_payload()

    assert ok.keys() == err.keys() == {"status", "agent", "response"}
    assert CreateStatus.OK == "OK"
    assert CreateStatus.ERROR == "ERROR"
