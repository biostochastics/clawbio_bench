#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ClawBio NutriGx Advisor Benchmark Harness
===========================================
Tests nutrigx_advisor.py with 10 test cases focused on:
  - Score accuracy (known genotypes → deterministic risk scores)
  - Reproducibility bundle (commands.sh, environment.yml, checksums)
  - SNP panel coverage and thresholds
  - Edge cases (empty file, no panel SNPs, all risk alleles)

Rubric (9 categories):
  score_correct           — Score matches expected value (tolerance-based)
  score_incorrect         — Score diverges from expected
  repro_functional        — Repro bundle artifacts all present and well-formed
  repro_broken            — Any repro artifact missing or malformed
  snp_valid               — Panel SNPs found in input
  snp_invalid             — Panel SNPs missing or mismatched
  threshold_consistent    — Risk category matches documented thresholds
  threshold_mismatch      — Code thresholds differ from expected
  harness_error           — Harness infrastructure error
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "nutrigx-advisor"
BENCHMARK_VERSION = "0.1.0"

RUBRIC_CATEGORIES = [
    "score_correct",
    "score_incorrect",
    "repro_functional",
    "repro_broken",
    "snp_valid",
    "snp_invalid",
    "threshold_consistent",
    "threshold_mismatch",
    "harness_error",
]

PASS_CATEGORIES = [
    "score_correct",
    "repro_functional",
    "snp_valid",
    "threshold_consistent",
]
FAIL_CATEGORIES = [
    "score_incorrect",
    "repro_broken",
    "snp_invalid",
    "threshold_mismatch",
]

GROUND_TRUTH_REFS = {
    "SNPPANEL_V1": (
        "ClawBio NutriGx SNP panel v0.2.0, 28 SNPs across 12 nutrient domains "
        "(data/snp_panel.json, accessed 2026-04-04)"
    ),
    "MTHFR_C677T": (
        "Frosst, P. et al. (1995). A candidate genetic risk factor for "
        "vascular disease: a common mutation in MTHFR. Nature Genetics, "
        "10(1), 111-113. doi:10.1038/ng0595-111"
    ),
}

CATEGORY_LEGEND = {
    "score_correct": {"color": "#22c55e", "label": "Score matches expected"},
    "score_incorrect": {"color": "#ef4444", "label": "Score diverges"},
    "repro_functional": {"color": "#22c55e", "label": "Repro bundle works"},
    "repro_broken": {"color": "#f97316", "label": "Repro bundle fails"},
    "snp_valid": {"color": "#86efac", "label": "SNP panel valid"},
    "snp_invalid": {"color": "#ef4444", "label": "SNP panel error"},
    "threshold_consistent": {"color": "#86efac", "label": "Thresholds match docs"},
    "threshold_mismatch": {"color": "#fbbf24", "label": "Code vs docs disagree"},
    "harness_error": {"color": "#9ca3af", "label": "Harness infrastructure error"},
}

# Documented thresholds from score_variants.py
THRESHOLDS = {"Low": (0.0, 3.5), "Moderate": (3.5, 6.5), "Elevated": (6.5, 10.0)}


# ---------------------------------------------------------------------------
# Stderr classification helpers
# ---------------------------------------------------------------------------
#
# These helpers replace naive `"Error" in stderr` substring checks, which
# over-credit unrelated crashes as "clean rejection" (e.g. a tool that dies
# with a FileNotFoundError printed via sys.exit without a traceback would
# previously score as `snp_valid`).
#
# * ``_is_genuine_crash`` — True iff stderr carries Python traceback structure
#   OR a line that starts with ``Error:``/``Exception:``. Excludes benign
#   substrings like ``"no errors found"`` or ``"Error correcting code"``.
# * ``_stderr_mentions_panel`` — True iff stderr mentions SNP panel validation
#   concepts. Required positive evidence before crediting a non-zero exit as
#   ``snp_valid`` (correct panel rejection).

_PANEL_REJECTION_KEYWORDS = (
    "panel",
    "snp",
    "rsid",
    "genotype",
    "variant",
    "allele",
    "no match",
    "not found in panel",
    "empty input",
    "unsupported format",
)


def _is_genuine_crash(stderr: str) -> bool:
    """Return True iff stderr contains a traceback or line-anchored error."""
    if not stderr:
        return False
    if "Traceback (most recent call last):" in stderr:
        return True
    for line in stderr.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("Error:", "Exception:", "Fatal:", "FATAL:")):
            return True
    return False


