"""Tests for the markdown report renderer (PR-comment rendering + baseline diff)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clawbio_bench.markdown_report import (
    STICKY_MARKER,
    _extract_findings,
    _finding_key,
    render_markdown_report,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_aggregate(
    commit: str,
    findings_by_harness: dict[str, list[dict]],
    mode: str = "smoke",
) -> dict:
    """Build a minimal aggregate_report.json-shaped dict for tests."""
    harnesses = {}
    total_pass = 0
    total_eval = 0
    for name, findings in findings_by_harness.items():
        fails = len(findings)
        passes = 10 - fails
        harnesses[name] = {
            "version": "1.0.0",
            "pass": fails == 0,
            "total_cases": 10,
            "evaluated": 10,
            "harness_errors": 0,
            "pass_count": passes,
            "fail_count": fails,
            "pass_rate": round(passes / 10 * 100, 1),
            "pass_categories": ["correct"],
            "categories": {"correct": passes, "incorrect": fails},
            "critical_failures": findings,
        }
        total_pass += passes
        total_eval += 10
    return {
        "benchmark_suite_version": "1.0.1",
        "date": "2026-04-04",
        "clawbio_commit": commit,
        "mode": mode,
        "wall_clock_seconds": 12.3,
        "environment": {"python": "3.12.0", "platform": "linux"},
        "harnesses": harnesses,
        "overall": {
            "pass": total_pass == total_eval,
            "total_cases": total_eval,
            "total_evaluated": total_eval,
            "total_harness_errors": 0,
            "total_pass": total_pass,
            "total_pass_rate": round(total_pass / total_eval * 100, 1) if total_eval else 0.0,
            "blocking_skills": [n for n, h in harnesses.items() if not h["pass"]],
        },
    }


@pytest.fixture
def results_dir(tmp_path: Path) -> Path:
    """A results dir with a current aggregate_report.json."""
    agg = _make_aggregate(
        commit="abc12345",
        findings_by_harness={
            "bio-orchestrator": [
                {"test": "or_01", "category": "routed_wrong", "rationale": "wrong tool"},
                {"test": "or_02", "category": "stub_silent", "rationale": "silent stub"},
            ],
            "equity-scorer": [
                {"test": "eq_01", "category": "fst_incorrect", "rationale": "FST off"},
            ],
        },
    )
    path = tmp_path / "results"
    path.mkdir()
    (path / "aggregate_report.json").write_text(json.dumps(agg), encoding="utf-8")
    return path


@pytest.fixture
def baseline_file(tmp_path: Path) -> Path:
    """A baseline aggregate with a different finding set.

    * ``eq_01`` is now resolved (was fst_incorrect, no longer failing)
    * ``or_02`` is unchanged
    * A brand-new ``or_03`` appears only in the current run (tested via
      results_dir fixture — here we represent baseline state)
    * ``or_01`` is unchanged (appears in both)
    """
    agg = _make_aggregate(
        commit="def67890",
        findings_by_harness={
            "bio-orchestrator": [
                {"test": "or_01", "category": "routed_wrong", "rationale": "wrong tool"},
                {"test": "or_02", "category": "stub_silent", "rationale": "silent stub"},
            ],
            "equity-scorer": [
                {"test": "eq_01", "category": "fst_incorrect", "rationale": "FST off"},
                # This finding is resolved in the current run:
                {"test": "eq_02", "category": "heim_unbounded", "rationale": "HEIM unbounded"},
            ],
        },
    )
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(agg), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Finding extraction
# ---------------------------------------------------------------------------


def test_extract_findings_flattens_across_harnesses():
    agg = _make_aggregate(
        commit="abc",
        findings_by_harness={
            "harness-a": [{"test": "t1", "category": "c1", "rationale": "r1"}],
            "harness-b": [
                {"test": "t2", "category": "c2", "rationale": "r2"},
                {"test": "t3", "category": "c3", "rationale": "r3"},
            ],
        },
    )
    findings = _extract_findings(agg)
    assert len(findings) == 3
    # Deterministic sort
    assert [f["test"] for f in findings] == ["t1", "t2", "t3"]


def test_finding_key_is_stable_across_runs():
    f1 = {"harness": "h", "test": "t", "category": "c", "rationale": "anything"}
    f2 = {"harness": "h", "test": "t", "category": "c", "rationale": "different text"}
    # Rationale changes do NOT change identity — only (harness, test, category) do
    assert _finding_key(f1) == _finding_key(f2)


def test_finding_key_category_change_is_new_finding():
    f1 = {"harness": "h", "test": "t", "category": "fst_incorrect", "rationale": ""}
    f2 = {"harness": "h", "test": "t", "category": "fst_mislabeled", "rationale": ""}
    assert _finding_key(f1) != _finding_key(f2)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_contains_sticky_marker(results_dir: Path):
    md = render_markdown_report(results_dir)
    assert md.startswith(STICKY_MARKER)


def test_render_no_baseline_shows_current_findings(results_dir: Path):
    md = render_markdown_report(results_dir)
    assert "clawbio-bench audit" in md
    assert "bio-orchestrator" in md
    assert "or_01" in md
    assert "routed_wrong" in md
    # No baseline — no diff section
    assert "New findings" not in md
    assert "Resolved findings" not in md


def test_render_with_baseline_diffs_new_and_resolved(results_dir: Path, baseline_file: Path):
    md = render_markdown_report(results_dir, baseline=baseline_file)
    # eq_02 was in baseline, not in current → resolved
    assert "Resolved findings" in md
    assert "eq_02" in md
    # or_01, or_02, eq_01 are in both → unchanged
    assert "Unchanged findings" in md


def test_render_category_flip_counts_as_new_and_resolved(tmp_path: Path):
    """Flipping fst_incorrect → fst_mislabeled at the same test should show
    both a resolved entry (old category) and a new entry (new category)."""
    current = _make_aggregate(
        "cur",
        {"equity-scorer": [{"test": "eq_01", "category": "fst_mislabeled", "rationale": ""}]},
    )
    baseline = _make_aggregate(
        "base",
        {"equity-scorer": [{"test": "eq_01", "category": "fst_incorrect", "rationale": ""}]},
    )
    rdir = tmp_path / "r"
    rdir.mkdir()
    (rdir / "aggregate_report.json").write_text(json.dumps(current))
    bfile = tmp_path / "b.json"
    bfile.write_text(json.dumps(baseline))

    md = render_markdown_report(rdir, baseline=bfile)
    # Both sides should be represented
    assert "fst_incorrect" in md  # resolved side
    assert "fst_mislabeled" in md  # new side
    assert "New findings" in md
    assert "Resolved findings" in md


def test_render_clean_run_no_findings(tmp_path: Path):
    agg = _make_aggregate("clean", {"bio-orchestrator": []})
    rdir = tmp_path / "r"
    rdir.mkdir()
    (rdir / "aggregate_report.json").write_text(json.dumps(agg))

    md = render_markdown_report(rdir)
    assert "No findings at this commit" in md
    assert "PASS" in md


def test_render_artifact_url_in_footer(results_dir: Path):
    md = render_markdown_report(results_dir, artifact_url="https://example.com/artifact/42")
    assert "https://example.com/artifact/42" in md


def test_render_handles_missing_baseline_file_gracefully(results_dir: Path, tmp_path: Path):
    """A stale baseline path should not crash the render — treat as no baseline."""
    md = render_markdown_report(results_dir, baseline=tmp_path / "does_not_exist.json")
    # Falls back to no-baseline rendering
    assert "New findings" not in md
    assert "bio-orchestrator" in md


def test_render_accepts_aggregate_file_directly(results_dir: Path):
    md = render_markdown_report(results_dir / "aggregate_report.json")
    assert STICKY_MARKER in md
    assert "bio-orchestrator" in md


def test_render_truncates_long_rationales(tmp_path: Path):
    long = "x" * 500
    agg = _make_aggregate(
        "c",
        {"bio-orchestrator": [{"test": "t", "category": "routed_wrong", "rationale": long}]},
    )
    rdir = tmp_path / "r"
    rdir.mkdir()
    (rdir / "aggregate_report.json").write_text(json.dumps(agg))

    md = render_markdown_report(rdir)
    # Truncated to ~200 chars with ellipsis — the full 500-x string should not appear
    assert "x" * 500 not in md
    assert "..." in md


# ---------------------------------------------------------------------------
# Follow-up fixes: rationale sanitization, ordering, caps, multi-commit
# ---------------------------------------------------------------------------


def test_rationale_newlines_are_collapsed(tmp_path: Path):
    """Multi-line rationale must not break the enclosing markdown bullet."""
    agg = _make_aggregate(
        "c",
        {
            "bio-orchestrator": [
                {
                    "test": "t1",
                    "category": "routed_wrong",
                    "rationale": "first line\nsecond line\n::warning::hello",
                }
            ]
        },
    )
    rdir = tmp_path / "r"
    rdir.mkdir()
    (rdir / "aggregate_report.json").write_text(json.dumps(agg))
    md = render_markdown_report(rdir)

    # The finding appears in both the summary bullet list and the per-test
    # breakdown — at least one line must contain the collapsed rationale.
    finding_lines = [line for line in md.splitlines() if "routed_wrong" in line]
    assert len(finding_lines) >= 1
    # Content is present but newlines and workflow commands are flattened into
    # the same bullet line (no standalone `::warning::` line).
    assert any("first line second line" in fl for fl in finding_lines)
    assert not any(line.strip().startswith("::warning::") for line in md.splitlines())


def test_rationale_html_is_escaped(tmp_path: Path):
    """A rationale containing HTML must not mangle the <details> structure."""
    agg = _make_aggregate(
        "c",
        {
            "bio-orchestrator": [
                {
                    "test": "t1",
                    "category": "routed_wrong",
                    "rationale": "see </details><script>alert(1)</script>",
                }
            ]
        },
    )
    rdir = tmp_path / "r"
    rdir.mkdir()
    (rdir / "aggregate_report.json").write_text(json.dumps(agg))
    md = render_markdown_report(rdir)

    # Raw closing tag must be escaped so it cannot close the enclosing <details>
    assert "</details><script>" not in md
    assert "&lt;/details&gt;" in md
    assert "&lt;script&gt;" in md


def test_harness_ordering_is_deterministic(tmp_path: Path):
    """Summary table rows must be sorted by harness name, independent of JSON key order."""
    # Insert harnesses in reverse alphabetical order
    agg = _make_aggregate(
        "c",
        {
            "zeta-harness": [{"test": "z1", "category": "bad", "rationale": ""}],
            "alpha-harness": [{"test": "a1", "category": "bad", "rationale": ""}],
            "mid-harness": [{"test": "m1", "category": "bad", "rationale": ""}],
        },
    )
    rdir = tmp_path / "r"
    rdir.mkdir()
    (rdir / "aggregate_report.json").write_text(json.dumps(agg))
    md = render_markdown_report(rdir)

    # In the summary table, alpha-harness must appear before mid- and zeta-
    ai = md.index("alpha-harness")
    mi = md.index("mid-harness")
    zi = md.index("zeta-harness")
    assert ai < mi < zi


def test_unchanged_findings_are_capped(tmp_path: Path):
    """Unchanged findings are capped to avoid blowing GitHub's comment size limit."""
    # 50 identical findings in both current and baseline → 50 unchanged
    many = [{"test": f"t{i:03d}", "category": "routed_wrong", "rationale": ""} for i in range(50)]
    agg_cur = _make_aggregate("cur", {"bio-orchestrator": many})
    agg_base = _make_aggregate("base", {"bio-orchestrator": many})
    rdir = tmp_path / "r"
    rdir.mkdir()
    (rdir / "aggregate_report.json").write_text(json.dumps(agg_cur))
    bfile = tmp_path / "b.json"
    bfile.write_text(json.dumps(agg_base))

    md = render_markdown_report(rdir, baseline=bfile)
    # Summary count reflects all 50
    assert "Unchanged findings (50)" in md
    # Body is capped with a "+N more" indicator
    assert "more — see the full verdicts artifact" in md
    # And not every finding appears by test name
    # (at least one of the later ones should be absent)
    assert "t049" not in md


