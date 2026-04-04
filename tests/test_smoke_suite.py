"""Smoke tests for every registered benchmark harness.

Runs each harness in --smoke mode and asserts:
  - Process completes without harness crash
  - Output files exist (manifest.json, summary.json, etc.)
  - At least one test case was evaluated
  - Verdict structure is valid
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from clawbio_bench.cli import HARNESS_REGISTRY

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.benchmark,
]


def test_harness_smoke(harness_name, clawbio_repo, tmp_path):
    """Run one harness in --smoke mode and assert basic success criteria."""
    output_dir = tmp_path / "smoke_output"
    cmd = [
        sys.executable,
        "-m",
        "clawbio_bench",
        "--smoke",
        "--harness",
        harness_name,
        "--repo",
        str(clawbio_repo),
        "--output",
        str(output_dir),
        "--allow-dirty",
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )

    # The suite exits non-zero when benchmarks fail — that's expected
    # We only care that it didn't crash (returncode should be 0 or 1)
    assert result.returncode in (0, 1), (
        f"Harness {harness_name} crashed (exit {result.returncode})\n"
        f"STDOUT:\n{result.stdout[-1000:]}\n"
        f"STDERR:\n{result.stderr[-2000:]}"
    )

    benchmark_name = HARNESS_REGISTRY[harness_name]["benchmark_name"]
    harness_output = output_dir / benchmark_name

    # Debug: show what was actually produced
    if not harness_output.exists():
        # Check if outputs went to a different location
        actual_contents = list(output_dir.rglob("manifest.json"))
        raise AssertionError(
            f"Harness output dir missing: {harness_output}\n"
            f"output_dir contents: {list(output_dir.iterdir()) if output_dir.exists() else 'N/A'}\n"
            f"manifest.json found at: {actual_contents}\n"
            f"STDOUT:\n{result.stdout[-1500:]}\n"
            f"STDERR:\n{result.stderr[-500:]}"
        )

    # Check output files exist
    for fname in ["manifest.json", "summary.json", "heatmap_data.json", "all_verdicts.json"]:
        fpath = harness_output / fname
        assert fpath.exists(), f"Missing output file: {fpath}"

    # Check summary has evaluated > 0
    summary = json.loads((harness_output / "summary.json").read_text())
    head_key = next((k for k in summary if k != "_meta"), None)
    assert head_key is not None, "summary.json missing commit entry"

    evaluated = summary[head_key].get("evaluated", 0)
    assert evaluated > 0, f"Smoke test for {harness_name} produced zero evaluated results"

    # Check all verdicts have valid structure
    verdicts = json.loads((harness_output / "all_verdicts.json").read_text())
    assert len(verdicts) > 0, "No verdicts produced"

    for v in verdicts:
        assert "verdict" in v, f"Verdict missing 'verdict' key: {v.get('test_case')}"
        assert "category" in v["verdict"], "Verdict missing category"
        assert "rationale" in v["verdict"], "Verdict missing rationale"


def test_package_imports():
    """Verify all 5 harnesses can be imported cleanly."""
    from clawbio_bench import core
    from clawbio_bench.harnesses import (
        equity_harness,
        metagenomics_harness,
        nutrigx_harness,
        orchestrator_harness,
        pharmgx_harness,
    )

    assert hasattr(core, "run_benchmark_matrix")
    assert hasattr(core, "BenchmarkConfigError")

    for mod in [
        orchestrator_harness,
        equity_harness,
        nutrigx_harness,
        pharmgx_harness,
        metagenomics_harness,
    ]:
        assert hasattr(mod, "RUBRIC_CATEGORIES"), f"{mod.__name__} missing RUBRIC_CATEGORIES"
        assert hasattr(mod, "BENCHMARK_NAME"), f"{mod.__name__} missing BENCHMARK_NAME"


def test_core_ground_truth_parsing(tmp_path):
    """Test parse_ground_truth with various formats."""
    from clawbio_bench.core import parse_ground_truth

    gt_file = tmp_path / "ground_truth.txt"
    gt_file.write_text(
        "# BENCHMARK: test v1.0\n"
        "# GROUND_TRUTH_FST: 0.300\n"
        "# FINDING_CATEGORY: fst_correct\n"
        "# PAYLOAD: input.vcf\n"
        "not a comment\n"
    )
    gt = parse_ground_truth(gt_file)
    assert gt["BENCHMARK"] == "test v1.0"
    assert gt["GROUND_TRUTH_FST"] == "0.300"
    assert gt["FINDING_CATEGORY"] == "fst_correct"
    assert gt["PAYLOAD"] == "input.vcf"


def test_core_resolve_test_case_model_b(tmp_path):
    """Test resolve_test_case with Model B (directory)."""
    from clawbio_bench.core import resolve_test_case

    tc_dir = tmp_path / "test_case"
    tc_dir.mkdir()
    (tc_dir / "ground_truth.txt").write_text(
        "# PAYLOAD: input.vcf\n# FINDING_CATEGORY: fst_correct\n"
    )
    (tc_dir / "input.vcf").write_text("##fileformat=VCFv4.1\n")

    gt, payload = resolve_test_case(tc_dir)
    assert gt["PAYLOAD"] == "input.vcf"
    assert payload is not None
    assert payload.name == "input.vcf"


def test_core_path_traversal_blocked(tmp_path):
    """Test that path traversal in PAYLOAD is blocked."""
    from clawbio_bench.core import resolve_test_case

    tc_dir = tmp_path / "test_case"
    tc_dir.mkdir()
    (tc_dir / "ground_truth.txt").write_text("# PAYLOAD: ../../etc/passwd\n")

    with pytest.raises(ValueError, match="Path traversal"):
        resolve_test_case(tc_dir)
