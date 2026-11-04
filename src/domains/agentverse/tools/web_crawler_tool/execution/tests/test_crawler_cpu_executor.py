"""RUNTIME-CONFIG-0.1: crawler CPU workers start with ``spawn``, whatever the platform
default or how the API server was started.

The worker check runs in a clean interpreter: a spawned worker inherits its parent's
``sys.path``, and pytest's prepended test roots (e.g. ``src/base/infrastructure``) would
shadow standard-library modules such as ``http`` in the worker, unlike in the API.
"""

import json
import subprocess
import sys

from src.domains.agentverse.tools.web_crawler_tool.execution.crawler_cpu_executor import (
    crawler_cpu_executor,
    page_cpu_executor,
)

_CHECK = r"""
import asyncio, json, os
from src.domains.agentverse.tools.web_crawler_tool.execution.crawler_cpu_executor import (
    CpuExecutor, _warmup_process,
)

async def main():
    executor = CpuExecutor(max_workers=2)
    facts = {"parent": os.getpid()}
    try:
        facts["pids"] = sorted({await executor.run(os.getpid) for _ in range(6)})
        facts["parents"] = sorted({await executor.run(os.getppid) for _ in range(6)})
        await executor.warmup()
        await executor.run(_warmup_process)
        try:
            await executor.run(int, "not a number")
        except ValueError as exc:  # Worker errors still propagate.
            facts["error"] = type(exc).__name__
        workers = list(executor._pool._processes.values())
        facts["classes"] = sorted({type(w).__name__ for w in workers})
        facts["cmdlines"] = sorted(
            {"spawn_main" in open(f"/proc/{w.pid}/cmdline").read() for w in workers}
        )
        facts["count"] = len(workers)
    finally:
        await executor.aclose()
    facts["alive_after_close"] = [w.is_alive() for w in workers]
    try:
        await executor.run(os.getpid)
    except RuntimeError as exc:
        facts["after_close"] = str(exc)
    await executor.aclose()  # Idempotent.
    print(json.dumps(facts))

asyncio.run(main())
"""


def test_workers_genuinely_start_with_spawn_and_shut_down():
    done = subprocess.run(
        [sys.executable, "-c", _CHECK], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr[-2000:]
    facts = json.loads(done.stdout.strip().splitlines()[-1])
    assert facts["classes"] == ["SpawnProcess"]  # Not ForkProcess.
    assert facts["cmdlines"] == [True]  # Started through multiprocessing.spawn.
    assert facts["count"] == 2 and facts["parent"] not in facts["pids"]
    assert facts["parents"] == [facts["parent"]]
    assert facts["error"] == "ValueError"
    assert facts["alive_after_close"] == [False, False]
    assert "closed" in facts["after_close"]


def test_the_registered_executors_keep_their_sizes_and_use_spawn():
    for executor, size in ((crawler_cpu_executor, 4), (page_cpu_executor, 6)):
        assert executor._max_workers == size
        assert executor._pool._mp_context.get_start_method() == "spawn"
