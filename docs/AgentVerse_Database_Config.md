# AgentVerse Database Configuration Guide

This document explains how to configure the database systems in
**AgentVerse** including both **document storage** (MongoDB/Cosmos DB) and
**vector storage** (ChromaDB/Azure AI Search).

------------------------------------------------------------------------

## 1. Overview

The system supports multiple database backends:

### Document Storage (for agent metadata, collections, etc.)
-   **Local MongoDB**
    -   Runs as a Docker service with a persistent volume.
-   **Azure Cosmos DB**
    -   Mongo-compatible API for production environments.

#### Collections (`DBService`)

| Constant | Collection | Purpose |
|----------|------------|---------|
| `AGENTS_COL` | `coll_agents` | Agent documents. |
| `VDB_COLS_COL` | `coll_vdb_collections` | Vector-store collection metadata. |
| `OPERATION_LOGS_COL` | `coll_operation_logs` | Audit/operation logs. |
| `MESSAGE_REPORTS_COL` | `coll_message_reports` | User-submitted message reports. |
| `GREEN_CLAIM_AUDITS_COL` | `coll_green_claim_audits` | Green-claim audit results. |
| `WORKFLOWS_COL` | `coll_workflows` | Persisted workflow catalog (source of truth for `workflow_key` resolution). Unique index on `key`. Seeded once from code-defined builtins; thereafter DB-managed via the `/api/v1/workflows` REST API. |

### Vector Storage (for knowledge base / RAG)
-   **ChromaDB (Local)**
    -   File-based vector database for local development.
    -   Stores embeddings in `./chroma_db` directory.
-   **Azure AI Search**
    -   Enterprise-grade vector search service for production.
    -   Purpose-built for RAG and Gen AI applications.

The selection logic is controlled by environment variables and
configuration classes in the application.

------------------------------------------------------------------------

## 2. Environment Variables

### Local Development (Docker Mongo)

``` env
ENVIRONMENT=Development
DB_URI=mongodb://mongodb:27017
DB_NAME=agentverse_local
```

### Azure Cosmos DB

``` env
ENVIRONMENT=prod
DB_URI=mongodb://<user>:<password>@<cosmos-host>:10255/?ssl=true&appName=@<account-name>@
DB_NAME=agentverse_prod
```

> ℹ️ The `retryWrites=false` option is automatically added if not
> present in Cosmos URIs.

### DB_BACKEND options

-   `auto` → Use Cosmos if ENVIRONMENT != Development
-   `cosmos` → Force Cosmos
-   `mongo` → Force Mongo

------------------------------------------------------------------------

## 3. Docker Compose Setup

A typical setup with MongoDB volume:

``` yaml
services:
  api:
    build: .
    env_file:
      - ./.env
    depends_on:
      - mongodb

  mongodb:
    image: mongo:7
    restart: unless-stopped
    volumes:
      - mongo_data:/data/db
    ports:
      - "27017:27017"

volumes:
  mongo_data:
```

------------------------------------------------------------------------

## 4. Application Behavior

-   If `DB_BACKEND=mongo` or `ENVIRONMENT=Development`, the app connects to
    the **local MongoDB** service.
-   If `DB_BACKEND=cosmos` or `ENVIRONMENT != Development`, the app connects to **Azure Cosmos**.

Log output during startup:

-   `🧪 Using local MongoDB...`
-   `🔐 Using Azure Cosmos DB (Mongo API)`

------------------------------------------------------------------------

## 5. Common Issues

-   **Connection errors** → Ensure Docker service is running for local,
    or credentials are valid for Cosmos.
-   **Auth errors on Cosmos** → Ensure `retryWrites=false` is present in
    the URI (the system adds it automatically).
-   **Wrong DB selection** → Check `ENVIRONMENT` and `DB_BACKEND`
    variables.

------------------------------------------------------------------------

------------------------------------------------------------------------

## 6. Vector Database Configuration

### ChromaDB (Local Development)

``` env
ENVIRONMENT=Development
```

-   ChromaDB stores vector embeddings in the `./chroma_db` directory.
-   Automatically used when `ENVIRONMENT=Development`.
-   No cloud dependencies required.

### Azure AI Search (Production)

``` env
ENVIRONMENT=prod  # or any value other than Development

# Azure AI Search settings (required for production)
AZURE_SEARCH_ENDPOINT=https://your-search-service.search.windows.net
AZURE_SEARCH_KEY=<your-admin-key>
AZURE_SEARCH_INDEX_PREFIX=agentverse
AZURE_SEARCH_VECTOR_DIMENSIONS=1536
```

-   **Azure Search Setup:**
    1. Create an Azure AI Search service in the Azure portal
    2. Copy the endpoint URL and admin key
    3. Set environment variables in your Azure deployment
    4. Indexes are created automatically on first document upload

------------------------------------------------------------------------

## 7. Application Behavior

### Document Storage
-   If `DB_BACKEND=mongo` or `ENVIRONMENT=Development`, the app connects to
    **local MongoDB**.
-   If `DB_BACKEND=cosmos` or `ENVIRONMENT != Development`, the app connects to **Azure Cosmos DB**.

### Vector Storage (Simple Selection)
-   If `ENVIRONMENT=Development`, the app uses **ChromaDB** (local file storage).
-   Otherwise, the app uses **Azure AI Search** (cloud service).

Log output during startup:

-   `🧪 Using local MongoDB...`
-   `🔐 Using Azure Cosmos DB (Mongo API)`
-   `📚 Using ChromaDB for vector storage (local development)`
-   `🔍 Using Azure AI Search for vector storage (production)`

------------------------------------------------------------------------

## 8. Common Issues

### Document Database
-   **Connection errors** → Ensure Docker service is running for local,
    or credentials are valid for Cosmos.
-   **Auth errors on Cosmos** → Ensure `retryWrites=false` is present in
    the URI (the system adds it automatically).
-   **Wrong DB selection** → Check `ENVIRONMENT` and `DB_BACKEND` variables.

### Vector Database
-   **ChromaDB persistence issues** → Ensure `./chroma_db` directory has write permissions.
-   **Azure Search authentication errors** → Verify `AZURE_SEARCH_KEY` and endpoint URL are set.
-   **Index not found** → Indexes are created automatically on first document upload.
-   **Embedding dimension mismatch** → Ensure `AZURE_SEARCH_VECTOR_DIMENSIONS=1536` for ada-002.

------------------------------------------------------------------------

## 9. Cost Considerations

### Azure Cosmos DB (Document Storage)
-   Free tier: 1,000 RU/s + 25GB storage
-   Production: Scales based on throughput and storage (~$25-200+/month)

### Azure AI Search (Vector Storage)
-   Free tier: 3 indexes, 50MB storage (development only)
-   Basic: $74/month (2GB storage)
-   Standard S1: $245/month (25GB storage, production-ready)
-   Includes unlimited queries (no per-query charges)

------------------------------------------------------------------------

## 10. Conclusion

This setup ensures **AgentVerse** automatically switches between local
databases (for development) and Azure cloud services (for production)
based on the `ENVIRONMENT` variable.

The dual-backend architecture provides:
-   **Simple local development** with ChromaDB and MongoDB (no cloud setup needed)
-   **Production scalability** with Azure AI Search and Cosmos DB
-   **Cost efficiency** by avoiding cloud services during development
-   **Automatic selection** based on environment
