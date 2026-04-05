"""Regression tests for findings surfaced during review passes.

Each test pins a specific bug that was found and fixed, so the same class of
regression cannot silently reappear. Covered areas:
  - rich non-TTY fallback and --list duplicate-header suppression
  - harness_error save path (persistence + chain of custody)
  - phenotype whitespace normalization and case-insensitive negation
  - validate_verdict_schema type checks (test_case.name, commit).
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from clawbio_bench.core import (
    VerdictSchemaError,
    _record_harness_error,
    validate_verdict_schema,
)
from clawbio_bench.harnesses.pharmgx_harness import _has_negated, _phenotype_matches


class TestPhenotypeWhitespaceNormalization:
    """Regression: _phenotype_matches substring fallback treated
    'not normal metabolizer' (space) and 'not\\tnormal metabolizer' (tab) as
    non-matching because the lookbehind regex rejected both via the negation
    check, then the substring fallback compared raw strings byte-for-byte.
    """

    def test_space_vs_tab_negation_matches(self):
        assert _phenotype_matches(
            "not normal metabolizer",
            "not\tnormal metabolizer",
        )

    def test_double_space_vs_tab_negation_matches(self):
        assert _phenotype_matches(
            "not  normal metabolizer",
            "not\tnormal metabolizer",
        )

    def test_opposite_negation_still_rejected(self):
        """Normalization must not regress the 'not X' vs 'X' rejection."""
        assert not _phenotype_matches("not normal metabolizer", "normal metabolizer")

    def test_expressor_non_expressor_still_rejected(self):
        """Regex-layer rejection for `-` boundary must still hold."""
        assert not _phenotype_matches("non-expressor", "expressor")


class TestHasNegatedCaseInsensitivity:
    """Regression: _has_negated called regex.search without
    regex.IGNORECASE, so raw text containing 'NOT' or 'Not' bypassed the
    check. Callers happened to lowercase first but the helper was not
    self-consistent."""

    def test_uppercase_not_detected(self):
        assert _has_negated("NOT normal metabolizer", "normal metabolizer")

    def test_mixed_case_not_detected(self):
        assert _has_negated("Not normal metabolizer", "normal metabolizer")


class TestValidateVerdictSchemaTypeChecks:
    """Regression: the minimum-contract validator checked that test_case is
    a dict with a 'name' key, but never checked that name was a string or that
    commit was a dict. Malformed shapes leaked through, especially via the
    harness_error strict-mode escape hatch."""

    def test_rejects_non_string_test_case_name(self):
        doc = {
            "verdict": {"category": "harness_error", "rationale": "boom"},
            "test_case": {"name": 123},  # int, not str
            "commit": {"sha": "abc"},
        }
        with pytest.raises(VerdictSchemaError, match="test_case.name must be str"):
            validate_verdict_schema(doc)

    def test_rejects_non_dict_commit(self):
        doc = {
            "verdict": {"category": "harness_error", "rationale": "boom"},
            "test_case": {"name": "t1"},
            "commit": "not-a-dict",  # str, not dict
        }
        with pytest.raises(VerdictSchemaError, match="commit must be a dict"):
            validate_verdict_schema(doc)

    def test_harness_error_still_exempt_from_strict_full_schema(self):
        """The escape hatch still works: well-formed minimal harness_error
        docs pass strict=True."""
        doc = {
            "verdict": {"category": "harness_error", "rationale": "boom"},
            "test_case": {"name": "t1"},
            "commit": {"sha": "abc"},
        }
        validate_verdict_schema(doc, strict=True)  # no raise


class TestRecordHarnessErrorPersistsToDisk:
    """Regression: run_benchmark_matrix appended harness_error verdicts to
    the in-memory list but never called save_verdict, so failure-path
    verdicts had no per-case verdict.json with a SHA-256 self-hash. The
    --verify flow silently ignored them."""

    def test_persists_verdict_to_canonical_path(self, tmp_path):
        all_verdicts: list[dict] = []
        commit_sha = "abc123def456"
        _record_harness_error(
            all_verdicts,
            output_base=tmp_path,
            commit_sha=commit_sha,
            commit_short=commit_sha[:8],
            commit_meta={"date": "2026-04-04", "message": "test"},
            tc_name="fixture_01",
            exception=RuntimeError("boom"),
        )
        # In-memory append
        assert len(all_verdicts) == 1
        assert all_verdicts[0]["verdict"]["category"] == "harness_error"

        # On-disk canonical verdict.json
        verdict_path = tmp_path / commit_sha / "fixture_01" / "verdict.json"
        assert verdict_path.exists()

        # Has self-hash
        saved = json.loads(verdict_path.read_bytes())
        assert "_verdict_sha256" in saved
        assert len(saved["_verdict_sha256"]) == 64

    def test_verify_mode_now_covers_harness_errors(self, tmp_path):
        """End-to-end: after _record_harness_error, verify_verdict_file
        returns ok."""
        from clawbio_bench.core import verify_verdict_file

        all_verdicts: list[dict] = []
        _record_harness_error(
            all_verdicts,
            output_base=tmp_path,
            commit_sha="abc123",
            commit_short="abc12345",
            commit_meta={},
            tc_name="fixture_02",
            exception=ValueError("oops"),
        )
        verdict_path = tmp_path / "abc123" / "fixture_02" / "verdict.json"
        ok, msg = verify_verdict_file(verdict_path)
        assert ok, msg


class TestRichFallbackIsByteStable:
    """Regression: get_console() used to return a neutered Console
    (force_terminal=False, no_color=True) for non-TTY streams, but
    rich.table.Table still emits box-drawing characters in that mode, so
    tables rendered visually in piped output. Fix: return None on non-TTY
    so renderers take the plain print() path."""

    def test_list_piped_output_contains_no_box_drawing(self):
        """Running `clawbio-bench --list` in a non-TTY subprocess must
        produce plain ASCII output without box-drawing characters."""
        result = subprocess.run(
            [sys.executable, "-m", "clawbio_bench", "--list"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        # Box-drawing characters rich tables use
        for ch in ("┏", "┳", "┓", "━", "┃", "╇", "┡", "│", "└", "┘"):
            assert ch not in result.stdout, (
                f"Piped --list output should not contain rich table "
                f"character {ch!r}. Got:\n{result.stdout}"
            )

    def test_no_rich_flag_matches_non_tty(self):
        """`--no-rich --list` and piped `--list` must produce the same
        stdout (modulo non-meaningful whitespace)."""
        piped = subprocess.run(
            [sys.executable, "-m", "clawbio_bench", "--list"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        norich = subprocess.run(
            [sys.executable, "-m", "clawbio_bench", "--list", "--no-rich"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert piped.returncode == 0
        assert norich.returncode == 0
        assert piped.stdout == norich.stdout

    def test_list_has_no_duplicate_header(self):
        """Regression: `--no-rich --list` printed the heading twice
        because both cli.py and render_harness_list emitted a title."""
        result = subprocess.run(
            [sys.executable, "-m", "clawbio_bench", "--list", "--no-rich"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        # "registered harnesses" should appear exactly once
        assert result.stdout.count("registered harnesses") == 1
