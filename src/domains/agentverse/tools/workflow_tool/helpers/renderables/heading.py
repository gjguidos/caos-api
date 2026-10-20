def is_heading_only(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("### ") and "\n" not in stripped
