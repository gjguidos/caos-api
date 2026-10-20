from __future__ import annotations

from src.domains.agentverse.tools.auditor_tool.config import AuditorToolConfig
from src.domains.agentverse.tools.spec import ToolSpec

auditor_tool = ToolSpec(
    key="auditor",
    name="Auditor Tool",
    description=(
        "Audits a piece of text against the agent's rule packs "
        "(e.g. green-claims, GDPR) and returns a structured compliance report."
    ),
    version="0.1.0",
    metadata={
        # -------- core behaviour --------
        "execution_mode": "context_only",
        "llm_free": False,
        "route": "invoker",
        "tool_class": "auditor",
        "tags": ["compliance", "legal", "audit", "policy"],
        "required_rulepacks": True,
        "min_rulepacks": 1,
        "required_rulepacks_hint": [
            "green_claims_compliance",
            "gdpr_compliance",
        ],
        "config_schema": ("src.domains.agentverse.tools.auditor_tool.AuditorToolConfig"),
        "config": AuditorToolConfig().model_dump(),
        "default_config": AuditorToolConfig().model_dump(),
        "outputs": [
            {"key": "answer", "label": "Audit report", "type": "string", "path": "answer"},
        ],
        # -------- UI hints --------
        "ui": {
            "category": "compliance",
            "display_hints": ["answer-first", "detailed"],
            "config": {
                "chat_input": {
                    "id": "chat_input",
                    "placeholder": "Enter your text here...",
                    "file_upload": False,
                    "flag": "--text",
                    "required": True,
                },
                "group_label_map": {
                    "Audit": "Audit settings",
                    "Rule Packs": "Rule packs",
                    "Advanced": "Advanced",
                },
            },
            "components": [
                {
                    "id": "mode",
                    "type": "select",
                    "widget": "PillGroup",
                    "group": None,
                    "flag_config": {
                        "flag": "--mode",
                        "required": True,
                    },
                    "render_paths": ["workspace.conversationArea.inputSection.toolMode"],
                    "slot_order": 0,
                    "props": {
                        "options": [
                            {"value": "audit", "label": "Audit"},
                        ],
                    },
                },
                {
                    "id": "text",
                    "type": "free_text",
                    "widget": "ChatInput",
                    "group": None,
                    "flag_config": {
                        "flag": "--text",
                        "required": True,
                    },
                    "render_paths": ["chatInput"],
                    "slot_order": None,
                    "props": {
                        "placeholder": "Paste the text you want to audit...",
                    },
                },
                # --- Audit settings panel ---
                {
                    "id": "domain",
                    "type": "select",
                    "widget": "SelectField",
                    "group": "Audit",
                    "flag_config": {
                        "flag": "--domain",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.auditSettings"],
                    "slot_order": 0,
                    "props": {
                        "label": "Domain",
                        "options": [
                            {"value": "green_claims", "label": "Green claims"},
                            {"value": "gdpr", "label": "GDPR"},
                        ],
                    },
                },
                {
                    "id": "strict",
                    "type": "boolean",
                    "widget": "ToggleField",
                    "group": "Audit",
                    "flag_config": {
                        "flag": "--strict",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.auditSettings"],
                    "slot_order": 1,
                    "props": {
                        "label": "Strict mode",
                    },
                },
                # --- Rule packs panel ---
                {
                    "id": "pack_ids",
                    "type": "multiselect",
                    "widget": "CheckboxGroupField",
                    "group": "Rule Packs",
                    "flag_config": {
                        "flag": "--pack",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.rulePacks"],
                    "slot_order": 0,
                    "props": {
                        "label": "Rule packs",
                        "options": [
                            {
                                "value": "green_claims_compliance",
                                "label": "Green claims compliance",
                            },
                            {
                                "value": "gdpr_compliance",
                                "label": "GDPR compliance",
                            },
                        ],
                    },
                },
                # --- Advanced panel ---
                {
                    "id": "max_issues",
                    "type": "number",
                    "widget": "NumberField",
                    "group": "Advanced",
                    "flag_config": {
                        "flag": "--max-issues",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.advanced"],
                    "slot_order": 0,
                    "props": {
                        "label": "Max issues",
                        "min": 1,
                        "max": 100,
                        "step": 1,
                    },
                },
            ],
            "shortcuts": [
                {
                    "label": "Green Claims Audit",
                    "overrides": {"domain": "green_claims"},
                },
                {
                    "label": "GDPR Text Check",
                    "overrides": {"domain": "gdpr"},
                },
            ],
        },
        # -------- capabilities metadata --------
        "supports": {
            "streaming": True,
            "attachments": [],
            "multi_turn_memory": True,
        },
        # -------- CLI help (for your tooling layer) --------
        "cli": {
            "command": "audit",
            "args": [
                {
                    "name": "--text,-t",
                    "help": "Text to audit for compliance.",
                    "required": True,
                },
                {
                    "name": "--domain,-d",
                    "help": "Rule domain (e.g. green_claims|gdpr).",
                    "required": False,
                },
                {
                    "name": "--pack,-p",
                    "help": "Explicit rule pack id (e.g. green_claims_compliance).",
                    "required": False,
                },
                {
                    "name": "--strict",
                    "help": "Treat warnings as errors in overall judgement.",
                    "action": "store_true",
                },
                {
                    "name": "--out,-o",
                    "help": "Output file path (JSON). If omitted → stdout.",
                },
                {
                    "name": "--pretty",
                    "help": "Pretty-print JSON.",
                    "action": "store_true",
                },
                {
                    "name": "--quiet",
                    "help": "Suppress non-JSON logs.",
                    "action": "store_true",
                },
            ],
            "examples": [
                '@audit Our product is 100% eco-friendly." -d green_claims',
                'agentverse-tools audit -t "We never share your data with third parties." -d gdpr',
            ],
        },
    },
)
