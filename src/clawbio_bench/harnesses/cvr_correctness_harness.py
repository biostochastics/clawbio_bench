#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ClawBio Clinical Variant Reporter — Phase 2a: ACMG Classification Correctness
===============================================================================

**Scope (Phase 2a): ACMG/AMP criterion-level and classification-level
correctness.** This harness validates that the clinical-variant-reporter
skill applies ACMG criteria correctly given frozen evidence, respects
ClinGen SVI refinements, and honors VCEP supersession rules.

Design principle: **score criteria-first, classification-second.** Many
tools get the final label right for the wrong reasons (or vice versa).
Criteria-level scoring reveals *what kind of safety failure* occurred.

What this harness checks:

  * **Population frequency thresholds** — BA1 (>5% = stand-alone benign),
    BS1 (>1%), PM2 (absent or extremely low per ClinGen SVI PM2 v1.0,
    approved 2020-09-04). Ancestry-stratified frequency required.
  * **PVS1 strength modulation** — per ClinGen SVI PVS1 decision tree
    (Abou Tayoun 2018, PMID 30192042). PVS1 must not be applied when LoF
    is not the established disease mechanism. Strength tiers: PVS1,
    PVS1_Strong, PVS1_Moderate, PVS1_Supporting.
  * **PP3/BP4 calibration** — per ClinGen SVI (Pejaver 2022, PMID 36413997).
    Must use ONE calibrated computational tool per variant (not multiple).
    Calibrated tool can reach Supporting/Moderate/Strong.
  * **VCEP supersession** — for genes with approved ClinGen VCEPs (BRCA1/2
    ENIGMA, Lynch syndrome InSiGHT, RASopathy, etc.), generic ACMG rules
    are superseded. Tool must apply gene-specific modifications.
  * **Classification aggregation** — Richards et al. 2015 Table 5 combination
    rules. Correct criteria but wrong 5-tier outcome is a distinct error.
  * **Gene-disease validity** — ClinGen GDV 7-tier model (Definitive, Strong,
    Moderate, Limited, Disputed, Refuted, No Known Disease Relationship).
    Variants in Limited/Disputed genes need explicit caveats.
  * **Secondary Findings** — SF v3.3 (Lee 2025, 84 genes, PMID 40568962).

Correctness is assessed relative to a **versioned, frozen truth set** with
explicit standard revision pinning. The truth set manifest records source
URLs, retrieval dates, and SHA-256 hashes for chain of custody.

Rubric (16 categories):
  classification_correct           — All criteria and classification match
  pvs1_strength_error              — Wrong PVS1 strength per SVI tree
  pvs1_applicability_error         — PVS1 applied when LoF not mechanism
  population_frequency_error       — BA1/BS1/PM2 thresholds wrong
  pm2_threshold_misapplied         — "Absent only" instead of PM2 v1.0
  in_silico_overcounting           — Multiple PP3/BP4 evidence units
  pp3_bp4_calibration_error        — Wrong strength for calibrated score
  criterion_double_counting        — Correlated evidence counted twice
  clinvar_strength_misuse          — Single-submitter treated as definitive
  vcep_rules_ignored               — Generic ACMG when VCEP exists
  vcep_rule_version_mismatch       — Correct VCEP, wrong revision
  gene_disease_validity_error      — No GDV caveat for Limited/Disputed
  sf_list_outdated                 — SF list not v3.3 (84 genes)
  classification_aggregation_error — Correct criteria, wrong 5-tier label
  criteria_not_machine_parseable   — Can't extract criteria from output
  harness_error                    — Infrastructure failure
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

BENCHMARK_NAME = "cvr-acmg-correctness"
BENCHMARK_VERSION = "0.1.1"

RUBRIC_CATEGORIES = [
    "classification_correct",
    "pvs1_strength_error",
    "pvs1_applicability_error",
    "population_frequency_error",
    "pm2_threshold_misapplied",
    "in_silico_overcounting",
    "pp3_bp4_calibration_error",
    "criterion_double_counting",
    "clinvar_strength_misuse",
    "vcep_rules_ignored",
    "vcep_rule_version_mismatch",
    "gene_disease_validity_error",
    "sf_list_outdated",
    "classification_aggregation_error",
    "self_consistency_error",
    "criteria_not_machine_parseable",
    "harness_error",
]

PASS_CATEGORIES = ["classification_correct"]
FAIL_CATEGORIES = [
    "pvs1_strength_error",
    "pvs1_applicability_error",
    "population_frequency_error",
    "pm2_threshold_misapplied",
    "in_silico_overcounting",
    "pp3_bp4_calibration_error",
    "criterion_double_counting",
    "clinvar_strength_misuse",
    "vcep_rules_ignored",
    "vcep_rule_version_mismatch",
    "gene_disease_validity_error",
    "sf_list_outdated",
    "classification_aggregation_error",
    "self_consistency_error",
]

