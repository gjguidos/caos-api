from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.domains.agentverse.llm.helpers import (
    AgentLike,
    ChatMessage,
    ToolManagerLike,
    format_step,
    post_steps,
)
from src.domains.agentverse.llm.helpers.context_phase import (
    collect_context_deltas,
    is_non_system_tool,
    is_pure_tool,
)
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain
from src.domains.agentverse.personalities.utils.resolve_personality import resolve_personality


def _extract_final_text(answer_raw: Any) -> str:
    if isinstance(answer_raw, str):
        return answer_raw.strip()
    if isinstance(answer_raw, dict):
        ch = (answer_raw.get("choices") or [None])[0]
        if isinstance(ch, dict):
            msg = ch.get("message") or {}
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg["content"].strip()
            if isinstance(ch.get("text"), str):
                return ch["text"].strip()
        for key in ("text", "content"):
            if isinstance(answer_raw.get(key), str):
                return answer_raw[key].strip()
    return ""


def _norm(s):
    return (s or "").strip().lower().replace(" ", "_")


def _preview(text: str | list | None, n: int = 160) -> str:
    if isinstance(text, list):
        s = str(text)[:n]
        return s + ("…" if len(str(text)) > n else "")
    s = (text or "").strip()
    return s[:n] + ("…" if len(s) > n else "")


