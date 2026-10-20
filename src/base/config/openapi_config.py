from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from src.base.config.config import settings


class OpenAPIConfig:
    """Configuration class for OpenAPI/Swagger setup"""

    def __init__(self):
        self.tenant_id = settings.azure.entra_tenant_id
        self.client_id = settings.azure.entra_client_id
        self.scopes = settings.azure.entra_api_scopes

    def get_swagger_ui_init_oauth(self) -> dict[str, Any]:
        """Get Swagger UI OAuth initialization configuration"""
        return {
            "usePkceWithAuthorizationCodeGrant": True,
            "clientId": self.client_id,
            "scopes": self.scopes or "",
        }

    def get_swagger_ui_parameters(self) -> dict[str, Any]:
        """Get Swagger UI parameters"""
        return {
            "persistAuthorization": True,
        }

    def create_custom_openapi_schema(self, app: FastAPI) -> dict[str, Any]:
        """Create custom OpenAPI schema with Microsoft OAuth security"""
        if app.openapi_schema:
            return app.openapi_schema

        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
        )

        # Add Microsoft OAuth security scheme
        openapi_schema["components"]["securitySchemes"] = {
            "MicrosoftOAuth": {
                "type": "oauth2",
                "flows": {
                    "authorizationCode": {
                        "authorizationUrl": f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/authorize",
                        "tokenUrl": f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                        "scopes": {self.scopes: "Access the API"} if self.scopes else {},
                    }
                },
            },
        }

        # Add default security requirements
        # This tells Swagger UI that most endpoints need authentication
        openapi_schema["security"] = [{"MicrosoftOAuth": [self.scopes] if self.scopes else []}]

        # Remove security requirement from public endpoints
        for path, path_info in openapi_schema["paths"].items():
            for method, method_info in path_info.items():
                if method.lower() in ["get", "post", "put", "delete", "patch"]:
                    # Public endpoints don't need authentication
                    if "/public" in path or path in [
                        "/docs",
                        "/openapi.json",
                        "/favicon.ico",
                    ]:
                        method_info["security"] = []

        app.openapi_schema = openapi_schema
        return app.openapi_schema


def setup_openapi(app: FastAPI) -> None:
    """Setup OpenAPI configuration for the FastAPI app"""

    config = OpenAPIConfig()

    def custom_openapi():
        return config.create_custom_openapi_schema(app)

    app.swagger_ui_init_oauth = config.get_swagger_ui_init_oauth()
    app.swagger_ui_parameters = config.get_swagger_ui_parameters()
    app.openapi = custom_openapi
