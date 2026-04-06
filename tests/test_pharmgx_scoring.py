"""Unit tests for pharmgx_harness.score_pgx_verdict — every rubric branch."""

from __future__ import annotations

from clawbio_bench.harnesses.pharmgx_harness import (
    _phenotype_matches,
    score_pgx_verdict,
)

# ---------------------------------------------------------------------------
# _phenotype_matches — fuzzy matching logic
# ---------------------------------------------------------------------------


class TestPhenotypeMatches:
    def test_exact_match(self):
        assert _phenotype_matches("Normal Metabolizer", "Normal Metabolizer")

    def test_case_insensitive(self):
        assert _phenotype_matches("normal metabolizer", "NORMAL METABOLIZER")

    def test_canonical_term_match(self):
        assert _phenotype_matches("CYP2D6 Normal Metabolizer (AS=2.0)", "Normal Metabolizer")

    def test_different_phenotypes_do_not_match(self):
        assert not _phenotype_matches("Normal Metabolizer", "Poor Metabolizer")
        assert not _phenotype_matches("Intermediate Metabolizer", "Ultrarapid Metabolizer")

    def test_empty_strings(self):
        assert not _phenotype_matches("", "Normal Metabolizer")
        assert not _phenotype_matches("Normal Metabolizer", "")
        assert not _phenotype_matches("", "")

    def test_not_genotyped(self):
        assert _phenotype_matches("not genotyped", "Not Genotyped")

    def test_indeterminate(self):
        assert _phenotype_matches("Indeterminate", "indeterminate")

    def test_rejects_negated_term(self):
        # "not normal metabolizer" should NOT match "normal metabolizer"
        assert not _phenotype_matches("not normal metabolizer", "normal metabolizer")
        assert not _phenotype_matches("normal metabolizer", "not normal metabolizer")

    def test_both_negated_still_match(self):
        # Both sides negated — semantically identical, should match
        assert _phenotype_matches("not normal metabolizer", "not normal metabolizer")

    def test_expressor_non_expressor_collision(self):
        # Round 2 regression: 'expressor' is a substring of 'non-expressor',
        # and Python \b treats '-' as a word boundary. The left-anchor must
        # reject hyphen prefixes too, otherwise opposite clinical phenotypes
        # would be scored as matching.
        assert not _phenotype_matches("non-expressor", "expressor")
        assert not _phenotype_matches("expressor", "non-expressor")
        assert _phenotype_matches("expressor", "expressor")
        assert _phenotype_matches("non-expressor", "non-expressor")

    def test_short_substring_bypass_of_negation_rejected(self):
        """Tier-A regression: the substring fallback must reject candidates
        that land inside a negated context in the longer string.

        Previously, ``"normal"`` on one side would match inside
        ``"not normal metabolizer"`` on the other via the
        ``obs_n in exp_n`` short-circuit, crediting opposite clinical
        phenotypes as equivalent.
        """
        # "normal" is not a _KEY_TERM, so the same-term guard doesn't fire.
        # The context-aware guard must catch it.
        assert not _phenotype_matches("normal", "not normal metabolizer")
        assert not _phenotype_matches("not normal metabolizer", "normal")


# ---------------------------------------------------------------------------
# score_pgx_verdict — core scoring engine, every rubric branch
# ---------------------------------------------------------------------------


def _report_analysis(
    *,
    report_exists: bool = True,
    gene_profiles: dict | None = None,
    drug_classifications: dict | None = None,
    warfarin_present: bool = False,
    warnings_in_report: list | None = None,
    data_quality_warning_present: bool = False,
) -> dict:
    return {
        "report_exists": report_exists,
        "gene_profiles": gene_profiles or {},
        "drug_classifications": drug_classifications or {},
        "warnings_in_report": warnings_in_report or [],
        "data_quality_warning_present": data_quality_warning_present,
        "warfarin_present": warfarin_present,
    }


def _result_json_analysis(
    *, has_tuple_keys: bool = False, exists: bool = True, valid_json: bool = True
) -> dict:
    return {
        "has_tuple_keys": has_tuple_keys,
        "exists": exists,
        "valid_json": valid_json,
        "drug_count": 0,
        "warfarin_in_results": False,
        "error": None,
    }


