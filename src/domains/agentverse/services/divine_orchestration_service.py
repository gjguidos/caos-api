from __future__ import annotations

from types import SimpleNamespace

from fastapi import WebSocket

from src.base.entities.user import User
from src.base.handlers.exception import classify_error
from src.domains.agentverse.entities.agent_request import AgentRequest
from src.domains.agentverse.events.socket_contract import CreateResult, LogLevel, LogStage
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain
from src.domains.agentverse.services.agent_chat_service import AgentChatService
from src.domains.agentverse.services.agent_service import AgentService
from src.domains.agentverse.tools.tool_manager import ToolManager


class DivineOrchestrationService:
    """Orchestrates agent lifecycle: creation, validation, storage, and activation."""

    def __init__(
        self,
        agent_service: AgentService,
        agent_chat_service: AgentChatService,
    ):
        self.agent_service = agent_service
        self.agent_chat_service = agent_chat_service

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[👑 DOS][⚙️ INIT] divine_orchestration_service_initialized",
            extra={
                "service": self.__class__.__name__,
                "has_agent_service": agent_service is not None,
                "has_agent_chat_service": agent_chat_service is not None,
            },
        )

    async def create_agent(
        self,
        websocket: WebSocket,
        socket_emitter,
        socket_id: str,
        **data,
    ):
        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[👑 DOS][🚀 CREATE] create_agent_start",
            extra={
                "data_keys": list(data.keys()),
            },
        )
        await socket_emitter.log(
            "[👑 DOS][🚀 CREATE] Initiating creation of new EVA unit...",
            level=LogLevel.INFO,
            stage=LogStage.PARSE,
        )

        request = self._create_request_context(websocket)

        user: User = websocket.scope.get("user")
        if not user:
            AGENTVERSE_LOGGER.warning(
                LogDomain.COMMAND_ROOM,
                "[👑 DOS][❌ CREATE] user_not_authenticated",
            )
            await socket_emitter.log(
                "[👑 DOS][❌ CREATE] Agent creation failed: User not authenticated",
                level=LogLevel.ERROR,
                stage=LogStage.PARSE,
            )
            await self._finalize_failure(socket_emitter, "User not authenticated")
            return {"error": "User not authenticated"}

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[👑 DOS][👤 CREATE] authenticated_user_resolved",
        )

        try:
            agent_request = await self._parse_request(socket_emitter, data, user)
        except Exception as e:
            AGENTVERSE_LOGGER.error(
                LogDomain.COMMAND_ROOM,
                "[👑 DOS][💥 CREATE] parse_request_failed",
                extra={
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
                exc_info=True,
            )
            message, code = classify_error(e)
            await socket_emitter.log(
                f"[👑 DOS][💥 CREATE] Failed to parse agent creation request: {message}",
                level=LogLevel.ERROR,
                stage=LogStage.PARSE,
            )
            await self._finalize_failure(socket_emitter, message)
            return {"error": message, "code": code}

        try:
            AGENTVERSE_LOGGER.info(
                LogDomain.COMMAND_ROOM,
                "[👑 DOS][🏗️ CREATE] agent_service_create_requested",
                extra={
                    "agent_type": getattr(agent_request, "agent_type", None),
                    "tool_count": len(getattr(agent_request, "tools", []) or []),
                },
            )

            agent = await self.agent_service.create_agent(
                socket_emitter,
                request,
                agent_request,
                user,
            )
        except Exception as e:
            AGENTVERSE_LOGGER.error(
                LogDomain.COMMAND_ROOM,
                "[👑 DOS][💥 CREATE] agent_service_create_failed",
                extra={
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
                exc_info=True,
            )
            message, code = classify_error(e)
            await socket_emitter.log(
                f"[👑 DOS][💥 CREATE] Agent creation failed: {message}",
                level=LogLevel.ERROR,
                stage=LogStage.CONFIGURE,
            )
            await self._finalize_failure(socket_emitter, message)
            return {"error": message, "code": code}

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[👑 DOS][✅ CREATE] agent_created",
        )

        try:
            AGENTVERSE_LOGGER.info(
                LogDomain.COMMAND_ROOM,
                "[👑 DOS][🧪 TEST] test_agent_start",
            )
            await socket_emitter.log(
                f"[👑 DOS][🧪 TEST] Testing EVA unit '{agent.name}'...",
                level=LogLevel.INFO,
                stage=LogStage.TEST,
            )

            test_result = await self.agent_chat_service.test_agent(
                socket_emitter=socket_emitter,
                request=request,
                agent_id=agent.id,
                user=user,
                socket_id=socket_id,
                tenant_id=getattr(self, "tenant_id", None),
            )

            if test_result.get("status") == "success":
                AGENTVERSE_LOGGER.info(
                    LogDomain.COMMAND_ROOM,
                    "[👑 DOS][✅ TEST] test_agent_success",
                    extra={
                        "response_text": test_result.get("response_text"),
                    },
                )
                await socket_emitter.log(
                    f"[👑 DOS][✅ TEST] EVA unit test completed: {test_result.get('response_text', 'OK')}",
                    level=LogLevel.SUCCESS,
                    stage=LogStage.TEST,
                )
            else:
                AGENTVERSE_LOGGER.warning(
                    LogDomain.COMMAND_ROOM,
                    "[👑 DOS][⚠️ TEST] test_agent_degraded",
                    extra={
                        "status": test_result.get("status"),
                        "message": test_result.get("message"),
                    },
                )
                await socket_emitter.log(
                    f"[👑 DOS][⚠️ TEST] EVA unit test had issues: {test_result.get('message', 'Unknown error')}",
                    level=LogLevel.WARNING,
                    stage=LogStage.TEST,
                )
        except Exception as e:
            AGENTVERSE_LOGGER.error(
                LogDomain.COMMAND_ROOM,
                "[👑 DOS][💥 TEST] test_agent_failed",
                extra={
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
                exc_info=True,
            )
            message, _code = classify_error(e)
            await socket_emitter.log(
                f"[👑 DOS][💥 TEST] EVA unit test failed: {message}",
                level=LogLevel.ERROR,
                stage=LogStage.TEST,
            )
            # Agent is already stored; do not fail creation.

        await self._finalize_creation(socket_emitter, agent)

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[👑 DOS][🏁 CREATE] create_agent_success",
            extra={
                "agent_system_name": getattr(agent, "system_name", None),
            },
        )

        return {
            "agent_id": agent.id,
            "agent_system_name": agent.system_name,
        }

    def _create_request_context(self, websocket: WebSocket):
        request = SimpleNamespace()
        request.app = websocket.app

        AGENTVERSE_LOGGER.debug(
            LogDomain.COMMAND_ROOM,
            "[👑 DOS][🧱 CONTEXT] request_context_created",
            extra={
                "service": self.__class__.__name__,
                "has_app": getattr(request, "app", None) is not None,
            },
        )

        return request

    async def _parse_request(self, socket_emitter, data, user: User) -> AgentRequest:
        """Parse and validate the incoming agent creation request."""
        try:
            payload = data.get("message", {})

            AGENTVERSE_LOGGER.info(
                LogDomain.COMMAND_ROOM,
                "[👑 DOS][📝 PARSE] parse_request_start",
                extra={
                    "payload_keys": list(payload.keys()) if isinstance(payload, dict) else [],
                },
            )

            agent_request = AgentRequest(**payload)
            normalized_specs, required_kinds = ToolManager.validate_specs(agent_request.tools)
            agent_request.tools = normalized_specs
            agent_request.required_kinds = required_kinds
            agent_request.user_id = user.id

            AGENTVERSE_LOGGER.info(
                LogDomain.COMMAND_ROOM,
                "[👑 DOS][✅ PARSE] parse_request_success",
                extra={
                    "agent_type": getattr(agent_request, "agent_type", None),
                    "tool_count": len(getattr(agent_request, "tools", []) or []),
                    "required_kinds": list(getattr(agent_request, "required_kinds", []) or []),
                },
            )

            return agent_request
        except Exception as e:
            AGENTVERSE_LOGGER.error(
                LogDomain.COMMAND_ROOM,
                "[👑 DOS][💥 PARSE] parse_request_failed",
                extra={
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
                exc_info=True,
            )
            raise

    async def _finalize_creation(self, socket_emitter, agent):
        """Emit final logs and events after successful agent creation and testing."""
        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[👑 DOS][🏁 FINALIZE] finalize_creation_start",
            extra={
                "agent_name": getattr(agent, "name", None),
                "agent_system_name": getattr(agent, "system_name", None),
            },
        )

        await socket_emitter.log(
            f"[👑 DOS][✨ FINALIZE] EVA unit '{agent.name}' is now operational.",
            level=LogLevel.SUCCESS,
            stage=LogStage.FINALIZE,
        )

        result = CreateResult.ok(
            agent_id=agent.id,
            agent_system_name=agent.system_name,
            response="EVA unit created and deployed successfully.",
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[👑 DOS][📡 FINALIZE] emit_create_event",
            extra={
                "event_name": "joshu-a.create",
            },
        )

        await socket_emitter.event("joshu-a.create", {"response": result.to_payload()})

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[👑 DOS][✅ FINALIZE] finalize_creation_success",
        )

    async def _finalize_failure(self, socket_emitter, message: str) -> None:
        """Emit the structured terminal event for a failed creation attempt.

        Mirrors the success envelope from `_finalize_creation` so the client can
        detect failure from `response.status` instead of parsing the log prose.
        """
        result = CreateResult.error(message)

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[👑 DOS][📡 CREATE] emit_create_failure_event",
            extra={
                "event_name": "joshu-a.create",
            },
        )

        await socket_emitter.event("joshu-a.create", {"response": result.to_payload()})
