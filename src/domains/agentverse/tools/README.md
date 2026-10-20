# Tool UI Component Registry

This document describes how to declare runtime UI for agentverse tools using the
component registry.

## Table of contents

- [Tool UI Component Registry](#tool-ui-component-registry)
  - [Table of contents](#table-of-contents)
  - [Overview](#overview)
  - [ui.config](#uiconfig)
  - [ui.components - component schema](#uicomponents---component-schema)
  - [Types \& widgets](#types--widgets)
  - [render\_paths reference](#render_paths-reference)
    - [Available paths](#available-paths)
    - [Properties panel section naming](#properties-panel-section-naming)
  - [flag\_config](#flag_config)
    - [Special flag values](#special-flag-values)
  - [group \& groupLabel](#group--grouplabel)
  - [Props reference by widget](#props-reference-by-widget)
    - [PillGroup (`type: "select"`)](#pillgroup-type-select)
    - [ChatInput (`type: "free_text"`)](#chatinput-type-free_text)
    - [TextField (`type: "text"`)](#textfield-type-text)
    - [CheckboxGroupField (`type: "multiselect"`)](#checkboxgroupfield-type-multiselect)
    - [NumberField (`type: "number"`)](#numberfield-type-number)
    - [SelectField (`type: "select"`)](#selectfield-type-select)
    - [ToggleField (`type: "boolean"`)](#togglefield-type-boolean)
  - [Full example - writer\_tool](#full-example---writer_tool)

---

## Overview

Every tool's `metadata["ui"]` declares its runtime UI through two keys:

```python
"ui": {
    "config": { ... },      # panel-level display config
    "components": [ ... ],  # one entry per runtime field
}
```

`components` is the single source of truth consumed by the frontend.

---

## ui.config

```python
"config": {
    "group_label_map": {
        "<group_key>": "<display label shown in the panel header>",
        ...
    }
}
```

`group_label_map` maps the internal `group` string used in each component to a
human-readable section heading rendered in the properties panel.

**Rules:**
- Components with `group: None` don't need a mapping.
- Provide an empty dict `{}` if the tool has no properties panel groups.

**Example:**
```python
"config": {
    "group_label_map": {
        "Persona": "Voice & audience",
        "Length":  "Output length",
    }
}
```

---

## ui.components - component schema

Each entry in `components` is a dict with the following shape:

```python
{
    "id":           str,          # unique field identifier; matches the CLI flag name
    "type":         str,          # logical data type (see Types & widgets)
    "widget":       str,          # FE widget component name (see Types & widgets)
    "group":        str | None,   # panel group key; None = not in properties panel
    "flag_config":  { ... },      # how this field maps to a CLI flag
    "render_paths": [str, ...],   # where in the UI this component is mounted
    "slot_order":   int | None,   # position within a render_path slot
    "props":        { ... },      # widget-specific display props (labels, options, …)
}
```
---

## Types & widgets

The following type/widget combinations are supported.

| `type` | `widget` | Description |
|---|---|---|---|
| `select` | `PillGroup` | Single-choice pill buttons |
| `select` | `SelectField` | Single-choice dropdown |
| `free_text` | `ChatInput` | Replaces the main chat input box |
| `text` | `TextField` | Single-line text input |
| `multiselect` | `CheckboxGroupField` | Multi-choice checkboxes |
| `number` | `NumberField` | Numeric stepper / input |

---

## render_paths reference

`render_paths` is a list of dot-separated slot identifiers. In practice every tool uses
exactly one path per component.

### Available paths

| Path | Location | Notes |
|---|---|---|
| `workspace.conversationArea.inputSection.toolMode` | Chat input bar - mode pill strip | Reserved for the `mode` component. `slot_order` controls left-to-right position. |
| `chatInput` | Chat input box | Use for the primary `free_text` / `text` component. `slot_order: None`. |
| `propertiesPanel.<section>` | Right-hand properties panel | `<section>` is a camelCase slug you define. Components in the same section are ordered by `slot_order`. |

### Properties panel section naming

Use camelCase slugs that describe the logical group. Examples from current tools:

| Section slug | Display label (via `group_label_map`) |
|---|---|
| `persona` | "Voice & audience" |
| `length` | "Output length" |
| `behaviour` | "Ideation mode" |
| `audienceTone` | "Audience & tone" |
| `platforms` | "Platforms & frameworks" |
| `constraints` | "Constraints" |
| `auditSettings` | "Audit settings" |
| `rulePacks` | "Rule packs" |
| `advanced` | "Advanced" |

---

## flag_config

Controls how the component value is serialised into a CLI-style flag string when the
tool is invoked.

```python
"flag_config": {
    "flag":     str,   # the flag string passed to the tool's parse_flags()
    "required": bool,  # if True, the FE must provide a value before submission
}
```

### Special flag values

| `flag` value | Meaning |
|---|---|
| `"@"` | Tool invocation (e.g. `@writer`, `@auditor`). Maps to the canonical `@tool_key` syntax in the chat input. Always on the `mode` component. |
| `"--<name>"` | Standard long flag, e.g. `"--tone-of-voice"`. Must match a key in `parse_flags()`. |

---

## group & groupLabel

`group` is the internal group key. It must match a key in `group_label_map` when the
component lives in the properties panel.

```python
{"id": "style", "group": "Persona", ...}
```
---

## Props reference by widget

### PillGroup (`type: "select"`)

```python
"props": {
    "label":   str,       # optional section label
    "options": [
        {
            "value":         str,   # serialised value sent to parse_flags
            "label":         str,   # display text
            "requiresInput": bool,  # (mode only) whether the chat input is active
        },
    ],
}
```

### ChatInput (`type: "free_text"`)

```python
"props": {
    "placeholder": str,   # hint text shown in the chat input
}
```

### TextField (`type: "text"`)

```python
"props": {
    "label":       str,   # field label
    "placeholder": str,   # hint text (optional)
}
```

### CheckboxGroupField (`type: "multiselect"`)

```python
"props": {
    "label":   str,
    "options": [
        {"value": str, "label": str},
    ],
}
```

### NumberField (`type: "number"`)

```python
"props": {
    "label": str,
    "min":   int | float,
    "max":   int | float,
    "step":  int | float,
}
```

### SelectField (`type: "select"`)

Same shape as `PillGroup`. Rendered as a dropdown instead of pills.

```python
"props": {
    "label":   str,
    "options": [
        {"value": str, "label": str},
    ],
}
```

### ToggleField (`type: "boolean"`)

```python
"props": {
    "label": str,
}
```

---

## Full example - writer_tool

```python
"ui": {
    "config": {
        "group_label_map": {
            "Persona": "Voice & audience",
            "Length":  "Output length",
        }
    },
    "components": [
        # --- Tool mode (chat input bar) ---
        {
            "id": "mode", "type": "select", "widget": "PillGroup",
            "group": None,
            "flag_config": {"flag": "--mode", "required": True},
            "render_paths": ["workspace.conversationArea.inputSection.toolMode"],
            "slot_order": 0,
            "props": {
                "options": [
                    {"value": "write",     "label": "Write",     "requiresInput": True},
                    {"value": "rewrite",   "label": "Rewrite",   "requiresInput": False},
                    {"value": "expand",    "label": "Expand",    "requiresInput": False},
                    {"value": "summarize", "label": "Summarize", "requiresInput": False},
                ],
            },
        },
        # --- Primary text input (replaces chat box) ---
        {
            "id": "text", "type": "free_text", "widget": "ChatInput",
            "group": None,
            "flag_config": {"flag": "--text", "required": True},
            "render_paths": ["chatInput"],
            "slot_order": None,
            "props": {"placeholder": "Enter your text here..."},
        },
        # --- Properties panel: Persona ---
        {
            "id": "role", "type": "text", "widget": "TextField",
            "group": "Persona", "groupLabel": "Voice & Audience",
            "flag_config": {"flag": "--role", "required": False},
            "render_paths": ["propertiesPanel.persona"],
            "slot_order": 0,
            "props": {"label": "Role", "placeholder": "e.g., Marketing Manager"},
        },
        # ... style, mood, audience, tone_of_voice ...
        # --- Properties panel: Length ---
        {
            "id": "max_char_length", "type": "number", "widget": "NumberField",
            "group": "Length",
            "flag_config": {"flag": "--max-chars", "required": False},
            "render_paths": ["propertiesPanel.length"],
            "slot_order": 0,
            "props": {"label": "Max Character Length", "min": 0, "max": 5000, "step": 10},
        },
    ],
}
```

---
