"""
Main entry point for the API.
"""

import json
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.base.config.config import settings
from src.base.handlers.json_encoder import CustomJSONEncoder
from src.base.lifespan.lifespan import lifespan
from src.base.logging.logging_config import LoggingConfig
from src.base.system.initialize_app import initialize_app
from src.base.utils.env_utils import is_local_development

# --- Logging configuration ---
LoggingConfig.setup_logging()
logger = logging.getLogger(__name__)


# Configure FastAPI to use the custom JSON encoder
def custom_json_serializer(*args, **kwargs):
    return json.dumps(*args, cls=CustomJSONEncoder, **kwargs)


# OpenAPI tag metadata — controls the order and gives a one-line description for
# each group shown in Swagger UI. Tags assigned on routers/routes must match these
# names exactly. Tags not listed here still appear, but after the ordered ones.
OPENAPI_TAGS = [
    {
        "name": "Agents",
        "description": "Agent management: list, fetch, update, delete agents, plus personalities, cognitive modules and soul-protocol schema.",
    },
    {"name": "Agent Collections", "description": "Knowledge collections attached to agents."},
    {
        "name": "Workflows",
        "description": "Workflow definitions: list, fetch, create, update, delete and validate.",
    },
    {"name": "Files", "description": "File uploads used by agents and workflows."},
    {"name": "Operations", "description": "Audit trail of operation logs (admin)."},
    {"name": "Reports", "description": "Message reports (admin)."},
    {"name": "Green Claim", "description": "Green-claim compliance auditing endpoints."},
    {
        "name": "Realtime (WebSocket)",
        "description": "WebSocket channels for live agent interaction.",
    },
    {"name": "Health", "description": "Liveness and readiness probes."},
    {
        "name": "Rate Limiting",
        "description": "Example endpoints demonstrating rate-limit policies.",
    },
]

# Create the FastAPI app
is_local_dev = is_local_development()
app = FastAPI(
    title="CAOS API",
    description="API for the CAOS platform",
    openapi_tags=OPENAPI_TAGS,
    contact={
        "name": settings.application.application_owner,
        "email": settings.application.application_owner_email,
    },
    version="0.0.1",
    lifespan=lifespan,
    default_response_class=JSONResponse,
    json_dumps=custom_json_serializer,
    docs_url="/docs" if is_local_dev else None,
    redoc_url="/redoc" if is_local_dev else None,
    openapi_url="/openapi.json" if is_local_dev else None,
)

# Delegate the startup process to the startup module
app = initialize_app(app)

logger.info(settings.texts_new.main.application_started)
