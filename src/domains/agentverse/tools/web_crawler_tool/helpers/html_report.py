from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag


def build_html_components_report(
    *,
    html: str,
    url: str,
    final_url: str,
    soup: BeautifulSoup | None = None,
) -> dict[str, Any]:
    if soup is None:
        soup = BeautifulSoup(html or "", "html.parser")

    return {
        "url": url,
        "final_url": final_url or url,
        "page": extract_page_metadata(soup),
        "semantic_regions": extract_semantic_regions(soup),
        "headings": extract_headings(soup),
        "navigation": extract_navigation(soup, base_url=final_url or url),
        "interactive": extract_interactive_components(soup),
        "forms": extract_forms(soup),
        "media": extract_media(soup, base_url=final_url or url),
        "seo": extract_seo_metadata(soup),
        "technical_assets": extract_technical_assets(soup, base_url=final_url or url),
        "accessibility": extract_accessibility_signals(soup),
    }


def extract_page_metadata(soup: BeautifulSoup) -> dict[str, Any]:
    html_tag = soup.find("html")

    return {
        "title": clean_text(soup.title.get_text(" ")) if soup.title else "",
        "lang": html_tag.get("lang") if isinstance(html_tag, Tag) else None,
        "charset": extract_charset(soup),
        "body_classes": list((soup.body or {}).get("class", [])) if soup.body else [],
    }


def extract_charset(soup: BeautifulSoup) -> str | None:
    charset = soup.find("meta", attrs={"charset": True})
    if isinstance(charset, Tag):
        return charset.get("charset")

    content_type = soup.find("meta", attrs={"http-equiv": "Content-Type"})
    if isinstance(content_type, Tag):
        return content_type.get("content")

    return None


def extract_semantic_regions(soup: BeautifulSoup) -> dict[str, int]:
    tags = [
        "header",
        "nav",
        "main",
        "section",
        "article",
        "aside",
        "footer",
        "dialog",
        "search",
    ]

    return {tag: len(soup.find_all(tag)) for tag in tags}


def extract_headings(soup: BeautifulSoup) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []

    for node in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if not isinstance(node, Tag):
            continue

        text = clean_text(node.get_text(" "))

        if not text:
            continue

        headings.append(
            {
                "level": node.name,
                "text": text,
                "id": node.get("id"),
                "classes": node.get("class", []),
            }
        )

    return headings


def extract_navigation(
    soup: BeautifulSoup,
    *,
    base_url: str,
) -> list[dict[str, Any]]:
    navs: list[dict[str, Any]] = []

    for index, nav in enumerate(soup.find_all("nav"), start=1):
        if not isinstance(nav, Tag):
            continue

        links = []

        for anchor in nav.find_all("a", href=True):
            if not isinstance(anchor, Tag):
                continue

            href = anchor.get("href")
            text = clean_text(anchor.get_text(" "))

            links.append(
                {
                    "text": text,
                    "href": urljoin(base_url, str(href)),
                }
            )

        navs.append(
            {
                "index": index,
                "label": nav.get("aria-label"),
                "link_count": len(links),
                "links": links[:50],
            }
        )

    return navs


def extract_interactive_components(soup: BeautifulSoup) -> dict[str, Any]:
    buttons = []
    links = []

    for button in soup.find_all("button"):
        if not isinstance(button, Tag):
            continue

        buttons.append(
            {
                "text": clean_text(button.get_text(" ")),
                "type": button.get("type"),
                "aria_label": button.get("aria-label"),
                "classes": button.get("class", []),
                "disabled": button.has_attr("disabled"),
            }
        )

    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue

        links.append(
            {
                "text": clean_text(anchor.get_text(" ")),
                "href": anchor.get("href"),
                "aria_label": anchor.get("aria-label"),
                "target": anchor.get("target"),
                "rel": anchor.get("rel", []),
            }
        )

    roles = Counter(
        str(node.get("role"))
        for node in soup.find_all(attrs={"role": True})
        if isinstance(node, Tag) and node.get("role")
    )

    return {
        "button_count": len(buttons),
        "link_count": len(links),
        "role_counts": dict(roles),
        "buttons": buttons[:100],
        "links": links[:200],
    }


def extract_forms(soup: BeautifulSoup) -> list[dict[str, Any]]:
    forms = []

    for index, form in enumerate(soup.find_all("form"), start=1):
        if not isinstance(form, Tag):
            continue

        controls = []

        for field in form.find_all(["input", "select", "textarea", "button"]):
            if not isinstance(field, Tag):
                continue

            field_id = field.get("id")
            label = find_label_for_field(soup, field_id) if field_id else None

            options = []
            if field.name == "select":
                for option in field.find_all("option"):
                    if isinstance(option, Tag):
                        options.append(
                            {
                                "text": clean_text(option.get_text(" ")),
                                "value": option.get("value"),
                                "selected": option.has_attr("selected"),
                            }
                        )

            controls.append(
                {
                    "tag": field.name,
                    "type": field.get("type"),
                    "name": field.get("name"),
                    "id": field_id,
                    "placeholder": field.get("placeholder"),
                    "aria_label": field.get("aria-label"),
                    "label": label,
                    "required": field.has_attr("required"),
                    "disabled": field.has_attr("disabled"),
                    "autocomplete": field.get("autocomplete"),
                    "value": field.get("value"),
                    "options": options,
                }
            )

        forms.append(
            {
                "index": index,
                "method": form.get("method"),
                "action": form.get("action"),
                "target": form.get("target"),
                "enctype": form.get("enctype"),
                "control_count": len(controls),
                "controls": controls,
            }
        )

    return forms


