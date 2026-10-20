# AgentVerse Conversation History

How a chat agent's conversation history is stored, replayed, and kept out of the
model when it shouldn't be there. Written for future sessions touching the
`ChatAgent` → orchestrator → LLM invoker → tool path.

## TL;DR

- History is a **per-user / per-agent / per-session Redis list** of plain dicts
  (`{"role", "content", ...}`), newest-first in Redis, returned oldest-first.
- One object owns all of it: **`Conversation`** (`history/conversation.py`),
  constructed per turn by the `ChatAgent`. It snapshots on `load()` and writes
  through on `append_*`, so read-before / write-after is structural rather than
  a property of anyone's statement order.
- Prior turns are replayed into the prompt in **exactly one place**,
  `llm/request.py:build`, gated on the turn's tools declaring
  `supports.multi_turn_memory: true`. Tools do not splice history themselves.
- For a `context_based` tool turn (`@writer ...`), the entry stored is **the
  expanded prompt the model actually saw**, not the raw `@tool` command. The raw
  command is kept in `meta` for audit only and **never sent to the model**.
- Stored entries can carry bookkeeping keys (`meta`, `name`). The **single
  outbound gate** `ChatLLMMixin._sanitize_messages` strips everything except
  `role` / `content` / `name` right before the LLM call.

## The Conversation module

`src/domains/agentverse/history/conversation.py`

Seven members and one flag. Everything behind it - the Redis list, the window, the
role mapping, `meta` stripping, the rendered-vs-raw user turn rule - is private.

| Member | Purpose |
| --- | --- |
| `await Conversation.load(cache=, request=, user_id=, agent_id=, session_id=)` | Snapshot the session's stored entries. |
| `as_messages() -> list[ChatMessage]` | The snapshot as replay messages: `role` / `content` / `name`, `meta` dropped. |
| `len(conversation)` | How many turns the snapshot holds. What the run report and the log lines want. |
| `entries() -> list[dict]` | The raw snapshot, as persisted, `meta` and all. No production caller - it is the only window onto what was actually stored, which is what the tests assert against. |
| `brief() -> str` | The last meaningful text, for resolving a bare `@tool` command. |
| `await append_user(raw, rendered=None)` | Record the user turn. |
| `await append_assistant(text)` | Record the reply. |
| `degraded: bool` | The store failed this turn; the snapshot is empty for that reason, not because the conversation is new. |

`load()` captures the list once. `append_*` writes through to Redis **without**
mutating the snapshot, so `as_messages()` keeps returning the pre-turn view for
the whole turn. There is deliberately no `commit()` - a buffered design adds a
lifecycle step that can be forgotten and loses the turn on a crash.

`AgentHistoryStore` (`history/store.py`) is the Redis adapter and is private to
`Conversation` - it has no other callers.

- **Key** (`cache/helpers.py:history_key`):
  `…:history:v{ver}:{user_id}:{agent_id}:{session_id or "default"}`.
- `append_many` → `LPUSH` each JSON entry, then `LTRIM` to
  `redis_history_max_items`, then `EXPIRE` to `redis_history_ttl`.
- `fetch` → `LRANGE 0..limit-1`, JSON-decodes, reverses to oldest-first, and
  returns `(entries, undecodable_count)`. One corrupt entry must not empty a
  conversation, but the count comes back so `Conversation` can say so out loud
  rather than silently returning a shorter list.

### Degraded turns

A store failure does **not** fail the turn - a Redis blip should not take chat
down. `load()` returns an empty conversation with `degraded=True`, logs once at
error level, and the flag reaches the run report as `input.history_degraded`.
That turns "the bot forgot our conversation" from an unfalsifiable complaint
into a field you can check.

## Per-turn flow (`ChatAgent.respond`)

`src/domains/agentverse/agents/chat_agent/chat_agent.py`

1. `_parse_user_input` splits the raw input into `clean_user_input` + flags.
2. `_load_conversation(session_id)` → `Conversation.load(...)`.
3. `_execute_orchestrator(...)` calls `orchestrator.plan_and_execute(...)` with a
   `ctx` carrying `request`, `raw_request`, `conversation`, flags, etc. The
   orchestrator returns the populated `ctx`. The `Conversation` travels as the
   object, never as a snapshot of its entries alongside it: a phase that wants
   a count calls `entries()`, and the run report reads `degraded` off the live
   object so a store failure after the loop started still shows up.