class TestCorrectDeterminate:
    def test_exact_phenotype_match(self):
        gt = {
            "FINDING_CATEGORY": "correct_determinate",
            "TARGET_GENE": "CYP2D6",
            "EXPECTED_EXIT_CODE": "0",
            "GROUND_TRUTH_PHENOTYPE": "Poor Metabolizer",
        }
        ra = _report_analysis(
            gene_profiles={"CYP2D6": {"diplotype": "*4/*4", "phenotype": "Poor Metabolizer"}}
        )
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "correct_determinate"

    def test_wrong_phenotype(self):
        gt = {
            "FINDING_CATEGORY": "correct_determinate",
            "TARGET_GENE": "CYP2D6",
            "EXPECTED_EXIT_CODE": "0",
            "GROUND_TRUTH_PHENOTYPE": "Poor Metabolizer",
        }
        ra = _report_analysis(
            gene_profiles={"CYP2D6": {"diplotype": "*1/*1", "phenotype": "Normal Metabolizer"}}
        )
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "incorrect_determinate"


class TestCorrectIndeterminate:
    def test_correctly_indeterminate(self):
        gt = {
            "FINDING_CATEGORY": "correct_indeterminate",
            "TARGET_GENE": "DPYD",
            "EXPECTED_EXIT_CODE": "0",
            "GROUND_TRUTH_PHENOTYPE": "Indeterminate",
        }
        ra = _report_analysis(
            gene_profiles={"DPYD": {"diplotype": "unknown", "phenotype": "Indeterminate"}}
        )
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "correct_indeterminate"

    def test_false_normal_on_indeterminate_case(self):
        gt = {
            "FINDING_CATEGORY": "correct_indeterminate",
            "TARGET_GENE": "CYP2D6",
            "EXPECTED_EXIT_CODE": "0",
            "GROUND_TRUTH_PHENOTYPE": "Indeterminate",
        }
        ra = _report_analysis(
            gene_profiles={"CYP2D6": {"diplotype": "*1/*1", "phenotype": "Normal Metabolizer"}}
        )
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "incorrect_determinate"


class TestOmission:
    def test_warfarin_silently_missing(self):
        gt = {
            "FINDING_CATEGORY": "omission",
            "TARGET_GENE": "VKORC1",
            "EXPECTED_EXIT_CODE": "0",
            "HAZARD_DRUG": "Warfarin",
        }
        ra = _report_analysis(
            drug_classifications={"Codeine": "standard"},
            warfarin_present=False,
        )
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "omission"

    def test_warfarin_crash_with_tuple_key_evidence(self):
        """Warfarin tuple-key bug: tool crashed AND result.json has the bug."""
        gt = {
            "FINDING_CATEGORY": "omission",
            "TARGET_GENE": "VKORC1",
            "EXPECTED_EXIT_CODE": "0",
            "HAZARD_DRUG": "Warfarin",
        }
        ra = _report_analysis(report_exists=True, warfarin_present=False)
        rja = _result_json_analysis(has_tuple_keys=True, valid_json=False)
        verdict = score_pgx_verdict(gt, ra, [], rja, 1)
        assert verdict["category"] == "omission"

    def test_warfarin_crash_WITHOUT_tuple_key_evidence_is_not_omission(self):
        """A random crash should NOT be credited as the known tuple-key bug."""
        gt = {
            "FINDING_CATEGORY": "omission",
            "TARGET_GENE": "VKORC1",
            "EXPECTED_EXIT_CODE": "0",
            "HAZARD_DRUG": "Warfarin",
        }
        ra = _report_analysis(report_exists=True, warfarin_present=False)
        rja = _result_json_analysis(has_tuple_keys=False, valid_json=True)
        verdict = score_pgx_verdict(gt, ra, [], rja, 1)
        # Without tuple-key evidence, this is just an incorrect_determinate crash
        assert verdict["category"] == "incorrect_determinate"


