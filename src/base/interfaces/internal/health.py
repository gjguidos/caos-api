from fastapi import APIRouter
from starlette.responses import JSONResponse

from src.base.dependencies.di_container import Container  # Import the DI container

router = APIRouter()


@router.get("/health", tags=["Health"])
async def health():
    """
    Health check endpoint.
    This endpoint returns 200 OK if the service is healthy.
    """
    return JSONResponse(
        status_code=200, content={"status": "Healthy", "message": "Service is up and running."}
    )


@router.get("/", tags=["Health"])
async def health_v2():
    """
    Health check endpoint.
    This endpoint returns 200 OK if the service is healthy.
    """
    return JSONResponse(
        status_code=200,
        content={"status": "Healthy", "message": "Service is up and running."},
    )


@router.get("/readiness", tags=["Health"])
async def readiness():
    """
    Advanced readiness check endpoint.
    This endpoint checks if the service is ready to accept requests by verifying dependencies.
    """
    container = Container()
    mongo_client = container.db_settings.mongo_client()

    # Check if the MongoDB client is connected
    if mongo_client.client is None:
        return JSONResponse(
            status_code=503,
            content={"status": "Not Ready", "message": "MongoDB client is not connected."},
        )

    return JSONResponse(
        status_code=200,
        content={"status": "Ready", "message": "Service is ready to accept traffic."},
    )
