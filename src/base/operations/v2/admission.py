"""OPS2-04C.1 admission contracts: durable descriptors, never execution permission.

A descriptor records *what* was admitted and *under which verified authority* it was
admitted. It holds no permit, credential, token or executable object and cannot be
turned back into one. Later execution must re-establish authority through UTU.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    model_validator,
)

from src.base.operations.v2.models import NonBlankText, OperationRecord

if TYPE_CHECKING:
    from src.base.operations.v2.managed import ManagedOperation

ADMISSION_PRODUCER = "ops.admission"
MAX_INLINE_INPUT_BYTES = 64 * 1024
MAX_DISCOVERY_LIMIT = 100
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SIGNAL = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_DISCOVERY_KEY = re.compile(r"\d{20}:[0-9a-f-]{36}")


class UnknownCapability(LookupError):
    """No registered provider matches the requested capability and exact version."""


class UnapprovedRecoveryPolicy(ValueError):
    """The requested recovery policy is not approved for admission."""


class InvalidAdmission(ValueError):
    """Malformed admission request, input or idempotency key."""


class IdempotencyConflict(ValueError):
    """The idempotency key was already used for a different request in this scope."""


def _bounded(value: str) -> str:
    if len(value) > 256 or value != value.strip():
        raise ValueError("Identifiers must be unpadded and at most 256 characters")
    return value


def _signal(value: str) -> str:
    if not _SIGNAL.fullmatch(value):
        raise ValueError(f"Invalid capability signal: {value!r}")
    return value


def _digest(value: str) -> str:
    if not _DIGEST.fullmatch(value):
        raise ValueError("Expected a lowercase SHA-256 hex digest")
    return value


Identifier = Annotated[NonBlankText, AfterValidator(_bounded)]
Signal = Annotated[StrictStr, AfterValidator(_signal)]
Digest = Annotated[StrictStr, AfterValidator(_digest)]


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class CapabilityRef(_Frozen):
    """Exact registered capability and the approved provider/executable resolved for it.

    Metadata only: it neither proves the executable is still deployed nor grants invocation.
    """

    signal: Signal
    version: Identifier
    provider: Identifier
    executable: Identifier


class InlineInput(_Frozen):
    """Validated JSON object stored as immutable canonical text with its digest."""

    kind: Literal["inline"]
    canonical: StrictStr
    digest: Digest

    @classmethod
    def from_value(cls, value: dict[str, Any]) -> Self:
        if not isinstance(value, dict):
            raise InvalidAdmission("Inline execution input must be a JSON object")
        try:
            text = canonical_json(value)
            if json.loads(text) != value:
                raise ValueError("Input does not round-trip as JSON")
        except (TypeError, ValueError) as exc:
            raise InvalidAdmission("Execution input must be plain JSON") from exc
        return cls(kind="inline", canonical=text, digest=hashlib.sha256(text.encode()).hexdigest())

    @model_validator(mode="after")
    def consistent_input(self) -> Self:
        if len(self.canonical.encode()) > MAX_INLINE_INPUT_BYTES:
            raise ValueError("Inline execution input exceeds the admission bound")
        value = json.loads(self.canonical)
        if not isinstance(value, dict) or canonical_json(value) != self.canonical:
            raise ValueError("Inline input must be a canonical JSON object")
        if hashlib.sha256(self.canonical.encode()).hexdigest() != self.digest:
            raise ValueError("Inline input digest mismatch")
        return self

    @property
    def value(self) -> dict[str, Any]:
        return json.loads(self.canonical)  # Fresh copy; the stored text stays immutable.


class InputReference(_Frozen):
    """Content-addressed reference; never a signed or credential-bearing URL."""

    kind: Literal["reference"]
    uri: Annotated[NonBlankText, Field(max_length=2048)]
    digest: Digest
    size_bytes: Annotated[int, Field(strict=True, ge=0)]

    @model_validator(mode="after")
    def no_embedded_credentials(self) -> Self:
        parts = urlsplit(self.uri)
        if not parts.scheme or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("Input references require a scheme and no userinfo/query/fragment")
        return self


ExecutionInput = Annotated[InlineInput | InputReference, Field(discriminator="kind")]


class AuthorizationProvenance(_Frozen):
    """Evidence of the admission-time UTU decision, not a permit or grant.

    ``evidence_digest`` is a one-way digest of the verified permit scope and versions;
    it cannot be reconstructed into a permit. Session identifiers are not persisted.
    """

    authority: Literal["utu"]
    tenant_id: Identifier
    principal_id: Identifier
    subject_id: Identifier
    policy_version: Identifier
    evidence_digest: Digest


class RecoveryPolicyRef(_Frozen):
    name: Annotated[StrictStr, AfterValidator(_signal)]
    version: Annotated[int, Field(strict=True, ge=1)]


# Unresolved work is reconciled or manually reviewed; never blindly replayed.
MANUAL_REVIEW_V1 = RecoveryPolicyRef(name="ops.recovery.manual_review", version=1)
APPROVED_RECOVERY_POLICIES = frozenset({MANUAL_REVIEW_V1})


class ExecutionDescriptor(_Frozen):
    """Immutable, versioned description of admitted work. It grants nothing."""

    descriptor_version: Literal[1]
    capability: CapabilityRef
    input: ExecutionInput
    tenant_id: Identifier
    requester_id: Identifier
    authorization: AuthorizationProvenance
    recovery_policy: RecoveryPolicyRef
    correlation_id: Identifier
    causation_id: Identifier | None

    @model_validator(mode="after")
    def trusted_identity_agrees(self) -> Self:
        if (self.tenant_id, self.requester_id) != (
            self.authorization.tenant_id,
            self.authorization.principal_id,
        ):
            raise ValueError("Descriptor identity must match its authorization provenance")
        return self

    @property
    def digest(self) -> str:
        return sha256_hex(self.model_dump(mode="json"))


def request_digest(descriptor: ExecutionDescriptor) -> str:
    """Idempotency comparison: the requested work, not per-attempt lineage or evidence.

    Uses the requested signal@version, not the resolved provider/executable, so a retry
    after an ambiguous write and a redeploy still resolves to the original admission.
    """
    return sha256_hex(
        {
            "capability": [descriptor.capability.signal, descriptor.capability.version],
            "input": descriptor.input.digest,
            "recovery_policy": descriptor.recovery_policy.model_dump(mode="json"),
        }
    )


def idempotency_scope(tenant_id: str, requester_id: str, key: str) -> str:
    if not isinstance(key, str) or not 1 <= len(key) <= 200 or key != key.strip():
        raise InvalidAdmission("Idempotency key must be 1..200 unpadded characters")
    return sha256_hex({"tenant": tenant_id, "requester": requester_id, "key": key})


def discovery_key(operation: OperationRecord) -> str:
    """Fixed-width creation-time ordering key; fairness only, never eligibility."""
    micros = (operation.created_at - _EPOCH) // timedelta(microseconds=1)
    if micros < 0:
        raise InvalidAdmission("Admission time precedes the Unix epoch")
    return f"{micros:020d}:{operation.operation_id}"


class AdmissionRecord(_Frozen):
    descriptor: ExecutionDescriptor
    idempotency_scope: Digest
    discovery_key: Annotated[StrictStr, Field(pattern=_DISCOVERY_KEY.pattern)]


class DispatchCandidate(_Frozen):
    """Reference to pending work; discovery is not a claim or execution grant."""

    operation_id: UUID
    tenant_id: Identifier
    signal: Signal
    capability_version: Identifier
    correlation_id: Identifier
    causation_id: Identifier | None
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    expected_generation: Annotated[int, Field(strict=True, ge=0)]
    discovery_key: Annotated[StrictStr, Field(pattern=_DISCOVERY_KEY.pattern)]


class DiscoveryRequest(_Frozen):
    contract_version: Literal[1] = 1
    limit: Annotated[int, Field(strict=True, ge=1, le=MAX_DISCOVERY_LIMIT)] = 25
    after: Annotated[StrictStr, Field(pattern=_DISCOVERY_KEY.pattern)] | None = None


class DiscoveryPage(_Frozen):
    contract_version: Literal[1] = 1
    candidates: tuple[DispatchCandidate, ...]
    next_after: Annotated[StrictStr, Field(pattern=_DISCOVERY_KEY.pattern)] | None


class InputReferenceVerifier(Protocol):
    """Trusted store adapter required before any reference can be admitted.

    Must confirm, for the trusted tenant, that the object exists in an approved immutable
    store, is readable by that tenant and matches ``digest``/``size_bytes``, and that it
    will be retained for the operation's lifetime. Raise to reject; never fetch arbitrary
    URIs. No implementation exists yet, so references are rejected by default.
    """

    async def verify(self, reference: InputReference, *, tenant_id: str) -> None: ...


class CapabilityCatalog(Protocol):
    """Port over the existing capability registry; resolution is not authorization."""

    def resolve(self, signal: str, version: str) -> CapabilityRef: ...


class OperationAdmissionRepository(Protocol):
    async def admit(
        self, operation: OperationRecord, admission: AdmissionRecord
    ) -> tuple[ManagedOperation, bool]: ...

    async def discover(
        self, request: DiscoveryRequest, *, tenant_id: str | None = None
    ) -> DiscoveryPage: ...
