"""Offline safety checks only: never connect to a real broker during discovery."""

import hashlib
import hmac
import json
import socket
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from src.base.events.v2.contracts import EventEnvelope
from src.base.events.v2.tests import live_rabbitmq_smoke as smoke


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Offline smoke-runner tests attempted network access")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


@pytest.fixture
def arguments(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    for name, value in {
        "RABBITMQ_HOST": "broker.invalid",
        "RABBITMQ_PORT": "5672",
        "RABBITMQ_VIRTUAL_HOST": "caos-smoke-unit",
        "RABBITMQ_USER": "synthetic-user",
        "RABBITMQ_PASSWORD": "never-print-password",
        "COGNITIVE_EVENT_SIGNING_KEY": "é" * 16,
    }.items():
        monkeypatch.setenv(name, value)
    return [
        "--expected-host",
        "broker.invalid",
        "--expected-port",
        "5672",
        "--expected-vhost",
        "caos-smoke-unit",
        "--exchange",
        "caos.smoke.unit",
        "--management-url",
        "http://broker.invalid:15672",
        "--expected-cluster-name",
        "unit-cluster",
    ]


@pytest.fixture
def target(arguments: list[str]) -> smoke.Target:
    return smoke.preflight(smoke.parser().parse_args(arguments), smoke.MessagingConfig(), b"x" * 32)


def test_default_preflight_does_not_load_root_or_connect(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, live = Mock(), Mock()
    monkeypatch.setattr(smoke, "load_root", root)
    monkeypatch.setattr(smoke, "run_supervised", live)
    assert smoke.main(arguments) == 0
    root.assert_not_called()
    live.assert_not_called()
    output = capsys.readouterr().out
    assert "no network operations performed" in output
    assert "never-print-password" not in output and "é" * 16 not in output


@pytest.mark.parametrize(
    "extra",
    [
        ["--expected-host", "other.invalid"],
        ["--expected-port", "5673"],
        ["--expected-vhost", "/"],
        ["--exchange", "caos.events.v2"],
        ["--management-url", "http://user:never-print-password@broker.invalid:15672"],
        ["--management-url", "http://other.invalid:15672"],
        ["--live"],
    ],
)
def test_unsafe_preflight_never_enters_live_mode(
    arguments: list[str],
    extra: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = Mock()
    monkeypatch.setattr(smoke, "run_supervised", live)
    assert smoke.main(arguments + extra) == 1
    live.assert_not_called()
    assert "never-print-password" not in capsys.readouterr().out


def test_dirty_source_blocks_live_before_network(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_source(expected: str) -> None:
        raise smoke.SmokeFailure("dirty source")

    live = Mock()
    monkeypatch.setattr(smoke, "verify_source", reject_source)
    monkeypatch.setattr(smoke, "run_supervised", live)
    assert smoke.main(arguments + ["--live", "--confirm-isolated-vhost"]) == 1
    live.assert_not_called()


def test_backend_credentials_are_not_printed(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(smoke, "verify_source", Mock())
    monkeypatch.setattr(
        smoke, "run_supervised", Mock(side_effect=RuntimeError("amqp://secret:password@host"))
    )
    assert smoke.main(arguments + ["--live", "--confirm-isolated-vhost"]) == 1
    output = capsys.readouterr().out
    assert "RuntimeError" in output and "amqp://" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe", [None, "visibility", "cluster", "queues", "connections", "bindings", "exchange"]
)
async def test_management_ownership_gate(target: smoke.Target, unsafe: str | None) -> None:
    exchange = f"/api/exchanges/{target.vhost}/{target.exchange}"
    responses = {
        "/api/whoami": {"tags": ["management" if unsafe == "visibility" else "monitoring"]},
        "/api/overview": {"cluster_name": "wrong" if unsafe == "cluster" else target.cluster},
        "/api/vhosts": [{"name": target.vhost}],
        f"/api/queues/{target.vhost}": [{"name": "existing"}] if unsafe == "queues" else [],
        "/api/connections": [{"vhost": target.vhost}] if unsafe == "connections" else [],
        exchange: {"type": "direct" if unsafe == "exchange" else "topic", "durable": True},
        f"{exchange}/bindings/source": [{"routing_key": "#"}] if unsafe == "bindings" else [],
    }

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses[request.url.path])

    async with httpx.AsyncClient(
        base_url=target.management_url, transport=httpx.MockTransport(respond)
    ) as http:
        management = smoke.Management(http, target)
        if unsafe:
            with pytest.raises(smoke.SmokeFailure):
                await management.verify_isolation()
        else:
            await management.verify_isolation()


@pytest.mark.asyncio
async def test_zero_depth_without_ack_statistics_is_not_success(target: smoke.Target) -> None:
    management = smoke.Management(Mock(), replace(target, timeout=0.01))
    management.get = AsyncMock(
        return_value={"consumers": 1, "messages_ready": 0, "messages_unacknowledged": 0}
    )
    with pytest.raises(smoke.SmokeFailure, match="missing ACK statistics"):
        await management.wait_queue(target.queues[0], 1, 0, 1)


def test_metadata_validation_uses_canonical_envelope_and_rejects_tampering(
    target: smoke.Target,
) -> None:
    key = b"x" * 32
    event = EventEnvelope(
        event_type=target.event_type,
        actor_id="publisher",
        tenant_id=target.run_id,
        payload={"text": "café"},
    )
    canonical = json.dumps(
        event.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    message = SimpleNamespace(
        body=event.model_dump_json().encode(),
        headers={"caos-signature": hmac.new(key, canonical, hashlib.sha256).hexdigest()},
        message_id=event.event_id,
        correlation_id=event.correlation_id,
        exchange=target.exchange,
        routing_key=target.event_type,
        content_type="application/json",
        delivery_mode=2,
    )
    assert smoke.validate_metadata(message, target, key) == event
    event.payload["text"] = "tampered"
    message.body = event.model_dump_json().encode()
    with pytest.raises(smoke.SmokeFailure, match="Signature verification failed"):
        smoke.validate_metadata(message, target, key)


@pytest.mark.asyncio
async def test_cleanup_never_deletes_an_unowned_queue(target: smoke.Target) -> None:
    run = object.__new__(smoke.LiveRun)
    run.target = replace(target, timeout=0.01)
    run.root = Mock()
    run.runtime = SimpleNamespace(stop=AsyncMock(), _registrations=[])
    run.service = SimpleNamespace(
        stop=AsyncMock(),
        repository=SimpleNamespace(purge_queue=AsyncMock(), delete_queue=AsyncMock()),
    )
    run.client = SimpleNamespace(
        connection=None, publish_channel=None, consumer_channel=None, _consumers={}
    )
    run.observer = None
    run.owned = {"shared-production-queue"}
    run.management = SimpleNamespace(get=AsyncMock(), connections=AsyncMock(return_value=[]))
    errors = await run.cleanup()
    assert errors
    run.management.get.assert_not_awaited()
    run.service.repository.purge_queue.assert_not_awaited()
    run.service.repository.delete_queue.assert_not_awaited()
    assert run.service.stop.await_count == 2
    run.root.unwire.assert_called_once()


@pytest.mark.asyncio
async def test_bounded_step_preserves_expected_authorization_rejection(
    target: smoke.Target,
) -> None:
    run = object.__new__(smoke.LiveRun)
    run.target = target
    denied = smoke.EventPermissionError("denied")
    with pytest.raises(smoke.EventPermissionError) as caught:
        await run.step("unauthorized publication", AsyncMock(side_effect=denied)())
    assert caught.value is denied


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_replace_original_failure(
    target: smoke.Target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAOS_SMOKE_MANAGEMENT_USER", "synthetic")
    monkeypatch.setenv("CAOS_SMOKE_MANAGEMENT_PASSWORD", "synthetic")
    original = smoke.SmokeFailure("original operation failed")
    run = SimpleNamespace(
        exercise=AsyncMock(side_effect=original), cleanup=AsyncMock(return_value=["test cleanup"])
    )
    monkeypatch.setattr(
        smoke, "Management", Mock(return_value=SimpleNamespace(verify_isolation=AsyncMock()))
    )
    monkeypatch.setattr(smoke, "LiveRun", Mock(return_value=run))
    with pytest.raises(smoke.SmokeFailure) as caught:
        await smoke.live(target, b"x" * 32)
    assert caught.value is original
    run.cleanup.assert_awaited_once()


def test_root_is_unwired_after_initialization_failure(
    target: smoke.Target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Mock()
    client = SimpleNamespace(url="amqp://u:p@unexpected.invalid:5672/caos-smoke-unit")
    root.messaging.rabbitmq_client.return_value = client
    root.messaging.rabbitmq_service.return_value = SimpleNamespace(
        repository=SimpleNamespace(rabbitmq_client=client)
    )
    monkeypatch.setattr(smoke, "load_root", Mock(return_value=root))
    with pytest.raises(smoke.SmokeFailure, match="Root messaging target differs"):
        smoke.LiveRun(target, Mock(), b"x" * 32)
    root.unwire.assert_called_once()


def test_hard_deadline_terminates_only_the_owned_worker(
    target: smoke.Target,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Mock()
    process.is_alive.return_value = True

    def terminate() -> None:
        process.is_alive.return_value = False

    process.terminate.side_effect = terminate
    context = Mock()
    context.Process.return_value = process
    monkeypatch.setattr(smoke.multiprocessing, "get_context", Mock(return_value=context))
    with pytest.raises(smoke.SmokeFailure, match="cleanup is unproven"):
        smoke.run_supervised(target, b"x" * 32)
    process.start.assert_called_once()
    process.terminate.assert_called_once()
    process.close.assert_called_once()
    assert context.Process.call_args.kwargs["daemon"] is True
