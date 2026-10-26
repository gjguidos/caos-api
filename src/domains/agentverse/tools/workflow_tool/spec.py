from __future__ import annotations

from src.domains.agentverse.tools.spec import ToolSpec
from src.domains.agentverse.tools.workflow_tool.config import WorkflowToolConfig

workflow_tool = ToolSpec(
    key="workflow",
    name="Workflow Tool",
    description="LLM-free workflow runtime: executes deterministic step pipelines, supports pause gates, and routes tool calls.",
    version="0.1.0",
    tool_class="orchestration",
    tags=["orchestration", "workflow", "state_machine"],
    config=WorkflowToolConfig().model_dump(),
    metadata={
        "ownership": "global",
        "provider": "tools:orchestration",
        "llm_free": True,
        "execution_mode": "pure",
        "route": "pure",
        "config_schema": ("src.domains.agentverse.tools.workflow_tool.WorkflowToolConfig"),
        "default_config": WorkflowToolConfig().model_dump(),
        "tags": ["orchestration", "workflow", "state_machine"],
        "requires": {"tool_runner": True},
        # Intentionally empty: a workflow's result is the aggregate of its steps,
        # not a single typed value worth exposing as a ${outputs.<step_id>.<key>} binding.
        "outputs": [],
        "ui": {
            "admin": {
                "renderer": "WorkflowToolConfig",
                "configurable_fields": [
                    "max_steps_per_call",
                    "run_ttl_seconds",
                ],
                "fields": {
                    "max_steps_per_call": {
                        "label": "Max Steps Per Call",
                        "help": "Guardrail to prevent runaway runs. Recommended 25-100.",
                        "input": "number",
                        "constraints": {"min": 1, "max": 1000},
                        "component": {
                            "library": "shadcn",
                            "name": "Input",
                            "props": {"type": "number", "className": "mt-1 w-full"},
                        },
                    },
                    "run_ttl_seconds": {
                        "label": "Run TTL Seconds",
                        "help": "Blank = null (no TTL if your store supports it).",
                        "input": "number_or_null",
                        "constraints": {"min": 60},
                        "component": {
                            "library": "shadcn",
                            "name": "Input",
                            "props": {
                                "type": "number",
                                "placeholder": "604800 (7 days) or blank for null",
                                "className": "mt-1 w-full",
                            },
                        },
                    },
                },
            },
            "icon": "🧭",
            "category": "orchestration",
            "display_hints": ["state-machine", "pause-gates"],
        },
        "supports": {"streaming": False, "attachments": [], "multi_turn_memory": True},
    },
)
