"""Declarative Agent GUI composition, contract version 1. Presentation, never authority.

An Agent owns a flat list of GUI components. Each names a supported component
``type``, a unique ``key``, whether it is the ``default`` (primary) interface and a
strictly validated ``config``. Version 1 supports ``chat`` and ``form`` only: no
tabs, dashboards, timelines, nesting or arbitrary trees.

Ownership of content stays where it already lives:
- ``chat`` presents the existing V2 conversation interface; UTU/CPBX grants still
  decide whether the agent can answer.
- ``form`` never declares fields. It references either a Workflow the Agent itself
  declares (whose ``WorkflowSpec.inputs``/``ui`` own the form) or an existing
  versioned input contract. Rendering a form submits nothing and grants nothing.

The agent's ``type`` keeps its functional meaning; components only describe how a
client may present the agent. Semantic checks (registry, Workflow version, agent
compatibility) are ``data.validation``'s; this module validates structure.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

GUI_CONTRACT_VERSION: Final = 1
AGENT_CREATION_DRAFT_CONTRACT: Final = "caos.agent_creation_draft"
ComponentKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
WorkflowKey = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^\S+$")]
Version = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^\S+$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatConfig(_Strict):
    """The existing V2 conversation interface; nothing is configurable in v1."""


class WorkflowFormSource(_Strict):
    """Present a Workflow's declared inputs; the Workflow stays independently executable."""

    kind: Literal["workflow"]
    key: WorkflowKey
    version: Version


class ContractFormSource(_Strict):
    """Present an existing versioned input contract (currently Enki's creation draft)."""

    kind: Literal["contract"]
    contract: Literal["caos.agent_creation_draft"]
    schema_version: Literal[1]


class FormConfig(_Strict):
    source: Annotated[WorkflowFormSource | ContractFormSource, Field(discriminator="kind")]


class _Component(_Strict):
    version: Literal[1] = GUI_CONTRACT_VERSION
    key: ComponentKey
    default: bool = False


class ChatComponent(_Component):
    type: Literal["chat"]
    config: ChatConfig = Field(default_factory=ChatConfig)


class FormComponent(_Component):
    type: Literal["form"]
    config: FormConfig


GuiComponent = Annotated[ChatComponent | FormComponent, Field(discriminator="type")]
GUI_COMPONENT_TYPES = ("chat", "form")
MAX_COMPONENTS = 8


def check_components(components: list[Any]) -> list[Any]:
    """Structural rules for a flat list: bounded, unique keys, exactly one default."""
    if len(components) > MAX_COMPONENTS:
        raise ValueError(f"An Agent declares at most {MAX_COMPONENTS} GUI components")
    keys = [component.key for component in components]
    if len(set(keys)) != len(keys):
        raise ValueError("GUI component keys must be unique")
    if components and sum(1 for component in components if component.default) != 1:
        raise ValueError("Exactly one GUI component must be the default")
    return components


class GuiComponents(BaseModel):
    """Validates a component list on its own (also used for persisted definitions)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    components: list[GuiComponent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _structure(self) -> GuiComponents:
        check_components(self.components)
        return self


def default_gui_components(
    *, agent_type: str | None, role_name: str | None, runtime_version: str | None
) -> list[ChatComponent | FormComponent]:
    """The reviewed default presentation for V2 agents; V1 keeps its own workspaces.

    Mirrors ``supports_v2_conversation``: chat agents converse; the approved
    form/enki composition is form-first with an independent conversation.
    """
    if runtime_version != "v2":
        return []
    if agent_type == "chat":
        return [ChatComponent(type="chat", key="conversation", default=True)]
    if (agent_type, role_name) == ("form", "enki"):
        return [
            FormComponent(
                type="form",
                key="agent_configuration",
                default=True,
                config=FormConfig(
                    source=ContractFormSource(
                        kind="contract",
                        contract=AGENT_CREATION_DRAFT_CONTRACT,
                        schema_version=1,
                    )
                ),
            ),
            ChatComponent(type="chat", key="conversation"),
        ]
    return []


def effective_gui_components(definition: dict[str, Any]) -> list[ChatComponent | FormComponent]:
    """Persisted components, or the reviewed default for definitions created before v1."""
    persisted = definition.get("gui_components") or []
    if persisted:
        return list(GuiComponents.model_validate({"components": persisted}).components)
    return default_gui_components(
        agent_type=definition.get("type"),
        role_name=definition.get("role_name"),
        runtime_version=definition.get("runtime_version"),
    )
