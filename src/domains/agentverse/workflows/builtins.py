"""
Explicit catalog of code-defined ("builtin") workflows.

These are seeded once into Mongo (``seed_builtin_workflows``); thereafter the DB
row is the source of truth and is never overwritten. We reference the factory
functions directly rather than relying on the legacy registry's decorator
side-effects.
"""

from __future__ import annotations

from collections.abc import Callable

from src.domains.agentverse.workflows.crawl_operation_v1.crawl_operation_v1 import (
    crawl_operation_v1,
)
from src.domains.agentverse.workflows.mini_workflow.mini_workflow import mini_workflow_v1
from src.domains.agentverse.workflows.nested_mini_workflow.nested_mini_workflow_v1 import (
    nested_mini_workflow_v1,
)
from src.domains.agentverse.workflows.spec import WorkflowSpec

BUILTIN_WORKFLOWS: list[Callable[[], WorkflowSpec]] = [
    crawl_operation_v1,
    mini_workflow_v1,
    nested_mini_workflow_v1,
]
