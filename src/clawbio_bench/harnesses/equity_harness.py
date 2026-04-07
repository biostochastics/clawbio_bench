#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ClawBio Equity Scorer Benchmark Harness
=========================================
Tests equity_scorer.py with 14 synthetic test inputs:
  - FST accuracy and label correctness
  - HEIM score bounds
  - Edge cases (.vcf.gz, monomorphic, single-sample)
  - CSV mode inflation

Rubric (10 categories):
  fst_correct     — FST value within tolerance AND correctly labeled
  fst_incorrect   — FST value outside tolerance of expected
  fst_mislabeled  — FST value correct but label says "Hudson FST"
  heim_bounded    — HEIM composite in [0, 100]
  heim_unbounded  — HEIM composite outside [0, 100]
  csv_honest      — CSV mode does not inflate FST coverage
  csv_inflated    — CSV mode reports FST_coverage = 1.0 (no FST computed)
  edge_handled    — Edge case produces clean error/warning, no crash
  edge_crash      — Edge case produces crash/traceback
  harness_error   — Harness infrastructure error
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "equity-scorer"
BENCHMARK_VERSION = "0.1.0"

RUBRIC_CATEGORIES = [
    "fst_correct",
    "fst_incorrect",
    "fst_mislabeled",
    "heim_bounded",
    "heim_unbounded",
    "csv_honest",
    "csv_inflated",
    "edge_handled",
    "edge_crash",
    "harness_error",
]

PASS_CATEGORIES = ["fst_correct", "heim_bounded", "csv_honest", "edge_handled"]
FAIL_CATEGORIES = [
    "fst_incorrect",
    "fst_mislabeled",
    "heim_unbounded",
    "csv_inflated",
    "edge_crash",
]

GROUND_TRUTH_REFS = {
    "NEI_1973": (
        "Nei, M. (1973). Analysis of Gene Diversity in Subdivided "
        "Populations. Proceedings of the National Academy of Sciences, "
        "70(12), 3321-3323."
    ),
    "HUDSON_1992": (
        "Hudson, R.R., Slatkin, M. & Maddison, W.P. (1992). "
        "Estimation of levels of gene flow from DNA sequence data. "
        "Genetics, 132(2), 583-589."
    ),
    "WEIR_COCKERHAM_1984": (
        "Weir, B.S. & Cockerham, C.C. (1984). Estimating F-Statistics "
        "for the Analysis of Population Structure. Evolution, 38(6), "
        "1358-1370."
    ),
}

CATEGORY_LEGEND = {
    "fst_correct": {
        "color": "#22c55e",
        "label": "FST correct + labeled correctly",
        "tier": "pass",
    },
    "fst_incorrect": {
        "color": "#ef4444",
        "label": "FST value outside tolerance",
        "tier": "critical",
    },
    "fst_mislabeled": {
        "color": "#f97316",
        "label": "FST correct, label WRONG",
        "tier": "warning",
    },
    "heim_bounded": {
        "color": "#86efac",
        "label": "HEIM in [0, 100]",
        "tier": "pass",
    },
    "heim_unbounded": {
        "color": "#ef4444",
        "label": "HEIM outside [0, 100]",
        "tier": "warning",
    },
    "csv_honest": {
        "color": "#22c55e",
        "label": "CSV mode honest",
        "tier": "pass",
    },
    "csv_inflated": {
        "color": "#f97316",
        "label": "CSV mode inflated",
        "tier": "warning",
    },
    "edge_handled": {
        "color": "#86efac",
        "label": "Edge case handled",
        "tier": "pass",
    },
    "edge_crash": {
        "color": "#ef4444",
        "label": "Edge case CRASH",
        "tier": "critical",
    },
    "harness_error": {
        "color": "#9ca3af",
        "label": "Harness infrastructure error",
        "tier": "infra",
    },
}


# ---------------------------------------------------------------------------
# Report Analyzer
# ---------------------------------------------------------------------------


