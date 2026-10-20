from collections.abc import Awaitable, Callable


class EventRouter:
    def __init__(self):
        self._handlers: dict[str, Callable[..., Awaitable]] = {}

    def on(self, event_name: str):
        def decorator(func: Callable[..., Awaitable]):
            self._handlers[event_name] = func
            return func

        return decorator

    def get(self, event_name: str) -> Callable[..., Awaitable] | None:
        return self._handlers.get(event_name)

    @property
    def handlers(self):
        return self._handlers

    def all_events(self) -> dict[str, Callable[..., Awaitable]]:
        return self._handlers

    def register(self, event_name: str, func: Callable[..., Awaitable]):
        self._handlers[event_name] = func

    def unregister(self, event_name: str):
        if event_name in self._handlers:
            del self._handlers[event_name]
