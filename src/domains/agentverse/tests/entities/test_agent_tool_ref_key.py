"""The tool key is the sole addressing contract on a persisted tool reference.

``AgentToolRef`` used to carry a nullable ``key`` plus a display ``name`` that the
factory fell back to. Both are gone: the key is required and canonicalized at the
boundary, and the display name is read back from the registry at expansion time.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.domains.agentverse.entities.agent_request import AgentRequest
from src.domains.agentverse.tools.spec import AgentToolRef


class TestAgentToolRefKey:
    def test_key_is_canonicalized(self):
        assert AgentToolRef(key="  Datetime Tool  ").key == "datetime_tool"

    def test_missing_key_rejected(self):
        with pytest.raises(ValidationError):
            AgentToolRef()  # type: ignore[call-arg]

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_empty_key_rejected(self, bad):
        with pytest.raises(ValidationError):
            AgentToolRef(key=bad)

    def test_display_name_is_not_stored(self):
        """A stray ``name`` from an older document is ignored, not persisted."""
        ref = AgentToolRef.model_validate({"key": "datetime", "name": "Datetime Tool"})
        assert not hasattr(ref, "name")
        assert ref.model_dump() == {"key": "datetime", "config": {}}


class TestAgentRequestToolKeys:
    """The request-level ``_require_tool_keys`` validator is gone - the field type
    now enforces it, so a keyless tool fails at parse time.
    """

    @staticmethod
    def _request(**overrides):
        return AgentRequest(name="A", prompt="p", type="chat", user_id="u1", **overrides)

    def test_keyed_tool_accepted(self):
        req = self._request(tools=[{"key": "Datetime Tool"}])
        assert [t.key for t in req.tools] == ["datetime_tool"]

    def test_keyless_tool_rejected(self):
        with pytest.raises(ValidationError):
            self._request(tools=[{"name": "Datetime Tool"}])
