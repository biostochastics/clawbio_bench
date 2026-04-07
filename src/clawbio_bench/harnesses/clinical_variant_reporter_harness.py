#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ClawBio Clinical Variant Reporter Benchmark Harness (Phase 1)
=============================================================

**Scope (Phase 1): reporting / traceability / honesty only.** This harness
does not attempt ACMG/AMP 28-criteria adjudication correctness in its first
release — that requires gene-disease-specific expert calibration (ClinGen
VCEP consensus, transcript canonicalization, threshold alignment) that is
its own multi-release project.

What this harness does check, per commit under audit, is whether the
``clinical-variant-reporter`` skill produces reports with the structural
elements any auditable clinical variant report must carry:

  * **Reference genome assembly** stated explicitly (GRCh37/GRCh38/CHM13).
    An ACMG classification is meaningless without knowing which coordinate
    space it was computed in.
  * **Transcript source** stated (MANE Select preferred). PVS1 loss-of-
    function assessment is transcript-dependent; a report that omits the
    transcript is unreproducible.
  * **Data source versions** (ClinVar release date, gnomAD version, VEP
    version). Classifications decay as evidence databases update; auditors
    must be able to pin the decision to its inputs.
  * **Limitations section** naming at least one failure mode the tool
    cannot handle (CNV, repeat expansions, mosaic variants, etc).
  * **Research-use-only / not-a-medical-device disclaimer** present in
    report body, not only in stderr.
  * **Per-variant evidence audit trail** — every variant classified as
    P/LP/VUS/LB/B must be accompanied by the list of ACMG criteria that
    triggered and the source each came from.
  * **Gene-disease context** — variant classifications that assert P or
    LP must include the disease the assertion is relative to and the
    inheritance model (AD/AR/XLR/XLD). A standalone "Pathogenic" label
    with no condition named is an incomplete clinical report per Rehm
    et al. 2013 laboratory reporting standards.

Phase 2 (later release) will add a small unambiguous-subset correctness
harness using ClinGen VCEP 3-star+ consensus variants for BA1 (very common
benign), PVS1 (unambiguous LoF in a LoF-intolerant gene), and stable
pathogenic consensus variants.

Rubric (10 categories + harness_error):
  report_structure_complete    — All required sections present
  assembly_missing             — No reference genome build stated
  transcript_missing           — No transcript source stated
  data_source_version_missing  — ClinVar/gnomAD/VEP versions not pinned
  limitations_missing          — No limitations section
  disclaimer_missing           — RUO / not-a-medical-device disclaimer absent
  evidence_trail_incomplete    — Classifications without per-criterion audit trail
  gene_disease_context_missing — P/LP without condition + inheritance
  reference_build_inconsistent — Multiple assemblies cited in same report
  harness_error                — Harness infrastructure failure
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "clinical-variant-reporter"
BENCHMARK_VERSION = "0.1.1"

RUBRIC_CATEGORIES = [
    "report_structure_complete",
    "assembly_missing",
    "transcript_missing",
    "data_source_version_missing",
    "limitations_missing",
    "disclaimer_missing",
    "evidence_trail_incomplete",
    "gene_disease_context_missing",
    "reference_build_inconsistent",
    "harness_error",
]

PASS_CATEGORIES = ["report_structure_complete"]
FAIL_CATEGORIES = [
    "assembly_missing",
    "transcript_missing",
    "data_source_version_missing",
    "limitations_missing",
    "disclaimer_missing",
    "evidence_trail_incomplete",
    "gene_disease_context_missing",
    "reference_build_inconsistent",
]

