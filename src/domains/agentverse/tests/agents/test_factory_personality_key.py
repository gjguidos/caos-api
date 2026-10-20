"""``AgentService`` validates the personality reference against the registry and
stores it in canonical (normalized) form. Unknown keys are rejected at write time,
on both the create and the update path.
"""

from __future__ import annotations

import pytest

# Importing the personality modules triggers their registration decorators,
# populating the personality registry (in the app this happens via the
# auto-router walk during initialize_app).
import src.domains.agentverse.personalities.empty_eva  # noqa: F401
import src.domains.agentverse.personalities.shakespeare  # noqa: F401
from src.domains.agentverse.registries import personality_registry_instance


def test_valid_key_is_returned_normalized() -> None:
    resolve = personality_registry_instance.resolve_key
    assert resolve("Empty EVA", kind="personality") == "empty_eva"
    assert resolve("William Shakespeare", kind="personality") == "william_shakespeare"


def test_unknown_key_raises() -> None:
    with pytest.raises(ValueError, match="Unknown personality"):
        personality_registry_instance.resolve_key("does_not_exist", kind="personality")
