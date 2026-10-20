from __future__ import annotations

import inspect

from src.domains.agentverse.agents.mixins.utils.LLMCallsMixin.base import require
from src.domains.agentverse.agents.mixins.utils.LLMCallsMixin.helpers.helpers import filter_kwargs
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)


class ImageGenMixin:
    async def llm_image_generate_async(self, agent, prompt: str, **opts):
        """
        Single, explicit contract:
        delegate to agent.image.generate_image_async(prompt, ...).
        """
        require(agent, "image_out")
        img = getattr(self, "image", None)
        if img is None:
            raise RuntimeError("No image backend bound on agent.image")

        if not hasattr(img, "generate_image_async"):
            raise RuntimeError("Image backend missing generate_image_async")

        sig = inspect.signature(img.generate_image_async)
        if "response_format" in sig.parameters and "response_format" not in opts:
            opts = dict(opts)
            opts["response_format"] = "b64_json"

        args = filter_kwargs(img.generate_image_async, prompt=prompt, **opts)

        AGENTVERSE_LOGGER.info(
            LogDomain.COGNITION_BAY,
            "[🧠 IMAGE][🎨 GENERATE] image_generate_start",
            extra={
                "agent_id": getattr(agent, "id", None),
                "agent_name": getattr(agent, "name", None),
                "prompt_len": len(prompt or ""),
                "option_keys": [k for k in args.keys() if k != "prompt"],
                "backend_type": type(img).__name__,
            },
        )

        return await img.generate_image_async(**args)
