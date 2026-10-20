from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
    AgentSoulProtocol,
)
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain
from src.domains.agentverse.personalities.utils.default_personality import (
    DEFAULT_PERSONALITY_KEY,
)
from src.domains.agentverse.registries import personality_registry_instance


def resolve_personality(
    personality_key: str | None,
    *,
    override_key: str | None = None,
) -> AgentSoulProtocol:
    """Instantiate the soul addressed by a personality key.

    Tries the per-turn ``override_key`` first, then the agent's own key, then the
    default. A key that is set but does not resolve is a data-integrity problem,
    not a normal miss, so it is logged at ERROR before falling through - unlike an
    absent key, which legitimately means "use the default".
    """
    for source, key in (("override", override_key), ("agent", personality_key)):
        if not key:
            continue
        try:
            cls = personality_registry_instance.get(key)
        except KeyError:
            AGENTVERSE_LOGGER.error(
                LogDomain.EVANGELION_BAY,
                "[🧬 EVA][PERSONALITY] key_unresolved",
                extra={"source": source, "personality_key": key},
            )
            continue

        AGENTVERSE_LOGGER.info(
            LogDomain.EVANGELION_BAY,
            "[🧬 EVA][PERSONALITY] key_binding",
            extra={"source": source, "personality_key": key},
        )
        return cls()

    try:
        cls = personality_registry_instance.get(DEFAULT_PERSONALITY_KEY)
    except KeyError:
        AGENTVERSE_LOGGER.error(
            LogDomain.EVANGELION_BAY,
            "[💀 EVA][PERSONALITY] resolution_failed",
            extra={"missing_profile": DEFAULT_PERSONALITY_KEY},
        )
        raise RuntimeError(
            f"EVA personality resolution failed - '{DEFAULT_PERSONALITY_KEY}' profile is missing."
        ) from None

    AGENTVERSE_LOGGER.info(
        LogDomain.EVANGELION_BAY,
        "[🌑 EVA][PERSONALITY] default_used",
        extra={"personality_key": DEFAULT_PERSONALITY_KEY},
    )
    return cls()
