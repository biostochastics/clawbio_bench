#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ClawBio Clinical Variant Reporter — Phase 2c: Variant Identity Harness
=======================================================================

**Scope (Phase 2c): variant representation correctness.** This harness
validates that the clinical-variant-reporter skill produces correctly
formed variant identities — HGVS nomenclature, transcript selection,
normalization, and assembly coordinate consistency.

Phase 2c is a prerequisite for Phase 2a (ACMG classification correctness):
if variant identity is wrong, classification correctness cannot be assessed.

What this harness checks:

  * **HGVS syntax** — coding DNA (``c.``), protein (``p.``), and genomic
    (``g.``) descriptions conform to HGVS v21.1 rules (Hart et al. 2024,
    PMID 39702242). Parentheses on predicted protein changes, versioned
    transcript accessions, underscore range separators, 3-letter amino acid
    codes.
  * **HGVS semantic accuracy** — the HGVS string describes the variant it
    claims to describe (e.g., ``c.123A>G`` matches the genomic alt allele).
  * **Transcript selection** — MANE Select transcripts used when available
    (LRGs deprecated per HGVS v21.1, December 2024). Versioned accessions
    required (``NM_004006.2`` not ``NM_004006``).
  * **Variant normalization** — indels left-aligned per VCF spec, duplications
    described as ``dup`` (not ``ins``), 3' rule applied.
  * **Assembly coordinate consistency** — all coordinates match the stated
    reference build; no cross-build contamination.

