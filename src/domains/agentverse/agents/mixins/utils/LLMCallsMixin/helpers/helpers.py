from __future__ import annotations

import inspect


def filter_kwargs(fn, **kwargs):
    sig = inspect.signature(fn)
    return {k: v for k, v in kwargs.items() if k in sig.parameters and v is not None}


def get_attr_chain(root, chain: str):
    cur = root
    for p in chain.split("."):
        if not hasattr(cur, p):
            return None, None
        cur = getattr(cur, p)
    return (cur, chain) if callable(cur) else (None, None)