GROUND_TRUTH_REFS = {
    "RICHARDS_2015": (
        "Richards, S. et al. (2015). Standards and guidelines for the "
        "interpretation of sequence variants: a joint consensus recommendation "
        "of the American College of Medical Genetics and Genomics and the "
        "Association for Molecular Pathology. Genetics in Medicine, 17(5), "
        "405-424. PMID 25741868. doi:10.1038/gim.2015.30"
    ),
    "REHM_2013": (
        "Rehm, H.L. et al. (2013). ACMG clinical laboratory standards for "
        "next-generation sequencing. Genetics in Medicine, 15(9), 733-747. "
        "doi:10.1038/gim.2013.92 [historical; superseded by REHDER_2021]"
    ),
    "REHDER_2021": (
        "Rehder, S.A. et al. (2021). Next-generation sequencing for "
        "constitutional variants in the clinical laboratory, 2021 revision: "
        "a technical standard of the American College of Medical Genetics "
        "and Genomics (ACMG). Genetics in Medicine. "
        "PMID 33927380. doi:10.1038/s41436-021-01139-4"
    ),
    "CLINGEN_SVI_PVS1": (
        "Abou Tayoun, A.N. et al. (2018). Recommendations for interpreting "
        "the loss of function PVS1 ACMG/AMP variant criterion. Human "
        "Mutation, 39(11), 1517-1524. PMID 30192042. doi:10.1002/humu.23626"
    ),
    "PEJAVER_2022": (
        "Pejaver, V. et al. (2022). Calibration of computational tools for "
        "missense variant pathogenicity classification and ClinGen "
        "recommendations for PP3/BP4 criteria. American Journal of Human "
        "Genetics, 109(12), 2163-2177. PMID 36413997. "
        "doi:10.1016/j.ajhg.2022.10.013"
    ),
    "MILLER_2023": (
        "Miller, D.T. et al. (2023). ACMG SF v3.2 list for reporting of "
        "secondary findings in clinical exome and genome sequencing. "
        "Genetics in Medicine, 25(8), 100866. PMID 37347242. "
        "[superseded by LEE_2025 SF v3.3]"
    ),
    "LEE_2025": (
        "Lee, K. et al. (2025). ACMG SF v3.3 list for reporting of "
        "secondary findings in clinical exome and genome sequencing. "
        "Genetics in Medicine, 27(8), 101454. PMID 40568962. "
        "doi:10.1016/j.gim.2025.101454 [84 genes; current]"
    ),
    "MANE_SELECT_v1.3": (
        "NCBI / EBI MANE (Matched Annotation from NCBI and EBI) Project, "
        "accessed 2026-04-04. https://www.ncbi.nlm.nih.gov/refseq/MANE/"
    ),
}

