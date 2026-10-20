# JWT Authentication Middleware

This directory contains middleware components for handling JWT authentication with **Microsoft Azure AD** integration in the FastAPI application.

## JWT Verification Middleware

The `JWTVerificationMiddleware` is responsible for automatically verifying Azure AD JWT tokens in incoming requests and providing authenticated user information to route handlers.

### Features

- **Azure AD Integration**: Validates JWT tokens issued by Microsoft Azure Active Directory using JWKS (JSON Web Key Set) validation.
- **Automatic Token Verification**: Extracts and verifies JWT tokens from the `Authorization` header with proper Bearer token format validation.
- **User Information Mapping**: Maps Azure AD token claims to a standardized user object in `request.state.user` for easy access in route handlers.
- **Regex Path Exclusions**: Uses regex patterns to skip verification for certain paths like documentation (`/docs`, `/redoc`), internal routes (`/internal/`), and health checks (`/metrics`).
- **Security Logging**: Comprehensive logging of unauthorized access attempts with detailed context for security monitoring.
- **Standardized Error Responses**: Returns structured JSON error responses with appropriate HTTP status codes and security headers.
- **Dependency Injection Ready**: Integrated with the application's dependency injection container for service management.

### Azure AD Token Claims Mapping

The middleware extracts and maps Azure AD token claims to a standardized `User` entity object:

```python
from src.base.entities.user import User

request.state.user = User(
    id=claims.get("oid"),                    # Azure Object ID
    email=claims.get("email") or claims.get("preferred_username"),
    name=claims.get("name"),                 # Display name
    roles=claims.get("roles", []),           # Application roles
    scopes=claims.get("scp", "").split()    # OAuth2 scopes
)
```

The `User` entity is a Pydantic model that provides:
- Type safety and validation
- IDE autocomplete support
- Clear attribute access (e.g., `user.id`, `user.roles`)
- Automatic JSON serialization

### How to Use

#### 1. In Protected Routes (Direct Access)

Access the authenticated user directly from the request state:

```python
from src.base.entities.user import User

@router.get("/protected")
async def protected_endpoint(request: Request):
    user: User = request.state.user
    return {
        "message": f"Hello, {user.name}!",
        "user_id": user.id,
        "email": user.email,
        "roles": user.roles
    }
```

#### 2. With Dependency Helpers

Use the dependency helpers in `src.base.dependencies.jwt_dependencies`:

```python
from src.base.dependencies.jwt_dependencies import (
    get_current_user_from_state,
    get_user_id_from_state,
    get_user_roles_from_state
)
from src.base.entities.user import User

@router.get("/protected")
async def protected_endpoint(
    current_user: User = Depends(get_current_user_from_state),
    user_id: str = Depends(get_user_id_from_state),
    user_roles: list = Depends(get_user_roles_from_state)
):
    return {
        "message": f"Hello, user {user_id}!",
        "roles": user_roles,
        "user_info": current_user
    }
```

#### 3. With Role-Based Access Control (RBAC)

Use the RBAC dependency from `src.base.authentication.rbac` to enforce role and scope requirements:

```python
from src.base.authentication.rbac import require_roles_and_scopes
from src.base.entities.user import User

@router.get("/admin")
async def admin_endpoint(
    user: User = Depends(require_roles_and_scopes(
        required_roles=[["admin"], ["super_admin"]],  # User needs admin OR super_admin role
        required_scopes=[["read", "write"]]           # User needs both read AND write scopes
    ))
):
    return {"message": "Admin access granted", "user": user}
```

#### 4. Excluding Paths from Verification

The middleware uses **regex patterns** to exclude certain paths from JWT verification. Default excluded paths:

- `^/docs` - OpenAPI documentation
- `^/redoc` - ReDoc documentation
- `^/openapi.json` - OpenAPI schema
- `^/internal/` - Internal system routes
- `^/metrics` - Prometheus metrics

To modify excluded paths, update the `exclude_paths` parameter when registering the middleware:

```python
middleware = JWTVerificationMiddleware(
    app=app,
    jwt_service=jwt_service,
    exclude_paths=[
        r"^/docs",
        r"^/health",
        r"^/public/.*",  # All public routes
        r"^/auth/login"  # Login endpoint
    ]
)
```

## JWT Service

The JWT middleware uses the `JWTService` to handle Azure AD token verification and validation. The service integrates with Microsoft Azure AD's JWKS (JSON Web Key Set) endpoint for secure token validation.

### Azure AD Configuration

The authentication system requires the following Azure AD configuration:

```python
TENANT_ID = settings.azure.entra_tenant_id    # Your Azure AD tenant ID
CLIENT_ID = settings.azure.entra_client_id    # Your registered application ID
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
JWKS_URL = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"
```

### Token Validation Process

The service performs the following validation steps:

1. **JWKS Retrieval**: Fetches public keys from Azure AD's JWKS endpoint (cached for performance)
2. **Header Validation**: Verifies the JWT header and extracts the key identifier (`kid`)
3. **Signature Verification**: Validates the token signature using RS256 algorithm
4. **Claims Validation**: Verifies issuer, audience, and expiration claims
5. **User Mapping**: Maps Azure AD claims to application user object

## Security Features

### Enhanced Security Logging

The middleware includes comprehensive security logging for monitoring unauthorized access attempts:

- **Failed Authentication Attempts**: Logs details about invalid tokens, missing headers, and malformed requests
- **Security Context**: Captures request details, client information, and failure reasons
- **Audit Trail**: Maintains detailed logs for security analysis and compliance

### Standardized Error Responses

All authentication failures return structured JSON responses:

```json
{
  "success": false,
  "statusCode": 401,
  "message": "Authentication required: Missing Authorization header"
}
```

### Security Headers

Unauthorized responses include appropriate security headers:

- `WWW-Authenticate: Bearer` - Indicates Bearer token authentication is required

## Working with CORS and Other Middleware

The JWT middleware should be registered early in the middleware stack to ensure it runs after CORS middleware but before most other application-specific middleware. This ordering is important for proper handling of preflight requests and authentication flows.

## Testing with Azure AD Tokens

To test the authentication system with Azure AD:

1. **Obtain Token**: Get a valid JWT token from Azure AD using your application registration
2. **Set Authorization Header**: Include the token in requests as `Authorization: Bearer <token>`
3. **Access Protected Routes**: Use the token to access protected endpoints

Example using curl:

```bash
curl -H "Authorization: Bearer <your-azure-ad-token>" \
     http://localhost:8000/protected-endpoint
```

## Troubleshooting

### Common Issues

1. **Invalid Signing Key**: Ensure your Azure AD tenant ID and client ID are correctly configured
2. **Token Expiration**: Azure AD tokens have limited lifetime; refresh tokens as needed
3. **Audience Mismatch**: Verify the token was issued for your specific application (client ID)
4. **Network Issues**: Ensure the application can reach Azure AD's JWKS endpoint for key validation
