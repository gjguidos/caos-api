from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from src.domains.agentverse.tools.web_crawler_tool.types import (
    ContentBlockItem,
    ContentBlocksReport,
)

BLOCK_SELECTOR_TAGS = [
    "main",
    "section",
    "article",
    "aside",
]


CTA_KEYWORDS = {
    "apply",
    "join",
    "start",
    "get started",
    "learn more",
    "read more",
    "explore",
    "contact",
    "subscribe",
    "register",
    "book",
    "shop",
    "buy",
    "download",
}


CARD_CLASS_HINTS = {
    "card",
    "tile",
    "grid",
    "teaser",
    "promo",
    "item",
    "product",
    "article",
}


HERO_CLASS_HINTS = {
    "hero",
    "masthead",
    "banner",
    "intro",
    "jumbotron",
}


QUOTE_TAGS = {"blockquote", "q"}


def build_content_blocks_report(
    *,
    html: str,
    page_url: str,
    max_blocks: int = 80,
    max_text_chars_per_block: int = 1200,
    min_text_len: int = 80,
    clean_soup: BeautifulSoup | None = None,
) -> ContentBlocksReport:
    soup = clean_soup

    if soup is None:
        soup = BeautifulSoup(html or "", "html.parser")

        for element in soup(["script", "style", "noscript", "svg"]):
            element.decompose()

    candidates = _find_block_candidates(soup)

    blocks: list[ContentBlockItem] = []

    for node in candidates:
        if not isinstance(node, Tag):
            continue

        block = _build_block(
            node=node,
            page_url=page_url,
            index=len(blocks) + 1,
            max_text_chars=max_text_chars_per_block,
        )

        if _should_keep_block(block, min_text_len=min_text_len):
            blocks.append(block)

        if len(blocks) >= max_blocks:
            break

    return ContentBlocksReport(
        block_count=len(blocks),
        total_text_len=sum(block.text_len for block in blocks),
        primary_evidence_blocks=sum(
            1 for block in blocks
            if block.evidence_role == "primary_text_evidence"
        ),
        supporting_evidence_blocks=sum(
            1 for block in blocks
            if block.evidence_role == "supporting_text_evidence"
        ),
        low_priority_blocks=sum(
            1 for block in blocks
            if block.evidence_role == "low_priority_text"
        ),
        text_heavy_blocks=sum(
            1 for block in blocks
            if block.text_len >= 500
        ),
        combined_text="\n\n".join(block.text for block in blocks if block.text),
        blocks=blocks,
    )


def _find_block_candidates(soup: BeautifulSoup) -> list[Tag]:
    body = soup.body or soup

    candidates: list[Tag] = []

    for tag_name in BLOCK_SELECTOR_TAGS:
        candidates.extend(
            node for node in body.find_all(tag_name) if isinstance(node, Tag)
        )

    # Also include divs that look structurally meaningful.
    for div in body.find_all("div"):
        if not isinstance(div, Tag):
            continue

        classes = _classes(div)
        role = div.get("role")
        text = _clean_text(div.get_text(" "))

        if role in {"main", "region", "banner", "navigation", "contentinfo"}:
            candidates.append(div)
            continue

        if any(cls_hint in " ".join(classes).lower() for cls_hint in HERO_CLASS_HINTS):
            candidates.append(div)
            continue

        if any(cls_hint in " ".join(classes).lower() for cls_hint in CARD_CLASS_HINTS):
            candidates.append(div)
            continue

        # Avoid adding every layout div. Keep divs that carry visible content.
        if len(text) >= 180 and (div.find(["h1", "h2", "h3"]) or len(div.find_all("a")) > 0):
            candidates.append(div)

    return _dedupe_nodes_preserve_order(candidates)


def _build_block(
    *,
    node: Tag,
    page_url: str,
    index: int,
    max_text_chars: int,
) -> ContentBlockItem:
    heading_node = node.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    heading = _clean_text(heading_node.get_text(" ")) if isinstance(heading_node, Tag) else ""
    heading_level = heading_node.name if isinstance(heading_node, Tag) else None

    text = _clean_text(node.get_text(" "))

    if len(text) > max_text_chars:
        text = text[:max_text_chars].rstrip() + "..."

    classes = _classes(node)

    signals = _infer_signals(
        node=node,
        text=text,
        heading=heading,
        classes=classes,
    )

    block_type = _infer_block_type(
        node=node,
        text=text,
        signals=signals,
    )

    evidence_score = _score_content_block(
        block_type=block_type,
        tag=node.name or "",
        text_len=len(text),
        heading=heading,
        signals=signals,
    )

    return ContentBlockItem(
        index=index,
        page_url=page_url,
        block_type=block_type,
        heading=heading,
        heading_level=heading_level,
        text=text,
        text_len=len(text),
        evidence_score=evidence_score,
        evidence_role=_classify_evidence_role(
            block_type=block_type,
            tag=node.name or "",
            evidence_score=evidence_score,
        ),
        source={
            "tag": node.name or "",
            "id": node.get("id"),
            "classes": classes[:12],
            "role": node.get("role"),
            "signals": signals,
        },
    )