def extract_media(
    soup: BeautifulSoup,
    *,
    base_url: str,
) -> dict[str, Any]:
    images = []

    for img in soup.find_all("img"):
        if not isinstance(img, Tag):
            continue

        src = img.get("src") or img.get("data-src")

        images.append(
            {
                "src": urljoin(base_url, str(src)) if src else None,
                "alt": img.get("alt"),
                "width": img.get("width"),
                "height": img.get("height"),
                "loading": img.get("loading"),
                "classes": img.get("class", []),
            }
        )

    videos = [
        {
            "src": node.get("src"),
            "controls": node.has_attr("controls"),
            "autoplay": node.has_attr("autoplay"),
        }
        for node in soup.find_all("video")
        if isinstance(node, Tag)
    ]

    iframes = [
        {
            "src": node.get("src"),
            "title": node.get("title"),
            "width": node.get("width"),
            "height": node.get("height"),
        }
        for node in soup.find_all("iframe")
        if isinstance(node, Tag)
    ]

    return {
        "image_count": len(images),
        "video_count": len(videos),
        "iframe_count": len(iframes),
        "images": images[:200],
        "videos": videos,
        "iframes": iframes,
    }


def extract_seo_metadata(soup: BeautifulSoup) -> dict[str, Any]:
    meta_description = soup.find("meta", attrs={"name": "description"})
    canonical = soup.find("link", attrs={"rel": "canonical"})

    open_graph = {}
    twitter = {}

    for meta in soup.find_all("meta"):
        if not isinstance(meta, Tag):
            continue

        prop = meta.get("property")
        name = meta.get("name")
        content = meta.get("content")

        if prop and str(prop).startswith("og:"):
            open_graph[str(prop)] = content

        if name and str(name).startswith("twitter:"):
            twitter[str(name)] = content

    json_ld_count = len(
        soup.find_all("script", attrs={"type": "application/ld+json"})
    )

    return {
        "description": meta_description.get("content") if isinstance(meta_description, Tag) else None,
        "canonical": canonical.get("href") if isinstance(canonical, Tag) else None,
        "open_graph": open_graph,
        "twitter": twitter,
        "json_ld_count": json_ld_count,
    }


def extract_technical_assets(
    soup: BeautifulSoup,
    *,
    base_url: str,
) -> dict[str, Any]:
    stylesheets = []

    for link in soup.find_all("link", href=True):
        if not isinstance(link, Tag):
            continue

        rel = link.get("rel") or []

        if "stylesheet" in rel:
            stylesheets.append(urljoin(base_url, str(link.get("href"))))

    scripts = []

    for script in soup.find_all("script"):
        if not isinstance(script, Tag):
            continue

        src = script.get("src")

        scripts.append(
            {
                "src": urljoin(base_url, str(src)) if src else None,
                "type": script.get("type"),
                "async": script.has_attr("async"),
                "defer": script.has_attr("defer"),
                "inline": src is None,
            }
        )

    return {
        "stylesheet_count": len(stylesheets),
        "script_count": len(scripts),
        "inline_script_count": sum(1 for script in scripts if script["inline"]),
        "stylesheets": stylesheets[:100],
        "scripts": scripts[:100],
    }


def extract_accessibility_signals(soup: BeautifulSoup) -> dict[str, Any]:
    images = [img for img in soup.find_all("img") if isinstance(img, Tag)]
    buttons = [btn for btn in soup.find_all("button") if isinstance(btn, Tag)]
    inputs = [inp for inp in soup.find_all("input") if isinstance(inp, Tag)]

    missing_alt_images = [
        img.get("src")
        for img in images
        if not img.has_attr("alt")
    ]

    unlabeled_buttons = [
        {
            "text": clean_text(btn.get_text(" ")),
            "aria_label": btn.get("aria-label"),
            "classes": btn.get("class", []),
        }
        for btn in buttons
        if not clean_text(btn.get_text(" ")) and not btn.get("aria-label")
    ]

    unlabeled_inputs = []

    for inp in inputs:
        field_id = inp.get("id")
        has_label = bool(find_label_for_field(soup, field_id)) if field_id else False

        if not has_label and not inp.get("aria-label") and not inp.get("placeholder"):
            unlabeled_inputs.append(
                {
                    "type": inp.get("type"),
                    "name": inp.get("name"),
                    "id": field_id,
                }
            )

    ids = [
        str(node.get("id"))
        for node in soup.find_all(attrs={"id": True})
        if isinstance(node, Tag) and node.get("id")
    ]

    duplicate_ids = [
        item
        for item, count in Counter(ids).items()
        if count > 1
    ]

    return {
        "missing_alt_image_count": len(missing_alt_images),
        "unlabeled_button_count": len(unlabeled_buttons),
        "unlabeled_input_count": len(unlabeled_inputs),
        "duplicate_id_count": len(duplicate_ids),
        "missing_alt_images": missing_alt_images[:100],
        "unlabeled_buttons": unlabeled_buttons[:100],
        "unlabeled_inputs": unlabeled_inputs[:100],
        "duplicate_ids": duplicate_ids[:100],
        "has_main": soup.find("main") is not None,
        "has_header": soup.find("header") is not None,
        "has_footer": soup.find("footer") is not None,
        "has_nav": soup.find("nav") is not None,
    }


def find_label_for_field(soup: BeautifulSoup, field_id: str | None) -> str | None:
    if not field_id:
        return None

    label = soup.find("label", attrs={"for": field_id})

    if isinstance(label, Tag):
        return clean_text(label.get_text(" "))

    return None


def clean_text(value: str) -> str:
    return " ".join((value or "").split()).strip()