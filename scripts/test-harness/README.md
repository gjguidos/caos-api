# Disposable integration-test harness (TEST-SAFETY-0.1)

`run.sh` is the only supported way to run the destructive UTU and Genesis integration
tests. These tests create, revoke and delete UTU tenant authority state.

## Running tests

```bash
scripts/test-harness/run.sh                                   # full backend suite
scripts/test-harness/run.sh src/domains/agentverse/tests/agents -x
CAOS_TEST_PARALLEL=4 scripts/test-harness/run.sh <tests>      # concurrent runners
```

- **Arguments** are passed to pytest. The default is `src`, and the harness always adds
  `-q -rfE`.
- **Image:** the `caos-api-api` image must already be built (override with
  `CAOS_TEST_IMAGE`).
- **Exit codes:**
  - 0: all runners passed;
  - 1: a runner failed, or the harness refused to start;
  - 3: a harness resource remained after cleanup;
  - 130 and 143: interrupted by SIGINT or SIGTERM. Cleanup still runs, and an
    interrupted run never reports success.
- **Expected failures:** a full run currently reports 5 inherited failures in
  `src/base/events/v2/tests/test_root_signing_key_wiring.py`, so the exit code is 1.
  Their cause is in that test's own container fixture
  (`AttributeError: 'DynamicContainer' object has no attribute 'mongodb_repository'`),
  not the harness. Anything else is a regression.
- **Elsewhere,** for example `docker compose exec api pytest`, these tests skip when
  `UTU_TEST_REDIS_URL` is unset, and are refused before any connection when it is set.

## The disposable boundary (established by the harness)

- **Network:** a dedicated `--internal` Docker network, labelled
  `caos.test-harness=<run>`, with no route to other networks.
- **Data services:** fresh MongoDB, application Redis and UTU test Redis containers on
  that network only. They use tmpfs data, with no volumes and no published ports.
- **Runner:** on that network only. The repository's `src`, `config` and
  `pyproject.toml` are mounted read-only. Its environment is explicit: `.env` is never
  passed, so the runner gets no live configuration or credentials. A random
  event-signing key is generated for each run.
- **Checks:** before any test starts, the harness verifies that the network is
  internal, and that every container's networks, published ports, mounts and harness
  label are exactly as expected. Any difference aborts the run.

## What the tests verify before any destructive operation

See `src/domains/agentverse/tests/utu_isolation.py`.

1. **Endpoint:** `UTU_TEST_REDIS_URL` is a `redis://` URL with a host, and is not the
   application's configured Redis, literally or by resolved address.
2. **Internal network:** the runner has no default route. The live API container, or
   any normally networked container, is refused.
3. **Redis process identity:** the harness reads `run_id` from `INFO server` of the UTU
   Redis container it created, and declares it as `UTU_TEST_REDIS_RUN_ID`. The tests
   connect only to the declared Redis. They refuse when the identity is absent,
   malformed, mismatched, or `INFO server` is unreadable.
4. **Contamination:** no production-format authority keys exist (`caos:utu:v1:*`,
   `caos:{genesis-sessions}:v1:*`).
5. **Isolation:** each run then uses its own UTU key prefix (`caos-test:<run>:`), and
   deletes only what it created. A refused invocation deletes nothing.

A `run_id` is a process identity, not proof of disposability. The disposable boundary
is the harness.

## Concurrent execution

`CAOS_TEST_PARALLEL=N` (1-8) starts N runners against the same disposable services.
Each runner, and each test run inside it, owns its UTU tenant key. One run's teardown
never touches another run's policy, incarnation or revocations.

## Failure logs

- **Location:** each harness run writes to `${CAOS_TEST_LOG_DIR:-$TMPDIR/caos-test-harness}/<run>/`,
  a mode-0700 directory.
- **Per-runner log:** `<runner>.log`, the complete pytest output with tracebacks.
- **Per-runner summary:** `<runner>.summary` and the combined `summary.txt`. Each line
  gives the run, runner, Docker start and finish times, exit code, a failure category
  and the test arguments.
- **Categories:** `passed`, `refused_unsafe_target`, `utu_authority_denied`,
  `test_failure`, `no_tests_collected`, `harness_or_runtime_error`, with HTTP statuses
  from assertions where present.
- **Service logs:** on failure, the MongoDB and Redis logs are saved under `services/`
  before cleanup.
- **Retention:** passing runs delete their logs unless `CAOS_TEST_KEEP_LOGS=1`; failing
  runs keep them.
- **Content:** logs contain pytest output only, with no environment, credentials or
  authority values.

## Why not the live API container

The live API container is attached to the live Compose network, can reach the live
Redis and MongoDB, and carries the live `.env` credentials. Destructive integration
tests create, revoke and delete authority state, so they must never run there.

- `docker compose exec api pytest` remains fine for non-destructive tests.
- The destructive suites skip there when `UTU_TEST_REDIS_URL` is unset. When it is set,
  they are refused before any connection: the container has a default route and no
  harness-declared Redis identity.
- **Nataraja:** `inspect` and `reconcile --dry-run` are read-only and supported in the
  live container. `verify`'s integration suites belong in this harness.

## Cleanup and limitations

- **Cleanup:** every container and the network of the run are removed on exit,
  including after failures. The harness then checks that nothing labelled with its run
  remains.
- **Deliberate misuse is out of scope.** The guard protects against accidental use.
  Someone who deliberately builds an internal network around a live Redis and declares
  its `run_id` defeats it.
- **Non-authority keys** (conversations, presence) use production formats inside the
  disposable Redis, which is destroyed with the run.
- **Nataraja `inspect`** (read-only) is independent of this harness. Nataraja `verify`'s
  test-running mode needs the harness environment, and refuses outside it.
