from __future__ import annotations

# --- defaults used by config/store ---
DEFAULT_MAX_STEPS_PER_CALL = 50
DEFAULT_RUN_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days

# Runtime plumbing the tool manager injects, which must never be folded into a
# run's declared inputs. The workflow tool's own control flags are *not* listed
# here - they are derived from ``WorkflowTool._run``'s signature in
# ``__init__.py``, so adding a parameter cannot silently reintroduce the leak.
#
# `text` is deliberately NOT reserved: workflows such as `mini_workflow` bind
# `${inputs.text}` to the invoking turn's text, and that is load-bearing.
RESERVED_EXECUTION_KEYS: frozenset[str] = frozenset(
    {
        "context",
        "socket_emitter",
        "request",
        "history",
        "policy",
        "agent",
        "allow_llm",
        "kwargs",
        # build_local_tool_call_kwargs injects this alongside `text`.
        "raw_text",
    }
)