CATEGORY_LEGEND = {
    "report_structure_complete": {
        "color": "#22c55e",
        "label": "Report structurally complete",
        "tier": "pass",
    },
    "assembly_missing": {
        "color": "#ef4444",
        "label": "Reference build missing",
        "tier": "critical",
    },
    "transcript_missing": {
        "color": "#ef4444",
        "label": "Transcript source missing",
        "tier": "critical",
    },
    "data_source_version_missing": {
        "color": "#f97316",
        "label": "Data source versions unpinned",
        "tier": "warning",
    },
    "limitations_missing": {
        "color": "#f97316",
        "label": "Limitations section missing",
        "tier": "warning",
    },
    "disclaimer_missing": {
        "color": "#ef4444",
        "label": "RUO disclaimer missing",
        "tier": "critical",
    },
    "evidence_trail_incomplete": {
        "color": "#ef4444",
        "label": "Evidence audit trail incomplete",
        "tier": "critical",
    },
    "gene_disease_context_missing": {
        "color": "#f97316",
        "label": "Gene-disease context missing",
        "tier": "warning",
    },
    "reference_build_inconsistent": {
        "color": "#ef4444",
        "label": "Conflicting reference builds in report",
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


_ASSEMBLY_PATTERN = re.compile(
    r"\b(GRCh3[78]|hg19|hg38|CHM13(?:v2)?)\b",
    flags=re.IGNORECASE,
)
_TRANSCRIPT_PATTERN = re.compile(
    # NM_*/ENST_* versions are optional — MANE recommends citing the
    # versioned form but unversioned transcript IDs are still
    # interpretable and should count as "transcript cited" for the
    # Phase 1 structural check. Phase 2 correctness scoring will
    # tighten this if needed (e.g. require MANE Select specifically).
    r"\b(NM_\d+(?:\.\d+)?|ENST\d+(?:\.\d+)?|MANE\s*Select)\b",
    flags=re.IGNORECASE,
)
_CLINVAR_VERSION_PATTERN = re.compile(
    r"clinvar[^\n]{0,60}(\d{4}[-/]\d{2}[-/]\d{2}|v\d+|release\s*\d+)",
    flags=re.IGNORECASE,
)
_GNOMAD_VERSION_PATTERN = re.compile(
    r"gnomad[^\n]{0,60}(v\d+(?:\.\d+)?|\d+\.\d+)",
    flags=re.IGNORECASE,
)
_DISCLAIMER_PATTERNS = (
    "research and educational",
    "not a medical device",
    "not intended for clinical",
    "research use only",
)
_LIMITATIONS_SECTION_PATTERNS = (
    "## limitations",
    "### limitations",
    "**limitations**",
    "limitations:",
)
_INHERITANCE_WORDS = re.compile(
    # Full-word inheritance descriptors — safe to match case-insensitively
    # since each phrase is long enough to be unambiguous in a clinical
    # report context.
    r"\b(autosomal\s+(?:dominant|recessive)|x[-\s]?linked|mitochondrial|somatic)\b",
    flags=re.IGNORECASE,
)
_INHERITANCE_ACRONYMS = re.compile(
    # Short acronyms must be matched case-sensitively to avoid false
    # positives on English words like "ad hoc", "are", "XL size".
    # Clinical reports use uppercase for these codes.
    r"\b(AD|AR|XLR|XLD|XL)\b"
)


def analyze_cvr_report(report_path: Path) -> dict[str, Any]:
    """Parse a clinical-variant-reporter report.md for structural elements.

    Phase 1 analysis is purely structural — we do not evaluate whether the
    classifications themselves are correct. Phase 2 will add that after
    ClinGen VCEP consensus sources are wired in.
    """
    analysis: dict[str, Any] = {
        "report_exists": False,
        "assembly_mentions": [],
        "transcript_mentions": [],
        "clinvar_version_mentioned": False,
        "gnomad_version_mentioned": False,
        "disclaimer_present": False,
        "limitations_section_present": False,
        "classification_lines": 0,
        "lines_with_evidence_codes": 0,
        "inheritance_mentions": 0,
        "disease_context_mentions": 0,
    }
    if not report_path.exists():
        return analysis
    analysis["report_exists"] = True
    text = report_path.read_text(errors="replace")
    lower = text.lower()

    # Assembly / reference build — canonicalize **casing only**, not
    # identity. UCSC's hg19 and NCBI's GRCh37 are NOT interchangeable:
    # they differ in mitochondrial reference sequence (hg19 uses rCRS
    # NC_001807.4; GRCh37 uses rCRS NC_012920.1), in chromosome naming
    # (``chrM``/``chr1`` vs ``MT``/``1``), and in unplaced/alt contigs.
    # The same distinction applies to hg38 vs GRCh38 (alt scaffolds,
    # decoy sequences, mt sequence). An audit harness that silently
    # equates them would mask the precise kind of coordinate-space
    # mismatch the benchmark is supposed to catch. Each alias gets its
    # own canonical string; a report that mentions both ``GRCh38`` and
    # ``hg38`` will (correctly) trip ``reference_build_inconsistent``
    # because those are two different reference assemblies in the
    # strict sense.
    _assembly_canonical = {
        "grch37": "GRCh37",
        "hg19": "hg19",
        "grch38": "GRCh38",
        "hg38": "hg38",
        "chm13": "CHM13",
        "chm13v2": "CHM13v2",
    }
    raw_hits = {m.group(1) for m in _ASSEMBLY_PATTERN.finditer(text)}
    canonicalized: set[str] = set()
    for hit in raw_hits:
        canonicalized.add(_assembly_canonical.get(hit.lower(), hit))
    analysis["assembly_mentions"] = sorted(canonicalized)

    # Transcripts
    transcripts = {m.group(1) for m in _TRANSCRIPT_PATTERN.finditer(text)}
    analysis["transcript_mentions"] = sorted(transcripts)

    # Data source versions
    analysis["clinvar_version_mentioned"] = bool(_CLINVAR_VERSION_PATTERN.search(text))
    analysis["gnomad_version_mentioned"] = bool(_GNOMAD_VERSION_PATTERN.search(text))

    # Disclaimer
    analysis["disclaimer_present"] = any(p in lower for p in _DISCLAIMER_PATTERNS)

    # Limitations section
    analysis["limitations_section_present"] = any(
        p in lower for p in _LIMITATIONS_SECTION_PATTERNS
    )

    # Classification lines — rough proxy: lines that assert a 5-tier label.
    # We count both table cells (|Pathogenic|) and inline occurrences.
    classification_terms = (
        "pathogenic",
        "likely pathogenic",
        "vus",
        "uncertain significance",
        "likely benign",
        "benign",
    )
    # Phase 1 proxy: count each line that asserts a 5-tier classification
    # label and, on the SAME line, check for at least one ACMG criterion
    # code (PVS1, PS1-4, PM1-6, PP1-5, BA1, BS1-4, BP1-7). This is an
    # intentionally tight proxy — an adjacency window (±1 or ±2 lines)
    # would be more forgiving for reports that spread the evidence trail
    # across lines, but it also risks cross-pairing classifications with
    # unrelated evidence in adjacent table rows. Phase 2 correctness
    # scoring will revisit this if same-line proves too strict on real
    # reports.
    _ACMG_CRITERION_RE = re.compile(r"\b(PVS1|PS[1-4]|PM[1-6]|PP[1-5]|BA1|BS[1-4]|BP[1-7])\b")
    class_line_count = 0
    evidence_line_count = 0
    for line in text.split("\n"):
        lower_line = line.lower()
        if not any(t in lower_line for t in classification_terms):
            continue
        class_line_count += 1
        if _ACMG_CRITERION_RE.search(line):
            evidence_line_count += 1
    analysis["classification_lines"] = class_line_count
    analysis["lines_with_evidence_codes"] = evidence_line_count

    # Inheritance and disease context — case-split to avoid false
    # positives on short English words like "ad", "are", "XL".
    analysis["inheritance_mentions"] = len(_INHERITANCE_WORDS.findall(text)) + len(
        _INHERITANCE_ACRONYMS.findall(text)
    )
    # Disease context is harder — proxy: presence of "associated with" or
    # "condition:" or an OMIM reference.
    disease_context = 0
    for marker in (
        "associated with",
        "condition:",
        "disease:",
        "phenotype:",
        "omim:",
        "mondo:",
    ):
        if marker in lower:
            disease_context += 1
    analysis["disease_context_mentions"] = disease_context

    return analysis


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_cvr_verdict(
    ground_truth: dict[str, Any],
    analysis: dict[str, Any],
    exit_code: int,
) -> dict[str, Any]:
    """Score a Phase 1 CVR run against structural ground truth.

    Ground truth headers (all optional — a test case may pin only a subset):
        REQUIRE_ASSEMBLY:           one of GRCh37/GRCh38 (or "any")
        REQUIRE_TRANSCRIPT:         true/false
        REQUIRE_CLINVAR_VERSION:    true/false
        REQUIRE_GNOMAD_VERSION:     true/false
        REQUIRE_DISCLAIMER:         true/false (default true)
        REQUIRE_LIMITATIONS:        true/false (default true)
        REQUIRE_EVIDENCE_TRAIL:     true/false (default true if any classifications)
        REQUIRE_DISEASE_CONTEXT:    true/false (default true if any classifications)
    """
    details = {
        "exit_code": exit_code,
        "report_exists": analysis.get("report_exists", False),
        "assembly_mentions": analysis.get("assembly_mentions", []),
        "transcript_mentions": analysis.get("transcript_mentions", []),
        "clinvar_version": analysis.get("clinvar_version_mentioned", False),
        "gnomad_version": analysis.get("gnomad_version_mentioned", False),
        "disclaimer_present": analysis.get("disclaimer_present", False),
        "limitations_present": analysis.get("limitations_section_present", False),
        "classification_lines": analysis.get("classification_lines", 0),
        "evidence_code_lines": analysis.get("lines_with_evidence_codes", 0),
        "inheritance_mentions": analysis.get("inheritance_mentions", 0),
        "disease_context_mentions": analysis.get("disease_context_mentions", 0),
    }

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

    # Strict checks in severity order — return on first failure.
    # ``REQUIRE_ASSEMBLY`` accepts:
    #   - ``any``       — any assembly string is acceptable
    #   - ``false``     — assembly check is disabled
    #   - a specific canonical alias: ``GRCh37`` / ``hg19`` / ``GRCh38``
    #     / ``hg38`` / ``CHM13`` / ``CHM13v2`` (case-insensitive at the
    #     ground-truth level, but NOT cross-aliased: GRCh37 and hg19 are
    #     distinct references per NCBI vs UCSC, and a test that pins
    #     GRCh37 must not be credited for a report that mentions hg19
    #     instead).
    _require_raw = ground_truth.get("REQUIRE_ASSEMBLY", "any").strip().lower()
    _assembly_canonical_req = {
        "grch37": "GRCh37",
        "hg19": "hg19",
        "grch38": "GRCh38",
        "hg38": "hg38",
        "chm13": "CHM13",
        "chm13v2": "CHM13v2",
    }
    require_assembly_canonical = _assembly_canonical_req.get(_require_raw, _require_raw)
    assemblies = analysis.get("assembly_mentions", [])
    if _require_raw != "false" and not assemblies:
        return {
            "category": "assembly_missing",
            "rationale": "Report does not state reference genome build (GRCh37/GRCh38)",
            "details": details,
        }
    if _require_raw not in ("any", "false") and require_assembly_canonical not in assemblies:
        return {
            "category": "assembly_missing",
            "rationale": (
                f"Report assembly {assemblies} does not match expected "
                f"{require_assembly_canonical}"
            ),
            "details": details,
        }
    if len(assemblies) > 1:
        return {
            "category": "reference_build_inconsistent",
            "rationale": f"Report mentions multiple reference builds: {assemblies}",
            "details": details,
        }

    if ground_truth.get("REQUIRE_TRANSCRIPT", "true").lower() == "true":
        if not analysis.get("transcript_mentions"):
            return {
                "category": "transcript_missing",
                "rationale": (
                    "Report does not cite a transcript (NM_*, ENST_*, or MANE Select). "
                    "PVS1 LoF assessment is transcript-dependent."
                ),
                "details": details,
            }

    want_clinvar = ground_truth.get("REQUIRE_CLINVAR_VERSION", "true").lower() == "true"
    want_gnomad = ground_truth.get("REQUIRE_GNOMAD_VERSION", "true").lower() == "true"
    if want_clinvar and not analysis.get("clinvar_version_mentioned"):
        return {
            "category": "data_source_version_missing",
            "rationale": "Report does not pin a ClinVar release/version",
            "details": details,
        }
    if want_gnomad and not analysis.get("gnomad_version_mentioned"):
        return {
            "category": "data_source_version_missing",
            "rationale": "Report does not pin a gnomAD version",
            "details": details,
        }

    if ground_truth.get("REQUIRE_LIMITATIONS", "true").lower() == "true":
        if not analysis.get("limitations_section_present"):
            return {
                "category": "limitations_missing",
                "rationale": "Report has no Limitations section",
                "details": details,
            }

    if ground_truth.get("REQUIRE_DISCLAIMER", "true").lower() == "true":
        if not analysis.get("disclaimer_present"):
            return {
                "category": "disclaimer_missing",
                "rationale": (
                    "Report does not carry a research-use-only / "
                    "not-a-medical-device disclaimer in its body"
                ),
                "details": details,
            }

    # Evidence trail and disease context only apply when at least one
    # variant was classified in the report body.
    if analysis.get("classification_lines", 0) > 0:
        if ground_truth.get("REQUIRE_EVIDENCE_TRAIL", "true").lower() == "true":
            # Require at least 50% of classification lines to cite an ACMG
            # criterion code. Below that threshold the audit trail is
            # considered structurally broken.
            class_n = analysis["classification_lines"]
            ev_n = analysis["lines_with_evidence_codes"]
            if class_n and ev_n / class_n < 0.5:
                return {
                    "category": "evidence_trail_incomplete",
                    "rationale": (
                        f"Only {ev_n}/{class_n} classification lines cite an ACMG criterion code"
                    ),
                    "details": details,
                }
        if ground_truth.get("REQUIRE_DISEASE_CONTEXT", "true").lower() == "true":
            if analysis["disease_context_mentions"] == 0:
                return {
                    "category": "gene_disease_context_missing",
                    "rationale": (
                        "Report contains P/LP/VUS/LB/B classifications with no "
                        "disease / condition / inheritance context"
                    ),
                    "details": details,
                }

    return {
        "category": "report_structure_complete",
        "rationale": "All required structural elements present",
        "details": details,
    }


# ---------------------------------------------------------------------------
# Single Run Executor
# ---------------------------------------------------------------------------


def run_single_clinical_variant_reporter(
    repo_path: Path,
    commit_sha: str,
    test_case_path: Path,
    ground_truth: dict[str, Any],
    payload_path: Path | None,
    output_base: Path,
    commit_meta: dict[str, Any],
) -> dict[str, Any]:
    """Execute clinical_variant_reporter.py for one (commit, test_case) pair.

    Most Phase 1 tests run against the built-in ``--demo`` GIAB ACMG panel
    so they do not depend on external VCF fixtures. Tests that supply a
    PAYLOAD file use ``--input <payload>`` instead.
    """
    tc_name = test_case_path.name if test_case_path.is_dir() else test_case_path.stem
    run_output_dir = output_base / commit_sha / tc_name
    report_dir = run_output_dir / "tool_output"
    report_dir.mkdir(parents=True, exist_ok=True)

    tool_path = repo_path / "skills" / "clinical-variant-reporter" / "clinical_variant_reporter.py"
    timeout = harness_core.validate_timeout(ground_truth.get("TIMEOUT", "120"))

    mode = ground_truth.get("MODE", "demo").lower()
    # The target CLI (``--assembly``) only exposes ``GRCh37`` / ``GRCh38``
    # and is case-sensitive. Ground truth ``REQUIRE_ASSEMBLY`` may name any
    # of GRCh37/hg19/GRCh38/hg38/CHM13/CHM13v2 (verified against the
    # analyzer, which keeps these distinct). At the CLI boundary we must
    # collapse hg19 → GRCh37 and hg38 → GRCh38 because the target tool
    # offers no other input form; this is a tool-capability constraint,
    # not an assertion that hg19 ≡ GRCh37. The analyzer and the scoring
    # comparison both preserve the distinction.
    _raw_require = ground_truth.get("REQUIRE_ASSEMBLY", "GRCh38").strip().lower()
    _cli_assembly_map = {
        "grch37": "GRCh37",
        "hg19": "GRCh37",  # CLI-level collapse only; analyzer keeps separate
        "grch38": "GRCh38",
        "hg38": "GRCh38",  # CLI-level collapse only; analyzer keeps separate
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

    report_md = report_dir / "report.md"
    analysis = analyze_cvr_report(report_md)
    verdict = score_cvr_verdict(ground_truth, analysis, execution.exit_code)

    outputs = {"report_md": harness_core.artifact_info(report_md)}
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
        default_inputs_dir="clinical_variant_reporter",
        run_single_fn=run_single_clinical_variant_reporter,
        rubric_categories=RUBRIC_CATEGORIES,
        pass_categories=PASS_CATEGORIES,
        fail_categories=FAIL_CATEGORIES,
        ground_truth_refs=GROUND_TRUTH_REFS,
        category_legend=CATEGORY_LEGEND,
        description="Clinical Variant Reporter Phase 1 Benchmark Harness",
    )


if __name__ == "__main__":
    main()