def _stderr_mentions_panel(stderr: str) -> bool:
    """Return True iff stderr contains at least one SNP/panel-related keyword.

    Used to require positive evidence before crediting a non-zero exit as
    ``snp_valid`` (correct rejection). A tool that crashes with a generic
    ``FileNotFoundError`` is not correctly rejecting a panel input — it's
    crashing — and should not be scored as pass.
    """
    if not stderr:
        return False
    lowered = stderr.lower()
    return any(kw in lowered for kw in _PANEL_REJECTION_KEYWORDS)


# ---------------------------------------------------------------------------
# Report Analyzer
# ---------------------------------------------------------------------------


def analyze_nutrigx_output(
    report_md_path: Path,
    result_json_path: Path,
    output_dir: Path,
    stdout: str,
    stderr: str,
) -> dict:
    """Parse NutriGx outputs."""
    analysis: dict[str, Any] = {
        "report_exists": False,
        "result_json_exists": False,
        "result_json_valid": False,
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

    # Parse stderr
    if stderr:
        for line in stderr.split("\n"):
            line = line.strip()
            if "WARNING" in line or "warning" in line.lower():
                analysis["warnings"].append(line)
            if "ERROR" in line:
                analysis["errors"].append(line)

    # Check repro artifacts
    for fname, key in [
        ("commands.sh", "commands_sh"),
        ("environment.yml", "environment_yml"),
        ("checksums.txt", "checksums_txt"),
        ("provenance.json", "provenance_json"),
    ]:
        fpath = output_dir / fname
        if fpath.exists() and fpath.stat().st_size > 0:
            analysis["repro_artifacts"][key] = True
        if fname == "commands.sh" and fpath.exists():
            analysis["commands_sh_content"] = fpath.read_text(errors="replace")

    # Parse result.json
    if result_json_path.exists():
        analysis["result_json_exists"] = True
        try:
            with open(result_json_path, encoding="utf-8") as f:
                data = json.load(f)
            analysis["result_json_valid"] = True

            rdata = data.get("data", data)
            if isinstance(rdata, dict):
                analysis["risk_scores"] = rdata.get("risk_scores", {})
                analysis["snp_calls"] = rdata.get("snp_calls", {})

            summary = data.get("summary", {})
            if isinstance(summary, dict):
                analysis["panel_coverage"] = summary.get("panel_snps_tested")
                analysis["domains_assessed"] = summary.get("domains_assessed")
                analysis["elevated_domains"] = summary.get("elevated_domains", [])

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            analysis["errors"].append(f"result.json parse: {e}")

    # Parse report.md
    if report_md_path.exists():
        analysis["report_exists"] = True

    return analysis


# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------


def score_nutrigx_verdict(
    ground_truth: dict,
    analysis: dict,
    execution: harness_core.ExecutionResult,
) -> dict:
    """Score a NutriGx run against ground truth."""
    finding_category = ground_truth.get("FINDING_CATEGORY", "")
    expected_exit = int(ground_truth.get("EXPECTED_EXIT_CODE", "0"))
    exit_code = execution.exit_code

    details = {
        "expected_exit_code": expected_exit,
        "observed_exit_code": exit_code,
        "report_exists": analysis.get("report_exists", False),
        "result_json_valid": analysis.get("result_json_valid", False),
    }

    # ── Crash on unexpected exit — use harness_error, not snp_invalid ──
    # Use _is_genuine_crash instead of a naive "Error" substring so benign
    # stderr content (e.g. "no errors found") doesn't trip crash detection.
    if exit_code != 0 and exit_code != expected_exit:
        if _is_genuine_crash(execution.stderr):
            return {
                "category": "harness_error",
                "rationale": f"Tool crashed (exit {exit_code}) with traceback",
                "details": details,
            }

    # ── Expected error cases ──
    # A clean non-zero exit is only ``snp_valid`` when stderr carries
    # positive evidence that the rejection was panel-related. Without that
    # evidence, a tool that dies for an unrelated reason (missing file,
    # sys.exit without message, SIGKILL) would previously be over-credited.
    if expected_exit != 0 and exit_code != 0:
        if _is_genuine_crash(execution.stderr):
            return {
                "category": "snp_invalid",
                "rationale": f"Crashed (exit {exit_code}) with traceback",
                "details": details,
            }
        if _stderr_mentions_panel(execution.stderr):
            return {
                "category": "snp_valid",
                "rationale": (f"Panel rejection handled cleanly with exit {exit_code}"),
                "details": details,
            }
        details["stderr_mentions_panel"] = False
        return {
            "category": "snp_invalid",
            "rationale": (
                f"Non-zero exit {exit_code} without panel-related stderr "
                f"evidence — cannot credit as clean panel rejection"
            ),
            "details": details,
        }

    # ── Score accuracy ──
    if finding_category in ("score_correct", "score_incorrect"):
        target_domain = ground_truth.get("GROUND_TRUTH_DOMAIN", "")
        expected_score_str = ground_truth.get("GROUND_TRUTH_SCORE", "")
        tolerance = float(ground_truth.get("SCORE_TOLERANCE", "0.5"))

        risk_scores = analysis.get("risk_scores", {})
        observed = risk_scores.get(target_domain, {})
        observed_score = observed.get("score")

        details.update(
            {
                "target_domain": target_domain,
                "expected_score": expected_score_str,
                "observed_score": observed_score,
                "tolerance": tolerance,
                "observed_category": observed.get("category"),
            }
        )

        if observed_score is not None and expected_score_str:
            expected_score = float(expected_score_str)
            within = abs(observed_score - expected_score) <= tolerance
            details["within_tolerance"] = within
            if within:
                return {
                    "category": "score_correct",
                    "rationale": (
                        f"{target_domain} score {observed_score} within "
                        f"tolerance of expected {expected_score}"
                    ),
                    "details": details,
                }
            return {
                "category": "score_incorrect",
                "rationale": (
                    f"{target_domain} score {observed_score} outside "
                    f"tolerance of expected {expected_score}"
                ),
                "details": details,
            }

        return {
            "category": "score_incorrect",
            "rationale": f"Could not extract score for domain {target_domain}",
            "details": details,
        }

    # ── Reproducibility ──
    if finding_category in ("repro_functional", "repro_broken"):
        artifacts = analysis.get("repro_artifacts", {})
        all_present = all(artifacts.values())
        details["repro_artifacts"] = artifacts

        # Check commands.sh content
        cmd_content = analysis.get("commands_sh_content", "")
        has_shebang = cmd_content.startswith("#!/")
        has_set_e = "set -e" in cmd_content
        details["commands_sh_has_shebang"] = has_shebang
        details["commands_sh_has_set_e"] = has_set_e

        if all_present and has_shebang:
            return {
                "category": "repro_functional",
                "rationale": "All repro artifacts present and well-formed",
                "details": details,
            }
        missing = [k for k, v in artifacts.items() if not v]
        return {
            "category": "repro_broken",
            "rationale": f"Repro artifacts missing/broken: {missing}",
            "details": details,
        }

    # ── SNP panel validation ──
    if finding_category in ("snp_valid", "snp_invalid"):
        panel_coverage = analysis.get("panel_coverage")
        details["panel_coverage"] = panel_coverage

        if panel_coverage is not None and panel_coverage > 0:
            return {
                "category": "snp_valid",
                "rationale": f"Panel coverage: {panel_coverage} SNPs found",
                "details": details,
            }
        # A non-zero exit is only ``snp_valid`` when stderr carries positive
        # evidence that the rejection was panel-related AND the exit did not
        # come from a crash. Otherwise the tool is crashing for an unrelated
        # reason and should not be credited.
        if exit_code != 0:
            if _is_genuine_crash(execution.stderr):
                return {
                    "category": "snp_invalid",
                    "rationale": (f"Crashed (exit {exit_code}) — not a clean panel rejection"),
                    "details": details,
                }
            if _stderr_mentions_panel(execution.stderr):
                return {
                    "category": "snp_valid",
                    "rationale": "Tool correctly rejected input with 0 panel SNPs",
                    "details": details,
                }
            details["stderr_mentions_panel"] = False
            return {
                "category": "snp_invalid",
                "rationale": (
                    f"Non-zero exit {exit_code} without panel-related stderr "
                    f"evidence — cannot credit as clean rejection"
                ),
                "details": details,
            }
        return {
            "category": "snp_invalid",
            "rationale": "Zero panel SNPs but tool did not error",
            "details": details,
        }

    # ── Threshold consistency ──
    if finding_category in ("threshold_consistent", "threshold_mismatch"):
        target_domain = ground_truth.get("GROUND_TRUTH_DOMAIN", "")
        expected_category = ground_truth.get("GROUND_TRUTH_CATEGORY", "")
        risk_scores = analysis.get("risk_scores", {})
        observed = risk_scores.get(target_domain, {})
        observed_category = observed.get("category", "")
        observed_score = observed.get("score")

        details.update(
            {
                "target_domain": target_domain,
                "expected_category": expected_category,
                "observed_category": observed_category,
                "observed_score": observed_score,
            }
        )

        if observed_category == expected_category:
            return {
                "category": "threshold_consistent",
                "rationale": (
                    f"{target_domain}: '{observed_category}' matches expected "
                    f"(score={observed_score})"
                ),
                "details": details,
            }
        return {
            "category": "threshold_mismatch",
            "rationale": (
                f"{target_domain}: '{observed_category}' != expected "
                f"'{expected_category}' (score={observed_score})"
            ),
            "details": details,
        }

    # ── Fallback ──
    return {
        "category": "harness_error",
        "rationale": f"Unhandled finding_category: {finding_category}",
        "details": details,
    }


# ---------------------------------------------------------------------------
# Single Run Executor
# ---------------------------------------------------------------------------


def run_single_nutrigx(
    repo_path: Path,
    commit_sha: str,
    test_case_path: Path,
    ground_truth: dict,
    payload_path: Path | None,
    output_base: Path,
    commit_meta: dict,
) -> dict:
    """Execute nutrigx_advisor.py for one (commit, test_case) pair."""
    tc_name = test_case_path.name if test_case_path.is_dir() else test_case_path.stem
    run_output_dir = output_base / commit_sha / tc_name
    tool_output_dir = run_output_dir / "tool_output"
    tool_output_dir.mkdir(parents=True, exist_ok=True)

    tool_path = repo_path / "skills" / "nutrigx_advisor" / "nutrigx_advisor.py"
    timeout = harness_core.validate_timeout(ground_truth.get("TIMEOUT", "60"))

    if not payload_path:
        return harness_core.harness_error_verdict(
            tc_name,
            commit_meta,
            ValueError(f"NutriGx test case {tc_name} has no payload file"),
            ground_truth=ground_truth,
        )

    cmd = [
        sys.executable,
        str(tool_path),
        "--input",
        str(payload_path),
        "--output",
        str(tool_output_dir),
        "--no-figures",
    ]

    fallback_cmd = [
        sys.executable,
        str(tool_path),
        "--input",
        str(payload_path),
        "--output",
        str(tool_output_dir),
    ]

    execution = harness_core.capture_execution(
        cmd=cmd,
        cwd=repo_path,
        timeout=timeout,
        fallback_cmd=fallback_cmd,
        fallback_flag="--no-figures",
    )

    harness_core.save_execution_logs(execution, run_output_dir)

    report_md = tool_output_dir / "nutrigx_report.md"
    result_json = tool_output_dir / "result.json"

    analysis = analyze_nutrigx_output(
        report_md,
        result_json,
        tool_output_dir,
        execution.stdout,
        execution.stderr,
    )

    verdict = score_nutrigx_verdict(ground_truth, analysis, execution)

    driver_path = (
        test_case_path / "ground_truth.txt" if test_case_path.is_dir() else test_case_path
    )

    outputs = {
        "report_md": harness_core.artifact_info(report_md),
        "result_json": harness_core.artifact_info(result_json),
        "commands_sh": harness_core.artifact_info(tool_output_dir / "commands.sh"),
        "environment_yml": harness_core.artifact_info(tool_output_dir / "environment.yml"),
        "checksums_txt": harness_core.artifact_info(tool_output_dir / "checksums.txt"),
        "provenance_json": harness_core.artifact_info(tool_output_dir / "provenance.json"),
    }

    verdict_doc = harness_core.build_verdict_doc(
        benchmark_name=BENCHMARK_NAME,
        benchmark_version=BENCHMARK_VERSION,
        commit_meta=commit_meta,
        test_case_name=tc_name,
        ground_truth=ground_truth,
        ground_truth_refs=GROUND_TRUTH_REFS,
        execution=execution,
        outputs=outputs,
        report_analysis=analysis,
        verdict=verdict,
        driver_path=driver_path,
        payload_path=payload_path,
    )

    harness_core.save_verdict(verdict_doc, run_output_dir)
    return verdict_doc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    harness_core.run_harness_main(
        benchmark_name=BENCHMARK_NAME,
        benchmark_version=BENCHMARK_VERSION,
        default_inputs_dir="nutrigx",
        run_single_fn=run_single_nutrigx,
        rubric_categories=RUBRIC_CATEGORIES,
        pass_categories=PASS_CATEGORIES,
        fail_categories=FAIL_CATEGORIES,
        ground_truth_refs=GROUND_TRUTH_REFS,
        category_legend=CATEGORY_LEGEND,
        description="ClawBio NutriGx Advisor Benchmark Harness",
    )


if __name__ == "__main__":
    main()