4. `conversation.append_user(user_input, rendered=ctx.get("rendered_user"))`.
5. `conversation.append_assistant(...)` from the reply.

The entries stored for turn N are therefore replayed starting at turn N+1.

If the agent has no role or no orchestrator there is nothing to run: the user
turn is recorded and `OrchestratorUnavailableError` is raised, which
`handle_chat` maps to an `ORCHESTRATOR_UNAVAILABLE` error envelope. Recording
first means a retry sees an unbroken conversation.

## What gets stored for the user turn

`append_user(raw, rendered=None)`:

- If `rendered` is a **non-empty string** → store it as `content`, with
  `meta={"raw_input": raw}`. This is the `context_based` tool case (local **or**
  proxy/external): the model-facing prompt is persisted, the raw `@tool` command
  is kept for audit.
- Otherwise (plain chat, pure tools, no constructed prompt) → store `raw`
  unchanged, no `meta`.

`rendered` is always a `str | None` by the time it reaches the agent:
`ToolContext.user_instruction` is a plain string on every path, local or remote.
A tool's images are a separate field and never form part of the persisted text.
See [Why images aren't persisted](#why-images-arent-persisted-in-history) for
the rationale.

### How `rendered_user` reaches the agent

The expanded prompt is produced deep in the LLM invoker and bubbled up through
the orchestrator via the `ctx` dict. The hop chain:

1. `llm/invoker.py` — after `resolve_tool_context`, set
   `ctx["rendered_user"] = tool_context.user_instruction` when the tool
   constructed one. (`user_instruction or user_text` is what the model actually
   receives, so the persisted entry matches the model-facing user message.)
2. `tools/tool_manager_v1/execution/invoker_path.py` — `as_envelope` normalizes
   the tool response to a fixed key set and **drops the llm `context`**, so
   `rendered_user` is explicitly re-attached to the envelope top level.
3. `agents/orchestrator/built_in_phases/h_task.py` — reads
   `env["rendered_user"]` and lifts it onto the orchestrator `ctx`.
4. `chat_agent.py` reads `ctx.get("rendered_user")` and passes it to
   `append_user`.

If no context tool ran (plain chat / pure / `llm_only` turns), `rendered_user` is
never set and the raw input is stored.

## How history is replayed to the model

**One splice, in `llm/request.py:build`.** The `Conversation` rides the tool
path from `ctx["conversation"]` → `h_task` common kwargs → the router →
`execute_invoker_path` → `invoker.llm` → `build`, which puts
`conversation.as_messages()` before the final user turn. Tools receive no
history and do no splicing of their own.

The gate lives inside `build` rather than at the call site because a caller who
must remember to pass the conversation is a caller who will one day forget.

Two related but separate uses of the same data survive:

- **Brief recovery.** A bare `@green_claim` resolves its prompt from
  `conversation.brief()`, which `invoker.llm` passes to
  `resolve_tool_context` as `history_brief`. This is independent of
  `multi_turn_memory` - a tool that wants no replay can still be *about* the
  last thing said. A pure tool reaches the same brief through
  `ctx["conversation"]` and recovers its own subject: `draw` does this inside
  `_run` (`tools/_helpers/tool_command.py:is_bare_tool_command` is the shared
  predicate).
Nothing else reads the conversation. In particular `rag` does **not**: it
answers each question from retrieved context alone. It once took a `history=`
kwarg, but the shape it expected (`{"user": …}` / `{"agent": …}`) was produced
by a tool-scoped memory stream that no longer exists, so the kwarg had been
silently dead. It is gone rather than repaired - if rag should condense
follow-ups against prior turns, that is a feature to design, not a parameter to
re-wire.

### The `multi_turn_memory` contract (tool authors, incl. remote MCP)

A tool opts **in** to conversation replay by declaring, in its registry
metadata:

```python
"supports": {"multi_turn_memory": True}
```

Rules, all deliberate:

- **Absent means no history.** A tool that says nothing receives nothing. This
  is what lets a remote tool server that has never heard of this flag keep its
  current behaviour rather than silently starting to receive conversation
  content over the wire. That is a data-egress property, not just a
  prompt-content one.
- **Remote MCP tools must send a JSON boolean.** The value is read as
  `supports.get("multi_turn_memory") is True`. A string `"true"` is treated as
  no. Remote `supports` is untrusted JSON, and failing tool *registration* over
  a sloppy value would take a third-party tool offline for what is only a
  prompt-window preference - so it fails closed instead, in the same direction
  as the default.
- **One tool per turn.** `h_plan` emits exactly one step, so the predicate asks
  one key - there is no merge policy to pick.

The predicate lives in `llm/helpers/context_phase.py:replays_conversation`,
beside the other registry questions the invoker asks about a tool key.

## System text lives in `instructions`, on every path

`src/domains/agentverse/llm/request.py`

`LlmRequest` has two fields and one convention: `instructions` carries the
system text, `input` carries the conversation. **No caller puts a
`{"role": "system"}` entry in `input`.** `instructions` is required with no
default precisely so a new caller has to decide where its system text goes
rather than inheriting a second convention by omission; a caller with none to
send passes `""`, which the client omits rather than sending as an empty field.

Two kinds of caller reach this:

- `request.build()` composes the conversational path - persona, then the
  system-role entries in the delta's `messages` - and replays history.
- The phase tools (`analyze`, `evaluate`, `refine`, `cta`) and `vision_mixin`
  construct `LlmRequest` directly with a single user turn. They opt out of
  persona, delta-merging and history by not calling `build()`. There is no
  shared helper for this: the construction is one legal line, and a profile
  abstraction that ignores most of its arguments would be worse than the line.

`rag_tool` is the **one exempt caller**: it calls `self.llm.get_response_async`
directly, because it needs a per-request `model` and `LlmRequest` has no such
field yet. It sends a single user turn and follows the `instructions` convention
anyway. Folding it onto `llm_chat_async` is blocked on `model` reaching
`LlmRequest`.

## The single outbound sanitize gate

`src/domains/agentverse/agents/mixins/utils/LLMCallsMixin/chat_mixin.py`

`ChatLLMMixin.llm_chat_async` is the **only** door to the chat LLM backend. Right
before the call it runs `_sanitize_messages`, which reduces every dict message to
`{"role", "content"}` plus `name` when present, dropping `meta` and any other
keys.

The gate exists for the **conversation-replay path**: `build()` splices in
history entries that genuinely carry `meta` (the raw @tool command, kept for
audit). `Conversation.as_messages()` already strips it, so the central splice
does not depend on the gate - but it is the guarantee that holds regardless of
which of the two constructs the message list, and the direct-construction
callers pass a single freshly-built user turn on which it is a no-op.

**Invariant to preserve:** any new path that sends messages to the chat backend
should go through `llm_chat_async` (or replicate `_sanitize_messages`).
`rag_tool` is the sole exception, and its message is a freshly constructed dict
with no bookkeeping keys.

## Config knobs

`settings.cache` (`base/config/cache_settings.py`):

| Setting | Default | Meaning |
| --- | --- | --- |
| `redis_history_window` | 20 | **Read depth, not a retention cap.** Applied once, as `Conversation.load`'s fetch limit, so it bounds the Redis read and caps every consumer of the turn's snapshot: the replay spliced into the prompt and the brief a bare `@tool` resolves against. Nothing below re-trims. |
| `redis_history_max_items` | 500 | Retention: the `LTRIM` cap on the stored list. |
| `redis_history_ttl` | 604800 | Retention: the key's TTL, refreshed on every write. |
| `redis_history_key_version` | 1 | Key namespace version. |

There are no per-tool windows. Four of them used to exist (chat 24, ideation 10,
writer 6, auditor 4), disagreed with each other and with their own config
defaults, and were all capped by a hardcoded `limit=50` on load. If a specific
tool genuinely needs a different depth, add **one** documented override with the
reason attached - not four magic numbers back.

Nothing counts tokens, and nothing summarises or compacts. `as_messages()`
returns a rendered list, so a compactor can slot in behind it later without an
interface change.

## Gotchas / invariants

- **Read-before / write-after**: the user+assistant entries for turn N first
  appear in history at turn N+1. `Conversation`'s snapshot enforces this within
  a turn; do not add a second door into the stored list.
- **One `Conversation` per turn.** A long-lived instance re-scoped per session
  is a leak waiting to happen; per-turn construction also scopes the snapshot
  invariant to a single object lifetime.
- **Failures degrade, they do not fail the turn**: see
  [Degraded turns](#degraded-turns).
- **Proxy/external tools are covered**: MCP proxy tools register with
  `route=invoker` when their `execution_mode` is `context_only`/`invoker`/`llm`
  (`registries/mcp_tool_source.py`), so their `provide_context` runs in
  `resolve_tool_context` exactly like local context tools and their
  `user_instruction` flows through the same `rendered_user` path. Proxy tools that
  register as `pure` construct no prompt and need no handling.
- **Multimodal prompts persist as text, not images**: `user_instruction` is
  stored; the tool's images are not. See
  [Why images aren't persisted](#why-images-arent-persisted-in-history).

## Why images aren't persisted in history

A tool's images (e.g. from a proxy tool returning text + images) reach the model
as content parts whose `image_url` is a **self-contained `data:{mime};base64,...`
URL** (`base/integrations/mcp/proxy_contract.py:extract_images`). They are
*not* expiring SAS URLs — that's a separate concern on the file-upload path — so
storing them would be technically faithful. We still **drop them from history on
purpose**, for cost/size reasons, not expiry:

- **Size, unbounded per entry.** History is a capped list (`redis_history_max_items`,
  default 500) with a TTL (`redis_history_ttl`, default 7 days), but the trim is
  by *item count* — there is no per-entry size limit. Base64 images run ~100 KB
  to several MB each. A few image turns can bloat one session to tens of MB.
  Redis is a hot in-memory cache; large values slow down every `LPUSH`/`LRANGE`
  for that session.
- **Repeated, unwanted replay cost.** Every turn whose tools opt into
  `multi_turn_memory` splices the last `redis_history_window` entries into the
  LLM call, and `content` passes through verbatim. A persisted base64 image
  would therefore be **re-sent to the model every subsequent turn** — recurring
  vision-token cost and latency for an image the user usually cared about once.

If multi-turn image recall ever becomes a real requirement, prefer one of:

1. Persist a short **text caption/description** of the image in history (cheap,
   gives the model continuity without the pixels).
2. Store the image **out-of-band** (blob store) keyed by id, keep only the
   **id/reference** in history, and re-hydrate **selectively** (not on every
   turn).

Do **not** put raw base64 image data in the Redis history list.

## Key files

| Concern | File |
| --- | --- |
| The conversation module | `src/domains/agentverse/history/conversation.py` |
| Redis list adapter (private to it) | `src/domains/agentverse/history/store.py` |
| Redis keys / ttl / window | `src/domains/agentverse/cache/helpers.py`, `src/base/config/cache_settings.py` |
| Turn orchestration | `src/domains/agentverse/agents/chat_agent/chat_agent.py` |
| The central splice | `src/domains/agentverse/llm/request.py` (`build`) |
| `multi_turn_memory` predicate | `src/domains/agentverse/llm/helpers/context_phase.py` (`replays_conversation`) |
| Expanded-prompt source | `src/domains/agentverse/llm/invoker.py` |
| The tool context type | `src/domains/agentverse/llm/helpers/datatypes.py` (`ToolContext`) |
| Proxy tool + contract | `src/base/integrations/mcp/proxy_tool.py`, `proxy_contract.py` |
| Proxy route registration | `src/domains/agentverse/registries/mcp_tool_source.py` |
| Envelope re-attach | `src/domains/agentverse/tools/tool_manager_v1/execution/invoker_path.py` |
| ctx bubble-up | `src/domains/agentverse/agents/orchestrator/built_in_phases/h_task.py` |
| Outbound sanitize gate | `src/domains/agentverse/agents/mixins/utils/LLMCallsMixin/chat_mixin.py` |
| Tests | `src/domains/agentverse/tests/history/test_conversation.py`, `tests/llm/test_request_build.py`, `tests/agents/test_history_constructed_prompts.py` |
