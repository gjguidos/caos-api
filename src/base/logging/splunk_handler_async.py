import asyncio
import json
import logging
import traceback

import aiohttp

LEVEL_MAP = {
    "DEBUG": "Debug",
    "INFO": "Information",
    "WARNING": "Warning",
    "ERROR": "Error",
    "CRITICAL": "Critical",
}


class AsyncSplunkHECHandler(logging.Handler):
    """
    Asynchronous Splunk HEC logging handler for FastAPI.
    Uses an asyncio.Queue and background task to send logs.
    """

    def __init__(
        self,
        host: str,
        token: str,
        url: str,
        application_name: str,
        timeout=2,
    ):
        super().__init__()
        self.host = host
        self.token = token
        self.url = url
        self.application_name = application_name
        self.timeout = timeout
        self.queue = None  # Will be created when start() is called
        self._task = None
        self._stop_event = None  # Will be created when start() is called
        self._session = None
        self._loop = None  # Store reference to the event loop
        self._auth_failed = False
        self._auth_warned = False

    async def start(self):
        """Call once at app startup (after event loop exists)."""
        self._loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue(maxsize=1000)
        self._stop_event = asyncio.Event()
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._worker_loop())

    async def stop(self):
        """Call on app shutdown to gracefully close session."""
        self._stop_event.set()
        if self._task:
            await self._task
        if self._session:
            await self._session.close()

    def emit(self, record):
        """Non-blocking enqueue; safe to call from sync context."""
        try:
            if self._auth_failed:
                return
            payload = self._format_payload(record)
            # Check if we have a valid loop reference and it's not closed
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._safe_put, payload)
            # If no loop available, silently drop the log (handler not started yet)
        except Exception:
            self.handleError(record)

    def _safe_put(self, payload):
        if self.queue.full():
            try:
                _ = self.queue.get_nowait()  # Drop oldest
            except asyncio.QueueEmpty:
                pass
        self.queue.put_nowait(payload)

    def _format_payload(self, record):
        props = {}
        skip_keys = {"msg", "levelname", "levelno"}
        for key, value in record.__dict__.items():
            if key not in skip_keys:
                props[key] = self._safe_json_value(value)

        payload = {
            "time": record.created,
            "host": self.host,
            "event": {
                "Level": LEVEL_MAP.get(record.levelname, record.levelname),
                "RenderedMessage": record.getMessage(),
                "System": self.application_name,
                "Properties": props,
            },
        }

        if record.exc_info:
            payload["event"]["Exception"] = "".join(traceback.format_exception(*record.exc_info))

        return payload

    def _safe_json_value(self, value):
        try:
            json.dumps(value)
            return value
        except (TypeError, OverflowError):
            return str(value)

    async def _worker_loop(self):
        headers = {
            "Authorization": f"Splunk {self.token}",
            "Content-Type": "application/json",
        }

        while not self._stop_event.is_set() or not self.queue.empty():
            try:
                payload = await asyncio.wait_for(self.queue.get(), timeout=0.5)
            except TimeoutError:
                continue

            try:
                await self._send_with_retry(headers, payload)
            except Exception:
                # Never let a Splunk failure crash the worker loop
                pass

    async def _send_with_retry(self, headers: dict, payload: dict):
        for attempt in range(3):
            try:
                async with self._session.post(
                    self.url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                ) as resp:
                    if resp.status < 400:
                        return
                    # Auth errors (bad token) - no point retrying
                    if resp.status in (401, 403):
                        if not self._auth_warned:
                            self._auth_warned = True
                            import sys

                            print(
                                f"[Splunk] Authentication failed (HTTP {resp.status}). "
                                "Subsequent Splunk log delivery will be silently skipped. "
                                "Check SPLUNK_HEC_TOKEN.",
                                file=sys.stderr,
                            )
                        self._auth_failed = True
                        return
            except Exception:
                await asyncio.sleep(0.5 * (attempt + 1))
