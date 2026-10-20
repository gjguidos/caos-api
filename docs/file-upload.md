# File Upload Support

Cross-repo recap of how file upload works end-to-end. Use this as a reference when extending the feature, adding new file-aware tools, or debugging issues.

## Scope

Users can attach files to chat messages directed at tools that opt in via `supports.files`. Bytes are uploaded once over HTTPS to the API, stored in Azure Blob, and referenced on the WebSocket by ID. The tool server (or proxy-delegated tools) fetches the bytes via short-lived SAS URLs minted server-side, processes them, and the API deletes the blobs after the turn completes.

Repos involved:

- `agentverse-api` — upload endpoint, blob storage, SAS minting, WS validation, per-turn cleanup
- `agentverse-proxy` — passes `supports.files` and `files` through the MCP contract
- `agentverse-tool-server` — `FileClient` for fetching uploads inside tools; `extract_tool` reference implementation
- `agentverse-client` — paperclip UI, XHR multipart upload, file refs in WS message

## Wire Contract

A single file is represented everywhere as:

```json
{
  "file_id": "string",
  "name": "string",
  "mime": "string",
  "size": 0,
  "sha256": "string",
  "url": "https://...?sas-token"   // present only on tool-server-bound payloads
}
```

- API → client: no `url` (client never needs the bytes back).
- API → MCP proxy → tool server: `url` is a freshly minted SAS URL with a short TTL (~5 min).
- WS message from client → API: only `file_id` references; the API resolves them from its own store.

**SAS URLs are bearer credentials. Never log them.** Log `file_id`, `name`, `mime`, `size`, `sha256` freely.

## agentverse-api

### Endpoint

`POST /api/v1/files` (multipart/form-data, field `file`) — `src/domains/agentverse/interfaces/api/v1/file_routes.py`

Auth: standard JWT; user must have `agentverse-user` or `agentverse-admin`.

Response: the wire contract minus `url`.

### Storage

- `src/base/services/file_service.py` — `FileService` with `upload`, `get_metadata`, `mint_sas_url`, `delete_many`
- `src/base/config/blob_storage_config.py` — `BlobStorageConfig` (connection string, container, default SAS TTL)
- `src/base/dependencies/containers/blob_storage_container.py` — DI container, registered in root `di_container.py`

MIME sniffing uses `puremagic`.

### WS Validation & Cleanup

`src/domains/agentverse/services/agent_chat_service.py`:

- `_validate_files()` — resolves each `file_id` to metadata, enforces the `@tool_key`-resolved tool's `supports.files` (`max_count`, `max_size_bytes`, `accepted_mimes`). Rejects unknown or unauthorized IDs.
- `_resolve_tool_key()` — picks the tool from the message text by canonical `@tool_key`.
- `_handle_regular_response` — accepts the resolved `user` and runs a `try/finally` that calls `FileService.delete_many(file_ids, user_id=...)` after the turn, whether or not the tool succeeded.

### SAS Minting

`src/base/integrations/mcp/proxy_tool.py` — `MCPProxyTool.run()` mints SAS URLs for each file ref immediately before dispatching to the MCP proxy. URLs never leave that boundary.

### FilesSupport on Tool Specs

`src/domains/agentverse/tools/spec.py`:

```python
class FilesSupport(_BM):
    max_count: int = 1
    max_size_bytes: int = 200 * 1024 * 1024
    accepted_mimes: list[str] | None = None
```

`ToolSpec.supports` is a plain dict (`{"files": {...}}`); use the `ToolSpec.files_support` property to get a parsed `FilesSupport` or `None`.

`MCPToolSource` (`src/domains/agentverse/registries/mcp_tool_source.py`) preserves `supports` verbatim from the proxy in `enriched_metadata["supports"]`.

## agentverse-proxy

Schema-locking tests in `test_backend.py`, `test_tool_schema.py`, `test_routes_tools.py` ensure:

- `supports.files` from the upstream tool server flows through to API consumers
- The proxy forwards `files` (list of dicts with `url`) on tool execute requests
- Pydantic models use `extra="allow"` so the wire contract can evolve without proxy redeploys

50/50 tests pass.

## agentverse-tool-server

### FileClient

`src/base/infrastructure/file_client.py` — reusable, framework-agnostic.

```python
from src.base.infrastructure.file_client import FileClient, UploadedFile, FileDownloadError

client = FileClient()  # defaults: timeout=60s, max_bytes=200 MiB, verify_checksum=True
uploaded = UploadedFile.from_wire(file_dict)
data: bytes = await client.fetch(uploaded)

# Or, when a tool only ever takes one file:
data: bytes | None = await client.fetch_first(request.files)
```

