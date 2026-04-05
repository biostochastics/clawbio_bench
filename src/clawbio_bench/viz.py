#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Heatmap visualization for ClawBio benchmark results.

Renders commit × test_case grids from heatmap_data.json produced by any harness.
Supports both single-harness and aggregate (multi-harness) views.

Requires: matplotlib (install with `pip install clawbio-bench[viz]`)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Default color palette for categories across all harnesses.
# Harness-specific category_legend colors override these when available.
DEFAULT_COLORS = {
    # Pass shades (greens)
    "correct_determinate": "#22c55e",
    "correct_indeterminate": "#86efac",
    "routed_correct": "#22c55e",
    "stub_warned": "#86efac",
    "fst_correct": "#22c55e",
    "heim_bounded": "#22c55e",
    "csv_honest": "#22c55e",
    "edge_handled": "#22c55e",
    "injection_blocked": "#22c55e",
    "exit_handled": "#22c55e",
    "demo_functional": "#86efac",
    "score_correct": "#22c55e",
    "repro_functional": "#22c55e",
    "snp_valid": "#22c55e",
    "threshold_consistent": "#22c55e",
    # Fail shades (reds/ambers)
    "incorrect_determinate": "#ef4444",
    "incorrect_indeterminate": "#f97316",
    "omission": "#0f2440",
    "disclosure_failure": "#cf7a30",
    "routed_wrong": "#ef4444",
    "stub_silent": "#f97316",
    "unroutable_crash": "#ef4444",
    "fst_incorrect": "#ef4444",
    "fst_mislabeled": "#f97316",
    "heim_unbounded": "#f97316",
    "csv_inflated": "#ef4444",
    "edge_crash": "#ef4444",
    "injection_succeeded": "#ef4444",
    "exit_suppressed": "#f97316",
    "demo_broken": "#ef4444",
    "score_incorrect": "#ef4444",
    "repro_broken": "#ef4444",
    "snp_invalid": "#f97316",
    "threshold_mismatch": "#f97316",
    # Neutral
    "unroutable_handled": "#60a5fa",
    "harness_error": "#9ca3af",
}


