"""``conversation@1.0.0``: optional, first-class Conversation Tool declaration (CONVERSATION-0.1A).

Attachment declares that an Agent *may* converse, in which modes, with which bounded
history and which exact activity contracts. It never authorizes: the existing
``chat.context`` / ``chat.response`` UTU grants still decide every turn, and the activity
contracts are resolved against the frozen Activity Contract Registry at use.
"""

from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from src.domains.agentverse.tools.spec import SynapticCapabilitySpec, SynapticToolSpec, ToolSpec

TOOL_KEY: Final = "conversation"
TOOL_VERSION: Final = "1.0.0"
# Conversation is a CAOS capability, not an Agent-specific route. The Tool declares its
# relationship to the existing signals; their providers and grants are unchanged.
CONTEXT_SIGNAL: Final = "chat.context"
RESPONSE_SIGNAL: Final = "chat.response"
SIGNAL_VERSION: Final = "1.0.0"
CONTEXT_PROVIDER: Final = "tool.chat"
RESPONSE_PROVIDER: Final = "cognitive.chat"

ConversationMode = Literal["context", "llm"]
MODE_SIGNALS: Final[dict[str, str]] = {"context": CONTEXT_SIGNAL, "llm": RESPONSE_SIGNAL}
ALL_MODES: Final[tuple[ConversationMode, ...]] = ("context", "llm")
# The existing V2 history projection bounds (``ConversationHistoryV2.project``);
# configuration can only narrow them.
MAX_HISTORY_MESSAGES: Final = 24
MAX_HISTORY_CHARACTERS: Final = 32768
# Exact ``id@version`` activity contract keys (``ActivityContract.key()``).
ACTIVITY_KEY_PATTERN: Final = (
    r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+@(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
)
ActivityKey = Annotated[str, StringConstraints(pattern=ACTIVITY_KEY_PATTERN, max_length=100)]


def _all_modes() -> list[ConversationMode]:
    return ["context", "llm"]


class HistoryPolicy(BaseModel):
    """How much prior conversation an Agent's turn may see. Narrows, never widens."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    max_messages: int = Field(default=MAX_HISTORY_MESSAGES, ge=0, le=MAX_HISTORY_MESSAGES)
    max_characters: int = Field(default=MAX_HISTORY_CHARACTERS, ge=0, le=MAX_HISTORY_CHARACTERS)


class ConversationToolConfig(BaseModel):
    """Per-Agent conversation configuration. A restriction, never authority.

    No grant, signal, scope, provider, prompt, instruction or model can be expressed.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    modes: Annotated[list[ConversationMode], Field(min_length=1, max_length=2)] = Field(
        default_factory=_all_modes
    )
    history: HistoryPolicy = Field(default_factory=HistoryPolicy)
    activities: Annotated[list[ActivityKey], Field(max_length=8)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len(set(self.modes)) != len(self.modes):
            raise ValueError("Duplicate conversation mode")
        if len(set(self.activities)) != len(self.activities):
            raise ValueError("Duplicate activity contract reference")
        return self


def _action(signal: str, provider: str) -> SynapticCapabilitySpec:
    # ``granted``: attachment never confers the capability; UTU grants still decide.
    return SynapticCapabilitySpec(
        signal=signal, version=SIGNAL_VERSION, provider=provider, access="granted"
    )


conversation_tool = ToolSpec(
    key=TOOL_KEY,
    name="Conversation",
    description=(
        "Lets an Agent take part in V2 conversations in the configured modes, with bounded "
        "history and exact activity contracts. Discovery and attachment do not authorize "
        "execution; conversation grants are evaluated separately on every turn."
    ),
    version=TOOL_VERSION,
    tool_class="conversational",
    scope="agent",
    exposure="public",
    tags=["conversation", "communication"],
    config=ConversationToolConfig().model_dump(),
    metadata={
        "ownership": "global",
        "execution_mode": "synaptic_only",
        "llm_free": True,
        "config_schema": (
            "src.domains.agentverse.tools.conversation_tool.spec.ConversationToolConfig"
        ),
    },
    synaptic=SynapticToolSpec(
        capabilities=[
            _action(CONTEXT_SIGNAL, CONTEXT_PROVIDER),
            _action(RESPONSE_SIGNAL, RESPONSE_PROVIDER),
        ]
    ),
    ui={
        "category": "conversation",
        "admin": {
            "fields": {
                "modes": {
                    "label": "Conversation modes",
                    "input": "multiselect",
                    "help": (
                        "How this Agent may take part in a conversation; execution is "
                        "authorized separately."
                    ),
                    "default": list(ALL_MODES),
                    "options": [
                        {
                            "value": "context",
                            "label": "Context",
                            "description": "Contributes conversation context without a model.",
                        },
                        {
                            "value": "llm",
                            "label": "Response",
                            "description": "Answers with its configured language model.",
                        },
                    ],
                },
            }
        },
    },
)
