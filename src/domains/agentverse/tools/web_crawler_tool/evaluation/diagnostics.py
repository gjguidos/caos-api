from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

SPA_MARKERS = [
    'id="root"',
    "id='root'",
    'id="app"',
    "id='app'",
    'id="__next"',
    "id='__next'",
    'id="__nuxt"',
    "id='__nuxt'",
    "data-reactroot",
    "window.__next_data__",
    "window.__initial_state__",
    "__nuxt__",
]

FRAMEWORK_MARKERS = [
    "_next/",
    "next/static",
    "_nuxt/",
    "ng-version",
    "svelte",
    "vite",
    "webpack",
    "astro-island",
]

JS_REQUIRED_MARKERS = [
    "please enable javascript",
    "you need to enable javascript",
    "enable javascript to continue",
    "this app requires javascript",
    "javascript is disabled",
]

PLACEHOLDER_MARKERS = [
    "loading...",
    "spinner",
    "skeleton",
    "shimmer",
    "placeholder",
    "progressbar",
]

PROTECTION_MARKERS = [
    "checking your browser",
    "just a moment",
    "verify you are human",
    "captcha",
    "access denied",
    "bot detection",
    "cloudflare",
]


def build_static_page_diagnostics(
    html: str,
    *,
    soup: BeautifulSoup | None = None,
) -> dict[str, Any]:
    html = html or ""
    html_lower = html.lower()
    if soup is None:
        soup = BeautifulSoup(
            html,
            "html.parser",
        )

    detected_spa_markers = [
        marker for marker in SPA_MARKERS if marker in html_lower
    ]

    detected_framework_markers = [
        marker for marker in FRAMEWORK_MARKERS if marker in html_lower
    ]

    detected_js_required_markers = [
        marker for marker in JS_REQUIRED_MARKERS if marker in html_lower
    ]

    detected_placeholder_markers = [
        marker for marker in PLACEHOLDER_MARKERS if marker in html_lower
    ]

    detected_protection_markers = [
        marker for marker in PROTECTION_MARKERS if marker in html_lower
    ]

    body = soup.body
    body_text = body.get_text(" ", strip=True) if body else ""

    return {
        "renderer": "static",
        "html_size": len(html.encode("utf-8", errors="ignore")),
        "body_text_len": len(body_text),
        "script_count": len(soup.find_all("script")),
        "style_count": len(soup.find_all("style")),
        "has_body": body is not None,
        "has_spa_marker": bool(detected_spa_markers),
        "spa_markers": detected_spa_markers[:10],
        "has_framework_marker": bool(detected_framework_markers),
        "framework_markers": detected_framework_markers[:10],
        "has_js_required_marker": bool(detected_js_required_markers),
        "js_required_markers": detected_js_required_markers[:10],
        "has_placeholder_marker": bool(detected_placeholder_markers),
        "placeholder_markers": detected_placeholder_markers[:10],
        "has_protection_marker": bool(detected_protection_markers),
        "protection_markers": detected_protection_markers[:10],
    }