- `UploadedFile` is a frozen dataclass over the wire contract.
- `fetch()` streams via `httpx.AsyncClient`, caps at `max_bytes`, verifies SHA256 if present.
- Errors: `FileDownloadError` (HTTP/network), `FileSizeExceededError`, `FileChecksumError`. Error messages contain `file_id` and `name`, never the URL.

### Tool Integration Pattern

A tool that accepts files:

1. Declares `supports.files` in its `ToolSpec` (see `src/domain/tools/extract_tool/spec.py`).
2. Accepts `files: list[dict[str, Any]] | None = None` in `run()` (and `provide_context()` for invoker-route tools). `BaseTool.run` carries the kwarg.
3. Instantiates a `FileClient` once in `__init__` and calls `fetch` / `fetch_first` from `run`.

The HTTP route at `src/domain/server/interface/routes.py` passes `files=request.files or []` straight to the tool instance.

### Reference Implementation: extract_tool

`src/domain/tools/extract_tool/__init__.py` — extracts text from PPTX using `python-pptx`. Invoke with canonical `@extract`. Max 1 file, 200 MiB.

### Dependencies

`pyproject.toml`: `httpx>=0.27.0`, `python-pptx>=0.6.23`.

### Tests

- `tests/domain/tools/test_file_client.py` — 15 tests over `UploadedFile`, fetch happy/error paths, size cap, checksum verification, error message redaction.
- `tests/domain/tools/test_extract_tool.py` — 7 tests. Patches `tool._file_client.fetch` directly.

Patch target if mocking transport: `src.base.infrastructure.file_client.httpx.AsyncClient`.

32/32 tests pass.

## agentverse-client

- `src/app/(workspace)/chat/_api/upload_file.ts` — XHR multipart upload with progress events.
- `src/app/(workspace)/chat/_hooks/useFileUpload.ts` — upload state (idle/uploading/done/error), exposes the resulting `FileRef`.
- `src/app/(workspace)/chat/_hooks/useAgentChat.ts` — `sendMessageWithFiles` puts `files: FileRef[]` into the WS message body.
- `src/app/(workspace)/chat/_components/ToolChatBar/ToolChatBar.tsx` — paperclip button + pending file pill, gated by the selected tool's `supports.files`.
- `src/store/slices/registry/toolsSlice.ts` — `ToolDefinition.supports` extended with the `FilesSupport` shape so the UI can read constraints.

Type definitions live in `src/app/(workspace)/chat/websocket.types.ts` (`FileRef`) and `chat.types.ts` (`FilesSupport`, `EnrichedTool`).

## Adding a New File-Aware Tool

1. **Tool server**: in the tool's `spec.py`, set `supports={"files": {"max_count": N, "max_size_bytes": M, "accepted_mimes": [...]}}`.
2. Add `files: list[dict[str, Any]] | None = None` to the tool's `run` signature.
3. Use `FileClient` (`from src.base.infrastructure.file_client import FileClient`) to fetch bytes inside `run`.
4. The proxy auto-forwards `supports` and `files`; no changes needed.
5. The API auto-applies validation via `_validate_files` against the tool resolved from the message's `@tool_key`.
6. The client picks up the paperclip automatically once `supports.files` is in the tool definition.

## Security Notes

- SAS URLs: short TTL, scoped to a single blob, read-only. Minted server-side per tool dispatch. **Never logged, never returned to the client.**
- File visibility: an uploaded file is owned by the uploading user; `_validate_files` rejects `file_id`s that don't belong to the requesting user.
- Cleanup: blobs are deleted in a `finally` after the chat turn, regardless of tool outcome. Failures to delete are logged but don't fail the turn.
- Size/MIME enforced server-side at validation time; the client UI hint is a convenience, not a guarantee.

## Key Files Quick Reference

| Concern | File |
|---|---|
| Upload endpoint | `agentverse-api/src/domains/agentverse/interfaces/api/v1/file_routes.py` |
| Blob storage | `agentverse-api/src/base/services/file_service.py` |
| SAS minting at dispatch | `agentverse-api/src/base/integrations/mcp/proxy_tool.py` |
| WS validation + cleanup | `agentverse-api/src/domains/agentverse/services/agent_chat_service.py` |
| `FilesSupport` model | `agentverse-api/src/domains/agentverse/tools/spec.py` |
| FileClient | `agentverse-tool-server/src/base/infrastructure/file_client.py` |
| Tool integration example | `agentverse-tool-server/src/domain/tools/extract_tool/__init__.py` |
| Client upload | `agentverse-client/src/app/(workspace)/chat/_api/upload_file.ts` |
| Client send-with-files | `agentverse-client/src/app/(workspace)/chat/_hooks/useAgentChat.ts` |
