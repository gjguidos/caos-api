from __future__ import annotations

from src.domains.agentverse.registries import workflow_registry_instance
from src.domains.agentverse.workflows.spec import WorkflowSpec


@workflow_registry_instance.register(
    key="debug_two_mystic_images_v1",
    name="Debug Two Mystic Images",
    description="Minimal workflow to verify two separate Mystic image calls produce two separate image results.",
    version="0.1.0",
    metadata={
        "ui": {
            "icon": "🧪",
            "category": "debug",
            "steps": ["product_image", "scene_image"],
            "tools": ["mystic_image"],
        },
        "governance": {
            "status": "debug",
            "visibility": "org",
            "allowed_roles": ["admin"],
        },
    },
)
def debug_two_mystic_images_v1() -> WorkflowSpec:
    steps = [
        {
            "id": "product_image",
            "type": "tool",
            "command": (
                "@mystic_image "
                "--prompt=${inputs.product_prompt|isolated studio product photo of a small modular storage bench on a pure white background, no room, no props, no people} "
                "--aspect_ratio=${inputs.product_aspect_ratio|square_1_1} "
                "--resolution=${inputs.resolution|2k} "
                "--model=${inputs.model|realism} "
                "--engine=${inputs.engine|automatic} "
                "--creative_detailing=${inputs.product_creative_detailing|25} "
                "--fixed_generation=false "
                "--poll_interval_seconds=${inputs.poll_interval_seconds|2} "
                "--max_attempts=${inputs.max_attempts|30} "
                "--dry_run=${inputs.dry_run|false}"
            ),
        },
        {
            "id": "scene_image",
            "type": "tool",
            "depends_on": ["product_image"],
            "command": (
                "@mystic_image "
                "--prompt=${inputs.scene_prompt|documentary lifestyle photo of the same modular storage bench inside a crowded Mexico City family apartment entryway, ceramic tile floor, shoes, bags, jackets, cleaning supplies, warm daylight, lived-in local home context} "
                "--aspect_ratio=${inputs.scene_aspect_ratio|social_post_4_5} "
                "--resolution=${inputs.resolution|2k} "
                "--model=${inputs.model|realism} "
                "--engine=${inputs.engine|automatic} "
                "--creative_detailing=${inputs.scene_creative_detailing|45} "
                "--fixed_generation=false "
                "--poll_interval_seconds=${inputs.poll_interval_seconds|2} "
                "--max_attempts=${inputs.max_attempts|30} "
                "--dry_run=${inputs.dry_run|false}"
            ),
        },
        {
            "id": "end",
            "type": "end",
            "depends_on": ["scene_image"],
        },
    ]

    return WorkflowSpec(
        key="debug_two_mystic_images_v1",
        name="Debug Two Mystic Images",
        description="Minimal workflow to verify two independent Mystic image generations.",
        version="0.1.0",
        definition={
            "name": "Debug Two Mystic Images",
            "steps": steps,
        },
        tags=[
            "debug",
            "image-generation",
            "workflow",
        ],
        ui={
            "icon": "🧪",
            "category": "debug",
        },
    )