def _classify_evidence_role(
    *,
    block_type: str,
    tag: str,
    evidence_score: float,
) -> str:
    if block_type == "navigation" or tag == "nav":
        return "navigation_text"

    if block_type == "footer" or tag == "footer":
        return "footer_text"

    if evidence_score >= 0.70:
        return "primary_text_evidence"

    if evidence_score >= 0.40:
        return "supporting_text_evidence"

    return "low_priority_text"


def _score_content_block(
    *,
    block_type: str,
    tag: str,
    text_len: int,
    heading: str,
    signals: list[str],
) -> float:
    score = 0.0
    signal_set = set(signals)

    if block_type in {"main", "content", "article", "section", "quote"}:
        score += 0.30

    if tag in {"main", "article", "section"}:
        score += 0.25

    if heading:
        score += 0.10

    if text_len >= 1000:
        score += 0.25
    elif text_len >= 500:
        score += 0.18
    elif text_len >= 200:
        score += 0.10

    if "text_heavy" in signal_set:
        score += 0.15

    if block_type in {"navigation", "footer", "media"}:
        score -= 0.45

    if tag in {"nav", "footer", "header"}:
        score -= 0.25

    if "cta_body_text" in signal_set and text_len < 250:
        score -= 0.10

    return round(max(0.0, min(1.0, score)), 4)


def _infer_signals(
    *,
    node: Tag,
    text: str,
    heading: str,
    classes: list[str],
) -> list[str]:
    signals: list[str] = []

    tag = node.name or ""
    class_blob = " ".join(classes).lower()
    text_lower = text.lower()
    heading_lower = heading.lower()

    if tag in {"main", "section", "article", "aside"}:
        signals.append(f"semantic_tag:{tag}")

    if tag in {"header", "footer", "nav"}:
        signals.append(f"structural_tag:{tag}")

    if heading:
        signals.append("has_heading")

    if len(text) >= 500:
        signals.append("text_heavy")

    if any(keyword in text_lower for keyword in CTA_KEYWORDS):
        signals.append("cta_body_text")

    if any(hint in class_blob for hint in HERO_CLASS_HINTS):
        signals.append("hero_class_hint")

    if any(hint in class_blob for hint in CARD_CLASS_HINTS):
        signals.append("card_class_hint")

    if tag in QUOTE_TAGS or "quote" in class_blob or "testimonial" in class_blob:
        signals.append("quote_hint")

    if heading_lower.startswith("welcome") or heading_lower.startswith("introduc"):
        signals.append("intro_heading_hint")

    return _dedupe(signals)


def _infer_block_type(
    *,
    node: Tag,
    text: str,
    signals: list[str],
) -> str:
    tag = node.name or ""

    if tag == "nav" or node.get("role") == "navigation":
        return "navigation"

    if tag == "footer":
        return "footer"

    if tag == "header":
        return "header"

    if tag == "main":
        return "main"

    if tag == "article":
        return "article"

    if tag == "section":
        return "section"

    if "quote_hint" in signals:
        return "quote"

    if "cta_body_text" in signals and len(text) < 300:
        return "cta"

    return "content"


def _should_keep_block(
    block: ContentBlockItem,
    *,
    min_text_len: int = 80,
) -> bool:
    if block.block_type in {"navigation", "footer", "media"}:
        return False

    if block.block_type == "header" and block.evidence_score < 0.5:
        return False

    if block.text_len < min_text_len:
        return False

    if block.evidence_score >= 0.35:
        return True

    if block.heading and block.text_len >= 120:
        return True

    if block.text_len >= 300:
        return True

    return False


def _classes(node: Tag) -> list[str]:
    classes = node.get("class") or []

    if isinstance(classes, list):
        return [str(cls) for cls in classes]

    if isinstance(classes, str):
        return classes.split()

    return []


def _clean_text(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)

    return result


def _dedupe_nodes_preserve_order(nodes: list[Tag]) -> list[Tag]:
    seen: set[int] = set()
    result: list[Tag] = []

    for node in nodes:
        node_id = id(node)
        if node_id in seen:
            continue

        seen.add(node_id)
        result.append(node)

    return result