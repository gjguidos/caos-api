"""Explicit live integration runner; invoking without --live performs no network I/O."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import io
import json
import logging
import multiprocessing
import os
import socket
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict

from src.base.config.cognitive_event_config import CognitiveEventConfig
from src.base.config.messaging_config import MessagingConfig
from src.base.events.v2.contracts import EventEnvelope, EventPermissionError
from src.base.events.v2.policy import EventGrants, EventPrincipal
from src.base.events.v2.registry import EventContract, Subscription

BASE_COMMIT = "5d33821e9b3d76f7bd0fa6bdcb246d0bca9c7b2c"
logger = logging.getLogger(__name__)


class SmokeFailure(RuntimeError):
    """A deliberately credential-free diagnostic safe to print."""


def require(condition: bool, diagnostic: str) -> None:
    if not condition:
        raise SmokeFailure(diagnostic)


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    vhost: str
    exchange: str
    management_url: str
    cluster: str
    timeout: float
    overall_timeout: float = 180.0
    run_id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def event_type(self) -> str:
        return f"caos.smoke.{self.run_id}.verified"

    @property
    def queues(self) -> tuple[str, str]:
        return (f"caos.smoke.{self.run_id}.runtime", f"caos.smoke.{self.run_id}.metadata")

    def queue_path(self, name: str) -> str:
        require(name in self.queues, "Refusing access to a queue not owned by this run")
        return f"/api/queues/{quote(self.vhost, safe='')}/{quote(name, safe='')}"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--live", action="store_true", help="Explicitly permit broker operations")
    result.add_argument("--confirm-isolated-vhost", action="store_true")
    result.add_argument("--expected-host", required=True)
    result.add_argument("--expected-port", required=True, type=int)
    result.add_argument("--expected-vhost", required=True)
    result.add_argument("--exchange", required=True)
    result.add_argument("--management-url", required=True)
    result.add_argument("--expected-cluster-name", required=True)
    result.add_argument("--expected-commit", default=BASE_COMMIT)
    result.add_argument("--timeout", type=float, default=20.0, help="Seconds per bounded step")
    result.add_argument(
        "--overall-timeout", type=float, default=180.0, help="Hard live worker deadline"
    )
    return result


def preflight(args: argparse.Namespace, config: MessagingConfig, key: bytes) -> Target:
    require(
        not any(char in args.expected_host for char in "/@?# "),
        "Expected host must be a hostname, not a URL",
    )
    require(
        config.rabbitmq_host == args.expected_host, "Configured AMQP host differs from approval"
    )
    require(
        config.rabbitmq_port == args.expected_port, "Configured AMQP port differs from approval"
    )
    require(
        config.rabbitmq_virtual_host == args.expected_vhost,
        "Configured vhost differs from approval",
    )
    require(
        args.expected_vhost.startswith("caos-smoke-"), "A dedicated caos-smoke-* vhost is required"
    )
    require(
        args.exchange.startswith("caos.smoke."), "A dedicated caos.smoke.* exchange is required"
    )
    require(not any(char in args.exchange for char in "#*"), "Exchange wildcards are forbidden")
    require(1 <= args.timeout <= 120, "Step timeout must be between 1 and 120 seconds")
    require(
        args.timeout <= args.overall_timeout <= 900,
        "Overall timeout must be at least the step timeout and at most 900 seconds",
    )
    require(
        isinstance(key, bytes) and len(key) >= 32, "Signing key must encode to at least 32 bytes"
    )
    url = urlsplit(args.management_url)
    require(url.scheme in {"http", "https"}, "Management URL must use HTTP or HTTPS")
    require(url.hostname == args.expected_host, "Management and approved AMQP hosts must match")
    require(
        not url.username and not url.password,
        "Use environment variables for management credentials",
    )
    require(
        url.path in {"", "/"} and not url.query and not url.fragment, "Use a management origin URL"
    )
    require(
        not args.live or args.confirm_isolated_vhost,
        "Live mode requires explicit vhost ownership confirmation",
    )
    return Target(
        args.expected_host,
        args.expected_port,
        args.expected_vhost,
        args.exchange,
        args.management_url.rstrip("/"),
        args.expected_cluster_name,
        args.timeout,
        args.overall_timeout,
    )


def verify_source(expected_commit: str) -> None:
    """Do not describe a dirty production checkout as validation of the candidate."""

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL, text=True).strip()

    require(
        git("rev-parse", "HEAD") == expected_commit,
        "HEAD differs from the approved candidate commit",
    )
    changed = git(
        "diff",
        "--name-only",
        expected_commit,
        "--",
        "src",
        "entrypoint.py",
        "pyproject.toml",
        "uv.lock",
        "Dockerfile",
        "docker-compose.yml",
        ":(exclude)src/**/tests/**",
    )
    require(
        not changed, "Production files differ from the candidate; use an isolated clean snapshot"
    )
    untracked = git("ls-files", "--others", "--exclude-standard", "--", "src").splitlines()
    require(
        not any("/tests/" not in name for name in untracked),
        "Untracked production source prevents candidate validation",
    )


class Management:
    """Read-only broker evidence; mutation is exclusively through owned CAOS AMQP APIs."""

    def __init__(self, http: httpx.AsyncClient, target: Target) -> None:
        self.http = http
        self.target = target

    async def get(self, path: str) -> Any:
        response = await self.http.get(path)
        if response.status_code == 404:
            return None
        require(
            response.status_code == 200,
            f"Management evidence unavailable (HTTP {response.status_code})",
        )
        return response.json()

    async def connections(self) -> list[dict[str, Any]]:
        result = await self.get("/api/connections")
        require(isinstance(result, list), "Management connection inventory unavailable")
        return [item for item in result if item.get("vhost") == self.target.vhost]

    async def verify_isolation(self) -> None:
        target = self.target
        identity = await self.get("/api/whoami")
        require(isinstance(identity, dict), "Management identity evidence unavailable")
        tags = identity.get("tags", [])
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.split(",")]
        require(
            isinstance(tags, list) and bool({"monitoring", "administrator"}.intersection(tags)),
            "Management user needs monitoring/administrator visibility for a complete connection inventory",
        )
        overview = await self.get("/api/overview")
        require(
            bool(overview) and overview.get("cluster_name") == target.cluster,
            "Broker cluster identity mismatch",
        )
        vhost = quote(target.vhost, safe="")
        vhosts = await self.get("/api/vhosts")
        require(
            isinstance(vhosts, list)
            and any(isinstance(info, dict) and info.get("name") == target.vhost for info in vhosts),
            "Approved vhost does not exist or is not visible",
        )
        require(
            await self.get(f"/api/queues/{vhost}") == [],
            "Vhost is not empty; refusing live operations",
        )
        require(
            await self.connections() == [], "Vhost has active connections; refusing live operations"
        )
        exchange_path = f"/api/exchanges/{vhost}/{quote(target.exchange, safe='')}"
        exchange = await self.get(exchange_path)
        require(
            bool(exchange)
            and exchange.get("type") == "topic"
            and exchange.get("durable") is True
            and not exchange.get("auto_delete")
            and not exchange.get("internal")
            and not exchange.get("arguments"),
            "Provision the dedicated exchange as durable topic, without extra arguments",
        )
        require(
            await self.get(f"{exchange_path}/bindings/source") == [],
            "Exchange has existing bindings; refusing use",
        )

    async def wait_queue(
        self, name: str, consumers: int, ready: int, acknowledgements: int
    ) -> None:
        async def settled() -> bool:
            info = await self.get(self.target.queue_path(name))
            return bool(info) and (
                info.get("consumers") == consumers
                and info.get("messages_ready") == ready
                and info.get("messages_unacknowledged") == 0
                and info.get("message_stats", {}).get("ack", -1) >= acknowledgements
            )

        await eventually(
            f"queue {name}: consumers={consumers}, ready={ready}, unacknowledged=0, ACKs>={acknowledgements}; "
            "missing ACK statistics are inconclusive, not success",
            settled,
            self.target.timeout,
        )


async def eventually(label: str, predicate: Callable[[], Awaitable[bool]], timeout: float) -> None:
    try:
        async with asyncio.timeout(timeout):
            while not await predicate():
                await asyncio.sleep(0.2)
    except TimeoutError:
        raise SmokeFailure(f"Timed out verifying {label}") from None


def validate_metadata(message: Any, target: Target, key: bytes) -> EventEnvelope:
    event = EventEnvelope.model_validate_json(message.body)
    canonical = json.dumps(
        event.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    signature = (message.headers or {}).get("caos-signature")
    require(isinstance(signature, str), "Signature header is missing or not a string")
    require(
        hmac.compare_digest(signature, hmac.new(key, canonical, hashlib.sha256).hexdigest()),
        "Signature verification failed",
    )
    require(message.message_id == event.event_id, "AMQP message identity differs from envelope")
    require(
        message.correlation_id == event.correlation_id,
        "AMQP correlation identity differs from envelope",
    )
    require(
        message.exchange == target.exchange and message.routing_key == target.event_type,
        "Unexpected AMQP route",
    )
    require(
        message.content_type == "application/json" and message.delivery_mode == 2,
        "Expected persistent JSON delivery",
    )
    return event


def load_root() -> Any:
    """Import the untouched production root, forbidding import-time network traffic."""

    def forbidden(*args: object, **kwargs: object) -> None:
        raise SmokeFailure("Root import attempted network I/O; no broker test was started")

    with ExitStack() as stack:
        for owner, name in (
            (socket, "create_connection"),
            (socket, "getaddrinfo"),
            (socket.socket, "connect"),
            (socket.socket, "connect_ex"),
        ):
            stack.enter_context(patch.object(owner, name, forbidden))
        stack.enter_context(redirect_stdout(io.StringIO()))
        stack.enter_context(redirect_stderr(io.StringIO()))
        from src.base.dependencies.di_container import Container

        return Container()


class SmokePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class LiveRun:
    def __init__(self, target: Target, management: Management, key: bytes) -> None:
        self.target, self.management, self.key = target, management, key
        self.root = load_root()
        try:
            self.initialize()
        except BaseException:
            try:
                self.root.unwire()
            except BaseException:
                logger.exception("Unwiring after initialization failure failed", exc_info=False)
            raise

    def initialize(self) -> None:
        target, key = self.target, self.key
        self.client = self.root.messaging.rabbitmq_client()
        self.service = self.root.messaging.rabbitmq_service()
        require(
            self.service.repository.rabbitmq_client is self.client,
            "DI messaging client identity mismatch",
        )
        parsed = urlsplit(self.client.url)
        require(
            (parsed.hostname, parsed.port, unquote(parsed.path[1:]))
            == (target.host, target.port, target.vhost),
            "Root messaging target differs from approved preflight configuration",
        )
        self.publisher, self.subscriber = (
            f"publisher-{target.run_id}",
            f"subscriber-{target.run_id}",
        )
        self.root.cognitive_event.event_policy.add_kwargs(
            grants={
                self.publisher: EventGrants(publish=frozenset({target.event_type})),
                self.subscriber: EventGrants(subscribe=frozenset({target.event_type})),
            }
        )
        # Existing settings exchange fields are not wired. Configure the real provider
        # explicitly so production's shared caos.events.v2 is never declared here.
        self.root.cognitive_event.event_runtime.add_kwargs(exchange=target.exchange)
        self.policy = self.root.cognitive_event.event_policy()
        self.registry = self.root.cognitive_event.event_registry()
        self.registry.register_contract(EventContract(target.event_type, SmokePayload))
        self.registry.register_subscription(
            Subscription(self.subscriber, target.event_type, self.handle, target.queues[0])
        )
        self.runtime = self.root.cognitive_event.event_runtime()
        require(
            self.runtime._registry is self.registry
            and self.runtime._policy is self.policy
            and self.runtime._messaging is self.service,
            "Resolved runtime does not share the root registry, policy and messaging providers",
        )
        require(
            isinstance(self.runtime._key, bytes) and self.runtime._key == key,
            "Root signing key wiring differs from configuration",
        )
        self.received: list[EventEnvelope] = []
        self.observed: list[EventEnvelope] = []
        self.callback_failures: list[str] = []
        self.observer: str | None = None
        self.owned: set[str] = set()

    async def step(self, label: str, action: Awaitable[Any]) -> Any:
        try:
            async with asyncio.timeout(self.target.timeout):
                return await action
        except TimeoutError:
            raise SmokeFailure(f"Timed out during {label}") from None
        except Exception as error:
            if isinstance(error, (SmokeFailure, EventPermissionError)):
                raise
            raise SmokeFailure(
                f"{label} failed ({type(error).__name__}; backend details suppressed)"
            ) from None

    async def handle(self, event: EventEnvelope) -> None:
        self.received.append(event)

    async def observe(self, message: Any) -> None:
        try:
            self.observed.append(validate_metadata(message, self.target, self.key))
        except Exception:
            self.callback_failures.append("Raw metadata validation failed")
            raise
        # Do not ACK here. RabbitMQClient owns the message.process() context.

    async def exercise(self) -> None:
        target = self.target
        await self.step("isolated AMQP connection", self.client.connect())
        self.owned.update(target.queues)
        await self.step("runtime subscription", self.runtime.start())
        await self.step(
            "metadata route",
            self.service.setup_event_queue(target.exchange, target.queues[1], target.event_type),
        )
        self.observer = await self.step(
            "metadata subscription", self.service.subscribe(target.queues[1], self.observe)
        )
        authorized = self.runtime.bind(EventPrincipal(self.publisher, target.run_id))

        async def publish(sequence: int) -> EventEnvelope:
            return await self.step(
                "authorized publication",
                authorized.emit(
                    target.event_type,
                    {"text": f"café-{sequence}"},
                    correlation_id=f"{target.run_id}-{sequence}",
                    causation_id=target.run_id,
                ),
            )

        first = await publish(1)

        async def first_delivered() -> bool:
            require(not self.callback_failures, "Metadata callback failed")
            return len(self.received) == 1 and len(self.observed) == 1

        await eventually("signed first delivery", first_delivered, target.timeout)
        require(
            self.received == self.observed == [first],
            "Received envelopes differ from authorized publication",
        )
        require(
            first.actor_id == self.publisher and first.tenant_id == target.run_id,
            "Principal identity was not preserved",
        )
        require(first.causation_id == target.run_id, "Causation identity was not preserved")
        await self.management.wait_queue(target.queues[0], 1, 0, 1)
        await self.management.wait_queue(target.queues[1], 1, 0, 1)
        await self.step("targeted cancellation", self.service.stop_consuming(self.observer))
        await self.step(
            "repeated targeted cancellation", self.service.stop_consuming(self.observer)
        )
        await self.management.wait_queue(target.queues[1], 0, 0, 1)
        await self.management.wait_queue(target.queues[0], 1, 0, 1)
        second = await publish(2)

        async def second_delivered() -> bool:
            require(not self.callback_failures, "Metadata callback failed")
            return len(self.received) == 2

        await eventually("delivery after targeted cancellation", second_delivered, target.timeout)
        require(
            self.received == [first, second] and self.observed == [first],
            "Cancellation disrupted the wrong consumer",
        )
        await self.management.wait_queue(target.queues[0], 1, 0, 2)
        await self.management.wait_queue(target.queues[1], 0, 1, 1)
        denied = self.runtime.bind(EventPrincipal(f"denied-{target.run_id}", target.run_id))
        try:
            await self.step(
                "unauthorized publication", denied.emit(target.event_type, {"text": "denied"})
            )
        except EventPermissionError:
            pass
        else:
            raise SmokeFailure("Unauthorized publication was not rejected")
        await self.management.wait_queue(target.queues[0], 1, 0, 2)
        await self.management.wait_queue(target.queues[1], 0, 1, 1)
        require(
            self.received == [first, second] and self.observed == [first],
            "Unauthorized publication produced a delivery",
        )

    async def cleanup(self) -> list[str]:
        errors: list[str] = []

        async def attempt(label: str, action: Awaitable[Any]) -> None:
            try:
                await self.step(label, action)
            except BaseException:
                errors.append(label)
                logger.exception("Cleanup failed: %s", label, exc_info=False)

        await attempt("runtime stop", self.runtime.stop())
        await attempt("repeated runtime stop", self.runtime.stop())
        if self.observer is not None:
            await attempt("observer stop", self.service.stop_consuming(self.observer))
            await attempt("repeated observer stop", self.service.stop_consuming(self.observer))
        for name in sorted(self.owned):

            async def remove(queue: str = name) -> None:
                info = await self.management.get(self.target.queue_path(queue))
                if info is None:
                    return

                async def idle() -> bool:
                    state = await self.management.get(self.target.queue_path(queue))
                    return (
                        bool(state)
                        and state.get("consumers") == 0
                        and state.get("messages_unacknowledged") == 0
                    )

                await eventually(
                    f"owned queue {queue} has no consumers or in-flight deliveries",
                    idle,
                    self.target.timeout,
                )
                # Only this run's exact UUID queues; never delete/purge an exchange.
                await self.service.repository.purge_queue(queue)
                await self.service.repository.delete_queue(queue)

            await attempt(f"remove owned queue {name}", remove())
        handles = (
            self.client.connection,
            self.client.publish_channel,
            self.client.consumer_channel,
        )
        await attempt("messaging stop", self.service.stop())
        await attempt("repeated messaging stop", self.service.stop())
        if self.client._consumers or self.runtime._registrations:
            errors.append("consumer registrations remain")
        if any(
            handle is not None
            for handle in (
                self.client.connection,
                self.client.publish_channel,
                self.client.consumer_channel,
            )
        ):
            errors.append("connection/channel handles remain")
        if any(handle is not None and not handle.is_closed for handle in handles):
            errors.append("retained connection/channel is not closed")

        async def gone() -> bool:
            queues_gone = all(
                [
                    await self.management.get(self.target.queue_path(name)) is None
                    for name in self.owned
                ]
            )
            return queues_gone and await self.management.connections() == []

        await attempt(
            "broker cleanup evidence",
            eventually("owned queues and connection gone", gone, self.target.timeout),
        )
        try:
            self.root.unwire()
        except Exception:
            errors.append("root unwiring failed")
            logger.exception("Root unwiring failed", exc_info=False)
        return errors


async def live(target: Target, key: bytes) -> None:
    user = os.environ.get("CAOS_SMOKE_MANAGEMENT_USER")
    password = os.environ.get("CAOS_SMOKE_MANAGEMENT_PASSWORD")
    require(
        bool(user) and bool(password),
        "Management credentials must be supplied through CAOS_SMOKE_MANAGEMENT_* environment variables",
    )
    async with httpx.AsyncClient(
        base_url=target.management_url,
        auth=httpx.BasicAuth(user, password),
        timeout=min(target.timeout, 5),
        follow_redirects=False,
        trust_env=False,
    ) as http:
        management = Management(http, target)
        await management.verify_isolation()
        run = LiveRun(target, management, key)
        failed = False
        try:
            await run.exercise()
        except BaseException:
            failed = True
            raise
        finally:
            errors = await run.cleanup()
            if errors:
                print("Cleanup failures:", "; ".join(errors))
                if not failed:
                    raise SmokeFailure("Live cleanup did not pass")
        print(
            f"PASS: authorized delivery, signatures, broker ACKs, cancellation and cleanup ({target.run_id})"
        )


def worker(target: Target, key: bytes) -> None:
    """Only the spawned child can construct DI or contact the approved broker."""
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(live(target, key))
    except BaseException as error:
        print(
            f"FAIL: {error}"
            if isinstance(error, SmokeFailure)
            else f"FAIL: {type(error).__name__} (details suppressed to protect credentials)",
            flush=True,
        )
        logger.exception("Live worker failed (%s)", type(error).__name__, exc_info=False)
        raise SystemExit(1) from None


def run_supervised(target: Target, key: bytes) -> None:
    """Bound even cancellation-suppressing library rollback; never claim forced cleanup."""
    process = multiprocessing.get_context("spawn").Process(
        target=worker, args=(target, key), daemon=True
    )

    def terminate() -> None:
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)

    process.start()
    try:
        process.join(target.overall_timeout)
        if process.is_alive():
            terminate()
            raise SmokeFailure(
                "Hard worker timeout; cleanup is unproven. Verify the printed UUID-owned queues"
            )
        require(
            process.exitcode == 0,
            "Live worker failed; inspect its sanitized diagnostic and verify owned resources",
        )
    finally:
        if process.is_alive():
            terminate()
        if not process.is_alive():
            process.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    # Do not emit backend exceptions/URLs containing secrets, including library logs.
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        config = MessagingConfig()
        key = CognitiveEventConfig().signing_key.get_secret_value().encode("utf-8")
        target = preflight(args, config, key)
        print(
            json.dumps(
                {
                    "host": target.host,
                    "port": target.port,
                    "vhost": target.vhost,
                    "exchange": target.exchange,
                    "run_id": target.run_id,
                    "owned_queues": target.queues,
                    "live": args.live,
                }
            ),
            flush=True,
        )
        if not args.live:
            print(
                "Configuration preflight only: broker ownership has NOT been verified; no network operations performed"
            )
            return 0
        verify_source(args.expected_commit)
        run_supervised(target, key)
        return 0
    except Exception as error:
        print(
            f"FAIL: {error}"
            if isinstance(error, SmokeFailure)
            else f"FAIL: {type(error).__name__} (details suppressed to protect credentials)"
        )
        logger.exception("Smoke operation failed (%s)", type(error).__name__, exc_info=False)
        return 1
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())