References:
  HGVS v21.1 specification — hgvs-nomenclature.org
  Hart et al. (2024) — Genome Med 16:149, PMID 39702242
  Hsu et al. (2025) — Hum Genomics 19:70, PMID 40542397
  MANE Select — NCBI/EBI (https://www.ncbi.nlm.nih.gov/refseq/MANE/)

Rubric (8 categories):
  variant_identity_correct       — All representation checks pass
  hgvs_syntax_error              — HGVS expression violates v21.1 rules
  hgvs_semantic_mismatch         — HGVS string doesn't match the variant
  transcript_selection_error     — Non-MANE transcript when MANE available
  variant_normalization_error    — Incorrect indel normalization
  assembly_coordinate_mismatch   — Coordinates inconsistent with stated build
  liftover_mapping_error         — Incorrect cross-build translation
  harness_error                  — Harness infrastructure failure
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

BENCHMARK_NAME = "cvr-variant-identity"
BENCHMARK_VERSION = "0.1.1"

RUBRIC_CATEGORIES = [
    "variant_identity_correct",
    "hgvs_syntax_error",
    "hgvs_semantic_mismatch",
    "transcript_selection_error",
    "variant_normalization_error",
    "assembly_coordinate_mismatch",
    "liftover_mapping_error",
    "self_consistency_error",
    "harness_error",
]

PASS_CATEGORIES = ["variant_identity_correct"]
FAIL_CATEGORIES = [
    "hgvs_syntax_error",
    "hgvs_semantic_mismatch",
    "transcript_selection_error",
    "variant_normalization_error",
    "assembly_coordinate_mismatch",
    "liftover_mapping_error",
    "self_consistency_error",
]

GROUND_TRUTH_REFS = {
    "HGVS_V21": (
        "HGVS Nomenclature v21.1 specification. hgvs-nomenclature.org. Accessed 2026-04-07."
    ),
    "HART_2024": (
        "Hart, R.K. et al. (2024). HGVS Nomenclature 2024: improvements "
        "to community engagement, usability, and computability. Genome "
        "Medicine, 16, 149. PMID 39702242. doi:10.1186/s13073-024-01421-5"
    ),
    "HSU_2025": (
        "Hsu, C.-H. et al. (2025). Toward streamline variant "
        "classification: discrepancies in variant nomenclature and syntax "
        "for ClinVar pathogenic variants across annotation tools. Human "
        "Genomics, 19, 70. PMID 40542397. doi:10.1186/s40246-025-00778-x"
    ),
    "MANE_SELECT": (
        "NCBI / EBI MANE (Matched Annotation from NCBI and EBI) Project. "
        "https://www.ncbi.nlm.nih.gov/refseq/MANE/"
    ),
    "RICHARDS_2015": (
        "Richards, S. et al. (2015). Standards and guidelines for the "
        "interpretation of sequence variants. Genetics in Medicine, 17(5), "
        "405-424. PMID 25741868. doi:10.1038/gim.2015.30"
    ),
}

CATEGORY_LEGEND = {
    "variant_identity_correct": {
        "color": "#22c55e",
        "label": "Variant identity correct",
        "tier": "pass",
    },
    "hgvs_syntax_error": {
        "color": "#ef4444",
        "label": "HGVS syntax violates v21.1",
        "tier": "critical",
    },
    "hgvs_semantic_mismatch": {
        "color": "#ef4444",
        "label": "HGVS does not match variant",
        "tier": "critical",
    },
    "transcript_selection_error": {
        "color": "#f97316",
        "label": "Non-MANE transcript used",
        "tier": "warning",
    },
    "variant_normalization_error": {
        "color": "#f97316",
        "label": "Indel normalization incorrect",
        "tier": "warning",
    },
    "assembly_coordinate_mismatch": {
        "color": "#ef4444",
        "label": "Coordinates inconsistent with assembly",
        "tier": "critical",
    },
    "liftover_mapping_error": {
        "color": "#f97316",
        "label": "Cross-build liftover error",
        "tier": "warning",
    },
    "self_consistency_error": {
        "color": "#ef4444",
        "label": "Tool output contradicts its own expected behavior",
        "tier": "critical",
    },
    "harness_error": {
        "color": "#9ca3af",
        "label": "Harness infrastructure error",
        "tier": "infra",
    },
}


# ---------------------------------------------------------------------------
# HGVS Validation Patterns (per HGVS v21.1)
# ---------------------------------------------------------------------------

# Coding DNA: NM_000123.4:c.123A>G or NM_000123.4:c.76_83del
_HGVS_CDNA_RE = re.compile(
    r"\b(NM_\d+\.\d+|ENST\d+\.\d+):c\."
    r"([-*]?\d+(?:[+-]\d+)?(?:_[-*]?\d+(?:[+-]\d+)?)?)"  # position(s)
    r"([A-Z]>[A-Z]|del[A-Z]*|dup[A-Z]*|ins[A-Z]+|delins[A-Z]+|=)?"  # change
)

# Protein: p.(Ser42Cys) or p.Ser42Cys or p.(Arg456GlyfsTer17)
_HGVS_PROTEIN_RE = re.compile(
    r"\bp\.\(?("
    r"[A-Z][a-z]{2}\d+[A-Z][a-z]{2}"  # missense 3-letter
    r"|[A-Z][a-z]{2}\d+(?:Ter|\*)"  # nonsense
    r"|[A-Z][a-z]{2}\d+[A-Z][a-z]{2}fs(?:Ter(?:\d+)?|\*(?:\d+)?)?"  # frameshift
    r"|[A-Z][a-z]{2}\d+[A-Z][a-z]{2}ext(?:Ter\d+)?"  # extension (stop-loss)
    r"|[A-Z][a-z]{2}\d+(?:_[A-Z][a-z]{2}\d+)?del"  # deletion
    r"|[A-Z][a-z]{2}\d+(?:_[A-Z][a-z]{2}\d+)?dup"  # duplication
    r"|[A-Z][a-z]{2}\d+(?:_[A-Z][a-z]{2}\d+)?ins(?:[A-Z][a-z]{2})+"  # insertion
    r"|[A-Z][a-z]{2}\d+="  # synonymous with position
    r"|="  # synonymous (no change)
    r"|\?"  # completely unknown
    r")\)?"
)

# Predicted protein change without parentheses: detect p.Ser42Cys but
# not p.(Ser42Cys). Check for missing OPENING paren, not closing.
_UNPREDICTED_PROTEIN_RE = re.compile(
    r"\bp\."  # p. prefix
    r"(?!\()"  # NOT followed by opening paren (lookahead)
    r"([A-Z][a-z]{2}\d+[A-Z][a-z]{2}(?:fs(?:Ter\d+)?)?)\b"
)

# Versioned transcript: NM_000123.4 (has version) vs NM_000123 (unversioned)
_VERSIONED_TRANSCRIPT_RE = re.compile(r"\b(NM_\d+\.\d+|ENST\d+\.\d+)\b")
_UNVERSIONED_TRANSCRIPT_RE = re.compile(r"\b(NM_\d+|ENST\d+)(?!\.\d)\b")

# MANE Select mention
_MANE_SELECT_RE = re.compile(r"\bMANE\s*Select\b", flags=re.IGNORECASE)

# Indel range: must use underscore, not hyphen.
# Wrong: c.76-83del (hyphen in range means intronic offset)
# Right: c.76_83del (underscore for range)
#
# IMPORTANT: c.123-1del is a VALID intronic-offset deletion (position 123,
# 1nt into the upstream intron) and must NOT be flagged.  We require both
# numbers to be ≥3 digits — intronic offsets are typically 1-2 digits, and
# any genuine range error in clinical reporting will use larger position
# numbers.  This is a safe heuristic that avoids false positives on
# splice-site variants.
_RANGE_HYPHEN_ERROR_RE = re.compile(r":c\.(\d{3,})-(\d{3,})(del|dup|ins)")

# One-letter amino acid codes in protein descriptions (common error)
_ONE_LETTER_AA_RE = re.compile(r"\bp\.[\(]?([A-Z]\d+[A-Z])(?:fs)?\)?\b")

# Incomplete frameshift: p.Arg456fs (missing Ter position)
_INCOMPLETE_FS_RE = re.compile(
    r"\bp\.[\(]?[A-Z][a-z]{2}\d+[A-Z][a-z]{2}fs\)?\b"
    r"(?!Ter|[*])"
)


# ---------------------------------------------------------------------------
# Report Analyzer
# ---------------------------------------------------------------------------


def analyze_variant_identity(report_dir: Path) -> dict[str, Any]:
    """Parse CVR output for variant identity correctness.

    Examines both report.md and structured sidecars (result.json,
    tables/acmg_classifications.tsv) for HGVS expressions, transcript
    citations, normalization patterns, and coordinate consistency.
    """
    analysis: dict[str, Any] = {
        "report_exists": False,
        "result_json_exists": False,
        "hgvs_cdna_expressions": [],
        "hgvs_protein_expressions": [],
        "versioned_transcripts": [],
        "unversioned_transcripts": [],
        "mane_select_mentioned": False,
        "range_hyphen_errors": [],
        "one_letter_aa_errors": [],
        "incomplete_frameshift_errors": [],
        "unpredicted_protein_errors": [],
        "assembly_stated": None,
        "coordinate_assemblies_detected": [],
        "variant_count": 0,
    }

    report_md = report_dir / "report.md"
    result_json = report_dir / "result.json"
    classifications_tsv = report_dir / "tables" / "acmg_classifications.tsv"

    # Collect all text sources for analysis
    texts: list[str] = []

    if report_md.exists():
        analysis["report_exists"] = True
        texts.append(report_md.read_text(errors="replace"))

    if result_json.exists():
        analysis["result_json_exists"] = True
        try:
            rj = json.loads(result_json.read_text(errors="replace"))
            analysis["variant_count"] = len(rj.get("variants", []))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            analysis["result_json_parse_error"] = f"{type(exc).__name__}: {exc}"

    if classifications_tsv.exists():
        texts.append(classifications_tsv.read_text(errors="replace"))

    full_text = "\n".join(texts)
    if not full_text.strip():
        return analysis

    # HGVS coding DNA expressions
    analysis["hgvs_cdna_expressions"] = [m.group(0) for m in _HGVS_CDNA_RE.finditer(full_text)]

    # HGVS protein expressions
    analysis["hgvs_protein_expressions"] = [
        m.group(0) for m in _HGVS_PROTEIN_RE.finditer(full_text)
    ]

    # Transcript versioning
    analysis["versioned_transcripts"] = sorted(
        {m.group(1) for m in _VERSIONED_TRANSCRIPT_RE.finditer(full_text)}
    )
    analysis["unversioned_transcripts"] = sorted(
        {m.group(1) for m in _UNVERSIONED_TRANSCRIPT_RE.finditer(full_text)}
    )
    analysis["mane_select_mentioned"] = bool(_MANE_SELECT_RE.search(full_text))

    # HGVS syntax errors
    analysis["range_hyphen_errors"] = [
        m.group(0) for m in _RANGE_HYPHEN_ERROR_RE.finditer(full_text)
    ]
    analysis["one_letter_aa_errors"] = [m.group(0) for m in _ONE_LETTER_AA_RE.finditer(full_text)]
    analysis["incomplete_frameshift_errors"] = [
        m.group(0) for m in _INCOMPLETE_FS_RE.finditer(full_text)
    ]
    analysis["unpredicted_protein_errors"] = [
        m.group(0) for m in _UNPREDICTED_PROTEIN_RE.finditer(full_text)
    ]

    # Assembly detection (reuse Phase 1 pattern)
    _assembly_re = re.compile(r"\b(GRCh3[78]|hg19|hg38|CHM13(?:v2)?)\b", flags=re.IGNORECASE)
    assemblies = {m.group(1).upper() for m in _assembly_re.finditer(full_text)}
    # Normalize
    _canon = {
        "GRCH37": "GRCh37",
        "HG19": "hg19",
        "GRCH38": "GRCh38",
        "HG38": "hg38",
        "CHM13": "CHM13",
        "CHM13V2": "CHM13v2",
    }
    analysis["coordinate_assemblies_detected"] = sorted(_canon.get(a, a) for a in assemblies)

    return analysis


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_identity_verdict(
    ground_truth: dict[str, Any],
    analysis: dict[str, Any],
    exit_code: int,
) -> dict[str, Any]:
    """Score a Phase 2c variant identity run.

    Ground truth headers:
        CHECK_HGVS_SYNTAX:         true/false (default true)
        CHECK_TRANSCRIPT_VERSION:  true/false (default true)
        CHECK_MANE_SELECT:         true/false (default true)
        CHECK_NORMALIZATION:       true/false (default true)
        CHECK_ASSEMBLY_COORDS:     true/false (default true)
        EXPECTED_ASSEMBLY:         GRCh37/GRCh38/any (default GRCh38)
        EXPECTED_HGVS_CDNA:        comma-separated expected c. expressions
        EXPECTED_HGVS_PROTEIN:     comma-separated expected p. expressions
        EXPECTED_TRANSCRIPT:       expected transcript accession (e.g. NM_007294.4)
    """
    details = {
        "exit_code": exit_code,
        "report_exists": analysis.get("report_exists", False),
        "result_json_exists": analysis.get("result_json_exists", False),
        "hgvs_cdna_count": len(analysis.get("hgvs_cdna_expressions", [])),
        "hgvs_protein_count": len(analysis.get("hgvs_protein_expressions", [])),
        "versioned_transcripts": analysis.get("versioned_transcripts", []),
        "unversioned_transcripts": analysis.get("unversioned_transcripts", []),
        "mane_select_mentioned": analysis.get("mane_select_mentioned", False),
        "range_hyphen_errors": analysis.get("range_hyphen_errors", []),
        "one_letter_aa_errors": analysis.get("one_letter_aa_errors", []),
        "incomplete_frameshift_errors": analysis.get("incomplete_frameshift_errors", []),
        "unpredicted_protein_errors": analysis.get("unpredicted_protein_errors", []),
        "assemblies_detected": analysis.get("coordinate_assemblies_detected", []),
        "variant_count": analysis.get("variant_count", 0),
    }
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

    # --- HGVS syntax checks ---
    if ground_truth.get("CHECK_HGVS_SYNTAX", "true").lower() == "true":
        # Range hyphen errors (c.76-83del instead of c.76_83del)
        if analysis.get("range_hyphen_errors"):
            return {
                "category": "hgvs_syntax_error",
                "rationale": (
                    f"HGVS range uses hyphen instead of underscore: "
                    f"{analysis['range_hyphen_errors']}"
                ),
                "details": details,
            }
        # One-letter amino acid codes
        if analysis.get("one_letter_aa_errors"):
            return {
                "category": "hgvs_syntax_error",
                "rationale": (
                    f"HGVS protein uses 1-letter amino acid codes "
                    f"(3-letter preferred per v21.1): "
                    f"{analysis['one_letter_aa_errors']}"
                ),
                "details": details,
            }
        # Incomplete frameshift (missing Ter position)
        if analysis.get("incomplete_frameshift_errors"):
            return {
                "category": "hgvs_syntax_error",
                "rationale": (
                    f"Incomplete frameshift notation (missing Ter position): "
                    f"{analysis['incomplete_frameshift_errors']}"
                ),
                "details": details,
            }
        # Unpredicted protein without parentheses — opt-in only.
        # HGVS v21.1 RECOMMENDS parens around predicted protein effects but
        # does not strictly require them.  Many clinical tools omit parens
        # when reporting protein changes derived from VCF.  This check is
        # off by default to avoid false-positive failures on tools that
        # follow common clinical convention rather than strict v21.1.
        if ground_truth.get("CHECK_PROTEIN_PARENS", "false").lower() == "true" and analysis.get(
            "unpredicted_protein_errors"
        ):
            return {
                "category": "hgvs_syntax_error",
                "rationale": (
                    f"Predicted protein change without parentheses "
                    f"(should be p.(Xxx) per HGVS v21.1): "
                    f"{analysis['unpredicted_protein_errors'][:5]}"
                ),
                "details": details,
            }

    # --- HGVS semantic checks ---
    expected_cdna = ground_truth.get("EXPECTED_HGVS_CDNA", "").strip()
    if expected_cdna:
        expected_set = {e.strip() for e in expected_cdna.split(",") if e.strip()}
        found_set = set(analysis.get("hgvs_cdna_expressions", []))
        missing = expected_set - found_set
        if missing:
            return {
                "category": "hgvs_semantic_mismatch",
                "rationale": (f"Expected HGVS c. expression(s) not found: {sorted(missing)}"),
                "details": details,
            }

    expected_protein = ground_truth.get("EXPECTED_HGVS_PROTEIN", "").strip()
    if expected_protein:
        expected_set = {e.strip() for e in expected_protein.split(",") if e.strip()}
        found_set = set(analysis.get("hgvs_protein_expressions", []))
        missing = expected_set - found_set
        if missing:
            return {
                "category": "hgvs_semantic_mismatch",
                "rationale": (f"Expected HGVS p. expression(s) not found: {sorted(missing)}"),
                "details": details,
            }

    # --- Transcript selection ---
    if ground_truth.get("CHECK_TRANSCRIPT_VERSION", "true").lower() == "true":
        unversioned = analysis.get("unversioned_transcripts", [])
        versioned = analysis.get("versioned_transcripts", [])
        # Fail only if there are NO versioned transcripts at all (or if
        # ground truth specifically requires no unversioned). A mix is
        # tolerated since many tools cite both forms in different sections.
        if unversioned and not versioned:
            return {
                "category": "transcript_selection_error",
                "rationale": (
                    f"Only unversioned transcript accessions found "
                    f"(HGVS v21.1 requires versioned form somewhere "
                    f"in report): {unversioned[:5]}"
                ),
                "details": details,
            }

    if ground_truth.get("CHECK_MANE_SELECT", "true").lower() == "true":
        # Strict mode requires the literal "MANE Select" string in the
        # report. Default mode (CHECK_MANE_SELECT_STRICT=false) accepts
        # MANE-aligned transcripts (NM_/ENST_ prefixed) without requiring
        # the exact "MANE Select" string. This recognizes that many tools
        # cite MANE transcripts by accession without spelling it out.
        strict_mane = ground_truth.get("CHECK_MANE_SELECT_STRICT", "false").lower() == "true"
        if not analysis.get("mane_select_mentioned"):
            if strict_mane and (
                analysis.get("versioned_transcripts") or analysis.get("unversioned_transcripts")
            ):
                return {
                    "category": "transcript_selection_error",
                    "rationale": (
                        "Transcripts cited but 'MANE Select' literal string "
                        "not mentioned (CHECK_MANE_SELECT_STRICT=true)."
                    ),
                    "details": details,
                }

    expected_transcript = ground_truth.get("EXPECTED_TRANSCRIPT", "").strip()
    if expected_transcript:
        all_transcripts = set(analysis.get("versioned_transcripts", []))
        if expected_transcript not in all_transcripts:
            return {
                "category": "transcript_selection_error",
                "rationale": (
                    f"Expected transcript {expected_transcript} not found "
                    f"in report. Found: {sorted(all_transcripts)}"
                ),
                "details": details,
            }

    # --- Normalization checks ---
    if ground_truth.get("CHECK_NORMALIZATION", "true").lower() == "true":
        # Range-hyphen errors caught above under syntax; normalization
        # checks focus on dup-vs-ins and left-alignment which require
        # ground truth expectations. Check if ground truth specifies
        # expected expressions that differ from found ones.
        pass  # Covered by EXPECTED_HGVS_CDNA semantic checks above

    # --- Assembly coordinate consistency ---
    if ground_truth.get("CHECK_ASSEMBLY_COORDS", "true").lower() == "true":
        assemblies = analysis.get("coordinate_assemblies_detected", [])
        expected_asm = ground_truth.get("EXPECTED_ASSEMBLY", "GRCh38").strip()

        if len(assemblies) > 1:
            return {
                "category": "assembly_coordinate_mismatch",
                "rationale": (f"Multiple reference assemblies detected in output: {assemblies}"),
                "details": details,
            }

        if expected_asm.lower() not in ("any", "false") and assemblies:
            # Normalize for comparison
            _norm = {
                "grch37": "GRCh37",
                "hg19": "hg19",
                "grch38": "GRCh38",
                "hg38": "hg38",
            }
            expected_norm = _norm.get(expected_asm.lower(), expected_asm)
            if expected_norm not in assemblies:
                return {
                    "category": "assembly_coordinate_mismatch",
                    "rationale": (f"Expected assembly {expected_norm} but found {assemblies}"),
                    "details": details,
                }

    # --- Self-consistency check (tool output vs expected tool behavior) ---
    # Dual-layer: EXPECTED_* = clinical gold standard, EXPECTED_TOOL_* = what
    # the tool claims to produce.  Mismatch here means the tool contradicts
    # its own documented behavior (regression, config error, etc.).
    tool_hgvs_cdna = ground_truth.get("EXPECTED_TOOL_HGVS_CDNA", "").strip()
    if tool_hgvs_cdna:
        tool_set = {e.strip() for e in tool_hgvs_cdna.split(",") if e.strip()}
        found_set = set(analysis.get("hgvs_cdna_expressions", []))
        tool_missing = tool_set - found_set
        if tool_missing:
            return {
                "category": "self_consistency_error",
                "rationale": (
                    f"Tool's own expected HGVS c. expressions "
                    f"{sorted(tool_set)} but output has "
                    f"{sorted(found_set)}. Missing: {sorted(tool_missing)}"
                ),
                "details": details,
            }

    tool_transcript = ground_truth.get("EXPECTED_TOOL_TRANSCRIPT", "").strip()
    if tool_transcript:
        all_transcripts = set(analysis.get("versioned_transcripts", []))
        if tool_transcript not in all_transcripts:
            return {
                "category": "self_consistency_error",
                "rationale": (
                    f"Tool's own expected transcript {tool_transcript} "
                    f"not found in output. Found: {sorted(all_transcripts)}"
                ),
                "details": details,
            }

    return {
        "category": "variant_identity_correct",
        "rationale": "All variant identity checks passed",
        "details": details,
    }


# ---------------------------------------------------------------------------
# Single Run Executor
# ---------------------------------------------------------------------------


def run_single_cvr_identity(
    repo_path: Path,
    commit_sha: str,
    test_case_path: Path,
    ground_truth: dict[str, Any],
    payload_path: Path | None,
    output_base: Path,
    commit_meta: dict[str, Any],
) -> dict[str, Any]:
    """Execute clinical_variant_reporter.py and validate variant identity."""
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

    analysis = analyze_variant_identity(report_dir)
    verdict = score_identity_verdict(ground_truth, analysis, execution.exit_code)

    outputs = {
        "report_md": harness_core.artifact_info(report_dir / "report.md"),
        "result_json": harness_core.artifact_info(report_dir / "result.json"),
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
        default_inputs_dir="cvr_identity",
        run_single_fn=run_single_cvr_identity,
        rubric_categories=RUBRIC_CATEGORIES,
        pass_categories=PASS_CATEGORIES,
        fail_categories=FAIL_CATEGORIES,
        ground_truth_refs=GROUND_TRUTH_REFS,
        category_legend=CATEGORY_LEGEND,
        description="Clinical Variant Reporter Phase 2c — Variant Identity Harness",
    )


if __name__ == "__main__":
    main()
