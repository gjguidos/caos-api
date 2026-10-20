"""A step's display payload names which declared output key produced what.

The frontend links a downstream step's ``${outputs.<step>.<key>}`` reference to
the upstream value by parsing the command itself, so it only needs the upstream
payload to say which declared key each value belongs to. Image outputs must be
referenced by index rather than repeated, because an image "url" here is
routinely an inline base64 data URI - re-emitting one would duplicate the whole
payload.
"""

from __future__ import annotations

# Importing registers these tools, so their declared outputs resolve.
import src.domains.agentverse.tools.analyze_tool  # noqa: F401
import src.domains.agentverse.tools.draw_tool  # noqa: F401
import src.domains.agentverse.tools.math_tool  # noqa: F401
from src.domains.agentverse.tools.workflow_tool.formatter import present_output_display

_PNG = "data:image/png;base64,iVBORw0AA=="
_JPG = "data:image/jpeg;base64,/9j/AA=="


class TestScalarOutputsCarryTheirValue:
    def test_a_string_output_is_named_and_valued(self) -> None:
        declared = present_output_display("math", {"answer": "42"})["declared_outputs"]

        assert declared == {"answer": {"label": "Answer", "type": "string", "value": "42"}}

    def test_the_value_is_the_declared_path_not_the_display_text(self) -> None:
        """`text` is whatever the bubble renders; the annotation follows `path`."""
        env = {"message": "Done!", "answer": "42"}
        output = present_output_display("math", env)

        assert output["text"] == "Done!"
        assert output["declared_outputs"]["answer"]["value"] == "42"


class TestImageOutputsAreReferencedNotRepeated:
    def test_an_image_output_points_into_the_images_array(self) -> None:
        output = present_output_display("draw", {"images": [{"url": _PNG}]})

        assert [im["url"] for im in output["images"]] == [_PNG]
        assert output["declared_outputs"] == {
            "image": {"label": "Image", "type": "image", "image_indexes": [0]}
        }

    def test_multiple_images_map_to_every_index(self) -> None:
        output = present_output_display("draw", {"images": [{"url": _PNG}, {"url": _JPG}]})

        assert output["declared_outputs"]["image"]["image_indexes"] == [0, 1]

    def test_no_base64_is_duplicated_into_the_annotation(self) -> None:
        """The whole point of the index: the payload carries each image once."""
        entry = present_output_display("draw", {"images": [{"url": _PNG}]})["declared_outputs"][
            "image"
        ]

        assert "value" not in entry
        assert _PNG not in repr(entry)

    def test_a_raw_base64_blob_is_matched_like_any_other_image(self) -> None:
        """`collect_images` rewrites a bare blob into a data URI for display, so
        matching on the source string would have to re-normalize; matching on
        where the image was found sidesteps that entirely."""
        blob = "iVBORw0" + "A" * 200
        output = present_output_display("draw", {"images": [blob]})

        assert len(output["images"]) == 1
        assert output["declared_outputs"]["image"]["image_indexes"] == [0]

    def test_only_images_under_the_declared_path_are_attributed(self) -> None:
        """An image elsewhere in the env is displayed but is not this key's value.

        `draw` declares its image at `images`. A second image reachable from an
        unrelated branch still renders in the bubble, so it must appear in
        `images` - but attributing it to the declared key would tell the frontend
        `${outputs.<step>.image}` resolves to something it does not.
        """
        output = present_output_display("draw", {"images": [{"url": _PNG}], "logo": {"url": _JPG}})

        assert [im["url"] for im in output["images"]] == [_PNG, _JPG]
        assert output["declared_outputs"]["image"]["image_indexes"] == [0]

    def test_the_wire_shape_carries_no_internal_collection_fields(self) -> None:
        """`path` exists to resolve the indexes server-side; it must not ship."""
        output = present_output_display("draw", {"images": [{"url": _PNG}]})

        assert [set(im) for im in output["images"]] == [{"url", "mime_type"}]

    def test_a_declared_image_absent_from_the_payload_reports_no_indexes(self) -> None:
        output = present_output_display("draw", {"images": [], "answer": "nothing drawn"})

        assert output["declared_outputs"]["image"]["image_indexes"] == []


class TestAnnotationIsAlwaysPresentAndNeverFatal:
    def test_a_tool_declaring_no_outputs_annotates_empty(self) -> None:
        declared = present_output_display("analyze", {"answer": "looked at it"})

        assert declared["declared_outputs"] == {}

    def test_an_unknown_tool_key_annotates_empty(self) -> None:
        """`output_display` falls back to the step id for non-canonical stored
        shapes, which is not a registry key."""
        declared = present_output_display("no_such_tool_key", {"answer": "x"})

        assert declared["declared_outputs"] == {}

    def test_a_failed_step_annotates_empty(self) -> None:
        """Nothing was produced, so no declared key resolved."""
        output = present_output_display("math", {"success": False, "error": "boom"})

        assert output["ok"] is False
        assert output["declared_outputs"] == {}

    def test_a_non_dict_env_annotates_empty(self) -> None:
        assert present_output_display("math", "just text")["declared_outputs"] == {}

    def test_existing_display_fields_are_untouched(self) -> None:
        """The annotation is additive - `text`/`images` keep their contract."""
        output = present_output_display("math", {"answer": "42"})

        assert output["text"] == "42"
        assert output["images"] == []
        assert output["ok"] is True
        assert output["error"] is None
