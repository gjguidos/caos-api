from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from src.base.authentication.permission import Permission
from src.base.authentication.rbac import require_roles_and_scopes
from src.base.authentication.role import Role
from src.base.entities.user import User
from src.domains.agentverse.dependencies.get_agent_service import get_agent_service
from src.domains.agentverse.dependencies.get_personality_service import get_personality_service
from src.domains.agentverse.dependencies.get_registry_service import get_registry_service
from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
    AgentSoulProtocol,
)
from src.domains.agentverse.entities.agent_update_request import AgentUpdateRequest
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.services.agent_service import AgentService
from src.domains.agentverse.services.personality_service import PersonalityService
from src.domains.agentverse.services.registry_service import RegistryService

router = APIRouter(tags=["Agents"])


def diff_dump(soul: AgentSoulProtocol) -> dict:
    baseline = AgentSoulProtocol()
    raw = soul.model_dump(exclude_none=True)
    base = baseline.model_dump(exclude_none=True)
    return {k: v for k, v in raw.items() if v != base.get(k)}


@router.get("/api/v1/agents/list")
async def find_all_agents(
    agent_service: AgentService = Depends(get_agent_service),
    user: User = Depends(
        require_roles_and_scopes(
            required_roles=[[Role.USER]], required_permissions=[Permission.AGENT_READ]
        )
    ),
):
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🤖 AGENTS] list_agents_start",
        extra={
            "user_id": getattr(user, "id", None),
        },
    )

    results = await agent_service.list_agents(user)

    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🤖 AGENTS] list_agents_success",
        extra={
            "user_id": getattr(user, "id", None),
            "result_count": len(results) if hasattr(results, "__len__") else None,
        },
    )
    return results


@router.get("/api/v1/agents/id/{agent_id}")
async def find_agent(
    agent_id: str,
    agent_service: AgentService = Depends(get_agent_service),
    user: User = Depends(
        require_roles_and_scopes(
            required_roles=[[Role.USER]], required_permissions=[Permission.AGENT_READ]
        )
    ),
):
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🤖 AGENTS] get_agent_start",
        extra={
            "user_id": getattr(user, "id", None),
            "agent_id": agent_id,
        },
    )

    results = await agent_service.get_agent(agent_id, user)

    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🤖 AGENTS] get_agent_success",
        extra={
            "user_id": getattr(user, "id", None),
            "agent_id": agent_id,
        },
    )
    return results


@router.patch("/api/v1/agents/id/{agent_id}")
async def update_agent(
    agent_id: str,
    update_request: AgentUpdateRequest,
    request: Request,
    agent_service: AgentService = Depends(get_agent_service),
    user: User = Depends(
        require_roles_and_scopes(
            required_roles=[[Role.USER]], required_permissions=[Permission.AGENT_WRITE]
        )
    ),
):
    """
    Update an existing agent with the provided fields.
    Only fields included in the request will be updated.
    """
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🔄 AGENTS] update_agent_start",
        extra={
            "user_id": getattr(user, "id", None),
            "agent_id": agent_id,
            "update_fields": list(update_request.model_dump(exclude_unset=True).keys()),
        },
    )

    updated_agent = await agent_service.update_agent(request, agent_id, update_request, user)

    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][✅ AGENTS] update_agent_success",
        extra={
            "user_id": getattr(user, "id", None),
            "agent_id": agent_id,
        },
    )
    return updated_agent


@router.delete("/api/v1/agents/id/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: str,
    agent_service: AgentService = Depends(get_agent_service),
    user: User = Depends(
        require_roles_and_scopes(
            required_roles=[[Role.USER]], required_permissions=[Permission.AGENT_DELETE]
        )
    ),
):
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🗑️ AGENTS] delete_agent_start",
        extra={
            "user_id": getattr(user, "id", None),
            "agent_id": agent_id,
        },
    )

    await agent_service.delete_agent(agent_id, user)

    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][✅ AGENTS] delete_agent_success",
        extra={
            "user_id": getattr(user, "id", None),
            "agent_id": agent_id,
        },
    )
    return Response(status_code=204)


@router.get("/api/v1/agents/personalities/{key}/soul")
async def get_soul(
    key: str,
    personality_service: PersonalityService = Depends(get_personality_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.USER]])),
):
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][💫 PERSONALITY] get_soul_start",
        extra={
            "user_id": getattr(user, "id", None),
            "personality_key": key,
        },
    )

    try:
        soul = personality_service.extract_soul_for_client(key, user)
    except KeyError as exc:
        raise HTTPException(404, "Personality unavailable") from exc
    result = soul.minimal_dump()

    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][✅ PERSONALITY] get_soul_success",
        extra={
            "user_id": getattr(user, "id", None),
            "personality_key": key,
            "field_count": len(result) if hasattr(result, "__len__") else None,
        },
    )
    return result


