"""Disposable lifecycle verification. Never touches the live environment.

Runs the existing integration suites (real Mongo in throwaway ``caos_test_*``
databases, Redis only at an explicitly provided disposable URL) as separate
processes and reports each lifecycle area. A live Redis target is refused.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit

from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.nataraja.manifest import EnvironmentManifest
from src.domains.agentverse.nataraja.plan import REPORT_CONTRACT, worst

_BASE = "src/domains/agentverse"
SUITES: tuple[tuple[str, str, str], ...] = (
    (
        "bootstrap",
        "JOSHUA → Genesis → Enki bootstrap and repeated bootstrap",
        f"{_BASE}/tests/agents/test_genesis_bootstrap_integration.py",
    ),
    (
        "initiation",
        "JOSHUA initiation, idempotent replay and denied paths",
        f"{_BASE}/tests/agents/test_genesis_initiation_integration.py",
    ),
    (
        "recursive_thoth",
        "Enki → Genesis → Thoth exactly once, Khepri and Seshat causation",
        f"{_BASE}/tests/agents/test_recursive_genesis_integration.py",
    ),
    (
        "lifecycle_separation",
        "Genesis creates sleeping; Khepri activates separately (Enki → Thoth)",
        f"{_BASE}/tests/agents/test_genesis_khepri_lifecycle_integration.py",
    ),
    (
        "nabu_acceptance",
        "Named Nabu rehearsal: G3.3 review/approval → G3.4 creation, sleeping",
        f"{_BASE}/tests/agents/test_agent_creation_integration.py::test_nabu_acceptance_scenario",
    ),
    (
        "authorization",
        "UTU sessions, scoped authority and revocation",
        " ".join(
            f"{_BASE}/agents/lifecycle/tests/{name}.py"
            for name in ("test_scoped_requester", "test_utu_revocation", "test_session_authority")
        ),
    ),
    (
        "review",
        "G3.3 validation gateway, review integrity and approval",
        " ".join(
            f"{_BASE}/tests/agents/{name}.py"
            for name in ("test_validation_gateway_integration", "test_creation_review_integration")
        ),
    ),
    (
        "manual_creation",
        "G3.4 governed manual creation of an isolated test Agent",
        f"{_BASE}/tests/agents/test_agent_creation_integration.py",
    ),
    (
        "nataraja",
        "Read-only inspection against a disposable environment",
        f"{_BASE}/nataraja/tests",
    ),
)
_COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?)")


class UnsafeVerificationTarget(ValueError):
    pass


def _endpoint(url: str) -> tuple[str | None, int, str]:
    parts = urlsplit(url)
    return parts.hostname, parts.port or 6379, parts.path.lstrip("/") or "0"


def require_disposable_redis(url: str | None, live_url: str | None) -> str:
    if not url:
        raise UnsafeVerificationTarget("Provide a disposable Redis URL (--redis-url)")
    if urlsplit(url).scheme not in ("redis", "rediss"):
        raise UnsafeVerificationTarget("Disposable target must be a redis:// URL")
    if live_url and _endpoint(url)[:2] == _endpoint(live_url)[:2]:
        # Same server as the live authority: refuse every database on it.
        raise UnsafeVerificationTarget("Refusing the live Redis server for verification")
    return url


def run_suite(paths: str, redis_url: str, timeout: int = 900) -> dict[str, Any]:
    env = {
        **os.environ,
        "UTU_TEST_REDIS_URL": redis_url,
        "OPS2_MONGO_INTEGRATION": "1",
        "NATARAJA_VERIFY_REDIS_URL": redis_url,
    }
    result = subprocess.run(  # fixed interpreter and repo test paths
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-W",
            "ignore",
            *paths.split(),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    tail = result.stdout.strip().splitlines()[-1:] or [""]
    counts = {
        kind.rstrip("s") if kind.startswith("error") else kind: int(n)
        for n, kind in _COUNT.findall(tail[0])
    }
    return {"exit_code": result.returncode, "summary": tail[0][:200], **counts}


def verify(
    manifest: EnvironmentManifest, *, redis_url: str, live_redis_url: str | None
) -> dict[str, Any]:
    require_disposable_redis(redis_url, live_redis_url)
    resources, findings = [], []
    for key, title, paths in SUITES:
        outcome = run_suite(paths, redis_url)
        if outcome.get("failed") or outcome.get("error") or outcome["exit_code"] not in (0, 5):
            status = "failed"
        elif outcome.get("skipped") or not outcome.get("passed"):
            # Skipped integration means the disposable lifecycle was not exercised.
            status = "blocked"
        else:
            status = "passed"
        resources.append(
            {"kind": "verification", "id": key, "name": title, "status": status, **outcome}
        )
        if status != "passed":
            findings.append(
                {
                    "code": f"verify.{status}",
                    "resource": key,
                    "status": status,
                    "message": f"{title}: {outcome['summary']}",
                }
            )
    body = {
        "contract": REPORT_CONTRACT,
        "mode": "verify",
        "environment": manifest.environment,
        "status": worst([r["status"] for r in resources]),
        "revisions": {"manifest_revision": manifest.revision, "manifest_digest": manifest.digest},
        "targets": {"redis": "disposable (explicit)", "mongo": "disposable caos_test_* databases"},
        "resources": resources,
        "findings": findings,
        "reconciliation_plan": [],
    }
    return {**body, "report_digest": sha256_hex(body)}