def analyze_equity_report(
    report_md_path: Path,
    result_json_path: Path,
    stderr: str,
) -> dict[str, Any]:
    """Parse equity scorer outputs to extract FST, HEIM, and labels."""
    analysis: dict[str, Any] = {
        "report_exists": False,
        "result_json_exists": False,
        "result_json_valid": False,
        "heim_score": None,
        "heim_rating": None,
        "heim_components": {},
        "fst_values": {},
        "fst_table_header": None,
        "fst_colorbar_label": None,
        "het_values": {},
        "n_samples": None,
        "n_populations": None,
        "n_variants": None,
        "warnings": [],
        "errors": [],
        "has_disclaimer": False,
        "population_counts": {},
        "fst_coverage": None,
    }

    # Parse stderr warnings
    if stderr:
        for line in stderr.split("\n"):
            line = line.strip()
            if line and ("WARNING" in line or "warning" in line.lower()):
                analysis["warnings"].append(line)
            if "Error" in line or "Traceback" in line:
                analysis["errors"].append(line)

    # Parse result.json
    if result_json_path.exists():
        analysis["result_json_exists"] = True
        try:
            with open(result_json_path, encoding="utf-8") as f:
                data = json.load(f)
            analysis["result_json_valid"] = True

            # Extract from result.json data section
            # Schema validation: handle unexpected shapes
            if not isinstance(data, dict):
                analysis["errors"].append(
                    f"result.json top-level is {type(data).__name__}, expected dict"
                )
            else:
                rdata = data.get("data", data)
                if not isinstance(rdata, dict):
                    rdata = data
                if isinstance(rdata, dict) and "heim_score" in rdata:
                    analysis["heim_score"] = rdata["heim_score"]
                    analysis["heim_rating"] = rdata.get("rating")
                    analysis["heim_components"] = rdata.get("components", {})
                    analysis["population_counts"] = rdata.get("population_counts", {})
                    analysis["fst_coverage"] = rdata.get("components", {}).get("fst_coverage")
                elif isinstance(rdata, dict) and "heim" in rdata:
                    heim = rdata["heim"]
                    if isinstance(heim, dict):
                        analysis["heim_score"] = heim.get("heim_score")
                        analysis["heim_rating"] = heim.get("rating")
                        analysis["heim_components"] = heim.get("components", {})
                        analysis["population_counts"] = heim.get("population_counts", {})
                        analysis["fst_coverage"] = heim.get("components", {}).get("fst_coverage")

                # FST from result.json
                if isinstance(rdata, dict) and "fst" in rdata:
                    fst_data = rdata["fst"]
                    if isinstance(fst_data, dict):
                        for pair_key, val in fst_data.items():
                            if isinstance(val, (int, float)):
                                analysis["fst_values"][pair_key] = val

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            analysis["errors"].append(f"result.json parse error: {e}")

    # Parse report.md
    if report_md_path.exists():
        analysis["report_exists"] = True
        text = report_md_path.read_text(errors="replace")

        # HEIM score from report
        heim_match = re.search(r"HEIM Equity Score:\s*([\d.]+)/100\s*\((\w+)\)", text)
        if heim_match:
            if analysis["heim_score"] is None:
                analysis["heim_score"] = float(heim_match.group(1))
            analysis["heim_rating"] = heim_match.group(2)

        # Score breakdown table
        for row_match in re.finditer(r"\|\s*([\w\s]+?)\s*\|\s*([\d.]+(?:None)?)\s*\|", text):
            name = row_match.group(1).strip().lower()
            val_str = row_match.group(2).strip()
            if val_str != "None":
                try:
                    val = float(val_str)
                except ValueError:
                    continue
                if "representation" in name:
                    analysis["heim_components"]["representation_index"] = val
                elif "heterozygosity" in name:
                    analysis["heim_components"]["heterozygosity_balance"] = val
                elif "fst coverage" in name:
                    analysis["heim_components"]["fst_coverage"] = val
                    if analysis["fst_coverage"] is None:
                        analysis["fst_coverage"] = val
                elif "geographic" in name:
                    analysis["heim_components"]["geographic_spread"] = val

        # FST table header detection (key mislabeling test)
        fst_header_match = re.search(
            r"\|\s*Comparison\s*\|\s*(.*?FST.*?)\s*\|", text, re.IGNORECASE
        )
        if fst_header_match:
            analysis["fst_table_header"] = fst_header_match.group(1).strip()

        # FST values from report table
        for fst_row in re.finditer(r"\|\s*(\w+)\s+vs\s+(\w+)\s*\|\s*([\d.]+)\s*\|", text):
            pair = f"{fst_row.group(1)}_vs_{fst_row.group(2)}"
            analysis["fst_values"][pair] = float(fst_row.group(3))

        # Sample/pop counts from report header
        samples_match = re.search(r"\*\*Samples\*\*:\s*(\d+)", text)
        if samples_match:
            analysis["n_samples"] = int(samples_match.group(1))
        pops_match = re.search(r"\*\*Populations\*\*:\s*(\d+)", text)
        if pops_match:
            analysis["n_populations"] = int(pops_match.group(1))
        vars_match = re.search(r"\*\*Variants.*?\*\*:\s*(\d+)", text)
        if vars_match:
            analysis["n_variants"] = int(vars_match.group(1))

        # Disclaimer
        if "research and educational" in text.lower() or "not a medical" in text.lower():
            analysis["has_disclaimer"] = True

    return analysis


# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------


def score_equity_verdict(
    ground_truth: dict[str, Any],
    analysis: dict[str, Any],
    execution: harness_core.ExecutionResult,
) -> dict[str, Any]:
    """Score an equity scorer run against ground truth.

    Ground truth headers:
        GROUND_TRUTH_FST: expected FST value (float)
        GROUND_TRUTH_FST_PAIR: specific pair key to check (e.g. POP_A_vs_POP_B)
        GROUND_TRUTH_FST_ESTIMATOR: "Nei's GST" or "Hudson FST"
        GROUND_TRUTH_HEIM: expected HEIM score bound
        GROUND_TRUTH_HET: expected heterozygosity
        FST_TOLERANCE: numerical tolerance for FST comparison
        FINDING_CATEGORY: expected rubric category
        HAZARD_METRIC: description of hazard
        EXPECTED_EXIT_CODE: 0 or non-zero
    """
    finding_category = ground_truth.get("FINDING_CATEGORY", "")
    expected_exit = int(ground_truth.get("EXPECTED_EXIT_CODE", "0"))
    exit_code = execution.exit_code

    details = {
        "expected_exit_code": expected_exit,
        "observed_exit_code": exit_code,
        "report_exists": analysis.get("report_exists", False),
        "result_json_valid": analysis.get("result_json_valid", False),
        "heim_score": analysis.get("heim_score"),
        "fst_table_header": analysis.get("fst_table_header"),
    }

    # ── Crash on unexpected exit ──
    if exit_code != 0 and exit_code != expected_exit and "Traceback" in execution.stderr:
        return {
            "category": "edge_crash",
            "rationale": f"Tool crashed (exit {exit_code}) with traceback",
            "details": details,
        }

    # ── Expected crash/edge cases ──
    if expected_exit != 0:
        if exit_code != 0:
            # Check for genuine crash even when exit code matches expected:
            # a traceback with expected exit code is still a crash.
            if "Traceback" in execution.stderr:
                return {
                    "category": "edge_crash",
                    "rationale": f"Tool crashed (exit {exit_code}) with traceback",
                    "details": details,
                }
            return {
                "category": "edge_handled",
                "rationale": f"Edge case handled with exit {exit_code}",
                "details": details,
            }
        # Tool succeeded when we expected failure
        if exit_code == 0 and finding_category == "edge_crash":
            return {
                "category": "edge_handled",
                "rationale": "Tool handled edge case gracefully (no crash)",
                "details": details,
            }

    # ── FST scoring ──
    if finding_category in ("fst_correct", "fst_incorrect", "fst_mislabeled"):
        expected_fst_str = ground_truth.get("GROUND_TRUTH_FST", "")
        expected_estimator = ground_truth.get("GROUND_TRUTH_FST_ESTIMATOR", "Nei's GST")
        tolerance = float(ground_truth.get("FST_TOLERANCE", "0.02"))

        # Get observed FST — use specific pair key if provided.
        # With multiple pairs, require exact match; falling back to the first
        # pair can silently score the wrong comparison.
        observed_fst = None
        fst_values = analysis.get("fst_values", {})
        expected_pair = ground_truth.get("GROUND_TRUTH_FST_PAIR", "")
        if expected_pair:
            if expected_pair in fst_values:
                observed_fst = fst_values[expected_pair]
            # else: explicit mismatch — fall through with observed_fst=None
        elif len(fst_values) == 1:
            # Only safe to guess the pair when exactly one exists
            observed_fst = next(iter(fst_values.values()))
        elif len(fst_values) > 1:
            # Ambiguous: ground truth didn't specify which pair to check
            details["expected_fst"] = expected_fst_str
            details["fst_values"] = fst_values
            return {
                "category": "harness_error",
                "rationale": (
                    "Multiple FST pairs present but GROUND_TRUTH_FST_PAIR not "
                    f"specified; cannot disambiguate among {list(fst_values)}"
                ),
                "details": details,
            }

        observed_label = analysis.get("fst_table_header", "")

        details.update(
            {
                "expected_fst": expected_fst_str,
                "observed_fst": observed_fst,
                "tolerance": tolerance,
                "expected_estimator": expected_estimator,
                "observed_label": observed_label,
                "fst_values": fst_values,
            }
        )

        if expected_fst_str and observed_fst is not None:
            expected_fst = float(expected_fst_str)
            within_tolerance = abs(observed_fst - expected_fst) <= tolerance
            details["within_tolerance"] = within_tolerance

            if not within_tolerance:
                return {
                    "category": "fst_incorrect",
                    "rationale": (
                        f"FST {observed_fst:.4f} outside tolerance of "
                        f"expected {expected_fst:.4f} (tol={tolerance})"
                    ),
                    "details": details,
                }

            # Value correct — now check label. A MISSING label is NOT the same
            # as a correct label: the tool cannot be credited for labeling
            # when no label was emitted at all.
            if not observed_label:
                details["label_correct"] = False
                return {
                    "category": "fst_mislabeled",
                    "rationale": (
                        f"FST value {observed_fst:.4f} correct, but no "
                        f"estimator label was emitted (expected "
                        f"'{expected_estimator}')"
                    ),
                    "details": details,
                }

            label_correct = expected_estimator.lower() in observed_label.lower()
            details["label_correct"] = label_correct

            if not label_correct:
                return {
                    "category": "fst_mislabeled",
                    "rationale": (
                        f"FST value {observed_fst:.4f} correct, but label "
                        f"'{observed_label}' should be '{expected_estimator}'"
                    ),
                    "details": details,
                }

            return {
                "category": "fst_correct",
                "rationale": (f"FST {observed_fst:.4f} within tolerance and correctly labeled"),
                "details": details,
            }

        # No FST data available — use fst_incorrect, not edge_crash
        if not analysis.get("report_exists"):
            return {
                "category": "fst_incorrect",
                "rationale": "No report generated for FST test",
                "details": details,
            }
        return {
            "category": "fst_incorrect",
            "rationale": "Could not extract FST value from report",
            "details": details,
        }

    # ── HEIM scoring ──
    if finding_category in ("heim_bounded", "heim_unbounded"):
        heim_score = analysis.get("heim_score")
        details["heim_score"] = heim_score
        details["heim_components"] = analysis.get("heim_components", {})

        if heim_score is not None:
            bounded = 0.0 <= heim_score <= 100.0
            details["heim_bounded"] = bounded

            if bounded:
                return {
                    "category": "heim_bounded",
                    "rationale": f"HEIM score {heim_score} in [0, 100]",
                    "details": details,
                }
            return {
                "category": "heim_unbounded",
                "rationale": f"HEIM score {heim_score} outside [0, 100]",
                "details": details,
            }

        if not analysis.get("report_exists"):
            return {
                "category": "edge_crash",
                "rationale": "No report generated for HEIM test",
                "details": details,
            }
        return {
            "category": "heim_unbounded",
            "rationale": "Could not extract HEIM score from output",
            "details": details,
        }

    # ── CSV scoring ──
    if finding_category in ("csv_honest", "csv_inflated"):
        fst_coverage = analysis.get("fst_coverage")
        details["fst_coverage"] = fst_coverage

        if fst_coverage is not None:
            # CSV mode should report fst_coverage = 0.0 since no VCF data
            expected_coverage = float(ground_truth.get("GROUND_TRUTH_FST_COVERAGE", "0.0"))
            # 0.01 epsilon: floating-point tolerance for coverage (distinct from FST_TOLERANCE)
            if fst_coverage <= expected_coverage + 0.01:
                return {
                    "category": "csv_honest",
                    "rationale": (
                        f"CSV mode reports fst_coverage={fst_coverage} "
                        f"(expected ~{expected_coverage})"
                    ),
                    "details": details,
                }
            return {
                "category": "csv_inflated",
                "rationale": (
                    f"CSV mode inflates fst_coverage={fst_coverage} "
                    f"(expected ~{expected_coverage})"
                ),
                "details": details,
            }

        # Check if report exists and HEIM was computed
        if analysis.get("heim_score") is not None:
            return {
                "category": "csv_honest",
                "rationale": "CSV mode produced HEIM score without FST inflation",
                "details": details,
            }
        return {
            "category": "edge_crash",
            "rationale": "Could not determine CSV mode FST coverage",
            "details": details,
        }

    # ── Edge case scoring ──
    if finding_category in ("edge_handled", "edge_crash"):
        if exit_code == 0:
            return {
                "category": "edge_handled",
                "rationale": "Edge case handled without crash",
                "details": details,
            }
        if "Traceback" in execution.stderr:
            return {
                "category": "edge_crash",
                "rationale": f"Edge case caused crash (exit {exit_code})",
                "details": details,
            }
        return {
            "category": "edge_handled",
            "rationale": f"Edge case handled with clean exit {exit_code}",
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


def run_single_equity(
    repo_path: Path,
    commit_sha: str,
    test_case_path: Path,
    ground_truth: dict[str, Any],
    payload_path: Path | None,
    output_base: Path,
    commit_meta: dict[str, Any],
) -> dict[str, Any]:
    """Execute equity_scorer.py for one (commit, test_case) pair."""
    tc_name = test_case_path.name if test_case_path.is_dir() else test_case_path.stem
    run_output_dir = output_base / commit_sha / tc_name
    tool_output_dir = run_output_dir / "tool_output"
    tool_output_dir.mkdir(parents=True, exist_ok=True)

    tool_path = repo_path / "skills" / "equity-scorer" / "equity_scorer.py"
    timeout = harness_core.validate_timeout(ground_truth.get("TIMEOUT", "60"))

    # Build command
    if not payload_path:
        return harness_core.harness_error_verdict(
            tc_name,
            commit_meta,
            ValueError(f"Equity test case {tc_name} has no payload file"),
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

    # Add custom weights if specified (U-2 unbounded test)
    weights = ground_truth.get("WEIGHTS")
    if weights:
        harness_core.validate_weights(weights)
        cmd.extend(["--weights", weights])

    # Add population map if specified — with path traversal check
    pop_map_file = ground_truth.get("POP_MAP_FILE")
    if pop_map_file:
        pop_map_path = harness_core.validate_payload_path(pop_map_file, test_case_path)
        if pop_map_path.exists():
            cmd.extend(["--pop-map", str(pop_map_path)])

    # Fallback without --no-figures for older commits
    fallback_cmd = [
        sys.executable,
        str(tool_path),
        "--input",
        str(payload_path),
        "--output",
        str(tool_output_dir),
    ]
    if weights:
        fallback_cmd.extend(["--weights", weights])
    if pop_map_file:
        pop_map_path = harness_core.validate_payload_path(pop_map_file, test_case_path)
        if pop_map_path.exists():
            fallback_cmd.extend(["--pop-map", str(pop_map_path)])

    # Execute
    execution = harness_core.capture_execution(
        cmd=cmd,
        cwd=repo_path,
        timeout=timeout,
        fallback_cmd=fallback_cmd,
        fallback_flag="--no-figures",
    )

    # Save logs
    harness_core.save_execution_logs(execution, run_output_dir)

    # Analyze outputs
    report_md = tool_output_dir / "report.md"
    result_json = tool_output_dir / "result.json"

    analysis = analyze_equity_report(
        report_md,
        result_json,
        execution.stderr,
    )

    # Score
    verdict = score_equity_verdict(ground_truth, analysis, execution)

    # Build verdict document
    driver_path = (
        test_case_path / "ground_truth.txt" if test_case_path.is_dir() else test_case_path
    )

    outputs = {
        "report_md": harness_core.artifact_info(report_md),
        "result_json": harness_core.artifact_info(result_json),
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
        default_inputs_dir="equity",
        run_single_fn=run_single_equity,
        rubric_categories=RUBRIC_CATEGORIES,
        pass_categories=PASS_CATEGORIES,
        fail_categories=FAIL_CATEGORIES,
        ground_truth_refs=GROUND_TRUTH_REFS,
        category_legend=CATEGORY_LEGEND,
        description="ClawBio Equity Scorer Benchmark Harness",
    )


if __name__ == "__main__":
    main()
