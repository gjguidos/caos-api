import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.dependencies.request_id_dependency import get_request_id
from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.services.divine_orchestration_service import DivineOrchestrationService

logger = logging.getLogger(__name__)


@inject
async def get_divine_orchestration_service(
    request_id: str = Depends(get_request_id),
    divine_orchestration_service: DivineOrchestrationService = Depends(
        Provide[AgentverseContainer.divine_orchestration_service]
    ),
):
    logger.info(f"creating DivnieOrchestrationService with request_id: {request_id}")
    divine_orchestration_service.request_id = request_id
    logger.info(f"DivnieOrchestrationService created {divine_orchestration_service}")
    return divine_orchestration_service


"""
Analysis and explanation of get_divine_orchestration_service.py:

- This file defines an async dependency provider function for FastAPI, used to inject the `DivineOrchestrationService` into routes or event handlers.

Key points:

1. **Imports:**
   - Uses FastAPI's `Depends` for dependency injection.
   - Uses `dependency_injector`'s `Provide` and `inject` for advanced DI.
   - Imports the DI container and the service class.

2. **get_divine_orchestration_service function:**
   - Decorated with `@inject` for dependency injection.
   - Accepts two dependencies:
     - `request_id`: Provided by `get_request_id`, likely a unique ID for the current request/session.
     - `divine_orchestration_service`: Provided by the DI container, which manages the lifecycle and configuration of the service.
   - Sets the `request_id` on the service instance for traceability/logging.
   - Logs the creation and returns the service instance.

**Usage:**
- This function is used as a dependency in other parts of the backend (e.g., event handlers, API routes) to get a properly configured instance of `DivineOrchestrationService` for the current request.

**Summary:**
- This file enables clean, testable, and request-scoped injection of the orchestration service throughout the backend using FastAPI's dependency system and dependency-injector.
"""
