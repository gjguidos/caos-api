def strip_quotes(s: str) -> str:
    """
    Remove surrounding quotes without interpreting escape sequences.
    Preserves UTF-8 and literal backslashes. Use json.loads() for JSON escapes.
    """
    if not s:
        return s
    if (s[0] == s[-1]) and s[0] in ("'", '"'):
        return s[1:-1]
    return s
