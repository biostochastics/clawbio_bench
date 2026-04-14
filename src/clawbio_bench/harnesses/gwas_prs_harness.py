#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ClawBio GWAS-PRS Benchmark Harness
====================================
Validates the gwas-prs skill (polygenic risk score calculator) against
analytically derived ground truth. Each test case provides a synthetic
23andMe-format genotype file and a PGS scoring file subset with
hand-calculated expected PRS, Z-score, and percentile.

Uses Model B: directory with ground_truth.txt + input.txt payload.

Rubric (7 categories + harness_error):
  score_exact_match        — PRS matches expected within tolerance
  percentile_correct       — Percentile within tolerance (score may differ due to coverage)
  coverage_correctly_flagged — Low-coverage score correctly skipped or warned
  score_incorrect          — PRS outside tolerance of expected value
  percentile_incorrect     — Percentile outside tolerance
  coverage_not_flagged     — Low-coverage score reported without warning
  missing_output           — Expected output files not produced
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "gwas-prs"
BENCHMARK_VERSION = "0.1.0"

RUBRIC_CATEGORIES = [
    "score_exact_match",
    "percentile_correct",
    "coverage_correctly_flagged",
    "score_incorrect",
    "percentile_incorrect",
    "coverage_not_flagged",
    "missing_output",
    "harness_error",
]

PASS_CATEGORIES = [
    "score_exact_match",
    "percentile_correct",
    "coverage_correctly_flagged",
]

FAIL_CATEGORIES = [
    "score_incorrect",
    "percentile_incorrect",
    "coverage_not_flagged",
    "missing_output",
]

GROUND_TRUTH_REFS = {
    "PGS_CATALOG": "PGS Catalog (https://www.pgscatalog.org), Lambert et al. 2021, Nat Genet",
    "T2D_GWAS_VARIANTS": (
        "Synthetic 8-variant T2D benchmark derived from DIAGRAM/GWAS Catalog "
        "loci (TCF7L2, PPARG, KCNJ11, SLC30A8, CDKN2A/B, IGF2BP2, HHEX). "
        "Betas are approximate ln(OR) from Morris et al. 2012 / Mahajan et al. 2018. "
        "NOT a PGS Catalog entry — PGS000013 is GPS_CAD (Khera 2018, coronary artery "
        "disease, 6.6M variants). Vassy et al. 2014 T2D scores are PGS000031 (62 var), "
        "PGS000032 (20 var), PGS000033 (10 var)."
    ),
    "STANDARD_ADDITIVE": (
        "Standard additive dosage model: PRS = SUM(dosage_i * beta_i), "
        "where dosage is the count of effect alleles (0, 1, or 2)"
    ),
    "PERCENTILE_METHOD": (
        "Percentile via normal CDF: Z = (PRS - mean) / SD, percentile = Phi(Z) * 100"
    ),
}

CATEGORY_LEGEND = {
    "score_exact_match": {
        "color": "#22c55e",
        "label": "PRS exact match",
        "tier": "pass",
    },
    "percentile_correct": {
        "color": "#86efac",
        "label": "Percentile correct",
        "tier": "pass",
    },
    "coverage_correctly_flagged": {
        "color": "#a7f3d0",
        "label": "Low coverage correctly flagged",
        "tier": "pass",
    },
    "score_incorrect": {
        "color": "#ef4444",
        "label": "PRS WRONG",
        "tier": "critical",
    },
    "percentile_incorrect": {
        "color": "#fbbf24",
        "label": "Percentile wrong",
        "tier": "warning",
    },
    "coverage_not_flagged": {
        "color": "#f97316",
        "label": "Low coverage NOT flagged",
        "tier": "warning",
    },
    "missing_output": {
        "color": "#1e1b4b",
        "label": "Output files missing",
        "tier": "critical",
    },
    "harness_error": {
        "color": "#6b7280",
        "label": "Harness error (infra)",
        "tier": "infra",
    },
}

# ---------------------------------------------------------------------------
# Score tolerance
# ---------------------------------------------------------------------------

SCORE_TOLERANCE = 1e-4
PERCENTILE_TOLERANCE = 0.5  # percentage points


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


