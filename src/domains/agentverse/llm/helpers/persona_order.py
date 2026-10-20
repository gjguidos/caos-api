from __future__ import annotations

from typing import Any

PERSONA_MARKERS = (
    "PERSONALITY EXECUTION CONTRACT -",  # your PEC
    "You are ",  # identity contract
    "PERSONA FINGERPRINT -",  # fingerprint block
    "RITUAL INFERENCE POLICY",  # ritual policy
)


def _is_persona_block(text: str) -> bool:
    t = (text or "").lower()
    return any(m.lower() in t for m in PERSONA_MARKERS)


def enforce_persona_last(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only one system message and ensure persona parts are last inside it.
    Preserve order among non-persona and persona chunks respectively.
    """
    system_chunks: list[str] = []
    persona_chunks: list[str] = []
    non_system: list[dict[str, Any]] = []

    for m in messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            # Split in case a system message already concatenated multiple blocks
            parts = [p.strip() for p in content.split("\n\n") if p.strip()]
            for p in parts:
                (persona_chunks if _is_persona_block(p) else system_chunks).append(p)
        else:
            non_system.append(m)

    # 1) single system message: non-persona first, persona last
    merged_system = "\n\n".join([*system_chunks, *persona_chunks]).strip()
    out: list[dict[str, Any]] = []
    if merged_system:
        out.append({"role": "system", "content": merged_system})

    # 2) then the rest (assistant context blocks, history, user)
    out.extend(non_system)
    return out
