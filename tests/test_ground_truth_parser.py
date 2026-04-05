"""Negative and edge-case tests for parse_ground_truth."""

from __future__ import annotations

import pytest

from clawbio_bench.core import BenchmarkConfigError, parse_ground_truth


class TestBomHandling:
    def test_utf8_bom_stripped(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_bytes(b"\xef\xbb\xbf# FINDING_CATEGORY: fst_correct\n# GROUND_TRUTH_FST: 0.1\n")
        gt = parse_ground_truth(f)
        assert gt["FINDING_CATEGORY"] == "fst_correct"
        assert gt["GROUND_TRUTH_FST"] == "0.1"


class TestLeadingBlankLines:
    def test_blank_lines_before_header(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_text("\n\n# FINDING_CATEGORY: fst_correct\n# TARGET_GENE: CYP2D6\n")
        gt = parse_ground_truth(f)
        assert gt["FINDING_CATEGORY"] == "fst_correct"
        assert gt["TARGET_GENE"] == "CYP2D6"


class TestFlexibleColonSpacing:
    def test_no_space_after_colon(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_text("# FINDING_CATEGORY:fst_correct\n")
        gt = parse_ground_truth(f)
        assert gt["FINDING_CATEGORY"] == "fst_correct"

    def test_space_before_colon(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_text("# FINDING_CATEGORY : fst_correct\n")
        gt = parse_ground_truth(f)
        assert gt["FINDING_CATEGORY"] == "fst_correct"

    def test_multiple_spaces(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_text("# FINDING_CATEGORY  :   fst_correct\n")
        gt = parse_ground_truth(f)
        assert gt["FINDING_CATEGORY"] == "fst_correct"


class TestDuplicateKeys:
    def test_last_value_wins_with_warning(self, tmp_path, capsys):
        f = tmp_path / "gt.txt"
        f.write_text("# FINDING_CATEGORY: fst_correct\n# FINDING_CATEGORY: heim_bounded\n")
        gt = parse_ground_truth(f)
        assert gt["FINDING_CATEGORY"] == "heim_bounded"
        captured = capsys.readouterr()
        assert "Duplicate header" in captured.err


class TestRequiredFields:
    def test_missing_required_field_raises(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_text("# FINDING_CATEGORY: fst_correct\n")
        with pytest.raises(BenchmarkConfigError, match="missing required fields"):
            parse_ground_truth(f, required_fields=["FINDING_CATEGORY", "PAYLOAD"])

    def test_all_required_present(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_text("# FINDING_CATEGORY: fst_correct\n# PAYLOAD: input.vcf\n")
        gt = parse_ground_truth(f, required_fields=["FINDING_CATEGORY", "PAYLOAD"])
        assert gt["FINDING_CATEGORY"] == "fst_correct"


class TestMalformedLines:
    def test_line_without_colon_ignored(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_text("# FINDING_CATEGORY: fst_correct\n# random comment no colon\n")
        gt = parse_ground_truth(f)
        assert gt == {"FINDING_CATEGORY": "fst_correct"}

    def test_key_starting_with_digit_ignored(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_text("# 123KEY: value\n# VALID: yes\n")
        gt = parse_ground_truth(f)
        assert "123KEY" not in gt
        assert gt["VALID"] == "yes"


class TestHeaderBlockTermination:
    def test_blank_line_ends_header_block(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_text(
            "# FINDING_CATEGORY: fst_correct\n"
            "\n"  # blank — ends header block
            "# NOT_PARSED: should_not_appear\n"
            "data below\n"
        )
        gt = parse_ground_truth(f)
        assert gt["FINDING_CATEGORY"] == "fst_correct"
        assert "NOT_PARSED" not in gt

    def test_data_line_ends_header_block(self, tmp_path):
        f = tmp_path / "gt.txt"
        f.write_text("# FINDING_CATEGORY: fst_correct\ndata line\n# LATE: ignored\n")
        gt = parse_ground_truth(f)
        assert gt["FINDING_CATEGORY"] == "fst_correct"
        assert "LATE" not in gt
