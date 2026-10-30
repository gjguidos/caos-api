"""``python -m scripts.nataraja {inspect|verify|reconcile --dry-run}`` (development only).

Exit codes: 0 passed/warning/deferred, 1 failed, 2 blocked or refused.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from src.domains.agentverse.nataraja.manifest import DEFAULT_MANIFEST, load_manifest
from src.domains.agentverse.nataraja.report import to_json, to_text

_EXIT = {"passed": 0, "warning": 0, "deferred": 0, "failed": 1, "blocked": 2}


def _require_development() -> None:
    if (
        os.environ.get("ENVIRONMENT") != "development"
        or os.environ.get("GENESIS_LOCAL_ENABLED") != "true"
    ):
        raise SystemExit(
            "Nataraja is development-only (ENVIRONMENT=development, GENESIS_LOCAL_ENABLED=true)"
        )


async def _observe(manifest: Any) -> Any:
    import redis.asyncio as aioredis
    from motor.motor_asyncio import AsyncIOMotorClient

    import src.main  # noqa: F401 - registers Agent types, Roles and Souls; no lifespan runs
    from src.base.config.config import settings
    from src.base.dependencies.di_container import Container
    from src.domains.agentverse.nataraja.observe import (
        AsyncReadOnlyRedis,
        MotorReadOnlyMongo,
        observe,
        redact_target,
    )

    async def runtime() -> Any:
        import httpx

        url = os.environ.get(
            "NATARAJA_RUNTIME_URL", "http://127.0.0.1:8001/internal/dev/nataraja/runtime"
        )
        async with httpx.AsyncClient(timeout=3) as http:
            response = await http.get(url)  # read-only, loopback-only route
            response.raise_for_status()
            payload = response.json()
        return payload.get("data", payload) if isinstance(payload, dict) else None

    database = settings.database
    mongo_client: Any = AsyncIOMotorClient(database.resolved_uri, serverSelectionTimeoutMS=3000)
    redis_client = aioredis.Redis.from_url(settings.cache.redis_url, socket_timeout=3)
    try:
        return await observe(
            tenant_id=manifest.tenant_id,
            mongo=MotorReadOnlyMongo(mongo_client[database.resolved_dbname]),
            redis=AsyncReadOnlyRedis(redis_client),
            container=Container(),
            runtime=runtime,
            targets={
                "mongo": {
                    **redact_target(database.resolved_uri),
                    "database": database.resolved_dbname,
                },
                "redis": redact_target(settings.cache.redis_url),
            },
        )
    finally:
        mongo_client.close()
        await redis_client.aclose()


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--manifest", default=DEFAULT_MANIFEST)
    common.add_argument("--json", action="store_true", help="print the machine-readable report")
    parser = argparse.ArgumentParser(prog="nataraja", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect", parents=[common], help="read-only inspection")
    verify = commands.add_parser(
        "verify", parents=[common], help="lifecycle verification on disposable infrastructure"
    )
    verify.add_argument("--redis-url", default=os.environ.get("NATARAJA_VERIFY_REDIS_URL"))
    reconcile = commands.add_parser(
        "reconcile", parents=[common], help="propose reconciliation actions"
    )
    reconcile.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    _require_development()
    manifest = load_manifest(args.manifest)

    if args.command == "reconcile" and not args.dry_run:
        print("Only --dry-run is implemented; Nataraja executes no actions.", file=sys.stderr)
        return 2
    if args.command == "verify":
        from src.base.config.config import settings
        from src.domains.agentverse.nataraja.verify import UnsafeVerificationTarget
        from src.domains.agentverse.nataraja.verify import verify as run

        try:
            report = run(
                manifest, redis_url=args.redis_url, live_redis_url=settings.cache.redis_url
            )
        except UnsafeVerificationTarget as exc:
            print(f"Refused: {exc}", file=sys.stderr)
            return 2
    else:
        from src.domains.agentverse.nataraja.plan import evaluate

        observation = asyncio.run(_observe(manifest))
        report = evaluate(manifest, observation, mode=args.command)
    print(to_json(report) if args.json else to_text(report), end="")
    return _EXIT[report["status"]]
