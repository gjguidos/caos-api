# Genesis V2 conversational reply

The client requests `chat.message` with `response_mode=llm`; omitted mode preserves deterministic context. The dispatcher requests fixed `chat.response` through the agent-scoped CPBX client. Trusted bootstrap binds `CognitiveResponseV2.handle`; explicit UTU `chat.response` and existing conversation/context grants are required. The Adam-only local policy adds this signal with no delegation lines.

The adapter assembles the unchanged immutable cognitive contracts from the CPBX permit, verified session, persisted V2 chat definition, registered general role/personality/rulepacks and bounded authorized history. Deterministic contributions never enter assistant history. Required role tools and unsupported types fail explicitly. No V1 runtime is constructed.

Model resources use the existing running ClientFactory and async repository. Exact persisted model keys resolve directly. The historical trusted Genesis seed's provider-only label uses the runtime catalog's configured default key; there is no guessed model or provider fallback. Credentials stay in existing clients. Responses report the selected catalog key and provider/model usage metadata.

The existing durable turn fence prevents concurrent or repeated inference on retries; user/reply transcript rows and replay envelope commit together via Redis Lua. Failed/cancelled turns commit no transcript batch. A crashed in-flight turn remains fenced for trusted recovery; this is not exactly-once provider execution across process failure. Revocation and cancellation use existing UTU watchers and CPBX gates; post-call and pre-commit checks fail closed. Already submitted Redis commits drain atomically, as for deterministic turns.

Provider/model calls have a 60-second turn timeout. No tools, teams, moderators or autonomous loop are introduced. Local client endpoints are GENESIS_COMMUNICATION_URL and GENESIS_WEBSOCKET_URL; both are nonsecret loopback addresses in .env.local.

## Local verification (2026-10-26)

Actual approved Adam browser execution succeeded with runtime key `openai-gpt-6-astra`, provider `openai`, model `gpt-6-astra`. A follow-up recalled Orion from the authorized transcript. No credential or policy blocker remains.

Focused backend: 256 passed; focused frontend: 64 passed; TypeScript and scoped lint/format checks passed. Real browser E2E: 3 passed using installed Chrome (`CAOS_E2E_CHANNEL=chrome`). The absent default Playwright browser cache did not require installation.

An expanded run separately found the pre-existing `ChatToolV2._run` envelope test failure: the context-only tool inherits BaseTool's required socket argument/unimplemented method. That unrelated legacy interface was left unchanged; actual CPBX deterministic context tests pass.
