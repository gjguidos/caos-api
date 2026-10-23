# Explicit live RabbitMQ smoke test

`live_rabbitmq_smoke.py` is a separate-process runner, **not** a discovered pytest
test. Default invocation only checks local configuration: it does not import the
root container, resolve hostnames, or contact the broker. The ordinary discovered
`test_live_rabbitmq_smoke.py` contains offline safety tests with network access blocked.

## Safety prerequisites (operator responsibility)

1. Use a clean, approved checkout/snapshot of
   `5d33821e9b3d76f7bd0fa6bdcb246d0bca9c7b2c`, with these test artifacts copied in.
   Do not discard or stash the user's existing changes. Live mode checks HEAD and
   refuses modified tracked production source/build configuration. Git and the
   project's existing Python dependencies must be installed in the test process.
2. Have a broker administrator provision a **dedicated, empty** vhost, e.g.
   `caos-smoke-validation`, and a **dedicated pre-existing durable topic exchange**
   named `caos.smoke.validation`, without auto-delete, internal mode, arguments,
   queues, or bindings. No production/API process may use this vhost. Do not point
   this runner at `/` or a shared application vhost. Unique routing on a shared
   topic exchange does not prevent other wildcard subscribers receiving events.
3. Approve the exact AMQP host/port/vhost, management origin and cluster name through
   the administrator. The management endpoint must address the same broker host
   directly, not an unrelated server/load balancer. Prefer HTTPS outside a trusted
   local development network. The runner verifies cluster identity, vhost existence,
   no existing queues/connections, and the dedicated exchange properties/bindings
   via read-only management requests before opening its AMQP connection.
   The current CAOS messaging URL uses plain AMQP: use disposable test credentials
   on a trusted isolated network/VPN, never production credentials over an untrusted link.
4. Supply `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_VIRTUAL_HOST`, `RABBITMQ_USER`,
   and `RABBITMQ_PASSWORD` from an approved secret source. Restrict the AMQP user's
   permissions to this test vhost and `caos.smoke.*` resources. Supply management
   credentials through `CAOS_SMOKE_MANAGEMENT_USER` and
   `CAOS_SMOKE_MANAGEMENT_PASSWORD`; they must have a verified `monitoring` or
   `administrator` tag and permit reading the vhost/queue statistics and **all users'**
   connections. A plain management user may see an incomplete connection inventory
   and is rejected. Management ACK statistics must be available; absence is **inconclusive**,
   never a pass. Do not put credentials in CLI arguments or URLs.
5. Supply `COGNITIVE_EVENT_SIGNING_KEY` (UTF-8 encoding >=32 bytes), plus all unrelated
   settings required to import the real CAOS root. Root import is network-blocked
   and must complete without contacting Redis, MongoDB, OpenAI, or another service.
   `MessagingConfig` does not automatically read a local `.env`: inject its environment
   variables explicitly. Do not print secrets in shell tracing.

## Invocation

From the approved repository root, in the existing uv environment with dependencies
already installed (`--no-sync` avoids installing/updating them), first run:

```bash
uv run --no-sync python -B -m src.base.events.v2.tests.live_rabbitmq_smoke \
  --expected-host localhost --expected-port 5672 \
  --expected-vhost caos-smoke-validation --exchange caos.smoke.validation \
  --management-url http://localhost:15672 \
  --expected-cluster-name '<administrator-confirmed-cluster-name>'
```

This is only a **no-network configuration preflight**, not proof of broker ownership.
After the operator verifies the prerequisites, use the same command with:

```text
--live --confirm-isolated-vhost
```

Do not execute in the running API Python process. Do not call `init_resources()` or
start the application lifespan. The runner imports the actual root `Container`,
creates a fresh instance and uses only its messaging/cognitive providers. It registers
real `EventContract`, `Subscription`, exact `EventGrants`, and `EventPrincipal` objects.
It does not replace the runtime, signing key, policy or transport with mocks.

