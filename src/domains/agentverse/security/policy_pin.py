"""Explicit approved UTU policy pin for managed local startup (POLICY-PIN-0.1).

Startup and every Uvicorn reload re-run local initialization. Before POLICY-PIN-0.1 that
installed whatever checked-in policy the process loaded, so an accidental start (or a
reload triggered by any source edit) turned an unreviewed policy edit into installed
authority. The pin makes installation an explicit, reviewed decision:

- the effective source policy (``policy_version``, the existing UTU digest) must equal
  ``approved_policy_version``;
- the installed tenant policy must already equal it. The only installs are a first
  install into an absent tenant policy (only with ``provision_unprovisioned``), or an
  explicit transition from exactly ``transition_from``, both through the existing
  compare-and-set;
- anything else fails closed before any Redis write, and never overwrites a mismatch.

Startup logs its outcome. That log is operational observability only, not durable
authorization provenance (UTU remains the sole policy authority; nothing is recorded).

The pin is a server-owned file next to the policy file (``<policy>.pin.json``), or
``GENESIS_POLICY_PIN_FILE``. It grants nothing: grants still come only from the policy.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError, model_validator

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.security.revocation import policy_version

if TYPE_CHECKING:
    from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
    from src.domains.agentverse.security.revocation import RevocationStore
    from src.domains.agentverse.security.utu import UTUAuthority

PIN_CONTRACT = "caos.utu_policy_pin"
PIN_FILE_ENV = "GENESIS_POLICY_PIN_FILE"
_DIGEST = re.compile(r"[0-9a-f]{64}")
PinOutcome = Literal["verified", "provisioned", "transitioned"]
logger = logging.getLogger(__name__)


class PolicyPinError(ValueError):
    """Startup refused: the pin is missing, invalid or does not authorize the state."""


class PolicyPin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: Literal["caos.utu_policy_pin"]
    schema_version: Literal[1]
    tenant_id: str
    approved_policy_version: str
    # The exact installed version this pin authorizes replacing (one reviewed upgrade
    # or rollback). ``None``: no transition is authorized.
    transition_from: str | None = None
    # Explicit first-install consent: an ABSENT installed policy may receive the approved
    # version. Never set for a managed tenant that is already provisioned: a missing policy
    # there means loss, corruption or the wrong Redis, and startup must refuse.
    provision_unprovisioned: StrictBool = False

    @model_validator(mode="after")
    def _exact(self) -> PolicyPin:
        for value in (self.approved_policy_version, self.transition_from):
            if value is not None and not _DIGEST.fullmatch(value):
                raise ValueError("Policy versions are exact 64-character hex digests")
        if self.transition_from == self.approved_policy_version:
            raise ValueError("transition_from must differ from approved_policy_version")
        if not self.tenant_id:
            raise ValueError("tenant_id is required")
        return self


def pin_path(policy_file: str | None) -> Path:
    override = os.environ.get(PIN_FILE_ENV)
    if override:
        return Path(override)
    if not policy_file:
        raise PolicyPinError(
            "Local startup requires GENESIS_SECURITY_POLICY_FILE and its approved policy pin"
        )
    path = Path(policy_file)
    return path.with_name(path.stem + ".pin.json")


def load_pin(policy_file: str | None) -> tuple[PolicyPin, Path]:
    """Read the pin on every call (startup and each reload): never cached."""
    path = pin_path(policy_file)
    try:
        text = path.read_text()
    except OSError as exc:
        raise PolicyPinError(
            f"Approved policy pin {path} is required for local startup and is unreadable"
        ) from exc
    try:
        return PolicyPin.model_validate_json(text), path
    except ValidationError as exc:
        raise PolicyPinError(f"Approved policy pin {path} is invalid") from exc


def _short(value: str | None) -> str:
    return "none" if value is None else value[:12] + "…"


async def enforce_policy_pin(
    *,
    store: RevocationStore,
    utu: UTUAuthority,
    policy: SynapticAuthorization,
    pin: PolicyPin,
    tenant_id: str,
    source: Path | None = None,
) -> PinOutcome:
    """Verify, or install exactly as the pin authorizes. Raises before any write."""
    where = f" ({source})" if source else ""
    if pin.tenant_id != tenant_id:
        raise PolicyPinError(f"Approved policy pin{where} is for another tenant")
    checked_in = policy_version(policy)
    if checked_in != pin.approved_policy_version:
        raise PolicyPinError(
            f"Checked-in policy {_short(checked_in)} is not the approved pin "
            f"{_short(pin.approved_policy_version)}{where}; nothing was installed. Review the "
            "policy change, then update approved_policy_version deliberately."
        )
    raw = await store.current_policy(tenant_id)
    installed = raw.split(":")[-1] if raw else None
    approved = pin.approved_policy_version
    if installed == approved:
        _log("verified", tenant_id, installed, approved)
        return "verified"
    if installed is None and not pin.provision_unprovisioned:
        raise PolicyPinError(
            f"Tenant {tenant_id} has no installed UTU policy and the approved pin{where} "
            "does not authorize a first install; nothing was installed. A managed tenant's "
            "missing policy indicates loss, corruption or the wrong Redis: investigate. Set "
            "provision_unprovisioned only for a genuinely new tenant."
        )
    if installed is not None and installed != pin.transition_from:
        raise PolicyPinError(
            f"Installed policy {_short(installed)} is not the approved pin "
            f"{_short(approved)}{where} and no transition from it is authorized; nothing was "
            "installed. To replace it, set transition_from to the exact installed version."
        )
    # A first install, or the one authorized transition: the existing CAS (expected is
    # the exact stored value), so a concurrent change fails and keeps prior authority.
    try:
        await utu.install_policy(tenant_id, expected=raw, authorization=policy)
    except EventPermissionError as exc:
        raise PolicyPinError(
            f"Installing approved policy {_short(approved)} over {_short(installed)} for "
            f"tenant {tenant_id} did not complete ({exc}); the installed policy changed "
            "concurrently or UTU is unavailable, and the prior authority is kept. Inspect "
            "with Nataraja before retrying."
        ) from exc
    outcome: PinOutcome = "provisioned" if installed is None else "transitioned"
    _log(outcome, tenant_id, installed, approved)
    return outcome


def _log(outcome: PinOutcome, tenant_id: str, installed: str | None, approved: str) -> None:
    # Operational observability only (digests are not secrets); not durable provenance.
    logger.info(
        "UTU policy pin %s: tenant=%s installed=%s approved=%s",
        outcome,
        tenant_id,
        _short(installed),
        _short(approved),
    )