GROUND_TRUTH_REFS = {
    "RICHARDS_2015": (
        "Richards, S. et al. (2015). Standards and guidelines for the "
        "interpretation of sequence variants. Genetics in Medicine, 17(5), "
        "405-424. PMID 25741868. doi:10.1038/gim.2015.30"
    ),
    "ABOU_TAYOUN_2018": (
        "Abou Tayoun, A.N. et al. (2018). Recommendations for interpreting "
        "the loss of function PVS1 ACMG/AMP variant criterion. Human "
        "Mutation, 39(11), 1517-1524. PMID 30192042. doi:10.1002/humu.23626"
    ),
    "CLINGEN_SVI_PM2": (
        "ClinGen SVI Recommendation for PM2 (Absence/Rarity), v1.0, "
        "approved 2020-09-04. clinicalgenome.org/docs/"
        "pm2-recommendation-for-absence-rarity"
    ),
    "PEJAVER_2022": (
        "Pejaver, V. et al. (2022). Calibration of computational tools for "
        "missense variant pathogenicity classification and ClinGen "
        "recommendations for PP3/BP4 criteria. Am J Hum Genet, 109(12), "
        "2163-2177. PMID 36413997. doi:10.1016/j.ajhg.2022.10.013"
    ),
    "LEE_2025": (
        "Lee, K. et al. (2025). ACMG SF v3.3 list for reporting of "
        "secondary findings. Genetics in Medicine, 27(8), 101454. "
        "PMID 40568962. doi:10.1016/j.gim.2025.101454 [84 genes; current]"
    ),
    "GHASEMNEJAD_2026": (
        "Ghasemnejad, T. et al. (2026). Comprehensive evaluation of "
        "ACMG/AMP-based variant classification tools. Bioinformatics, "
        "42(2), btaf623. PMC12916173. doi:10.1093/bioinformatics/btaf623"
    ),
    "EISENHART_2025": (
        "Eisenhart, C. et al. (2025). Automating ACMG variant "
        "classifications with BIAS-2015 v2.1.1. Genome Medicine, 17, 148. "
        "PMID 41382245. doi:10.1186/s13073-025-01581-y"
    ),
    "REHDER_2021": (
        "Rehder, S.A. et al. (2021). Next-generation sequencing for "
        "constitutional variants in the clinical laboratory, 2021 revision. "
        "PMID 33927380. doi:10.1038/s41436-021-01139-4"
    ),
}

CATEGORY_LEGEND = {
    "classification_correct": {
        "color": "#22c55e",
        "label": "ACMG classification correct",
        "tier": "pass",
    },
    "pvs1_strength_error": {
        "color": "#ef4444",
        "label": "PVS1 strength tier wrong per SVI",
        "tier": "critical",
    },
    "pvs1_applicability_error": {
        "color": "#ef4444",
        "label": "PVS1 applied when LoF not mechanism",
        "tier": "critical",
    },
    "population_frequency_error": {
        "color": "#ef4444",
        "label": "BA1/BS1/PM2 threshold misapplied",
        "tier": "critical",
    },
    "pm2_threshold_misapplied": {
        "color": "#f97316",
        "label": "PM2 uses 'absent' not 'absent or extremely low'",
        "tier": "warning",
    },
    "in_silico_overcounting": {
        "color": "#ef4444",
        "label": "Multiple in-silico tools counted as multiple PP3/BP4",
        "tier": "critical",
    },
    "pp3_bp4_calibration_error": {
        "color": "#f97316",
        "label": "PP3/BP4 strength miscalibrated",
        "tier": "warning",
    },
    "criterion_double_counting": {
        "color": "#f97316",
        "label": "Correlated evidence counted twice",
        "tier": "warning",
    },
    "clinvar_strength_misuse": {
        "color": "#f97316",
        "label": "ClinVar review status ignored",
        "tier": "warning",
    },
    "vcep_rules_ignored": {
        "color": "#ef4444",
        "label": "Gene-specific VCEP rules not applied",
        "tier": "critical",
    },
    "vcep_rule_version_mismatch": {
        "color": "#f97316",
        "label": "VCEP rules applied but wrong version",
        "tier": "warning",
    },
    "gene_disease_validity_error": {
        "color": "#f97316",
        "label": "Gene-disease validity caveat missing",
        "tier": "warning",
    },
    "sf_list_outdated": {
        "color": "#f97316",
        "label": "Secondary findings list not v3.3 (84 genes)",
        "tier": "warning",
    },
    "classification_aggregation_error": {
        "color": "#ef4444",
        "label": "Correct criteria but wrong 5-tier label",
        "tier": "critical",
    },
    "self_consistency_error": {
        "color": "#ef4444",
        "label": "Tool output contradicts its own expected behavior",
        "tier": "critical",
    },
    "criteria_not_machine_parseable": {
        "color": "#9ca3af",
        "label": "Criteria not extractable from output",
        "tier": "advisory",
    },
    "harness_error": {
        "color": "#9ca3af",
        "label": "Harness infrastructure error",
        "tier": "infra",
    },
}


