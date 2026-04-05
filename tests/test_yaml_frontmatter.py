"""Tests for the YAML frontmatter ground-truth parser in core.parse_ground_truth.

The parser is backward-compatible: files using the legacy `# KEY: value`
format are still accepted (covered by test_smoke_suite.py::test_core_ground_truth_parsing).
These tests exercise the new YAML-in-comment path specifically.

The dual parser design is critical for Model A test cases (PharmGx 23andMe-style
TSVs) where the ground-truth file IS the audited tool's input. The YAML block
must live entirely inside `#`-prefixed lines so ClawBio still sees a comment
block it skips, and the TSV payload below remains untouched.
"""

from __future__ import annotations

import pytest

from clawbio_bench.core import BenchmarkConfigError, parse_ground_truth


def _write(tmp_path, content: str):
    path = tmp_path / "ground_truth.txt"
    path.write_text(content, encoding="utf-8")
    return path


class TestYamlFrontmatterBasics:
    def test_parses_simple_yaml_block(self, tmp_path):
        path = _write(
            tmp_path,
            "# ---\n"
            "# BENCHMARK: yaml_test_v1\n"
            "# FINDING_CATEGORY: correct_determinate\n"
            "# TARGET_GENE: CYP2C19\n"
            "# ---\n",
        )
        gt = parse_ground_truth(path)
        assert gt["BENCHMARK"] == "yaml_test_v1"
        assert gt["FINDING_CATEGORY"] == "correct_determinate"
        assert gt["TARGET_GENE"] == "CYP2C19"

    def test_preserves_upper_snake_keys(self, tmp_path):
        """UPPER_SNAKE_CASE keys must pass through unchanged — downstream
        scoring code indexes gt[...] with exactly these strings."""
        path = _write(
            tmp_path,
            "# ---\n"
            "# GROUND_TRUTH_PHENOTYPE: Poor Metabolizer\n"
            "# EXPECTED_EXIT_CODE: 0\n"
            "# FINDING_CATEGORY: correct_determinate\n"
            "# ---\n",
        )
        gt = parse_ground_truth(path)
        # Keys unchanged
        assert "GROUND_TRUTH_PHENOTYPE" in gt
        assert "EXPECTED_EXIT_CODE" in gt
        # Numeric scalar coerced to string for downstream int() compat
        assert gt["EXPECTED_EXIT_CODE"] == "0"

    def test_ignores_payload_after_closing_fence(self, tmp_path):
        """Audited-tool payload must not be treated as ground truth headers."""
        path = _write(
            tmp_path,
            "# ---\n"
            "# BENCHMARK: pgx_test\n"
            "# FINDING_CATEGORY: correct_determinate\n"
            "# ---\n"
            "# rsid\tchromosome\tposition\tgenotype\n"
            "rs4244285\t10\t96541616\tAA\n"
            "rs4986893\t10\t96540410\tGG\n",
        )
        gt = parse_ground_truth(path)
        assert gt["BENCHMARK"] == "pgx_test"
        assert "rs4244285" not in gt  # TSV rows must not leak into ground truth

    def test_supports_yaml_block_scalars(self, tmp_path):
        """YAML block scalars (|) are the reason to migrate — multi-line
        narrative fields get a clean home instead of continuation-line hacks."""
        path = _write(
            tmp_path,
            "# ---\n"
            "# BENCHMARK: multiline_test\n"
            "# FINDING_CATEGORY: correct_determinate\n"
            "# GROUND_TRUTH_BEHAVIOR: |\n"
            "#   Line one of the narrative\n"
            "#   Line two of the narrative\n"
            "#   Line three of the narrative\n"
            "# ---\n",
        )
        gt = parse_ground_truth(path)
        behavior = gt["GROUND_TRUTH_BEHAVIOR"]
        assert "Line one of the narrative" in behavior
        assert "Line three of the narrative" in behavior

    def test_coerces_scalar_types_to_strings(self, tmp_path):
        """ints/floats/bools in YAML get stringified so downstream code that
        passes gt[...] through int()/str comparisons keeps working."""
        path = _write(
            tmp_path,
            "# ---\n"
            "# BENCHMARK: coercion_test\n"
            "# FINDING_CATEGORY: correct_determinate\n"
            "# EXPECTED_EXIT_CODE: 2\n"
            "# SOME_FLOAT: 3.14\n"
            "# ---\n",
        )
        gt = parse_ground_truth(path)
        assert gt["EXPECTED_EXIT_CODE"] == "2"
        assert gt["SOME_FLOAT"] == "3.14"


