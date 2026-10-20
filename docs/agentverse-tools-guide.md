# AgentVerse Tools Guide

How tools work in `src/domains/agentverse/tools/`. Read this before adding or editing a tool.

## What a tool is

A tool is a unit of capability an agent can run (writer, ideation, rag, draw, math, …).
Each tool is a `BaseTool` subclass plus registry metadata. The user selects a tool explicitly
by putting an `@<tool_key>` token in the message.

## Invocation contract (the important part)

A tool fires when the raw message text contains its **canonical key** prefixed with `@`.

- **Canonical key.** A tool is selected by its canonical key only.
- **Matching** (`build_at_key_pattern` in `_helpers/trigger_cli_parser.py`):
  case-insensitive, word-boundary anchored — `(?<!\w)@<canon_key>(?![\w-])`.
  `@writer`, `@Writer`, `@writer:` match; `foo@writer`, `@writerly` do not.
- **Canonicalization** (`utils/canonicalize.py`): lowercase, strip, keep `[a-z0-9_-]`.
  So it's `@green_claim` and not `@green-claim`/`@greenclaim`.
- **A message without an `@key`** routes to the chat/converse tool.

Resolution happens in `AgentChatService._resolve_tool_key` (`services/agent_chat_service.py`),
which canonicalizes the `@token` and looks it up in `tool_registry_instance._meta_by_key`.

### Canonical keys

`analyze`, `chat`, `cta`, `datetime`, `draw`, `evaluate`, `math`,
`rag`, `refine`, `auditor`, `green_claim`, `ideation`, `workflow`, `writer`.
(`analyze`/`evaluate`/`refine` are internal pipeline tools, not user-facing selections.)
MCP proxy tools register under their own keys.

### Mode and other options: `--flag` CLI

Per-call options are passed as CLI flags **inside the message text**, parsed anchored on the
`@key`. Mode is one of these flags:

```
@writer --mode="rewrite" --tone-of-voice="formal" --max-char-length=280 Make this punchier.
```

- `--mode` is an **opaque pass-through**: an unknown value falls back to the tool's default.
- Flag forms: `--flag=value`, `--flag value`, bare `--flag` (boolean true). A value runs until
  the next flag or end of string. Quotes are stripped.
- Free text (everything not consumed by a flag) becomes the tool's `topic` (or `topic_key`).

The frontend invokes a mode by emitting `@<key> --mode=<value>` in the message text.

## CLI parsing helpers (`tools/_helpers/trigger_cli_parser.py`)

Tools don't parse text by hand. Use these:

- `extract_cli_any(*, request, kwargs, text, tool_key, list_keys, int_keys, bool_keys,
  topic_key, short_to_long, extra_keys)` — the entry point. Merge order is
  `kwargs → text → request.cli`. `mode` is always in the allowed set. Reads `request["cli"]`
  if present, else scans `request` text fields (`raw_text`, `original_text`, `user_text`,
  `query`, `message`, …) for the `@key`.
- `parse_tool_cli_from_text(...)` — lower-level single-string parse.
- `build_at_key_pattern(tool_key)` / `strip_leading_at_key(s, tool_key)` — the `@key` regex.

`list_keys`/`int_keys`/`bool_keys` declare value coercion; everything else is a string.
See `tools/ideation_tool/__init__.py` for the canonical usage.

## Anatomy of a tool (`tools/base.py`)

- **`BaseTool`** — subclass and implement `async def _run(self, socket_emitter, **params)`.
  `BaseTool.execute(...)` wraps `_run` with timeout + retries and normalizes the return.
- **Return** either a `ToolResult(success, result, error, meta)` **or** a standardized
  envelope dict containing both `success` and `result` keys (passed through unchanged).
  Use `BaseTool._final_error(message=...)` for the error envelope.
- **`ToolConfig`** — base runtime config (`enabled`, `timeout`, `retries`, `retry_backoff`,
  `extra`, economy knobs). Tool-specific config subclasses a Pydantic model; constructors are
  tolerant (`extra="ignore"`).
- Class attrs: `name` (human label), `key` (canonical), `version`, `parameters`,
  `required_dependencies`.

## Context tools: `ToolContext` (`llm/helpers/datatypes.py`)

A tool that shapes an LLM turn rather than answering it implements
`async def provide_context(...) -> ToolContext` and registers with
`meta.route: invoker`. Exactly one tool runs per turn, so its `ToolContext` *is*
the turn's context - there is no merging with anyone else's.

```python
@dataclass
class ToolContext:
    system_instruction: str | None = None   # how the model should behave this turn
    user_instruction: str | None = None     # the user message for this turn
    images: list[EnvImage] = field(default_factory=list)
```

**This is field-for-field the tool-server's `ContextToolResult`**
(`../agentverse-tool-server/src/domain/tools/base.py`). A remote tool returns
that shape over the wire and `ToolContext.from_wire` reads it; a local tool
constructs `ToolContext` directly. The two are the same contract on purpose, and
a field added to one belongs in both.

What `llm/request.py:build` does with it:

