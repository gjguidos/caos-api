# AgentVerse Workflow Run Contract

This document describes the stable start/resume/get contract for the `workflow_tool`.
The canvas frontend reducer builds against this contract.

## Overview

The `WorkflowTool` exposes three operations via the `op` parameter:

| `op` | Purpose |
| --- | --- |
| `start` | Start a new workflow run with optional inputs |
| `resume` | Resume a paused run by continuing or cancelling |
| `get` | Fetch current status, step status, and sanitized outputs |

## Delivery Channels

### DSL

Send a message over the agent's `comm` WebSocket using the `@workflow` command:

```text
@workflow --op=start --inputs=<urlencoded-json>
@workflow --op=resume --run_id=<id> --pause_payload=<urlencoded-json>
@workflow --op=get --run_id=<id>
```

`--inputs=` and `--pause_payload=` accept URL-encoded JSON objects.

### Structured `tool_kwargs`

Pass parameters directly in the `data.tool_kwargs` object of the WebSocket message:

```json
{
  "op": "start",
  "workflow_key": "my_workflow_v1",
  "inputs": { "text": "hello" }
}
```

Both channels produce the same run and response.

## Operations

### `op=start`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `op` | `string` | yes | `"start"` |
| `workflow_key` | `string` | no | Registry key for the workflow. Can be omitted when the agent tool config pins `config.workflow_key`. |
| `inputs` | `object` | no | Input values declared in `WorkflowSpec.inputs`. |
| `run_id` | `string` | no | Client-provided run ID; auto-generated when omitted. |

### `op=resume`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `op` | `string` | yes | `"resume"` |
| `run_id` | `string` | yes | ID of the paused run. |
| `pause_payload` | `object` | yes | Resume payload. |

`pause_payload` shape:

```json
{
  "action": "continue",
  "step_id": "<paused-step-id>",
  "pause_id": "<pause-id>",
  "data": {}
}
```

`action` supports `continue` and `cancel`. `step_id` is inferred when omitted.

### `op=get`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `op` | `string` | yes | `"get"` |
| `run_id` | `string` | yes | ID of the run to fetch. |

## Run Snapshot

All three operations return exactly one canonical `workflow.run` snapshot shape:

```jsonc
{
  "kind": "workflow.run",
  "status": "COMPLETED | PAUSED | RUNNING | FAILED | CANCELLED | NOT_FOUND",
  "run_id": "wf_...",
  "workflow_key": "my_workflow_v1",
  "workflow_name": "My Workflow",
  "paused": null,
  "error": null,
  "steps": {
    "write": {
      "status": "COMPLETED",
      "attempts": 1,
      "started_at": "2026-01-01T00:00:00+00:00",
      "completed_at": "2026-01-01T00:00:01+00:00",
      "last_error": null
    }
  },
  "outputs": {
    "write": {
      "ok": true,
      "tool": "writer",
      "text": "Written text",
      "images": [],
      "error": null
    },
    "draw": {
      "ok": true,
      "tool": "draw",
      "text": "Here's the image.",
      "images": [
        { "url": "data:image/png;base64,iVBORw0...", "mime_type": "image/png" }
      ],
      "error": null
    }
  }
}
```

The top-level snapshot intentionally does not include `message`, `text`, `plan`,
`renderables`, `output_keys`, or a duplicate nested `result` object. Workflow topology
comes from the workflow registry metadata, not each run snapshot.

### `NOT_FOUND` status

When `op=get` is called with a `run_id` that does not exist in the store (expired TTL,
wrong ID, etc.), the snapshot is returned with `status: "NOT_FOUND"` instead of a
generic error envelope. `workflow_key`, `workflow_name`, `steps`, and `outputs` are all
empty/null:

```jsonc
{
  "kind": "workflow.run",
  "status": "NOT_FOUND",
  "run_id": "wf_...",
  "workflow_key": null,
  "workflow_name": null,
  "paused": null,
  "error": "Run not found: wf_...",
  "steps": {},
  "outputs": {}
}
```

`outputs` is keyed by workflow `step_id`. Each value is the **display-only** public tool
result — exactly what the chat bubble and the canvas render, and nothing else:

| Field | Type | Description |
| --- | --- | --- |
| `ok` | `boolean` | Whether the step succeeded. |
| `tool` | `string` | Tool key that produced the output. |
| `text` | `string \| null` | Rendered text, or `null` when the step has none. |
| `images` | `array` | Deduped images, each `{ "url", "mime_type" }`. A given image appears once; the redundant `src`/`storage` pairing is dropped. |
| `error` | `string \| null` | Error message when `ok` is false. |

Outputs must not carry raw execution fields (`env`, `raw`, `raw_payload`, `payload`,
`debug`, trace data) or heavy internals (`renderables`, nested `result`, `context`,
`addons`). Those are server-log/debug only and must not cross the wire.

### `paused` Object

When `status == "PAUSED"`, `paused` contains the first active pause:

```jsonc
{
  "pause_id": "run123:review",
  "step_id": "review",
  "prompt": "Review before continuing.",
  "actions": ["continue", "cancel"],
  "workflow_key": "my_workflow_v1",
  "workflow_name": "My Workflow"
}
```

Pauses are a simple approve / reject gate. The frontend renders one button per
entry in `actions` (`continue` = approve, `cancel` = reject) and resumes by sending
that value as `pause_payload.action`.

## Live `workflow.step` Events

The frontend-visible workflow event contract is a step state-change event:

```jsonc
{
  "kind": "workflow.step",
  "run_id": "wf_...",
  "workflow_key": "my_workflow_v1",
  "step_id": "write",
  "status": "RUNNING | PAUSED | COMPLETED | FAILED | CANCELLED",
  "output": { "...": "display-only shape, present only for terminal step statuses" }
}
```

When present, `output` uses the same display-only shape as snapshot `outputs[*]`
(`ok`, `tool`, `text`, `images`, `error`).

Only `workflow.step` events are forwarded by the workflow event adapter. Debug/internal
workflow events are server-log-only.

## Declared Workflow Inputs

A workflow can declare a typed input contract in `WorkflowSpec.inputs`. The frontend uses
this schema to render a typed Run form:

```python
WorkflowSpec(
    key="my_workflow_v1",
    inputs=[
        WorkflowInputField(
            key="text",
            label="Text",
            input="textarea",
            required=True,
            help="Text the workflow will write up.",
        )
    ],
    ...
)
```

The serialized `inputs` list is available in workflow registry metadata.
