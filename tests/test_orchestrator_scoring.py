"""Unit tests for orchestrator_harness.score_routing_verdict — every rubric branch."""

from __future__ import annotations

from datetime import UTC, datetime

from clawbio_bench.core import ExecutionResult
from clawbio_bench.harnesses.orchestrator_harness import score_routing_verdict


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
        cmd=["python", "orchestrator.py"],
        cwd="/tmp",
        timeout_seconds=30,
    )


def _mk_analysis(**overrides) -> dict:
    base = {
        "selected_skill": None,
        "detection_method": None,
        "is_stub": None,
        "is_executable": None,
        "warning_emitted": False,
        "error_message": None,
        "available_skills": [],
        "exit_code": 0,
        "raw_stdout_excerpt": "",
        "json_parsed": False,
        "list_skills_output": False,
    }
    base.update(overrides)
    return base


class TestCorrectRouting:
    def test_routed_to_executable(self):
        gt = {
            "GROUND_TRUTH_SKILL": "equity-scorer",
            "GROUND_TRUTH_EXECUTABLE": "true",
            "GROUND_TRUTH_ROUTING_METHOD": "extension",
            "EXPECTED_EXIT_CODE": "0",
            "FINDING_CATEGORY": "routed_correct",
        }
        analysis = _mk_analysis(
            selected_skill="equity-scorer",
            is_stub=False,
            is_executable=True,
            json_parsed=True,
        )
        verdict = score_routing_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "routed_correct"

    def test_routed_wrong(self):
        gt = {
            "GROUND_TRUTH_SKILL": "equity-scorer",
            "GROUND_TRUTH_ROUTING_METHOD": "extension",
            "EXPECTED_EXIT_CODE": "0",
            "FINDING_CATEGORY": "routed_correct",
        }
        analysis = _mk_analysis(
            selected_skill="pharmgx-reporter",
            is_stub=False,
            is_executable=True,
            json_parsed=True,
        )
        verdict = score_routing_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "routed_wrong"


class TestStubRouting:
    # Post-P0 decoupling (2026-04-04): stub-ness must be declared by the
    # test case's ``GROUND_TRUTH_EXECUTABLE`` field, not inferred from a
    # stale harness-internal set or from the tool's own stdout. Tests
    # below explicitly pin ``GROUND_TRUTH_EXECUTABLE: false`` for
    # vcf-annotator (a confirmed stub at ClawBio HEAD 5cf83c5).

    def test_stub_with_warning(self):
        gt = {
            "GROUND_TRUTH_SKILL": "vcf-annotator",
            "GROUND_TRUTH_EXECUTABLE": "false",
            "GROUND_TRUTH_ROUTING_METHOD": "keyword",
            "EXPECTED_EXIT_CODE": "0",
            "FINDING_CATEGORY": "stub_warned",
        }
        analysis = _mk_analysis(
            selected_skill="vcf-annotator",
            is_stub=True,
            is_executable=False,
            warning_emitted=True,
            json_parsed=True,
        )
        verdict = score_routing_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "stub_warned"

    def test_stub_silent(self):
        gt = {
            "GROUND_TRUTH_SKILL": "vcf-annotator",
            "GROUND_TRUTH_EXECUTABLE": "false",
            "GROUND_TRUTH_ROUTING_METHOD": "keyword",
            "EXPECTED_EXIT_CODE": "0",
            "FINDING_CATEGORY": "stub_silent",
        }
        analysis = _mk_analysis(
            selected_skill="vcf-annotator",
            is_stub=True,
            is_executable=False,
            warning_emitted=False,
            json_parsed=True,
        )
        verdict = score_routing_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "stub_silent"

    def test_tool_stub_override_ignored_when_ground_truth_executable(self):
        """Regression test (2026-04-04): the tool's own ``stub=True``
        signal must NOT override the per-test ``GROUND_TRUTH_EXECUTABLE``
        declaration. If a tool mislabels its own skill, the verdict
        must not be corrupted."""
        gt = {
            "GROUND_TRUTH_SKILL": "pharmgx-reporter",
            "GROUND_TRUTH_EXECUTABLE": "true",
            "GROUND_TRUTH_ROUTING_METHOD": "keyword",
            "EXPECTED_EXIT_CODE": "0",
            "FINDING_CATEGORY": "routed_correct",
        }
        analysis = _mk_analysis(
            selected_skill="pharmgx-reporter",
            is_stub=True,  # tool claims stub, but ground truth says executable
            is_executable=False,
            warning_emitted=False,
            json_parsed=True,
        )
        verdict = score_routing_verdict(gt, analysis, _mk_execution())
        assert verdict["category"] == "routed_correct"
        # The observed_is_stub mismatch is preserved in details for audit
        assert verdict["details"].get("observed_is_stub") is True


class TestUnroutable:
    def test_unroutable_handled_clean_error(self):
        gt = {
            "GROUND_TRUTH_SKILL": "",
            "GROUND_TRUTH_ROUTING_METHOD": "error",
            "EXPECTED_EXIT_CODE": "1",
            "FINDING_CATEGORY": "unroutable_handled",
        }
        analysis = _mk_analysis(
            selected_skill=None,
            error_message="Could not determine skill",
            exit_code=1,
        )
        verdict = score_routing_verdict(gt, analysis, _mk_execution(exit_code=1))
        assert verdict["category"] == "unroutable_handled"

    def test_unroutable_crash(self):
        gt = {
            "GROUND_TRUTH_SKILL": "",
            "GROUND_TRUTH_ROUTING_METHOD": "error",
            "EXPECTED_EXIT_CODE": "1",
            "FINDING_CATEGORY": "unroutable_handled",
        }
        analysis = _mk_analysis(selected_skill=None)
        exec_result = _mk_execution(exit_code=1, stderr="Traceback (most recent call last):\n")
        verdict = score_routing_verdict(gt, analysis, exec_result)
        assert verdict["category"] == "unroutable_crash"

    def test_tool_routed_when_should_not(self):
        gt = {
            "GROUND_TRUTH_SKILL": "",
            "GROUND_TRUTH_ROUTING_METHOD": "error",
            "EXPECTED_EXIT_CODE": "1",
            "FINDING_CATEGORY": "unroutable_handled",
        }
        analysis = _mk_analysis(selected_skill="equity-scorer")
        verdict = score_routing_verdict(gt, analysis, _mk_execution(exit_code=0))
        assert verdict["category"] == "routed_wrong"


class TestNoSkillDetected:
    def test_no_skill_but_expected_one(self):
        gt = {
            "GROUND_TRUTH_SKILL": "equity-scorer",
            "GROUND_TRUTH_ROUTING_METHOD": "extension",
            "EXPECTED_EXIT_CODE": "0",
            "FINDING_CATEGORY": "routed_correct",
        }
        analysis = _mk_analysis(selected_skill=None)
        verdict = score_routing_verdict(gt, analysis, _mk_execution(exit_code=0))
        assert verdict["category"] == "routed_wrong"
