#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ClawBio PharmGx Reporter Benchmark Harness
============================================
Ported from standalone clawbio-pgx-benchmark/run_benchmark.py.
Tests pharmgx_reporter.py with 24 synthetic 23andMe-format inputs covering
CPIC Level 1A gene-drug pairs: CYP2D6/codeine, CYP2C19/clopidogrel, DPYD/
fluoropyrimidines, SLCO1B1/statins, HLA-B*57:01/abacavir, TPMT/thiopurines,
UGT1A1/irinotecan, CYP3A5/tacrolimus, VKORC1+CYP2C9/warfarin, and phasing
ambiguity + GRCh37 reference-mismatch negative controls.

Uses Model A: self-contained .txt files with # KEY: value headers (or YAML
frontmatter in `# ---` fences).

Rubric (7 categories + harness_error):
  correct_determinate        — Right phenotype, right drug classification
  correct_indeterminate      — Correctly returns indeterminate
  scope_honest_indeterminate — Correctly returns Indeterminate for a variant that
                               DTC arrays cannot resolve (CNV, hybrid, phasing).
                               This is correct clinical behavior, not a failure.
  incorrect_determinate      — Wrong phenotype (false Normal)
  incorrect_indeterminate    — Indeterminate when answer IS determinable
  omission                   — Drug silently missing from report
  disclosure_failure         — Warning on stderr but NOT in report body
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import regex  # type: ignore[import-untyped]  # third-party: variable-length lookbehind

from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "pharmgx-reporter"
BENCHMARK_VERSION = "0.1.0"

RUBRIC_CATEGORIES = [
    "correct_determinate",
    "correct_indeterminate",
    "scope_honest_indeterminate",
    "incorrect_determinate",
    "incorrect_indeterminate",
    "omission",
    "disclosure_failure",
    "harness_error",
]

PASS_CATEGORIES = ["correct_determinate", "correct_indeterminate", "scope_honest_indeterminate"]
FAIL_CATEGORIES = [
    "incorrect_determinate",
    "incorrect_indeterminate",
    "omission",
    "disclosure_failure",
]

GROUND_TRUTH_REFS = {
    "CPIC_OPIOID": "CPIC Guideline for CYP2D6 and Opioid Therapy, v3.0 (2023)",
    "CPIC_FLUOROPYRIMIDINE": "CPIC Guideline for DPYD and Fluoropyrimidines (Amstutz et al. 2018, PMID 29152729)",
    "CPIC_IRINOTECAN": "DPWG Guideline for UGT1A1 and Irinotecan; CPIC irinotecan guideline pending (see cpicpgx.org)",
    "CPIC_WARFARIN": "CPIC Guideline for CYP2C9/VKORC1 and Warfarin, v2.0 (2017)",
    "CPIC_TACROLIMUS": "CPIC Guideline for CYP3A5 and Tacrolimus, v2.0 (2015, updated 2021)",
    "CPIC_THIOPURINE": "CPIC Guideline for TPMT/NUDT15 and Thiopurines, v2.0 (2018, updated 2024)",
    "CPIC_CLOPIDOGREL": "CPIC Guideline for CYP2C19 and Clopidogrel, v2.0 (2022)",
    "CPIC_VORICONAZOLE": "CPIC Guideline for CYP2C19 and Voriconazole, v1.0 (2017)",
    "CPIC_STATINS": "CPIC Guideline for SLCO1B1, ABCG2, CYP2C9 and Statin-Associated Musculoskeletal Symptoms, v2.0 (2022)",
    "CPIC_ABACAVIR": "CPIC Guideline for HLA-B and Abacavir (Martin et al. 2012, PMID 22378157; 2014 update PMID 24561393)",
    "FDA_CODEINE": "FDA Boxed Warning: Codeine in CYP2D6 Ultra-rapid Metabolizers (2017)",
    "PHARMVAR_CYP2D6": "PharmVar CYP2D6 Allele Definitions (accessed 2026-04-03)",
    "PHARMVAR_CYP2C19": "PharmVar CYP2C19 Allele Definitions (accessed 2026-04-04)",
    "DPWG_MTHFR": (
        "DPWG Guideline for MTHFR and Folic Acid/Methotrexate "
        "(van der Pol KH et al. 2024, Eur J Hum Genet, PMC10853275, PMID 36056234). "
        "No CPIC guideline exists for MTHFR. ACMG 2013 (PMID 23288205): "
        "lack of evidence for MTHFR polymorphism testing."
    ),
}

CATEGORY_LEGEND = {
    "correct_determinate": {
        "color": "#22c55e",
        "label": "Correct (determinate)",
        "tier": "pass",
    },
    "correct_indeterminate": {
        "color": "#86efac",
        "label": "Correct (indeterminate)",
        "tier": "pass",
    },
    "scope_honest_indeterminate": {
        "color": "#a7f3d0",
        "label": "Scope-honest Indeterminate (DTC limitation, correct behavior)",
        "tier": "advisory",
    },
    "incorrect_determinate": {
        "color": "#ef4444",
        "label": "WRONG (false Normal)",
        "tier": "critical",
    },
    "incorrect_indeterminate": {
        "color": "#fbbf24",
        "label": "Unnecessary indeterminate",
        "tier": "warning",
    },
    "omission": {
        "color": "#1e1b4b",
        "label": "Drug MISSING from report",
        "tier": "critical",
    },
    "disclosure_failure": {
        "color": "#f97316",
        "label": "Warning on stderr only",
        "tier": "warning",
    },
    "harness_error": {
        "color": "#9ca3af",
        "label": "Harness infrastructure error",
        "tier": "infra",
    },
}


