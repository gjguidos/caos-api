import importlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel as _BM


def get_cfg_value(cfg, name, default=None):
    if cfg is None:
        return default
    try:
        return getattr(cfg, name)
    except Exception:
        pass
    try:
        return cfg.get(name, default)
    except Exception:
        pass
    try:
        return vars(cfg).get(name, default)
    except Exception:
        return default


def maybe_coerce_config(schema_ref: str | None, cfg: dict):
    if not schema_ref:
        return SimpleNamespace(**cfg) if cfg else SimpleNamespace()
    try:
        if "." in schema_ref:
            module_path, class_name = schema_ref.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
        else:
            cls = globals().get(schema_ref)
        if cls and issubclass(cls, _BM):
            return cls(**cfg)
    except Exception:
        pass
    return dict(cfg)


def apply_config_defaults(
    cfg_obj: Any,
    *,
    param_specs: dict[str, dict[str, Any]],
    params: set[str],
    meta: dict[str, Any] | None = None,
    get_cfg_value: Callable[[Any, str, Any], Any],
    coerce_val: Callable[[Any, dict[str, Any]], Any],
) -> dict[str, Any]:
    """
    Fill params from config when names match, plus optional mapped names.

    Mirrors ToolManager._apply_config_defaults behavior:
      - Direct param name matches (get_cfg_value(cfg_obj, p, None))
      - Optional mapping via meta["config_param_map"] (e.g. default_timezone -> timezone)
      - Coerces mapped values through coerce_val using param_specs[param_name]
    """
    out: dict[str, Any] = {}

    # direct name matches
    for p in params:
        v = get_cfg_value(cfg_obj, p, None)
        if v is not None:
            out[p] = v

    # optional mapping (e.g., default_timezone -> timezone)
    mapping = (meta or {}).get("config_param_map") or {}
    for cfg_name, param_name in mapping.items():
        if (param_name in params) and (param_name not in out):
            v = get_cfg_value(cfg_obj, cfg_name, None)
            if v is not None:
                out[param_name] = coerce_val(v, param_specs.get(param_name, {}))

    return out
