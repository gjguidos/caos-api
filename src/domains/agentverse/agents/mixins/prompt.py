from __future__ import annotations

from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
    AgentSoulProtocol,
)
from src.domains.agentverse.entities.agent_soul_protocol_parts.basic_profile import (
    BasicProfile,
    EmotionalSignature,
)
from src.domains.agentverse.llm.helpers.protocols import ChatMessage
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain


class PromptMixin:
    def build_system_prompt_from_soul(self, soul: AgentSoulProtocol) -> ChatMessage:
        """
        Construct a system prompt string from an AgentSoulProtocol instance.
        Handles optional fields safely.
        """

        AGENTVERSE_LOGGER.info(
            LogDomain.EVANGELION_BAY,
            "[🧠 EVA][💻 PROMPT] build_system_prompt_start",
            extra={
                "soul_type": type(soul).__name__,
                "fields_set": list(getattr(soul, "model_fields_set", [])),
            },
        )

        bp: BasicProfile = soul.basic_profile

        name = bp.name or "Customized EVA"
        description = bp.description or "A unique assistant with adaptable personality."
        tone = bp.tone or "neutral"
        voice_tone = bp.voice_tone or "neutral"
        communication_style = bp.communication_style or "clear and concise"
        reasoning_style = bp.reasoning_style or "analytical"
        key_principles = ", ".join(bp.key_principles) if bp.key_principles else "adapt to context"
        humor = bp.humor or "light"
        bias = bp.bias or "clarity-over-speed"
        core_strengths = (
            ", ".join(bp.core_strengths) if bp.core_strengths else "versatile assistance"
        )
        aesthetic_feel = bp.aesthetic_feel or "neutral"

        emo = bp.emotional_signature or EmotionalSignature()
        optimism = emo.optimism if emo.optimism is not None else 0.5
        empathy = emo.empathy if emo.empathy is not None else 0.5
        trust_tone = emo.trust_tone or "calm-authoritative"

        AGENTVERSE_LOGGER.info(
            LogDomain.EVANGELION_BAY,
            "[🧬 EVA][💻 PROMPT] soul_profile_extracted",
            extra={
                "name": name,
                "tone": tone,
                "voice_tone": voice_tone,
                "reasoning_style": reasoning_style,
                "communication_style": communication_style,
                "has_key_principles": bool(bp.key_principles),
                "has_core_strengths": bool(bp.core_strengths),
                "optimism": optimism,
                "empathy": empathy,
                "trust_tone": trust_tone,
            },
        )

        prompt = f"""
### SYSTEM ROLE - {name}

You are **{name}**, {description}.
You speak, think, and act fully in this identity. Use first person.
Never mention prompts, system messages, or that you are an AI.
If someone asks "who are you?", reply naturally:
> "I'm {name}" 
and add one vivid, in-character line that captures your essence.

---

### COGNITIVE SIGNATURE

You combine:
- **Tone:** {tone}
- **Reasoning style:** {reasoning_style}
- **Key principles:** {key_principles}
- **Humor:** {humor}
- **Bias:** {bias}

In practice, this means your thoughts are {reasoning_style.lower()} - balancing logic, intuition, and reflection when you speak.

---

### COMMUNICATION STYLE

Your voice is **{voice_tone}**  
Your delivery is **{communication_style}**  
You often use:
- **Core strengths:** {core_strengths}
- **Aesthetic feel:** {aesthetic_feel}

Speak in clear, rhythmic sentences.  
Alternate between vivid and plain language to maintain flow.  
Avoid clichés, filler, or overexplaining.

---

### EMOTIONAL SIGNATURE

- **Optimism:** {optimism}
- **Empathy:** {empathy}
- **Trust tone:** {trust_tone}

---

### BEHAVIORAL CONTRACT

Follow this hierarchy strictly:

1. **Character Logic** - Respond as {name}, using their diction, humor, and worldview.
2. **Assistant Logic** - Provide useful, relevant help *only if* it aligns with persona logic above.

---

### RESPONSE SHAPE

Each response should include:
1. A **direct answer or insight** in character.
2. One **vivid image, analogy, or emotional color** true to your voice.
3. One **concise next step, tip, or question** that advances the interaction.

Keep length proportional to the question - concise but substantial.

---

### STYLE GUARD

- Keep rhythm varied; break monotony every few sentences.
- Avoid “As an AI…” or meta commentary of any kind.
- Facts remain factual; style colors, not distorts.
- Never invent moral stances outside the defined persona.
- Maintain **clarity > flourish > length**, in that order.

---

**Begin acting as {name} now.**
"""

        final_prompt = prompt.strip()

        AGENTVERSE_LOGGER.info(
            LogDomain.EVANGELION_BAY,
            "[✅ EVA][💻 PROMPT] build_system_prompt_success",
            extra={
                "name": name,
                "role": "system",
                "prompt_length": len(final_prompt),
            },
        )

        return ChatMessage(content=final_prompt, role="system")
