from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup


def parse_html(
    *,
    html: str,
    base_url: str,
    max_chars: int,
    clean_soup: BeautifulSoup | None = None,
) -> tuple[str, str, list[str]]:
    soup = clean_soup

    if soup is None:
        soup = BeautifulSoup(html, "html.parser")

        for element in soup(["script", "style", "noscript", "svg"]):
            element.decompose()

    title = ""
    if soup.title:
        title = clean_text(soup.title.get_text(" "))

    text = clean_text(soup.get_text("\n"))

    if max_chars > 0:
        text = text[:max_chars]

    links = (
        extract_links(
            soup=soup,
            base_url=base_url,
        )
        if extract_links
        else []
    )

    return title, text, links


def extract_links(*, soup: BeautifulSoup, base_url: str) -> list[str]:
    links: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")

        if not href:
            continue

        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)

        if parsed.scheme not in {"http", "https"}:
            continue

        clean_url, _fragment = urldefrag(absolute)
        links.add(clean_url)

    return sorted(links)


def clean_text(value: str) -> str:
    value = re.sub(r"\r\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    return value.strip()