# ---------------------------------------------------------------------------
# ACMG Criteria Patterns
# ---------------------------------------------------------------------------

_CRITERION_RE = re.compile(
    r"\b(PVS1|PS[1-4]|PM[1-6]|PP[1-5]|BA1|BS[1-4]|BP[1-7])"
    r"(?:_(Very_Strong|Strong|Moderate|Supporting))?\b",
    flags=re.IGNORECASE,
)

# 5-tier classification labels
_CLASSIFICATION_LABELS = {
    "pathogenic": "Pathogenic",
    "likely pathogenic": "Likely Pathogenic",
    "uncertain significance": "VUS",
    "vus": "VUS",
    "likely benign": "Likely Benign",
    "benign": "Benign",
}

# PP3/BP4 in-silico tool names (common ones for overcounting detection).
# Use word-boundary regex to avoid false positives on English prose
# (e.g., "SIFT" matching "sifting", "VEST" matching "investigate").
_IN_SILICO_TOOLS = (
    "REVEL",
    "CADD",
    "SIFT",
    "PolyPhen",
    "MutationTaster",
    "DANN",
    "GERP",
    "phyloP",
    "AlphaMissense",
    "VEST",
    "MetaLR",
    "MetaSVM",
    "BayesDel",
    "MPC",
    "PrimateAI",
)
_IN_SILICO_TOOL_RES = {
    tool: re.compile(rf"\b{re.escape(tool)}\b", flags=re.IGNORECASE) for tool in _IN_SILICO_TOOLS
}

# SF v3.3 gene count marker
_SF_VERSION_RE = re.compile(
    r"(?:ACMG\s+)?SF\s+v?(\d+\.\d+)",
    flags=re.IGNORECASE,
)

