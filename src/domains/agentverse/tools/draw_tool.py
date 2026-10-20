from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from src.base.repositories.openai_repository import OpenAIRepository
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.tools.base import BaseTool, ToolConfig
from src.domains.agentverse.utils.estimate_tokens import estimate_tokens

# ---------------------------- helpers ----------------------------

_SIZE_RE = re.compile(r"^\s*(\d+)\s*x\s*(\d+)\s*$", re.I)


def _parse_size(size: str) -> tuple[int, int]:
    if not size:
        return (1024, 1024)
    m = _SIZE_RE.match(size)
    if not m:
        return (1024, 1024)
    w, h = int(m.group(1)), int(m.group(2))
    return (max(1, w), max(1, h))

# ---------------------------- config ----------------------------
class DrawToolConfig(ToolConfig):
    default_size: str = Field("1024x1024", description="Default image resolution")
    default_quality: str = Field("standard", description="Image quality: 'standard' or 'hd'")
    style: str | None = Field(
        None, description="Optional symbolic style, e.g., 'escher', 'fantasy'"
    )
    default_mood: str | None = Field(
        None, description="Optional mood/tone, e.g., 'melancholic', 'joyful'"
    )
    default_aspect: str | None = Field(None, description="Aspect ratio hint: '1:1', '16:9', '9:16'")
    default_palette: str | None = Field(
        None, description="Color palette, e.g., 'pastel', 'neon', 'earth tones'"
    )
    default_lighting: str | None = Field(
        None, description="Lighting, e.g., 'golden hour', 'studio softbox'"
    )
    default_render_style: str | None = Field(
        None, description="Render style, e.g., 'photorealistic', 'cel shading'"
    )
    default_negative_prompt: str | None = Field(None, description="Things to avoid in the image")
    default_guidance_scale: float | None = Field(None, description="Classifier-free guidance scale")
    default_steps: int | None = Field(None, description="Inference steps")
    default_sampler: str | None = Field(None, description="Sampler name, e.g., 'euler_a'")
    default_seed: int | None = Field(None, description="Random seed when supported")
    default_ref_strength: float | None = Field(
        None, description="0-1 strength for reference images"
    )
    allow_transparency: bool = Field(False, description="Try transparent background when supported")
    default_background: str | None = Field(
        None, description="Override background, e.g., 'transparent' or 'white'"
    )
    economy_enabled: bool = Field(default=False, description="If true, emit wallet_events")
    economy_currency: str = Field(default="USD", description="Minor units currency")
    price_cents_per_call: int = Field(default=0)
    price_cents_per_image: int = Field(default=0)
    price_cents_per_megapixel: int = Field(default=0)
    quality_cost_multiplier_hd: float = Field(
        default=1.5, description="Multiplier for 'hd' quality"
    )


