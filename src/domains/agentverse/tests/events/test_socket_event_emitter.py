"""Unit tests for the SocketEventEmitter structured log envelope."""

from __future__ import annotations

import asyncio

import pytest

from src.domains.agentverse.events.socket_contract import LogLevel, LogStage
from src.domains.agentverse.events.socket_event_emitter import SocketEventEmitter


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


def _emitter() -> tuple[SocketEventEmitter, _FakeWebSocket]:
    ws = _FakeWebSocket()
    return SocketEventEmitter(ws, asyncio.Lock()), ws


@pytest.mark.asyncio
async def test_log_emits_declared_envelope():
    emitter, ws = _emitter()

    await emitter.log(
        "EVA unit 'My new agent' inscribed in AEI with seal e804c5.",
        level=LogLevel.SUCCESS,
        stage=LogStage.REGISTER,
    )

    assert len(ws.sent) == 1
    msg = ws.sent[0]
    assert msg["type"] == "log"
    assert msg["level"] == "success"
    assert msg["stage"] == "register"
    assert msg["message"].startswith("EVA unit")
    assert msg["ts"].endswith("Z")


@pytest.mark.asyncio
async def test_log_stage_is_optional():
    emitter, ws = _emitter()

    await emitter.log("Spinning up...", level=LogLevel.INFO)

    assert ws.sent[0]["stage"] is None
    assert ws.sent[0]["level"] == "info"


@pytest.mark.asyncio
async def test_event_and_emit_envelopes():
    emitter, ws = _emitter()

    await emitter.event("joshu-a.create", {"response": {"status": "OK"}})
    await emitter.emit("chat", {"response": "hi"})

    assert ws.sent[0] == {"type": "event", "event": "joshu-a.create", "response": {"status": "OK"}}
    assert ws.sent[1] == {"type": "chat", "event": "chat", "response": "hi"}
