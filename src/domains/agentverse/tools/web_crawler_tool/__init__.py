from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries import (
    tool_registry_instance as tool_registry,
)
from src.domains.agentverse.tools.base import (
    BaseTool,
    ToolExecutionError,
)
from src.domains.agentverse.tools.web_crawler_tool import opts
from src.domains.agentverse.tools.web_crawler_tool.clean_up.clean_up_service import (
    WebCrawlerCleanupService,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.delivery.report_builder import (
    build_report_refs,
)
from src.domains.agentverse.tools.web_crawler_tool.delivery.reports import build_report_delivery
from src.domains.agentverse.tools.web_crawler_tool.execution.acquisition import (
    acquire_crawl,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.scope_resolver import (
    resolve_crawl_scope,
)
from src.domains.agentverse.tools.web_crawler_tool.formatter import (
    format_crawler_result,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.prepare_crawl_execution import (
    prepare_crawl_execution,
)
from src.domains.agentverse.tools.web_crawler_tool.job_policy import (
    WebCrawlerJobPolicy,
)
from src.domains.agentverse.tools.web_crawler_tool.params import (
    parameters,
)
from src.domains.agentverse.tools.web_crawler_tool.profile_resolver import (
    resolve_web_crawler_profile,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.metadata import (
    build_metadata_report,
)
from src.domains.agentverse.tools.web_crawler_tool.result_pipeline import (
    normalize_requested_reports,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    normalize_crawler_runtime,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapSelection,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.types import (
    SitemapTraversalPolicy,
)
from src.domains.agentverse.tools.web_crawler_tool.spec import (
    web_crawler_tool,
)


@tool_registry.bind_spec(web_crawler_tool)
class WebCrawlerTool(BaseTool):
    """
    Enterprise WebCrawlerTool.

    The tool invocation is intentionally short-lived.

    `_run()`:
        - preserves the original WebCrawlerTool input contract
        - normalizes the requested crawl
        - creates an AgentVerse operation
        - schedules the actual crawl
        - returns the operation run_id immediately

    `_execute_operation()`:
        - owns the actual crawl lifecycle
        - may continue after the originating chat turn completes
        - must not depend on the originating WebSocket remaining connected

    Default crawler behavior:
        - LLM-free
        - bounded crawl
        - static HTML fetch
        - same-domain only
        - private network protection
        - structured JSON-safe output
    """

    name = web_crawler_tool.name
    key = web_crawler_tool.key or "web_crawler"
    description = web_crawler_tool.description
    version = web_crawler_tool.version or "0.1.0"
    tool_class = web_crawler_tool.tool_class or "web_research"

    requires_tool_runner: bool = False
    job_policy_type = WebCrawlerJobPolicy

    parameters = parameters

    def __init__(
        self,
        config: WebCrawlerToolConfig | dict | None = None,
    ):
        
        super().__init__(
            config=opts._normalize_config(config),
        )

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format_result(
        self,
        data: object,
        *,
        output_format: str = "markdown",
    ):
        if hasattr(data, "model_dump"):
            raw = data.model_dump()
        elif isinstance(data, dict):
            raw = dict(data)
        else:
            raw = {
                "pages": [],
                "errors": [
                    f"Unsupported crawler result: {type(data)}"
                ],
            }

        asset_observations = raw.pop(
            "asset_observations",
            None,
        )

        if not isinstance(asset_observations, list):
            asset_observations = None

        return format_crawler_result(
            {"result": raw},
            output_format=output_format,
            asset_observations=asset_observations,
        )

    def format_profile(
        self,
        *,
        output_profile: str | None,
        request_overrides: dict[str, Any],
        custom_profiles: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        try:
            return resolve_web_crawler_profile(
                output_profile=output_profile,
                request_overrides=request_overrides,
                custom_profiles=custom_profiles,
            )
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Tool invocation
    # ------------------------------------------------------------------

    async def _run(
        self,
        socket_emitter=None,
        url: str = "",
        mode: str | None = None,
        auth_mode: str | None = None,
        browser_session_id: str | None = None,
        crawl_strategy: str | None = None,
        crawl_scope: str | None = None,
        sitemap_selection: SitemapSelection | None = None,
        sitemap_traversal_policy: (
            SitemapTraversalPolicy | None
        ) = None,
        max_sitemaps: int | None = None,
        page_acquisition_limit: (
            int | None
        ) = None,
        include_links: bool | None = None,
        report: str | None = None,
        output_profile: str | None = None,
        output_format: str | None = None,
        include_raw_pages: bool | None = None,
        include_security_audit: bool | None = None,
        include_errors: bool | None = None,
        find: str | None = None,
        find_scope: str = "all",
        find_report: str | None = None,
        media_extensions: list[str] | None = None,
        media_filename: str | None = None,
        media_role: str | None = None,
        document_extensions: list[str] | None = None,
        document_filename: str | None = None,
        document_type: str | None = None,
        case_sensitive: bool = False,
        max_matches: int = 20,
        max_pages: int | None = None,
        max_depth: int | None = None,
        same_domain_only: bool | None = None,
        max_chars_per_page: int | None = None,
        timeout_seconds: int | None = None,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Start a crawler operation.

        `**kwargs` intentionally absorbs generic AgentVerse execution
        parameters such as `text`.

        Those generic parameters are NOT forwarded into
        `normalize_crawler_runtime()`. This preserves the original crawler
        execution contract and prevents unrelated AgentVerse execution
        fields from leaking into crawler runtime normalization.
        """
        agent = getattr(self, "agent", None)

        cfg: WebCrawlerToolConfig = self.config

        # --------------------------------------------------------------
        # IMPORTANT:
        # Preserve the original crawler normalization boundary.
        #
        # Do NOT pass **kwargs here.
        # --------------------------------------------------------------

        runtime = normalize_crawler_runtime(
            cfg=cfg,
            profile_resolver=self.format_profile,
            url=url,
            crawl_scope=crawl_scope,
            sitemap_selection=sitemap_selection,
            sitemap_traversal_policy=(
                sitemap_traversal_policy
            ),

            max_sitemaps=max_sitemaps,
            page_acquisition_limit=(
                page_acquisition_limit
            ),
            mode=mode,
            auth_mode=auth_mode,
            browser_session_id=browser_session_id,
            crawl_strategy=crawl_strategy,
            include_links=include_links,
            report=report,
            output_profile=output_profile,
            output_format=output_format,
            include_raw_pages=include_raw_pages,
            include_security_audit=include_security_audit,
            include_errors=include_errors,
            find=find,
            find_scope=find_scope,
            find_report=find_report,
            media_extensions=media_extensions,
            media_filename=media_filename,
            media_role=media_role,
            document_extensions=document_extensions,
            document_filename=document_filename,
            document_type=document_type,
            case_sensitive=case_sensitive,
            max_matches=max_matches,
            max_pages=max_pages,
            max_depth=max_depth,
            same_domain_only=same_domain_only,
            max_chars_per_page=max_chars_per_page,
            timeout_seconds=timeout_seconds,
            context=context,
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER][LIMITS][RESOLVED] "
                f"crawl_scope={runtime.crawl_scope} "
                f"requested_page_acquisition_limit="
                f"{page_acquisition_limit} "
                f"resolved_page_acquisition_limit="
                f"{runtime.page_acquisition_limit} "
                f"resolved_max_pages={runtime.max_pages}"
            ),
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER][SITE_MAP][SELECTION] "
                f"{runtime.params.sitemap_selection}"
            ),
        )

        # --------------------------------------------------------------
        # Operation infrastructure
        # --------------------------------------------------------------

        operation_service = getattr(
            self,
            "operation_service",
            None,
        )

        operation_runner = getattr(
            self,
            "operation_runner",
            None,
        )

        job_supervisor = getattr(
            self,
            "job_supervisor",
            None,
        )

        if operation_service is None:
            raise ToolExecutionError(
                "WebCrawlerTool requires OperationService, "
                "but no operation_service runtime dependency was bound."
            )

        if operation_runner is None:
            raise ToolExecutionError(
                "WebCrawlerTool requires OperationRunner, "
                "but no operation_runner runtime dependency was bound."
            )
        
        if job_supervisor is None:
            raise ToolExecutionError(
                "WebCrawlerTool requires JobSupervisor, "
                "but no job_supervisor runtime dependency was bound."
            )

        # --------------------------------------------------------------
        # Create operation
        # --------------------------------------------------------------

        operation = await operation_service.create(
            kind="crawl",
            producer=self.key,
            user_id=agent.user_id,
            agent_id=agent.id,
            session_id=agent.default_session_id,
            metadata={
                "url": runtime.params.url,
                "crawl_scope": runtime.crawl_scope,
                "mode": runtime.crawl_mode,

                "max_pages": (
                    runtime.max_pages
                    if runtime.crawl_scope != "sitemap"
                    else None
                ),

                "sitemap_traversal_policy": (
                    runtime.sitemap_traversal_policy
                    if runtime.crawl_scope == "sitemap"
                    else None
                ),

                "max_sitemaps": (
                    runtime.max_sitemaps
                    if runtime.crawl_scope == "sitemap"
                    else None
                ),

                "page_acquisition_limit": (
                    runtime.page_acquisition_limit
                    if runtime.crawl_scope == "sitemap"
                    else None
                ),
            },
        )

        # --------------------------------------------------------------
        # Start detached operation
        # --------------------------------------------------------------

        await operation_runner.start(
            run_id=operation.run_id,
            operation=lambda run_id: self._execute_operation(
                run_id=run_id,
                runtime=runtime,
                agent=agent,
            ),
        )

        # --------------------------------------------------------------
        # Immediate tool response
        # --------------------------------------------------------------

        answer = (
            f"Crawl started for {runtime.params.url}. "
            f"It is running in the background. "
            f"Operation ID: {operation.run_id}."
        )

        return {
            "answer": answer,
            "message": answer,
            "text": answer,
            "display_text": answer,
            "report_text": answer,
            "result": {
                "operation": {
                    "run_id": operation.run_id,
                    "kind": "crawl",
                    "status": "started",
                },
                "request": {
                    "url": runtime.params.url,
                    "crawl_scope": runtime.crawl_scope,
                    "mode": runtime.crawl_mode,
                    "max_pages": runtime.max_pages,
                },
            },
            "artifacts": [],
            "error": None,
        }

    # ------------------------------------------------------------------
    # Detached crawl operation
    # ------------------------------------------------------------------

    async def _execute_operation(
        self,
        *,
        run_id: str,
        runtime,
        agent,
    ) -> dict[str, Any]:
        """
        Execute the actual crawler operation.

        This is owned by OperationRunner rather than the originating
        chat/WebSocket task.

        No originating socket emitter is retained here.
        """
        started_at = datetime.now(UTC)
        started_perf = perf_counter()

        cfg: WebCrawlerToolConfig = self.config

        requested_reports = normalize_requested_reports(
            runtime.report
        )

        operation_service = getattr(
            self,
            "operation_service",
            None,
        )

        acquisition_context_factory = getattr(
            self,
            "acquisition_context_factory",
            None,
        )

        if acquisition_context_factory is None:
            raise ToolExecutionError(
                "WebCrawlerTool requires CrawlAcquisitionContextFactory, "
                "but no acquisition_context_factory runtime dependency was bound."
            )

        if operation_service is None:
            raise ToolExecutionError(
                "WebCrawlerTool lost its OperationService "
                "runtime dependency."
            )

        # --------------------------------------------------------------
        # Preparing
        # --------------------------------------------------------------

        await operation_service.update_progress(
            run_id,
            phase="preparing",
            current=0,
            message="Preparing crawl execution.",
        )

        prepared = await prepare_crawl_execution(
            cfg=cfg,
            runtime=runtime,
            agent=agent,
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🕷️ WEB CRAWLER][START]",
            extra={
                "run_id": run_id,
                "url": prepared.start_url,
                "mode": prepared.crawl_mode,
                "auth_mode": runtime.auth_mode,
                "max_pages": runtime.max_pages,
                "max_depth": runtime.max_depth,
                "same_domain_only": runtime.same_domain_only,
                "include_links": runtime.include_links,
                "output_profile": runtime.output_profile,
            },
        )

        # --------------------------------------------------------------
        # Page acquisition setup
        # --------------------------------------------------------------

        await operation_service.update_progress(
            run_id,
            phase="acquisition_setup",
            current=0,
            message="Preparing page acquisition.",
        )

        # --------------------------------------------------------------
        # Preparing context for acquisition
        # --------------------------------------------------------------
        acquisition_context = await acquisition_context_factory.build(
            run_id=run_id,
            cfg=cfg,
            runtime=runtime,
            requested_reports=requested_reports,
            prepared=prepared,
            agent=agent,
        )

        cleanup_service = WebCrawlerCleanupService(
            sitemap_inventory=(
                acquisition_context.sitemap_url_inventory_store
            ),
            report_repository=acquisition_context.crawl_run_report_store,
            queue_service=agent.queue_service,
        )

        crawler_policy = WebCrawlerJobPolicy(
            cleanup_service=cleanup_service,
            operation_service=operation_service,
            operation_runner=self.operation_runner,
        )

        await self.job_supervisor.register_job(
            run_id=run_id,
            producer=self.key,
            policy=crawler_policy,
        )

        # --------------------------------------------------------------
        # Scope planning
        # --------------------------------------------------------------

        await operation_service.update_progress(
            run_id,
            phase="resolving_crawl_scope",
            current=0,
            message="Resolving crawl scope.",
        )

        scope = await resolve_crawl_scope(
            agent=agent,
            run_id=run_id,
            cfg=cfg,
            runtime=runtime,
            prepared=prepared,
            site_state_store=(
                acquisition_context.site_state_store
            ),
            sitemap_url_inventory_store=(
                acquisition_context.sitemap_url_inventory_store
            ),
            operation_service=operation_service,
            started_at=started_at,
            started_perf=started_perf,
            tool_key=self.key,
            tool_version=self.version,
            format_result=self.format_result,
        )

        if (
            runtime.crawl_scope == "sitemap"
            and not scope.has_targets
        ):
            if scope.sitemap_plan is None:
                raise RuntimeError(
                    "Sitemap scope produced no sitemap plan."
                )

            return scope.sitemap_plan.to_payload()
        
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[CRAWLER][SCOPE][RESOLVE] crawl scope resolved"
        )

        # --------------------------------------------------------------
        # Acquisition
        # --------------------------------------------------------------
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[CRAWLER][ACQUISITION][START] starting acquisition"
        )
        try:
            acquisition = await acquire_crawl(
                run_id=run_id,
                cfg=cfg,
                runtime=runtime,
                start_url=prepared.start_url,
                crawl_mode=prepared.crawl_mode,
                crawl_plan=scope.crawl_plan,
                fetcher=acquisition_context.fetcher,
                auth_context=prepared.auth_context,
                agent=agent,
                crawl_run_report_store=(
                    acquisition_context.crawl_run_report_store
                ),
                sitemap_url_inventory_store=(
                    acquisition_context.sitemap_url_inventory_store
                ),
                asset_store=acquisition_context.asset_store,
                operation_service=operation_service,
                on_page_fetched=(
                    acquisition_context
                    .page_processing_pipeline
                    .handle
                ),
                acquisition_queue_name=(
                    acquisition_context.acquisition_queue_name
                ),
                acquisition_consumer_group=(
                    acquisition_context.acquisition_consumer_group
                ),
                report_queue_name=(
                    acquisition_context.report_queue_name
                ),
                report_consumer_group=(
                    acquisition_context.report_consumer_group
                ),
            )

        finally:
            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                    "[CRAWLER][ACQUISITION][FINALLY] closing context"
            )
            await acquisition_context.close()
        
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[CRAWLER][ACQUISITION][FINISH] acquisition finished"
        )

        
        # Reports are written incrementally by the report workers. Do not
        # reconstruct pages or run a second whole-crawl projection here.
        # Consumers can use report_reader with these references to read or
        # compose a report on demand.
        await operation_service.update_progress(
            run_id,
            phase="reporting",
            current=acquisition.queued_for_parse,
            total=acquisition.queued_for_parse,
            message="Crawl reports are available in the report store.",
        )

        finished_at = datetime.now(UTC)
        duration_seconds = (
            finished_at - started_at
        ).total_seconds()

        crawl_summary = {
            "planned": acquisition.planned,
            "fetched": acquisition.fetched,
            "queued_for_parse": acquisition.queued_for_parse,
            "failed": acquisition.failed,
            "chunks_completed": acquisition.chunks_completed,
        }

        metadata_report = build_metadata_report(
            start_url=prepared.start_url,
            mode=prepared.crawl_mode,
            output_profile=runtime.output_profile,
            output_format=runtime.output_format,
            report=runtime.report,
            requested_reports=sorted(requested_reports),
            crawl_strategy=runtime.crawl_strategy,
            max_pages=runtime.max_pages,
            max_depth=runtime.max_depth,
            timeout_seconds=runtime.timeout_seconds,
            max_chars_per_page=runtime.max_chars_per_page,
            same_domain_only=runtime.same_domain_only,
            include_links=runtime.include_links,
            include_raw_pages=False,
            include_security_audit=runtime.include_security_audit,
            include_errors=runtime.include_errors,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            duration_ms=duration_seconds * 1000,
            duration_seconds=duration_seconds,
        )

        metadata = metadata_report.model_dump(mode="json")
        metadata.update(
            {
                "run_id": run_id,
                "page_count": acquisition.fetched,
            }
        )

        await acquisition_context.crawl_run_report_store.set_metadata(
            run_id=run_id,
            metadata=metadata,
        )

        await acquisition_context.crawl_run_report_store.mark_complete(
            run_id=run_id,
            pages_parsed=acquisition.queued_for_parse,
            pages_failed=acquisition.failed,
        )

        manifest = {
            "schema": "artifact.manifest.v1",
            "run_id": run_id,
            "metadata": metadata,
            "artifacts": acquisition.report_refs,
        }

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[CRAWLER][REPORT_REFS][RAW] "
                f"type={type(acquisition.report_refs)} "
                f"value={acquisition.report_refs!r}"
            ),
        )

        for index, ref in enumerate(acquisition.report_refs or []):
            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                (
                    "[CRAWLER][REPORT_REF][RAW] "
                    f"index={index} "
                    f"type={type(ref)} "
                    f"value={ref!r}"
                ),
            )

        report_refs = build_report_refs(
            run_id=run_id,
            report_refs=acquisition.report_refs,
        )

        markdown = build_crawler_completion_markdown(
            run_id=run_id,
            metadata=metadata,
            crawl_summary=crawl_summary,
            report_refs=report_refs,
        )

        payload = {
            "answer": markdown,
            "message": markdown,
            "text": markdown,
            "display_text": markdown,
            "report_text": markdown,
            "run_id": run_id,
            "status": "complete",
            "result": {
                "run_id": run_id,
                "status": "complete",
                "manifest": manifest,
                "crawl_summary": crawl_summary,
                "report_refs": report_refs,
            },
            "artifacts": report_refs,
            "error": None,
        }

        return payload
    
def build_crawler_completion_markdown(
    *,
    run_id: str,
    metadata: dict[str, Any],
    crawl_summary: dict[str, Any],
    report_refs: list[dict[str, Any]],
) -> str:
    timing = metadata.get("timing") or {}

    started_at = (
        metadata.get("started_at")
        or timing.get("started_at")
    )

    finished_at = (
        metadata.get("finished_at")
        or timing.get("finished_at")
    )

    duration_seconds = (
        metadata.get("duration_seconds")
        if metadata.get("duration_seconds") is not None
        else timing.get("duration_seconds")
    )
    
    lines = [
        "# Web Crawl Result",
        "",
        f"**Start URL:** {metadata.get('start_url') or metadata.get('source_url')}",
        f"**Mode:** {metadata.get('mode') or metadata.get('crawl_mode')}",
        f"**Output profile:** {metadata.get('output_profile')}",
        f"**Crawl strategy:** {metadata.get('crawl_strategy')}",
        f"**Pages crawled:** {metadata.get('page_count', 0)}",
        f"**Started at:** {started_at or 'unknown'}",
        f"**Finished at:** {finished_at or 'unknown'}",
        f"**Duration:** "
        f"{duration_seconds if duration_seconds is not None else 0}s",
        "## Crawl Summary",
        "",
        f"- **Planned:** {crawl_summary.get('planned', 0)}",
        f"- **Fetched:** {crawl_summary.get('fetched', 0)}",
        f"- **Queued for parse:** {crawl_summary.get('queued_for_parse', 0)}",
        f"- **Failed:** {crawl_summary.get('failed', 0)}",
        "",
        "## Available Reports",
        "",
    ]

    if crawl_summary.get("chunks_completed") is not None:
        chunk_summary_index = lines.index("## Available Reports")
        lines.insert(
            chunk_summary_index - 1,
            (
                "- **Chunks completed:** "
                f"{crawl_summary['chunks_completed']}"
            ),
        )

    for raw_ref in report_refs or []:
        if isinstance(raw_ref, str):
            report_id = raw_ref
            ref: dict[str, Any] = {}
        elif isinstance(raw_ref, dict):
            ref = raw_ref
            report_id = str(
                ref.get("id") or ref.get("name") or "report"
            )
        else:
            continue

        delivery = ref.get("delivery") or {}
        delivery = build_report_delivery(
            run_id=run_id,
            report_id=report_id,
            existing=ref.get("delivery"),
        )

        url = delivery["url"]

        label = report_id.replace("_", " ").title()
        lines.append(f"- [{label}]({url})")

    lines.extend(
        [
            "",
            "Detailed report data is available through "
            "the report links above.",
        ]
    )

    return "\n".join(lines)
