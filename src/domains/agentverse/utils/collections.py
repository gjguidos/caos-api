"""
Collection utility functions for agentverse.

Provides common operations on lists, sets, and other collections.
"""

from typing import TypeVar

T = TypeVar("T")


def dedup(seq: list[T]) -> list[T]:
    """
    Deduplicate a sequence while preserving order and filtering out falsy values.

    Args:
        seq: The sequence to deduplicate

    Returns:
        A list with duplicates removed, maintaining original order, with falsy values filtered out

    Examples:
        >>> dedup([1, 2, 2, 3, None, 3, 4])
        [1, 2, 3, 4]
        >>> dedup(['a', 'b', 'a', '', 'c'])
        ['a', 'b', 'c']
    """
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out