class TestScopeHonestIndeterminate:
    def test_tool_returns_indeterminate_for_cnv(self):
        """Tool correctly returns Indeterminate for a CNV scope limitation."""
        gt = {
            "FINDING_CATEGORY": "scope_honest_indeterminate",
            "TARGET_GENE": "CYP2D6",
            "EXPECTED_EXIT_CODE": "0",
            "GROUND_TRUTH_PHENOTYPE": "CYP2D6 Intermediate Metabolizer (*1/*5)",
            "GROUND_TRUTH_BEHAVIOR": "whole-gene deletion, CNV undetectable from SNP",
        }
        ra = _report_analysis(
            gene_profiles={"CYP2D6": {"diplotype": "unknown", "phenotype": "Indeterminate"}},
        )
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "scope_honest_indeterminate"

    def test_tool_discloses_cnv_limitation_in_report(self):
        """Tool reports a determinate phenotype but discloses CNV limitation for the target gene."""
        gt = {
            "FINDING_CATEGORY": "scope_honest_indeterminate",
            "TARGET_GENE": "CYP2D6",
            "EXPECTED_EXIT_CODE": "0",
            "GROUND_TRUTH_PHENOTYPE": "CYP2D6 Normal Metabolizer (*1/*1)",
            "GROUND_TRUTH_BEHAVIOR": "CNV not assessed",
        }
        ra = _report_analysis(
            gene_profiles={"CYP2D6": {"diplotype": "*1/*1", "phenotype": "Normal Metabolizer"}},
            data_quality_warning_present=True,
            warnings_in_report=["DATA QUALITY WARNING: CYP2D6 copy number not assessed"],
        )
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "scope_honest_indeterminate"

    def test_dqw_for_wrong_gene_is_disclosure_failure(self):
        """A DQW that names a different gene should NOT credit the target gene."""
        gt = {
            "FINDING_CATEGORY": "scope_honest_indeterminate",
            "TARGET_GENE": "CYP2D6",
            "EXPECTED_EXIT_CODE": "0",
            "GROUND_TRUTH_PHENOTYPE": "CYP2D6 Normal Metabolizer (*1/*1)",
            "GROUND_TRUTH_BEHAVIOR": "CNV not assessed",
        }
        ra = _report_analysis(
            gene_profiles={"CYP2D6": {"diplotype": "*1/*1", "phenotype": "Normal Metabolizer"}},
            data_quality_warning_present=True,
            warnings_in_report=["DATA QUALITY WARNING: UGT1A1 copy number not assessed"],
        )
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "disclosure_failure"

    def test_tool_discloses_cnv_via_warnings_in_report(self):
        """Tool reports determinate but warnings_in_report mentions CNV for gene."""
        gt = {
            "FINDING_CATEGORY": "scope_honest_indeterminate",
            "TARGET_GENE": "CYP2D6",
            "EXPECTED_EXIT_CODE": "0",
            "GROUND_TRUTH_PHENOTYPE": "CYP2D6 Normal Metabolizer (*1/*1)",
            "GROUND_TRUTH_BEHAVIOR": "CNV not assessed",
        }
        ra = _report_analysis(
            gene_profiles={"CYP2D6": {"diplotype": "*1/*1", "phenotype": "Normal Metabolizer"}},
            data_quality_warning_present=False,
            warnings_in_report=["report_mentions: CYP2D6 copy number not assessed"],
        )
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "scope_honest_indeterminate"

    def test_generic_warning_for_wrong_gene_not_credited(self):
        """A CNV warning for a different gene should not credit this gene."""
        gt = {
            "FINDING_CATEGORY": "scope_honest_indeterminate",
            "TARGET_GENE": "CYP2D6",
            "EXPECTED_EXIT_CODE": "0",
            "GROUND_TRUTH_PHENOTYPE": "CYP2D6 Intermediate Metabolizer",
            "GROUND_TRUTH_BEHAVIOR": "whole-gene deletion, CNV undetectable",
        }
        ra = _report_analysis(
            gene_profiles={"CYP2D6": {"diplotype": "*1/*1", "phenotype": "Normal Metabolizer"}},
            data_quality_warning_present=False,
            warnings_in_report=["report_mentions: UGT1A1 copy number limitation"],
        )
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "disclosure_failure"

    def test_tool_silently_reports_normal_no_disclosure(self):
        """Tool reports Normal without disclosing scope limitation → disclosure_failure."""
        gt = {
            "FINDING_CATEGORY": "scope_honest_indeterminate",
            "TARGET_GENE": "CYP2D6",
            "EXPECTED_EXIT_CODE": "0",
            "GROUND_TRUTH_PHENOTYPE": "CYP2D6 Intermediate Metabolizer (*1/*5)",
            "GROUND_TRUTH_BEHAVIOR": "whole-gene deletion, CNV undetectable from SNP",
        }
        ra = _report_analysis(
            gene_profiles={"CYP2D6": {"diplotype": "*1/*1", "phenotype": "Normal Metabolizer"}},
            data_quality_warning_present=False,
            warnings_in_report=[],
        )
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "disclosure_failure"


