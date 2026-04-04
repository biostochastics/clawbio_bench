#!/usr/bin/env python3
"""
ClawBio Benchmark Suite — CLI Entry Point
===========================================
Usage:
    clawbio-bench --smoke --repo /path/to/ClawBio
    clawbio-bench --smoke --harness orchestrator --repo /path/to/ClawBio
    clawbio-bench --all-commits --repo /path/to/ClawBio
    clawbio-bench --regression-window 10 --repo /path/to/ClawBio
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from clawbio_bench import __version__
from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Harness Registry
# ---------------------------------------------------------------------------

HARNESS_REGISTRY = {
    "orchestrator": {
        "module": "clawbio_bench.harnesses.orchestrator_harness",
        "benchmark_name": "bio-orchestrator",
        "default_inputs_dir": "orchestrator",
        "description": "Bio-orchestrator routing decisions",
    },
    "equity": {
        "module": "clawbio_bench.harnesses.equity_harness",
        "benchmark_name": "equity-scorer",
        "default_inputs_dir": "equity",
        "description": "Equity scorer FST/HEIM metrics",
    },
    "nutrigx": {
        "module": "clawbio_bench.harnesses.nutrigx_harness",
        "benchmark_name": "nutrigx-advisor",
        "default_inputs_dir": "nutrigx",
        "description": "NutriGx nutrigenomics scoring + reproducibility",
    },
    "pharmgx": {
        "module": "clawbio_bench.harnesses.pharmgx_harness",
        "benchmark_name": "pharmgx-reporter",
        "default_inputs_dir": "pharmgx",
        "description": "PharmGx pharmacogenomics phenotype + drug classification",
    },
    "metagenomics": {
        "module": "clawbio_bench.harnesses.metagenomics_harness",
        "benchmark_name": "claw-metagenomics",
        "default_inputs_dir": "metagenomics",
        "description": "Metagenomics demo-mode + security analysis",
    },
}

# Package-level test cases directory
PACKAGE_ROOT = Path(__file__).resolve().parent
TEST_CASES_ROOT = PACKAGE_ROOT / "test_cases"


def load_harness(name: str):
    """Dynamically import a harness module."""
    info = HARNESS_REGISTRY[name]
    return import_module(info["module"])


def run_single_harness(
    name: str,
    repo_path: Path,
    commits: list[str],
    output_base: Path,
    allow_dirty: bool = False,
    inputs_override: Path | None = None,
    quiet: bool = False,
) -> dict:
    """Run one harness and return its summary."""
    mod = load_harness(name)
    info = HARNESS_REGISTRY[name]

    inputs_path = inputs_override or (TEST_CASES_ROOT / info["default_inputs_dir"])
    test_cases = harness_core.resolve_test_cases(inputs_path)

    harness_output = output_base / info["benchmark_name"]
    harness_output.mkdir(parents=True, exist_ok=True)

    harness_core.write_manifest(
        harness_output,
        mod.BENCHMARK_NAME,
        mod.BENCHMARK_VERSION,
        repo_path,
        commits,
        test_cases,
        mod.GROUND_TRUTH_REFS,
        mod.RUBRIC_CATEGORIES,
        mod.PASS_CATEGORIES,
        mod.FAIL_CATEGORIES,
    )

    run_fn = getattr(mod, f"run_single_{name}")
    verdicts = harness_core.run_benchmark_matrix(
        repo_path,
        commits,
        test_cases,
        harness_output,
        run_fn,
        mod.BENCHMARK_NAME,
        allow_dirty=allow_dirty,
        quiet=quiet,
    )

    heatmap = harness_core.build_heatmap_data(verdicts, mod.CATEGORY_LEGEND)
    with open(harness_output / "heatmap_data.json", "w") as f:
        json.dump(heatmap, f, indent=2, default=str)

    summary = harness_core.build_summary(verdicts, mod.PASS_CATEGORIES)
    with open(harness_output / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    with open(harness_output / "all_verdicts.json", "w") as f:
        json.dump(verdicts, f, indent=2, default=str)

    total = len(verdicts)
    harness_errors = sum(
        1 for v in verdicts if v.get("verdict", {}).get("category") == "harness_error"
    )
    evaluated = total - harness_errors
    pass_count = sum(
        1 for v in verdicts if v.get("verdict", {}).get("category") in mod.PASS_CATEGORIES
    )
    pass_rate = round(pass_count / evaluated * 100, 1) if evaluated > 0 else 0.0

    cats = Counter(v.get("verdict", {}).get("category", "unknown") for v in verdicts)

    critical = [v for v in verdicts if v.get("verdict", {}).get("category") in mod.FAIL_CATEGORIES]

    return {
        "version": mod.BENCHMARK_VERSION,
        "pass": pass_count == evaluated and evaluated > 0,
        "total_cases": total,
        "evaluated": evaluated,
        "harness_errors": harness_errors,
        "pass_count": pass_count,
        "fail_count": evaluated - pass_count,
        "pass_rate": pass_rate,
        "pass_categories": mod.PASS_CATEGORIES,
        "categories": dict(cats),
        "critical_failures": [
            {
                "test": v.get("test_case", {}).get("name"),
                "category": v.get("verdict", {}).get("category"),
                "rationale": v.get("verdict", {}).get("rationale"),
            }
            for v in critical[:10]
        ],
    }


def _harness_summary() -> str:
    """Build a summary of registered harnesses for --list output."""
    lines = []
    for name, info in HARNESS_REGISTRY.items():
        inputs_path = TEST_CASES_ROOT / info["default_inputs_dir"]
        if inputs_path.is_dir():
            count = sum(
                1
                for p in inputs_path.iterdir()
                if p.is_file() or (p.is_dir() and (p / "ground_truth.txt").exists())
            )
        else:
            count = 0
        lines.append(f"  {name:<16} {count:>3} tests   {info['description']}")
    return "\n".join(lines)


def main():
    epilog = (
        "examples:\n"
        "  clawbio-bench --smoke --repo /path/to/ClawBio\n"
        "  clawbio-bench --smoke --harness equity --repo /path/to/ClawBio\n"
        "  clawbio-bench --regression-window 10 --repo /path/to/ClawBio\n"
        "  clawbio-bench --all-commits --repo /path/to/ClawBio\n"
        "  clawbio-bench --heatmap results/suite/20260404_120000/\n"
        "  clawbio-bench --list\n"
    )
    parser = argparse.ArgumentParser(
        description="ClawBio Benchmark Suite — safety and correctness harnesses "
        "for computational biology tools",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__} (core {harness_core.CORE_VERSION})",
    )
    harness_core.add_common_args(parser)
    parser.add_argument(
        "--harness",
        type=str,
        default=None,
        choices=list(HARNESS_REGISTRY.keys()),
        help="Run a specific harness only (default: all)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_harnesses",
        help="List available harnesses and test case counts, then exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would run (commits, test cases) without executing",
    )
    parser.add_argument(
        "--heatmap",
        type=Path,
        default=None,
        metavar="RESULTS_DIR",
        help="Render heatmap from a previous run's results directory",
    )
    parser.add_argument(
        "--heatmap-output",
        type=Path,
        default=None,
        metavar="PATH",
        help="Output path for heatmap PNG (default: RESULTS_DIR/heatmap.png)",
    )
    args = parser.parse_args()

    # List-only mode
    if args.list_harnesses:
        print(f"clawbio-bench {__version__} — registered harnesses:\n")
        print(_harness_summary())
        total = sum(
            sum(
                1
                for p in (TEST_CASES_ROOT / info["default_inputs_dir"]).iterdir()
                if p.is_file() or (p.is_dir() and (p / "ground_truth.txt").exists())
            )
            for info in HARNESS_REGISTRY.values()
            if (TEST_CASES_ROOT / info["default_inputs_dir"]).is_dir()
        )
        print(f"\n  {'total':<16} {total:>3} tests")
        return

    # Heatmap-only mode
    if args.heatmap:
        from clawbio_bench.viz import render_heatmap

        render_heatmap(args.heatmap, output_path=args.heatmap_output)
        return

    repo_path = args.repo.resolve()
    allow_dirty = getattr(args, "allow_dirty", False)

    try:
        harness_core.validate_repo(repo_path)
        commits = harness_core.resolve_commits(args, repo_path)
    except (harness_core.BenchmarkConfigError, harness_core.DirtyRepoError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_base = (args.output or Path.cwd() / "results" / "suite" / timestamp).resolve()
    output_base.mkdir(parents=True, exist_ok=True)

    if args.smoke:
        mode = "smoke"
    elif args.all_commits:
        mode = "full"
    elif args.regression_window:
        mode = f"regression-{args.regression_window}"
    else:
        mode = "custom"

    harness_names = [args.harness] if args.harness else list(HARNESS_REGISTRY.keys())

    print(f"\nClawBio Benchmark Suite v{__version__}")
    print(f"  Repo: {repo_path}")
    print(f"  Commits: {len(commits)}")
    print(f"  Mode: {mode}")
    print(f"  Harnesses: {', '.join(harness_names)}")
    print(f"  Output: {output_base}")
    print()

    # Dry-run mode: show plan and exit
    if args.dry_run:
        for name in harness_names:
            info = HARNESS_REGISTRY[name]
            inputs_path = args.inputs or (TEST_CASES_ROOT / info["default_inputs_dir"])
            try:
                test_cases = harness_core.resolve_test_cases(inputs_path)
            except harness_core.BenchmarkConfigError:
                test_cases = []
            print(f"  {info['benchmark_name']}: {len(test_cases)} test cases")
            for tc in test_cases:
                tc_name = tc.stem if tc.is_file() else tc.name
                print(f"    - {tc_name}")
        total_runs = sum(
            len(
                harness_core.resolve_test_cases(
                    args.inputs or (TEST_CASES_ROOT / HARNESS_REGISTRY[n]["default_inputs_dir"])
                )
            )
            for n in harness_names
        ) * len(commits)
        print(f"\n  Total runs: {total_runs} ({len(commits)} commits x test cases)")
        print("  (dry run — nothing executed)")
        return

    quiet = getattr(args, "quiet", False)
    results = {}
    suite_start = time.monotonic()

    for name in harness_names:
        info = HARNESS_REGISTRY[name]
        if not quiet:
            print(f"\n{'#' * 60}")
            print(f"# HARNESS: {info['benchmark_name']} — {info['description']}")
            print(f"{'#' * 60}")

        try:
            results[info["benchmark_name"]] = run_single_harness(
                name,
                repo_path,
                commits,
                output_base,
                allow_dirty=allow_dirty,
                inputs_override=args.inputs,
                quiet=quiet,
            )
        except Exception as e:
            print(f"HARNESS FAILED: {e}", file=sys.stderr)
            results[info["benchmark_name"]] = {
                "version": "error",
                "pass": False,
                "error": str(e),
                "total_cases": 0,
                "evaluated": 0,
                "harness_errors": 0,
                "pass_count": 0,
                "fail_count": 0,
                "pass_rate": 0.0,
            }

    suite_elapsed = time.monotonic() - suite_start

    total_cases = sum(r.get("total_cases", 0) for r in results.values())
    total_evaluated = sum(r.get("evaluated", 0) for r in results.values())
    total_harness_errors = sum(r.get("harness_errors", 0) for r in results.values())
    total_pass = sum(r.get("pass_count", 0) for r in results.values())
    total_pass_rate = round(total_pass / total_evaluated * 100, 1) if total_evaluated > 0 else 0.0

    blocking = [name for name, r in results.items() if not r.get("pass", False)]

    head_result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    head_short = head_result.stdout.strip() if head_result.returncode == 0 else "unknown"

    aggregate = {
        "benchmark_suite_version": harness_core.CORE_VERSION,
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "clawbio_commit": head_short,
        "mode": mode,
        "wall_clock_seconds": round(suite_elapsed, 1),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "harnesses": results,
        "overall": {
            "pass": len(blocking) == 0 and total_evaluated > 0,
            "total_cases": total_cases,
            "total_evaluated": total_evaluated,
            "total_harness_errors": total_harness_errors,
            "total_pass": total_pass,
            "total_pass_rate": total_pass_rate,
            "blocking_skills": blocking,
        },
    }

    with open(output_base / "aggregate_report.json", "w") as f:
        json.dump(aggregate, f, indent=2)

    print(f"\n{'=' * 60}")
    print("BENCHMARK SUITE COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Wall clock: {suite_elapsed:.1f}s")
    print(
        f"  Overall: {total_pass}/{total_evaluated} pass "
        f"({total_pass_rate:.1f}%) "
        f"[{total_harness_errors} harness errors]"
    )

    for name, r in results.items():
        status = "PASS" if r.get("pass") else "FAIL"
        print(f"\n  {name}: {status}")
        print(
            f"    {r.get('pass_count', 0)}/{r.get('evaluated', 0)} pass "
            f"({r.get('pass_rate', 0):.1f}%)"
        )
        for cat, count in sorted(r.get("categories", {}).items()):
            print(f"      {cat}: {count}")

    if blocking:
        print(f"\n  BLOCKING: {', '.join(blocking)}")

    print(f"\nResults: {output_base}")

    if blocking:
        sys.exit(1)


if __name__ == "__main__":
    main()
