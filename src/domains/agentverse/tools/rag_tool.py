from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

import anyio
from pydantic import BaseModel

from src.base.handlers.exception import classify_error
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.tools.base import BaseTool, ToolConfig, ToolExecutionError
from src.domains.agentverse.utils.formatting import strip_citations

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

CITE_INLINE_RE = re.compile(r"\[Source:\s*([^,\]]+),\s*page\s*([^\]]+)\]", re.I)
NAME_EXT_RE = re.compile(r"\.(pdf|docx?|pptx?|rtf|txt|md)$", re.I)


@lru_cache(maxsize=1)
def _get_tokenizer():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


@lru_cache(maxsize=1)
def _get_symspell():
    try:
        from symspellpy import SymSpell

        s = SymSpell(max_dictionary_edit_distance=2)
        s.load_dictionary("frequency_dict.txt", term_index=0, count_index=1)
        return s
    except Exception:
        return None


@lru_cache(maxsize=1)
def _get_stemmer():
    try:
        from nltk.stem import PorterStemmer

        return PorterStemmer()
    except Exception:
        return None


def _estimate_tokens(text: str) -> int:
    tok = _get_tokenizer()
    if tok:
        try:
            return len(tok.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def _normalize_title(name: str | None) -> str:
    if not name:
        return "Unknown"
    s = str(name)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\.{2,}", ".", s)
    s = NAME_EXT_RE.sub("", s)
    return s.rstrip(". ")


def _guess_provider_from_obj(llm: Any) -> str | None:
    for attr in ("provider", "vendor"):
        v = getattr(llm, attr, None)
        if v:
            return str(v)
    mod = getattr(llm.__class__, "__module__", "") if llm else ""
    ml = mod.lower()
    if "azure" in ml:
        return "azure"
    if "openai" in ml:
        return "openai"
    if "anthropic" in ml:
        return "anthropic"
    if "cohere" in ml:
        return "cohere"
    if "google" in ml or "vertex" in ml:
        return "google"
    return None


def _safe_model_meta(
    llm: Any, request: Any = None, from_response: dict[str, Any] | None = None
) -> dict[str, str | None]:
    model = None
    provider = None
    if from_response:
        model = from_response.get("model") or from_response.get("model_name")
        provider = from_response.get("provider")

    if not model and llm:
        for attr in (
            "model_name",
            "model",
            "_model",
            "deployment_name",
            "deployment",
            "engine",
            "default_model",
            "completion_model",
            "chat_model",
        ):
            v = getattr(llm, attr, None)
            if isinstance(v, str) and v:
                model = v
                break
            if v and hasattr(v, "model") and isinstance(getattr(v, "model", None), str):
                model = v.model
                break
        if not provider:
            provider = _guess_provider_from_obj(llm)

    if request and hasattr(request, "app") and getattr(request.app, "state", None):
        try:
            defaults = request.app.state.cognitive_defaults or {}
            model = model or defaults.get("llm")
        except Exception:
            pass

    if not model and llm:
        model = getattr(llm.__class__, "__name__", None)

    return {"model": model, "provider": provider}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class VectorDBType(str, Enum):
    CHROMA = "chromadb"
    AZURE_SEARCH = "azure_search"
    PINECONE = "pinecone"
    WEAVIATE = "weaviate"
    QDRANT = "qdrant"


class RagMode(str, Enum):
    SINGLE = "single"
    MULTI = "multi"


class SearchStrategy(str, Enum):
    SIMILARITY = "similarity"
    MMR = "mmr"
    HYBRID = "hybrid"


class SecureRAGToolConfig(ToolConfig):
    requires_collection: bool = True
    default_vectordb: VectorDBType = VectorDBType.CHROMA
    mode: RagMode = RagMode.SINGLE
    collection: str | None = None

    search_strategy: SearchStrategy = SearchStrategy.MMR
    enable_metadata_filtering: bool = True
    enable_query_expansion: bool = True
    enable_spell_check: bool = True

    top_k: int = 5
    fetch_k: int = 50
    mmr_lambda: float = 0.5
    rerank_enabled: bool = True

    context_token_budget: int = 1200
    max_source_references: int = 3

    # Economy (integer cents)
    economy_enabled: bool = False
    economy_currency: str = "USD"
    price_cents_per_call: int = 0
    price_cents_per_doc: int = 0
    price_cents_per_1k_context_tokens: int = 0

    attach_sources_in_answer: bool = True
    citation_label: str = "Source"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@tool_registry.register_tool(
    key="rag",
    name="RAG Tool",
    description="Retrieval-Augmented Generation with diversified search and structured sources.",
    version="2.6.1",  # bumped
    metadata={
        "requires_collection": True,
        "config_schema": "src.domains.agentverse.tools.rag_tool.SecureRAGToolConfig",
        "config": SecureRAGToolConfig().model_dump(),
        "tags": ["knowledge_retrieval", "rag", "vector_db", "document_search"],
        "route": "llm_only",
        "outputs": [
            {"key": "answer", "label": "Answer", "type": "string", "path": "answer"},
            {"key": "sources", "label": "Sources", "type": "list", "path": "sources"},
        ],
    },
)
class SecureRAGTool(BaseTool):
    """
    RAG tool with:
      - per-tool memory (history),
      - clean prose (no inline cites in prompt),
      - structured sources,
      - addons {usage, wallet_events},
      - robust model/provider detection (incl. Azure),
      - exact token usage when available; estimates otherwise.
    """

    key = "rag"
    name = "RAG Tool"
    version = "2.6.0"

    required_dependencies = ["llm", "vectordb"]
    capabilities = ["knowledge_retrieval", "contextual_search", "document_query"]

    parameters = {
        "query": {"type": "string", "description": "User question to answer", "required": True},
        "collection": {"type": "string", "description": "Collection name", "required": False},
        "top_k": {
            "type": "integer",
            "description": "Max docs to consider",
            "required": False,
            "default": 5,
        },
        "metadata_filters": {
            "type": "object",
            "description": "Filter by metadata",
            "required": False,
        },
        "date_range": {"type": "object", "description": "Date range filter", "required": False},
        "restrict_to_source_id": {
            "type": "string",
            "description": "Restrict to a single source_id",
            "required": False,
        },
        "session_id": {
            "type": "string",
            "description": "Conversation/session id (for per-tool memory)",
            "required": False,
        },
        "user_id": {"type": "string", "description": "User id", "required": False},
        "agent_id": {"type": "string", "description": "Agent id", "required": False},
        "history": {
            "type": "array",
            "description": "Fallback history when no session memory exists",
            "required": False,
        },
        "agent_context": {
            "type": "string",
            "description": "Optional system/role context to prefix to the prompt",
            "required": False,
        },
    }

    # -------------------- lifecycle --------------------

    def __init__(self, llm, vectordb, config: Any):
        cfg = self._normalize_config(config)
        super().__init__(config=cfg, llm=llm, vectordb=vectordb)
        self.llm = llm
        self.vectordb = vectordb
        self.vectordb_service = None  # request-scoped, see _resolve_dependencies
        self.config: SecureRAGToolConfig = cfg
        self._score_mode = getattr(self.vectordb, "score_mode", "distance")
        logger.info("[SecureRAGTool] Initialized (collection=%r)", self.config.collection)

    @staticmethod
    def _normalize_config(config: Any) -> SecureRAGToolConfig:
        if isinstance(config, SecureRAGToolConfig):
            return config
        if isinstance(config, BaseModel):
            return SecureRAGToolConfig.model_validate(config)
        if isinstance(config, Mapping):
            return SecureRAGToolConfig(**dict(config))
        if isinstance(config, SimpleNamespace):
            return SecureRAGToolConfig(**vars(config))
        if isinstance(config, dict):
            return SecureRAGToolConfig(**config)
        raise TypeError(
            f"SecureRAGTool config must be dict/BaseModel/namespace; got {type(config)}"
        )

    # -------------------- tool-owned memory --------------------

    async def _append_tool_memory(
        self, *, session_id: str | None, role: str, content: Any, meta: dict | None = None
    ):
        agent = getattr(self, "agent", None)
        if not agent or not hasattr(agent, "_append_history"):
            return
        payload = content
        if isinstance(payload, str):
            payload = strip_citations(payload)
        entry = {"role": role, "tool": self.key, "content": payload}
        if meta:
            entry["meta"] = meta
        try:
            await agent._append_history(entries=[entry], session_id=session_id, tool=self.key)
        except Exception:
            logger.exception("[SecureRAGTool] Failed to append tool memory")

    async def _read_tool_memory(self, *, session_id: str | None, limit: int = 50) -> list[dict]:
        agent = getattr(self, "agent", None)
        if not agent or not hasattr(agent, "get_memory_history"):
            return []
        try:
            return (
                await agent.get_memory_history(limit=limit, session_id=session_id, tool=self.key)
                or []
            )
        except Exception:
            logger.exception("[SecureRAGTool] Failed to read tool memory")
            return []

    def _history_for_prompt(self, raw: list[dict], max_turns: int = 5) -> list[dict]:
        out: list[dict] = []
        for item in raw:
            if item.get("tool") != self.key:
                continue
            role = item.get("role")
            content = item.get("content", "")
            if role == "user":
                out.append({"user": strip_citations(content)})
            elif role == "tool":
                if isinstance(content, dict):
                    txt = None
                    for k in ("answer", "text", "content", "summary", "message"):
                        v = content.get(k)
                        if isinstance(v, str) and v.strip():
                            txt = v
                            break
                    if txt is None:
                        try:
                            txt = json.dumps(content)[:1000]
                        except Exception:
                            txt = str(content)
                else:
                    txt = str(content)
                out.append({"agent": strip_citations(txt)})
        return out[-(max_turns * 2) :]

    # -------------------- dependency resolution --------------------

    def _resolve_dependencies(self, request):
        cm = request.app.state.cognitive_modules
        defaults = request.app.state.cognitive_defaults
        self.llm = self.llm or cm["llm"].get(defaults.get("llm"))
        self.vectordb = self.vectordb or cm["knowledge_db"].get(defaults.get("knowledge_db"))
        self.vectordb_service = request.app.container.agentverse.vectordb_service()
        self.reranker = (
            getattr(cm, "get", lambda *_: None)("reranker") if hasattr(cm, "get") else None
        )
        if not self.llm:
            raise ToolExecutionError("SecureRAGTool requires an LLM client")
        if not self.vectordb:
            raise ToolExecutionError("SecureRAGTool requires a vector DB client")

    # -------------------- collection selection --------------------

    async def _maybe_get_agent_active_collection(self) -> str | None:
        agent = getattr(self, "agent", None)
        if not agent:
            return None
        get_ac = getattr(agent, "get_active_collection", None)
        if not callable(get_ac):
            return None
        res = get_ac()
        return await res if inspect.isawaitable(res) else res

    def _resolve_collection(self, passed: str | None, agent) -> str:
        if passed:
            return passed.strip()
        if self.config.mode == RagMode.SINGLE:
            try:
                remembered = (
                    anyio.run(agent.recall, "active_collection")
                    if agent and hasattr(agent, "recall")
                    else None
                )
            except Exception:
                remembered = None
            if remembered:
                return remembered
            cols = getattr(agent, "collections", []) or []
            if len(cols) == 1:
                return cols[0]
            raise ToolExecutionError(
                "No collection selected. Link exactly one to the agent or pass `collection=...` (SINGLE mode)."
            )
        raise ToolExecutionError(
            "RAG MULTI mode: pass `collection=` per call or switch to SINGLE mode."
        )

    # -------------------- query preprocessing --------------------

    def _normalize_query(self, q: str) -> str:
        q = q.strip()
        q = re.sub(r"\s+", " ", q)
        if getattr(self.config, "enable_spell_check", False):
            sym = _get_symspell()
            if sym:
                try:
                    sug = sym.lookup_compound(q.lower(), max_edit_distance=2)
                    if sug:
                        q = sug[0].term
                except Exception:
                    pass
            else:
                fixes = {r"\bteh\b": "the", r"\brecieve\b": "receive"}
                for pat, rep in fixes.items():
                    q = re.sub(pat, rep, q)
        return q

    def _expand_queries(self, q: str) -> list[str]:
        out = [q]
        stemmer = _get_stemmer()
        if stemmer:
            try:
                stem = " ".join(stemmer.stem(w) for w in q.split())
                if stem and stem != q:
                    out.append(stem)
            except Exception:
                pass
        if q.endswith("?") and any(q.lower().startswith(w) for w in ("what", "how")):
            body = re.sub(r"^(what|how)\s+", "", q.strip(), flags=re.I).rstrip("?")
            if body:
                out += [f"explain {body}", f"how does {body} work"]
        seen, dedup = set(), []
        for v in out:
            k = v.lower().strip()
            if k and k not in seen:
                dedup.append(v)
                seen.add(k)
        return dedup[:4]

    # -------------------- scoring helpers --------------------

    def _as_similarity(self, score: float) -> float:
        return -float(score) if self._score_mode == "distance" else float(score)

    def _dynamic_cutoff(self, items: list[tuple[Any, float]], k: int) -> list[tuple[Any, float]]:
        if not items:
            return items
        sims = [self._as_similarity(s) for _, s in items]
        best = max(sims)
        margin = 0.15
        kept: list[tuple[Any, float]] = []
        for (doc, s), sim in zip(items, sims):
            if sim >= best - margin:
                kept.append((doc, s))
        return kept[:k]

    def _dedupe_by_source_page(self, items: list[tuple[Any, float]]) -> list[tuple[Any, float]]:
        seen, out = set(), []
        for doc, s in items:
            meta = getattr(doc, "metadata", {}) or {}
            source_name = _normalize_title(
                meta.get("book_title")
                or meta.get("source_display")
                or meta.get("source")
                or "Unknown"
            )
            page = str(meta.get("page", "?"))
            key = (source_name, page)
            if key not in seen:
                out.append((doc, s))
                seen.add(key)
        return out

    def _rank_mmr_locally(
        self, pool: list[tuple[Any, float]], k: int, lambda_mult: float
    ) -> list[tuple[Any, float]]:
        if not pool:
            return []

        def toks(d):
            txt = (getattr(d, "page_content", "") or "").lower()
            return set(re.findall(r"\w+", txt))

        token_sets = [toks(doc) for doc, _ in pool]
        selected = [0]
        candidates = list(range(1, len(pool)))
        while candidates and len(selected) < k:
            best_idx, best_score = None, float("-inf")
            for c in candidates:
                rel = 1.0 - (c / max(1, len(pool) - 1))
                red = max(
                    (
                        len(token_sets[c] & token_sets[s])
                        / max(1, len(token_sets[c] | token_sets[s]))
                    )
                    for s in selected
                )
                mmr = lambda_mult * rel - (1 - lambda_mult) * red
                if mmr > best_score:
                    best_score, best_idx = mmr, c
            selected.append(best_idx)
            candidates.remove(best_idx)
        return [pool[i] for i in selected]

    # -------------------- context & sources --------------------

    def _build_context_and_sources(self, ranked: list[tuple[Any, float]]) -> tuple[str, list[dict]]:
        context_parts: list[str] = []
        source_refs: dict[tuple[str, str], dict] = {}
        used_tokens = 0

        ranked_sorted = sorted(ranked, key=lambda x: self._as_similarity(x[1]), reverse=True)
        for doc, score in ranked_sorted:
            content = getattr(doc, "page_content", "") or ""
            meta = getattr(doc, "metadata", {}) or {}
            source_name = _normalize_title(
                meta.get("book_title") or meta.get("source_display") or meta.get("source")
            )
            page = str(meta.get("page", "?"))

            add_tokens = _estimate_tokens(content)
            if used_tokens + add_tokens <= self.config.context_token_budget:
                context_parts.append(content)
                used_tokens += add_tokens
                key = (source_name, page)
                if key not in source_refs:
                    source_refs[key] = {
                        "source": source_name,
                        "page": page,
                        "score": float(score),
                        "excerpt": (content[:200] + "...") if len(content) > 200 else content,
                    }
            else:
                remaining = self.config.context_token_budget - used_tokens
                if remaining > 50:
                    approx_chars = remaining * 4
                    context_parts.append(content[:approx_chars])
                    used_tokens = self.config.context_token_budget
                break

        sources = sorted(
            source_refs.values(),
            key=lambda x: x["score"],
            reverse=(self._score_mode == "similarity"),
        )
        return "\n\n".join(context_parts), sources[: self.config.max_source_references]

    # -------------------- addons (usage + wallet events) --------------------

    def _compute_usage_and_addons(
        self,
        *,
        prompt_text: str,
        answer_text: str,
        source_refs: list[dict],
        context_text: str,
        started_at: datetime,
        request: Any = None,
        resp_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ended_at = datetime.now(UTC)
        latency_ms = int((ended_at - started_at).total_seconds() * 1000)

        exact_usage = (resp_meta or {}).get("usage") if resp_meta else None
        if exact_usage and any(
            k in exact_usage for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            input_tokens = int(exact_usage.get("prompt_tokens") or 0)
            output_tokens = int(exact_usage.get("completion_tokens") or 0)
            total_tokens = int(exact_usage.get("total_tokens") or (input_tokens + output_tokens))
            estimated_flag = False
        else:
            input_tokens = _estimate_tokens(prompt_text)
            output_tokens = _estimate_tokens(answer_text)
            total_tokens = input_tokens + output_tokens
            estimated_flag = True

        context_tokens = _estimate_tokens(context_text)

        model_meta = _safe_model_meta(self.llm, request=request, from_response=resp_meta or {})
        usage = {
            "latency_ms": latency_ms,
            "model": model_meta.get("model"),
            "provider": model_meta.get("provider"),
            "tokens": {"input": input_tokens, "output": output_tokens, "total": total_tokens},
            "context_tokens": context_tokens,
            "estimated": estimated_flag,
        }

        addons: dict[str, Any] = {"usage": usage}

        if getattr(self.config, "economy_enabled", False):
            docs_count = len(source_refs)
            buckets_1k = (context_tokens + 999) // 1000
            cost_cents = int(self.config.price_cents_per_call or 0)
            cost_cents += int(self.config.price_cents_per_doc or 0) * docs_count
            cost_cents += int(self.config.price_cents_per_1k_context_tokens or 0) * buckets_1k
            if cost_cents:
                addons["wallet_events"] = [
                    {
                        "idempotency_key": str(uuid.uuid4()),
                        "kind": "expense",
                        "currency": getattr(self.config, "economy_currency", "USD"),
                        "amount_minor": int(cost_cents),
                        "meta": {
                            "docs": docs_count,
                            "context_tokens": context_tokens,
                            "pricing": {
                                "per_call_cents": int(self.config.price_cents_per_call or 0),
                                "per_doc_cents": int(self.config.price_cents_per_doc or 0),
                                "per_1k_context_tokens_cents": int(
                                    self.config.price_cents_per_1k_context_tokens or 0
                                ),
                            },
                        },
                    }
                ]

        return addons

    # -------------------- main run --------------------

    async def _run(
        self,
        query: str,
        user_id: str | None = None,
        collection: str | None = None,
        top_k: int | None = None,
        request: Any | None = None,
        agent_context: str | None = None,
        history: list[dict] | None = None,
        generation_params: dict | None = None,
        metadata_filters: dict | None = None,
        session_id: str | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        try:
            start_time = datetime.now(UTC)
            if request:
                self._resolve_dependencies(request)

            coll_name = collection or self.config.collection
            if not coll_name:
                try:
                    coll_name = await self._maybe_get_agent_active_collection()
                except Exception:
                    coll_name = None
            if not coll_name:
                coll_name = self._resolve_collection(
                    passed=None, agent=getattr(self, "agent", None)
                )

            # Tool-owned memory → prompt history
            raw_tool_mem = (
                await self._read_tool_memory(session_id=session_id, limit=50) if session_id else []
            )
            prompt_hist = self._history_for_prompt(raw_tool_mem, max_turns=5)
            if not prompt_hist and history:
                prompt_hist = history[-10:]

            logger.info(
                "[SecureRAGTool] tool-mem-turns=%d (session=%s, agent_bound=%s)",
                len(prompt_hist),
                session_id,
                bool(getattr(self, "agent", None)),
            )

            # Pre-write current user turn so chained tools in same turn can see it
            await self._append_tool_memory(session_id=session_id, role="user", content=query)

            processed = self._normalize_query(query)
            variants = (
                self._expand_queries(processed)
                if self.config.enable_query_expansion
                else [processed]
            )

            k = int(top_k or self.config.top_k)
            fetch_k = max(self.config.fetch_k, k * 5)
            mf = metadata_filters if self.config.enable_metadata_filtering else None

            if not self.vectordb_service:
                raise ToolExecutionError("SecureRAGTool requires a request to search")

            # Queries must be embedded with the model the collection was written with,
            # which is not necessarily the vector store's own default.
            embedding_key = await self.vectordb_service.embedding_key_for_collection(coll_name)

            async def _search_one(q: str):
                try:
                    return await self.vectordb_service.search_with_score(
                        request=request,
                        collection=coll_name,
                        query=q,
                        top_k=fetch_k,
                        metadata_filter=mf,
                        embedding_key=embedding_key,
                    )
                except Exception as e:
                    logger.warning("[SecureRAGTool] search failed for %r: %s", q, e)
                    return []

            batches = await asyncio.gather(
                *[_search_one(v) for v in variants], return_exceptions=True
            )
            pool: list[tuple[Any, float]] = []
            for b in batches:
                if isinstance(b, Exception):
                    logger.warning("[SecureRAGTool] search variant error: %s", b)
                    continue
                pool.extend(b or [])
            if not pool:
                await self._append_tool_memory(
                    session_id=session_id,
                    role="tool",
                    content={"answer": "No results", "sources": []},
                )
                return self._create_no_results_response(query, processed, start_time)

            pool = self._dedupe_by_source_page(pool)
            if self.config.search_strategy in (
                SearchStrategy.MMR,
                SearchStrategy.HYBRID,
            ) and not hasattr(self.vectordb, "search_mmr_with_score"):
                selected = self._rank_mmr_locally(pool, k=k, lambda_mult=self.config.mmr_lambda)
            else:
                selected = sorted(pool, key=lambda x: self._as_similarity(x[1]), reverse=True)[:k]

            filtered = self._dynamic_cutoff(selected, k=k) or selected[:k]

            context, source_refs = self._build_context_and_sources(filtered)
            logger.info(
                "[SecureRAGTool] context_tokens=%d sources=%d",
                _estimate_tokens(context),
                len(source_refs),
            )

            gen_params = {"temperature": 0.1, "max_tokens": 500, **(generation_params or {})}
            prompt, prompt_sections = self._build_prompt(
                query=processed, context=context, agent_context=agent_context, history=prompt_hist
            )

            # Build messages for chat completion API
            messages = []

            # System message with instructions and context
            system_parts = []
            if prompt_sections.get("agent_context"):
                system_parts.append(prompt_sections["agent_context"])
            system_parts.append(prompt_sections["instructions"])
            system_parts.append(prompt_sections["context_block"])
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})

            # Add history
            if prompt_hist:
                for turn in prompt_hist:
                    if "user" in turn:
                        messages.append({"role": "user", "content": turn["user"]})
                    elif "agent" in turn:
                        messages.append({"role": "assistant", "content": turn["agent"]})

            # Add current question
            messages.append({"role": "user", "content": processed})

            response_text = await self.llm.get_response_async(
                input=messages,
                temperature=gen_params.get("temperature", 0.1),
                model=gen_params.get("model"),
            )
            resp_meta = {}

            final = self._format_final_response(
                query=processed,
                response=response_text,
                source_references=source_refs,
                processed_query=processed,
                start_time=start_time,
                context_length=_estimate_tokens(context),
                results_count=len(filtered),
            )

            addons = self._compute_usage_and_addons(
                prompt_text=prompt,
                answer_text=final["result"]["answer"],
                source_refs=final["result"]["sources"],
                context_text=context,
                started_at=start_time,
                request=request,
                resp_meta=resp_meta,
            )

            # Per-section prompt token breakdown (estimated)
            tb = {
                "agent_context": _estimate_tokens(prompt_sections.get("agent_context", "")),
                "history_block": _estimate_tokens(prompt_sections.get("history_block", "")),
                "instructions": _estimate_tokens(prompt_sections.get("instructions", "")),
                "context_block": _estimate_tokens(prompt_sections.get("context_block", "")),
                "question_block": _estimate_tokens(prompt_sections.get("question_block", "")),
            }
            tb["prompt_total_est"] = sum(tb.values())

            usage = addons.setdefault("usage", {})
            usage["token_breakdown"] = tb
            usage["history_turns_used"] = len(prompt_hist)

            final["result"]["addons"] = addons

            await self._append_tool_memory(
                session_id=session_id,
                role="tool",
                content={
                    "answer": final["result"]["answer"],
                    "sources": final["result"]["sources"],
                },
            )

            return final

        except ToolExecutionError:
            # User-correctable/config error (e.g. no collection selected) with an
            # actionable message — let it bubble to BaseTool.execute().
            raise
        except Exception as e:
            logger.error("[SecureRAGTool] ❌ error: %s", e, exc_info=True)
            message, code = classify_error(e)
            return self._final_error(message=message, code=code)

    # -------------------- prompt & response shaping --------------------

    def _instructions_text(self) -> str:
        return (
            "Use ONLY the provided context to answer. If details are partial, note uncertainty. "
            "Return only the answer prose; do not include any [Source: ...] style citations."
        )

    def _build_prompt(
        self,
        *,
        query: str,
        context: str,
        agent_context: str | None,
        history: list[dict] | None,
    ) -> tuple[str, dict[str, str]]:
        sections: dict[str, str] = {
            "agent_context": agent_context or "",
            "history_block": "",
            "instructions": self._instructions_text(),
            "context_block": f"Context:\n{context}",
            "question_block": f"Question: {query}\nAnswer:",
        }

        if history:
            lines = []
            for m in history[-5:]:
                if "user" in m:
                    lines.append(f"User: {m['user']}")
                elif "agent" in m:
                    lines.append(f"Agent: {m['agent']}")
            if lines:
                sections["history_block"] = "Conversation History:\n" + "\n".join(lines)

        parts: list[str] = []
        if sections["agent_context"]:
            parts.append(sections["agent_context"])
        if sections["history_block"]:
            parts.append(sections["history_block"])
        parts.append(sections["instructions"])
        parts.append(sections["context_block"])
        parts.append(sections["question_block"])

        prompt = "\n\n".join(parts)
        return prompt, sections

    def _format_final_response(
        self,
        *,
        query: str,
        response: str,
        source_references: list[dict],
        processed_query: str,
        start_time: datetime,
        context_length: int,
        results_count: int,
    ) -> dict[str, Any]:
        resp = CITE_INLINE_RE.sub("", response or "").strip()
        resp = re.sub(r"\n{3,}", "\n\n", resp)

        if self.config.attach_sources_in_answer and source_references:
            cites = [
                f"[{self.config.citation_label}: {s.get('source', 'Unknown')}, page {s.get('page', '?')}]"
                for s in source_references[: self.config.max_source_references]
            ]
            resp = (resp.rstrip() + "\n\n" + " ".join(cites)).strip()

        return {
            "success": True,
            "result": {
                "answer": resp,
                "sources": source_references,
                "search_metrics": {
                    "query": query,
                    "processed_query": processed_query,
                    "results_count": results_count,
                    "context_tokens": context_length,
                    "processing_time": (datetime.now(UTC) - start_time).total_seconds(),
                },
            },
        }

    def _create_no_results_response(
        self, query: str, processed_query: str, start_time: datetime
    ) -> dict[str, Any]:
        return {
            "success": True,
            "result": {
                "answer": "I couldn't find relevant information to answer your question.",
                "sources": [],
                "search_metrics": {
                    "query": query,
                    "processed_query": processed_query,
                    "results_count": 0,
                    "context_tokens": 0,
                    "processing_time": (datetime.now(UTC) - start_time).total_seconds(),
                },
            },
        }
