from src.base.infrastructure.ai.client_factory import ClientFactory
from src.base.integrations.noop import (
    InMemoryCache,
    NoOpDB,
    NoOpEmbeddingClient,
    NoOpImageClient,
    NoOpVectorDB,
    NoOpVideoClient,
)


def inject_noops(cm: dict[str, dict[str, object]]) -> None:
    """Ensure every domain group exists and has a safe fallback."""
    for kind in ("image", "video", "embedding", "cache", "db", "knowledge_db"):
        cm.setdefault(kind, {})

    cm["image"].setdefault("noop", NoOpImageClient())
    cm["video"].setdefault("noop", NoOpVideoClient())
    cm["embedding"].setdefault("noop", NoOpEmbeddingClient())
    cm["cache"].setdefault("inmem", InMemoryCache())  # safer than a hard NoOp for cache
    cm["db"].setdefault("noop", NoOpDB())
    cm["knowledge_db"].setdefault("noop", NoOpVectorDB())


KINDS = (
    "llm",
    "cache",
    "communication",
    "messaging",
    "audio",
    "embedding",
    "db",
    "knowledge_db",
    "image",
    "video",
)

FALLBACKS = {
    # if no real backend exists, use these; blank means "no default" (fail fast)
    "cache": "inmem",
    "db": "noop",
    "knowledge_db": "noop",
    "image": "noop",
    "video": "noop",
    "embedding": "noop",
    "llm": "",
    "audio": "",
    "messaging": "",
    "communication": "",  # often required; leave blank unless you want a hard fallback
}


def first_real_key(group: dict[str, object]) -> str:
    """Pick the first backend that is NOT a NoOp fallback."""
    for k, v in group.items():
        if not getattr(v, "is_noop", False):
            return k
    return ""  # no real backend available


def compute_defaults(cm: dict) -> dict:
    def pick(kind: str) -> str:
        real = first_real_key(cm.get(kind, {}))
        return real or FALLBACKS.get(kind, "")

    # The catalog endpoint exposes every registered group, including extensions.
    kinds = dict.fromkeys((*KINDS, *cm))
    return {kind: pick(kind) for kind in kinds}


def build_client_factory(
    cognitive_modules: dict[str, dict[str, object]], cognitive_defaults: dict[str, str]
) -> ClientFactory:
    """
    Build a ClientFactory from cognitive modules, with NoOp fallbacks ready.
    """
    # NoOps should already be injected into cognitive_modules via inject_noops()
    return ClientFactory(
        repos=cognitive_modules,
        defaults=cognitive_defaults,
        noop_map={
            "image": cognitive_modules["image"].get("noop", NoOpImageClient()),
            "video": cognitive_modules["video"].get("noop", NoOpVideoClient()),
            "embedding": cognitive_modules["embedding"].get("noop", NoOpEmbeddingClient()),
            "cache": cognitive_modules["cache"].get("inmem", InMemoryCache()),
            "db": cognitive_modules["db"].get("noop", NoOpDB()),
            "knowledge_db": cognitive_modules["knowledge_db"].get("noop", NoOpVectorDB()),
        },
    )


async def refresh_defaults(app, *, merge_existing: bool = True) -> dict:
    """
    Recompute app.state.cognitive_defaults from the current cognitive_modules.
    If merge_existing=True, keep any still-valid existing defaults.
    Returns the new defaults dict.
    """
    cm = app.state.cognitive_modules

    # ensure noops are present (idempotent)
    inject_noops(cm)

    new_defaults = compute_defaults(cm)

    if merge_existing and hasattr(app.state, "cognitive_defaults"):
        old = getattr(app.state, "cognitive_defaults", {}) or {}
        merged = {}
        for kind, candidate in new_defaults.items():
            # keep old if it still exists in the group; otherwise use the new candidate
            if old.get(kind) and old[kind] in cm.get(kind, {}):
                merged[kind] = old[kind]
            else:
                merged[kind] = candidate
        app.state.cognitive_defaults = merged
    else:
        app.state.cognitive_defaults = new_defaults

    # keep ClientFactory in sync
    cf = getattr(app.state, "client_factory", None)
    if cf:
        cf.set_defaults(app.state.cognitive_defaults)

    return app.state.cognitive_defaults