- `system_instruction` is wrapped in a precedence block (`## Task Instructions -
  Tool <name>`, labelled from the registry) and appended to `instructions` after
  the persona. No instruction means no block. This wrap applies to **every**
  context tool, local and remote alike.
- `user_instruction` becomes the final user turn, replacing the raw `@tool`
  command; absent, the raw text is used. Either way `images` ride along with it
  as `input_image` content parts.
- Prior turns are replayed ahead of it only if the tool declares
  `supports: {"multi_turn_memory": True}`.

`provide_context` returning anything other than a `ToolContext` is a contract
violation: it is logged by name, the turn runs without that context, and the run
report carries `routing.context_degraded = {tool, reason}`. Same for a
`provide_context` that raises. The turn still answers.

There is no third phase after the model answers. A tool that produces the result
itself - including one that generates images, or makes an LLM call of its own on
the way - is a **pure** tool: it does its work inside `_run` and returns the
result, and the invoker never learns what kind of work that was.

## Registration (`registries/tool_registry.py`)

Register with the decorator (or a `register(registry)` entry point per module):

```python
@tool_registry.register_tool(key="datetime", name="Datetime Tool")
class DateTimeTool(BaseTool): ...
```

- `key` is canonicalized; if omitted, derived from the class `key` attr or the display name.
- `metadata` carries `config`, `config_schema`, `tags`, `supports`, `ui`,
  `outputs`, `meta.route`, etc.
- `meta.route` (`pure` / `invoker` / `proxy` / `llm_only`) describes how the tool is executed
  and is retained by the runtime.
- `register_tool(...)` kwargs are strict — only the documented fields.

`list_public` (the tool catalog the frontend reads) returns: `key`, `name`, `description`,
`version`, `capabilities`, `default_config`, `config`, `config_schema`, `parameters_schema`,
`ui`, `outputs`, `supports`, `tool_class`, `tags`.

## Declared outputs & step-to-step binding

A tool declares the typed outputs it returns under `metadata["outputs"]`, each an
entry of `{key, label, type, path}` (see `ToolOutputSpec` in `tools/spec.py`).
`path` is a dot-path into the tool's unwrapped `env`; the workflow engine projects
it into the templating namespace so a downstream step can bind
`${outputs.<step_id>.<key>}`. `type` is one of `string | number | boolean | list |
object`.

`object` (and `list`) outputs carry **structured** data between steps. When such a
binding is substituted into a downstream command, the engine emits **compact JSON**
(`_encode_command_value` in `libs/engine/execute/tool.py`) rather than a Python
`str()` repr. The consuming tool receives it as real structured input for free: it
declares the receiving param as `object`/`array`, the CLI splitter captures the JSON
blob as the flag value, and `coerce_val` (`utils/coercion.py`) `json.loads` it back
into a dict/list. So a bounding box declared as an `object` output flows
`producer.env → ${outputs.<id>.box} → --box {"x":..} → dict` end-to-end. Scalars keep
the historical `str()` projection.

### Declaring an object *input* (the `JsonField` widget)

The receiving side of an `object` binding is an input field the consuming tool
exposes in its `metadata["ui"]` config. A UIComponent renders through the same
two-hop path as every other field (widget → `flag_config` flag), so a JSON input is
just a component with `type: "object"` and `widget: "JsonField"`:

```json
{
  "id": "payload",
  "type": "object",
  "widget": "JsonField",
  "group": "Input",
  "flag_config": { "flag": "--payload", "required": false },
  "props": { "label": "Payload", "placeholder": "Enter JSON" }
}
```

The `type: "object"` is what makes `coerce_val` `json.loads` the flag value (see
above), and `widget: "JsonField"` is what the builder renders as an object-accepting
target — so an upstream `object` output binds into `--payload` cleanly and without an
advisory mismatch badge. `flag_config.flag` is the CLI flag the bound JSON lands on;
`required`/`props` follow the usual UIComponent conventions.

## File uploads

A tool opts into file uploads by declaring `metadata.supports.files`
(`max_count`, `max_size_bytes`, `accepted_mimes`). The user must invoke it with the tool's
`@key`; `AgentChatService._validate_files` then enforces the constraints and ownership against
the resolved tool.

See [`file-upload.md`](./file-upload.md) for the full end-to-end flow (upload endpoint, blob
storage, SAS minting, validation error codes, per-turn cleanup, and adding a file-aware tool).

## Adding a new tool — checklist

1. Create `tools/<name>_tool.py` (or a package). Subclass `BaseTool`, implement `_run`.
2. Define a Pydantic config subclass if the tool has settings.
3. Parse user options with `extract_cli_any(..., tool_key="<key>")`; declare list/int/bool keys.
4. Return a `ToolResult` or a `{success, result, ...}` envelope.
5. Register with `@tool_registry.register_tool(key="<key>", name="...")` and supply `metadata`
   (`supports`, `ui`, `tags`, `meta.route`).
6. Pick a **canonical key** — that key (prefixed with `@`) is how users invoke the tool.
7. Add tests under `src/domains/agentverse/tests/...`. Run `uv run --extra test pytest`,
   then `ruff check . && ruff format .` and `mypy src`.
