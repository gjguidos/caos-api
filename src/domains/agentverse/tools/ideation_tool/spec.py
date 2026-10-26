from src.domains.agentverse.tools.ideation_tool.config import IdeationToolConfig
from src.domains.agentverse.tools.spec import ToolSpec

ideation_tool = ToolSpec(
    key="ideation",
    name="Ideation Tool",
    description=(
        "Generates idea clusters, hooks, CTAs, hashtags and social packs as structured, "
        "platform-aware brainstorming output."
    ),
    version="1.5.0",
    enabled=True,
    metadata={
        "ownership": "global",
        # -------- core behaviour --------
        "execution_mode": "context_only",
        "llm_free": False,
        "route": "invoker",
        "tool_class": "ideation",
        "tags": ["writing", "marketing", "creative", "social"],
        "config_schema": ("src.domains.agentverse.tools.ideation_tool.IdeationToolConfig"),
        "config": IdeationToolConfig().model_dump(),
        "default_config": IdeationToolConfig().model_dump(),
        "outputs": [
            {"key": "answer", "label": "Answer", "type": "string", "path": "answer"},
        ],
        # -------- UI addons  --------
        "ui": {
            "config": {
                "chat_input": {
                    "id": "chat_input",
                    "placeholder": "e.g. Time-tracking SaaS for remote teams",
                    "file_upload": False,
                    "flag": "--topic",
                    "required": True,
                },
                "group_label_map": {
                    "Behaviour": "Ideation mode",
                    "Audience & Tone": "Audience & tone",
                    "Platforms & Frameworks": "Platforms & frameworks",
                    "Constraints": "Constraints",
                    "Advanced": "Advanced",
                },
            },
            "components": [
                {
                    "id": "num_ideas",
                    "type": "number",
                    "widget": "NumberField",
                    "group": "Behaviour",
                    "flag_config": {
                        "flag": "--num-ideas",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.behaviour"],
                    "slot_order": 1,
                    "props": {
                        "label": "Number of ideas",
                        "min": 1,
                        "max": 50,
                        "step": 1,
                    },
                },
                {
                    "id": "goal",
                    "type": "text",
                    "widget": "TextField",
                    "group": "Audience & Tone",
                    "flag_config": {
                        "flag": "--goal",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.audienceTone"],
                    "slot_order": 0,
                    "props": {
                        "label": "Goal",
                        "placeholder": "awareness | engagement | clickthrough | signup | purchase | retention",
                    },
                },
                {
                    "id": "audience",
                    "type": "text",
                    "widget": "TextField",
                    "group": "Audience & Tone",
                    "flag_config": {
                        "flag": "--audience",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.audienceTone"],
                    "slot_order": 1,
                    "props": {
                        "label": "Audience",
                        "placeholder": "e.g. B2B CMOs in EU",
                    },
                },
                {
                    "id": "brand",
                    "type": "text",
                    "widget": "TextField",
                    "group": "Audience & Tone",
                    "flag_config": {
                        "flag": "--brand",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.audienceTone"],
                    "slot_order": 2,
                    "props": {
                        "label": "Brand",
                        "placeholder": "Brand name or short voice note",
                    },
                },
                {
                    "id": "region",
                    "type": "text",
                    "widget": "TextField",
                    "group": "Audience & Tone",
                    "flag_config": {
                        "flag": "--region",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.audienceTone"],
                    "slot_order": 3,
                    "props": {
                        "label": "Region",
                        "placeholder": "US | EU | Nordics ...",
                    },
                },
                # --- Platforms & Frameworks panel ---
                {
                    "id": "platforms",
                    "type": "multiselect",
                    "widget": "CheckboxGroupField",
                    "group": "Platforms & Frameworks",
                    "flag_config": {
                        "flag": "--platforms",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.platforms"],
                    "slot_order": 0,
                    "props": {
                        "label": "Platforms",
                        "options": [
                            {"value": "x", "label": "X / Twitter"},
                            {"value": "instagram", "label": "Instagram"},
                            {"value": "linkedin", "label": "LinkedIn"},
                            {"value": "tiktok", "label": "TikTok"},
                            {"value": "youtube", "label": "YouTube"},
                        ],
                    },
                },
                {
                    "id": "frameworks",
                    "type": "multiselect",
                    "widget": "CheckboxGroupField",
                    "group": "Platforms & Frameworks",
                    "flag_config": {
                        "flag": "--frameworks",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.platforms"],
                    "slot_order": 1,
                    "props": {
                        "label": "Copy frameworks",
                        "options": [
                            {"value": "AIDA", "label": "AIDA"},
                            {"value": "PAS", "label": "PAS"},
                            {"value": "4P", "label": "4P"},
                        ],
                    },
                },
                {
                    "id": "max_hashtags_per_platform",
                    "type": "number",
                    "widget": "NumberField",
                    "group": "Platforms & Frameworks",
                    "flag_config": {
                        "flag": "--max-hashtags-per-platform",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.platforms"],
                    "slot_order": 2,
                    "props": {
                        "label": "Max hashtags / platform",
                        "min": 0,
                        "max": 50,
                        "step": 1,
                    },
                },
                # --- Constraints panel ---
                {
                    "id": "mentions",
                    "type": "text",
                    "widget": "TextField",
                    "group": "Constraints",
                    "flag_config": {
                        "flag": "--mentions",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.constraints"],
                    "slot_order": 0,
                    "props": {
                        "label": "Mentions",
                        "placeholder": "@Brand @Partner (space-separated)",
                    },
                },
                {
                    "id": "banned_words",
                    "type": "text",
                    "widget": "TextField",
                    "group": "Constraints",
                    "flag_config": {
                        "flag": "--banned-words",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.constraints"],
                    "slot_order": 1,
                    "props": {
                        "label": "Banned words",
                        "placeholder": "space-separated list",
                    },
                },
                {
                    "id": "constraints",
                    "type": "text",
                    "widget": "TextField",
                    "group": "Constraints",
                    "flag_config": {
                        "flag": "--constraints",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.constraints"],
                    "slot_order": 2,
                    "props": {
                        "label": "Extra constraints",
                        "placeholder": "e.g. no discounts, no hard selling, avoid jargon",
                    },
                },
                # --- Advanced panel ---
                {
                    "id": "seed",
                    "type": "number",
                    "widget": "NumberField",
                    "group": "Advanced",
                    "flag_config": {
                        "flag": "--seed",
                        "required": False,
                    },
                    "render_paths": ["propertiesPanel.advanced"],
                    "slot_order": 0,
                    "props": {
                        "label": "Seed",
                        "min": 0,
                        "max": 2**31 - 1,
                        "step": 1,
                    },
                },
            ],
        },
        "supports": {
            "streaming": True,
            "attachments": [],
            "multi_turn_memory": True,
        },
        # ---------- CLI help ----------
        "cli": {
            "command": "ideate",
            "args": [
                {
                    "name": "--topic,-t",
                    "help": "Topic/product/campaign focus",
                    "required": True,
                },
                {
                    "name": "--mode,-m",
                    "help": "cluster|social|framework|cta-only|tags-only",
                    "required": False,
                },
                {
                    "name": "--goal,-g",
                    "help": "awareness|engagement|clickthrough|signup|purchase|retention",
                },
                {"name": "--audience,-a", "help": "e.g., 'B2B CMOs in EU'"},
                {
                    "name": "--tone,-T",
                    "help": "professional|playful|bold|luxury|technical|poetic|minimalist",
                },
                {"name": "--num-ideas,-n", "help": "3-50 (default 12)"},
                {
                    "name": "--platforms,-p",
                    "help": "x instagram linkedin tiktok youtube",
                },
                {"name": "--brand,-b", "help": "Brand name or short voice note"},
                {"name": "--mentions,-M", "help": "@Brand @Partner (space-separated)"},
                {"name": "--banned-words,-x", "help": "space-separated list"},
                {"name": "--region,-r", "help": "US|EU|Nordics ..."},
                {
                    "name": "--max-hashtags-per-platform",
                    "help": "int (default from config)",
                },
                {"name": "--frameworks,-F", "help": "AIDA PAS 4P"},
                {"name": "--creativity,-c", "help": "low|medium|high"},
                {"name": "--seed,-s", "help": "int"},
                {
                    "name": "--out,-o",
                    "help": "Output file path (JSON). If omitted → stdout",
                },
                {
                    "name": "--pretty",
                    "help": "Pretty-print JSON",
                    "action": "store_true",
                },
                {
                    "name": "--quiet",
                    "help": "Suppress non-JSON logs",
                    "action": "store_true",
                },
            ],
            "examples": [
                'agentverse-tools ideate -t "SaaS time-tracking" -n 10',
                'agentverse-tools ideate -t "High-end hats for dogs" -m social -p x instagram -T luxury -n 12',
                'agentverse-tools ideate -t "HR analytics platform" -m framework -F AIDA PAS',
            ],
        },
    },
)
