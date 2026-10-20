from typing import Any


def is_pause_envelope(x: dict[str, Any]) -> bool:
    if str(x.get("status") or "").upper() == "PAUSED":
        return True

    result = x.get("result")
    if isinstance(result, dict) and result.get("paused"):
        return True

    if x.get("paused"):
        return True

    return False