def parse_prs_results(results_json_path: Path) -> list[dict[str, Any]]:
    """Parse the PRS results JSON file produced by gwas_prs.py."""
    if not results_json_path.exists():
        return []
    try:
        with open(results_json_path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


# ---------------------------------------------------------------------------
# Scoring logic
# ---------------------------------------------------------------------------


def score_prs_verdict(
    ground_truth: dict[str, Any],
    results: list[dict[str, Any]],
    exit_code: int,
) -> dict[str, Any]:
    """Score tool output against ground truth.

    Ground truth fields:
        EXPECTED_PRS: float — expected raw PRS
        EXPECTED_PERCENTILE: float or "SKIP" — expected percentile
        EXPECTED_COVERAGE: str — e.g. "8/8" or "5/12"
        EXPECTED_CATEGORY: str — expected risk category or "SKIP"
        PGS_ID: str — which PGS score was tested
        FINDING_CATEGORY: str — expected verdict category
        SCORE_TOLERANCE: float (optional, default 1e-4)
    """
    finding_category = ground_truth.get("FINDING_CATEGORY", "score_exact_match")
    expected_prs = ground_truth.get("EXPECTED_PRS")
    expected_percentile = ground_truth.get("EXPECTED_PERCENTILE")
    expected_coverage = ground_truth.get("EXPECTED_COVERAGE")
    pgs_id = ground_truth.get("PGS_ID", "unknown")
    tol = float(ground_truth.get("SCORE_TOLERANCE", SCORE_TOLERANCE))

    details: dict[str, Any] = {
        "pgs_id": pgs_id,
        "expected_prs": expected_prs,
        "expected_percentile": expected_percentile,
        "expected_coverage": expected_coverage,
    }

    if exit_code != 0:
        # Non-zero exit is always missing_output — even for coverage tests.
        # A crash should never be mistaken for a deliberate low-coverage skip.
        return {
            "category": "missing_output",
            "rationale": f"Tool exited with code {exit_code}",
            "details": details,
        }

    if not results:
        if finding_category == "coverage_correctly_flagged":
            return {
                "category": "coverage_correctly_flagged",
                "rationale": "No results produced (coverage below threshold, correctly skipped)",
                "details": details,
            }
        return {
            "category": "missing_output",
            "rationale": "No PRS results JSON found or empty",
            "details": details,
        }

    target = None
    for r in results:
        if r.get("pgs_id") == pgs_id:
            target = r
            break

    if target is None:
        if finding_category == "coverage_correctly_flagged":
            return {
                "category": "coverage_correctly_flagged",
                "rationale": f"{pgs_id} correctly excluded from results (low coverage)",
                "details": details,
            }
        return {
            "category": "missing_output",
            "rationale": f"PGS ID {pgs_id} not found in results",
            "details": details,
        }

    raw_prs = target.get("raw_score")
    raw_pct = target.get("percentile")
    observed_used = target.get("variants_used", 0)
    observed_total = target.get("variants_total", 0)
    observed_coverage = f"{observed_used}/{observed_total}"

    # Coerce observed values to float — tool JSON may emit strings or ints
    observed_prs: float | None = None
    if raw_prs is not None:
        with contextlib.suppress(TypeError, ValueError):
            observed_prs = float(raw_prs)
    observed_percentile: float | None = None
    if raw_pct is not None:
        with contextlib.suppress(TypeError, ValueError):
            observed_percentile = float(raw_pct)

    details["observed_prs"] = observed_prs
    details["observed_percentile"] = observed_percentile
    details["observed_coverage"] = observed_coverage

    if finding_category == "coverage_correctly_flagged":
        return {
            "category": "coverage_not_flagged",
            "rationale": (
                f"Expected score to be flagged/skipped for low coverage, "
                f"but got result with coverage {observed_coverage}"
            ),
            "details": details,
        }

    if expected_prs is not None and expected_prs != "SKIP":
        exp = float(expected_prs)
        if observed_prs is None:
            return {
                "category": "score_incorrect",
                "rationale": f"No numeric raw_score in results (got {raw_prs!r})",
                "details": details,
            }
        if abs(observed_prs - exp) > tol:
            return {
                "category": "score_incorrect",
                "rationale": (
                    f"PRS mismatch: expected {exp:.6f}, "
                    f"got {observed_prs:.6f} (delta {abs(observed_prs - exp):.6f}, "
                    f"tolerance {tol})"
                ),
                "details": details,
            }

    if expected_percentile is not None and expected_percentile != "SKIP":
        exp_pct = float(expected_percentile)
        ptol = float(ground_truth.get("PERCENTILE_TOLERANCE", PERCENTILE_TOLERANCE))
        if observed_percentile is None:
            return {
                "category": "percentile_incorrect",
                "rationale": "No percentile in results",
                "details": details,
            }
        if abs(observed_percentile - exp_pct) > ptol:
            return {
                "category": "percentile_incorrect",
                "rationale": (
                    f"Percentile mismatch: expected {exp_pct:.1f}%, "
                    f"got {observed_percentile:.1f}% "
                    f"(delta {abs(observed_percentile - exp_pct):.1f}pp, "
                    f"tolerance {ptol}pp)"
                ),
                "details": details,
            }

    if expected_coverage is not None and expected_coverage != "SKIP":
        if observed_coverage != expected_coverage:
            return {
                "category": "score_incorrect",
                "rationale": (
                    f"Coverage mismatch: expected {expected_coverage}, got {observed_coverage}"
                ),
                "details": details,
            }

    prs_str = f"{observed_prs:.6f}" if observed_prs is not None else "N/A"
    pct_str = f"{observed_percentile:.1f}%" if observed_percentile is not None else "N/A"
    return {
        "category": finding_category,
        "rationale": (
            f"PRS={prs_str} (expected {expected_prs}), "
            f"percentile={pct_str}, "
            f"coverage={observed_coverage}"
        ),
        "details": details,
    }


# ---------------------------------------------------------------------------
# Main harness function
# ---------------------------------------------------------------------------


def run_single_gwas_prs(
    repo_path: Path,
    commit_sha: str,
    test_case_path: Path,
    ground_truth: dict[str, Any],
    payload_path: Path | None,
    output_base: Path,
    commit_meta: dict[str, Any],
) -> dict[str, Any]:
    """Execute gwas-prs for one (commit, test_case) pair and return verdict."""
    tc_name = test_case_path.name if test_case_path.is_dir() else test_case_path.stem
    run_output_dir = output_base / commit_meta.get("short", commit_sha[:9]) / tc_name
    run_output_dir.mkdir(parents=True, exist_ok=True)

    input_path = payload_path or test_case_path
    pgs_id = ground_truth.get("PGS_ID", "unknown")
    report_dir = run_output_dir / "tool_output"
    report_dir.mkdir(parents=True, exist_ok=True)

    tool_candidates = [
        repo_path / "skills" / "gwas-prs" / "gwas_prs.py",
        repo_path / "skills 2" / "gwas-prs" / "gwas_prs.py",
    ]
    tool_path = None
    for candidate in tool_candidates:
        if candidate.exists():
            tool_path = candidate
            break

    if tool_path is None:
        return harness_core.harness_error_verdict(
            tc_name,
            commit_meta,
            FileNotFoundError("gwas_prs.py not found in skills/ or skills 2/"),
            ground_truth=ground_truth,
        )

    cmd = [
        sys.executable,
        str(tool_path),
        "--input",
        str(input_path),
        "--pgs-id",
        pgs_id,
        "--output",
        str(report_dir),
    ]

    try:
        execution = harness_core.capture_execution(
            cmd=cmd,
            cwd=repo_path,
            timeout=120,
        )
    except Exception as exc:
        return harness_core.harness_error_verdict(
            tc_name,
            commit_meta,
            exc,
            ground_truth=ground_truth,
        )

    harness_core.save_execution_logs(execution, run_output_dir)

    results_json_path = report_dir / "prs_results.json"
    variants_csv_path = report_dir / "prs_variants.csv"

    results = parse_prs_results(results_json_path)

    report_analysis: dict[str, Any] = {
        "results_count": len(results),
        "exit_code": execution.exit_code,
    }
    if results:
        r = results[0]
        report_analysis["observed_prs"] = r.get("raw_score")
        report_analysis["observed_percentile"] = r.get("percentile")
        report_analysis["observed_category"] = r.get("risk_category")
        report_analysis["variants_used"] = r.get("variants_used")
        report_analysis["variants_total"] = r.get("variants_total")

    verdict = score_prs_verdict(ground_truth, results, execution.exit_code)

    outputs = {
        "prs_results_json": harness_core.artifact_info(results_json_path),
        "prs_variants_csv": harness_core.artifact_info(variants_csv_path),
        "prs_report_md": harness_core.artifact_info(report_dir / "prs_report.md"),
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
        report_analysis=report_analysis,
        verdict=verdict,
        driver_path=(
            test_case_path / "ground_truth.txt" if test_case_path.is_dir() else test_case_path
        ),
        payload_path=input_path,
    )

    harness_core.save_verdict(verdict_doc, run_output_dir)
    return verdict_doc


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    harness_core.run_harness_main(
        benchmark_name=BENCHMARK_NAME,
        benchmark_version=BENCHMARK_VERSION,
        default_inputs_dir="gwas_prs",
        run_single_fn=run_single_gwas_prs,
        rubric_categories=RUBRIC_CATEGORIES,
        pass_categories=PASS_CATEGORIES,
        fail_categories=FAIL_CATEGORIES,
        ground_truth_refs=GROUND_TRUTH_REFS,
        category_legend=CATEGORY_LEGEND,
        description="ClawBio GWAS-PRS Benchmark Harness",
    )


if __name__ == "__main__":
    main()
