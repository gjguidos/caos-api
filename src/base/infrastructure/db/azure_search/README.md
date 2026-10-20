# Azure AI Search Integration for AgentVerse

This directory contains the Azure AI Search implementation for vector storage in AgentVerse, providing a production-ready alternative to ChromaDB for RAG (Retrieval Augmented Generation) workloads.

## Overview

Azure AI Search is Microsoft's enterprise-grade search service with native vector search capabilities, purpose-built for Gen AI and RAG applications. This integration provides:

- **ChromaDB-compatible interface** - Drop-in replacement with minimal code changes
- **Automatic index management** - Indexes created on-demand
- **Production-ready** - Enterprise SLA, global availability, automatic scaling
- **Hybrid search** - Combines vector similarity with keyword search
- **Managed service** - No infrastructure management required

## Architecture

```
azure_search/
├── azure_search_client.py      # Core client with ChromaDB-compatible API
```

## Configuration

Add these environment variables to your `.env` file:

```bash
# Azure AI Search settings (required for production)
AZURE_SEARCH_ENDPOINT=https://your-service.search.windows.net
AZURE_SEARCH_KEY=your-admin-key-here
AZURE_SEARCH_INDEX_PREFIX=agentverse
AZURE_SEARCH_VECTOR_DIMENSIONS=1536  # For text-embedding-ada-002
```

### Backend Selection Logic

The system automatically selects the vector database based on the `ENVIRONMENT` variable:
- `ENVIRONMENT=Development`: Uses **ChromaDB** (local file storage)
- `ENVIRONMENT=prod` (or any other value): Uses **Azure AI Search** (cloud service)

## Setup

### 1. Create Azure AI Search Service

```bash
# Using Azure CLI
az search service create \
  --name your-search-service \
  --resource-group your-rg \
  --sku basic \
  --location eastus

# Get admin key
az search admin-key show \
  --service-name your-search-service \
  --resource-group your-rg
```

