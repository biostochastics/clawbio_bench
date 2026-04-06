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
    "scope_honest_indeterminate": "#a7f3d0",
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


def _load_heatmap_files(results_dir: Path) -> list[Path]:
    """Find heatmap_data.json files in the results directory."""
    heatmap_files = sorted(results_dir.rglob("heatmap_data.json"))
    if not heatmap_files:
        print(f"ERROR: No heatmap_data.json found in {results_dir}", file=sys.stderr)
        sys.exit(1)
    return heatmap_files


def _merge_heatmap_data(
    heatmap_files: list[Path],
) -> tuple[list[dict], list[str], dict, dict, dict[str, list[str]]]:
    """Merge data from one or more heatmap_data.json files.

    Returns (commits, test_cases, matrix, legend, harness_boundaries).
    ``harness_boundaries`` maps harness name → list of test case names belonging
    to that harness, preserving insertion order for hierarchical rendering.
    """
    all_commits: list[dict] = []
    all_test_cases: list[str] = []
    all_matrix: dict = {}
    all_legend: dict = {}
    seen_commits: set[str] = set()
    harness_boundaries: dict[str, list[str]] = {}

    multi = len(heatmap_files) > 1

    for hf in heatmap_files:
        with open(hf, encoding="utf-8") as f:
            data = json.load(f)

        harness_name = hf.parent.name if multi else ""
        harness_prefix = f"{harness_name}/" if multi else ""

        for commit in data.get("commits", []):
            sha = commit["sha"]
            if sha not in seen_commits:
                all_commits.append(commit)
                seen_commits.add(sha)

        harness_tcs: list[str] = []
        for tc in data.get("test_cases", []):
            prefixed = f"{harness_prefix}{tc}"
            all_test_cases.append(prefixed)
            harness_tcs.append(prefixed)

        if harness_name:
            harness_boundaries[harness_name] = harness_tcs

        for key, val in data.get("matrix", {}).items():
            sha, tc = key.split(":", 1)
            new_key = f"{sha}:{harness_prefix}{tc}"
            all_matrix[new_key] = val

        for cat, info in data.get("category_legend", {}).items():
            if cat not in all_legend:
                all_legend[cat] = info

    return all_commits, all_test_cases, all_matrix, all_legend, harness_boundaries


def _build_commit_label(commit: dict) -> str:
    """Build a Y-axis label for a commit, including tag/release if present."""
    short = commit.get("short", commit["sha"][:8])
    date = commit.get("date", "")[:10]
    tags = commit.get("tags", [])
    if tags:
        tag_str = ", ".join(tags[:2])
        if len(tags) > 2:
            tag_str += f" +{len(tags) - 2}"
        return f"{short} ({date}) [{tag_str}]"
    return f"{short} ({date})"


