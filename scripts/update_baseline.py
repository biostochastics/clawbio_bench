#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Baseline manager for the daily audit workflow.

Compares the current run's aggregate pass rate against a stored baseline
JSON file. If the current pass rate is **strictly better**, the baseline
is replaced and the script exits 0. If the pass rate regressed or stayed
the same, the old baseline is kept and the script exits 0 (the delta
report will surface the regression). The exit code is always 0 unless
the input files are missing or malformed (exit 2).

Usage:
    python scripts/update_baseline.py \
        --current results/today/aggregate_report.json \
        --baseline baselines/latest_baseline.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def _load_aggregate(path: Path) -> dict:
    """Load an aggregate_report.json, resolving dir-or-file ambiguity."""
    path = path.resolve()
    candidate = path / "aggregate_report.json" if path.is_dir() else path
    if not candidate.exists():
        raise FileNotFoundError(f"aggregate_report.json not found at {candidate}")
    with open(candidate, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {candidate}")
    return data


def _pass_rate(aggregate: dict) -> float:
    overall = aggregate.get("overall") or {}
    return float(overall.get("total_pass_rate", 0.0))


def _commit(aggregate: dict) -> str:
    return aggregate.get("clawbio_commit", "unknown")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update the audit baseline if the current run improved.",
    )
    parser.add_argument(
        "--current",
        type=Path,
        required=True,
        help="Path to the current run's aggregate_report.json (or results dir)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="Path to the stored baseline JSON file (will be overwritten on improvement)",
    )
    args = parser.parse_args()

    try:
        current = _load_aggregate(args.current)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot load current results: {exc}", file=sys.stderr)
        return 2

    current_rate = _pass_rate(current)
    current_commit = _commit(current)

    # If no baseline exists yet, the current run becomes the baseline.
    baseline_path = args.baseline.resolve()
    if not baseline_path.exists():
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            args.current.resolve()
            if args.current.resolve().is_file()
            else args.current.resolve() / "aggregate_report.json",
            baseline_path,
        )
        print(
            f"No existing baseline. Initializing at {current_rate:.1f}% (commit {current_commit})."
        )
        return 0

    try:
        baseline = _load_aggregate(baseline_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"WARNING: corrupt baseline ({exc}), replacing with current run.", file=sys.stderr)
        shutil.copy2(
            args.current.resolve()
            if args.current.resolve().is_file()
            else args.current.resolve() / "aggregate_report.json",
            baseline_path,
        )
        return 0

    baseline_rate = _pass_rate(baseline)
    baseline_commit = _commit(baseline)

    if current_rate > baseline_rate:
        # Improvement: promote current to baseline.
        source = (
            args.current.resolve()
            if args.current.resolve().is_file()
            else args.current.resolve() / "aggregate_report.json"
        )
        shutil.copy2(source, baseline_path)
        print(
            f"IMPROVED: {baseline_rate:.1f}% ({baseline_commit}) -> "
            f"{current_rate:.1f}% ({current_commit}). Baseline updated."
        )
    elif current_rate < baseline_rate:
        print(
            f"REGRESSION: {baseline_rate:.1f}% ({baseline_commit}) -> "
            f"{current_rate:.1f}% ({current_commit}). Baseline NOT updated."
        )
    else:
        print(
            f"UNCHANGED: {current_rate:.1f}% (baseline {baseline_commit}, "
            f"current {current_commit}). Baseline NOT updated."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