class TestYamlFrontmatterHardening:
    """Regression tests for the hardened YAML parser:

    * Keys must match UPPER_SNAKE_CASE (parallel to the legacy parser).
    * Anchors (``&``) and aliases (``*``) are rejected.
    * Merge keys (``<<:``) are rejected.
    * Nested dict/list values are recursively normalized to strings.
    """

    def test_rejects_lowercase_key(self, tmp_path):
        path = _write(
            tmp_path,
            "# ---\n# finding_category: correct_determinate\n# ---\n",
        )
        with pytest.raises(BenchmarkConfigError, match="UPPER_SNAKE"):
            parse_ground_truth(path)

    def test_rejects_mixed_case_key(self, tmp_path):
        path = _write(
            tmp_path,
            "# ---\n# FindingCategory: correct_determinate\n# ---\n",
        )
        with pytest.raises(BenchmarkConfigError, match="UPPER_SNAKE"):
            parse_ground_truth(path)

    def test_rejects_anchor(self, tmp_path):
        path = _write(
            tmp_path,
            "# ---\n"
            "# BASE: &base normal metabolizer\n"
            "# GROUND_TRUTH_PHENOTYPE: *base\n"
            "# FINDING_CATEGORY: correct_determinate\n"
            "# ---\n",
        )
        with pytest.raises(BenchmarkConfigError, match="anchors or aliases"):
            parse_ground_truth(path)

    def test_rejects_merge_key(self, tmp_path):
        path = _write(
            tmp_path,
            "# ---\n"
            "# DEFAULTS:\n"
            "#   PREFIX: X\n"
            "# FINDING_CATEGORY: correct_determinate\n"
            "# <<: *DEFAULTS\n"
            "# ---\n",
        )
        with pytest.raises(BenchmarkConfigError):
            parse_ground_truth(path)

    def test_normalizes_nested_dict_to_strings(self, tmp_path):
        path = _write(
            tmp_path,
            "# ---\n"
            "# FINDING_CATEGORY: correct_determinate\n"
            "# GROUND_TRUTH_DETAILS:\n"
            "#   drug_name: warfarin\n"
            "#   dose_mg: 5\n"
            "#   contraindicated: true\n"
            "# ---\n",
        )
        gt = parse_ground_truth(path)
        nested = gt["GROUND_TRUTH_DETAILS"]
        assert isinstance(nested, dict)
        assert nested["drug_name"] == "warfarin"
        assert nested["dose_mg"] == "5"
        assert nested["contraindicated"] == "True"

    def test_normalizes_nested_list_to_strings(self, tmp_path):
        path = _write(
            tmp_path,
            "# ---\n"
            "# FINDING_CATEGORY: correct_determinate\n"
            "# GENES:\n"
            "#   - CYP2D6\n"
            "#   - CYP2C19\n"
            "#   - DPYD\n"
            "# ---\n",
        )
        gt = parse_ground_truth(path)
        assert gt["GENES"] == ["CYP2D6", "CYP2C19", "DPYD"]


class TestYamlFrontmatterErrors:
    def test_rejects_unterminated_block(self, tmp_path):
        path = _write(
            tmp_path,
            "# ---\n# BENCHMARK: broken\n# FINDING_CATEGORY: correct_determinate\n",
        )
        with pytest.raises(BenchmarkConfigError, match="Unterminated YAML frontmatter"):
            parse_ground_truth(path)

    def test_rejects_non_comment_line_inside_block(self, tmp_path):
        path = _write(
            tmp_path,
            "# ---\n"
            "# BENCHMARK: broken\n"
            "not a comment line\n"
            "# FINDING_CATEGORY: correct_determinate\n"
            "# ---\n",
        )
        with pytest.raises(BenchmarkConfigError, match="Non-comment line"):
            parse_ground_truth(path)

    def test_rejects_non_mapping_root(self, tmp_path):
        path = _write(
            tmp_path,
            "# ---\n# - this is a list\n# - not a mapping\n# ---\n",
        )
        with pytest.raises(BenchmarkConfigError, match="must be a mapping"):
            parse_ground_truth(path)

    def test_rejects_non_string_key(self, tmp_path):
        path = _write(
            tmp_path,
            "# ---\n# 42: numeric key is not allowed\n# ---\n",
        )
        with pytest.raises(BenchmarkConfigError, match="must be a string"):
            parse_ground_truth(path)

    def test_required_fields_still_enforced(self, tmp_path):
        path = _write(
            tmp_path,
            "# ---\n# BENCHMARK: missing_required\n# ---\n",
        )
        with pytest.raises(BenchmarkConfigError, match="missing required fields"):
            parse_ground_truth(path, required_fields=["FINDING_CATEGORY"])


class TestDualParserBackwardsCompat:
    def test_legacy_format_still_parses(self, tmp_path):
        """Files without the `# ---` sentinel fall through to the legacy
        `# KEY: value` parser. This is what makes the migration voluntary
        per-file — no flag day required."""
        path = _write(
            tmp_path,
            "# BENCHMARK: legacy_test v1.0\n"
            "# GROUND_TRUTH_FST: 0.300\n"
            "# FINDING_CATEGORY: fst_correct\n"
            "# PAYLOAD: input.vcf\n"
            "not a comment\n",
        )
        gt = parse_ground_truth(path)
        assert gt["BENCHMARK"] == "legacy_test v1.0"
        assert gt["GROUND_TRUTH_FST"] == "0.300"
        assert gt["FINDING_CATEGORY"] == "fst_correct"
        assert gt["PAYLOAD"] == "input.vcf"

    def test_blank_line_before_yaml_fence_allowed(self, tmp_path):
        """Leading blank lines are tolerated in both formats."""
        path = _write(
            tmp_path,
            "\n"
            "\n"
            "# ---\n"
            "# BENCHMARK: blanks_before\n"
            "# FINDING_CATEGORY: correct_determinate\n"
            "# ---\n",
        )
        gt = parse_ground_truth(path)
        assert gt["BENCHMARK"] == "blanks_before"

    def test_yaml_fence_inside_legacy_header_does_not_trigger(self, tmp_path):
        """A `# ---` that appears AFTER a legacy key/value pair should not
        flip the parser into YAML mode mid-stream — the format is decided
        by the first non-blank line only."""
        path = _write(
            tmp_path,
            "# BENCHMARK: legacy_first\n"
            "# FINDING_CATEGORY: correct_determinate\n"
            "# ---\n"
            "# this should be ignored by legacy parser\n",
        )
        gt = parse_ground_truth(path)
        assert gt["BENCHMARK"] == "legacy_first"
        assert gt["FINDING_CATEGORY"] == "correct_determinate"