@router.get("/api/v1/agents/cognitive-modules/keys")
async def list_cognitive_module_keys(
    request: Request,
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.USER]])),
):
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🧠 COGNITIVE] list_module_keys_start",
        extra={
            "user_id": getattr(user, "id", None),
        },
    )

    cm = request.app.state.cognitive_modules
    defaults = request.app.state.cognitive_defaults

    result = {
        category: {"options": list(backends.keys()), "default": defaults[category]}
        for category, backends in cm.items()
    }

    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][✅ COGNITIVE] list_module_keys_success",
        extra={
            "user_id": getattr(user, "id", None),
            "category_count": len(result),
            "categories": list(result.keys()),
        },
    )

    return result


@router.get("/api/v1/agents/soul-protocol/schema")
def get_agent_soul_protocol_schema(
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.USER]])),
):
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🧬 SOUL] schema_request",
        extra={
            "user_id": getattr(user, "id", None),
        },
    )

    schema = AgentSoulProtocol().model_json_schema()

    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][✅ SOUL] schema_success",
        extra={
            "user_id": getattr(user, "id", None),
            "schema_top_keys": list(schema.keys()) if isinstance(schema, dict) else [],
        },
    )
    return schema


@router.get("/api/v1/agents/cognitive-modules/llm")
def list_llm(request: Request):
    llms = list(request.app.state.cognitive_modules["llm"].keys())

    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🧠 LLM] list_llm_success",
        extra={
            "llm_count": len(llms),
            "llms": llms,
        },
    )
    return llms


@router.get("/api/v1/agents/registry/{type}")
async def get_agent_types(
    type: str,
    registry_service: RegistryService = Depends(get_registry_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.USER]])),
):
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][📚 REGISTRY] list_registry_start",
        extra={
            "user_id": getattr(user, "id", None),
            "registry_type": type,
        },
    )

    try:
        results = registry_service.list_for_client(type, user, include_metadata=True)
    except KeyError as exc:
        raise HTTPException(404, "Registry unavailable") from exc

    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][✅ REGISTRY] list_registry_success",
        extra={
            "user_id": getattr(user, "id", None),
            "registry_type": type,
            "result_count": len(results) if hasattr(results, "__len__") else None,
        },
    )

    return results


@router.get("/api/v1/agents/registries/{registry_type}/entry/{key}")
async def get_registry_entry_details(
    registry_type: str,
    key: str,
    registry_service: RegistryService = Depends(get_registry_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.USER]])),
):
    """Retrieve full details of a specific registry entry, addressed by canonical key."""
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][📘 REGISTRY] get_entry_start",
        extra={
            "user_id": getattr(user, "id", None),
            "registry_type": registry_type,
            "key": key,
        },
    )

    try:
        component_cls = registry_service.get_info_for_client(registry_type, key, user)
        result = component_cls.model_dump()

        AGENTVERSE_LOGGER.info(
            LogDomain.COMMAND_ROOM,
            "[🌐 API][✅ REGISTRY] get_entry_success",
            extra={
                "user_id": getattr(user, "id", None),
                "registry_type": registry_type,
                "key": key,
                "field_count": len(result) if hasattr(result, "__len__") else None,
            },
        )
        return result

    except ValueError as ve:
        AGENTVERSE_LOGGER.warning(
            LogDomain.COMMAND_ROOM,
            "[🌐 API][⚠️ REGISTRY] get_entry_value_error",
            extra={
                "user_id": getattr(user, "id", None),
                "registry_type": registry_type,
                "key": key,
                "error": str(ve),
            },
        )
        return {"error": str(ve)}

    except KeyError:
        AGENTVERSE_LOGGER.warning(
            LogDomain.COMMAND_ROOM,
            "[🌐 API][❌ REGISTRY] get_entry_missing",
            extra={
                "user_id": getattr(user, "id", None),
                "registry_type": registry_type,
                "key": key,
            },
        )
        return {"error": f"Entry with key '{key}' not found in registry '{registry_type}'."}

    except Exception as e:
        AGENTVERSE_LOGGER.error(
            LogDomain.COMMAND_ROOM,
            "[🌐 API][💥 REGISTRY] get_entry_failed",
            extra={
                "user_id": getattr(user, "id", None),
                "registry_type": registry_type,
                "key": key,
                "error_type": type(e).__name__,
                "error": str(e),
            },
            exc_info=True,
        )
        return {"error": "An unexpected internal error occurred."}
