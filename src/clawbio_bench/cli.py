#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
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
from types import ModuleType

from clawbio_bench import (
    AUDIT_TARGET_URL,
    AUTHOR,
    AUTHOR_EMAIL,
    HOMEPAGE_URL,
    PROJECT_METADATA,
    __version__,
)
from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Harness Registry
# ---------------------------------------------------------------------------

HARNESS_REGISTRY = {
    "orchestrator": {
        "module": "clawbio_bench.harnesses.orchestrator_harness",
        "run_fn": "run_single_orchestrator",
        "benchmark_name": "bio-orchestrator",
        "default_inputs_dir": "orchestrator",
        "description": "Bio-orchestrator routing decisions",
    },
    "equity": {
        "module": "clawbio_bench.harnesses.equity_harness",
        "run_fn": "run_single_equity",
        "benchmark_name": "equity-scorer",
        "default_inputs_dir": "equity",
        "description": "Equity scorer FST/HEIM metrics",
    },
    "nutrigx": {
        "module": "clawbio_bench.harnesses.nutrigx_harness",
        "run_fn": "run_single_nutrigx",
        "benchmark_name": "nutrigx-advisor",
        "default_inputs_dir": "nutrigx",
        "description": "NutriGx nutrigenomics scoring + reproducibility",
    },
    "pharmgx": {
        "module": "clawbio_bench.harnesses.pharmgx_harness",
        "run_fn": "run_single_pharmgx",
        "benchmark_name": "pharmgx-reporter",
        "default_inputs_dir": "pharmgx",
        "description": "PharmGx pharmacogenomics phenotype + drug classification",
    },
    "metagenomics": {
        "module": "clawbio_bench.harnesses.metagenomics_harness",
        "run_fn": "run_single_metagenomics",
        "benchmark_name": "claw-metagenomics",
        "default_inputs_dir": "metagenomics",
        "description": "Metagenomics demo-mode + security analysis",
    },
    "finemapping": {
        "module": "clawbio_bench.harnesses.finemapping_harness",
        "run_fn": "run_single_finemapping",
        "benchmark_name": "clawbio-finemapping",
        "default_inputs_dir": "finemapping",
        "description": "Fine-mapping (ABF / SuSiE / credible sets) correctness + silent-failure audit",
    },
    "clinical_variant_reporter": {
        "module": "clawbio_bench.harnesses.clinical_variant_reporter_harness",
        "run_fn": "run_single_clinical_variant_reporter",
        "benchmark_name": "clinical-variant-reporter",
        "default_inputs_dir": "clinical_variant_reporter",
        "description": (
            "Clinical Variant Reporter Phase 1 — structural/traceability "
            "audit of ACMG reports (no full 28-criteria adjudication)"
        ),
    },
}

# Package-level test cases directory
PACKAGE_ROOT = Path(__file__).resolve().parent
TEST_CASES_ROOT = PACKAGE_ROOT / "test_cases"


def load_harness(name: str) -> ModuleType:
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

    run_fn = getattr(mod, info["run_fn"])
    verdicts = harness_core.run_benchmark_matrix(
        repo_path,
        commits,
        test_cases,
        harness_output,
        run_fn,
        mod.BENCHMARK_NAME,
        allow_dirty=allow_dirty,
        quiet=quiet,
        rubric_categories=mod.RUBRIC_CATEGORIES,
        pass_categories=mod.PASS_CATEGORIES,
        fail_categories=mod.FAIL_CATEGORIES,
    )

    heatmap = harness_core.build_heatmap_data(verdicts, mod.CATEGORY_LEGEND)
    with open(harness_output / "heatmap_data.json", "w", encoding="utf-8") as f:
        json.dump(heatmap, f, indent=2, default=str)

    summary = harness_core.build_summary(verdicts, mod.PASS_CATEGORIES)
    with open(harness_output / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    with open(harness_output / "all_verdicts.json", "w", encoding="utf-8") as f:
        json.dump(verdicts, f, indent=2, default=str)

    # Chain of custody: write verdict hash sidecar per-harness
    harness_core.write_verdict_hashes(harness_output)

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

    # Full list (not truncated) — downstream consumers (markdown renderer,
    # baseline diffing) need every finding, and 93 tests * 5 harnesses keeps
    # the file tractable. Callers that render to console should slice locally.
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
                "commit": v.get("commit", {}).get("sha", "")[:8],
            }
            for v in critical
        ],
    }


