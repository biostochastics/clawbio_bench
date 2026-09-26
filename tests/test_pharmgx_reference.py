"""Regression coverage for the GRCh37 fixture's documented alternatives (#139)."""

from pathlib import Path

import pytest

from clawbio_bench import core
from clawbio_bench.harnesses.pharmgx_harness import (
    analyze_report,
    analyze_stderr,
    run_single_pharmgx,
    score_pgx_verdict,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "src/clawbio_bench/test_cases/pharmgx/grch37_reference_mismatch.txt"
)


def write_report(tmp_path, phenotype, disclosure=""):
    report = tmp_path / "report.md"
    report.write_text(
        "# PharmGx report\n\n"
        f"{disclosure}\n\n"
        "| Gene | Diplotype | Phenotype |\n"
        "| --- | --- | --- |\n"
        f"| CYP2D6 | *4/*4 | {phenotype} |\n"
    )
    return analyze_report(report)


@pytest.mark.parametrize("phenotype", ["Poor Metabolizer", "Poor Metaboliser"])
def test_rsid_call_satisfies_bundled_reference_fixture(tmp_path, phenotype):
    verdict = score_pgx_verdict(
        core.parse_ground_truth(FIXTURE), write_report(tmp_path, phenotype), [], {}, 0
    )
    assert verdict["category"] == "correct_determinate", verdict


@pytest.mark.parametrize(
    ("phenotype", "disclosure", "expected"),
    [
        ("Indeterminate (unrecognised reference genome)", "", "correct_indeterminate"),
        ("Indeterminate (reference genome mismatch)", "", "correct_indeterminate"),
        ("Indeterminate", "WARNING: Genome build mismatch.", "correct_indeterminate"),
        ("Indeterminate", "", "incorrect_indeterminate"),
        ("Indeterminate", "No reference genome mismatch detected.", "incorrect_indeterminate"),
        (
            "Indeterminate",
            "Reference genome mismatch was not detected.",
            "incorrect_indeterminate",
        ),
        ("Normal Metabolizer", "WARNING: Genome build mismatch.", "incorrect_determinate"),
        ("Intermediate Metabolizer", "", "incorrect_determinate"),
        ("not Poor Metaboliser", "", "incorrect_determinate"),
    ],
)
def test_reference_alternative_requires_safe_disclosed_outcome(
    tmp_path, phenotype, disclosure, expected
):
    verdict = score_pgx_verdict(
        core.parse_ground_truth(FIXTURE),
        write_report(tmp_path, phenotype, disclosure),
        [],
        {},
        0,
    )
    assert verdict["category"] == expected, verdict


def test_stderr_only_mismatch_is_a_disclosure_failure(tmp_path):
    verdict = score_pgx_verdict(
        core.parse_ground_truth(FIXTURE),
        write_report(tmp_path, "Indeterminate"),
        analyze_stderr("WARNING: Reference genome mismatch: GRCh37 input; expected GRCh38."),
        {},
        0,
    )
    assert verdict["category"] == "disclosure_failure", verdict


@pytest.mark.parametrize(
    ("exit_code", "stderr", "expected"),
    [
        (
            2,
            "ERROR: Reference genome mismatch: GRCh37 input; expected GRCh38.",
            "correct_indeterminate",
        ),
        (1, "ERROR: Unsupported reference genome.", "correct_indeterminate"),
        (2, "python: cannot open file pharmgx_reporter.py", "incorrect_determinate"),
        (124, "ERROR: Reference genome mismatch", "incorrect_determinate"),
        (-9, "ERROR: Reference genome mismatch", "incorrect_determinate"),
        (0, "WARNING: Reference genome mismatch", "incorrect_determinate"),
        (
            1,
            "WARNING: Reference genome mismatch\nTraceback (most recent call last):\nValueError",
            "incorrect_determinate",
        ),
    ],
)
def test_rejection_through_real_harness(tmp_path, exit_code, stderr, expected):
    """Exercise capture_execution, including errors omitted by analyze_stderr."""
    repo = tmp_path / "target"
    skill = repo / "skills/pharmgx-reporter/pharmgx_reporter.py"
    skill.parent.mkdir(parents=True)
    skill.write_text(f"import sys\nprint({stderr!r}, file=sys.stderr)\nsys.exit({exit_code})\n")
    verdict = run_single_pharmgx(
        repo,
        "a" * 40,
        FIXTURE,
        core.parse_ground_truth(FIXTURE),
        FIXTURE,
        tmp_path / "results",
        {"sha": "a" * 40, "short_sha": "a" * 7, "date": "2026-09-16", "message": "fixture"},
    )
    assert verdict["verdict"]["category"] == expected, verdict


def test_reference_rejection_is_opt_in(tmp_path):
    ground_truth = core.parse_ground_truth(FIXTURE)
    ground_truth.pop("ALLOW_REFERENCE_MISMATCH", None)
    verdict = score_pgx_verdict(
        ground_truth,
        write_report(tmp_path, "Indeterminate (reference genome mismatch)"),
        [],
        {},
        0,
    )
    assert verdict["category"] == "incorrect_determinate", verdict


@pytest.mark.parametrize(
    ("phenotype", "expected"),
    [
        (None, "correct_indeterminate"),
        ("Indeterminate", "correct_indeterminate"),
        ("Poor Metabolizer", "incorrect_determinate"),
        ("Normal Metabolizer", "incorrect_determinate"),
    ],
)
def test_rejection_with_error_report(tmp_path, phenotype, expected):
    repo = tmp_path / "target"
    skill = repo / "skills/pharmgx-reporter/pharmgx_reporter.py"
    skill.parent.mkdir(parents=True)
    report = "ERROR: Reference genome mismatch: GRCh37 input; expected GRCh38.\n\n"
    if phenotype is not None:
        report += (
            "| Gene | Diplotype | Phenotype |\n| --- | --- | --- |\n"
            f"| CYP2D6 | unknown | {phenotype} |\n"
        )
    skill.write_text(
        "import sys\nfrom pathlib import Path\n"
        "output = Path(sys.argv[sys.argv.index('--output') + 1])\n"
        f"(output / 'report.md').write_text({report!r})\n"
        "sys.exit(2)\n"
    )
    verdict = run_single_pharmgx(
        repo,
        "a" * 40,
        FIXTURE,
        core.parse_ground_truth(FIXTURE),
        FIXTURE,
        tmp_path / "results",
        {"sha": "a" * 40, "short_sha": "a" * 7, "date": "2026-09-16", "message": "fixture"},
    )
    assert verdict["verdict"]["category"] == expected, verdict
