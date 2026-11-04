"""Typed activity-coordinator failures, shared by the V2 dispatcher and coordinators.

Dependency-free on purpose: the dispatcher maps these to ``event.error`` without
importing a coordinator (and, through it, the Tools it validates), so module import
order and registry initialization stay as they were before coordinators existed.
"""


class CoordinatorError(Exception):
    """Typed, requester-safe failure (codes only; never another owner's resources)."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
