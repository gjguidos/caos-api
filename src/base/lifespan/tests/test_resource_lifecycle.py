import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

CLEANUP_ORDER = [
    "supervisor.close",
    "runtime.stop",
    "rabbitmq.stop",
    "executors.close",
    "mongo.disconnect",
    "redis.exit",
    "splunk.stop",
]


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Load the actual lifespan with infrastructure/settings imports isolated."""
    trace: list[str] = []
    failures: dict[str, BaseException] = {}

    def operation(name: str) -> AsyncMock:
        async def run(*args: object, **kwargs: object) -> None:
            trace.append(name)
            if name in failures:
                raise failures[name]

        return AsyncMock(side_effect=run)

    def stub(name: str, **attrs: object) -> None:
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)

    settings = SimpleNamespace(sacred_keys=SimpleNamespace(JOSHU_A_PUBLIC_KEY="test-key"))
    splunk = SimpleNamespace(start=operation("splunk.start"), stop=operation("splunk.stop"))
    stub("src.base.config.config", settings=settings)
    stub("src.base.utils.env_utils", is_local_development=lambda: True)
    stub("src.base.logging.logging_config", LoggingConfig=SimpleNamespace(splunk_handler=splunk))
    stub(
        "src.base.integrations.wiring",
        inject_noops=Mock(),
        compute_defaults=Mock(return_value={"llm": "default"}),
        build_client_factory=Mock(),
    )
    path = Path(__file__).parents[1] / "lifespan.py"
    spec = importlib.util.spec_from_file_location("_test_caos_lifespan", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    executors = SimpleNamespace(
        count=Mock(return_value=0),
        warmup_all=operation("executors.warmup"),
        aclose_all=operation("executors.close"),
    )
    monkeypatch.setattr(module, "cpu_executor_registry", executors)
    monkeypatch.setattr(module.FastAPILimiter, "init", operation("limiter.init"))
    monkeypatch.setattr(module, "is_local_development", lambda: True)
    redis = SimpleNamespace(ping=operation("redis.ping"))
    redis_context = AsyncMock()

    async def enter(*args: object) -> SimpleNamespace:
        trace.append("redis.enter")
        if "redis.enter" in failures:
            raise failures["redis.enter"]
        return redis

    redis_context.__aenter__.side_effect = enter
    redis_context.__aexit__.side_effect = operation("redis.exit")
    rabbitmq = SimpleNamespace(
        connect=operation("rabbitmq.connect"), stop=operation("rabbitmq.stop")
    )
    runtime = SimpleNamespace(start=operation("runtime.start"), stop=operation("runtime.stop"))
    supervisor = SimpleNamespace(
        start=operation("supervisor.start"), close=operation("supervisor.close")
    )
    mongo = SimpleNamespace(
        connect=operation("mongo.connect"), disconnect=operation("mongo.disconnect")
    )
    container = Mock()
    container.redis.redis_client.return_value = redis_context
    container.messaging.rabbitmq_client.return_value = rabbitmq

    def construct_runtime() -> SimpleNamespace:
        trace.append("runtime.construct")
        if "runtime.construct" in failures:
            raise failures["runtime.construct"]
        return runtime

    container.cognitive_event.event_runtime.side_effect = construct_runtime
    container.operations.job_supervisor.return_value = supervisor
    container.database.mongo_client.return_value = mongo
    container.llm_container.repositories.return_value = {"llm": {"default": object()}}
    container.agentverse.mcp_tool_source = None
    app = SimpleNamespace(container=container, state=SimpleNamespace())
    return SimpleNamespace(module=module, app=app, trace=trace, failures=failures)


@pytest.mark.asyncio
async def test_startup_order_and_shutdown_dependencies(lifecycle: SimpleNamespace) -> None:
    async with lifecycle.module.lifespan(lifecycle.app):
        assert lifecycle.trace == [
            "splunk.start",
            "executors.warmup",
            "redis.enter",
            "rabbitmq.connect",
            "runtime.construct",
            "supervisor.start",
            "limiter.init",
            "redis.ping",
            "mongo.connect",
            "runtime.start",
        ]
    assert lifecycle.trace[-7:] == CLEANUP_ORDER


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize(
    "stage,expected",
    [
        ("splunk.start", ["splunk.stop"]),
        ("executors.warmup", ["executors.close", "splunk.stop"]),
        ("redis.enter", ["executors.close", "splunk.stop"]),
        ("rabbitmq.connect", ["rabbitmq.stop", "executors.close", "redis.exit", "splunk.stop"]),
        ("runtime.construct", ["rabbitmq.stop", "executors.close", "redis.exit", "splunk.stop"]),
        (
            "supervisor.start",
            [
                "supervisor.close",
                "runtime.stop",
                "rabbitmq.stop",
                "executors.close",
                "redis.exit",
                "splunk.stop",
            ],
        ),
        (
            "limiter.init",
            [
                "supervisor.close",
                "runtime.stop",
                "rabbitmq.stop",
                "executors.close",
                "redis.exit",
                "splunk.stop",
            ],
        ),
        ("redis.ping", CLEANUP_ORDER),
        ("mongo.connect", CLEANUP_ORDER),
        ("runtime.start", CLEANUP_ORDER),
    ],
)
async def test_startup_failure_cleans_acquired_resources(
    lifecycle: SimpleNamespace, stage: str, expected: list[str], cancelled: bool
) -> None:
    failure = asyncio.CancelledError("cancelled") if cancelled else RuntimeError("startup failed")
    lifecycle.failures[stage] = failure
    with pytest.raises(type(failure)) as caught:
        async with lifecycle.module.lifespan(lifecycle.app):
            pytest.fail("startup should fail")
    assert caught.value is failure
    assert [step for step in lifecycle.trace if step in CLEANUP_ORDER] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", CLEANUP_ORDER)
@pytest.mark.parametrize("cancelled", [False, True])
async def test_cleanup_failure_does_not_skip_other_resources(
    lifecycle: SimpleNamespace, stage: str, cancelled: bool
) -> None:
    failure = asyncio.CancelledError("cancelled") if cancelled else RuntimeError("cleanup failed")
    lifecycle.failures[stage] = failure
    with pytest.raises(type(failure)) as caught:
        async with lifecycle.module.lifespan(lifecycle.app):
            pass
    assert caught.value is failure
    assert lifecycle.trace[-7:] == CLEANUP_ORDER


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["startup", "body", "cancellation"])
async def test_original_failure_survives_all_cleanup_failures(
    lifecycle: SimpleNamespace, origin: str
) -> None:
    original = (
        asyncio.CancelledError("original") if origin == "cancellation" else RuntimeError("original")
    )
    for stage in CLEANUP_ORDER:
        lifecycle.failures[stage] = RuntimeError(f"failed {stage}")
    if origin == "startup":
        lifecycle.failures["runtime.start"] = original
    with pytest.raises(type(original)) as caught:
        async with lifecycle.module.lifespan(lifecycle.app):
            raise original
    assert caught.value is original
    assert lifecycle.trace[-7:] == CLEANUP_ORDER
