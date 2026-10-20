import asyncio


class SitemapChunkWindow:
    def __init__(
        self,
        *,
        max_in_flight: int = 4,
    ) -> None:
        if max_in_flight <= 0:
            raise ValueError(
                "max_in_flight must be greater than zero"
            )

        self._slots = asyncio.Semaphore(max_in_flight)

    async def acquire(self) -> None:
        await self._slots.acquire()

    def release(self) -> None:
        self._slots.release()