def test_multi_commit_run_shows_caveat_note(tmp_path: Path):
    """Non-smoke aggregates get a warning note about identity collapsing."""
    agg = _make_aggregate(
        "cur",
        {"bio-orchestrator": [{"test": "t", "category": "routed_wrong", "rationale": ""}]},
        mode="regression-20",
    )
    rdir = tmp_path / "r"
    rdir.mkdir()
    (rdir / "aggregate_report.json").write_text(json.dumps(agg))
    md = render_markdown_report(rdir)

    assert "multi-commit" in md.lower()
    assert "renderer is designed for single-commit" in md


def test_smoke_mode_has_no_caveat(tmp_path: Path):
    agg = _make_aggregate(
        "cur",
        {"bio-orchestrator": [{"test": "t", "category": "routed_wrong", "rationale": ""}]},
        mode="smoke",
    )
    rdir = tmp_path / "r"
    rdir.mkdir()
    (rdir / "aggregate_report.json").write_text(json.dumps(agg))
    md = render_markdown_report(rdir)

    assert "multi-commit" not in md.lower()


def test_corrupt_baseline_json_falls_back_gracefully(results_dir: Path, tmp_path: Path):
    """A baseline that exists but is invalid JSON (e.g. an HTML error page from
    a broken download) must not crash the render."""
    bad_baseline = tmp_path / "bad.json"
    bad_baseline.write_text("<!doctype html><html><body>404 Not Found</body></html>")
    md = render_markdown_report(results_dir, baseline=bad_baseline)
    # Falls back to absolute-findings mode
    assert "New findings" not in md
    assert "bio-orchestrator" in md


def test_non_dict_baseline_falls_back_gracefully(results_dir: Path, tmp_path: Path):
    """A JSON-valid-but-wrong-shape baseline (e.g. a list) must not crash."""
    bad_baseline = tmp_path / "list.json"
    bad_baseline.write_text("[1, 2, 3]")
    md = render_markdown_report(results_dir, baseline=bad_baseline)
    assert "New findings" not in md


def test_harness_errors_surface_in_status(tmp_path: Path):
    """When total_harness_errors > 0, the status line calls it out explicitly."""
    agg = _make_aggregate(
        "c", {"bio-orchestrator": [{"test": "t", "category": "bad", "rationale": ""}]}
    )
    agg["overall"]["total_harness_errors"] = 3
    rdir = tmp_path / "r"
    rdir.mkdir()
    (rdir / "aggregate_report.json").write_text(json.dumps(agg))
    md = render_markdown_report(rdir)

    assert "FINDINGS + HARNESS ERRORS" in md
    assert "Harness errors:" in md
