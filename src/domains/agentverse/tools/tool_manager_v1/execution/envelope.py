from __future__ import annotations

from src.domains.agentverse.utils.run_trace import STATUS_ERROR


def error_envelope(error: str, *, kind: str = STATUS_ERROR) -> dict:
    """The envelope a blocked or failed execution returns.

    Every path out of ``execute_router`` returns this shape on failure so a
    caller can read the reason off ``error`` instead of inferring it from a
    bare ``None``. The reason is repeated inside ``result`` because callers that
    unwrap the result and discard the envelope - the nested tool runner, most
    importantly - would otherwise be left with a bare ``FAILED`` status.

    ``error_kind`` names *which* gate closed (``vetoed``, ``blocked``, plain
    ``error``). The router's telemetry wrapper reads it back off the return
    value, so a new gate labels itself here rather than reaching into the
    wrapper. It is deliberately not called ``status``: the workflow engine
    already reads that key off envelopes and matches it against run states.
    """
    return {
        "success": False,
        "error": error,
        "error_kind": kind,
        "result": {"status": "FAILED", "error": error},
    }