Or create via [Azure Portal](https://portal.azure.com/#create/Microsoft.Search).

### 2. Install Dependencies

```bash
pip install azure-search-documents>=11.6.0 azure-identity>=1.19.0
# Or install from project root:
pip install -e ".[test]"
```

### 3. Configure Environment Variables

Update your `.env` file with the endpoint and admin key from step 1.

## Usage

### In Application Code

The integration is transparent - no code changes needed in your agents or tools:

```python
# Your existing RAG tool code works unchanged
from src.domains.agentverse.tools.rag_tool import SecureRAGTool

# Tool automatically uses Azure Search in production
tool = SecureRAGTool(llm=llm, vectordb=vectordb, config=config)
result = await tool._run(query="What is the SLA?", collection="docs")
```

## API Reference

### AzureSearchClient

ChromaDB-compatible client for Azure AI Search.

#### Methods

##### `add_documents(documents, ids, collection_name)`
Add documents with automatic embedding generation.

**Parameters:**
- `documents` (List[Document]): LangChain Document objects
- `ids` (List[str]): Unique document IDs
- `collection_name` (str): Target index name

**Returns:** `bool` - Success status

##### `add_embeddings(embeddings, ids, metadatas, contents, collection_name)`
Add precomputed embeddings (faster for bulk uploads).

**Parameters:**
- `embeddings` (List[List[float]]): Embedding vectors
- `ids` (List[str]): Unique document IDs
- `metadatas` (List[dict]): Metadata dictionaries
- `contents` (List[str]): Document text content
- `collection_name` (str): Target index name

**Returns:** `bool` - Success status

##### `similarity_search_with_score(query, collection, k, filter, request)`
Perform vector similarity search.

**Parameters:**
- `query` (str): Query text
- `collection` (str): Index name
- `k` (int): Number of results (default: 5)
- `filter` (dict, optional): Metadata filters
- `request` (Any, optional): FastAPI request context

**Returns:** `List[Tuple[Document, float]]` - Documents with similarity scores

##### `has_document(doc_id, collection_name)`
Check if document exists.

**Returns:** `bool`

##### `clear_collection(collection_name)`
Delete an entire index.

**Returns:** `bool` - Success status

## Index Schema

Each Azure Search index includes these fields:

| Field | Type | Purpose |
|-------|------|---------|
| `id` | String (key) | Unique document identifier |
| `content` | String | Document text content (searchable) |
| `content_vector` | Collection(Single) | Embedding vector (1536 dims for ada-002) |
| `metadata` | String | JSON-serialized metadata |
| `source` | String | Source file/URL (filterable) |
| `page` | String | Page number (filterable) |
| `user_id` | String | User id (filterable) |
| `agent_id` | String | Agent id (filterable) |

Vector configuration:
- **Algorithm**: HNSW (Hierarchical Navigable Small World)
- **Metric**: Cosine similarity
- **Parameters**: m=4, ef_construction=400, ef_search=500

## Performance Characteristics

### Query Latency
- **P50**: ~50-100ms (within Azure region)
- **P95**: ~200-300ms
- **P99**: ~500ms

### Indexing Throughput
- **Batch size**: 1,000 documents recommended
- **Throughput**: ~100-500 docs/sec (Standard S1)
- **Concurrent uploads**: Supported via batch API

### Scale Limits (Standard S1)
- **Max documents**: ~15 million (1KB each)
- **Max index size**: 25 GB
- **Max indexes**: 50
- **Max fields per index**: 1,000

## Troubleshooting

### "Azure Search not configured" warning
**Solution:** Set `AZURE_SEARCH_ENDPOINT` and `AZURE_SEARCH_KEY` environment variables.

### Index creation fails
**Possible causes:**
- Invalid service endpoint or key
- Insufficient permissions (need admin key, not query key)
- Service tier limits reached

**Solution:** Verify credentials and check Azure portal for service status.

### Embedding dimension mismatch
**Error:** `Vector field dimensions don't match`

**Solution:** Ensure `AZURE_SEARCH_VECTOR_DIMENSIONS` matches your embedding model:
- `text-embedding-ada-002`: 1536
- `text-embedding-3-small`: 1536
- `text-embedding-3-large`: 3072

### Search returns no results
**Possible causes:**
- Index is empty
- Query embedding dimension mismatch
- Overly restrictive filters

**Debug:**
```python
# Check if documents exist
client = self._get_search_client(collection)
results = client.search(search_text="*", top=10)
for doc in results:
    print(doc["id"], doc["content"][:100])
```

## Security

### Authentication Options

1. **Admin Key** (current implementation):
   ```python
   credential = AzureKeyCredential(key)
   ```

2. **Azure AD / Managed Identity** (recommended for production):
   ```python
   from azure.identity import DefaultAzureCredential
   credential = DefaultAzureCredential()
   ```

### Network Security

- Enable firewall rules in Azure portal to restrict access by IP
- Use Private Endpoints for Azure VNet integration
- Disable public access if using Private Link

### Key Management

**Development:**
- Store keys in `.env` file (not committed to git)

**Production:**
- Use Azure Key Vault for secret management
- Enable Managed Identity for the web app
- Rotate keys regularly (Azure Search supports dual keys)

## Monitoring

### Application Insights Integration

```python
# Log search operations
logger.info(
    "Azure Search query",
    extra={
        "collection": collection,
        "query": query,
        "results_count": len(results),
        "latency_ms": elapsed_ms
    }
)
```

## References

- [Azure AI Search Documentation](https://learn.microsoft.com/en-us/azure/search/)
- [Vector Search in Azure AI Search](https://learn.microsoft.com/en-us/azure/search/vector-search-overview)
- [Python SDK Reference](https://learn.microsoft.com/en-us/python/api/overview/azure/search-documents-readme)
- [Pricing Calculator](https://azure.microsoft.com/en-us/pricing/details/search/)

## Support

For issues or questions:
1. Check [Common Issues](#troubleshooting) above
2. Review application logs for errors
3. Check Azure portal for service health
4. Consult [AgentVerse Database Config Guide](../../../docs/AgentVerse_Database_Config.md)
