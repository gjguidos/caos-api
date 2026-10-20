"""
Seeding of builtin workflows into Mongo.

Builtin workflows are always upserted on startup, so updates to the workflow
spec are automatically reflected in the database.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.domains.agentverse.entities.workflow import WorkflowDocument, slugify_workflow_key
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain
from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.workflows.builtins import BUILTIN_WORKFLOWS
from src.domains.agentverse.workflows.helpers.validate_definition import (
    validate_workflow_definition,
)


async def ensure_workflow_indexes(db_service: DBService) -> None:
    """Create the unique ``key`` index (idempotent)."""
    collection = await db_service.repository.get_collection(DBService.WORKFLOWS_COL)
    await collection.create_index("key", unique=True)


async def seed_builtin_workflows(db_service: DBService) -> int:
    """Upsert all builtin workflows. Returns the count seeded."""
    await ensure_workflow_indexes(db_service)

    now = datetime.now(UTC).isoformat()
    seeded = 0

    for factory in BUILTIN_WORKFLOWS:
        spec = factory()
        key = slugify_workflow_key(spec.key)

        # Builtins are code, so an invalid one is a bug rather than user input:
        # seed the row regardless and let the run-time gates report, but take the
        # derived tool set whenever validation got far enough to resolve it.
        result = validate_workflow_definition(spec.definition)
        if not result.valid:
            AGENTVERSE_LOGGER.warning(
                LogDomain.NERV_HQ,
                "[🧭 WORKFLOW][🌱 SEED] builtin_definition_invalid",
                extra={
                    "workflow_key": key,
                    "errors": [issue.code for issue in result.errors],
                },
            )

        doc = WorkflowDocument.from_spec(
            spec, origin="builtin", now=now, required_tools=result.required_tools
        )

        await db_service.repository.upsert(
            query={"key": key},
            data=doc.model_dump(),
            collection_name=DBService.WORKFLOWS_COL,
        )
        seeded += 1
        AGENTVERSE_LOGGER.info(
            LogDomain.NERV_HQ,
            "[🧭 WORKFLOW][🌱 SEED] builtin_seeded",
            extra={"workflow_key": key},
        )

    AGENTVERSE_LOGGER.info(
        LogDomain.NERV_HQ,
        "[🧭 WORKFLOW][🌱 SEED] seed_complete",
        extra={"seeded": seeded, "total_builtins": len(BUILTIN_WORKFLOWS)},
    )
    return seeded
