from src.domains.agentverse.tools.analyze_tool import (
    ParsedAnalysis,
    _sanitize_analysis_payload,
)


class TestSanitizeAnalysisPayload:
    def test_prose_quality_threshold_falls_back_to_default(self):
        raw = _sanitize_analysis_payload(
            {
                "acceptance": {
                    "must_have": ["a synonym"],
                    "prefer": [],
                    "quality_threshold": (
                        "The response should be immediately usable by the user "
                        "as a natural English phrasing or synonym."
                    ),
                }
            }
        )

        assert raw["acceptance"]["quality_threshold"] == 0.8
        ParsedAnalysis(**raw).normalize()

    def test_numeric_strings_are_parsed_and_clamped(self):
        raw = _sanitize_analysis_payload(
            {
                "acceptance": {"quality_threshold": "0.6"},
                "route_confidence": "1.7",
                "disambiguation": {"score": "not a number"},
            }
        )

        assert raw["acceptance"]["quality_threshold"] == 0.6
        assert raw["route_confidence"] == 1.0
        assert raw["disambiguation"]["score"] == 0.0

    def test_empty_and_missing_scalars_use_defaults(self):
        raw = _sanitize_analysis_payload({"acceptance": {"quality_threshold": ""}})

        assert raw["acceptance"]["quality_threshold"] == 0.8
        assert raw["route_confidence"] == 0.0

    def test_nullish_jurisdiction_becomes_none(self):
        for value in (None, "", "  ", "none", "NULL", "N/A", False):
            raw = _sanitize_analysis_payload({"jurisdiction": value})

            assert raw["jurisdiction"] is None, value
            ParsedAnalysis(**raw).normalize()

    def test_non_string_jurisdiction_is_flattened(self):
        assert (
            _sanitize_analysis_payload({"jurisdiction": ["EU", "US"]})["jurisdiction"] == "EU, US"
        )
        assert (
            _sanitize_analysis_payload({"jurisdiction": {"country": "SE"}})["jurisdiction"] == "SE"
        )
        assert _sanitize_analysis_payload({"jurisdiction": " EU "})["jurisdiction"] == "EU"

    def test_structured_jurisdiction_still_validates(self):
        raw = _sanitize_analysis_payload({"jurisdiction": {"country": "SE", "region": "EU"}})

        parsed = ParsedAnalysis(**raw).normalize()
        assert parsed.jurisdiction == "SE, EU"

    def test_domain_defaults_when_missing_or_nullish(self):
        for value in (None, "", "none", [], {}):
            assert _sanitize_analysis_payload({"domain": value})["domain"] == "general"

        assert _sanitize_analysis_payload({"domain": " Legal "})["domain"] == "Legal"
