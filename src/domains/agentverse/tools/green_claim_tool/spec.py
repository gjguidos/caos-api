from __future__ import annotations

from src.domains.agentverse.tools.spec import ToolSpec

green_claim_tool = ToolSpec(
    key="green_claim",
    name="Green Claim Analyzer",
    description="Analyzes text for greenwashing and environmental compliance violations against the ICC Responsible Environmental Marketing Communications Checklist.",
    version="1.0.0",
    metadata={
        "ownership": "global",
        "provider": "tools:green_claim",
        "execution_mode": "pure",
        "route": "pure",
        "llm_free": True,
        "outputs": [
            {"key": "answer", "label": "Analysis", "type": "string", "path": "answer"},
        ],
        "ui": {
            "config": {
                "chat_input": {
                    "id": "chat_input",
                    "placeholder": "Enter your green claim details...",
                    "file_upload": True,
                    "flag": "--text",
                    "required": True,
                },
                "group_label_map": {},
            },
            "components": [],
        },
    },
)