def _count_test_cases(inputs_path: Path) -> int:
    """Count test cases using core's resolution logic."""
    try:
        return len(harness_core.resolve_test_cases(inputs_path))
    except harness_core.BenchmarkConfigError:
        return 0


def _harness_rows() -> list[tuple[str, int, str]]:
    """Build (name, test_case_count, description) triples for the --list
    output. Used by both the rich renderer and the plain fallback."""
    rows: list[tuple[str, int, str]] = []
    for name, info in HARNESS_REGISTRY.items():
        inputs_path = TEST_CASES_ROOT / info["default_inputs_dir"]
        count = _count_test_cases(inputs_path)
        rows.append((name, count, info["description"]))
    return rows


def main() -> None:
    epilog = (
        "examples:\n"
        "  clawbio-bench --smoke --repo /path/to/ClawBio\n"
        "  clawbio-bench --smoke --harness equity --repo /path/to/ClawBio\n"
        "  clawbio-bench --regression-window 10 --repo /path/to/ClawBio\n"
        "  clawbio-bench --all-commits --repo /path/to/ClawBio\n"
        "  clawbio-bench --heatmap results/suite/20260404_120000/\n"
        "  clawbio-bench --render-markdown results/suite/20260404_120000/  "
        "# smoke runs only\n"
        "  clawbio-bench --render-markdown results/ --baseline main-baseline.json\n"
        "  clawbio-bench --list\n"
        "  clawbio-bench --about              # project metadata + links\n"
        f"\nproject:\n"
        f"  homepage:     {HOMEPAGE_URL}\n"
        f"  audit target: {AUDIT_TARGET_URL}\n"
        f"  author:       {AUTHOR} <{AUTHOR_EMAIL}>\n"
    )
    parser = argparse.ArgumentParser(
        description="ClawBio Benchmark Suite — machine-readable harnesses "
        "for auditing bioinformatics tools",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # --version shows the core library version plus the author / home /
    # audit-target URLs so an auditor running ``clawbio-bench --version`` gets
    # everything they need to cite the tool without digging into pyproject.
    parser.add_argument(
        "--version",
        action="version",
        version=(
            f"%(prog)s {__version__} (core {harness_core.CORE_VERSION})\n"
            f"  author:       {AUTHOR} <{AUTHOR_EMAIL}>\n"
            f"  homepage:     {HOMEPAGE_URL}\n"
            f"  audit target: {AUDIT_TARGET_URL}"
        ),
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
        "--about",
        action="store_true",
        dest="show_about",
        help=(
            "Print full project metadata (author, email, license, homepage, "
            "issues, and the audit target repo) and exit"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "When combined with --list, emit a machine-readable JSON "
            "document instead of the human-friendly table. Fields: "
            "{version, harnesses: [{name, benchmark_name, description, "
            "test_case_count, pass_categories, fail_categories, "
            "rubric_categories}]}"
        ),
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
    parser.add_argument(
        "--render-markdown",
        type=Path,
        default=None,
        metavar="RESULTS_DIR",
        help=(
            "Render a PR-ready markdown report from a previous run's results directory. "
            "Intended for --smoke (single-commit) aggregates; multi-commit runs "
            "will render with a caveat note"
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        metavar="BASELINE_JSON",
        help=(
            "Optional baseline aggregate_report.json (or results dir) to diff against "
            "when rendering markdown; enables new/resolved finding detection"
        ),
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=None,
        metavar="PATH",
        help="Output path for rendered markdown (default: stdout)",
    )
    parser.add_argument(
        "--artifact-url",
        type=str,
        default=None,
        help="Optional URL to the full verdicts artifact, embedded in the markdown footer",
    )
    parser.add_argument(
        "--verify",
        type=Path,
        default=None,
        metavar="RESULTS_DIR",
        help="Verify chain of custody on an existing results directory and exit",
    )
    parser.add_argument(
        "--no-rich",
        action="store_true",
        help=(
            "Disable rich terminal output even on a TTY. Useful for CI logs "
            "and anywhere byte-stable plain text is required."
        ),
    )
    args = parser.parse_args()

    # Apply rich kill switch before any UI helper is called.
    if getattr(args, "no_rich", False):
        from clawbio_bench import ui

        ui.disable_rich()

    # --about: full metadata block. Runs first so ``--about --list`` still
    # prints only the about panel (about is the more specific intent).
    if getattr(args, "show_about", False):
        from clawbio_bench.ui import render_about

        render_about(PROJECT_METADATA, core_version=harness_core.CORE_VERSION)
        return

    # List-only mode
    if args.list_harnesses:
        # --list --json → machine-readable output for scripting consumers.
        # The schema exposes everything a dashboard or drift-checker would
        # need without having to import the package.
        if getattr(args, "json_output", False):
            from typing import Any as _Any

            harness_docs: list[dict[str, _Any]] = []
            total_test_cases = 0
            for name, info in HARNESS_REGISTRY.items():
                inputs_path = TEST_CASES_ROOT / info["default_inputs_dir"]
                try:
                    mod = load_harness(name)
                except Exception as exc:
                    harness_docs.append(
                        {
                            "name": name,
                            "benchmark_name": info["benchmark_name"],
                            "description": info["description"],
                            "test_case_count": 0,
                            "error": str(exc),
                        }
                    )
                    continue
                tc_count = _count_test_cases(inputs_path)
                total_test_cases += tc_count
                harness_docs.append(
                    {
                        "name": name,
                        "benchmark_name": info["benchmark_name"],
                        "description": info["description"],
                        "test_case_count": tc_count,
                        "benchmark_version": getattr(mod, "BENCHMARK_VERSION", None),
                        "rubric_categories": list(getattr(mod, "RUBRIC_CATEGORIES", [])),
                        "pass_categories": list(getattr(mod, "PASS_CATEGORIES", [])),
                        "fail_categories": list(getattr(mod, "FAIL_CATEGORIES", [])),
                    }
                )
            doc = {
                "version": __version__,
                "core_version": harness_core.CORE_VERSION,
                "harnesses": harness_docs,
                "total_test_cases": total_test_cases,
                # Additive metadata block. Downstream consumers that only
                # read ``version`` / ``harnesses`` stay backward compatible;
                # dashboards and drift-checkers can surface author, license,
                # and the audit target URL without importing the package.
                "metadata": dict(PROJECT_METADATA),
            }
            json.dump(doc, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            return

        from clawbio_bench.ui import render_harness_list

        print(f"clawbio-bench {__version__} — registered harnesses:\n")
        rows = _harness_rows()
        render_harness_list(rows, title=f"clawbio-bench {__version__}")
        total = sum(count for _, count, _ in rows)
        print(f"\n  total            {total:>3} tests")
        # Metadata footer: one-line pointers to the project repo and the
        # audit target so reviewers can pivot from the listing to either
        # codebase without digging through pyproject.toml.
        print(
            f"\n  homepage:     {HOMEPAGE_URL}"
            f"\n  audit target: {AUDIT_TARGET_URL}"
            f"\n  author:       {AUTHOR} <{AUTHOR_EMAIL}>"
        )
        return

    # Heatmap-only mode
    if args.heatmap:
        from clawbio_bench.viz import render_heatmap

        render_heatmap(args.heatmap, output_path=args.heatmap_output)
        return

    # Chain of custody verification mode — three-layer deep check covering
    # per-verdict self-hashes, verdict_hashes.json sidecar index, and
    # stdout.log / stderr.log hashes against each verdict's execution record.
    if args.verify:
        from clawbio_bench.ui import render_verify_result

        results_dir = args.verify.resolve()
        ok_count, fail_count, errors = harness_core.verify_results_directory(results_dir)
        render_verify_result(ok_count=ok_count, fail_count=fail_count, errors=errors)
        if fail_count:
            sys.exit(1)
        return

    # Markdown render mode: no benchmark run, just format an existing result
    if args.render_markdown:
        from clawbio_bench.markdown_report import render_markdown_report

        md = render_markdown_report(
            args.render_markdown,
            baseline=args.baseline,
            artifact_url=args.artifact_url,
        )
        if args.markdown_output:
            args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
            args.markdown_output.write_text(md, encoding="utf-8")
            print(f"Markdown report written: {args.markdown_output}")
        else:
            sys.stdout.write(md)
        return

    repo_path = args.repo.resolve()
    allow_dirty = getattr(args, "allow_dirty", False)

    from clawbio_bench.ui import (
        render_dry_run_plan,
        render_error,
        render_harness_header,
        render_startup_banner,
    )

    try:
        harness_core.validate_repo(repo_path)
        commits = harness_core.resolve_commits(args, repo_path)
    except (harness_core.BenchmarkConfigError, harness_core.DirtyRepoError) as e:
        render_error(f"ERROR: {e}")
        sys.exit(1)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_base = (args.output or Path.cwd() / "results" / "suite" / timestamp).resolve()

    if args.smoke:
        mode = "smoke"
    elif args.all_commits:
        mode = "full"
    elif args.regression_window:
        mode = f"regression-{args.regression_window}"
    else:
        mode = "custom"

    harness_names = [args.harness] if args.harness else list(HARNESS_REGISTRY.keys())

    render_startup_banner(
        suite_version=__version__,
        repo_path=repo_path,
        commit_count=len(commits),
        mode=mode,
        harness_names=harness_names,
        output_base=output_base,
    )

    # Dry-run mode: show plan and exit
    if args.dry_run:
        harness_plans: list[tuple[str, list[str]]] = []
        total_case_count = 0
        for name in harness_names:
            info = HARNESS_REGISTRY[name]
            inputs_path = args.inputs or (TEST_CASES_ROOT / info["default_inputs_dir"])
            try:
                test_cases = harness_core.resolve_test_cases(inputs_path)
            except harness_core.BenchmarkConfigError:
                test_cases = []
            tc_names = [tc.stem if tc.is_file() else tc.name for tc in test_cases]
            harness_plans.append((info["benchmark_name"], tc_names))
            total_case_count += len(tc_names)
        total_runs = total_case_count * len(commits)
        render_dry_run_plan(
            harness_plans,
            total_runs=total_runs,
            commit_count=len(commits),
        )
        return

    output_base.mkdir(parents=True, exist_ok=True)

    quiet = getattr(args, "quiet", False)
    results = {}
    # Track infra-level failures separately from in-matrix harness_error
    # verdicts. A harness-level exception (run_single_harness itself raising)
    # means the benchmark runner is broken — distinct from a test case that
    # emitted a harness_error verdict via the matrix. CI uses exit 2 to fail
    # loudly on the former while keeping the latter advisory.
    harness_infra_crashes: list[str] = []
    suite_start = time.monotonic()

    for name in harness_names:
        info = HARNESS_REGISTRY[name]
        if not quiet:
            render_harness_header(info["benchmark_name"], info["description"])

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
            render_error(f"HARNESS FAILED: {e}")
            harness_infra_crashes.append(info["benchmark_name"])
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

    with open(output_base / "aggregate_report.json", "w", encoding="utf-8") as f:
        json.dump(aggregate, f, indent=2)

    from clawbio_bench.ui import render_suite_summary

    render_suite_summary(
        results,
        total_pass=total_pass,
        total_evaluated=total_evaluated,
        total_pass_rate=total_pass_rate,
        total_harness_errors=total_harness_errors,
        wall_clock_seconds=suite_elapsed,
        blocking=blocking,
        infra_crashes=harness_infra_crashes,
    )

    print(f"\nResults: {output_base}")

    # Exit codes:
    #   0 = all harnesses pass
    #   1 = findings exist (expected — treated as advisory by CI)
    #   2 = at least one harness raised an infrastructure exception
    #       (benchmark itself is broken — CI fails loudly)
    if harness_infra_crashes:
        sys.exit(2)
    if blocking:
        sys.exit(1)


if __name__ == "__main__":
    main()
