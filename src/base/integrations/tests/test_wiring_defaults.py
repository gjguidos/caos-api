from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from src.base.integrations.wiring import compute_defaults, refresh_defaults
from src.domains.agentverse.interfaces.api.v1.interface import list_cognitive_module_keys


@pytest.mark.parametrize(
    "modules,expected",
    [
        ({"messaging": {"rabbitmq": object()}}, "rabbitmq"),
        ({"messaging": {}}, ""),
        ({}, ""),
        ({"messaging": {"noop": SimpleNamespace(is_noop=True)}}, ""),
    ],
)
def test_messaging_default_preserves_real_backend_or_empty(modules, expected):
    assert compute_defaults(modules)["messaging"] == expected


@pytest.mark.asyncio
async def test_catalog_defaults_cover_all_registered_categories():
    app = FastAPI()
    app.state.cognitive_modules = {
        "llm": {"openai": object()},
        "messaging": {"rabbitmq": object()},
        "extension": {"custom": object()},
    }
    app.state.cognitive_defaults = compute_defaults(app.state.cognitive_modules)
    request = Request({"type": "http", "app": app})
    result = await list_cognitive_module_keys(request, user=SimpleNamespace(id="owner"))
    assert result["messaging"] == {"options": ["rabbitmq"], "default": "rabbitmq"}
    assert result["extension"] == {"options": ["custom"], "default": "custom"}
    assert set(result) == set(app.state.cognitive_modules)


@pytest.mark.asyncio
async def test_refresh_keeps_valid_explicit_messaging_default():
    app = SimpleNamespace(
        state=SimpleNamespace(
            cognitive_modules={"messaging": {"first": object(), "chosen": object()}},
            cognitive_defaults={"messaging": "chosen"},
        )
    )
    assert (await refresh_defaults(app))["messaging"] == "chosen"
    del app.state.cognitive_modules["messaging"]["chosen"]
    assert (await refresh_defaults(app))["messaging"] == "first"