class TestDisclosureFailure:
    def test_warnings_on_stderr_not_in_report(self):
        gt = {
            "FINDING_CATEGORY": "disclosure_failure",
            "TARGET_GENE": "CYP2D6",
            "EXPECTED_EXIT_CODE": "0",
            "GROUND_TRUTH_PHENOTYPE": "Normal Metabolizer",
            "GROUND_TRUTH_BEHAVIOR": "CNV not detected",
        }
        ra = _report_analysis(
            gene_profiles={"CYP2D6": {"diplotype": "*1/*1", "phenotype": "Normal Metabolizer"}},
            data_quality_warning_present=False,
            warnings_in_report=[],
        )
        stderr_warnings = ["WARNING: CYP2D6 CNV not assessed"]
        verdict = score_pgx_verdict(gt, ra, stderr_warnings, _result_json_analysis(), 0)
        assert verdict["category"] == "disclosure_failure"


class TestExpectedExit:
    def test_expected_nonzero_exit_matched(self):
        gt = {
            "FINDING_CATEGORY": "correct_determinate",
            "EXPECTED_EXIT_CODE": "1",
        }
        ra = _report_analysis(report_exists=False)
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 1)
        assert verdict["category"] == "correct_determinate"

    def test_expected_nonzero_exit_unmatched(self):
        gt = {
            "FINDING_CATEGORY": "correct_determinate",
            "EXPECTED_EXIT_CODE": "1",
        }
        ra = _report_analysis(report_exists=True)
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "incorrect_determinate"


class TestNoReport:
    def test_exit_zero_no_report(self):
        gt = {
            "FINDING_CATEGORY": "correct_determinate",
            "TARGET_GENE": "CYP2D6",
            "EXPECTED_EXIT_CODE": "0",
            "GROUND_TRUTH_PHENOTYPE": "Normal Metabolizer",
        }
        ra = _report_analysis(report_exists=False)
        verdict = score_pgx_verdict(gt, ra, [], _result_json_analysis(), 0)
        assert verdict["category"] == "incorrect_determinate"


# ---------------------------------------------------------------------------
# Schema compliance — all scoring output must be a valid rubric category
# (integration check across scoring + validator)
# ---------------------------------------------------------------------------


class TestPgxScoringSchemaCompliance:
    def test_every_branch_returns_known_category(self):
        """Every branch of score_pgx_verdict must return a rubric category."""
        from clawbio_bench.harnesses.pharmgx_harness import RUBRIC_CATEGORIES

        cases = [
            (
                {
                    "FINDING_CATEGORY": "correct_determinate",
                    "TARGET_GENE": "CYP2D6",
                    "EXPECTED_EXIT_CODE": "0",
                    "GROUND_TRUTH_PHENOTYPE": "Poor Metabolizer",
                },
                _report_analysis(
                    gene_profiles={
                        "CYP2D6": {"diplotype": "*4/*4", "phenotype": "Poor Metabolizer"}
                    }
                ),
                [],
                _result_json_analysis(),
                0,
            ),
            (
                {
                    "FINDING_CATEGORY": "omission",
                    "TARGET_GENE": "VKORC1",
                    "EXPECTED_EXIT_CODE": "0",
                    "HAZARD_DRUG": "Warfarin",
                },
                _report_analysis(drug_classifications={}, warfarin_present=False),
                [],
                _result_json_analysis(),
                0,
            ),
            (
                {
                    "FINDING_CATEGORY": "correct_indeterminate",
                    "TARGET_GENE": "DPYD",
                    "EXPECTED_EXIT_CODE": "0",
                    "GROUND_TRUTH_PHENOTYPE": "Indeterminate",
                },
                _report_analysis(
                    gene_profiles={"DPYD": {"diplotype": "unknown", "phenotype": "Indeterminate"}}
                ),
                [],
                _result_json_analysis(),
                0,
            ),
            (
                {
                    "FINDING_CATEGORY": "scope_honest_indeterminate",
                    "TARGET_GENE": "CYP2D6",
                    "EXPECTED_EXIT_CODE": "0",
                    "GROUND_TRUTH_PHENOTYPE": "CYP2D6 Intermediate Metabolizer",
                    "GROUND_TRUTH_BEHAVIOR": "CNV undetectable",
                },
                _report_analysis(
                    gene_profiles={
                        "CYP2D6": {"diplotype": "unknown", "phenotype": "Indeterminate"}
                    }
                ),
                [],
                _result_json_analysis(),
                0,
            ),
        ]
        for gt, ra, warns, rja, exit_code in cases:
            verdict = score_pgx_verdict(gt, ra, warns, rja, exit_code)
            assert verdict["category"] in RUBRIC_CATEGORIES, (
                f"Category {verdict['category']!r} not in rubric"
            )
