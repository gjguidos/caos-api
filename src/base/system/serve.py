"""Start the CAOS API with Uvicorn (RUNTIME-CONFIG-0.1).

The governed runtime never reloads on its own: hot reload is a separate, explicit
developer setting, ``CAOS_API_RELOAD``, and is never derived from ``ENVIRONMENT``
(which also selects local authentication, policy and storage modes). Unset or ``false``
disables it; ``true`` enables it; any other value refuses to start.

Run as ``python -m src.base.system.serve`` (Compose ``command``) or through
``entrypoint.py`` (the image's default command).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

RELOAD_VARIABLE = "CAOS_API_RELOAD"
_VALUES = {"true": True, "false": False}

APP = "src.main:app"
HOST = "0.0.0.0"  # The container's published API port.
PORT = 8001


def reload_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Whether Uvicorn hot reload is explicitly enabled (default: disabled)."""
    value = (os.environ if environ is None else environ).get(RELOAD_VARIABLE)
    if value is None or value.strip() == "":
        return False
    try:
        return _VALUES[value.strip().lower()]
    except KeyError:
        raise ValueError(f"{RELOAD_VARIABLE} must be 'true' or 'false'") from None


def main() -> None:
    import uvicorn

    uvicorn.run(
        APP,
        host=HOST,
        port=PORT,
        reload=reload_enabled(),
        log_config=None,  # The application configures its own logging.
    )


if __name__ == "__main__":
    main()