def _render_single_heatmap(
    plt: Any,
    mcolors: Any,
    mpatches: Any,
    np: Any,
    all_commits: list[dict],
    all_test_cases: list[str],
    all_matrix: dict,
    all_legend: dict,
    harness_boundaries: dict[str, list[str]],
    output_path: Path,
    title: str | None = None,
    figsize: tuple[int, int] = (16, 10),
) -> Path:
    """Core rendering logic for a single heatmap image."""
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

    # Custom colormap — include a slot for missing (-1) cells
    missing_color = "#f1f5f9"
    full_colors = color_list + [missing_color]
    cmap = mcolors.ListedColormap(full_colors)
    bounds = [i - 0.5 for i in range(len(full_colors) + 1)]
    norm = mcolors.BoundaryNorm(bounds, cmap.N)
    # Map -1 → last color index (missing/unmatched cells)
    missing_idx = len(color_list)
    mat[mat == -1] = missing_idx
    if np.all(mat == missing_idx):
        print(
            "  WARNING: heatmap matrix is entirely missing — no verdict data "
            "matched any commit/test combination",
            file=sys.stderr,
        )

    # Scale figure size
    auto_height = max(6, min(40, n_commits * 0.2 + 4))
    auto_width = max(10, min(32, n_tests * 0.8 + 4))
    fig_w, fig_h = figsize if figsize != (16, 10) else (auto_width, auto_height)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.imshow(mat, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest")

    # Y-axis: commits with tag/release markers
    step = max(1, n_commits // 40)
    # Always show tagged commits regardless of step
    tagged_indices = {i for i, c in enumerate(all_commits) if c.get("tags")}
    y_ticks = sorted(set(range(0, n_commits, step)) | tagged_indices | {n_commits - 1})
    y_labels = []
    y_colors = []
    for i in y_ticks:
        c = all_commits[i]
        y_labels.append(_build_commit_label(c))
        y_colors.append("#7c3aed" if c.get("tags") else "#334155")
    ax.set_yticks(y_ticks)
    ax.set_yticklabels(y_labels, fontsize=7, fontfamily="monospace")
    # strict=False: matplotlib may return fewer tick labels than requested
    # (e.g. when auto-layout trims overlapping labels at small figure sizes).
    for tick_label, color in zip(ax.get_yticklabels(), y_colors, strict=False):
        tick_label.set_color(color)
        if color == "#7c3aed":
            tick_label.set_fontweight("bold")

    # Draw horizontal lines at tagged commits for release demarcation
    for i in tagged_indices:
        ax.axhline(y=i, color="#7c3aed", linewidth=0.5, alpha=0.4, linestyle="--")

    # X-axis: test cases with harness group separators
    display_names = [tc.split("/")[-1].replace("_", "\n") for tc in all_test_cases]
    ax.set_xticks(range(n_tests))
    ax.set_xticklabels(display_names, fontsize=6.5, rotation=45, ha="left", fontfamily="monospace")
    ax.xaxis.set_ticks_position("top")
    ax.xaxis.set_label_position("top")

    # Draw vertical separator lines between harnesses
    if harness_boundaries:
        offset = 0
        harness_midpoints = []
        for harness_name, tcs in harness_boundaries.items():
            if offset > 0:
                ax.axvline(x=offset - 0.5, color="#64748b", linewidth=1.5, alpha=0.6)
            midpoint = offset + len(tcs) / 2.0 - 0.5
            harness_midpoints.append((midpoint, harness_name))
            offset += len(tcs)
        # Add harness labels above the top axis
        for midpoint, harness_name in harness_midpoints:
            ax.text(
                midpoint,
                -1.8,
                harness_name,
                ha="center",
                va="bottom",
                fontsize=8,
                fontweight="bold",
                color="#0f2440",
                transform=ax.get_xaxis_transform(),
                clip_on=False,
            )

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
        ncol=min(5, len(cat_list)),
        fontsize=7,
        frameon=True,
        fancybox=True,
        edgecolor="#dee2e6",
    )

    # Title
    auto_title = title or f"ClawBio Benchmark Heatmap — {n_commits} commits × {n_tests} tests"
    n_tagged = len(tagged_indices)
    if n_tagged > 0:
        auto_title += f" ({n_tagged} releases)"
    ax.set_title(auto_title, fontsize=12, fontweight="bold", color="#0f2440", pad=40)

    fig.text(
        0.5,
        0.005,
        "clawbio-bench · github.com/biostochastics/clawbio_bench",
        ha="center",
        fontsize=7,
        color="#64748b",
    )

    plt.tight_layout(rect=[0, 0.04, 1.0, 0.93])

    # Save
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    print(f"Heatmap saved: {output_path} ({output_path.stat().st_size // 1024} KB)")
    return output_path


def render_heatmap(
    results_dir: Path,
    output_path: Path | None = None,
    title: str | None = None,
    figsize: tuple[int, int] = (16, 10),
) -> Path:
    """Render heatmap(s) from benchmark results.

    Looks for heatmap_data.json in results_dir (or subdirectories for
    aggregate view). When multiple harnesses are present, renders:
      1. An aggregate heatmap with hierarchical harness grouping
      2. Individual per-harness heatmaps in each harness subdirectory

    Returns the path to the saved aggregate PNG.
    """
    plt, mcolors, mpatches, np = _check_matplotlib()

    results_dir = results_dir.resolve()
    heatmap_files = _load_heatmap_files(results_dir)

    # Merge all data for the aggregate view
    all_commits, all_test_cases, all_matrix, all_legend, harness_boundaries = _merge_heatmap_data(
        heatmap_files
    )

    # 1. Render aggregate heatmap
    if output_path is None:
        output_path = results_dir / "heatmap.png"

    aggregate_path = _render_single_heatmap(
        plt,
        mcolors,
        mpatches,
        np,
        all_commits,
        all_test_cases,
        all_matrix,
        all_legend,
        harness_boundaries,
        output_path,
        title=title,
        figsize=figsize,
    )

    # 2. Render per-harness heatmaps when multiple harnesses exist
    if len(heatmap_files) > 1:
        for hf in heatmap_files:
            harness_name = hf.parent.name
            with open(hf, encoding="utf-8") as f:
                data = json.load(f)

            h_commits = data.get("commits", [])
            h_test_cases = data.get("test_cases", [])
            h_matrix = data.get("matrix", {})
            h_legend = data.get("category_legend", {})

            if not h_commits or not h_test_cases:
                continue

            per_harness_path = hf.parent / "heatmap.png"
            _render_single_heatmap(
                plt,
                mcolors,
                mpatches,
                np,
                h_commits,
                h_test_cases,
                h_matrix,
                h_legend,
                {},  # no harness boundaries within a single harness
                per_harness_path,
                title=f"{harness_name} — {len(h_commits)} commits × {len(h_test_cases)} tests",
                figsize=figsize,
            )

    return aggregate_path
