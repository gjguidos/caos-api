
# AgentVerse Redis Configuration Guide

This guide explains how to configure **Redis** for AgentVerse in two environments:
- **Local Docker Redis** (basic auth or no auth)
- **Azure Cache for Redis** using **Entra ID (AAD) token** authentication

It matches the provided `CacheSettings`, `redis_resource`, and `RedisContainer` code.

---

## 1) How selection works

- The resource selects **Entra (AAD) token auth** only when Environment is **not** `Development`:
- Otherwise it uses **basic connection** via `REDIS_URL` (or a URL built from host/port/db/tls/creds).  
- TLS:
  - Local docker redis defaults to `redis://` (`REDIS_TLS=false`)
  - Azure requires TLS; the Entra path sets `ssl=True`.

> Log lines will indicate the active mode:  
> - `Redis mode: BASIC (no Entra)`  
> - `Redis mode: ENTRA (AAD)`

---

## 2) Environment variables

### 2.1 Local development (Docker Redis)

```env
# ---- App / environment ----
ENVIRONMENT=Development

# ---- Connection (local docker) ----
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_TLS=false                 # plain TCP for local
REDIS_PREFIX=myapp
```

### 2.3 Azure Redis - Entra ID (AAD) token auth

```env
ENVIRONMENT=prod

# ---- Connection (host/port for Azure) ----
REDIS_HOST=<your-redis-name>.redis.cache.windows.net
REDIS_PORT=6380
REDIS_TLS=true
```
> For Entra auth, the code uses `redis_entraid.cred_provider` to wire an appropriate **credential provider** into `redis.asyncio.Redis`, and sets `ssl=True`. No password is sent; the provider advertises and refreshes tokens.

---

## 3) Docker Compose (local)

Add a Redis service with a **persistent volume**, and wire your API to it.

```yaml
services:
  api:
    build: .
    env_file:
      - ./.env
    depends_on:
      - redis
    # networks/ports as needed

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--save", "", "--appendonly", "no"]
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

volumes:
  redis_data:
```

> If you want a local password for testing:
> - add `--requirepass yourpass` to the command
> - set `REDIS_PASSWORD=yourpass` and optionally `REDIS_USERNAME=` (empty)

---

## 4) How the DI wiring works

```python
# containers/redis_container.py
class RedisContainer(containers.DeclarativeContainer):
    redis_client = providers.Resource(redis_resource)
    redis_repository = providers.Singleton(RedisRepositoryImpl, redis_client=redis_client)
    socket_redis_bridge_service = providers.Factory(SocketRedisBridgeService, redis_repository=redis_repository)
```

- `redis_resource` is an **async context manager** that returns a connected `redis.asyncio.Redis` client.
- It decides **BASIC vs ENTRA** mode at runtime based on the rules above, then calls `await client.ping()` to fail fast.
- Cleanup is awaited (`aclose()` if available, else `close`).

---

## 5) Runtime behavior & logs

- Local/basic mode:
  - Log: `Redis mode: BASIC (no Entra) env=... url=...`
  - URL comes from `REDIS_URL` or built from parts (host/port/db/tls/creds)
- Entra mode:
  - Log: `Redis mode: ENTRA (AAD) env=... host=... port=...`
  - Azure requires TLS; port typically `6380`

---

## 6) Key namespacing conventions

Given:
- `REDIS_PREFIX=myapp`
- `REDIS_KEY_NS=av`

You can construct stable prefixes like:

```
{myapp}:{av}:agent:v1:{tenant?}:<agent_id>
{myapp}:{av}:history:v1:{tenant?}:<session_id>
{myapp}:{av}:session:v1:{tenant?}:<session_id>
{myapp}:{av}:memory:v1:{tenant?}:<agent_id>
```

> If `*_key_multi_tenant=true`, inject your tenant id between the version and the leaf id.

---

## 7) Common pitfalls

- **TLS mismatch**: Azure requires TLS. For password mode use `rediss://` or `REDIS_TLS=true`.
- **Auth errors in Entra mode**: confirm access policy on the Redis resource and the exact identity in use (system vs user-assigned MI, or SP creds).
- **Firewall/VNet**: ensure your app host IP/VNet is allowed in Azure Cache for Redis networking.
- **Time drift**: token-based auth is sensitive to clock drift; keep servers NTP-synced.

---

## 8) Quick code examples

**Basic SET/GET using the injected repository**

```python
repo = container.redis_repository()  # dependency-injector
await repo.set("demo:key", "hello", ex=30)
val = await repo.get("demo:key")  # -> "hello"
```

**Publish/Subscribe (conceptual)**

```python
# publisher
await repo.publish("events:new-message", '{"id":"123","text":"hi"}')

# subscriber (simplified)
async with repo.pubsub() as ps:
    await ps.subscribe("events:new-message")
    async for msg in ps.listen():
        if msg["type"] == "message":
            print("payload:", msg["data"])
```

---

## 9) Minimal troubleshooting checklist

- [ ] Logs indicate expected mode (**BASIC** vs **ENTRA**)  
- [ ] `await client.ping()` succeeds  
- [ ] For Azure:
  - [ ] TLS is enabled (`rediss://` or `ssl=True`)
  - [ ] Port 6380 reachable
  - [ ] Password OR Entra identity configured (not both)
  - [ ] Access policy/roles assigned for Entra
- [ ] Keys are created with the expected namespace and TTL

---

## 10) FAQ

**Q: Can I use Entra in non-production?**  
Yes-set `ENVIRONMENT` to something not `Development`. Useful for preprod environments.

**Q: Do I need `REDIS_URL`?**  
No. If omitted, the settings build a URL from parts (`host/port/db/tls/username/password`).
---

## 11) Summary

- Local Docker → **BASIC** mode to `redis://redis:6379/0`.  
- Azure Redis:
  - **Password** mode → `rediss://...:6380/0` with access key.  
  - **Entra** mode → token-based, TLS on.
