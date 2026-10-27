from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, call

import pytest
from pytest import MonkeyPatch

from src.base.queues.models import AgentVerseJob
from src.base.queues.worker import QueueWorker, QueueWorkerPool
from src.domains.agentverse.tools.web_crawler_tool.execution.crawl_chunk_runner import (
    CrawlChunkResult,
)
from src.domains.agentverse.tools.web_crawler_tool.streams.consumer import (
    SitemapCrawlConsumer,
)
from src.domains.agentverse.tools.web_crawler_tool.streams.handler import (
    SitemapCrawlHandler,
)


def make_chunk_job(
    *,
    chunk_id: str,
    url_hash: str,
    chunk_index: int,
) -> AgentVerseJob:
    return AgentVerseJob(
        schema_name="crawler.sitemap_chunk_job.v1",
        job_id=f"job-{chunk_id}",
        run_id="run-1",
        chunk_id=chunk_id,
        payload={
            "chunk_index": chunk_index,
            "url_hashes": [url_hash],
        },
        created_at=datetime.now(UTC),
    )

class ConcurrentFakeQueueService:
    def __init__(
        self,
        messages: list[tuple[str, AgentVerseJob]],
        stop_event: asyncio.Event,
    ) -> None:
        self.messages = list(messages)
        self.stop_event = stop_event
        self.lock = asyncio.Lock()
        self.acknowledged: list[str] = []

    async def ensure_queue(
        self,
        *,
        queue_name: str,
        consumer_group: str,
    ) -> None:
        pass

    async def reclaim_stale(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        consumer_name: str,
        min_idle_ms: int,
        count: int = 100,
    ) -> list[tuple[str, AgentVerseJob]]:
        return []

    async def consume(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        consumer_name: str,
        count: int = 1,
        block_ms: int = 5_000,
    ) -> list[tuple[str, AgentVerseJob]]:
        async with self.lock:
            if not self.messages:
                self.stop_event.set()
                return []

            return [self.messages.pop(0)]

    async def acknowledge(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        message_id: str,
    ) -> None:
        async with self.lock:
            self.acknowledged.append(message_id)


class FakeQueueService:
    def __init__(
        self,
        messages: list[tuple[str, AgentVerseJob]],
    ) -> None:
        self.messages = messages
        self.acknowledged: list[str] = []

    async def acknowledge(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        message_id: str,
    ) -> None:
        self.acknowledged.append(message_id)

    async def consume(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        consumer_name: str,
        count: int = 1,
        block_ms: int = 5_000,
    ) -> list[tuple[str, AgentVerseJob]]:
        return self.messages[:count]

    async def ensure_queue(
        self,
        *,
        queue_name: str,
        consumer_group: str,
    ) -> None:
        pass

    async def reclaim_stale(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        consumer_name: str,
        min_idle_ms: int,
        count: int = 100,
    ) -> list[tuple[str, AgentVerseJob]]:
        return []

@pytest.fixture
def crawler_dependencies() -> dict[str, Any]:
    return {
        "cfg": SimpleNamespace(
            rate_limit_delay_ms=0,
            security=None,
        ),
        "runtime": SimpleNamespace(
            crawl_scope="sitemap",
        ),
        "start_url": "https://example.com",
        "crawl_mode": "static",
        "fetcher": object(),
        "auth_context": None,
        "agent": object(),
        "acquisition_started": 0.0,
        "accumulator": object(),
        "planned_total": 2,
        "on_page_fetched": AsyncMock(),
    }