# VCEP / expert panel mentions
_VCEP_RE = re.compile(
    r"\b(VCEP|expert\s+panel|ENIGMA|InSiGHT|ClinGen\s+\w+\s+VCEP)\b",
    flags=re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Report Analyzer
# ---------------------------------------------------------------------------


def analyze_acmg_correctness(
    report_dir: Path,
    target_rsid: str | None = None,
    target_gene: str | None = None,
) -> dict[str, Any]:
    """Parse CVR output for ACMG classification correctness signals.

    Examines report.md, result.json, and tables/acmg_classifications.tsv
    for criterion codes, classification labels, evidence sources, and
    structural signals of correctness or error.

    Args:
        report_dir: Directory containing report.md, result.json, and the
          tables/ subdirectory.
        target_rsid: If provided, restrict per-variant criterion / class
          extraction to the variant whose ``rsid`` field matches. This
          lets a test case scope its assertions to a specific variant in
          ClawBio's demo panel rather than the panel-wide aggregation
          (which is the wrong granularity for tests like "PVS1 must NOT
          be applied to gene X" when the panel contains 20 variants
          spanning multiple genes).
        target_gene: Same idea but matches by ``gene`` field. Used as a
          weaker fallback when only the gene is known. If both
          ``target_rsid`` and ``target_gene`` are set, ``target_rsid``
          wins (it's more specific). When neither is set, the analyser
          aggregates criteria across the entire panel — the legacy
          behaviour for tests that genuinely audit panel-wide signals
          (e.g. PHI persistence sweeps).
    """
    analysis: dict[str, Any] = {
        "report_exists": False,
        "result_json_exists": False,
        "result_json_parsed": False,
        "variant_count": 0,
        "target_rsid": target_rsid,
        "target_gene": target_gene,
        "target_match_count": 0,
        "classifications_found": {},
        "criteria_found": {},
        "criteria_with_strength": [],
        "pp3_count": 0,
        "bp4_count": 0,
        "in_silico_tools_cited": [],
        "pvs1_applied": False,
        "pvs1_strength": None,
        "ba1_applied": False,
        "pm2_applied": False,
        "vcep_mentioned": False,
        "vcep_names": [],
        "sf_version_cited": None,
        "sf_gene_count": None,
        "clinvar_stars_mentioned": False,
        "gene_disease_validity_mentioned": False,
    }

    report_md = report_dir / "report.md"
    result_json = report_dir / "result.json"
    classifications_tsv = report_dir / "tables" / "acmg_classifications.tsv"

    texts: list[str] = []

    if report_md.exists():
        analysis["report_exists"] = True
        texts.append(report_md.read_text(errors="replace"))

    # Structured JSON sidecar — preferred for criterion extraction
    if result_json.exists():
        analysis["result_json_exists"] = True
        try:
            rj = json.loads(result_json.read_text(errors="replace"))
            analysis["result_json_parsed"] = True
            variants = rj.get("variants", [])
            analysis["variant_count"] = len(variants)

            # Track per-variant PP3/BP4 counts to detect ACTUAL overcounting
            # (multiple PP3 codes within a SINGLE variant) — not just total
            # PP3 mentions across all variants in the panel.
            max_pp3_per_variant = 0
            max_bp4_per_variant = 0

            for v in variants:
                gene = v.get("gene", "unknown")
                v_rsid = v.get("rsid", "")

                # Apply target filter, if any. A target_rsid takes
                # precedence over target_gene because it disambiguates
                # genes that appear multiple times in the panel (e.g.
                # ClawBio's demo has 3 BRCA1 variants and 3 TP53
                # variants).
                if target_rsid is not None:
                    if v_rsid != target_rsid:
                        # Still record the classification under the gene
                        # so the panel inventory remains visible in
                        # details for diagnostics, but skip criterion
                        # extraction.
                        analysis["classifications_found"].setdefault(
                            gene,
                            _CLASSIFICATION_LABELS.get(
                                v.get("classification", "").lower(),
                                v.get("classification", "").lower(),
                            ),
                        )
                        continue
                    analysis["target_match_count"] += 1
                elif target_gene is not None:
                    if gene != target_gene:
                        analysis["classifications_found"].setdefault(
                            gene,
                            _CLASSIFICATION_LABELS.get(
                                v.get("classification", "").lower(),
                                v.get("classification", "").lower(),
                            ),
                        )
                        continue
                    analysis["target_match_count"] += 1

                classification = v.get("classification", "").lower()
                normalized = _CLASSIFICATION_LABELS.get(classification, classification)
                analysis["classifications_found"][gene] = normalized

                # ClawBio CVR uses "triggered_criteria" as the field name.
                # Fall back to "criteria" for tools that use the older naming.
                criteria = v.get("triggered_criteria") or v.get("criteria") or []
                if isinstance(criteria, list):
                    pp3_in_this_variant = 0
                    bp4_in_this_variant = 0
                    for c in criteria:
                        code = c if isinstance(c, str) else c.get("code", "")
                        strength = c.get("strength", "") if isinstance(c, dict) else ""
                        if code:
                            analysis["criteria_found"][code] = strength
                            analysis["criteria_with_strength"].append(
                                f"{code}_{strength}" if strength else code
                            )
                            if code.upper() == "PVS1":
                                analysis["pvs1_applied"] = True
                                analysis["pvs1_strength"] = strength or "Very_Strong"
                            elif code.upper() == "BA1":
                                analysis["ba1_applied"] = True
                            elif code.upper() == "PM2":
                                analysis["pm2_applied"] = True
                            elif code.upper() == "PP3":
                                pp3_in_this_variant += 1
                            elif code.upper() == "BP4":
                                bp4_in_this_variant += 1
                    max_pp3_per_variant = max(max_pp3_per_variant, pp3_in_this_variant)
                    max_bp4_per_variant = max(max_bp4_per_variant, bp4_in_this_variant)

            # pp3_count = MAX per variant, not total across panel
            analysis["pp3_count"] = max_pp3_per_variant
            analysis["bp4_count"] = max_bp4_per_variant

        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            analysis["result_json_parse_error"] = f"{type(exc).__name__}: {exc}"

    if classifications_tsv.exists():
        texts.append(classifications_tsv.read_text(errors="replace"))

    full_text = "\n".join(texts)
    lower = full_text.lower()

    # If JSON parsing didn't yield criteria, fall back to text extraction.
    # NOTE: text fallback only populates "criteria_found" presence flags,
    # NOT pp3_count/bp4_count.  Counting PP3 mentions in narrative reports
    # is unreliable — clinical reports list PP3 evaluation rows for every
    # variant in their per-variant tables, which is not the same as
    # multiple PP3 *applications* to a single variant.  Per-variant
    # overcounting can only be reliably detected from structured JSON.
    if not analysis["criteria_found"] and full_text.strip():
        for m in _CRITERION_RE.finditer(full_text):
            code = m.group(1).upper()
            strength = m.group(2) or ""
            analysis["criteria_found"][code] = strength
            analysis["criteria_with_strength"].append(f"{code}_{strength}" if strength else code)
            if code == "PVS1":
                analysis["pvs1_applied"] = True
                analysis["pvs1_strength"] = strength or "Very_Strong"
            elif code == "BA1":
                analysis["ba1_applied"] = True
            elif code == "PM2":
                analysis["pm2_applied"] = True

    # In-silico tool citations (word-boundary match to avoid prose false positives)
    for tool, pat in _IN_SILICO_TOOL_RES.items():
        if pat.search(full_text):
            analysis["in_silico_tools_cited"].append(tool)

    # VCEP mentions
    vcep_matches = _VCEP_RE.findall(full_text)
    if vcep_matches:
        analysis["vcep_mentioned"] = True
        analysis["vcep_names"] = sorted(set(vcep_matches))

    # SF version
    sf_match = _SF_VERSION_RE.search(full_text)
    if sf_match:
        analysis["sf_version_cited"] = sf_match.group(1)

    # ClinVar star rating awareness
    if any(p in lower for p in ("star", "review status", "expert panel", "practice guideline")):
        analysis["clinvar_stars_mentioned"] = True

    # Gene-disease validity
    if any(
        p in lower
        for p in (
            "gene-disease validity",
            "gene disease validity",
            "definitive",
            "limited evidence",
            "disputed",
            "no known disease relationship",
            "clingen validity",
        )
    ):
        analysis["gene_disease_validity_mentioned"] = True

    return analysis


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_correctness_verdict(
    ground_truth: dict[str, Any],
    analysis: dict[str, Any],
    exit_code: int,
) -> dict[str, Any]:
    """Score a Phase 2a ACMG correctness run.

    Ground truth headers:
        EXPECTED_CLASSIFICATION:     P/LP/VUS/LB/B (5-tier label)
        EXPECTED_CRITERIA:           comma-separated (PVS1,PM2,PP3)
        EXPECTED_CRITERIA_STRENGTH:  comma-separated (PVS1_Strong,PM2_Supporting)
        EXPECTED_ABSENT_CRITERIA:    criteria that MUST NOT be applied
        CHECK_PVS1_STRENGTH:        true/false (default false)
        EXPECTED_PVS1_STRENGTH:      Very_Strong/Strong/Moderate/Supporting
        CHECK_PP3_OVERCOUNTING:     true/false (default true)
        CHECK_VCEP_SUPERSESSION:    true/false (default false)
        EXPECTED_VCEP:              expected VCEP name (e.g. ENIGMA)
        CHECK_SF_VERSION:           true/false (default false)
        CHECK_GDV:                  true/false (default false)
        EXPECTED_GDV_TIER:          Definitive/Strong/Moderate/Limited/Disputed/Refuted/None
        CHECK_CLINVAR_STARS:        true/false (default false)
        GOLD_TIER:                  true/false — gold tier has strict criteria matching
    """
    details = {
        "exit_code": exit_code,
        "report_exists": analysis.get("report_exists", False),
        "result_json_parsed": analysis.get("result_json_parsed", False),
        "variant_count": analysis.get("variant_count", 0),
        "classifications_found": analysis.get("classifications_found", {}),
        "criteria_found": analysis.get("criteria_found", {}),
        "criteria_with_strength": analysis.get("criteria_with_strength", []),
        "pp3_count": analysis.get("pp3_count", 0),
        "bp4_count": analysis.get("bp4_count", 0),
        "in_silico_tools_cited": analysis.get("in_silico_tools_cited", []),
        "pvs1_applied": analysis.get("pvs1_applied", False),
        "pvs1_strength": analysis.get("pvs1_strength"),
        "vcep_mentioned": analysis.get("vcep_mentioned", False),
        "vcep_names": analysis.get("vcep_names", []),
        "sf_version_cited": analysis.get("sf_version_cited"),
        "clinvar_stars_mentioned": analysis.get("clinvar_stars_mentioned", False),
        "gene_disease_validity_mentioned": analysis.get("gene_disease_validity_mentioned", False),
    }
    # Surface JSON parse failures into details for diagnosability
    if "result_json_parse_error" in analysis:
        details["result_json_parse_error"] = analysis["result_json_parse_error"]

    if exit_code != 0:
        return {
            "category": "harness_error",
            "rationale": f"clinical-variant-reporter exited {exit_code}",
            "details": details,
        }
    if not analysis.get("report_exists"):
        return {
            "category": "harness_error",
            "rationale": "No report.md produced",
            "details": details,
        }

    # KNOWN_LIMITATION marker. Some Phase 2a tests probe ACMG features
    # (PVS1 strength modulation, calibrated PP3 strength, ENIGMA / InSiGHT
    # VCEP supersession, BS1+BP1 combination) that ClawBio's demo panel
    # does not currently express. The harness routes these to the
    # advisory `criteria_not_machine_parseable` bucket so the tests
    # remain visible without polluting the critical-finding count, and
    # they auto-flip to a real verdict the moment ClawBio's demo grows
    # the missing evidence (or the bench gains a per-variant input mode).
    known_limit = (
        ground_truth.get("KNOWN_LIMITATION_DEMO_LACKS_EVIDENCE", "false").lower() == "true"
    )
    if known_limit:
        details["known_limitation"] = "clawbio_demo_lacks_required_evidence"
        return {
            "category": "criteria_not_machine_parseable",
            "rationale": (
                "ClawBio's demo panel does not contain a variant that expresses the "
                "evidence pattern this Phase 2a test was designed to audit "
                "(declared via KNOWN_LIMITATION_DEMO_LACKS_EVIDENCE). This test "
                "will flip to a real verdict the moment the demo gains a matching "
                "variant or the bench adds per-variant input mode for CVR tests."
            ),
            "details": details,
        }

    # Target filter sanity: if a test specified a target_rsid or
    # target_gene but no variant in the panel matched, treat the test as
    # not-applicable rather than firing a misleading classification
    # error. The same advisory bucket as KNOWN_LIMITATION above.
    if (
        analysis.get("target_rsid") is not None or analysis.get("target_gene") is not None
    ) and analysis.get("target_match_count", 0) == 0:
        return {
            "category": "criteria_not_machine_parseable",
            "rationale": (
                f"Test scoped to target_rsid={analysis.get('target_rsid')} / "
                f"target_gene={analysis.get('target_gene')} but no variant in "
                f"the {analysis.get('variant_count', 0)}-variant panel matched"
            ),
            "details": details,
        }

    # Fail closed: if we can't parse criteria, emit specific category
    is_gold = ground_truth.get("GOLD_TIER", "false").lower() == "true"
    expected_criteria_raw = ground_truth.get("EXPECTED_CRITERIA", "").strip()

    if expected_criteria_raw and not analysis.get("criteria_found"):
        if not analysis.get("result_json_parsed"):
            return {
                "category": "criteria_not_machine_parseable",
                "rationale": (
                    "Ground truth expects specific criteria but tool output "
                    "has no parseable result.json and no extractable criteria "
                    "from report text"
                ),
                "details": details,
            }

    # --- PVS1 applicability ---
    expected_absent = ground_truth.get("EXPECTED_ABSENT_CRITERIA", "").strip()
    if expected_absent:
        absent_set = {c.strip().upper() for c in expected_absent.split(",") if c.strip()}
        found_set = {c.upper() for c in analysis.get("criteria_found", {})}
        wrongly_applied = absent_set & found_set
        if "PVS1" in wrongly_applied:
            return {
                "category": "pvs1_applicability_error",
                "rationale": (
                    "PVS1 applied but ground truth specifies it must NOT be "
                    "applied (LoF is not the established disease mechanism)"
                ),
                "details": details,
            }
        if wrongly_applied:
            return {
                "category": "criterion_double_counting",
                "rationale": (
                    f"Criteria {sorted(wrongly_applied)} applied but ground "
                    f"truth specifies they must not be"
                ),
                "details": details,
            }

    # --- PVS1 strength ---
    if ground_truth.get("CHECK_PVS1_STRENGTH", "false").lower() == "true":
        expected_pvs1 = ground_truth.get("EXPECTED_PVS1_STRENGTH", "").strip()
        if expected_pvs1 and analysis.get("pvs1_applied"):
            actual_pvs1 = (analysis.get("pvs1_strength") or "Very_Strong").lower()
            if actual_pvs1 != expected_pvs1.lower():
                return {
                    "category": "pvs1_strength_error",
                    "rationale": (
                        f"PVS1 strength is {actual_pvs1} but expected "
                        f"{expected_pvs1} per ClinGen SVI decision tree"
                    ),
                    "details": details,
                }

    # --- Population frequency ---
    expected_criteria = set()
    if expected_criteria_raw:
        expected_criteria = {
            c.strip().upper() for c in expected_criteria_raw.split(",") if c.strip()
        }

    # BA1 must be applied if expected
    if "BA1" in expected_criteria and not analysis.get("ba1_applied"):
        return {
            "category": "population_frequency_error",
            "rationale": (
                "BA1 (stand-alone benign, gnomAD AF >5%) expected but not "
                "applied. Tool missed common benign variant."
            ),
            "details": details,
        }

    # PM2 checks
    if "PM2" in expected_criteria and not analysis.get("pm2_applied"):
        return {
            "category": "pm2_threshold_misapplied",
            "rationale": (
                "PM2 expected (absent or extremely low frequency per "
                "ClinGen SVI PM2 v1.0, 2020) but not applied"
            ),
            "details": details,
        }

    # --- PP3/BP4 overcounting ---
    if ground_truth.get("CHECK_PP3_OVERCOUNTING", "true").lower() == "true":
        pp3_count = analysis.get("pp3_count", 0)
        bp4_count = analysis.get("bp4_count", 0)
        if pp3_count > 1:
            return {
                "category": "in_silico_overcounting",
                "rationale": (
                    f"PP3 applied {pp3_count} times — ClinGen SVI (Pejaver "
                    f"2022, PMID 36413997) requires exactly ONE calibrated "
                    f"computational tool per variant, not multiple"
                ),
                "details": details,
            }
        if bp4_count > 1:
            return {
                "category": "in_silico_overcounting",
                "rationale": (f"BP4 applied {bp4_count} times — must use single tool"),
                "details": details,
            }

    # --- PP3/BP4 strength calibration ---
    expected_strength_raw = ground_truth.get("EXPECTED_CRITERIA_STRENGTH", "").strip()
    if expected_strength_raw:
        expected_strengths = {
            s.strip().upper() for s in expected_strength_raw.split(",") if s.strip()
        }
        for es in expected_strengths:
            if es.startswith("PP3_") or es.startswith("BP4_"):
                actual_strengths = {s.upper() for s in analysis.get("criteria_with_strength", [])}
                if es not in actual_strengths:
                    return {
                        "category": "pp3_bp4_calibration_error",
                        "rationale": (
                            f"Expected {es} but not found in tool output. "
                            f"Pejaver 2022 Table 2 calibrated REVEL thresholds: "
                            f"[0.644,0.773)=Supporting, [0.773,0.932)=Moderate, "
                            f">=0.932=Strong"
                        ),
                        "details": details,
                    }

    # --- VCEP supersession ---
    if ground_truth.get("CHECK_VCEP_SUPERSESSION", "false").lower() == "true":
        expected_vcep = ground_truth.get("EXPECTED_VCEP", "").strip()
        if expected_vcep:
            # Require the SPECIFIC expected VCEP name in the output, not
            # just any "expert panel" mention.  ClinVar review status
            # often says "reviewed by expert panel" without the tool
            # actually applying VCEP-specific rules.
            vcep_names_lower = {n.lower() for n in analysis.get("vcep_names", [])}
            if expected_vcep.lower() not in vcep_names_lower:
                return {
                    "category": "vcep_rules_ignored",
                    "rationale": (
                        f"Gene has approved VCEP ({expected_vcep}) but tool "
                        f"output does not cite this specific VCEP. Found "
                        f"VCEPs: {sorted(vcep_names_lower) or 'none'}. "
                        f"Generic ACMG rules must be superseded."
                    ),
                    "details": details,
                }

    # --- ClinVar review status ---
    if ground_truth.get("CHECK_CLINVAR_STARS", "false").lower() == "true":
        if not analysis.get("clinvar_stars_mentioned"):
            return {
                "category": "clinvar_strength_misuse",
                "rationale": (
                    "ClinVar used for evidence but review status (stars) "
                    "not communicated. Single-submitter VUS and expert-panel "
                    "Benign are not equivalent evidence strengths."
                ),
                "details": details,
            }

    # --- Gene-disease validity ---
    if ground_truth.get("CHECK_GDV", "false").lower() == "true":
        expected_gdv = ground_truth.get("EXPECTED_GDV_TIER", "").strip().lower()
        if expected_gdv in ("limited", "disputed", "refuted", "none"):
            if not analysis.get("gene_disease_validity_mentioned"):
                return {
                    "category": "gene_disease_validity_error",
                    "rationale": (
                        f"Gene has ClinGen GDV tier '{expected_gdv}' but "
                        f"report does not mention gene-disease validity "
                        f"caveat. ClinGen GDV has 6 classification tiers; variants in "
                        f"Limited/Disputed genes need explicit caveats."
                    ),
                    "details": details,
                }

    # --- Secondary Findings version ---
    if ground_truth.get("CHECK_SF_VERSION", "false").lower() == "true":
        sf_version = analysis.get("sf_version_cited")
        if not sf_version:
            return {
                "category": "sf_list_outdated",
                "rationale": "No ACMG SF version cited in report",
                "details": details,
            }
        if sf_version != "3.3":
            return {
                "category": "sf_list_outdated",
                "rationale": (
                    f"SF v{sf_version} cited but current is v3.3 "
                    f"(Lee 2025, 84 genes, PMID 40568962)"
                ),
                "details": details,
            }

    # --- Criteria match (gold tier) ---
    if is_gold and expected_criteria:
        found_criteria = {c.upper() for c in analysis.get("criteria_found", {})}
        missing = expected_criteria - found_criteria
        if missing:
            # Missing criteria (including PVS1) is an aggregation/extraction
            # error, NOT a strength error.  Strength error means PVS1 was
            # applied at the wrong tier.  Missing entirely is a different
            # failure mode and gets a clearer rationale here.
            if "PVS1" in missing:
                return {
                    "category": "classification_aggregation_error",
                    "rationale": (
                        "PVS1 expected in gold-standard criteria but not "
                        "found in tool output — LoF criterion entirely "
                        f"missing. Other missing: {sorted(missing - {'PVS1'})}"
                    ),
                    "details": details,
                }
            return {
                "category": "classification_aggregation_error",
                "rationale": (
                    f"Expected criteria {sorted(expected_criteria)} but "
                    f"found {sorted(found_criteria)}. Missing: {sorted(missing)}"
                ),
                "details": details,
            }

    # --- Classification match (gold standard) ---
    expected_class = ground_truth.get("EXPECTED_CLASSIFICATION", "").strip()
    if expected_class:
        expected_norm = _CLASSIFICATION_LABELS.get(expected_class.lower(), expected_class)
        found_classes = set(analysis.get("classifications_found", {}).values())

        if found_classes and expected_norm not in found_classes:
            return {
                "category": "classification_aggregation_error",
                "rationale": (
                    f"Expected classification '{expected_norm}' but tool "
                    f"produced {sorted(found_classes)}"
                ),
                "details": details,
            }

    # --- Self-consistency check (tool output vs expected tool behavior) ---
    # Dual-layer ground truth: EXPECTED_* headers capture the clinical gold
    # standard; EXPECTED_TOOL_* headers capture what the tool claims it does.
    # A tool that is self-consistent but clinically wrong fails the gold
    # standard checks above.  A tool whose output contradicts its own
    # documented behavior fails the self-consistency check here.
    tool_class = ground_truth.get("EXPECTED_TOOL_CLASSIFICATION", "").strip()
    if tool_class:
        tool_norm = _CLASSIFICATION_LABELS.get(tool_class.lower(), tool_class)
        found_classes = set(analysis.get("classifications_found", {}).values())
        if found_classes and tool_norm not in found_classes:
            return {
                "category": "self_consistency_error",
                "rationale": (
                    f"Tool's own expected classification is '{tool_norm}' "
                    f"but it produced {sorted(found_classes)}. "
                    f"The tool contradicts its own documented behavior."
                ),
                "details": details,
            }

    tool_criteria_raw = ground_truth.get("EXPECTED_TOOL_CRITERIA", "").strip()
    if tool_criteria_raw:
        tool_expected = {c.strip().upper() for c in tool_criteria_raw.split(",") if c.strip()}
        found_criteria = {c.upper() for c in analysis.get("criteria_found", {})}
        tool_missing = tool_expected - found_criteria
        if tool_missing:
            return {
                "category": "self_consistency_error",
                "rationale": (
                    f"Tool's own expected criteria {sorted(tool_expected)} "
                    f"but output has {sorted(found_criteria)}. "
                    f"Missing from tool's own expectation: {sorted(tool_missing)}"
                ),
                "details": details,
            }

    return {
        "category": "classification_correct",
        "rationale": "ACMG criteria and classification match ground truth",
        "details": details,
    }


# ---------------------------------------------------------------------------
# Single Run Executor
# ---------------------------------------------------------------------------


def run_single_cvr_correctness(
    repo_path: Path,
    commit_sha: str,
    test_case_path: Path,
    ground_truth: dict[str, Any],
    payload_path: Path | None,
    output_base: Path,
    commit_meta: dict[str, Any],
) -> dict[str, Any]:
    """Execute clinical_variant_reporter.py and validate ACMG correctness."""
    tc_name = test_case_path.name if test_case_path.is_dir() else test_case_path.stem
    run_output_dir = output_base / commit_sha / tc_name
    report_dir = run_output_dir / "tool_output"
    report_dir.mkdir(parents=True, exist_ok=True)

    tool_path = repo_path / "skills" / "clinical-variant-reporter" / "clinical_variant_reporter.py"
    timeout = harness_core.validate_timeout(ground_truth.get("TIMEOUT", "120"))

    mode = ground_truth.get("MODE", "demo").lower()
    _raw_require = ground_truth.get("EXPECTED_ASSEMBLY", "GRCh38").strip().lower()
    _cli_assembly_map = {
        "grch37": "GRCh37",
        "hg19": "GRCh37",
        "grch38": "GRCh38",
        "hg38": "GRCh38",
    }
    assembly_arg = _cli_assembly_map.get(_raw_require, "GRCh38")

    if mode == "demo" or payload_path is None:
        cmd = [
            sys.executable,
            str(tool_path),
            "--demo",
            "--output",
            str(report_dir),
            "--assembly",
            assembly_arg,
        ]
    else:
        cmd = [
            sys.executable,
            str(tool_path),
            "--input",
            str(payload_path),
            "--output",
            str(report_dir),
            "--assembly",
            assembly_arg,
        ]

    execution = harness_core.capture_execution(
        cmd=cmd,
        cwd=repo_path,
        timeout=timeout,
    )
    harness_core.save_execution_logs(execution, run_output_dir)

    target_rsid = ground_truth.get("TARGET_RSID") or None
    target_gene = ground_truth.get("TARGET_GENE") or None
    analysis = analyze_acmg_correctness(
        report_dir,
        target_rsid=target_rsid,
        target_gene=target_gene,
    )
    verdict = score_correctness_verdict(ground_truth, analysis, execution.exit_code)

    outputs = {
        "report_md": harness_core.artifact_info(report_dir / "report.md"),
        "result_json": harness_core.artifact_info(report_dir / "result.json"),
        "classifications_tsv": harness_core.artifact_info(
            report_dir / "tables" / "acmg_classifications.tsv"
        ),
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
        driver_path=(
            test_case_path / "ground_truth.txt" if test_case_path.is_dir() else test_case_path
        ),
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
        default_inputs_dir="cvr_correctness",
        run_single_fn=run_single_cvr_correctness,
        rubric_categories=RUBRIC_CATEGORIES,
        pass_categories=PASS_CATEGORIES,
        fail_categories=FAIL_CATEGORIES,
        ground_truth_refs=GROUND_TRUTH_REFS,
        category_legend=CATEGORY_LEGEND,
        description="Clinical Variant Reporter Phase 2a — ACMG Correctness Harness",
    )


if __name__ == "__main__":
    main()
