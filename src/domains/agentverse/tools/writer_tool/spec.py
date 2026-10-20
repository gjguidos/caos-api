from __future__ import annotations

from src.domains.agentverse.tools.spec import ToolSpec
from src.domains.agentverse.tools.writer_tool.config import WriterToolConfig

writer_tool = ToolSpec(
    key="writer",
    name="Writer Tool",
    description=(
        "Assists with drafting, rewriting and polishing copy according to role, "
        "style, mood,audience, and length constraints."
    ),
    version="0.1.0",
    enabled=True,
    metadata={
        # -------- core behaviour --------
        "execution_mode": "context_only",
        "llm_free": False,
        "route": "invoker",
        "rulepack_context": {
            "enabled": True,
            "resolver": "writer",
            "merge_into_config": True,
            "snapshot_key": "rulepack_snapshot",
        },
        "tool_class": "writer",
        "tags": ["writing", "copy", "editing", "assistant"],
        #  path to the config model for backend tooling
        "config_schema": ("src.domains.agentverse.tools.writer_tool.WriterToolConfig"),
        # default config payload (hydrated into ToolSpec.config)
        "config": WriterToolConfig().model_dump(),
        "default_config": WriterToolConfig().model_dump(),
        # -------- declared outputs (bindable in the workflow builder) --------
        # `path` is relative to the tool's env, which for the writer is the
        # `result` object -> `answer` sits at the top.
        "outputs": [
            {"key": "answer", "label": "Answer", "type": "string", "path": "answer"},
        ],
        # -------- UI hints --------
        "ui": {
            "config": {
                "chat_input": {
                    "id": "chat_input",
                    "placeholder": "Enter your text here...",
                    "file_upload": False,
                    "flag": "--text",
                    "required": True,
                },
                "group_label_map": {
                    "Behaviour": "Tool Mode",
                    "Core": "Your request",
                    "Persona": "Voice & audience",
                    "Length": "Output length",
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
                            {"value": "write", "label": "Write", "requiresInput": True},
                            {"value": "rewrite", "label": "Rewrite", "requiresInput": True},
                            {"value": "expand", "label": "Expand", "requiresInput": True},
                            {"value": "summarize", "label": "Summarize", "requiresInput": True},
                        ],
                    },
                },
                {
                    "id": "mode",
                    "type": "select",
                    "widget": "ResponseActionBar",
                    "group": None,
                    "render_paths": ["workspace.conversationArea.responseCard.responseActionBar"],
                    "slot_order": 0,
                    "props": {
                        "options": [
                            {"value": "rewrite", "label": "Rewrite", "requiresInput": True},
                            {"value": "expand", "label": "Expand", "requiresInput": True},
                            {"value": "summarize", "label": "Summarize", "requiresInput": True},
                        ],
                    },
                },
                {
                    "id": "role",
                    "type": "text",
                    "widget": "TextField",
                    "group": "Persona",
                    "groupLabel": "Voice & Audience",
                    "flag_config": {
                        "flag": "--role",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.persona"],
                    "slot_order": 0,
                    "props": {
                        "label": "Role",
                        "placeholder": "e.g., Marketing Manager",
                    },
                },
                {
                    "id": "style",
                    "type": "text",
                    "widget": "TextField",
                    "group": "Persona",
                    "flag_config": {
                        "flag": "--style",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.persona"],
                    "slot_order": 1,
                    "props": {
                        "label": "Style",
                        "placeholder": "e.g., Professional, Casual",
                    },
                },
                {
                    "id": "mood",
                    "type": "text",
                    "widget": "TextField",
                    "group": "Persona",
                    "flag_config": {
                        "flag": "--mood",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.persona"],
                    "slot_order": 2,
                    "props": {
                        "label": "Mood",
                        "placeholder": "e.g., Friendly, Formal",
                    },
                },
                {
                    "id": "audience",
                    "type": "text",
                    "widget": "TextField",
                    "group": "Persona",
                    "flag_config": {
                        "flag": "--audience",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.persona"],
                    "slot_order": 3,
                    "props": {
                        "label": "Audience",
                        "placeholder": "e.g., Customers, Team members",
                    },
                },
                {
                    "id": "tone_of_voice",
                    "type": "multiselect",
                    "widget": "CheckboxGroupField",
                    "group": "Persona",
                    "flag_config": {
                        "flag": "--tone-of-voice",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.persona"],
                    "slot_order": 4,
                    "props": {
                        "label": "Tone of Voice",
                        "options": [
                            {"value": "clear", "label": "Clear"},
                            {"value": "curious", "label": "Curious"},
                            {"value": "confident", "label": "Confident"},
                            {"value": "honest", "label": "Honest"},
                            {"value": "humble", "label": "Humble"},
                            {"value": "optimistic", "label": "Optimistic"},
                            {"value": "playful", "label": "Playful"},
                        ],
                    },
                },
                {
                    "id": "max_char_length",
                    "type": "number",
                    "widget": "NumberField",
                    "group": "Length",
                    "flag_config": {
                        "flag": "--max-chars",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.length"],
                    "slot_order": 0,
                    "props": {
                        "label": "Max Character Length",
                        "min": 0,
                        "max": 5000,  # Aligned with config_fields
                        "step": 10,
                    },
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
            "command": "write",
            "args": [
                {
                    # concrete text to operate on
                    # maps to: request_data.text / source_text
                    "name": "--text",
                    "help": (
                        "Existing text to rewrite, expand or summarize "
                        "(e.g. current product description, draft copy, or long policy)."
                    ),
                    "required": False,
                },
                {
                    # behaviour selector
                    # maps to: request_data.mode
                    "name": "--mode,-M",
                    "help": (
                        "Writer mode: write | rewrite | expand | summarize "
                        "(defaults from context: text => rewrite, topic/idea => write)."
                    ),
                    "required": False,
                },
                {
                    # request_data.role
                    "name": "--role,-r",
                    "help": "Writer role (e.g. copywriter, legal, ux-writer).",
                    "required": False,
                },
                {
                    # request_data.style
                    "name": "--style,-s",
                    "help": "Text style (e.g. formal, conversational, playful).",
                    "required": False,
                },
                {
                    # request_data.mood
                    "name": "--mood,-m",
                    "help": "Emotional mood (e.g. warm, urgent, calm).",
                    "required": False,
                },
                {
                    # request_data.audience
                    "name": "--audience,-a",
                    "help": "Target audience (e.g. B2B buyers, teens).",
                    "required": False,
                },
                {
                    # request_data.max_char_length
                    "name": "--max-chars,-c",
                    "help": "Maximum character length for the output.",
                    "required": False,
                },
                {
                    # request_data.rule_pack
                    "name": "--rule-pack,-p",
                    "help": (
                        "Rule pack id to apply for this writer request. "
                        "Overrides the agent's configured default rule pack."
                    ),
                    "required": False,
                },
            ],
            # -------- CLI examples (terminal usage) --------
            "examples": [
                "agentverse-tools write "
                "--mode=rewrite "
                '--text "This is our current product description..." '
                '--idea "make it warmer, more visual, more emotional" '
                "-r copywriter",
                # summarize long policy text with specific rule pack
                "agentverse-tools write "
                "--mode=summarize "
                '--text "Very long internal policy text..." '
                "--max-chars=400 "
                "--rule-pack=gdpr_compliance",
                # expand short draft into a landing page story
                "agentverse-tools write "
                "--mode=expand "
                '--text "Short draft about our new lamp." '
                '--idea "turn this into a rich landing page hero and body copy" '
            ],
            # -------- inline @tool_key examples --------
            # These are intended for chat-style UIs; FE can render them as
            # “Try this” examples without any CLI context.
            "inline_examples": [
                {
                    "id": "inline_rewrite_text",
                    "label": "@writer rewrite existing copy",
                    "example": (
                        '@writer --mode=rewrite --text "This is our current product description..." -r copywriter'
                    ),
                },
                {
                    "id": "inline_summarize_policy",
                    "label": "@writer summarize long policy text",
                    "example": (
                        '@writer --mode=summarize --text "Very long internal policy text..." '
                        "--max-chars=400 "
                        "--rule-packs=gdpr_compliance"
                    ),
                },
                {
                    "id": "inline_expand_landing_page",
                    "label": "@writer expand into landing page story",
                    "example": (
                        '@writer --mode=expand --text "Short draft about our new lamp." '
                    ),
                },
            ],
        },
    },
)