def make_job() -> AgentVerseJob:
    return AgentVerseJob(
        schema_name="crawler.sitemap_chunk_job.v1",
        job_id="job-1",
        run_id="run-1",
        chunk_id="chunk-1",
        payload={
            "chunk_index": 0,
            "url_hashes": ["hash-1"],
        },
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_handler_processes_chunk_and_acknowledges_after_success(
    monkeypatch: MonkeyPatch,
    crawler_dependencies: dict[str, Any],
) -> None:
    job = make_job()
    message_id = "message-1"

    queue_service = FakeQueueService(
        messages=[(message_id, job)],
    )

    inventory_store = SimpleNamespace(
        get_leased_urls_by_hashes=AsyncMock(
            return_value=[
                SimpleNamespace(
                    url="https://example.com/page-1",
                    url_hash="hash-1",
                    source_sitemap_url=(
                        "https://example.com/sitemap.xml"
                    ),
                    discovery_order=0,
                )
            ],
        ),
        complete_batch=AsyncMock(),
        renew_sitemap_url_batch=AsyncMock(
            return_value=1,
        ),
    )

    expected_result = CrawlChunkResult(
        chunk_index=0,
        planned=1,
        fetched=1,
        queued_for_parse=1,
        failed=0,
        fetched_url_hashes=["hash-1"],
        failed_url_hashes=[],
    )

    async def fake_execute_crawl_chunk(
        **kwargs: Any,
    ) -> Any:
        return expected_result

    monkeypatch.setattr(
        "src.domains.agentverse.tools.web_crawler_tool.streams.consumer."
        "execute_crawl_chunk",
        fake_execute_crawl_chunk,
    )

    consumer = SitemapCrawlConsumer(
        run_id="run-1",
        queue_service=queue_service,
        inventory_store=inventory_store,
        queue_name="agentverse:crawler:acquisition:run-1",
        consumer_group="crawler:run-1",
        lease_seconds=300,
        lease_renew_interval_seconds=60,
        on_chunk_complete=None,
        on_chunk_progress=None,
        **crawler_dependencies,
    )

    handler = SitemapCrawlHandler(
        consumer=consumer,
        chunk_window=SimpleNamespace(release=Mock()),
        on_chunk_complete=AsyncMock(),
    )

    messages = await queue_service.consume(
        queue_name=consumer.queue_name,
        consumer_group=consumer.consumer_group,
        consumer_name="handler-test",
        count=1,
        block_ms=1_000,
    )

    assert len(messages) == 1

    received_message_id, received_job = messages[0]

    result = await handler(received_job)

    assert result is expected_result

    inventory_store.get_leased_urls_by_hashes.assert_awaited_once_with(
        run_id="run-1",
        lease_id="chunk-1",
        url_hashes=["hash-1"],
    )
    # Acquisition queues parsing; only the downstream page pipeline completes inventory.
    inventory_store.complete_batch.assert_not_awaited()
    handler.on_chunk_complete.assert_awaited_once_with(expected_result)
    handler.chunk_window.release.assert_called_once_with()

    assert queue_service.acknowledged == []

    await queue_service.acknowledge(
        queue_name=consumer.queue_name,
        consumer_group=consumer.consumer_group,
        message_id=received_message_id,
    )

    assert queue_service.acknowledged == [message_id]


@pytest.mark.asyncio
async def test_handler_does_not_acknowledge_failed_job(
    monkeypatch: MonkeyPatch,
    crawler_dependencies: dict[str, Any],
) -> None:
    job = make_job()

    queue_service = FakeQueueService(
        messages=[("message-1", job)],
    )

    inventory_store = SimpleNamespace(
        get_leased_urls_by_hashes=AsyncMock(
            return_value=[
                SimpleNamespace(
                    url="https://example.com/page-1",
                    url_hash="hash-1",
                    source_sitemap_url=(
                        "https://example.com/sitemap.xml"
                    ),
                    discovery_order=0,
                )
            ],
        ),
        complete_batch=AsyncMock(),
        renew_sitemap_url_batch=AsyncMock(
            return_value=1,
        ),
    )

    async def failing_execute_crawl_chunk(
        **kwargs: Any,
    ) -> Any:
        raise RuntimeError("crawl failed")

    monkeypatch.setattr(
        "src.domains.agentverse.tools.web_crawler_tool.streams.consumer."
        "execute_crawl_chunk",
        failing_execute_crawl_chunk,
    )

    consumer = SitemapCrawlConsumer(
        run_id="run-1",
        queue_service=queue_service,
        inventory_store=inventory_store,
        queue_name="agentverse:crawler:acquisition:run-1",
        consumer_group="crawler:run-1",
        lease_seconds=300,
        lease_renew_interval_seconds=60,
        on_chunk_complete=None,
        on_chunk_progress=None,
        **crawler_dependencies,
    )

    handler = SitemapCrawlHandler(
        consumer=consumer,
        chunk_window=SimpleNamespace(release=Mock()),
        on_chunk_complete=AsyncMock(),
    )

    with pytest.raises(
        RuntimeError,
        match="crawl failed",
    ):
        await handler(job)

    assert queue_service.acknowledged == []
    inventory_store.complete_batch.assert_not_awaited()
    handler.on_chunk_complete.assert_not_awaited()
    handler.chunk_window.release.assert_called_once_with()


@pytest.mark.asyncio
async def test_queue_worker_processes_crawler_handler(
    monkeypatch: MonkeyPatch,
    crawler_dependencies: dict[str, Any],
) -> None:
    job = make_job()
    message_id = "message-worker-1"

    queue_service = FakeQueueService(
        messages=[(message_id, job)],
    )

    inventory_store = SimpleNamespace(
        get_leased_urls_by_hashes=AsyncMock(
            return_value=[
                SimpleNamespace(
                    url="https://example.com/page-1",
                    url_hash="hash-1",
                    source_sitemap_url=(
                        "https://example.com/sitemap.xml"
                    ),
                    discovery_order=0,
                )
            ],
        ),
        complete_batch=AsyncMock(),
        renew_sitemap_url_batch=AsyncMock(
            return_value=1,
        ),
    )

    expected_result = CrawlChunkResult(
        chunk_index=0,
        planned=1,
        fetched=1,
        queued_for_parse=1,
        failed=0,
        fetched_url_hashes=["hash-1"],
        failed_url_hashes=[],
    )

    async def fake_execute_crawl_chunk(
        **kwargs: Any,
    ) -> Any:
        return expected_result

    monkeypatch.setattr(
        "src.domains.agentverse.tools.web_crawler_tool.streams.consumer."
        "execute_crawl_chunk",
        fake_execute_crawl_chunk,
    )

    consumer = SitemapCrawlConsumer(
        run_id="run-1",
        queue_service=queue_service,
        inventory_store=inventory_store,
        queue_name="agentverse:crawler:acquisition:run-1",
        consumer_group="crawler:run-1",
        lease_seconds=300,
        lease_renew_interval_seconds=60,
        on_chunk_complete=None,
        on_chunk_progress=None,
        **crawler_dependencies,
    )

    handler = SitemapCrawlHandler(
        consumer=consumer,
        chunk_window=SimpleNamespace(release=Mock()),
        on_chunk_complete=AsyncMock(),
    )

    async def complete_before_ack(result: CrawlChunkResult) -> None:
        assert result is expected_result
        assert queue_service.acknowledged == []

    handler.on_chunk_complete.side_effect = complete_before_ack

    worker = QueueWorker(
        queue_service=queue_service,
        queue_name=consumer.queue_name,
        consumer_group=consumer.consumer_group,
        handler=handler,
        stale_message_idle_ms=300_000,
        batch_size=1,
        block_ms=1_000,
    )

    processed = await worker.run(
        consumer_name="worker-0",
        max_jobs=1,
    )

    assert processed == 1
    assert queue_service.acknowledged == [message_id]

    handler.on_chunk_complete.assert_awaited_once_with(expected_result)
    handler.chunk_window.release.assert_called_once_with()
    inventory_store.complete_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_completion_failure_releases_window_and_worker_does_not_acknowledge() -> None:
    job = make_job()
    queue_service = FakeQueueService(messages=[("message-1", job)])
    result = CrawlChunkResult(
        chunk_index=0, planned=1, fetched=1, queued_for_parse=1, failed=0,
        fetched_url_hashes=["hash-1"], failed_url_hashes=[],
    )
    consumer = SimpleNamespace(process_job_without_ack=AsyncMock(return_value=result))
    handler = SitemapCrawlHandler(
        consumer=consumer,
        chunk_window=SimpleNamespace(release=Mock()),
        on_chunk_complete=AsyncMock(side_effect=RuntimeError("completion failed")),
    )
    worker = QueueWorker(
        queue_service=queue_service,
        queue_name="agentverse:crawler:acquisition:run-1",
        consumer_group="crawler:run-1",
        handler=handler,
    )

    with pytest.raises(RuntimeError, match="completion failed"):
        await worker.run(consumer_name="worker-0", max_jobs=1)

    consumer.process_job_without_ack.assert_awaited_once_with(job=job)
    handler.on_chunk_complete.assert_awaited_once_with(result)
    handler.chunk_window.release.assert_called_once_with()
    assert queue_service.acknowledged == []

@pytest.mark.asyncio
async def test_worker_pool_processes_chunks_once(
    monkeypatch: MonkeyPatch,
    crawler_dependencies: dict[str, Any],
) -> None:
    stop_event = asyncio.Event()

    job_one = make_chunk_job(
        chunk_id="chunk-1",
        url_hash="hash-1",
        chunk_index=0,
    )
    job_two = make_chunk_job(
        chunk_id="chunk-2",
        url_hash="hash-2",
        chunk_index=1,
    )

    queue_service = ConcurrentFakeQueueService(
        messages=[
            ("message-1", job_one),
            ("message-2", job_two),
        ],
        stop_event=stop_event,
    )

    records_by_hash = {
        "hash-1": SimpleNamespace(
            url="https://example.com/page-1",
            url_hash="hash-1",
            source_sitemap_url="https://example.com/sitemap.xml",
            discovery_order=0,
        ),
        "hash-2": SimpleNamespace(
            url="https://example.com/page-2",
            url_hash="hash-2",
            source_sitemap_url="https://example.com/sitemap.xml",
            discovery_order=1,
        ),
    }

    inventory_store = SimpleNamespace(
        get_leased_urls_by_hashes=AsyncMock(
            side_effect=lambda **kwargs: [
                records_by_hash[url_hash]
                for url_hash in kwargs["url_hashes"]
            ],
        ),
        complete_batch=AsyncMock(),
        renew_sitemap_url_batch=AsyncMock(
            return_value=1,
        ),
    )

    processed_chunk_ids: list[str] = []

    async def fake_execute_crawl_chunk(
        **kwargs: Any,
    ) -> Any:
        chunk = kwargs["chunk"]
        chunk_id = chunk.targets[0].metadata["url_hash"]
        processed_chunk_ids.append(chunk_id)

        await asyncio.sleep(0)

        return CrawlChunkResult(
            chunk_index=chunk.index,
            planned=1,
            fetched=1,
            queued_for_parse=1,
            failed=0,
            fetched_url_hashes=[chunk_id],
            failed_url_hashes=[],
        )

    monkeypatch.setattr(
        "src.domains.agentverse.tools.web_crawler_tool.streams.consumer."
        "execute_crawl_chunk",
        fake_execute_crawl_chunk,
    )

    consumer = SitemapCrawlConsumer(
        run_id="run-1",
        queue_service=queue_service,
        inventory_store=inventory_store,
        queue_name="agentverse:crawler:acquisition:run-1",
        consumer_group="crawler:run-1",
        lease_seconds=300,
        lease_renew_interval_seconds=60,
        on_chunk_complete=None,
        on_chunk_progress=None,
        **crawler_dependencies,
    )

    handler = SitemapCrawlHandler(
        consumer=consumer,
        chunk_window=SimpleNamespace(release=Mock()),
        on_chunk_complete=AsyncMock(),
    )

    worker_pool = QueueWorkerPool(
        worker_count=1,
        worker_factory=lambda index: QueueWorker(
            queue_service=queue_service,
            queue_name=consumer.queue_name,
            consumer_group=consumer.consumer_group,
            handler=handler,
            stale_message_idle_ms=300_000,
            batch_size=1,
            block_ms=1_000,
        ),
    )
    

    processed = await worker_pool.run(
        stop_event=stop_event,
        max_jobs=2,
    )

    assert processed == 2
    assert set(processed_chunk_ids) == {
        "hash-1",
        "hash-2",
    }
    assert len(processed_chunk_ids) == 2
    assert set(queue_service.acknowledged) == {
        "message-1",
        "message-2",
    }
    assert len(queue_service.acknowledged) == 2
    assert handler.on_chunk_complete.await_count == 2
    assert {
        result.args[0].fetched_url_hashes[0]
        for result in handler.on_chunk_complete.await_args_list
    } == {"hash-1", "hash-2"}
    assert handler.chunk_window.release.call_args_list == [call(), call()]
    inventory_store.complete_batch.assert_not_awaited()