async def llm(
    *,
    agent: AgentLike,
    user_text: str,
    providers: list[str],
    tool_manager: ToolManagerLike,
    policy: dict[str, Any] | None = None,
    session_id: str | None = None,
    history: list[ChatMessage] | None = None,
    context: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool_keys = [k for k in (providers or []) if is_non_system_tool(k) and not is_pure_tool(k)]

    AGENTVERSE_LOGGER.info(
        LogDomain.COGNITION_BAY,
        "[🧠 LLM][🚀 INVOKE] start",
        extra={
            "provider_count": len(providers or []),
            "eligible_provider_count": len(tool_keys),
            "history_len": len(history or []),
            "user_text_len": len(user_text or ""),
            "provider_keys": tool_keys,
        },
    )

    if not tool_keys:
        AGENTVERSE_LOGGER.warning(
            LogDomain.COGNITION_BAY,
            "[🧠 LLM][🚫 INVOKE] no_llm_capable_providers",
            extra={"providers": providers or []},
        )
        return {
            "answer_raw": {"success": False, "message": "No LLM-capable providers."},
            "answer": "Sorry - no tool could run for this request.",
            "context": {"attachments": [], "evidence": [], "hints": {}},
            "tool_results": {},
        }

    overrides = overrides or {}
    cli_flags = overrides.get("cli_flags") or {}
    args = overrides.get("args") or {}

    override_key = (
        overrides.get("persona_name")
        or (overrides.get("args") or {}).get("personality")
        or (overrides.get("cli_flags") or {}).get("personality")
    )

    soul = resolve_personality(
        getattr(agent, "personality_key", None), override_key=_norm(override_key)
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.COGNITION_BAY,
        "[🧠 LLM][🧬 PERSONA] resolved",
        extra={
            "override_persona": override_key,
            "resolved_persona_type": type(soul).__name__,
        },
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.COGNITION_BAY,
        f"[🧠 LLM][🧩 CONTEXT] collecting deltas from: {tool_keys}",
        extra={},
    )

    merged, ctx_tools = await collect_context_deltas(
        tool_keys=tool_keys,
        text=user_text,
        session_id=session_id,
        policy=policy,
        cli_flags=cli_flags,
        args=args,
        tool_manager=tool_manager,
        history=history,
        context=context,
        request={"raw_text": user_text, "overrides": overrides},
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.COGNITION_BAY,
        "[🧠 LLM][🧩 CONTEXT] deltas_collected",
        extra={
            "context_tool_count": len(ctx_tools or []),
            "context_tools": [t.key for t in ctx_tools],
            "system_override_count": len(merged.system_overrides or []),
            "has_user_override": bool(merged.user_overrides),
            "message_count": len(merged.messages or []),
            "attachment_count": len(merged.attachments or []),
            "evidence_count": len(merged.evidence or []),
            "hint_keys": sorted((merged.hints or {}).keys()),
        },
    )

    system_prompt = agent.build_system_prompt_from_soul(soul=soul)
    final_user_message = merged.user_overrides or user_text

    params = {
        k: merged.hints.get(k)
        for k in (
            "max_tokens",
            "temperature",
            "top_p",
            "presence_penalty",
            "frequency_penalty",
            "stop",
        )
        if merged.hints.get(k) is not None
    }

    context_system_messages = [
        m for m in (merged.messages or []) if m.get("role") == "system" and m.get("content")
    ]

    system_parts = [system_prompt["content"]]

    if merged.system_overrides or context_system_messages:
        AGENTVERSE_LOGGER.info(
            LogDomain.COGNITION_BAY,
            "[🧠 LLM][🧱 SYSTEM] composing_overrides",
            extra={
                "override_count": len(merged.system_overrides or []),
                "context_system_count": len(context_system_messages),
            },
        )

    for m in merged.system_overrides or []:
        if m.get("role") == "system" and m.get("content"):
            system_parts.append(m["content"])

    for m in context_system_messages:
        system_parts.append(m["content"])

    combined_system = {
        "role": "system",
        "content": "\n\n---\n\n".join(system_parts),
    }

    merged_non_system_messages = [m for m in (merged.messages or []) if m.get("role") != "system"]

    messages = [
        combined_system,
        *merged_non_system_messages,
        {"role": "user", "content": final_user_message},
    ]

    AGENTVERSE_LOGGER.info(
        LogDomain.COGNITION_BAY,
        "[🧠 LLM][📤 OUTBOUND] messages_ready",
        extra={
            "message_count": len(messages),
            "param_keys": sorted(params.keys()),
            "user_preview": _preview(final_user_message),
            "system_len": len(combined_system.get("content") or ""),
        },
    )

    raw = await agent.llm_chat_async(messages=messages, **params)

    model_text = _extract_final_text(raw)

    AGENTVERSE_LOGGER.info(
        LogDomain.COGNITION_BAY,
        "[🧠 LLM][📥 INBOUND] completion_received",
        extra={
            "output_len": len(model_text or ""),
            "output_preview": _preview(model_text),
        },
    )

    formatted = await format_step(tools=ctx_tools, raw_text=model_text)

    AGENTVERSE_LOGGER.info(
        LogDomain.COGNITION_BAY,
        "[🧠 LLM][🧾 FORMAT] format_complete",
        extra={
            "formatted_len": len(formatted.answer),
            "has_addons": bool(formatted.addons),
            "data_keys": sorted(formatted.data) if formatted.data else [],
        },
    )

    post_results = await post_steps(
        tools=ctx_tools,
        agent=agent,
        final_prompt=formatted.answer,
        hints=merged.hints,
        cli_flags=cli_flags,
        tool_manager=tool_manager,
    )

    first_presented = next(
        ("\n".join(r.presented_lines).strip() for r in post_results.values() if r.presented_lines),
        "",
    )
    top_answer = (first_presented or formatted.answer or model_text or "").strip()

    ctx = {
        "attachments": merged.attachments,
        "evidence": merged.evidence,
        "hints": merged.hints,
    }

    # When a context_based tool (local or proxy/external) constructed an expanded
    # prompt, surface its text so the chat agent can persist it as the model-facing
    # user turn instead of the raw @tool command. Covers multimodal proxy overrides
    # too (text extracted; images are per-turn attachments, not replayed). Empty for
    # pure/llm_only/plain-chat turns -> raw is kept.
    if rendered_user := merged.rendered_user_text:
        ctx["rendered_user"] = rendered_user

    tool_results_dict: dict[str, Any] = {}
    for key, pr in (post_results or {}).items():
        tool_results_dict[key] = asdict(pr)

    AGENTVERSE_LOGGER.info(
        LogDomain.COGNITION_BAY,
        "[🧠 LLM][✅ INVOKE] completed",
        extra={
            "top_answer_len": len(top_answer or ""),
            "tool_result_count": len(tool_results_dict),
            "attachment_count": len(ctx["attachments"] or []),
            "evidence_count": len(ctx["evidence"] or []),
            "addon_keys": sorted(formatted.addons.keys()),
        },
    )

    return {
        "answer_raw": raw,
        "context": ctx,
        "tool_results": tool_results_dict,
        # The formatted env owns `answer`, `addons` and `data` - same keys, same
        # emptiness rules the pure path gets from `ToolEnv.as_env`. `answer` is
        # then overridden: a post step's presented lines outrank the formatter.
        **formatted.as_env(),
        "answer": top_answer,
    }
