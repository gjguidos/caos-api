from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from src.domains.agentverse.tools.web_crawler_tool.types import (
    ContentBlockLink,
    ContentBlockMedia,
    DetectedHTMLBlockItem,
    DetectedHTMLBlocksReport,
)

BLOCK_SELECTOR_TAGS = [
    "main",
    "header",
    "footer",
    "nav",
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


def build_detected_html_blocks_report(
    *,
    html: str,
    page_url: str,
    max_blocks: int = 80,
    max_text_chars_per_block: int = 1200,
    clean_soup: BeautifulSoup | None = None,
) -> DetectedHTMLBlocksReport:
    if clean_soup is None:
        clean_soup = BeautifulSoup(html or "", "html.parser")

        for element in clean_soup(
            ["script", "style", "noscript", "svg"]
        ):
            element.decompose()

    candidates = _find_block_candidates(clean_soup)

    blocks: list[DetectedHTMLBlockItem] = []

    for node in candidates:
        if not isinstance(node, Tag):
            continue

        block = _build_block(
            node=node,
            page_url=page_url,
            index=len(blocks) + 1,
            max_text_chars=max_text_chars_per_block,
        )

        if _should_keep_block(block):
            blocks.append(block)

        if len(blocks) >= max_blocks:
            break

    return DetectedHTMLBlocksReport(
        block_count=len(blocks),
        hero_blocks=sum(1 for block in blocks if block.block_type == "hero"),
        navigation_blocks=sum(1 for block in blocks if block.block_type == "navigation"),
        cta_blocks=sum(1 for block in blocks if block.block_type == "cta"),
        card_blocks=sum(1 for block in blocks if block.block_type == "card"),
        media_blocks=sum(1 for block in blocks if block.block_type == "media"),
        quote_blocks=sum(1 for block in blocks if block.block_type == "quote"),
        footer_blocks=sum(1 for block in blocks if block.block_type == "footer"),
        article_blocks=sum(1 for block in blocks if block.block_type == "article"),
        text_heavy_blocks=sum(1 for block in blocks if "text_heavy" in block.signals),
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
) -> DetectedHTMLBlockItem:
    heading_node = node.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    heading = _clean_text(heading_node.get_text(" ")) if isinstance(heading_node, Tag) else ""
    heading_level = heading_node.name if isinstance(heading_node, Tag) else None

    text = _clean_text(node.get_text(" "))
    if len(text) > max_text_chars:
        text = text[:max_text_chars].rstrip() + "..."

    links = _extract_block_links(node=node, page_url=page_url)
    media = _extract_block_media(node=node, page_url=page_url)

    classes = _classes(node)
    signals = _infer_signals(
        node=node,
        text=text,
        heading=heading,
        links=links,
        media=media,
        classes=classes,
    )

    block_type = _infer_block_type(
        node=node,
        text=text,
        heading=heading,
        links=links,
        media=media,
        classes=classes,
        signals=signals,
    )

    return DetectedHTMLBlockItem(
        index=index,
        block_type=block_type,
        tag=node.name or "",
        id=node.get("id"),
        classes=classes,
        role=node.get("role"),
        page_url=page_url,
        heading=heading,
        heading_level=heading_level,
        text_preview=text,
        text_len=len(text),
        link_count=len(links),
        media_count=len(media),
        links=links[:20],
        media=media[:20],
        signals=signals,
        metadata={
            "direct_child_count": len([child for child in node.children if isinstance(child, Tag)]),
        },
    )


def _extract_block_links(*, node: Tag, page_url: str) -> list[ContentBlockLink]:
    links: list[ContentBlockLink] = []

    for anchor in node.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue

        href = str(anchor.get("href") or "").strip()
        text = _clean_text(anchor.get_text(" "))

        if not href:
            continue

        links.append(
            ContentBlockLink(
                text=text,
                href=urljoin(page_url, href),
            )
        )

    return links


def _extract_block_media(*, node: Tag, page_url: str) -> list[ContentBlockMedia]:
    media: list[ContentBlockMedia] = []

    for img in node.find_all("img"):
        if not isinstance(img, Tag):
            continue

        src = img.get("src") or img.get("data-src")

        media.append(
            ContentBlockMedia(
                kind="image",
                src=urljoin(page_url, str(src)) if src else None,
                alt=img.get("alt"),
                title=img.get("title"),
            )
        )

    for video in node.find_all("video"):
        if not isinstance(video, Tag):
            continue

        src = video.get("src")
        media.append(
            ContentBlockMedia(
                kind="video",
                src=urljoin(page_url, str(src)) if src else None,
                title=video.get("title"),
            )
        )

    for iframe in node.find_all("iframe"):
        if not isinstance(iframe, Tag):
            continue

        src = iframe.get("src")
        media.append(
            ContentBlockMedia(
                kind="iframe",
                src=urljoin(page_url, str(src)) if src else None,
                title=iframe.get("title"),
            )
        )

    return media


def _infer_signals(
    *,
    node: Tag,
    text: str,
    heading: str,
    links: list[ContentBlockLink],
    media: list[ContentBlockMedia],
    classes: list[str],
) -> list[str]:
    signals: list[str] = []

    tag = node.name or ""
    class_blob = " ".join(classes).lower()
    text_lower = text.lower()
    heading_lower = heading.lower()
    link_text = " ".join(link.text.lower() for link in links)

    if tag in {"header", "main", "footer", "nav", "section", "article", "aside"}:
        signals.append(f"semantic_tag:{tag}")

    if heading:
        signals.append("has_heading")

    if len(text) >= 500:
        signals.append("text_heavy")

    if links:
        signals.append("has_links")

    if media:
        signals.append("has_media")

    if any(keyword in link_text for keyword in CTA_KEYWORDS):
        signals.append("cta_link_text")

    if any(keyword in text_lower for keyword in CTA_KEYWORDS):
        signals.append("cta_body_text")

    if any(hint in class_blob for hint in HERO_CLASS_HINTS):
        signals.append("hero_class_hint")

    if any(hint in class_blob for hint in CARD_CLASS_HINTS):
        signals.append("card_class_hint")

    if tag in QUOTE_TAGS or "quote" in class_blob or "testimonial" in class_blob:
        signals.append("quote_hint")

    if "job" in text_lower or "career" in text_lower or "opportunit" in text_lower:
        signals.append("career_content_hint")

    if "product" in text_lower or "shop" in text_lower:
        signals.append("commerce_content_hint")

    if heading_lower.startswith("welcome") or heading_lower.startswith("introduc"):
        signals.append("intro_heading_hint")

    return _dedupe(signals)


def _infer_block_type(
    *,
    node: Tag,
    text: str,
    heading: str,
    links: list[ContentBlockLink],
    media: list[ContentBlockMedia],
    classes: list[str],
    signals: list[str],
) -> str:
    tag = node.name or ""
    class_blob = " ".join(classes).lower()

    if tag == "nav" or node.get("role") == "navigation":
        return "navigation"

    if tag == "footer":
        return "footer"

    if tag == "article":
        return "article"

    if "quote_hint" in signals:
        return "quote"

    if "hero_class_hint" in signals:
        return "hero"

    if tag == "header" and (heading or media):
        return "hero"

    if "cta_link_text" in signals or "cta_body_text" in signals:
        return "cta"

    if "card_class_hint" in signals:
        return "card"

    if len(media) > 0 and len(text) < 300:
        return "media"

    if tag == "section":
        return "section"

    if tag == "main":
        return "main"

    if "grid" in class_blob and links:
        return "card_grid"

    return "content"


def _should_keep_block(block: DetectedHTMLBlockItem) -> bool:
    if block.block_type in {"navigation", "footer"}:
        return True

    if block.heading:
        return True

    if block.text_len >= 120:
        return True

    if block.link_count >= 2:
        return True

    if block.media_count > 0 and block.text_len > 30:
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