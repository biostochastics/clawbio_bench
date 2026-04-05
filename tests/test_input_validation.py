"""Tests for input validation functions in core.py."""

from __future__ import annotations

import pytest

from clawbio_bench.core import (
    BenchmarkConfigError,
    validate_commit_sha,
    validate_payload_path,
    validate_timeout,
    validate_weights,
)


class TestValidateTimeout:
    def test_normal_value(self):
        assert validate_timeout("60") == 60

    def test_clamps_to_max(self):
        assert validate_timeout("999999") == 600

    def test_clamps_to_min(self):
        assert validate_timeout("0") == 1
        assert validate_timeout("-5") == 1

    def test_default_on_garbage(self):
        assert validate_timeout("abc") == 60
        assert validate_timeout("") == 60
        assert validate_timeout(None) == 60

    def test_int_input(self):
        assert validate_timeout(30) == 30

    def test_boundary_values(self):
        assert validate_timeout("1") == 1
        assert validate_timeout("600") == 600
        assert validate_timeout("601") == 600


class TestValidateCommitSha:
    def test_valid_full_sha(self):
        sha = "0013efcabc1234567890abcdef1234567890abcd"
        assert validate_commit_sha(sha) == sha

    def test_valid_short_sha(self):
        assert validate_commit_sha("0013efc") == "0013efc"

    def test_head_keyword(self):
        assert validate_commit_sha("HEAD") == "HEAD"

    def test_head_with_whitespace(self):
        assert validate_commit_sha("  HEAD  ") == "HEAD"

    def test_rejects_empty(self):
        with pytest.raises(BenchmarkConfigError, match="Invalid commit SHA"):
            validate_commit_sha("")

    def test_rejects_git_flags(self):
        with pytest.raises(BenchmarkConfigError, match="Invalid commit SHA"):
            validate_commit_sha("--all")

    def test_rejects_branch_name(self):
        with pytest.raises(BenchmarkConfigError, match="Invalid commit SHA"):
            validate_commit_sha("main")

    def test_rejects_too_short_3_char(self):
        with pytest.raises(BenchmarkConfigError, match="Invalid commit SHA"):
            validate_commit_sha("abc")

    def test_rejects_too_short_6_char(self):
        # 4-6 char SHAs have too many collisions; minimum is 7 (git default)
        with pytest.raises(BenchmarkConfigError, match="Invalid commit SHA"):
            validate_commit_sha("abcdef")

    def test_min_length_7(self):
        assert validate_commit_sha("abcdef1") == "abcdef1"

    def test_uppercase_hex(self):
        assert validate_commit_sha("ABCDEF1234") == "ABCDEF1234"


class TestValidateWeights:
    def test_normal_weights(self):
        assert validate_weights("0.25,0.25,0.25,0.25") == "0.25,0.25,0.25,0.25"

    def test_named_weights(self):
        assert validate_weights("fst=0.3,het=0.3,geo=0.2,rep=0.2")

    def test_rejects_flag_injection(self):
        with pytest.raises(ValueError, match="Invalid WEIGHTS"):
            validate_weights("--input /etc/passwd")

    def test_rejects_semicolon(self):
        with pytest.raises(ValueError, match="Invalid WEIGHTS"):
            validate_weights("0.5; rm -rf /")

    def test_rejects_pipe(self):
        with pytest.raises(ValueError, match="Invalid WEIGHTS"):
            validate_weights("0.5 | cat /etc/shadow")

    def test_rejects_backtick(self):
        with pytest.raises(ValueError, match="Invalid WEIGHTS"):
            validate_weights("`whoami`")

    def test_rejects_leading_dash(self):
        # Round 2 regression: values like '--help' or '-x' would be
        # reinterpreted by argparse as options rather than the value of
        # --weights, altering CLI behavior even though they match the
        # character class. Leading hyphens must be rejected.
        with pytest.raises(ValueError, match="Invalid WEIGHTS"):
            validate_weights("--help")
        with pytest.raises(ValueError, match="Invalid WEIGHTS"):
            validate_weights("-x")
        with pytest.raises(ValueError, match="Invalid WEIGHTS"):
            validate_weights("--foo")

    def test_allows_interior_hyphens(self):
        # Interior hyphens (e.g. negative weights or ranges) remain valid
        assert validate_weights("weight-a=0.5,weight-b=0.5")


class TestValidatePayloadPath:
    def test_valid_path(self, tmp_path):
        (tmp_path / "input.vcf").write_text("data")
        result = validate_payload_path("input.vcf", tmp_path)
        assert result.name == "input.vcf"

    def test_blocks_traversal(self, tmp_path):
        with pytest.raises(ValueError, match="Path traversal"):
            validate_payload_path("../../etc/passwd", tmp_path)

    def test_blocks_absolute_path(self, tmp_path):
        with pytest.raises(ValueError, match="Path traversal"):
            validate_payload_path("/etc/passwd", tmp_path)

    def test_allows_subdirectory(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "data.txt").write_text("data")
        result = validate_payload_path("subdir/data.txt", tmp_path)
        assert result.name == "data.txt"
