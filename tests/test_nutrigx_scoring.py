"""Unit tests for nutrigx_harness.score_nutrigx_verdict — every rubric branch."""

from __future__ import annotations

from datetime import UTC, datetime

from clawbio_bench.core import ExecutionResult
from clawbio_bench.harnesses.nutrigx_harness import score_nutrigx_verdict


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
        cmd=["python", "nutrigx_advisor.py"],
        cwd="/tmp",
        timeout_seconds=60,
    )


def _mk_analysis(**overrides) -> dict:
    base = {
        "report_exists": True,
        "result_json_exists": True,
        "result_json_valid": True,
        "risk_scores": {},
        "snp_calls": {},
        "panel_coverage": None,
        "domains_assessed": None,
        "elevated_domains": [],
        "repro_artifacts": {
            "commands_sh": False,
            "environment_yml": False,
            "checksums_txt": False,
            "provenance_json": False,
        },
        "commands_sh_content": "",
        "warnings": [],
        "errors": [],
    }
    base.update(overrides)
    return base


class TestScoreCorrectness:
    def test_score_within_tolerance(self):
        gt = {
            "FINDING_CATEGORY": "score_correct",
            "GROUND_TRUTH_DOMAIN": "folate",
            "GROUND_TRUTH_SCORE": "7.5",
            "SCORE_TOLERANCE": "0.5",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(risk_scores={"folate": {"score": 7.4, "category": "Elevated"}})
        verdict = score_nutrigx_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "score_correct"

    def test_score_outside_tolerance(self):
        gt = {
            "FINDING_CATEGORY": "score_correct",
            "GROUND_TRUTH_DOMAIN": "folate",
            "GROUND_TRUTH_SCORE": "7.5",
            "SCORE_TOLERANCE": "0.5",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(risk_scores={"folate": {"score": 3.0, "category": "Low"}})
        verdict = score_nutrigx_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "score_incorrect"

    def test_domain_missing(self):
        gt = {
            "FINDING_CATEGORY": "score_correct",
            "GROUND_TRUTH_DOMAIN": "caffeine",
            "GROUND_TRUTH_SCORE": "5.0",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(risk_scores={"folate": {"score": 3.0}})
        verdict = score_nutrigx_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "score_incorrect"


class TestReproducibilityBundle:
    def test_all_artifacts_present(self):
        gt = {"FINDING_CATEGORY": "repro_functional", "EXPECTED_EXIT_CODE": "0"}
        analysis = _mk_analysis(
            repro_artifacts={
                "commands_sh": True,
                "environment_yml": True,
                "checksums_txt": True,
                "provenance_json": True,
            },
            commands_sh_content="#!/bin/bash\nset -e\necho hello\n",
        )
        verdict = score_nutrigx_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "repro_functional"

    def test_missing_artifact(self):
        gt = {"FINDING_CATEGORY": "repro_functional", "EXPECTED_EXIT_CODE": "0"}
        analysis = _mk_analysis(
            repro_artifacts={
                "commands_sh": True,
                "environment_yml": False,
                "checksums_txt": True,
                "provenance_json": True,
            },
            commands_sh_content="#!/bin/bash\n",
        )
        verdict = score_nutrigx_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "repro_broken"

    def test_missing_shebang(self):
        gt = {"FINDING_CATEGORY": "repro_functional", "EXPECTED_EXIT_CODE": "0"}
        analysis = _mk_analysis(
            repro_artifacts={
                "commands_sh": True,
                "environment_yml": True,
                "checksums_txt": True,
                "provenance_json": True,
            },
            commands_sh_content="echo hello\n",  # no shebang
        )
        verdict = score_nutrigx_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "repro_broken"


class TestSnpPanelValidation:
    def test_panel_coverage_positive(self):
        gt = {"FINDING_CATEGORY": "snp_valid", "EXPECTED_EXIT_CODE": "0"}
        analysis = _mk_analysis(panel_coverage=12)
        verdict = score_nutrigx_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "snp_valid"

    def test_panel_coverage_zero_with_panel_stderr(self):
        """Non-zero exit with panel-related stderr evidence → snp_valid.

        Requires the tool to have printed at least one panel/SNP keyword on
        stderr so we know the rejection was about the panel, not a random
        crash.
        """
        gt = {"FINDING_CATEGORY": "snp_valid", "EXPECTED_EXIT_CODE": "1"}
        analysis = _mk_analysis(panel_coverage=0)
        verdict = score_nutrigx_verdict(
            gt,
            analysis,
            _mk_execution(
                exit_code=1,
                stderr="ERROR: no SNPs from the NutriGx panel matched the input\n",
            ),
        )
        assert verdict["category"] == "snp_valid"

    def test_panel_coverage_zero_without_panel_stderr_is_not_credited(self):
        """Non-zero exit with NO panel-related stderr must NOT be credited.

        Regression for the Tier-A scoring bug: previously any non-zero exit
        with zero coverage was scored ``snp_valid``, which over-credits
        unrelated crashes (missing file, sys.exit, SIGKILL) as clean panel
        rejection.
        """
        gt = {"FINDING_CATEGORY": "snp_valid", "EXPECTED_EXIT_CODE": "1"}
        analysis = _mk_analysis(panel_coverage=0)
        verdict = score_nutrigx_verdict(
            gt,
            analysis,
            _mk_execution(exit_code=1, stderr="Segmentation fault\n"),
        )
        assert verdict["category"] == "snp_invalid"

    def test_panel_coverage_zero_traceback_is_snp_invalid(self):
        """Traceback on a panel-validation test must score snp_invalid."""
        gt = {"FINDING_CATEGORY": "snp_valid", "EXPECTED_EXIT_CODE": "1"}
        analysis = _mk_analysis(panel_coverage=0)
        verdict = score_nutrigx_verdict(
            gt,
            analysis,
            _mk_execution(
                exit_code=1,
                stderr="Traceback (most recent call last):\n  File 'x.py'\n",
            ),
        )
        assert verdict["category"] == "snp_invalid"

    def test_panel_coverage_zero_tool_does_not_error(self):
        gt = {"FINDING_CATEGORY": "snp_invalid", "EXPECTED_EXIT_CODE": "0"}
        analysis = _mk_analysis(panel_coverage=0)
        verdict = score_nutrigx_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "snp_invalid"


class TestThresholdConsistency:
    def test_category_matches(self):
        gt = {
            "FINDING_CATEGORY": "threshold_consistent",
            "GROUND_TRUTH_DOMAIN": "vitamin_d",
            "GROUND_TRUTH_CATEGORY": "Elevated",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(risk_scores={"vitamin_d": {"score": 8.0, "category": "Elevated"}})
        verdict = score_nutrigx_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "threshold_consistent"

    def test_category_mismatch(self):
        gt = {
            "FINDING_CATEGORY": "threshold_consistent",
            "GROUND_TRUTH_DOMAIN": "vitamin_d",
            "GROUND_TRUTH_CATEGORY": "Elevated",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis(risk_scores={"vitamin_d": {"score": 8.0, "category": "Moderate"}})
        verdict = score_nutrigx_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "threshold_mismatch"


class TestCrashHandling:
    def test_unexpected_traceback(self):
        gt = {
            "FINDING_CATEGORY": "score_correct",
            "EXPECTED_EXIT_CODE": "0",
        }
        analysis = _mk_analysis()
        exec_result = _mk_execution(exit_code=1, stderr="Traceback (most recent call last):\n")
        verdict = score_nutrigx_verdict(gt, analysis, exec_result)
        assert verdict["category"] == "harness_error"
