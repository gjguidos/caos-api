from __future__ import annotations

from src.domains.agentverse.registries import workflow_registry_instance
from src.domains.agentverse.workflows.crawl_operation_v1.inputs import workflow_inputs
from src.domains.agentverse.workflows.spec import WorkflowSpec


@workflow_registry_instance.register(
    key="crawl_operation_v1",
    name="Crawl Operation",
    description=(
        "Runs the web crawler and materializes generated crawl reports "
        "as file artifacts."
    ),
    version="1.0.0",
    metadata={
        "operation": "crawl",
        "role_adapter": "crawler",
        "tools": [
            "web_crawler",
            "artifact_builder",
        ],
        "result_contract": "crawler.operation_result.v1",
    },
)
def crawl_operation_v1() -> WorkflowSpec:
    return WorkflowSpec(
        key="crawl_operation_v1",
        name="Crawl Operation V1",
        description=(
            "Workflow-backed crawl operation that crawls a site and "
            "materializes the generated reports as file artifacts."
        ),
        version="1.0.0",
        definition={
            "name": "Crawl Operation",
            "description": (
                "Run the web crawler and build file artifacts from "
                "the generated crawl reports."
            ),

            # -----------------------------------------------------------------
            # Contracts
            # -----------------------------------------------------------------
            "contracts": {
                "artifacts": {
                    "schema": "artifact.builder.contract.v1",

                    # ---------------------------------------------------------
                    # Shared metadata
                    #
                    # Applied to every artifact produced by this contract.
                    # Artifact-level metadata below can extend or override it.
                    # ---------------------------------------------------------
                    "metadata": {
                        "artifact_type": "crawl_report",
                        "producer": "web_crawler",
                        "operation": "crawl",

                        "source_url": "${steps.crawl_site.output.start_url}",
                        "started_at": "${steps.crawl_site.output.started_at}",
                        "finished_at": "${steps.crawl_site.output.finished_at}",
                        "duration_ms": "${steps.crawl_site.output.duration_ms}",
                        "duration_seconds": "${steps.crawl_site.output.duration_seconds}",

                        "crawl_mode": "${steps.crawl_site.output.mode}",
                        "crawl_strategy": "${steps.crawl_site.output.crawl_strategy}",
                        "output_profile": "${steps.crawl_site.output.output_profile}",
                        "output_format": "${steps.crawl_site.output.output_format}",

                        "page_count": "${steps.crawl_site.output.page_count}",
                        "max_pages": "${steps.crawl_site.output.resolved_max_pages}",
                        "max_depth": "${steps.crawl_site.output.resolved_max_depth}",
                    },

                    "artifacts": [
                        {
                            "id": "crawl_summary",
                            "source": "reports.crawl_summary",
                            "format": "json",
                            "filename": "crawl_summary.json",
                            "required": True,
                            "skip_if_missing": False,
                            "skip_if_empty": False,
                            "metadata": {
                                "report": "crawl_summary",
                            },
                        },
                        {
                            "id": "seo",
                            "source": "reports.seo",
                            "format": "json",
                            "filename": "seo.json",
                            "required": False,
                            "skip_if_missing": True,
                            "skip_if_empty": False,
                            "metadata": {
                                "report": "seo",
                            },
                        },
                        {
                            "id": "links",
                            "source": "reports.links",
                            "format": "json",
                            "filename": "links.json",
                            "required": False,
                            "skip_if_missing": True,
                            "skip_if_empty": False,
                            "metadata": {
                                "report": "links",
                            },
                        },
                        {
                            "id": "documents",
                            "source": "reports.documents",
                            "format": "json",
                            "filename": "documents.json",
                            "required": False,
                            "skip_if_missing": True,
                            "skip_if_empty": False,
                            "metadata": {
                                "report": "documents",
                            },
                        },
                        {
                            "id": "media",
                            "source": "reports.media",
                            "format": "json",
                            "filename": "media.json",
                            "required": False,
                            "skip_if_missing": True,
                            "skip_if_empty": False,
                            "metadata": {
                                "report": "media",
                            },
                        },
                        {
                            "id": "content_blocks",
                            "source": "reports.content_blocks",
                            "format": "json",
                            "filename": "content_blocks.json",
                            "required": False,
                            "skip_if_missing": True,
                            "skip_if_empty": False,
                            "metadata": {
                                "report": "content_blocks",
                            },
                        },
                        {
                            "id": "detected_html_blocks",
                            "source": "reports.detected_html_blocks",
                            "format": "json",
                            "filename": "detected_html_blocks.json",
                            "required": False,
                            "skip_if_missing": True,
                            "skip_if_empty": False,
                            "metadata": {
                                "report": "detected_html_blocks",
                            },
                        },
                        {
                            "id": "find",
                            "source": "reports.find",
                            "format": "json",
                            "filename": "find.json",
                            "required": False,
                            "skip_if_missing": True,
                            "skip_if_empty": False,
                            "metadata": {
                                "report": "find",
                            },
                        },
                        {
                            "id": "filters",
                            "source": "reports.filters",
                            "format": "json",
                            "filename": "filters.json",
                            "required": False,
                            "skip_if_missing": True,
                            "skip_if_empty": False,
                            "metadata": {
                                "report": "filters",
                            },
                        },
                        {
                            "id": "crawl_graph",
                            "source": "reports.crawl_graph",
                            "format": "json",
                            "filename": "crawl_graph.json",
                            "required": False,
                            "skip_if_missing": True,
                            "skip_if_empty": False,
                            "metadata": {
                                "report": "crawl_graph",
                            },
                        },
                        {
                            "id": "forms",
                            "source": "reports.forms",
                            "format": "json",
                            "filename": "forms.json",
                            "required": False,
                            "skip_if_missing": True,
                            "skip_if_empty": False,
                            "metadata": {
                                "report": "forms",
                            },
                        },
                    ],
                },
            },

            # -----------------------------------------------------------------
            # Steps
            # -----------------------------------------------------------------
            "steps": [
                {
                    "id": "crawl_site",
                    "type": "tool",
                    "command": (
                        "@web_crawler "
                        "--url=${inputs.url} "
                        "--mode=${inputs.mode|static} "
                        "--max_pages=${inputs.max_pages} "
                        "--max_depth=${inputs.max_depth} "
                        "--crawl_strategy=${inputs.crawl_strategy} "
                        "--same_domain_only=${inputs.same_domain_only} "
                        "--include_links=${inputs.include_links} "
                        "--include_errors=${inputs.include_errors} "
                        "--output_profile=${inputs.output_profile} "
                        "--report=${inputs.report} "
                        "--auth_mode=${inputs.auth_mode|none} "
                        "--browser_session_id=${inputs.browser_session_id|none} "
                    ),
                    "save_as": "crawl_result",
                },
                {
                    "id": "build_artifacts",
                    "type": "tool",
                    "depends_on": ["crawl_site"],
                    "command": (
                        "@artifact_builder "
                        "--content=${steps.crawl_site.output.reports} "
                        "--contract=${contracts.artifacts}"
                    ),
                    "save_as": "artifact_result",
                },
            ],

            # -----------------------------------------------------------------
            # Outputs
            # -----------------------------------------------------------------
            "outputs": {
                "status": "completed",
                "crawl_result": "${steps.crawl_site.output}",
                "artifacts": "${steps.build_artifacts.output}",
                "final_output": "${steps.build_artifacts.output}",
            },
        },
        inputs=workflow_inputs(),
        metadata={
            "operation": "crawl",
            "result_contract": "crawler.operation_result.v1",
            "artifact_contract": "artifact.builder.contract.v1",
            "storage": "none",
            "normalization": "artifact",
            "single_step": False,
        },
    )