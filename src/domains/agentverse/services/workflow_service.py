"""
WorkflowService — CRUD + resolution for the persisted workflow catalog.

Mongo is the source of truth (collection ``coll_workflows``). Workflows are
readable through UTU client catalog policy; runtime resolution is trusted and
remains separate from discovery/read authorization. Create/update/delete are gated at the REST layer
(ADMIN). ``key`` is the immutable foreign reference held by ``Agent.workflows``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from pymongo.errors import DuplicateKeyError

from src.base.entities.user import User
from src.domains.agentverse.entities.resource_ownership import verified_tenant
from src.domains.agentverse.entities.workflow import (
    WorkflowCreateRequest,
    WorkflowDocument,
    WorkflowUpdateRequest,
    WorkflowValidationResult,
    slugify_workflow_key,
)
from src.domains.agentverse.exceptions import (
    WorkflowInUseError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain
from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.services.registry_service import RegistryService
from src.domains.agentverse.security.manifest_projection import project_authoring_workflow
from pydantic import ValidationError
from src.domains.agentverse.tools.workflow_tool.models import ResolvedWorkflow, WorkflowDefinition
from src.domains.agentverse.utils.canonicalize import canonicalize
from src.domains.agentverse.workflows.helpers.validate_definition import (
    validate_workflow_definition,
)
from src.domains.agentverse.workflows.spec import WorkflowInputField


class WorkflowService:
    """Service for workflow catalog CRUD + runtime resolution."""

    def __init__(self, db_service: DBService):
        self.db_service = db_service

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    _slugify = staticmethod(slugify_workflow_key)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    async def _find_doc(self, key: str) -> dict | None:
        return await self.db_service.find_one({"key": key}, collection_name=DBService.WORKFLOWS_COL)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_workflow(
        self,
        definition: dict,
        *,
        inputs: list[WorkflowInputField] | None = None,
    ) -> WorkflowValidationResult:
        """Dry-run validation; never persists, never raises for validation issues."""
        return validate_workflow_definition(definition, inputs=inputs)

    def _assert_valid(self, definition: dict) -> list[str]:
        """Raise unless ``definition`` is valid; return the tool keys its steps invoke.

        The stored ``required_tools`` is derived, never declared, so it cannot drift
        from the DAG: it is exactly what the agent factory equips at workflow scope,
        and returning it from the one function that already parsed the definition
        keeps that true by construction without a second parse.
        """
        result = self.validate_workflow(definition)
        if not result.valid:
            raise WorkflowValidationError(
                message="Workflow definition failed validation",
                errors=[issue.model_dump() for issue in result.errors],
            )
        return result.required_tools

    # ------------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------------

    async def create_workflow(
        self,
        req: WorkflowCreateRequest,
        user: User,
    ) -> WorkflowDocument:
        if verified_tenant(user) is None:
            raise WorkflowValidationError(
                message="Authenticated identity with verified tenant required"
            )
        key = self._slugify(req.key or req.name)
        if not key:
            raise WorkflowValidationError(message="Workflow key/name resolved to an empty slug")

        existing = await self._find_doc(key)
        if existing is not None:
            raise WorkflowValidationError(
                message=f"Workflow '{key}' already exists",
                errors=[{"code": "DUPLICATE_KEY", "message": key}],
            )

        required_tools = self._assert_valid(req.definition)

        now = self._now()
        doc = WorkflowDocument(
            key=key,
            ownership="tenant",
            tenant_id=user.tenant_id,
            name=req.name,
            description=req.description,
            version=req.version,
            definition=req.definition,
            required_tools=required_tools,
            tags=req.tags,
            inputs=req.inputs,
            ui=req.ui,
            origin="db",
            created_by=user.id,
            updated_by=user.id,
            created_at=now,
            updated_at=now,
        )

        # Construct the response before writing: an unsupported/private manifest
        # must not produce a successful mutation followed by a projection error.
        client_doc = self._client_document(doc.model_dump(mode="json"), user)

        # The pre-check above is racy; the unique `key` index is the real guard.
        # Translate a lost race into the same validation error as the pre-check.
        try:
            await self.db_service.repository.create(doc.model_dump(), DBService.WORKFLOWS_COL)
        except DuplicateKeyError:
            raise WorkflowValidationError(
                message=f"Workflow '{key}' already exists",
                errors=[{"code": "DUPLICATE_KEY", "message": key}],
            ) from None

        AGENTVERSE_LOGGER.info(
            LogDomain.NERV_HQ,
            "[🧭 WORKFLOW][🏗️ CREATE] workflow_created",
            extra={"workflow_key": key, "created_by": user.id},
        )
        return client_doc

    # ------------------------------------------------------------------
    # READ
    # ------------------------------------------------------------------

    @staticmethod
    def _client_readable(
        doc: dict, user: User | None, *, operation: Literal["DISCOVER", "READ"] = "READ"
    ) -> bool:
        from src.domains.agentverse.registries.registries import registries

        return RegistryService(registries).client_metadata_allowed(doc, user, operation=operation)

    @staticmethod
    def _client_document(doc: dict, user: User | None) -> WorkflowDocument:
        if not WorkflowService._client_readable(doc, user):
            raise WorkflowNotFoundError(doc.get("key", ""))
        try:
            return WorkflowDocument.model_validate(project_authoring_workflow(doc))
        except (ValidationError, TypeError, ValueError, KeyError):
            raise WorkflowNotFoundError(doc.get("key", "")) from None

    async def get_workflow(self, key: str, user: User | None = None) -> WorkflowDocument:
        key = self._slugify(key)
        doc = await self._find_doc(key)
        if not doc or not self._client_readable(doc, user):
            raise WorkflowNotFoundError(key)
        return self._client_document(doc, user)

    async def list_workflows(self, user: User | None = None) -> list[WorkflowDocument]:
        docs = await self.db_service.find_all(collection_name=DBService.WORKFLOWS_COL)
        result = []
        for doc in docs:
            if not self._client_readable(doc, user, operation="DISCOVER"):
                continue
            try:
                result.append(self._client_document(doc, user))
            except WorkflowNotFoundError:
                continue
        return result

    async def get_workflows(self, keys: Iterable[str]) -> tuple[list[WorkflowDocument], list[str]]:
        """Fetch every key in one query, returning ``(found, missing)``.

        Callers own the policy for a key with no catalog row, because the right
        answer differs by moment: attach time rejects the save, run time skips it
        so a since-deleted workflow cannot brick an agent's other ones. Keys are
        slugified and deduplicated, and ``found`` preserves the caller's order.
        """
        slugs = list(dict.fromkeys(self._slugify(k) for k in keys if k))
        if not slugs:
            return [], []

        rows: list[dict] = await self.db_service.find_all(
            {"key": {"$in": slugs}}, collection_name=DBService.WORKFLOWS_COL
        )
        docs: dict[str, dict] = {row["key"]: row for row in rows}
        found = [WorkflowDocument(**docs[slug]) for slug in slugs if slug in docs]
        missing = [slug for slug in slugs if slug not in docs]
        return found, missing

    @staticmethod
    def required_tools_of(workflows: Iterable[WorkflowDocument]) -> set[str]:
        """Union of the tool keys the given workflows invoke."""
        return {canonicalize(t) for wf in workflows for t in (wf.required_tools or []) if t}

    async def required_tools_for(self, keys: Iterable[str]) -> set[str]:
        """Union of the tool keys the given workflow keys invoke.

        A key with no catalog row is logged and skipped rather than raising: an
        agent attached to a since-deleted workflow keeps its other workflows
        runnable, and running the missing one fails with its own clear error.
        """
        found, missing = await self.get_workflows(keys)
        for wf_key in missing:
            AGENTVERSE_LOGGER.warning(
                LogDomain.NERV_HQ,
                "[🧭 WORKFLOW][🔎 LOOKUP] attached_workflow_missing",
                extra={"workflow_key": wf_key},
            )
        return self.required_tools_of(found)

    # ------------------------------------------------------------------
    # UPDATE
    # ------------------------------------------------------------------

    async def update_workflow(
        self,
        key: str,
        req: WorkflowUpdateRequest,
        user: User,
    ) -> WorkflowDocument:
        key = self._slugify(key)
        existing = await self._find_doc(key)
        if (
            not existing
            or existing.get("ownership") != "tenant"
            or not self._client_readable(existing, user)
        ):
            raise WorkflowNotFoundError(key)

        update_data = req.model_dump_for_update()

        # The definition is the only input to required_tools, so it is the only
        # trigger: re-validating re-derives the stored tool set, which a new step
        # may have widened.
        if "definition" in update_data:
            update_data["required_tools"] = self._assert_valid(update_data["definition"])

        if not update_data:
            return self._client_document(existing, user)

        update_data["updated_by"] = user.id
        update_data["updated_at"] = self._now()

        self._client_document({**existing, **update_data}, user)

        await self.db_service.repository.update(
            query={"key": key},
            data=update_data,
            collection_name=DBService.WORKFLOWS_COL,
        )

        refreshed = await self._find_doc(key)
        if not refreshed:
            # The row disappeared between the update and the re-fetch
            # (e.g. a concurrent delete). Surface a domain error, not a TypeError.
            raise WorkflowNotFoundError(key)

        AGENTVERSE_LOGGER.info(
            LogDomain.NERV_HQ,
            "[🧭 WORKFLOW][🔄 UPDATE] workflow_updated",
            extra={
                "workflow_key": key,
                "updated_by": user.id,
                "updated_fields": list(update_data.keys()),
            },
        )
        return self._client_document(refreshed, user)

    # ------------------------------------------------------------------
    # DELETE
    # ------------------------------------------------------------------

    async def delete_workflow(self, key: str, user: User) -> None:
        key = self._slugify(key)
        existing = await self._find_doc(key)
        if (
            not existing
            or existing.get("ownership") != "tenant"
            or not self._client_readable(existing, user)
        ):
            raise WorkflowNotFoundError(key)

        pinned = await self._agents_pinned_to(key)
        if pinned:
            raise WorkflowInUseError(key, pinned)

        await self.db_service.repository.delete({"key": key}, DBService.WORKFLOWS_COL)

        AGENTVERSE_LOGGER.info(
            LogDomain.NERV_HQ,
            "[🧭 WORKFLOW][🗑️ DELETE] workflow_deleted",
            extra={"workflow_key": key, "deleted_by": user.id},
        )

    async def _agents_pinned_to(self, key: str) -> list[str]:
        """Return ids of agents that have ``key`` among their attached workflows."""
        query = {"workflows": key}
        agents = await self.db_service.find_all(query=query, collection_name=DBService.AGENTS_COL)
        return [a.get("id") for a in agents if a.get("id")]

    # ------------------------------------------------------------------
    # RESOLUTION (runtime — replaces the registry fallback)
    # ------------------------------------------------------------------

    async def resolve_definition(self, key: str) -> ResolvedWorkflow:
        """Live Mongo lookup that resolves a workflow key to an executable
        ``WorkflowDefinition`` plus the row's display ``name`` and declared run
        inputs. Raises ``WorkflowNotFoundError`` if absent."""
        key = self._slugify(key)
        doc = await self._find_doc(key)
        if not doc:
            raise WorkflowNotFoundError(key)

        inputs = [WorkflowInputField.model_validate(i) for i in doc.get("inputs") or []]
        return ResolvedWorkflow(
            definition=WorkflowDefinition.model_validate(doc["definition"]),
            name=doc.get("name"),
            inputs=inputs,
        )
