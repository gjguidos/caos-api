"""Atomic single-document admission and bounded pending discovery in ``operations_v2``.

MongoDB is authoritative. Admission is one ``insert_one`` of a complete schema-3
aggregate; discovery reads partial indexes containing only pending admitted work.
Neither path claims, activates, executes or authorizes anything.
"""

from typing import Any

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.base.operations.v2.admission import (
    AdmissionRecord,
    DiscoveryPage,
    DiscoveryRequest,
    DispatchCandidate,
    IdempotencyConflict,
    InvalidAdmission,
    request_digest,
)
from src.base.operations.v2.managed import (
    AmbiguousManagedWrite,
    ExecutionEpoch,
    InvalidManagedRequest,
    ManagedOperation,
)
from src.base.operations.v2.models import OperationRecord, OperationState
from src.base.operations.v2.mongo_managed_repository import (
    ADMITTED_SCHEMA_VERSION,
    managed_collection,
    managed_from_document,
    managed_to_document,
)
from src.base.operations.v2.mongo_repository import COLLECTION_NAME
from src.base.operations.v2.repository import DuplicateOperation, validate_snapshot
from src.base.repositories.mongodb_repository import MongoDBRepository

IDEMPOTENCY_INDEX = "ops_v2_admission_idempotency"
PENDING_INDEX = "ops_v2_pending_dispatch"
TENANT_PENDING_INDEX = "ops_v2_pending_dispatch_tenant"

# Leaving pending (activation/terminal failure) removes the entry from both indexes.
_PENDING = {
    "schema_version": ADMITTED_SCHEMA_VERSION,
    "snapshot.state": OperationState.PENDING.value,
}
_TENANT = "admission.descriptor.tenant_id"
_KEY = "admission.discovery_key"
_SCOPE = "admission.idempotency_scope"
_CANDIDATE_FIELDS = {
    "_id": 1,
    _TENANT: 1,
    _KEY: 1,
    "admission.descriptor.capability.signal": 1,
    "admission.descriptor.capability.version": 1,
    "admission.descriptor.correlation_id": 1,
    "admission.descriptor.causation_id": 1,
    "snapshot.revision": 1,
    "execution.generation": 1,
}


class MongoOperationAdmissionRepository:
    """No claim, worker, scheduler, notification state, migration or write retry."""

    def __init__(
        self, mongodb_repository: MongoDBRepository, collection_name: str = COLLECTION_NAME
    ) -> None:
        self._mongodb_repository = mongodb_repository
        self._collection_name = collection_name
        self._indexed = False

    async def _collection(self) -> Any:
        collection = await self._mongodb_repository.get_collection(self._collection_name)
        return managed_collection(collection)

    async def ensure_indexes(self) -> None:
        """Idempotent; conflicting existing definitions fail closed."""
        collection = await self._collection()
        await collection.create_index(
            [(_SCOPE, ASCENDING)],
            name=IDEMPOTENCY_INDEX,
            unique=True,
            partialFilterExpression={_SCOPE: {"$exists": True}},
        )
        await collection.create_index(
            [(_KEY, ASCENDING)], name=PENDING_INDEX, partialFilterExpression=_PENDING
        )
        await collection.create_index(
            [(_TENANT, ASCENDING), (_KEY, ASCENDING)],
            name=TENANT_PENDING_INDEX,
            partialFilterExpression=_PENDING,
        )
        self._indexed = True

    async def admit(
        self, operation: OperationRecord, admission: AdmissionRecord
    ) -> tuple[ManagedOperation, bool]:
        """Insert pending work, or return the same-scope same-request admission.

        Returns ``(aggregate, created)``. A different request under the same scope raises
        IdempotencyConflict. Database errors are ambiguous: repeat with the same key.
        """
        operation = validate_snapshot(operation)
        if operation.state != OperationState.PENDING:
            raise InvalidManagedRequest("Only new pending operations can be admitted")
        try:
            record = ManagedOperation(
                schema_version=ADMITTED_SCHEMA_VERSION,
                snapshot=operation,
                execution=ExecutionEpoch.idle(),
                last_state_change=None,
                admission=admission,
            )
        except ValueError as exc:
            raise InvalidAdmission("Admission does not match its pending operation") from exc
        if not self._indexed:
            # Scoped idempotency depends on the unique index; never insert without it.
            await self.ensure_indexes()
        collection = await self._collection()
        try:
            await collection.insert_one(managed_to_document(record))
            return record, True
        except DuplicateKeyError as exc:
            if _SCOPE not in ((exc.details or {}).get("keyPattern") or {}):
                raise DuplicateOperation(operation.operation_id) from exc
        except PyMongoError as exc:
            raise AmbiguousManagedWrite(operation.operation_id, None) from exc
        document = await collection.find_one({_SCOPE: admission.idempotency_scope})
        if document is None:
            # The conflicting insert is not yet visible at majority; no blind retry here.
            raise AmbiguousManagedWrite(operation.operation_id, None)
        existing = managed_from_document(document)
        assert existing.admission is not None
        if request_digest(existing.admission.descriptor) != request_digest(admission.descriptor):
            raise IdempotencyConflict("Idempotency key already admitted a different request")
        return existing, False

    async def discover(
        self, request: DiscoveryRequest, *, tenant_id: str | None = None
    ) -> DiscoveryPage:
        """One bounded, index-hinted page of pending admitted work, oldest first.

        Results may be stale on arrival and may repeat across callers or pages:
        eligibility is decided only by fenced activation against current state.
        ``next_after`` is None once a page is not full: the sweep is complete and the
        next one restarts from the oldest key, so work admitted behind a cursor (e.g.
        caller clock skew) is reached on the following sweep rather than starved.
        """
        request = DiscoveryRequest.model_validate(request, strict=True)
        cursor = await self._cursor(request, tenant_id)
        candidates = tuple([_candidate(row) for row in await cursor.to_list(length=request.limit)])
        full = len(candidates) == request.limit
        return DiscoveryPage(
            candidates=candidates, next_after=candidates[-1].discovery_key if full else None
        )

    async def _cursor(self, request: DiscoveryRequest, tenant_id: str | None) -> Any:
        query: dict[str, Any] = dict(_PENDING)
        index = PENDING_INDEX
        if tenant_id is not None:
            if not isinstance(tenant_id, str) or not tenant_id.strip():
                raise InvalidAdmission("Tenant-scoped discovery requires a tenant")
            query[_TENANT] = tenant_id
            index = TENANT_PENDING_INDEX
        if request.after is not None:
            query[_KEY] = {"$gt": request.after}
        collection = await self._collection()
        return (
            collection.find(query, _CANDIDATE_FIELDS)
            .sort([(_KEY, ASCENDING)] if tenant_id is None else [(_TENANT, 1), (_KEY, 1)])
            .hint(index)  # Fails closed if the index is absent rather than scanning.
            .limit(request.limit)
        )


def _candidate(row: dict[str, Any]) -> DispatchCandidate:
    descriptor = row["admission"]["descriptor"]
    return DispatchCandidate(
        operation_id=row["_id"],
        tenant_id=descriptor["tenant_id"],
        signal=descriptor["capability"]["signal"],
        capability_version=descriptor["capability"]["version"],
        correlation_id=descriptor["correlation_id"],
        causation_id=descriptor["causation_id"],
        expected_revision=row["snapshot"]["revision"],
        expected_generation=row["execution"]["generation"],
        discovery_key=row["admission"]["discovery_key"],
    )
