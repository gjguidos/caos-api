from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


class KnowledgeCollectionError(ValueError):
    pass


@dataclass(frozen=True)
class KnowledgePolicy:
    """
    Controls how an agent treats collections.
    - mode: "single" → must have exactly one linked collection.
            "multi"  → can hold many; caller/FE must set active_collection explicitly.
    - allow_public_fallback: if True and no active set (or invalid), try public scope resolver (optional).
    """

    mode: str = "single"  # "single" | "multi"
    allow_public_fallback: bool = False
    strict: bool = False


class KnowledgeAgentMixin:
    """
    Mixin for BaseAgent that manages 'active_collection' state:
      - persist via self.remember/recall
      - validate against self.collections (if present)
      - convenient helpers for tool kwargs
    Assumes BaseAgent API: .remember(key, val), .recall(key), .collections (list[str] or []), .id, .user_id
    """

    # ---------- public API ----------

    async def set_active_collection(
        self,
        name: str,
        *,
        enforce_membership: bool = True,
    ) -> str:
        """
        Persist active collection. Optionally ensure it's linked to this agent.
        """
        if not name or not isinstance(name, str):
            raise KnowledgeCollectionError("active_collection must be a non-empty string")

        allowed = list(getattr(self, "collections", []) or [])
        if enforce_membership and allowed and name not in allowed:
            raise KnowledgeCollectionError(
                f"Collection '{name}' is not linked to this agent; allowed={allowed}"
            )

        await self.remember("active_collection", name)
        return name

    async def get_active_collection(
        self,
        *,
        policy: KnowledgePolicy = None,
        fallback_from: str | None = None,
    ) -> str | None:
        try:
            mem = await self.recall("active_collection")
        except Exception:
            mem = None
        if mem:
            return mem

        linked: Sequence[str] = list(getattr(self, "collections", []) or [])
        policy = policy or KnowledgePolicy()
        if policy.mode == "single":
            if not linked:
                # ← changed: do NOT raise unless strict=True
                if policy.strict:
                    raise KnowledgeCollectionError(
                        "No collection linked to this agent (single-mode requires exactly one)."
                    )
                return None
            await self.remember("active_collection", linked[0])
            return linked[0]

        if policy.allow_public_fallback:
            pub = await self._resolve_public_default_collection(fallback_from=fallback_from)
            if pub:
                await self.remember("active_collection", pub)
                return pub

        if len(linked) > 1:
            if policy.strict:
                raise KnowledgeCollectionError(
                    "Multiple collections linked; frontend must set `active_collection` explicitly."
                )
            return None

        return linked[0] if linked else None

    async def ensure_active_collection(
        self,
        incoming: str | None,
        *,
        policy: KnowledgePolicy = KnowledgePolicy(),
        enforce_membership: bool = True,
    ) -> str:
        if incoming:
            return await self.set_active_collection(incoming, enforce_membership=enforce_membership)
        resolved = await self.get_active_collection(policy=policy)
        if not resolved:
            if policy.strict:
                raise KnowledgeCollectionError("No active collection could be resolved.")
            # Non-strict: bubble up None so callers can decide.
            return None
        return resolved

    async def inject_collection_into_kwargs(
        self,
        *,
        tool_kwargs: dict | None,
        incoming: str | None = None,
        policy: KnowledgePolicy = KnowledgePolicy(),
        enforce_membership: bool = True,
        kwarg_name: str = "collection",
    ) -> dict:
        """
        Ensure `tool_kwargs[kwarg_name]` contains the resolved collection for this turn.
        """
        out = dict(tool_kwargs or {})
        if kwarg_name not in out:
            coll = await self.ensure_active_collection(
                incoming, policy=policy, enforce_membership=enforce_membership
            )
            out[kwarg_name] = coll
        return out

    # ---------- optional hooks you can extend later ----------

    async def _resolve_public_default_collection(self, *, fallback_from: str | None) -> str | None:
        """
        Hook for (c) 'search all public collections' scenarios.
        Implement using your DB service if/when needed.
        """
        return None
