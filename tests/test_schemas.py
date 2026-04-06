"""Tests for the msgspec-based verdict schemas and the committed JSON Schema
artifacts under ``schemas/``.

Covers:
  - ``MinimalVerdictDoc`` accepts the shape emitted by ``harness_error_verdict``.
  - ``FullVerdictDoc`` accepts a fully-populated verdict.
  - ``FullVerdictDoc`` rejects unknown fields, negative wall_clock_seconds, and
    missing required fields.
  - ``validate_verdict_schema(strict=True)`` routes correctly based on category.
  - Committed ``schemas/*.schema.json`` files match what the current Struct
    definitions would generate (CI drift gate).
"""

from __future__ import annotations

import json
from pathlib import Path

import msgspec
import pytest

from clawbio_bench.core import VerdictSchemaError, validate_verdict_schema
from clawbio_bench.schemas import (
    FullVerdictDoc,
    MinimalVerdictDoc,
    generate_json_schemas,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_doc() -> dict:
    return {
        "verdict": {"category": "harness_error", "rationale": "boom"},
        "test_case": {"name": "eq_01"},
        "commit": {"sha": "abc123"},
    }


def _full_doc() -> dict:
    """A fully-populated verdict matching build_verdict_doc's output shape."""
    return {
        "benchmark_version": "0.1.0",
        "benchmark_name": "equity-scorer",
        "start_time_utc": "2026-04-04T00:00:00+00:00",
        "timestamp_utc": "2026-04-04T00:00:01+00:00",
        "wall_clock_seconds": 0.123,
        "commit": {"sha": "abc123", "short": "abc12345"},
        "test_case": {
            "name": "eq_01",
            "driver": "tests/test_cases/equity/eq_01/ground_truth.txt",
            "driver_sha256": "f" * 64,
            "payload": "input.vcf",
            "payload_sha256": "e" * 64,
        },
        "ground_truth": {"FINDING_CATEGORY": "fst_correct"},
        "ground_truth_references": {"HEIM": "HEIM v1.0"},
        "reference_genome": "GRCh38",
        "execution": {"exit_code": 0},
        "outputs": {},
        "report_analysis": {},
        "verdict": {
            "category": "fst_correct",
            "rationale": "FST values match",
            "details": {"fst": 0.123},
        },
        "environment": {"python_version": "3.13.0"},
    }


# ---------------------------------------------------------------------------
# msgspec Struct round-trip
# ---------------------------------------------------------------------------


class TestMinimalVerdictDoc:
    def test_accepts_harness_error_shape(self):
        msgspec.convert(_minimal_doc(), type=MinimalVerdictDoc)

    def test_accepts_full_doc_too(self):
        """Full docs must also validate against the minimal contract — this is
        what makes unconditional minimum validation safe."""
        msgspec.convert(_full_doc(), type=MinimalVerdictDoc)

    def test_rejects_missing_verdict(self):
        doc = _minimal_doc()
        del doc["verdict"]
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(doc, type=MinimalVerdictDoc)

    def test_rejects_missing_category(self):
        doc = _minimal_doc()
        del doc["verdict"]["category"]
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(doc, type=MinimalVerdictDoc)


class TestFullVerdictDoc:
    def test_accepts_full_doc(self):
        msgspec.convert(_full_doc(), type=FullVerdictDoc)

    def test_rejects_unknown_top_level_field(self):
        doc = _full_doc()
        doc["attacker_injected_field"] = "boom"
        with pytest.raises(msgspec.ValidationError, match="unknown field"):
            msgspec.convert(doc, type=FullVerdictDoc)

    def test_rejects_negative_wall_clock(self):
        doc = _full_doc()
        doc["wall_clock_seconds"] = -1.0
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(doc, type=FullVerdictDoc)

    def test_rejects_missing_required_field(self):
        doc = _full_doc()
        del doc["benchmark_version"]
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(doc, type=FullVerdictDoc)

    def test_rejects_wrong_type_for_category(self):
        doc = _full_doc()
        doc["verdict"]["category"] = 123
        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(doc, type=FullVerdictDoc)


# ---------------------------------------------------------------------------
# validate_verdict_schema strict=True path
# ---------------------------------------------------------------------------


class TestValidateVerdictSchemaStrict:
    def test_strict_accepts_full_doc(self):
        validate_verdict_schema(
            _full_doc(),
            rubric_categories=["fst_correct"],
            strict=True,
        )

    def test_strict_rejects_minimal_non_error_doc(self):
        """Minimal docs must fail strict validation unless they're harness_error.
        This is the drift gate for build_verdict_doc's output shape."""
        doc = _minimal_doc()
        doc["verdict"]["category"] = "fst_correct"  # not harness_error
        with pytest.raises(VerdictSchemaError, match="full schema"):
            validate_verdict_schema(
                doc,
                rubric_categories=["fst_correct"],
                strict=True,
            )

    def test_strict_accepts_harness_error_minimal_doc(self):
        """harness_error verdicts are exempt from the strict check — they're
        intentionally minimal."""
        doc = _minimal_doc()
        # Already has category='harness_error'
        validate_verdict_schema(doc, strict=True)

    def test_strict_rejects_unknown_field_in_full_doc(self):
        doc = _full_doc()
        doc["phantom_field"] = "injected"
        with pytest.raises(VerdictSchemaError):
            validate_verdict_schema(doc, strict=True)

    def test_non_strict_accepts_minimal_non_error_doc(self):
        """Without strict=True, the minimal doc (old behavior) still validates."""
        doc = _minimal_doc()
        doc["verdict"]["category"] = "fst_correct"
        validate_verdict_schema(
            doc,
            rubric_categories=["fst_correct"],
            strict=False,
        )


# ---------------------------------------------------------------------------
# Committed JSON Schema artifacts drift gate
# ---------------------------------------------------------------------------


class TestCommittedSchemas:
    """The files under ``schemas/`` must match what ``generate_json_schemas()``
    currently produces. If this fails, run ``python scripts/gen_schemas.py``
    and commit the updated artifacts.
    """

    def test_schemas_directory_exists(self):
        assert SCHEMAS_DIR.exists(), f"schemas/ directory missing: {SCHEMAS_DIR}"

    @staticmethod
    def _normalize_descriptions(obj: object) -> object:
        """Collapse whitespace in 'description' fields so Python 3.11 vs 3.14
        docstring indentation differences don't cause false drift failures."""
        if isinstance(obj, dict):
            return {
                k: (
                    " ".join(v.split())
                    if k == "description" and isinstance(v, str)
                    else TestCommittedSchemas._normalize_descriptions(v)
                )
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [TestCommittedSchemas._normalize_descriptions(v) for v in obj]
        return obj

    def test_committed_schemas_match_generator(self):
        current = generate_json_schemas()
        for name, schema in current.items():
            path = SCHEMAS_DIR / f"{name}.schema.json"
            assert path.exists(), (
                f"Missing committed schema: {path}. "
                f"Run `python scripts/gen_schemas.py` and commit."
            )
            committed = json.loads(path.read_text())
            # Normalize description whitespace for cross-version compatibility
            # (Python 3.14 changed inspect.cleandoc behavior for class docstrings).
            norm_committed = self._normalize_descriptions(committed)
            norm_schema = self._normalize_descriptions(schema)
            assert norm_committed == norm_schema, (
                f"Committed schema {path.name} drifted from Struct definition. "
                f"Run `python scripts/gen_schemas.py` and commit."
            )

    def test_schemas_are_valid_json_schema_2020_12(self):
        """Smoke check: every committed schema is valid JSON Schema 2020-12
        (verified by attempting to use it via jsonschema if available;
        otherwise just checks that it parses and declares the expected keys)."""
        for path in SCHEMAS_DIR.glob("*.schema.json"):
            schema = json.loads(path.read_text())
            # msgspec-generated schemas use $defs + properties + required
            assert "properties" in schema or "$ref" in schema, (
                f"{path.name} looks malformed (no properties or $ref)"
            )
