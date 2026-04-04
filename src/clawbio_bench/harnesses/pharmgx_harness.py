#!/usr/bin/env python3
"""
ClawBio PharmGx Reporter Benchmark Harness
============================================
Ported from standalone clawbio-pgx-benchmark/run_benchmark.py.
Tests pharmgx_reporter.py with 18 synthetic 23andMe-format inputs.

Uses Model A: self-contained .txt files with # KEY: value headers.

Rubric (6 categories):
  correct_determinate      — Right phenotype, right drug classification
  correct_indeterminate    — Correctly returns indeterminate
  incorrect_determinate    — Wrong phenotype (false Normal)
  incorrect_indeterminate  — Indeterminate when answer IS determinable
  omission                 — Drug silently missing from report
  disclosure_failure       — Warning on stderr but NOT in report body
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "pharmgx-reporter"
BENCHMARK_VERSION = "1.1.0"

RUBRIC_CATEGORIES = [
    "correct_determinate",
    "correct_indeterminate",
    "incorrect_determinate",
    "incorrect_indeterminate",
    "omission",
    "disclosure_failure",
    "harness_error",
]

PASS_CATEGORIES = ["correct_determinate", "correct_indeterminate"]
FAIL_CATEGORIES = [
    "incorrect_determinate",
    "incorrect_indeterminate",
    "omission",
    "disclosure_failure",
]

GROUND_TRUTH_REFS = {
    "CPIC_OPIOID": "CPIC Guideline for CYP2D6 and Opioid Therapy, v3.0 (2023)",
    "CPIC_FLUOROPYRIMIDINE": "CPIC Guideline for DPYD and Fluoropyrimidines, v3.0 (2018, updated 2023)",
    "CPIC_IRINOTECAN": "CPIC Guideline for UGT1A1 and Irinotecan, v2.0 (2020)",
    "CPIC_WARFARIN": "CPIC Guideline for CYP2C9/VKORC1 and Warfarin, v2.0 (2017)",
    "CPIC_TACROLIMUS": "CPIC Guideline for CYP3A5 and Tacrolimus, v2.0 (2015, updated 2021)",
    "CPIC_THIOPURINE": "CPIC Guideline for TPMT/NUDT15 and Thiopurines, v2.0 (2018, updated 2024)",
    "FDA_CODEINE": "FDA Boxed Warning: Codeine in CYP2D6 Ultra-rapid Metabolizers (2017)",
    "PHARMVAR_CYP2D6": "PharmVar CYP2D6 Allele Definitions (accessed 2026-04-03)",
}

CATEGORY_LEGEND = {
    "correct_determinate": {"color": "#22c55e", "label": "Correct (determinate)"},
    "correct_indeterminate": {"color": "#86efac", "label": "Correct (indeterminate)"},
    "incorrect_determinate": {"color": "#ef4444", "label": "WRONG (false Normal)"},
    "incorrect_indeterminate": {"color": "#fbbf24", "label": "Unnecessary indeterminate"},
    "omission": {"color": "#1e1b4b", "label": "Drug MISSING from report"},
    "disclosure_failure": {"color": "#f97316", "label": "Warning on stderr only"},
    "harness_error": {"color": "#9ca3af", "label": "Harness infrastructure error"},
}


# ---------------------------------------------------------------------------
# Report Analyzers (ported from run_benchmark.py)
# ---------------------------------------------------------------------------


def analyze_report(report_path):
    """Parse report.md for gene profiles, drug classifications, warnings."""
    analysis = {
        "gene_profiles": {},
        "drug_classifications": {},
        "warnings_in_report": [],
        "data_quality_warning_present": False,
        "disclaimer_present": False,
        "warfarin_present": False,
        "warfarin_classification": None,
    }
    if not report_path.exists():
        analysis["report_exists"] = False
        return analysis
    analysis["report_exists"] = True
    text = report_path.read_text(errors="replace")

    # Gene profiles table
    in_gene_table = False
    gene_header_indices = {}
    for line in text.split("\n"):
        if "Gene" in line and "Diplotype" in line and "Phenotype" in line and "|" in line:
            raw_headers = line.split("|")
            gene_header_indices = {}
            for i, h in enumerate(raw_headers):
                h_stripped = h.strip()
                if h_stripped:
                    gene_header_indices[h_stripped] = i
            in_gene_table = True
            continue
        if in_gene_table and line.startswith("|"):
            raw_cells = line.split("|")
            non_empty = [c.strip() for c in raw_cells if c.strip()]
            if non_empty and re.match(r"^[-:]+$", non_empty[0]):
                continue
            if len(raw_cells) >= 4:
                gene_idx = gene_header_indices.get("Gene")
                dipl_idx = gene_header_indices.get("Diplotype")
                phen_idx = gene_header_indices.get("Phenotype")
                if gene_idx is not None and dipl_idx is not None and phen_idx is not None:
                    gene = raw_cells[gene_idx].strip() if gene_idx < len(raw_cells) else ""
                    diplotype = raw_cells[dipl_idx].strip() if dipl_idx < len(raw_cells) else ""
                    phenotype = raw_cells[phen_idx].strip() if phen_idx < len(raw_cells) else ""
                else:
                    cells = [c.strip() for c in raw_cells if c.strip()]
                    if len(cells) < 3:
                        continue
                    gene, diplotype, phenotype = cells[0], cells[1], cells[2]
                if gene and gene != "Gene":
                    analysis["gene_profiles"][gene] = {
                        "diplotype": diplotype,
                        "phenotype": phenotype,
                    }
        elif in_gene_table and not line.startswith("|"):
            in_gene_table = False

    # Drug classifications table
    in_drug_table = False
    for line in text.split("\n"):
        if "Drug" in line and ("Classification" in line or "Status" in line) and "|" in line:
            in_drug_table = True
            continue
        if in_drug_table and line.startswith("|"):
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if cells and re.match(r"^[-:]+$", cells[0]):
                continue
            if len(cells) >= 2:
                drug_name = cells[0]
                classification = cells[-1].lower() if cells[-1] else ""
                for cat in ["standard", "caution", "avoid", "indeterminate"]:
                    if cat in classification:
                        analysis["drug_classifications"][drug_name] = cat
                        break
        elif in_drug_table and not line.startswith("|"):
            in_drug_table = False

    # Warfarin
    if "Warfarin" in analysis["drug_classifications"]:
        analysis["warfarin_present"] = True
        analysis["warfarin_classification"] = analysis["drug_classifications"]["Warfarin"]
    elif "warfarin" in text.lower():
        analysis["warfarin_present"] = True

    # Warnings — extract DQW body for disclosure_failure scoring (Crush/Gemini review)
    if "DATA QUALITY WARNING" in text:
        analysis["data_quality_warning_present"] = True
        dqw_match = re.search(r"DATA QUALITY WARNING\s*\n\n(.*?)(?=\n---|\n##)", text, re.DOTALL)
        if dqw_match:
            analysis["warnings_in_report"].append(dqw_match.group(1).strip())
    if "research and educational" in text.lower() or "not a medical device" in text.lower():
        analysis["disclaimer_present"] = True
    for term in [
        "structural variant",
        "cannot interpret",
        "CNV",
        "copy number",
        "TA-repeat",
        "gene duplication",
        "ultrarapid",
        "not assessed",
    ]:
        if term.lower() in text.lower():
            analysis["warnings_in_report"].append(f"report_mentions: {term}")

    return analysis


def analyze_stderr(stderr_text):
    warnings = []
    for line in stderr_text.split("\n"):
        line = line.strip()
        if "WARNING" in line or "warning" in line:
            warnings.append(line)
    return warnings


def analyze_result_json(result_json_path):
    analysis = {
        "exists": False,
        "valid_json": False,
        "has_tuple_keys": False,
        "warfarin_in_results": False,
        "drug_count": 0,
        "error": None,
    }
    if not result_json_path.exists():
        return analysis
    analysis["exists"] = True
    try:
        with open(result_json_path) as f:
            data = json.load(f)
        analysis["valid_json"] = True
        if "drug_results" in data:
            for _cat, drugs in data["drug_results"].items():
                analysis["drug_count"] += len(drugs) if isinstance(drugs, list) else 0
                if isinstance(drugs, list):
                    for d in drugs:
                        if isinstance(d, dict) and d.get("drug", "").lower() == "warfarin":
                            analysis["warfarin_in_results"] = True
    except json.JSONDecodeError as e:
        analysis["error"] = f"JSONDecodeError: {e}"
    except TypeError as e:
        analysis["error"] = f"TypeError (likely tuple keys): {e}"
        analysis["has_tuple_keys"] = True
    except Exception as e:
        analysis["error"] = str(e)
    return analysis


# ---------------------------------------------------------------------------
# Scoring Engine (ported verbatim from run_benchmark.py)
# ---------------------------------------------------------------------------


def _phenotype_matches(observed, expected):
    obs = observed.lower().strip()
    exp = expected.lower().strip()
    if not obs or not exp:
        return False
    _KEY_TERMS = [
        "normal metabolizer",
        "intermediate metabolizer",
        "poor metabolizer",
        "ultrarapid metabolizer",
        "normal function",
        "intermediate function",
        "poor function",
        "decreased function",
        "non-expressor",
        "expressor",
        "indeterminate",
        "not genotyped",
        "not_tested",
    ]
    for term in _KEY_TERMS:
        pattern = r"(?<!\bnot\s)" + re.escape(term)
        if re.search(pattern, obs) and re.search(pattern, exp):
            return True
    if len(obs) < 40 and obs in exp:
        return True
    return bool(len(exp) < 40 and exp in obs)


def _gene_relevant_warnings(stderr_warnings, target_gene):
    if not target_gene or target_gene == "N/A":
        return stderr_warnings
    return [w for w in stderr_warnings if target_gene.lower() in w.lower()]


def score_pgx_verdict(
    ground_truth, report_analysis, stderr_warnings, result_json_analysis, exit_code
):
    """Score a single (commit, input) pair against the 6-category rubric."""
    gt = ground_truth
    ra = report_analysis
    expected_category = gt.get("FINDING_CATEGORY", "")
    target_gene = gt.get("TARGET_GENE", "")
    expected_exit = int(gt.get("EXPECTED_EXIT_CODE", "0"))
    expected_phenotype = gt.get("GROUND_TRUTH_PHENOTYPE", "")
    gt_behavior = gt.get("GROUND_TRUTH_BEHAVIOR", "").lower()
    hazard_drug = gt.get("HAZARD_DRUG", "").lower()

    gene_data = (
        ra.get("gene_profiles", {}).get(target_gene, {})
        if target_gene and target_gene != "N/A"
        else {}
    )
    observed_phenotype = gene_data.get("phenotype", "NOT_IN_REPORT")
    gene_warnings = _gene_relevant_warnings(stderr_warnings, target_gene)

    details = {
        "observed_phenotype": observed_phenotype,
        "expected_phenotype": expected_phenotype,
        "target_gene": target_gene,
        "exit_code": exit_code,
        "stderr_warnings_total": len(stderr_warnings),
        "report_exists": ra.get("report_exists", False),
    }

    if expected_exit != 0:
        if exit_code == expected_exit:
            return {
                "category": "correct_determinate",
                "rationale": f"Tool correctly exited with code {exit_code}",
                "details": details,
            }
        return {
            "category": "incorrect_determinate",
            "rationale": f"Expected exit {expected_exit}, got {exit_code}",
            "details": details,
        }

    if exit_code != 0:
        if (
            expected_category == "omission"
            and ra.get("report_exists")
            and hazard_drug == "warfarin"
        ):
            return {
                "category": "omission",
                "rationale": f"Tool crashed (exit {exit_code}) — warfarin tuple JSON bug",
                "details": details,
            }
        return {
            "category": "incorrect_determinate",
            "rationale": f"Tool crashed with exit code {exit_code}",
            "details": details,
        }

    if not ra.get("report_exists"):
        return {
            "category": "incorrect_determinate",
            "rationale": "No report generated",
            "details": details,
        }

    if hazard_drug == "warfarin" and expected_category == "omission":
        if not ra.get("warfarin_present"):
            return {
                "category": "omission",
                "rationale": "Warfarin silently absent from report",
                "details": details,
            }

    if expected_category == "correct_determinate":
        if _phenotype_matches(observed_phenotype, expected_phenotype):
            return {
                "category": "correct_determinate",
                "rationale": f"Correct phenotype: {observed_phenotype}",
                "details": details,
            }
        return {
            "category": "incorrect_determinate",
            "rationale": f"Wrong: {observed_phenotype} (expected: {expected_phenotype})",
            "details": details,
        }

    if expected_category == "correct_indeterminate":
        if any(
            t in observed_phenotype.lower()
            for t in ["indeterminate", "not genotyped", "not_tested"]
        ):
            return {
                "category": "correct_indeterminate",
                "rationale": f"Correctly indeterminate: {observed_phenotype}",
                "details": details,
            }
        if "normal" in observed_phenotype.lower():
            return {
                "category": "incorrect_determinate",
                "rationale": f"False Normal: {observed_phenotype}",
                "details": details,
            }
        return {
            "category": "incorrect_indeterminate",
            "rationale": f"Unexpected for indeterminate: {observed_phenotype}",
            "details": details,
        }

    if expected_category == "disclosure_failure":
        has_stderr = len(gene_warnings) > 0 or len(stderr_warnings) > 0
        has_report = (
            ra.get("data_quality_warning_present")
            or any(target_gene.lower() in w.lower() for w in ra.get("warnings_in_report", []))
            if target_gene and target_gene != "N/A"
            else False
        )
        if has_stderr and not has_report:
            return {
                "category": "disclosure_failure",
                "rationale": f"Warnings on stderr not in report for {target_gene}",
                "details": details,
            }
        if any(t in gt_behavior for t in ["cnv", "copy number", "gene duplication"]):
            cnv_in_report = any(
                t in w.lower()
                for w in ra.get("warnings_in_report", [])
                for t in ["cnv", "copy number", "duplication"]
            )
            if not cnv_in_report:
                return {
                    "category": "disclosure_failure",
                    "rationale": f"No CNV limitation disclosed for {target_gene}",
                    "details": details,
                }
        if _phenotype_matches(observed_phenotype, expected_phenotype):
            return {
                "category": "correct_determinate",
                "rationale": f"Tool fixed this issue: {observed_phenotype}",
                "details": details,
            }
        return {
            "category": "disclosure_failure",
            "rationale": f"Disclosure failure for {target_gene}",
            "details": details,
        }

    if expected_category == "incorrect_indeterminate":
        if any(t in observed_phenotype.lower() for t in ["indeterminate", "unknown"]):
            return {
                "category": "incorrect_indeterminate",
                "rationale": f"Unnecessary indeterminate: {observed_phenotype}",
                "details": details,
            }
        if _phenotype_matches(observed_phenotype, expected_phenotype):
            return {
                "category": "correct_determinate",
                "rationale": f"Tool fixed this: {observed_phenotype}",
                "details": details,
            }
        return {
            "category": "incorrect_determinate",
            "rationale": f"Wrong: {observed_phenotype}",
            "details": details,
        }

    if expected_category == "incorrect_determinate":
        if _phenotype_matches(observed_phenotype, expected_phenotype):
            return {
                "category": "correct_determinate",
                "rationale": f"Tool fixed this: {observed_phenotype}",
                "details": details,
            }
        return {
            "category": "incorrect_determinate",
            "rationale": f"Incorrect as expected: {observed_phenotype}",
            "details": details,
        }

    if expected_category == "omission":
        if hazard_drug and hazard_drug != "n/a":
            drug_present = any(
                hazard_drug in k.lower() for k in ra.get("drug_classifications", {})
            )
            if drug_present:
                return {
                    "category": "correct_determinate",
                    "rationale": f"Omission fixed: {hazard_drug} now in report",
                    "details": details,
                }
        return {
            "category": "omission",
            "rationale": f"Drug omission for {hazard_drug}",
            "details": details,
        }

    if _phenotype_matches(observed_phenotype, expected_phenotype):
        return {
            "category": "correct_determinate",
            "rationale": f"Phenotype matches: {observed_phenotype}",
            "details": details,
        }
    return {
        "category": "incorrect_determinate",
        "rationale": f"Unmatched: {observed_phenotype} vs {expected_phenotype}",
        "details": details,
    }


# ---------------------------------------------------------------------------
# Single Run Executor
# ---------------------------------------------------------------------------


def run_single_pharmgx(
    repo_path: Path,
    commit_sha: str,
    test_case_path: Path,
    ground_truth: dict,
    payload_path: Path | None,
    output_base: Path,
    commit_meta: dict,
) -> dict:
    """Execute pharmgx_reporter.py for one (commit, input) pair."""
    # Model A: test_case_path IS the input file
    input_path = payload_path or test_case_path
    tc_name = input_path.stem
    commit_short = commit_sha[:8]
    run_output_dir = output_base / commit_short / tc_name
    report_dir = run_output_dir / "tool_output"
    report_dir.mkdir(parents=True, exist_ok=True)

    tool_path = repo_path / "skills" / "pharmgx-reporter" / "pharmgx_reporter.py"
    timeout = int(ground_truth.get("TIMEOUT", "60"))

    cmd = [
        sys.executable,
        str(tool_path),
        "--input",
        str(input_path),
        "--output",
        str(report_dir),
        "--no-enrich",
    ]

    fallback_cmd = [
        sys.executable,
        str(tool_path),
        "--input",
        str(input_path),
        "--output",
        str(report_dir),
    ]

    execution = harness_core.capture_execution(
        cmd=cmd,
        cwd=repo_path,
        timeout=timeout,
        fallback_cmd=fallback_cmd,
    )
    harness_core.save_execution_logs(execution, run_output_dir)

    report_md = report_dir / "report.md"
    result_json_path = report_dir / "result.json"
    report_analysis = analyze_report(report_md)
    stderr_warnings = analyze_stderr(execution.stderr)
    result_json_analysis = analyze_result_json(result_json_path)

    verdict = score_pgx_verdict(
        ground_truth,
        report_analysis,
        stderr_warnings,
        result_json_analysis,
        execution.exit_code,
    )

    outputs = {
        "report_md": harness_core.artifact_info(report_md),
        "result_json": harness_core.artifact_info(result_json_path),
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
        driver_path=input_path,
        payload_path=input_path,
    )

    harness_core.save_verdict(verdict_doc, run_output_dir)
    return verdict_doc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    harness_core.run_harness_main(
        benchmark_name=BENCHMARK_NAME,
        benchmark_version=BENCHMARK_VERSION,
        default_inputs_dir="pharmgx",
        run_single_fn=run_single_pharmgx,
        rubric_categories=RUBRIC_CATEGORIES,
        pass_categories=PASS_CATEGORIES,
        fail_categories=FAIL_CATEGORIES,
        ground_truth_refs=GROUND_TRUTH_REFS,
        category_legend=CATEGORY_LEGEND,
        description="ClawBio PharmGx Reporter Benchmark Harness",
        glob_pattern="*.txt",
    )


if __name__ == "__main__":
    main()