# ---------------------------------------------------------------------------
# Report Analyzers (ported from run_benchmark.py)
# ---------------------------------------------------------------------------


def analyze_report(report_path: Path) -> dict[str, Any]:
    """Parse report.md for gene profiles, drug classifications, warnings."""
    analysis: dict[str, Any] = {
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
    gene_header_indices: dict[str, int] = {}
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

    # Warnings — extract DQW body for disclosure_failure scoring
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


def analyze_stderr(stderr_text: str) -> list[str]:
    warnings: list[str] = []
    for line in stderr_text.split("\n"):
        line = line.strip()
        if "WARNING" in line or "warning" in line:
            warnings.append(line)
    return warnings


def analyze_result_json(result_json_path: Path) -> dict[str, Any]:
    analysis: dict[str, Any] = {
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
        with open(result_json_path, encoding="utf-8") as f:
            data = json.load(f)
        analysis["valid_json"] = True
        if "drug_results" in data:
            for drugs in data["drug_results"].values():
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


# Pre-compiled phenotype matching patterns (avoid recompilation per call)
_KEY_TERMS = [
    "normal metabolizer",
    "intermediate metabolizer",
    "poor metabolizer",
    "ultrarapid metabolizer",
    "rapid metabolizer",
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
# Phenotype term matching needs stronger boundary semantics than stdlib `re`
# can express cleanly:
#   1. Python's `\b` treats `-` as a word boundary, so plain `\b(expressor)\b`
#      matches inside "non-expressor". We need to reject preceding `-` too.
#   2. The "not <term>" negation must cover `not<ws>term` for any amount or
#      kind of horizontal whitespace — stdlib lookbehind is fixed-width, so
#      `(?<!\bnot\s)` only handles exactly one space character.
#
# The `regex` package (PyPI) supports variable-length lookbehind and
# Unicode-aware character properties, which lets us express the rule honestly:
#   - `(?<![\p{L}\p{N}_-])` — not preceded by a letter, digit, `_`, or `-`
#   - `(?<!\bnot\h+)` — not preceded by the word "not" + one-or-more horizontal
#     whitespace chars (handles tabs, multiple spaces, non-breaking space)
#   - `(?V1)` pins regex-module version semantics for forward compatibility.
_KEY_PATTERNS = [
    regex.compile(
        r"(?V1)(?<![\p{L}\p{N}_-])(?<!\bnot\h+)" + regex.escape(t),
        flags=regex.IGNORECASE,
    )
    for t in _KEY_TERMS
]

# Prevent false-positive substring matches on long phenotype descriptions
_MAX_SUBSTRING_MATCH_LEN = 40

# Negation prefixes that invert a phenotype term. If one side uses a negation
# the other does not, the phenotypes are opposite and must not match via the
# substring fallback.
#   "not " — handled via a regex with variable-length `\h+` so tabs and
#            multiple spaces between "not" and the term still trip the check.
#   "non-" and "non " — literal, no whitespace variation expected.
_NEGATION_LITERAL_PREFIXES = ("non-", "non ")


def _has_negated(text: str, term: str) -> bool:
    """Return True if `term` appears in `text` preceded by a negation prefix.

    Handles "not <term>" with any horizontal-whitespace separator (single/
    multiple spaces, tabs, non-breaking space) via the regex package's `\\h+`
    class. Literal "non-" and "non " variants are checked with simple
    substring membership since they don't have whitespace ambiguity.

    Case-insensitive via ``regex.IGNORECASE`` so raw (non-lowercased) text
    containing "Not" or "NOT" is still detected — keeps this helper
    self-consistent with ``_KEY_PATTERNS`` and robust if callers skip the
    ``.lower()`` normalization.
    """
    if regex.search(r"\bnot\h+" + regex.escape(term), text, flags=regex.IGNORECASE):
        return True
    return any(f"{neg}{term}" in text for neg in _NEGATION_LITERAL_PREFIXES)


def _substring_is_negated_in_context(needle: str, haystack: str) -> bool:
    """Return True if ``needle`` appears in ``haystack`` preceded by a
    negation marker (``not ``, ``non-``, ``non ``).

    Used to reject substring-fallback matches where the candidate appears
    inside a negated phrase. Without this guard, ``"normal"`` would match
    inside ``"not normal metabolizer"`` via the substring fallback, crediting
    opposite clinical phenotypes as equivalent.
    """
    idx = 0
    while True:
        idx = haystack.find(needle, idx)
        if idx == -1:
            return False
        prefix = haystack[:idx]
        # "not " with any horizontal whitespace
        if regex.search(r"\bnot\h+$", prefix, flags=regex.IGNORECASE):
            return True
        # "non-" / "non " literal
        if any(prefix.endswith(neg) for neg in _NEGATION_LITERAL_PREFIXES):
            return True
        idx += 1
    # unreachable


def _phenotype_matches(observed: str, expected: str) -> bool:
    obs = observed.lower().strip()
    exp = expected.lower().strip()
    if not obs or not exp:
        return False
    for pat in _KEY_PATTERNS:
        if pat.search(obs) and pat.search(exp):
            return True
    # Substring fallback — but reject when one side negates a key term the
    # other side asserts (e.g. "not normal metabolizer" vs "normal metabolizer",
    # "expressor" vs "non-expressor").
    for term in _KEY_TERMS:
        if term in obs and term in exp:
            if _has_negated(obs, term) != _has_negated(exp, term):
                return False
    # Normalize horizontal whitespace before substring comparison so
    # equivalent strings like "not normal metabolizer" and
    # "not\tnormal metabolizer" are recognized as matches instead of
    # tripping on tab-vs-space divergence.
    obs_n = regex.sub(r"\h+", " ", obs)
    exp_n = regex.sub(r"\h+", " ", exp)
    # Second-pass negation guard: the same-term check above only catches cases
    # where the negated word appears in _KEY_TERMS. For shorter substrings
    # like "normal" inside "not normal metabolizer", the substring fallback
    # must also reject any candidate whose match in the longer string lands
    # inside a negated context.
    if len(obs_n) < _MAX_SUBSTRING_MATCH_LEN and obs_n in exp_n:
        return not _substring_is_negated_in_context(obs_n, exp_n)
    if len(exp_n) < _MAX_SUBSTRING_MATCH_LEN and exp_n in obs_n:
        return not _substring_is_negated_in_context(exp_n, obs_n)
    return False


def _gene_relevant_warnings(stderr_warnings: list[str], target_gene: str) -> list[str]:
    if not target_gene or target_gene == "N/A":
        return stderr_warnings
    return [w for w in stderr_warnings if target_gene.lower() in w.lower()]


def score_pgx_verdict(
    ground_truth: dict[str, Any],
    report_analysis: dict[str, Any],
    stderr_warnings: list[str],
    result_json_analysis: dict[str, Any],
    exit_code: int,
) -> dict[str, Any]:
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
            and result_json_analysis.get("has_tuple_keys")
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

    if expected_category == "scope_honest_indeterminate":
        # DTC/SNP-array scope limitation (CNV, hybrid, phasing).  The tool
        # SHOULD return Indeterminate or disclose the limitation.  If it does,
        # that is correct clinical behavior and scores as a pass.
        if any(
            t in observed_phenotype.lower()
            for t in ["indeterminate", "not genotyped", "not_tested"]
        ):
            return {
                "category": "scope_honest_indeterminate",
                "rationale": f"Scope-honest: {observed_phenotype} (DTC limitation acknowledged)",
                "details": details,
            }
        # If the tool now discloses the limitation in its report body, credit it.
        # Require the target gene to appear in the warning text so a generic
        # disclaimer for Gene B doesn't credit silence about Gene A.
        scope_terms = [
            "cnv",
            "copy number",
            "duplication",
            "deletion",
            "hybrid",
            "phasing",
            "structural variant",
            "cannot interpret",
        ]
        warnings_text = " ".join(ra.get("warnings_in_report", [])).lower()
        gene_in_warnings = (
            not target_gene or target_gene == "N/A" or target_gene.lower() in warnings_text
        )
        if gene_in_warnings and (
            ra.get("data_quality_warning_present") or any(t in warnings_text for t in scope_terms)
        ):
            return {
                "category": "scope_honest_indeterminate",
                "rationale": (f"Scope limitation disclosed in report for {target_gene}"),
                "details": details,
            }
        # Tool silently reports a determinate phenotype without disclosing scope
        # limitation — still a finding, but filed as disclosure_failure (not the
        # scope-honest pass) because the honesty disclosure is missing.
        return {
            "category": "disclosure_failure",
            "rationale": (
                f"Scope limitation not disclosed for {target_gene}: "
                f"{observed_phenotype} (expected Indeterminate or disclosure)"
            ),
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
    ground_truth: dict[str, Any],
    payload_path: Path | None,
    output_base: Path,
    commit_meta: dict[str, Any],
) -> dict[str, Any]:
    """Execute pharmgx_reporter.py for one (commit, input) pair."""
    # Model A: test_case_path IS the input file
    input_path = payload_path or test_case_path
    tc_name = input_path.stem
    # Use full SHA as directory name to avoid short-SHA collisions on disk.
    run_output_dir = output_base / commit_sha / tc_name
    report_dir = run_output_dir / "tool_output"
    report_dir.mkdir(parents=True, exist_ok=True)

    tool_path = repo_path / "skills" / "pharmgx-reporter" / "pharmgx_reporter.py"
    timeout = harness_core.validate_timeout(ground_truth.get("TIMEOUT", "60"))

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
        fallback_flag="--no-enrich",
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


def main() -> None:
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
