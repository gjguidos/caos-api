import os
from pathlib import Path
from typing import Any

# Resolve paths
ROOT_DIR = Path(__file__).parent.parent.parent.parent.parent  # could this be more dynamic?


def parse_comma_separated_list(v: Any | None) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return v
    return [item.strip() for item in v.split(",")]


def resolve_path(env_var: str, default: str) -> str:
    """
    Resolve a path by checking if it's absolute or relative.
    If relative, prepend ROOT_DIR.
    """
    path = Path(os.getenv(env_var, default))
    return str(path if path.is_absolute() else ROOT_DIR / path)
