"""The display layer must report an image's real mime.

`collect_images` reads `mime`/`mime_type` off the image dict and defaults to
`image/jpeg` when neither is there, so an env whose images carry only a data URI
reports every image - PNGs included - as JPEG. The proxy adapter keeps `mime`
alongside the URL precisely so this reads the real value.
"""

from __future__ import annotations

import pytest

from src.base.integrations.mcp.proxy_contract import normalize_run_result
from src.domains.agentverse.tools.workflow_tool.formatter import present_output_display

_PNG_URL = "data:image/png;base64,AA=="


class TestDisplayImageMime:
    def test_a_png_from_the_proxy_reports_png(self) -> None:
        env = normalize_run_result(
            {"answer": "here", "images": [{"data": "AA==", "mime": "image/png"}]}
        ).as_env()

        images = present_output_display("analyzer", env)["images"]

        assert images == [{"url": _PNG_URL, "mime_type": "image/png"}]

    def test_an_image_without_a_mime_still_renders(self) -> None:
        """Runs persisted before `mime` was carried degrade, they do not fail."""
        images = present_output_display("analyzer", {"images": [{"url": _PNG_URL}]})["images"]

        assert images == [{"url": _PNG_URL, "mime_type": "image/jpeg"}]

    @pytest.mark.parametrize("mime", ["image/png", "image/webp", "image/gif"])
    def test_the_declared_mime_wins_over_the_default(self, mime: str) -> None:
        env = normalize_run_result({"answer": "here", "images": [{"data": "AA==", "mime": mime}]})

        images = present_output_display("analyzer", env.as_env())["images"]

        assert images[0]["mime_type"] == mime
