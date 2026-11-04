"""RUNTIME-CONFIG-0.1: explicit hot-reload control for the API server."""

import pytest

from src.base.system import serve


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, False),
        ({"CAOS_API_RELOAD": ""}, False),
        ({"CAOS_API_RELOAD": "false"}, False),
        ({"CAOS_API_RELOAD": "FALSE"}, False),
        ({"CAOS_API_RELOAD": "true"}, True),
        ({"CAOS_API_RELOAD": " True "}, True),
        # ENVIRONMENT never enables reload on its own.
        ({"ENVIRONMENT": "development"}, False),
        ({"ENVIRONMENT": "development", "CAOS_API_RELOAD": "false"}, False),
    ],
)
def test_reload_is_explicit_and_disabled_by_default(environ, expected):
    assert serve.reload_enabled(environ) is expected


@pytest.mark.parametrize("value", ["1", "0", "yes", "on", "no", "development", "ture"])
def test_unsupported_reload_values_refuse_to_start(value):
    with pytest.raises(ValueError, match="CAOS_API_RELOAD"):
        serve.reload_enabled({"CAOS_API_RELOAD": value})


@pytest.mark.parametrize(("value", "reload"), [(None, False), ("true", True)])
def test_main_starts_the_existing_app_with_the_existing_settings(monkeypatch, value, reload):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setenv("ENVIRONMENT", "development")
    if value is None:
        monkeypatch.delenv("CAOS_API_RELOAD", raising=False)
    else:
        monkeypatch.setenv("CAOS_API_RELOAD", value)
    serve.main()
    assert calls == [
        (
            ("src.main:app",),
            {"host": "0.0.0.0", "port": 8001, "reload": reload, "log_config": None},
        )
    ]


def test_an_invalid_value_fails_before_uvicorn_starts(monkeypatch):
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("must not start"))
    monkeypatch.setenv("CAOS_API_RELOAD", "maybe")
    with pytest.raises(ValueError):
        serve.main()