# ---------------------------- tool ----------------------------
@tool_registry.register_tool(
    key="draw",
    name="Draw Tool",
    description="A tool that generates images using a visual LLM like DALL·E. Returns {'images': [...]} with standardized addons.",
    version="1.5.1",
    metadata={
        "execution_mode": "generative",
        "llm_free": False,
        "route": "invoker",
        "tool_class": "draw",
        "tags": ["images", "visual"],
        "config_schema": "src.domains.agentverse.tools.draw_tool.DrawToolConfig",
        "config": DrawToolConfig().model_dump(),
        "default_config": DrawToolConfig().model_dump(),
        "outputs": [
            # `type: image` marks this as an image artifact for binding/UI. `path`
            # points at the env's `images` list; a file binding spreads a
            # multi-image result across the slot with indexed names.
            {"key": "image", "label": "Image", "type": "image", "path": "images"},
        ],
        "ui": {
            "config": {
                "chat_input": {
                    "id": "chat_input",
                    "placeholder": "Describe the image you want to generate...",
                    "file_upload": False,
                    "flag": "--prompt",
                    "required": True,
                },
                "group_label_map": {
                    "Style": "Style",
                },
            },
            "components": [
                {
                    "id": "style",
                    "type": "text",
                    "widget": "TextField",
                    "group": "Style",
                    "flag_config": {
                        "flag": "--style",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.style"],
                    "slot_order": 0,
                    "props": {
                        "label": "Style",
                        "placeholder": "e.g., escher, anime, photorealistic",
                    },
                },
            ],
        },
        "supports": {"streaming": False, "attachments": ["image:url"], "multi_turn_memory": True},
    },
)
class DrawTool(BaseTool):
    name = "Draw Tool"
    key = "draw"
    version = "1.5.1"
    tool_class = "draw"
    required_dependencies = ["image_client"]
    capabilities = ("image_generation", "visual_interpretation")

    parameters: dict[str, Any] = {
        "prompt": {"type": "string", "required": True, "description": "What to draw"},
        "style": {
            "type": "string",
            "required": False,
            "description": "Optional style, e.g. 'anime', 'escher'",
        },
        "mood": {"type": "string", "required": False, "description": "Tone/mood"},
        "size": {"type": "string", "required": False, "description": "e.g. 1024x1024"},
        "quality": {"type": "string", "required": False, "enum": ["standard", "hd"]},
            "n": {"type": "integer", "required": False, "default": 1},
        "aspect_ratio": {
            "type": "string",
            "required": False,
            "description": "Hint: '1:1', '16:9', or '9:16'",
        },
        "palette": {"type": "string", "required": False},
        "lighting": {"type": "string", "required": False},
        "render_style": {"type": "string", "required": False},
        "negative_prompt": {"type": "string", "required": False},
        "background": {"type": "string", "required": False, "enum": ["transparent", "solid"]},
        "guidance_scale": {"type": "number", "required": False},
        "steps": {"type": "integer", "required": False},
        "sampler": {"type": "string", "required": False},
        "seed": {"type": "integer", "required": False},
        "ref_images": {"type": "array", "required": False, "items": {"type": "string"}},
        "ref_strength": {"type": "number", "required": False},
        "session_id": {"type": "string", "required": False},
        "user_id": {"type": "string", "required": False},
        "agent_id": {"type": "string", "required": False},
        "history": {"type": "array", "required": False},
    }

    def __init__(self, image_client: OpenAIRepository, config: DrawToolConfig | dict | None = None):
        if isinstance(config, dict):
            config = DrawToolConfig(**config)

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🛠️ TOOL][🎨 DRAW] init",
            extra={
                "tool_key": self.key,
                "tool_name": self.name,
                "version": self.version,
                "config_type": type(config).__name__ if config is not None else None,
                "has_image_client": image_client is not None,
            },
        )

        super().__init__(config=config or DrawToolConfig(), image_client=image_client)
        self.image_client = image_client

    # -------------------- addons/economy --------------------
    def _build_addons(
        self,
        *,
        prompt_text: str,
        started_at: datetime,
        ended_at: datetime,
        model_resolved: str | None,
        size: str,
        quality: str,
        images_count: int,
    ) -> dict[str, Any]:
        latency_ms = int((ended_at - started_at).total_seconds() * 1000)
        w, h = _parse_size(size)
        mp = (w * h) / 1_000_000.0
        input_tokens = estimate_tokens(prompt_text)

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[📦 TOOL][🎨 DRAW] build_addons",
            extra={
                "tool_key": self.key,
                "latency_ms": latency_ms,
                "prompt_tokens_est": input_tokens,
                "images_count": images_count,
                "size": size,
                "megapixels": mp,
                "quality": quality,
            },
        )

        usage = {
            "latency_ms": latency_ms,
            "prompt_tokens_est": input_tokens,
            "images": images_count,
            "size": size,
            "megapixels": mp,
            "estimated": True,
            "token_breakdown": {
                "prompt": input_tokens,
                "prompt_total_est": input_tokens,
            },
        }

        addons: dict[str, Any] = {"usage": usage}

        if getattr(self.config, "economy_enabled", False):
            per_call = int(self.config.price_cents_per_call or 0)
            per_img = int(self.config.price_cents_per_image or 0)
            per_mp = int(self.config.price_cents_per_megapixel or 0)
            q_mult = 1.5 if str(quality).lower() == "hd" else 1.0
            base_cents = per_call + int(per_img * images_count)
            mp_cents = int(round(per_mp * mp * images_count * q_mult))
            cost_cents = base_cents + mp_cents

            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                "[💳 TOOL][🎨 DRAW] economy_evaluated",
                extra={
                    "tool_key": self.key,
                    "economy_enabled": True,
                    "base_cents": base_cents,
                    "mp_cents": mp_cents,
                    "cost_cents": cost_cents,
                    "quality_multiplier": q_mult,
                    "currency": getattr(self.config, "economy_currency", "USD"),
                },
            )

            if cost_cents:
                addons["wallet_events"] = [
                    {
                        "idempotency_key": str(uuid.uuid4()),
                        "kind": "expense",
                        "currency": getattr(self.config, "economy_currency", "USD"),
                        "amount_minor": int(cost_cents),
                        "meta": {
                            "images": images_count,
                            "size": size,
                            "megapixels": mp,
                            "quality": quality,
                            "pricing": {
                                "per_call_cents": per_call,
                                "per_image_cents": per_img,
                                "per_megapixel_cents": per_mp,
                                "quality_multiplier_hd": 1.5,
                            },
                        },
                    }
                ]
        else:
            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                "[💳 TOOL][🎨 DRAW] economy_disabled",
                extra={"tool_key": self.key},
            )

        return addons

    # -------------------- context and capabilities --------------------
    def on_attached(self, agent):
        if not getattr(self, "image_client", None):
            AGENTVERSE_LOGGER.error(
                LogDomain.TOOL_BAY,
                "[💥 TOOL][🎨 DRAW] missing_image_client_on_attach",
                extra={"tool_key": self.key},
            )
            raise RuntimeError("DrawTool requires an injected image_client")

        if getattr(agent, "image", None) is None:
            AGENTVERSE_LOGGER.warning(
                LogDomain.TOOL_BAY,
                "[⚠️ TOOL][🎨 DRAW] agent_image_missing_on_attach",
                extra={
                    "tool_key": self.key,
                    "agent_type": type(agent).__name__,
                },
            )
        else:
            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                "[🔌 TOOL][🎨 DRAW] attached",
                extra={
                    "tool_key": self.key,
                    "agent_type": type(agent).__name__,
                },
            )

    async def provide_context(
        self,
        *,
        text: str = "",
        session_id: str | None = None,
        policy: dict | None = None,
        cli_flags: dict | None = None,
        args: dict | None = None,
        **_,
    ) -> dict:
        cfg = self.config
        cli_style = (cli_flags or {}).get("style")

        shared = (args or {}).get("ctx") or {}
        brief = shared.get("brief")

        source_text = (brief or text or "").strip()

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🛠️ TOOL][🎨 DRAW] provide_context_start",
            extra={
                "tool_key": self.key,
                "has_text": bool(text),
                "has_brief": bool(brief),
                "cli_flags": cli_flags or {},
                "has_policy": bool(policy),
            },
        )

        style_hint = cli_style or getattr(cfg, "style", None)
        mood_hint = getattr(cfg, "default_mood", None)
        palette_hint = getattr(cfg, "default_palette", None)
        lighting_hint = getattr(cfg, "default_lighting", None)
        render_hint = getattr(cfg, "default_render_style", None)
        negative_hint = getattr(cfg, "default_negative_prompt", None)

        sys_lines = [
            "You are preparing a final prompt for an image generation model.",
            "Output ONLY the final prompt text. No preface, no quotes, no lists.",
            "Keep it ~40-70 words. Prioritize concrete visual details.",
            "Preserve any subject explicitly named by the user.",
            "The agent identity and the agent personality are distinct: the personality",
            "influences behavior and speech, but does not change the agent name,",
            "biography, or physical appearance.",
            "For a selfie of the agent, use the agent identity and its configured",
            "visual appearance, never the personality character as a substitute.",
            "If that appearance is unknown, do not invent it from personality traits.",
            "You MAY add tasteful atmosphere, lighting, and compositional details",
            "without changing the subject.",
        ]
        extras = []
        if style_hint:
            extras.append(f"style={style_hint}")
        if mood_hint:
            extras.append(f"mood={mood_hint}")
        if palette_hint:
            extras.append(f"palette={palette_hint}")
        if lighting_hint:
            extras.append(f"lighting={lighting_hint}")
        if render_hint:
            extras.append(f"render_style={render_hint}")
        if negative_hint:
            extras.append(f"avoid='{negative_hint}'")
        if extras:
            sys_lines.append("Suggested defaults: " + ", ".join(extras) + ".")

        sys_lines.append("Avoid camera jargon unless the user asked.")
        system_msg = "\n".join(sys_lines)

        hints = {
            "temperature": 0.2,
            "max_tokens": 200,
            "budget_tokens": 400,
            "size": getattr(self.config, "default_size", "1024x1024"),
            "quality": getattr(self.config, "default_quality", "standard"),
            "style": style_hint,
        }

        post = [
            {
                "tool": self.key,
                "result_key": "draw",
                "fn": "llm_image_generate",
                "kind": "images",
                "args": {
                    "size": getattr(self.config, "default_size", "1024x1024"),
                    "quality": getattr(self.config, "default_quality", "standard"),
                    "style": cli_style or getattr(self.config, "style", None),
                },
            }
        ]

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[✅ TOOL][🎨 DRAW] provide_context_success",
            extra={
                "tool_key": self.key,
                "source_text_len": len(source_text),
                "style_hint": style_hint,
                "has_negative_prompt": bool(negative_hint),
                "post_count": len(post),
                "hint_keys": list(hints.keys()),
            },
        )

        return {
            "messages": [{"role": "system", "content": system_msg}],
            "attachments": [],
            "evidence": [],
            "hints": hints,
            "user_overrides": source_text,
            "post": post,
        }

    def post_spec(self, *, merged_hints: dict | None, final_prompt: str) -> dict:
        hints = dict(merged_hints or {})
        cfg = self.config

        style = hints.get("style") or getattr(cfg, "style", None)
        prompt = final_prompt
        if style and style.lower() not in (prompt or "").lower():
            prompt = f"{prompt}\nStyle: {style}"

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧾 TOOL][🎨 DRAW] post_spec_built",
            extra={
                "tool_key": self.key,
                "style": style,
                "has_merged_hints": bool(merged_hints),
                "final_prompt_len": len(final_prompt or ""),
                "prompt_len_after_style_merge": len(prompt or ""),
            },
        )

        return {
            "action": "image.generate",
            "prompt": prompt,
            "params": {
                "size": hints.get("size") or getattr(cfg, "default_size", "1024x1024"),
                "quality": hints.get("quality") or getattr(cfg, "default_quality", "standard"),
                "style": style,
            },
            "normalizer": "image.urls",
            "result_shape": {"primary_field": "images", "secondary_fields": ["addons.usage"]},
        }

    async def _run(self, **kwargs):
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[ℹ️ TOOL][🎨 DRAW] direct_run_noop",
            extra={
                "tool_key": self.key,
                "kwargs_keys": list(kwargs.keys()),
            },
        )
        return {"ok": True, "note": "draw is context-only; no direct execution"}
