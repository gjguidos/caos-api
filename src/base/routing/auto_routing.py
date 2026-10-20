import importlib
import logging
import os

from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)


def register_all_routers(app: FastAPI, base_path: str, base_module: str):
    """
    Automatically discovers and registers all APIRouter instances from modules in a given directory,
    adding the domain root folder as a tag.

    Args:
        app (FastAPI): The FastAPI application instance.
        base_path (str): The filesystem path where router modules are located.
        base_module (str): The base Python module name (e.g., "src.domains").
    """
    logger.info(f"registering routes from module {base_module}")
    for root, _, files in os.walk(base_path):
        for file in files:
            if file.endswith(".py") and not file.startswith("__"):
                module_path = os.path.join(root, file)

                # Convert file path to a module path (e.g., "src.domains.user")
                relative_path = os.path.relpath(module_path, base_path).replace(os.sep, ".")
                module_name = f"{base_module}.{relative_path[:-3]}"  # Remove ".py"

                try:
                    # Use the normal import machinery so modules are loaded once and
                    # cached in sys.modules. Re-exec'ing a fresh module object (via
                    # spec_from_file_location + exec_module) creates a detached duplicate
                    # that breaks packages with heavy or circular __init__ imports.
                    module = importlib.import_module(module_name)

                    # Extract the domain name (root folder) from the relative path
                    domain_name = relative_path.split(".")[0]

                    # If the module has an APIRouter instance, register it with a tag
                    if hasattr(module, "router") and isinstance(module.router, APIRouter):
                        # Respect explicit tags. A router may declare its own tags at
                        # the router level (APIRouter(tags=[...])) or per route
                        # (@router.get(..., tags=[...])). Only fall back to the domain
                        # folder name when nothing has been tagged, so Swagger groups
                        # by the intended, human-friendly tag instead of the raw domain.
                        has_explicit_tags = bool(module.router.tags) or any(
                            getattr(route, "tags", None) for route in module.router.routes
                        )
                        if not has_explicit_tags:
                            module.router.tags = [domain_name]

                        app.include_router(module.router)
                        logger.info(
                            f"✔ Registered router from module: {module_name} "
                            f"(tags: {module.router.tags or 'per-route'})"
                        )

                except Exception as e:
                    logger.error(f"❌ Failed to import module {module_name}: {str(e)}")
