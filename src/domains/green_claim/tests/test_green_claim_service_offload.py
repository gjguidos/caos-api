"""
Tests that the CPU-bound parts of green claim analysis run off the event loop.

The problematic word matcher used to run inline in the request coroutine and
blocked the shared event loop for roughly a millisecond per token, which
tripped the websocket loop watchdog.
"""

import asyncio
import base64
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.green_claim.entities.green_claim_models import (
    GreenClaimLLMResponse,
    ProblematicWordMatch,
    ProblematicWordResult,
)
from src.domains.green_claim.services.green_claim_service import GreenClaimService


@pytest.fixture
def llm_response():
    return GreenClaimLLMResponse(
        green_claim_report=[],
        legal_statement="legal",
        guidelines="https://example.invalid/guidelines",
    )


@pytest.fixture
def service(llm_response):
    service = GreenClaimService(
        openai_repository=MagicMock(),
        audit_service=MagicMock(),
    )
    service._analyze_text = AsyncMock(return_value=llm_response)
    service._analyze_file = AsyncMock(return_value=llm_response)
    service._log_successful_audit = AsyncMock()
    service._log_failed_audit = AsyncMock()
    return service


def _matcher_stub(recorded_threads):
    """Matcher replacement that records which thread it ran on."""
    result = ProblematicWordResult(
        exact_matches=[
            ProblematicWordMatch(
                text="sustainable",
                matched_against="sustainable",
                confidence=1.0,
                type="exact",
                start=0,
                end=11,
            )
        ],
        close_matches=[],
    )

    def find(text: str) -> ProblematicWordResult:
        recorded_threads.append((threading.current_thread(), text))
        return result

    return find, result


@pytest.mark.asyncio
async def test_matcher_runs_off_the_event_loop(service):
    recorded: list = []
    find, expected = _matcher_stub(recorded)
    service._problematic_word_matcher.find = find

    result = await service.run_green_claim_analysis(text="sustainable", user_id="u1")

    assert len(recorded) == 1
    thread, text = recorded[0]
    assert thread is not threading.main_thread()
    assert text == "sustainable"
    assert result.problematic_words == expected


@pytest.mark.asyncio
async def test_chunked_matcher_runs_off_the_event_loop(service):
    recorded: list = []
    find, expected = _matcher_stub(recorded)
    service._problematic_word_matcher.find = find
    service._text_segmenter.segment = MagicMock(return_value=["chunk one", "chunk two"])

    result = await service.run_chunked_green_claim_analysis(text="sustainable text", user_id="u1")

    assert len(recorded) == 1
    thread, text = recorded[0]
    assert thread is not threading.main_thread()
    # The matcher sees the whole text, not the individual chunks
    assert text == "sustainable text"
    assert result.problematic_words == expected


@pytest.mark.asyncio
async def test_event_loop_keeps_running_during_analysis(service):
    """A slow matcher must not stop other tasks from being scheduled."""
    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    def slow_find(text: str) -> ProblematicWordResult:
        threading.Event().wait(0.3)
        return ProblematicWordResult(exact_matches=[], close_matches=[])

    service._problematic_word_matcher.find = slow_find

    ticker = asyncio.create_task(tick())
    try:
        await service.run_green_claim_analysis(text="some text", user_id="u1")
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)

    assert ticks > 5


@pytest.mark.asyncio
async def test_file_metadata_is_built_once_off_the_loop(service):
    recorded: list = []
    original = service._build_file_metadata

    def spy(file_data, mime_type, filename):
        recorded.append(threading.current_thread())
        return original(file_data, mime_type, filename)

    service._build_file_metadata = spy
    payload = base64.b64encode(b"pdf-bytes").decode()

    await service.run_green_claim_file_analysis(
        file_data=f"data:application/pdf;base64,{payload}",
        mime_type="application/pdf",
        filename="report.pdf",
        user_id="u1",
    )

    assert len(recorded) == 1
    assert recorded[0] is not threading.main_thread()


@pytest.mark.asyncio
async def test_file_metadata_not_recomputed_on_failure(service):
    calls = 0
    original = service._build_file_metadata

    def spy(file_data, mime_type, filename):
        nonlocal calls
        calls += 1
        return original(file_data, mime_type, filename)

    service._build_file_metadata = spy
    service._analyze_file = AsyncMock(side_effect=RuntimeError("llm exploded"))
    payload = base64.b64encode(b"pdf-bytes").decode()

    with pytest.raises(RuntimeError):
        await service.run_green_claim_file_analysis(
            file_data=f"data:application/pdf;base64,{payload}",
            mime_type="application/pdf",
            filename="report.pdf",
            user_id="u1",
        )

    assert calls == 1
    service._log_failed_audit.assert_awaited_once()
