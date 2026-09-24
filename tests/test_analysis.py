import pytest

from lingo.analysis import parse_analysis


def test_analysis_parser_accepts_structured_corrections():
    analysis = parse_analysis(
        {
            "summary": "Talked about work.",
            "praise": None,
            "reusable_item": "used to",
            "language_evidence": None,
            "proficiency_evidence": "Handled abstract detail.",
            "corrections": [
                {
                    "source_turn_sequence": 1,
                    "original": "since five years",
                    "corrected": "for five years",
                    "reason": "Use for with a duration.",
                    "category": "grammar",
                    "confidence": 0.95,
                    "pattern_key": "present-perfect-duration",
                    "language_transfer": None,
                }
            ],
        }
    )
    assert analysis.corrections[0].category == "grammar"


def test_analysis_parser_rejects_invalid_category():
    with pytest.raises(ValueError):
        parse_analysis(
            {
                "summary": "x",
                "corrections": [
                    {
                        "source_turn_sequence": 0,
                        "original": "x",
                        "corrected": "y",
                        "reason": "z",
                        "category": "pronunciation",
                        "confidence": 1,
                    }
                ],
            }
        )


def test_analysis_parser_rejects_empty_correction_text():
    with pytest.raises(ValueError, match="original"):
        parse_analysis(
            {
                "summary": "x",
                "corrections": [
                    {
                        "source_turn_sequence": 0,
                        "original": None,
                        "corrected": "y",
                        "reason": "z",
                        "category": "grammar",
                        "confidence": 0.9,
                    }
                ],
            }
        )


def test_analysis_parser_rejects_noncanonical_pattern_key():
    with pytest.raises(ValueError, match="pattern key"):
        parse_analysis(
            {
                "summary": "x",
                "corrections": [
                    {
                        "source_turn_sequence": 0,
                        "original": "x",
                        "corrected": "y",
                        "reason": "z",
                        "category": "grammar",
                        "confidence": 0.9,
                        "pattern_key": "duration_for",
                    }
                ],
            }
        )


def test_analysis_parser_rejects_boolean_confidence():
    with pytest.raises(ValueError, match="confidence"):
        parse_analysis(
            {
                "summary": "x",
                "corrections": [
                    {
                        "source_turn_sequence": 0,
                        "original": "x",
                        "corrected": "y",
                        "reason": "z",
                        "category": "grammar",
                        "confidence": True,
                    }
                ],
            }
        )
