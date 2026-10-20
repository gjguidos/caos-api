import asyncio
import time


class _NoOpBase:
    is_noop = True
    kind = "unknown"
    reason = "backend not configured"

    def __repr__(self):  # nice logs
        return f"<NoOp({self.kind}): {self.reason}>"


class NoOpImageClient(_NoOpBase):
    kind = "image"
    reason = "image backend not configured"

    async def generate(self, *a, **kw):
        raise RuntimeError(self.reason)


class NoOpVideoClient(_NoOpBase):
    kind = "video"
    reason = "video backend not configured"

    async def generate(self, *a, **kw):
        raise RuntimeError(self.reason)


class NoOpEmbeddingClient(_NoOpBase):
    kind = "embedding"
    reason = "embedding backend not configured"

    async def embed(self, *a, **kw):  # match your real interface
        raise RuntimeError(self.reason)


# ⚠ For infra, prefer an in-memory stub (not a dead NoOp) or keep them required.
# BaseAgent uses cache heavily (set/get/lpush/ltrim/lrange/expire/delete/exists).
# If you truly want a fallback, use a simple in-memory cache:


class InMemoryCache:
    kind = "cache"
    is_noop = True  # still a fallback

    def __init__(self):
        self._kv = {}
        self._lists = {}
        self._ttl = {}
        self._lock = asyncio.Lock()

    async def set(self, key, value, expiration=None, nx: bool = False):
        async with self._lock:
            if nx and key in self._kv:
                return False
            self._kv[key] = value
            if expiration:
                self._ttl[key] = time.time() + expiration
            return True

    async def get(self, key):
        async with self._lock:
            if key in self._ttl and time.time() > self._ttl[key]:
                self._kv.pop(key, None)
                self._ttl.pop(key, None)
                return None
            return self._kv.get(key)

    async def exists(self, key):
        return (await self.get(key)) is not None

    async def delete(self, key):
        async with self._lock:
            self._kv.pop(key, None)
            self._ttl.pop(key, None)
            self._lists.pop(key, None)
            return True

    async def expire(self, key, seconds):
        async with self._lock:
            if key in self._kv or key in self._lists:
                self._ttl[key] = time.time() + seconds
                return True
            return False

    async def lpush(self, key, value):
        async with self._lock:
            self._lists.setdefault(key, [])
            self._lists[key].insert(0, value)
            return len(self._lists[key])

    async def ltrim(self, key, start, end):
        async with self._lock:
            if key in self._lists:
                self._lists[key] = self._lists[key][start : end + 1]
            return True

    async def lrange(self, key, start, end):
        async with self._lock:
            lst = self._lists.get(key, [])
            if end == -1:
                end = len(lst) - 1
            return lst[start : end + 1]

    async def scan_keys(self, pattern: str):
        return []

    async def eval(self, script: str, keys: list[str], args: list):
        return 0


class NoOpDB(_NoOpBase):
    kind = "db"
    reason = "db backend not configured"

    async def find_one(self, *a, **kw):
        return None

    async def find(self, *a, **kw):
        return []

    async def create(self, *a, **kw):
        raise RuntimeError(self.reason)

    async def update_one(self, *a, **kw):
        raise RuntimeError(self.reason)

    async def delete(self, *a, **kw):
        return False


class NoOpMessaging(_NoOpBase):
    kind = "messaging"
    reason = "messaging backend not configured"

    async def publish(self, *a, **kw):
        raise RuntimeError(self.reason)

    async def subscribe(self, *a, **kw):
        raise RuntimeError(self.reason)

    async def unsubscribe(self, *a, **kw):
        raise RuntimeError(self.reason)


class NoOpVectorDB(_NoOpBase):
    kind = "knowledge_db"
    reason = "vector db not configured"

    async def query(self, *a, **kw):
        return []

    async def upsert(self, *a, **kw):
        return None