The runner explicitly supplies `exchange=` to the existing runtime provider because
the configured exchange settings are not currently wired to it. Thus it never
declares the shared production `caos.events.v2` exchange. CAOS idempotently redeclares
the approved test exchange; this runner never creates/deletes exchange resources
through the management API and never deletes an exchange.

## Evidence and cleanup

- A UUID identifies two exact test queue names, routing key and narrow grants.
- Authorized UTF-8 payload publication must reach the runtime handler and a separate
  metadata observer. Envelope identity/correlation/causation and raw persistent JSON
  AMQP metadata/signature must agree. Independent canonical HMAC verification runs
  inside the observer.
- Broker management evidence must show ACK counters increasing and zero outstanding
  deliveries. Handler completion or publisher confirms alone do not prove ACK.
  Callbacks never ACK manually: the existing client owns `message.process()`.
- Targeted observer cancellation is repeated; the runtime consumer must remain active
  and receive another event. The cancelled observer's queue retains its second message.
- An ungranted principal must receive `EventPermissionError` without another delivery.
- Finally, repeated runtime/observer cancellation runs before queue deletion and
  connection shutdown. Only the exact UUID-owned queues are purged/deleted, after
  observing zero consumers/unacknowledged deliveries. Messaging shutdown is repeated;
  client handles, local registrations and broker connection/queue inventories must
  show cleanup. Every cleanup stage is attempted and failures fail the run.

Per-step asyncio timeouts are cooperative: production cancellation handlers may spend
additional time rolling back. Live mode therefore runs in a **spawned child process**
with a hard overall deadline (`--overall-timeout`, default 180 seconds, followed by
bounded termination/kill waits for the owned worker). Forced termination is a **failure**,
not proof of graceful cleanup. The parent flushes the UUID and both exact queue names
before launching the worker. Retain that inventory for administrator-assisted cleanup.

Timeouts, unavailable statistics, cleanup failures, or root-import/configuration
failures return nonzero. Backend exception details/logs are suppressed to avoid
credential leaks. No live success has been established merely by adding this runner.
On hard termination/broker outage, queues may remain durable: retain the reported UUID
and ask the administrator to remove only those two exact queues after verifying
ownership. Do not purge/delete a vhost, shared queue, or exchange.

## Separate real FastAPI lifespan integration test plan (not implemented/executed)

The standalone broker smoke **does not validate full application shutdown**.

1. Use a separate opt-in test process with a fresh real application/container,
   approved disposable Redis/MongoDB resources and the same isolated-broker safeguards.
   Verify the exact candidate snapshot and do not initialize the running API's container.
2. Configure trusted contracts/grants and the dedicated exchange before entering the
   real FastAPI lifespan. Do not manually connect/close the same resources in parallel.
3. Record resource/connection identities and baseline broker/Redis/Mongo inventories.
   Enter the actual lifespan, publish and consume a real event, and wait for ACK evidence.
4. Exit normally. Verify supervisor/runtime stop before their infrastructure, no test
   consumers/connections remain, executors close, and Redis/Mongo/Splunk resources finish.
   Check broker-side state in addition to Python handles.
5. In isolated repeatable runs, inject connection refusal, runtime startup failure,
   application-body failure, cleanup failure and actual task cancellation. Verify the
   original exception survives, other cleanup stages run, and retries retain unfinished
   ownership. Never stop the shared development broker to inject a failure.
6. Separately test in-flight handler drain/redelivery on shutdown: consumer cancellation
   currently has no explicit drain contract. Do not claim exactly-once delivery.
7. Triage the known Redis pre-yield `ping()` cleanup gap and externally owned pool
   shutdown. Do not mask it with a mocked context manager or call the application
   lifecycle failure-safe until real provider evidence supports that claim.

## Next prerequisite before further NCSC work

Build and validate a reproducible, isolated smoke Compose setup before continuing
NCSC work. It must pin the approved source and dependencies, provision the dedicated
vhost/exchange and restricted users, inject disposable credentials without committing
secrets or env files, and document preflight, live execution, ACK evidence and cleanup.
The setup is not included in these three smoke-test artifacts; temporary Docker files
from the manual validation environment are not release artifacts.
