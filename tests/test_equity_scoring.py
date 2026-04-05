"""Unit tests for equity_harness.score_equity_verdict — every rubric branch."""

from __future__ import annotations

from datetime import UTC, datetime

from clawbio_bench.core import ExecutionResult
from clawbio_bench.harnesses.equity_harness import score_equity_verdict


def _mk_execution(exit_code: int = 0, stderr: str = "") -> ExecutionResult:
    now = datetime.now(UTC)
    return ExecutionResult(
        exit_code=exit_code,
        stdout="",
        stderr=stderr,
        wall_seconds=0.01,
        start_time=now,
        end_time=now,
        used_fallback=False,
        cmd=["python", "equity_scorer.py"],
        cwd="/tmp",
        timeout_seconds=60,
    )


def _mk_analysis(**overrides) -> dict:
    base = {
        "report_exists": True,
        "result_json_exists": True,
        "result_json_valid": True,
        "heim_score": None,
        "heim_components": {},
        "fst_values": {},
        "fst_table_header": None,
        "fst_coverage": None,
        "warnings": [],
        "errors": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# FST scoring
# ---------------------------------------------------------------------------


class TestFstCorrect:
    def test_value_and_label_correct(self):
        gt = {
            "FINDING_CATEGORY": "fst_correct",
            "GROUND_TRUTH_FST": "0.1500",
            "GROUND_TRUTH_FST_ESTIMATOR": "Nei's GST",
            "GROUND_TRUTH_FST_PAIR": "POP_A_vs_POP_B",
            "FST_TOLERANCE": "0.02",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(
            fst_values={"POP_A_vs_POP_B": 0.1495},
            fst_table_header="Nei's GST",
        )
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "fst_correct"

    def test_value_correct_label_wrong(self):
        gt = {
            "FINDING_CATEGORY": "fst_mislabeled",
            "GROUND_TRUTH_FST": "0.1500",
            "GROUND_TRUTH_FST_ESTIMATOR": "Nei's GST",
            "GROUND_TRUTH_FST_PAIR": "POP_A_vs_POP_B",
            "FST_TOLERANCE": "0.02",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(
            fst_values={"POP_A_vs_POP_B": 0.1495},
            fst_table_header="Hudson FST",
        )
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "fst_mislabeled"

    def test_value_outside_tolerance(self):
        gt = {
            "FINDING_CATEGORY": "fst_correct",
            "GROUND_TRUTH_FST": "0.1500",
            "GROUND_TRUTH_FST_ESTIMATOR": "Nei's GST",
            "GROUND_TRUTH_FST_PAIR": "POP_A_vs_POP_B",
            "FST_TOLERANCE": "0.02",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(
            fst_values={"POP_A_vs_POP_B": 0.25},
            fst_table_header="Nei's GST",
        )
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "fst_incorrect"

    def test_no_fst_data(self):
        gt = {
            "FINDING_CATEGORY": "fst_correct",
            "GROUND_TRUTH_FST": "0.15",
            "GROUND_TRUTH_FST_PAIR": "POP_A_vs_POP_B",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(fst_values={}, report_exists=False)
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "fst_incorrect"

    def test_missing_label_is_mislabeled_not_correct(self):
        """Empty/missing estimator label is not a pass (C-3)."""
        gt = {
            "FINDING_CATEGORY": "fst_correct",
            "GROUND_TRUTH_FST": "0.15",
            "GROUND_TRUTH_FST_ESTIMATOR": "Nei's GST",
            "GROUND_TRUTH_FST_PAIR": "POP_A_vs_POP_B",
            "FST_TOLERANCE": "0.02",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(
            fst_values={"POP_A_vs_POP_B": 0.15},
            fst_table_header=None,  # NO label emitted
        )
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "fst_mislabeled"

    def test_multiple_pairs_without_ground_truth_pair_is_harness_error(self):
        """Silent fallback to first pair masks wrong scoring (C-4)."""
        gt = {
            "FINDING_CATEGORY": "fst_correct",
            "GROUND_TRUTH_FST": "0.15",
            "FST_TOLERANCE": "0.02",
            "EXPECTED_EXIT_CODE": "0",
            # NO GROUND_TRUTH_FST_PAIR specified
        }
        analysis = _mk_analysis(
            fst_values={
                "POP_A_vs_POP_B": 0.15,
                "POP_A_vs_POP_C": 0.25,
                "POP_B_vs_POP_C": 0.18,
            },
            fst_table_header="Nei's GST",
        )
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "harness_error"
        assert "disambiguate" in verdict["rationale"].lower()

    def test_single_pair_without_ground_truth_pair_ok(self):
        """Only one pair present → unambiguous, proceed with scoring."""
        gt = {
            "FINDING_CATEGORY": "fst_correct",
            "GROUND_TRUTH_FST": "0.15",
            "GROUND_TRUTH_FST_ESTIMATOR": "Nei's GST",
            "FST_TOLERANCE": "0.02",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(
            fst_values={"POP_A_vs_POP_B": 0.15},
            fst_table_header="Nei's GST",
        )
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "fst_correct"

    def test_ground_truth_pair_mismatch(self):
        """Pair specified but not found → do not fall back silently."""
        gt = {
            "FINDING_CATEGORY": "fst_correct",
            "GROUND_TRUTH_FST": "0.15",
            "GROUND_TRUTH_FST_PAIR": "POP_X_vs_POP_Y",
            "FST_TOLERANCE": "0.02",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(
            fst_values={"POP_A_vs_POP_B": 0.15},
            fst_table_header="Nei's GST",
        )
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        # Should NOT be fst_correct — expected pair not present
        assert verdict["category"] != "fst_correct"


# ---------------------------------------------------------------------------
# HEIM scoring
# ---------------------------------------------------------------------------


class TestHeimBounds:
    def test_heim_in_bounds(self):
        gt = {"FINDING_CATEGORY": "heim_bounded", "EXPECTED_EXIT_CODE": "0"}
        analysis = _mk_analysis(heim_score=75.5)
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "heim_bounded"

    def test_heim_zero_boundary(self):
        gt = {"FINDING_CATEGORY": "heim_bounded", "EXPECTED_EXIT_CODE": "0"}
        analysis = _mk_analysis(heim_score=0.0)
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "heim_bounded"

    def test_heim_hundred_boundary(self):
        gt = {"FINDING_CATEGORY": "heim_bounded", "EXPECTED_EXIT_CODE": "0"}
        analysis = _mk_analysis(heim_score=100.0)
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "heim_bounded"

    def test_heim_above_hundred(self):
        gt = {"FINDING_CATEGORY": "heim_unbounded", "EXPECTED_EXIT_CODE": "0"}
        analysis = _mk_analysis(heim_score=150.0)
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "heim_unbounded"

    def test_heim_negative(self):
        gt = {"FINDING_CATEGORY": "heim_unbounded", "EXPECTED_EXIT_CODE": "0"}
        analysis = _mk_analysis(heim_score=-5.0)
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "heim_unbounded"


# ---------------------------------------------------------------------------
# CSV mode scoring
# ---------------------------------------------------------------------------


class TestCsvHonesty:
    def test_csv_honest(self):
        gt = {
            "FINDING_CATEGORY": "csv_honest",
            "GROUND_TRUTH_FST_COVERAGE": "0.0",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(fst_coverage=0.0, heim_score=60.0)
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "csv_honest"

    def test_csv_inflated(self):
        gt = {
            "FINDING_CATEGORY": "csv_inflated",
            "GROUND_TRUTH_FST_COVERAGE": "0.0",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(fst_coverage=1.0, heim_score=80.0)
        verdict = score_equity_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "csv_inflated"


# ---------------------------------------------------------------------------
# Edge case scoring
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_edge_handled_clean_exit(self):
        gt = {
            "FINDING_CATEGORY": "edge_handled",
            "EXPECTED_EXIT_CODE": "1",
        }
        analysis = _mk_analysis()
        exec_result = _mk_execution(exit_code=1, stderr="Clean error message\n")
        verdict = score_equity_verdict(gt, analysis, exec_result)
        assert verdict["category"] == "edge_handled"

    def test_edge_crash_with_traceback(self):
        gt = {
            "FINDING_CATEGORY": "edge_crash",
            "EXPECTED_EXIT_CODE": "1",
        }
        analysis = _mk_analysis()
        exec_result = _mk_execution(
            exit_code=1, stderr="Traceback (most recent call last):\n  File foo\n"
        )
        verdict = score_equity_verdict(gt, analysis, exec_result)
        assert verdict["category"] == "edge_crash"

    def test_unexpected_crash_even_expected_exit(self):
        """Even with expected exit code, traceback → edge_crash."""
        gt = {
            "FINDING_CATEGORY": "edge_handled",
            "EXPECTED_EXIT_CODE": "1",
        }
        analysis = _mk_analysis()
        exec_result = _mk_execution(exit_code=1, stderr="Traceback (most recent call last):\n")
        verdict = score_equity_verdict(gt, analysis, exec_result)
        assert verdict["category"] == "edge_crash"
