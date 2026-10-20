from __future__ import annotations

import asyncio
from abc import ABC
from typing import Any, Literal

from src.domains.agentverse.agents.agent_interface import AgentInterface
from src.domains.agentverse.agents.mixins import (
    CapabilitiesMixin,
    HistoryMixin,
    KnowledgeAgentMixin,
    LifecycleMixin,
    MemoryMixin,
    PromptMixin,
)
from src.domains.agentverse.agents.mixins.utils.LLMCallsMixin import LLMCallsMixin
from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
    AgentSoulProtocol,
)
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.personalities.utils.generate_personality_context import (
    generate_personality_context,
)
from src.domains.agentverse.personalities.utils.resolve_personality import (
    resolve_personality,
)
from src.domains.agentverse.tools.spec import ToolSpec


class BaseAgent(
    CapabilitiesMixin,
    LifecycleMixin,
    LLMCallsMixin,
    MemoryMixin,
    HistoryMixin,
    PromptMixin,
    AgentInterface,
    KnowledgeAgentMixin,
    ABC,
):
    def __init__(
        self,
        id: str,
        user_id: str,
        socket_emitter: Any,
        creator: str,
        name: str,
        system_name: str,
        type: str,
        prompt: str,
        llm: Any,
        image: Any = None,
        video: Any = None,
        audio: Any = None,
        db: Any = None,
        cache: Any = None,
        vectordb: Any = None,
        commbridge: Any = None,
        messaging: Any = None,
        orchestrator: Any = None,
        http: dict[str, Any] | None = None,
        rest: dict[str, Any] | None = None,
        queue_service: Any = None,
        public_settings: dict[str, Any] | None = None,
        collections: list[str] | None = None,
        chat_url: str | None = None,
        role_name: str | None = None,
        rule_pack_ids: list[str] | None = None,
        personality: dict[str, Any] | None = None,
        personality_key: str | None = None,
        soul: AgentSoulProtocol | None = None,
        dna_sequence: str | None = None,
        tools: list[ToolSpec | dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        turn_lock: asyncio.Lock | None = None,
        visibility: Literal["public", "private"] = "private",
        whitelisted_groups: list[str] | None = None,
        **kwargs,
    ):
        if kwargs:
            AGENTVERSE_LOGGER.debug(
                LogDomain.AGENT_EXISTENTIAL_CORE,
                "[🤖 EVA][🧩 INIT] unused_kwargs_detected",
                extra={
                    "agent_id": id,
                    "agent_name": name,
                    "unused_kwargs": list(kwargs.keys()),
                },
            )

        if turn_lock is not None and not isinstance(turn_lock, asyncio.Lock):
            AGENTVERSE_LOGGER.warning(
                LogDomain.AGENT_EXISTENTIAL_CORE,
                "[🤖 EVA][⚠️ INIT] invalid_turn_lock",
                extra={
                    "agent_id": id,
                    "agent_name": name,
                    "provided_type": str(type(turn_lock)),
                },
            )
            raise TypeError("turn_lock must be an asyncio.Lock")

        AGENTVERSE_LOGGER.info(
            LogDomain.AGENT_EXISTENTIAL_CORE,
            "[🤖 EVA][🧬 INIT] dna_assembly_start",
            extra={
                "agent_id": id,
                "agent_name": name,
                "agent_type": type,
                "creator_user_id": creator,
                "role_name": role_name,
            },
        )

        # core fields
        self._id = id
        self._user_id = user_id
        self.socket_emitter = socket_emitter
        self.creator = creator
        self._name = name
        self.system_name = system_name
        self._type = type
        self._turn_lock: asyncio.Lock = turn_lock or asyncio.Lock()
        self.role_name = role_name
        self.rule_pack_ids: list[str] = rule_pack_ids or []
        self.personality = dict(personality or {})
        self.personality_key = personality_key
        self.soul = soul or resolve_personality(personality_key)
        self.prompt = prompt
        self.llm = llm
        self.image = image
        self.video = video
        self.audio = audio
        self.db = db
        self.cache = cache
        self.queue_service = queue_service
        self.vectordb = vectordb
        self.commbridge = commbridge
        self.orchestrator = orchestrator
        self.messaging = messaging
        self.http = http or {}
        self.rest = rest or {}
        self.public_settings = dict(public_settings or {})
        self.collections = list(collections or [])
        self.chat_url = chat_url
        self.dna_sequence = dna_sequence
        self.metadata = dict(metadata or {})

        self.personality_context = generate_personality_context(
            self.personality,
            expanded=True,
        )

        self.capabilities = self._compute_capabilities()

        # runtime bindings (filled via bind_runtime)
        self.request: Any | None = None
        self.default_session_id: str | None = None
        self.tenant_id: str | None = None

        # tools
        self.tools: dict[str, Any] = {}
        self.tool_specs = list(tools or [])
        self.tool_manager = None
        self.tool_router = None

        # Always create ToolManager; only this agent's configured tools are loaded.
        from src.domains.agentverse.tools.tool_manager import ToolManager

        self.tool_manager = ToolManager(self, self.tool_specs)

        AGENTVERSE_LOGGER.debug(
            LogDomain.AGENT_EXISTENTIAL_CORE,
            "[🤖 EVA][🛠️ TOOLS] tool_manager_online",
            extra={
                "agent_id": id,
                "agent_name": name,
                "tool_spec_count": len(self.tool_specs),
            },
        )

        # security and visibility
        self.visibility = visibility
        self.whitelisted_groups = list(whitelisted_groups or [])

        AGENTVERSE_LOGGER.info(
            LogDomain.AGENT_EXISTENTIAL_CORE,
            "[🤖 EVA][🏁 INIT] dna_assembly_complete",
            extra={
                "agent_id": self._id,
                "agent_name": self._name,
                "dna_sequence": self.dna_sequence,
                "role_name": self.role_name,
                "tool_spec_count": len(self.tool_specs),
                "collection_count": len(self.collections),
                "visibility": self.visibility,
            },
        )

        AGENTVERSE_LOGGER.debug(
            LogDomain.AGENT_EXISTENTIAL_CORE,
            "[🤖 EVA][🧠 SOUL] personality_context_ready",
            extra={
                "agent_id": self._id,
                "agent_name": self._name,
                "personality_keys": list(self.personality.keys()),
            },
        )

    @property
    def id(self) -> str:
        return self._id

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def type(self) -> str:
        return self._type

    @property
    def turn_lock(self) -> asyncio.Lock:
        return self._turn_lock