def _check_matplotlib() -> tuple[Any, ...]:
    """Import matplotlib or exit with helpful message."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.colors as mcolors
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt
        import numpy as np

        return plt, mcolors, mpatches, np
    except ImportError:
        print(
            "ERROR: matplotlib is required for visualization.\n"
            "Install with: pip install clawbio-bench[viz]",
            file=sys.stderr,
        )
        sys.exit(1)


def render_heatmap(
    results_dir: Path,
    output_path: Path | None = None,
    title: str | None = None,
    figsize: tuple[int, int] = (16, 10),
) -> Path:
    """Render a heatmap from benchmark results.

    Looks for heatmap_data.json in results_dir (or subdirectories for
    aggregate view). Returns the path to the saved PNG.
    """
    plt, mcolors, mpatches, np = _check_matplotlib()

    results_dir = results_dir.resolve()

    # Find heatmap data files — could be one (single harness) or many (suite)
    heatmap_files = sorted(results_dir.rglob("heatmap_data.json"))
    if not heatmap_files:
        print(f"ERROR: No heatmap_data.json found in {results_dir}", file=sys.stderr)
        sys.exit(1)

    # Merge data from all heatmap files
    all_commits = []
    all_test_cases = []
    all_matrix = {}
    all_legend = {}
    seen_commits = set()

    for hf in heatmap_files:
        with open(hf, encoding="utf-8") as f:
            data = json.load(f)

        # Prefix test case names with harness name for multi-harness view
        harness_prefix = ""
        if len(heatmap_files) > 1:
            harness_prefix = hf.parent.name + "/"

        for commit in data.get("commits", []):
            sha = commit["sha"]
            if sha not in seen_commits:
                all_commits.append(commit)
                seen_commits.add(sha)

        for tc in data.get("test_cases", []):
            prefixed = f"{harness_prefix}{tc}"
            all_test_cases.append(prefixed)

        for key, val in data.get("matrix", {}).items():
            sha, tc = key.split(":", 1)
            new_key = f"{sha}:{harness_prefix}{tc}"
            all_matrix[new_key] = val

        for cat, info in data.get("category_legend", {}).items():
            if cat not in all_legend:
                all_legend[cat] = info

    n_commits = len(all_commits)
    n_tests = len(all_test_cases)

    if n_commits == 0 or n_tests == 0:
        print("ERROR: No data to render", file=sys.stderr)
        sys.exit(1)

    # Build category list from observed categories
    observed_cats = set()
    for val in all_matrix.values():
        observed_cats.add(val.get("category", "unknown"))
    cat_list = sorted(observed_cats)
    cat_to_idx = {c: i for i, c in enumerate(cat_list)}

    # Build color list
    color_list = []
    for cat in cat_list:
        if cat in all_legend:
            color_list.append(all_legend[cat].get("color", DEFAULT_COLORS.get(cat, "#9ca3af")))
        else:
            color_list.append(DEFAULT_COLORS.get(cat, "#9ca3af"))

    # Build matrix
    mat = np.full((n_commits, n_tests), -1, dtype=int)
    for i, commit in enumerate(all_commits):
        sha = commit["sha"]
        for j, tc in enumerate(all_test_cases):
            key = f"{sha}:{tc}"
            if key in all_matrix:
                cat = all_matrix[key].get("category", "unknown")
                mat[i, j] = cat_to_idx.get(cat, -1)

    # Custom colormap
    cmap = mcolors.ListedColormap(color_list)
    bounds = [i - 0.5 for i in range(len(cat_list) + 1)]
    norm = mcolors.BoundaryNorm(bounds, cmap.N)

    # Scale figure height by number of commits
    auto_height = max(6, min(30, n_commits * 0.15 + 4))
    auto_width = max(10, min(24, n_tests * 0.8 + 4))
    fig_w, fig_h = figsize if figsize != (16, 10) else (auto_width, auto_height)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.imshow(mat, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest")

    # Y-axis: commits
    step = max(1, n_commits // 30)
    y_ticks = list(range(0, n_commits, step))
    if n_commits - 1 not in y_ticks:
        y_ticks.append(n_commits - 1)
    y_labels = []
    for i in y_ticks:
        c = all_commits[i]
        date = c.get("date", "")[:10]
        y_labels.append(f"{c.get('short', c['sha'][:8])} ({date})")
    ax.set_yticks(y_ticks)
    ax.set_yticklabels(y_labels, fontsize=7, fontfamily="monospace")

    # X-axis: test cases
    display_names = [tc.split("/")[-1].replace("_", "\n") for tc in all_test_cases]
    ax.set_xticks(range(n_tests))
    ax.set_xticklabels(display_names, fontsize=6.5, rotation=45, ha="left", fontfamily="monospace")
    ax.xaxis.set_ticks_position("top")
    ax.xaxis.set_label_position("top")

    # Legend
    legend_patches = [
        mpatches.Patch(
            color=color_list[i],
            label=all_legend.get(cat, {}).get("label", cat),
        )
        for i, cat in enumerate(cat_list)
    ]
    ax.legend(
        handles=legend_patches,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.08),
        ncol=min(4, len(cat_list)),
        fontsize=7,
        frameon=True,
        fancybox=True,
        edgecolor="#dee2e6",
    )

    # Title
    auto_title = title or f"ClawBio Benchmark Heatmap — {n_commits} commits × {n_tests} tests"
    ax.set_title(auto_title, fontsize=12, fontweight="bold", color="#0f2440", pad=30)

    fig.text(
        0.5,
        0.005,
        "clawbio-bench · github.com/biostochastics/clawbio_bench",
        ha="center",
        fontsize=7,
        color="#64748b",
    )

    plt.tight_layout(rect=[0, 0.04, 1.0, 0.95])

    # Save
    if output_path is None:
        output_path = results_dir / "heatmap.png"
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig.savefig(output_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    print(f"Heatmap saved: {output_path} ({output_path.stat().st_size // 1024} KB)")
    return output_path
