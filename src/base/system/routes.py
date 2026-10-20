from src.base.interfaces.internal.health import router as health_check_router
from src.base.interfaces.internal.rate_limited_endpoints import router as rate_limited_router
from src.base.interfaces.public.operations import router as operations_router


def setup_internal_routes(app):
    # Include the health and readiness endpoints with a prefix
    app.include_router(health_check_router, tags=["Internal"])
    # Include rate-limited example endpoints
    app.include_router(rate_limited_router, prefix="/rate-limit-examples", tags=["Rate Limiting"])

    app.include_router(operations_router, prefix="/api/v1/operations", tags=["Operations"])
