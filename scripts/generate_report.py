#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
clawbio-bench Report Generator
===============================

Ingests a ``results/suite/<timestamp>/`` directory produced by
``clawbio-bench`` and renders a structured audit report as a Typst
document, then compiles it to PDF via the ``typst`` CLI.

Design goals
------------

* **Enumeration over titles.** The report's value is in listing every
  finding verbatim — category, rationale, ground-truth hazard metric,
  verdict details, citation — not in decorative headers. Sections are
  sectioned only where it helps an auditor navigate.
* **Stdlib only.** Matches the repo's "audit tool trusted-base"
  constraint. No jinja2, no pydantic, no matplotlib in this script.
* **Dynamic categories.** Every harness ships its own
  ``CATEGORY_LEGEND`` (color + label) via ``heatmap_data.json``. The
  report pulls colors from there so a new harness's categories render
  correctly without editing this script.
* **Chain of custody visible.** Every commit SHA, verdict hash, and
  environment fingerprint present in the results directory surfaces in
  the report so it is useful as evidence, not just as a summary.

Usage
-----

    python scripts/generate_report.py results/suite/20260405_024559/
    python scripts/generate_report.py results/suite/<ts>/ --output /tmp/audit.pdf
    python scripts/generate_report.py results/suite/<ts>/ --typ-only
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Palette (mirrors slides/clawbio-audit-slides.typ for brand consistency)
# ---------------------------------------------------------------------------

PALETTE = {
    # Industrial archival — near-monochrome with restrained severity accents
    "navy": "#1a1a2e",  # deep near-black for headings
    "steel": "#374151",  # dark grey for subheadings
    "sky": "#9ca3af",  # mid-grey for rules/accents (not used for text)
    "coral": "#b91c1c",  # muted dark red — critical severity
    "ember": "#92400e",  # muted dark amber — warning severity
    "forest": "#166534",  # muted dark green — pass
    "slate": "#6b7280",  # grey for metadata
    "pearl": "#f3f4f6",  # very light grey for subtle fills
    "snow": "#f9fafb",  # near-white
    "ink": "#111827",  # near-black body text
    "code_bg": "#f3f4f6",  # light grey code blocks (not dark)
    "code_fg": "#1f2937",  # dark text on light code blocks
}

# ---------------------------------------------------------------------------
# Unified 5-tier severity system
# ---------------------------------------------------------------------------
# Every verdict category maps to exactly one severity tier. The report uses
# tier colors instead of per-category colors for cross-harness consistency.

TIER_DEFS: dict[int, dict[str, str]] = {
    0: {"name": "Pass", "fill": "#166534", "bg": "#dcfce7", "text": "#166534"},
    1: {"name": "Advisory", "fill": "#854d0e", "bg": "#fef9c3", "text": "#854d0e"},
    2: {"name": "Warning", "fill": "#9a3412", "bg": "#ffedd5", "text": "#9a3412"},
    3: {"name": "Critical", "fill": "#991b1b", "bg": "#fee2e2", "text": "#991b1b"},
    4: {"name": "Infra", "fill": "#475569", "bg": "#f1f5f9", "text": "#475569"},
}

SEVERITY_TIERS: dict[str, int] = {
    # Tier 0: Pass — correct behavior, within spec
    "correct_determinate": 0,
    "correct_indeterminate": 0,
    "fst_correct": 0,
    "csv_honest": 0,
    "heim_bounded": 0,
    "edge_handled": 0,
    "score_correct": 0,
    "repro_functional": 0,
    "snp_valid": 0,
    "threshold_consistent": 0,
    "routed_correct": 0,
    "stub_warned": 0,
    "unroutable_handled": 0,
    "injection_blocked": 0,
    "exit_handled": 0,
    "demo_functional": 0,
    "finemap_correct": 0,
    "report_structure_complete": 0,
    # Tier 1: Advisory — technically correct but sub-optimal
    "scope_honest_indeterminate": 1,
    # Tier 2: Warning — incorrect or misleading, actionable
    "incorrect_indeterminate": 2,
    "disclosure_failure": 2,
    "fst_mislabeled": 2,
    "heim_unbounded": 2,
    "csv_inflated": 2,
    "repro_broken": 2,
    "snp_invalid": 2,
    "threshold_mismatch": 2,
    "stub_silent": 2,
    "exit_suppressed": 2,
    "susie_nonconvergence_suppressed": 2,
    "susie_moment_field_mislabeled": 2,
    "credset_pip_is_alpha_mismatch": 2,
    "credset_purity_mean_hides_weak": 2,
    "credset_purity_none_wrongly_pure": 2,
    "credset_coverage_incorrect": 2,
    "data_source_version_missing": 2,
    "limitations_missing": 2,
    "gene_disease_context_missing": 2,
    # Tier 3: Critical — crash, silent data loss, wrong result
    "incorrect_determinate": 3,
    "omission": 3,
    "fst_incorrect": 3,
    "edge_crash": 3,
    "score_incorrect": 3,
    "routed_wrong": 3,
    "unroutable_crash": 3,
    "injection_succeeded": 3,
    "demo_broken": 3,
    "pip_value_incorrect": 3,
    "pip_nan_silent": 3,
    "susie_null_forced_signal": 3,
    "susie_spurious_secondary_signal": 3,
    "abf_variant_n_collapsed": 3,
    "input_validation_missing": 3,
    "assembly_missing": 3,
    "transcript_missing": 3,
    "disclaimer_missing": 3,
    "evidence_trail_incomplete": 3,
    "reference_build_inconsistent": 3,
    # Tier 4: Infrastructure
    "harness_error": 4,
}


def category_tier(category: str) -> int:
    """Return the unified severity tier (0-4) for a category."""
    return SEVERITY_TIERS.get(category, 4)


# Fallback colors for categories that somehow lack a legend entry (should
# not happen, but keeps the report from blowing up on malformed input).
CATEGORY_FALLBACK_COLOR = {
    "harness_error": "#475569",
}
DEFAULT_UNKNOWN_COLOR = "#94a3b8"

# Cap on verdict.details dict entries rendered per finding — large SuSiE
# alpha / mu dumps would blow the page otherwise. The finding card notes
# how many were truncated.
MAX_DETAIL_ENTRIES = 8
MAX_DETAIL_VALUE_CHARS = 150

# When a severity group has more than this many findings with the same
# category, collapse them into a summary table instead of individual cards.
COLLAPSE_THRESHOLD = 5


# ---------------------------------------------------------------------------
# Typst escaping
# ---------------------------------------------------------------------------

# Typst markup characters that must be escaped when embedding arbitrary
# text inside content blocks. The list is deliberately broader than strict
# syntax requires because rationale strings can contain _arbitrary_ punctuation
# from upstream tools — leaning toward over-escaping keeps renders stable.
_TYPST_ESCAPES = {
    "\\": "\\\\",
    "#": "\\#",
    "$": "\\$",
    "@": "\\@",
    "*": "\\*",
    "_": "\\_",
    "`": "\\`",
    "<": "\\<",
    ">": "\\>",
    "[": "\\[",
    "]": "\\]",
    "~": "\\~",
    '"': '\\"',
}


def tesc(text: Any) -> str:
    """Escape an arbitrary value for safe interpolation into Typst markup."""
    if text is None:
        return ""
    s = str(text)
    out: list[str] = []
    for ch in s:
        out.append(_TYPST_ESCAPES.get(ch, ch))
    return "".join(out)


def tstr(text: Any) -> str:
    """Escape a value for use inside a Typst **string literal** (double-quoted).

    Only backslashes and double quotes need escaping inside ``"..."``; other
    markup characters are inert in string context.
    """
    if text is None:
        return ""
    s = str(text)
    return s.replace("\\", "\\\\").replace('"', '\\"')


def short_hash(h: str | None, length: int = 12) -> str:
    if not h:
        return "—"
    return h[:length]


def human_int(n: int) -> str:
    return f"{n:,}"


def pct(n: float) -> str:
    return f"{n:.1f}%"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_results(results_dir: Path) -> dict[str, Any]:
    """Walk the results directory and assemble everything the report needs.

    Returns a dict with the following shape::

        {
            "results_dir": Path,
            "aggregate": dict,       # aggregate_report.json (always required)
            "harnesses": [
                {
                    "name": "pharmgx-reporter",
                    "manifest": dict,
                    "summary": dict,
                    "heatmap": dict,
                    "verdicts": list[dict],   # all_verdicts.json
                    "verdict_hashes": dict,   # verdict_hashes.json (optional)
                    "aggregate_entry": dict,  # slice from aggregate_report
                },
                ...
            ]
        }

    Missing per-harness files are tolerated (a partial run still renders —
    the affected sections carry a loud warning). A missing
    ``aggregate_report.json`` is fatal because every section that aggregates
    across harnesses depends on it.
    """
    results_dir = results_dir.resolve()
    if not results_dir.is_dir():
        raise FileNotFoundError(f"Results directory not found: {results_dir}")

    aggregate_path = results_dir / "aggregate_report.json"
    if not aggregate_path.exists():
        raise FileNotFoundError(
            f"No aggregate_report.json in {results_dir}. "
            f"Pass the ``results/suite/<timestamp>/`` directory, not a sub-dir."
        )
    aggregate = _load_json(aggregate_path)

    harness_entries: list[dict[str, Any]] = []
    # Preserve aggregate's ordering so report sections match the suite run.
    for name in aggregate.get("harnesses", {}):
        h_dir = results_dir / name
        if not h_dir.is_dir():
            # Harness listed in aggregate but its subdirectory is gone —
            # still emit a stub so the auditor sees the gap.
            harness_entries.append(
                {
                    "name": name,
                    "manifest": None,
                    "summary": None,
                    "heatmap": None,
                    "verdicts": [],
                    "verdict_hashes": None,
                    "aggregate_entry": aggregate["harnesses"][name],
                    "missing": True,
                }
            )
            continue

        def _maybe(filename: str, _h_dir: Path = h_dir) -> Any | None:
            # ``_h_dir`` is bound as a default argument to capture the
            # current loop value; otherwise ruff B023 flags the closure
            # as late-binding on ``h_dir`` from the enclosing ``for``
            # loop, which would resolve all invocations to the final
            # iteration's directory.
            p = _h_dir / filename
            if not p.exists():
                return None
            try:
                return _load_json(p)
            except (OSError, json.JSONDecodeError) as exc:
                print(
                    f"  WARNING: failed to load {p.relative_to(results_dir)}: {exc}",
                    file=sys.stderr,
                )
                return None

        harness_entries.append(
            {
                "name": name,
                "manifest": _maybe("manifest.json"),
                "summary": _maybe("summary.json"),
                "heatmap": _maybe("heatmap_data.json"),
                "verdicts": _maybe("all_verdicts.json") or [],
                "verdict_hashes": _maybe("verdict_hashes.json"),
                "aggregate_entry": aggregate["harnesses"][name],
                "missing": False,
            }
        )

    return {
        "results_dir": results_dir,
        "aggregate": aggregate,
        "harnesses": harness_entries,
    }


# ---------------------------------------------------------------------------
# Finding extraction (used by both the at-a-glance matrix and the
# finding-by-finding enumeration section).
# ---------------------------------------------------------------------------


def is_failing(verdict_doc: dict[str, Any], pass_categories: list[str]) -> bool:
    """Anything that isn't a declared pass counts as a finding.

    ``harness_error`` is treated as a finding too — infrastructure failures
    are part of the audit record, not something to quietly drop.
    """
    category = verdict_doc.get("verdict", {}).get("category", "")
    if not category:
        return False
    return category not in pass_categories


_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")


def category_color(category: str, legend: dict[str, dict[str, str]]) -> str:
    """Return hex color for a category using the unified tier palette."""
    tier = category_tier(category)
    return TIER_DEFS[tier]["fill"]


def category_label(category: str, legend: dict[str, dict[str, str]]) -> str:
    entry = legend.get(category)
    if entry and isinstance(entry, dict):
        lbl = entry.get("label")
        if lbl:
            return str(lbl)
    return category.replace("_", " ")


# ---------------------------------------------------------------------------
# Typst emission helpers
# ---------------------------------------------------------------------------


def typst_rgb_var(name: str, hexcolor: str) -> str:
    return f'#let {name} = rgb("{hexcolor}")'


def emit_preamble(title: str, subtitle: str) -> str:
    """Page setup + color palette + typographic defaults.

    Industrial audit-console aesthetic: tight margins, monospace accents,
    bento-panel components, instrument-panel finding cards.
    """
    lines: list[str] = [
        "// clawbio-bench audit report — autogenerated, do not hand-edit.",
        f"// Title: {title}",
        "",
    ]
    for name, hexcolor in PALETTE.items():
        lines.append(typst_rgb_var(name, hexcolor))

    # Emit tier color variables
    lines.append("")
    lines.append("// ── Unified 5-tier severity palette ──")
    for _tier_idx, tdef in TIER_DEFS.items():
        tname = tdef["name"].lower()
        lines.append(f'#let tier-{tname}-fill = rgb("{tdef["fill"]}")')
        lines.append(f'#let tier-{tname}-bg   = rgb("{tdef["bg"]}")')
        lines.append(f'#let tier-{tname}-text = rgb("{tdef["text"]}")')

    lines += [
        "",
        "#set document(",
        f'  title: "{tstr(title)}",',
        f'  description: "{tstr(subtitle)}",',
        ")",
        "#set page(",
        '  paper: "a4",',
        "  margin: (x: 1.5cm, y: 1.6cm),",
        "  fill: white,",
        '  numbering: "1 / 1",',
        "  header: context {",
        "    if counter(page).get().first() > 1 [",
        "      #set text(7pt, fill: slate)",
        "      #grid(columns: (1fr, auto),",
        f"        [clawbio-bench #sym.dot.c {tesc(title)}],",
        "        [page #counter(page).display() / #counter(page).final().first()],",
        "      )",
        "      #v(0.06cm)",
        "      #line(length: 100%, stroke: 0.15pt + pearl)",
        "    ]",
        "  },",
        "  footer: context [",
        "    #set text(7pt, fill: slate)",
        "    #line(length: 100%, stroke: 0.15pt + pearl)",
        "    #v(0.04cm)",
        "    #align(center)[page #counter(page).display()]",
        "  ],",
        ")",
        "",
        "// ── Industrial Archivist typography ──",
        '#set text(font: "Libertinus Serif", size: 9pt, fill: ink)',
        "#set par(leading: 0.5em, justify: true)",
        "#set heading(numbering: none)",
        "#show heading.where(level: 1): it => {",
        "  v(0.4cm)",
        '  text(13pt, weight: "bold", fill: navy, upper(it.body))',
        "  v(0.08cm)",
        "  line(length: 100%, stroke: 0.6pt + ink)",
        "  v(0.2cm)",
        "}",
        "#show heading.where(level: 2): it => {",
        "  v(0.25cm)",
        '  text(11pt, weight: "bold", fill: ink, it.body)',
        "  v(0.05cm)",
        "  line(length: 100%, stroke: 0.25pt + steel)",
        "  v(0.12cm)",
        "}",
        '#show heading.where(level: 3): set text(9.5pt, weight: "bold", fill: steel)',
        '#show raw: set text(font: "DejaVu Sans Mono", size: 7pt)',
        "",
        "// ── instrument-panel primitives ──",
        '#let microcap(txt, color: slate) = text(6.6pt, font: "DejaVu Sans Mono", weight: "bold", tracking: 0.08em, fill: color, upper(txt))',
        '#let mono(txt, color: ink, size: 7.2pt) = text(size, font: "DejaVu Sans Mono", fill: color, txt)',
        '#let eyebrow(txt) = text(7.5pt, weight: "bold", fill: slate, tracking: 0.12em, upper(txt))',
        "#let rule() = { v(0.12cm); line(length: 100%, stroke: 0.15pt + pearl); v(0.15cm) }",
        "#let chip(label, color) = box(",
        "  fill: white,",
        "  stroke: 0.5pt + color,",
        "  inset: (x: 4pt, y: 1.5pt),",
        "  outset: (y: 1pt),",
        "  radius: 0pt,",
        '  text(6.8pt, weight: "bold", fill: color, label),',
        ")",
        "#let kvrow(k, v) = grid(",
        "  columns: (3cm, 1fr), column-gutter: 0.3cm, row-gutter: 0.1cm,",
        '  text(7.5pt, weight: "bold", fill: slate, k),',
        "  text(7.5pt, fill: ink, v),",
        ")",
        "// Instrument panel — hard-edge framed box",
        "#let ipanel(body, tint: white) = block(",
        "  fill: tint, width: 100%, inset: 7pt, radius: 0pt,",
        "  stroke: 0.4pt + steel, body,",
        ")",
        "// Dashboard metric cell",
        "#let dash-cell(title, body, tint: white, sc: steel) = block(",
        "  fill: tint, inset: 8pt, stroke: 0.4pt + sc, radius: 0pt, width: 100%,",
        "  [#microcap(title, color: sc) #v(4pt) #body],",
        ")",
        "// Metric mini-cell for finding headers",
        "#let metric-cell(label, value, tint: pearl) = block(",
        "  fill: tint, inset: (x: 4pt, y: 3pt), stroke: 0.3pt + steel,",
        "  [#microcap(label) #v(1pt) #mono(value, size: 7pt)],",
        ")",
        "#let codeblock(body) = block(",
        "  fill: code_bg, width: 100%, inset: 6pt, radius: 0pt,",
        "  stroke: 0.3pt + pearl,",
        '  text(fill: code_fg, font: "DejaVu Sans Mono", size: 6.8pt, body),',
        ")",
        "// Heatmap cell — tiny colored square",
        "#let hm-cell(fill-color) = box(",
        "  width: 8pt, height: 8pt, inset: 0pt, fill: fill-color,",
        "  stroke: 0.2pt + steel,",
        ")",
        "// Two-column wrapper",
        "#let two-col(left, right) = grid(",
        "  columns: (1fr, 1fr), column-gutter: 10pt,",
        "  left, right,",
        ")",
        "",
    ]
    return "\n".join(lines)


def emit_cover(
    title: str,
    subtitle: str,
    audit_target: str,
    commit_short: str,
    mode: str,
    date_str: str,
    suite_version: str,
    generated_at: str,
    overall: dict[str, Any],
) -> str:
    total_pass = overall.get("total_pass", 0)
    total_eval = overall.get("total_evaluated", 0)
    pass_rate = overall.get("total_pass_rate", 0.0)
    harness_errors = overall.get("total_harness_errors", 0)
    blocking = overall.get("blocking_skills", []) or []

    has_findings = total_eval > total_pass
    if harness_errors > 0 and has_findings:
        status_label = "FINDINGS + HARNESS ERRORS"
        status_color = "coral"
    elif harness_errors > 0:
        status_label = "HARNESS ERRORS"
        status_color = "ember"
    elif has_findings:
        status_label = "FINDINGS PRESENT"
        status_color = "coral"
    else:
        status_label = "OVERALL PASS"
        status_color = "forest"

    return "\n".join(
        [
            "// ── Compact archival cover ──",
            "#v(1.2cm)",
            "#eyebrow[Reproducible safety benchmark]",
            "#v(0.4cm)",
            f'#text(20pt, weight: "bold", fill: ink)[{tesc(title)}]',
            "#v(0.15cm)",
            f"#text(9pt, fill: slate)[{tesc(subtitle)}]",
            "#v(0.5cm)",
            "#line(length: 100%, stroke: 0.6pt + ink)",
            "#v(0.4cm)",
            "// Metadata — compact two-column layout",
            "#grid(columns: (1fr, 1fr), column-gutter: 0.8cm, row-gutter: 0.1cm,",
            f'  kvrow("Audit target", [{tesc(audit_target)}]),',
            f'  kvrow("HEAD commit", raw("{tstr(commit_short)}")),',
            f'  kvrow("Run mode", [{tesc(mode)}]),',
            f'  kvrow("Run date", [{tesc(date_str)}]),',
            f'  kvrow("Suite version", raw("{tstr(suite_version)}")),',
            f'  kvrow("Report generated", [{tesc(generated_at)}]),',
            ")",
            "#v(0.6cm)",
            "// Status — understated",
            f'#text(7pt, weight: "bold", fill: {status_color}, tracking: 0.1em)[{tesc(status_label)}]',
            "#v(0.15cm)",
            f'#text(32pt, weight: "bold", fill: ink)[{human_int(total_pass)} / {human_int(total_eval)}]',
            "#v(0.08cm)",
            f"#text(9pt, fill: slate)[tests passing ({pct(pass_rate)}) #sym.dot.c "
            f"{harness_errors} harness error"
            f"{'' if harness_errors == 1 else 's'} #sym.dot.c "
            f"{len(blocking)} blocking]",
            "#v(1cm)",
            "// Table of contents",
            '#text(7pt, weight: "bold", fill: slate, tracking: 0.1em)[CONTENTS]',
            "#v(0.1cm)",
            "#show outline.entry: set text(8pt)",
            "#outline(title: none, indent: 1.2em, depth: 2)",
            "#pagebreak()",
        ]
    )


def emit_executive_summary(
    harnesses: list[dict[str, Any]],
    aggregate: dict[str, Any],
) -> str:
    """Bento-grid executive dashboard + per-harness table."""
    overall = aggregate.get("overall") or {}
    total_pass = overall.get("total_pass", 0)
    total_eval = overall.get("total_evaluated", 0)
    total_fail = total_eval - total_pass
    total_errors = overall.get("total_harness_errors", 0)
    pass_rate = overall.get("total_pass_rate", 0.0)
    blocking = overall.get("blocking_skills", []) or []

    # Count persistent failures across all harnesses
    persistent_count = 0
    for h in harnesses:
        summary = h.get("summary") or {}
        meta = summary.get("_meta") or {}
        persistent_count += len(meta.get("persistent_failures") or [])

    # Find dominant failure categories
    from collections import Counter

    fail_cats: Counter[str] = Counter()
    for h in harnesses:
        entry = h["aggregate_entry"]
        cats = entry.get("categories") or {}
        pass_cats = set(entry.get("pass_categories") or [])
        for cat, count in cats.items():
            if cat not in pass_cats and cat != "harness_error":
                fail_cats[cat] += count
    top_fails = fail_cats.most_common(4)
    top_fails_rows: list[str] = []
    for cat, count in top_fails:
        top_fails_rows.append(f'      [#mono("{tstr(cat)}", size: 6.8pt)], [{count}],')

    # Blocking harnesses names
    blocking_names = ", ".join(blocking[:6]) if blocking else "none"

    lines: list[str] = [
        "== Executive summary",
        "",
        "// ── Bento dashboard ──",
        "#grid(",
        "  columns: (1.15fr, 0.85fr, 0.85fr),",
        "  rows: (auto, auto),",
        "  column-gutter: 7pt,",
        "  row-gutter: 7pt,",
        "",
        "  // Cell 1: suite status",
        "  dash-cell(",
        '    "suite status",',
        "    [",
        f'      #text(22pt, weight: "bold", fill: ink)[{human_int(total_pass)} / {human_int(total_eval)}]',
        "      #v(2pt)",
        f"      #text(8pt, fill: slate)[tests passing ({pct(pass_rate)})]",
        "      #v(4pt)",
        "      #line(length: 100%, stroke: 0.2pt + pearl)",
        "      #v(4pt)",
        "      #grid(",
        "        columns: (1fr, 1fr, 1fr),",
        "        column-gutter: 4pt,",
        f'        [#mono("fail {human_int(total_fail)}", color: coral)],',
        f'        [#mono("infra {human_int(total_errors)}", color: ember)],',
        f'        [#mono("pass {pct(pass_rate)}", color: forest)],',
        "      )",
        "    ],",
        "    tint: pearl,",
        "    sc: ink,",
        "  ),",
        "",
        "  // Cell 2: blocking harnesses",
        "  dash-cell(",
        '    "blocking harnesses",',
        "    [",
        f'      #text(18pt, weight: "bold", fill: coral)[{len(blocking)}]',
        "      #v(2pt)",
        f"      #text(7pt)[{tesc(blocking_names)}]",
        "    ],",
        "    tint: coral.lighten(92%),",
        "    sc: coral,",
        "  ),",
        "",
        "  // Cell 3: persistent failures",
        "  dash-cell(",
        '    "persistent failures",',
        "    [",
        f'      #text(18pt, weight: "bold", fill: ember)[{persistent_count}]',
        "      #v(2pt)",
        "      #text(7pt)[across all commits]",
        "    ],",
        "    tint: ember.lighten(90%),",
        "    sc: ember,",
        "  ),",
        "",
    ]

    # Cell 4: top failure categories
    if top_fails_rows:
        lines += [
            "  // Cell 4: dominant failure classes",
            "  dash-cell(",
            '    "top failure classes",',
            "    [",
            "      #table(",
            "        columns: (1fr, auto),",
            "        stroke: none,",
            "        inset: 1.5pt,",
            *[f"    {r}" for r in top_fails_rows],
            "      )",
            "    ],",
            "  ),",
            "",
        ]
    else:
        lines += [
            "  dash-cell(",
            '    "top failure classes",',
            "    [#text(8pt, fill: forest)[No failures]],",
            "  ),",
            "",
        ]

    # Cell 5: harness pass rates mini-table
    harness_mini_rows: list[str] = []
    for h in harnesses:
        entry = h["aggregate_entry"]
        hrate = entry.get("pass_rate", 0.0)
        hcolor = "forest" if hrate >= 85 else ("ember" if hrate >= 50 else "coral")
        harness_mini_rows.append(
            f'      [#mono("{tstr(h["name"][:18])}", size: 6.5pt)], '
            f'[#mono("{pct(hrate)}", color: {hcolor}, size: 6.8pt)],'
        )

    lines += [
        "  // Cell 5: per-harness rates",
        "  dash-cell(",
        '    "harness rates",',
        "    [",
        "      #table(",
        "        columns: (1fr, auto),",
        "        stroke: none,",
        "        inset: 1.5pt,",
        *[f"    {r}" for r in harness_mini_rows],
        "      )",
        "    ],",
        "  ),",
        "",
    ]

    # Cell 6: audit target info
    commit_short = short_hash(aggregate.get("clawbio_commit"), 8) or "—"
    mode = aggregate.get("mode") or "—"
    date_str = aggregate.get("date") or "—"
    lines += [
        "  // Cell 6: audit target",
        "  dash-cell(",
        '    "audit target",',
        "    [",
        '      #mono("ClawBio HEAD", size: 8.5pt)',
        "      #v(2pt)",
        f'      #mono("{tstr(commit_short)}", size: 7.5pt, color: slate)',
        "      #v(2pt)",
        f"      #text(7pt, fill: slate)[{tesc(date_str)} {tesc(mode)} run]",
        "    ],",
        "  ),",
        ")",
        "#v(0.3cm)",
        "",
    ]

    # Per-harness summary table (compact)
    rows: list[str] = []
    for h in harnesses:
        entry = h["aggregate_entry"]
        name = h["name"]
        total = entry.get("total_cases", 0)
        evaluated = entry.get("evaluated", 0)
        pass_count = entry.get("pass_count", 0)
        fail_count = entry.get("fail_count", 0)
        harness_errors = entry.get("harness_errors", 0)
        h_pass_rate = entry.get("pass_rate", 0.0)
        passed = entry.get("pass", False)
        status_chip = "forest" if passed else "coral"
        status_word = "PASS" if passed else "FINDINGS"
        rows.append(
            "  [{name}], {status}, [{pass_count}/{eval}], [{rate}], "
            "[{fail}], [{errors}], [{total}],".format(
                name=tesc(name),
                status=f'chip("{status_word}", {status_chip})',
                pass_count=human_int(pass_count),
                eval=human_int(evaluated),
                rate=pct(h_pass_rate),
                fail=human_int(fail_count),
                errors=human_int(harness_errors),
                total=human_int(total),
            )
        )

    lines += [
        '#show table.cell.where(y: 0): set text(weight: "bold", fill: white, size: 7.5pt)',
        "#table(",
        "  columns: (1fr, auto, auto, auto, auto, auto, auto),",
        "  align: (left, center, right, right, right, right, right),",
        "  inset: 4pt,",
        "  stroke: none,",
        "  fill: (x, y) => if y == 0 { navy } else if calc.odd(y) { pearl } else { white },",
        "  [Harness], [Status], [Pass], [Rate], [Fail], [Errors], [Total],",
        *rows,
        ")",
        "",
    ]
    return "\n".join(lines)


def emit_rubric_table(heatmap: dict[str, Any] | None) -> str:
    """Render the category legend grouped by severity tier.

    Falls back to an empty note if the heatmap / legend is unavailable
    (e.g. a harness that crashed before writing heatmap_data.json).
    """
    if not heatmap:
        return (
            '#text(9pt, style: "italic", fill: slate)'
            "[No rubric available — harness did not emit heatmap\\_data.json.]\n"
        )
    legend = heatmap.get("category_legend") or {}
    if not legend:
        return (
            '#text(9pt, style: "italic", fill: slate)'
            "[Rubric legend missing from heatmap\\_data.json.]\n"
        )

    # Build sorted list: tier asc, then category name asc
    entries: list[tuple[int, str, str]] = []
    for cat in sorted(legend.keys()):
        entry = legend[cat] or {}
        label = entry.get("label", cat.replace("_", " "))
        tier = category_tier(cat)
        entries.append((tier, cat, label))
    entries.sort(key=lambda e: (e[0], e[1]))

    lines: list[str] = [
        '#text(9pt, weight: "bold", fill: steel)[Rubric]',
        "#v(0.04cm)",
        "#table(",
        "  columns: (auto, auto, 1fr),",
        "  align: (center, left, left),",
        "  inset: 2pt,",
        "  stroke: none,",
    ]
    current_tier: int | None = None
    for tier, cat, label in entries:
        if tier != current_tier:
            current_tier = tier
            tdef = TIER_DEFS[tier]
            fill_hex = tdef["fill"]
            # Tier separator row
            lines.append(
                f"  table.cell(colspan: 3, inset: (top: 3pt, bottom: 1pt))"
                f'[#text(6.5pt, weight: "bold", fill: rgb("{fill_hex}"))'
                f"[{tesc(tdef['name'])}]],"
            )
        color_hex = TIER_DEFS[tier]["fill"]
        lines.append(
            f'  box(width: 6pt, height: 6pt, fill: rgb("{color_hex}"), stroke: none), '
            f'text(6.8pt, raw("{tstr(cat)}")), '
            f"text(6.8pt)[{tesc(label)}],"
        )
    lines.append(")")
    lines.append("#v(0.08cm)")

    return "\n".join(lines)


def emit_harness_header(h: dict[str, Any]) -> str:
    name = h["name"]
    entry = h["aggregate_entry"]
    total = entry.get("total_cases", 0)
    evaluated = entry.get("evaluated", 0)
    pass_count = entry.get("pass_count", 0)
    fail_count = entry.get("fail_count", 0)
    pass_rate = entry.get("pass_rate", 0.0)
    errors = entry.get("harness_errors", 0)

    # Pass-rate bar (width proportional to pass_rate)
    bar_frac = max(0.0, min(pass_rate / 100.0, 1.0))
    bar_color = "forest" if bar_frac >= 0.85 else ("ember" if bar_frac >= 0.5 else "coral")

    return "\n".join(
        [
            "#pagebreak(weak: true)",
            f"== {tesc(name)}",
            "// Stats strip",
            "#grid(columns: (auto, 1fr, auto), column-gutter: 0.3cm, align: horizon,",
            "  [",
            "    #stack(dir: ltr,",
            "      box(width: 3.5cm, height: 0.25cm, radius: 0pt, fill: pearl)[",
            f"        #place(left + horizon, box(width: {bar_frac * 3.5:.2f}cm, height: 0.25cm, radius: 0pt, fill: {bar_color}))",
            "      ],",
            "      h(0.1cm),",
            f'      text(9pt, weight: "bold", fill: ink)[{pct(pass_rate)}],',
            "    )",
            "  ],",
            f"  text(8pt, fill: slate)[{human_int(pass_count)}/{human_int(evaluated)} pass #sym.dot.c {human_int(fail_count)} findings #sym.dot.c {human_int(errors)} errors],",
            f"  text(8pt, fill: slate)[{human_int(total)} total],",
            ")",
            "#v(0.1cm)",
        ]
    )


def emit_commit_snapshot(h: dict[str, Any]) -> str:
    """Per-commit pass rate table. Shows 1-N rows depending on run mode."""
    summary = h.get("summary") or {}
    if not summary:
        return ""
    rows: list[str] = []
    # Deterministic commit ordering: use build_summary's insertion order, but
    # drop the _meta sentinel key.
    for sha, s in summary.items():
        if sha == "_meta":
            continue
        short = s.get("short_sha") or sha[:8]
        date = (s.get("commit_date") or "")[:10]
        message = s.get("commit_message") or ""
        if len(message) > 70:
            message = message[:67] + "..."
        pass_count = s.get("pass_count", 0)
        evaluated = s.get("evaluated", 0)
        rate = s.get("pass_rate", 0.0)
        errors = s.get("harness_errors", 0)
        rows.append(
            '  raw("{short}"), [{date}], [{msg}], [{pc}/{ev}], [{rate}], [{err}],'.format(
                short=tstr(short),
                date=tesc(date or "—"),
                msg=tesc(message or "—"),
                pc=human_int(pass_count),
                ev=human_int(evaluated),
                rate=pct(rate),
                err=human_int(errors),
            )
        )
    if not rows:
        return ""

    return "\n".join(
        [
            '#text(9pt, weight: "bold", fill: steel)[Commits evaluated]',
            "#v(0.08cm)",
            '#show table.cell.where(y: 0): set text(weight: "bold", fill: white, size: 7.5pt)',
            "#table(",
            "  columns: (auto, auto, 1fr, auto, auto, auto),",
            "  align: (left, left, left, right, right, right),",
            "  inset: 4pt,",
            "  stroke: none,",
            "  fill: (x, y) => if y == 0 { navy } else if calc.odd(y) { pearl } else { white },",
            "  [Commit], [Date], [Message], [Pass], [Rate], [Errors],",
            *rows,
            ")",
            "#v(0.15cm)",
        ]
    )


def _group_tests(tests: list[str]) -> list[tuple[str, list[str]]]:
    """Group test case names by prefix (gene/module).

    Heuristic: split on ``_`` and use the first token (uppercased) as the
    group. Known prefixes like ``eq_NN``, ``ng_NN``, ``mg_NN``, ``fm_NN``,
    ``cvr_NN`` are handled specially to extract the meaningful subgroup.
    Tests that don't group cleanly go into "OTHER".
    """
    groups: dict[str, list[str]] = {}
    for tc in tests:
        parts = tc.split("_")
        if len(parts) < 2:
            group = "OTHER"
        elif parts[0] in ("eq", "ng", "mg", "fm", "cvr"):
            # Equity: eq_01_fst_known_af → FST; eq_13_csv_ancestry → CSV
            # NutriGx: ng_01_all_risk_elevated → ALL_RISK / FOLATE / etc.
            # Finemapping: fm_01_abf_single_causal → ABF / SUSIE / CREDSET
            group = parts[2].upper() if len(parts) >= 3 else parts[0].upper()
        elif parts[0] in ("comp", "err", "ext", "force", "inj", "kw"):
            # Orchestrator prefix groups
            group = parts[0].upper()
        else:
            # pharmgx: cyp2d6_pm → CYP2D6, dpyd_2a_het → DPYD, warfarin_* → WARFARIN
            group = parts[0].upper()
        groups.setdefault(group, []).append(tc)

    return list(groups.items())


def emit_verdict_matrix(h: dict[str, Any]) -> str:
    """Grouped heatmap grid: rows grouped by gene/module, columns = commits.

    Compact layout: group label inline with first test, colored squares,
    category text right-aligned. Single-test groups are merged into an
    "OTHER" bucket to avoid vertical waste.
    """
    heatmap = h.get("heatmap") or {}
    commits = heatmap.get("commits") or []
    tests = heatmap.get("test_cases") or []
    matrix = heatmap.get("matrix") or {}
    if not commits or not tests:
        return ""

    raw_grouped = _group_tests(tests)
    single_commit = len(commits) == 1

    # Merge single-test groups into an "OTHER" bucket to avoid wasting
    # one group-label row per lone test (common in pharmgx: G6PD, MT, etc.)
    grouped: list[tuple[str, list[str]]] = []
    other_tests: list[str] = []
    for group_name, group_tests in raw_grouped:
        if len(group_tests) == 1:
            other_tests.append(group_tests[0])
        else:
            grouped.append((group_name, group_tests))
    if other_tests:
        grouped.append(("OTHER", other_tests))

    body_rows: list[str] = []
    for group_name, group_tests in grouped:
        # Compute group pass rate
        group_pass = 0
        group_total = 0
        for tc in group_tests:
            for c in commits:
                sha = c.get("sha") or ""
                cell = matrix.get(f"{sha}:{tc}") or {}
                cat = cell.get("category") or ""
                if cat:
                    group_total += 1
                    if category_tier(cat) == 0:
                        group_pass += 1
        grate = (group_pass / group_total * 100) if group_total > 0 else 0
        gcolor = "forest" if grate >= 85 else ("ember" if grate >= 50 else "coral")

        # Group separator row spanning all columns
        n_cols = 3 if single_commit else 1 + len(commits)
        body_rows.append(
            f"  table.cell(colspan: {n_cols}, inset: (top: 4pt, bottom: 1pt))["
            f'#text(6.5pt, weight: "bold", fill: ink)[{tesc(group_name)}] '
            f"#text(6pt, fill: {gcolor})[{pct(grate)}] "
            f"#text(6pt, fill: slate)[({len(group_tests)})]"
            "],"
        )

        for tc in group_tests:
            # Test name — strip group prefix for compactness
            suffix = tc
            parts = tc.split("_")
            if len(parts) >= 3 and parts[0] in ("eq", "ng", "mg", "fm", "cvr"):
                suffix = "_".join(parts[3:]) if len(parts) > 3 else parts[-1]
            elif len(parts) >= 2:
                suffix = "_".join(parts[1:])
            body_rows.append(f'  [#mono("{tstr(suffix)}", size: 6.5pt)],')

            # Heatmap cells
            for c in commits:
                sha = c.get("sha") or ""
                cell = matrix.get(f"{sha}:{tc}") or {}
                category = cell.get("category") or ""
                if not category:
                    body_rows.append(
                        "  box(width: 8pt, height: 8pt, fill: pearl, stroke: 0.2pt + steel),"
                    )
                else:
                    tier = category_tier(category)
                    fill_hex = TIER_DEFS[tier]["fill"]
                    body_rows.append(f'  hm-cell(rgb("{fill_hex}")),')

            # Category name in single-commit mode
            if single_commit:
                sha = commits[0].get("sha") or ""
                cell = matrix.get(f"{sha}:{tc}") or {}
                category = cell.get("category") or "—"
                tier = category_tier(category) if category != "—" else 4
                text_hex = TIER_DEFS[tier]["text"]
                body_rows.append(
                    f'  [#text(6.5pt, fill: rgb("{text_hex}"), '
                    f'font: "DejaVu Sans Mono")[{tesc(category)}]],'
                )

    # Split into pass/fail columns for density
    pass_rows: list[str] = []
    fail_rows: list[str] = []

    for group_name, group_tests in grouped:
        group_pass_tests: list[tuple[str, str, str]] = []  # (suffix, fill_hex, category)
        group_fail_tests: list[tuple[str, str, str]] = []
        for tc in group_tests:
            suffix = tc
            parts = tc.split("_")
            if len(parts) >= 3 and parts[0] in ("eq", "ng", "mg", "fm", "cvr"):
                suffix = "_".join(parts[3:]) if len(parts) > 3 else parts[-1]
            elif len(parts) >= 2:
                suffix = "_".join(parts[1:])
            sha = commits[0].get("sha") or "" if single_commit else ""
            cell = matrix.get(f"{sha}:{tc}") or {} if single_commit else {}
            category = cell.get("category") or ""
            tier = category_tier(category) if category else 4
            fill_hex = TIER_DEFS[tier]["fill"]
            if tier == 0:
                group_pass_tests.append((suffix, fill_hex, category))
            else:
                group_fail_tests.append((suffix, fill_hex, category))

        for target, tests_list in [
            (pass_rows, group_pass_tests),
            (fail_rows, group_fail_tests),
        ]:
            if not tests_list:
                continue
            gcolor = "forest" if target is pass_rows else "coral"
            target.append(
                f"  table.cell(colspan: 2, inset: (top: 3pt, bottom: 0pt))"
                f'[#text(6pt, weight: "bold", fill: {gcolor})'
                f"[{tesc(group_name)} ({len(tests_list)})]],"
            )
            for suffix, fill_hex, category in tests_list:
                tier = category_tier(category) if category else 4
                text_hex = TIER_DEFS[tier]["text"]
                target.append(
                    f'  [#hm-cell(rgb("{fill_hex}")) '
                    f'#mono("{tstr(suffix)}", size: 6pt)], '
                    f'[#text(6pt, fill: rgb("{text_hex}"), '
                    f'font: "DejaVu Sans Mono")[{tesc(category)}]],'
                )

    lines: list[str] = [
        '#text(9.5pt, weight: "bold", fill: steel)[Verdict matrix]',
        "#v(0.06cm)",
        "#grid(columns: (1fr, 1fr), column-gutter: 8pt,",
        "  [",
        f'    #microcap("passing ({sum(1 for r in pass_rows if not r.strip().startswith("table.cell"))})", color: forest)',
        "    #v(2pt)",
        "    #table(columns: (1fr, auto), inset: (x: 2pt, y: 1pt), stroke: none,",
    ]
    lines.extend(f"    {r}" for r in pass_rows)
    lines.append("    )")
    lines.append("  ], [")
    lines.append(
        f'    #microcap("findings ({sum(1 for r in fail_rows if not r.strip().startswith("table.cell"))})", color: coral)'
    )
    lines.append("    #v(2pt)")
    lines.append("    #table(columns: (1fr, auto), inset: (x: 2pt, y: 1pt), stroke: none,")
    lines.extend(f"    {r}" for r in fail_rows)
    lines.append("    )")
    lines.append("  ]")
    lines.append(")")
    lines.append("#v(0.12cm)")
    return "\n".join(lines)


def emit_persistent_failures(h: dict[str, Any]) -> str:
    summary = h.get("summary") or {}
    meta = summary.get("_meta") or {}
    persistent = meta.get("persistent_failures") or []
    always_err = meta.get("always_harness_errored") or []
    if not persistent and not always_err:
        return ""

    def _compact_name_list(names: list[str], label: str, color: str) -> list[str]:
        """Render a name list — use columns for long lists."""
        out: list[str] = []
        out.append(f'#text(9pt, weight: "bold", fill: {color})[{label} ({len(names)})]')
        out.append("#v(0.06cm)")
        if len(names) <= 8:
            out.append("#list(")
            for n in names:
                out.append(f'  raw("{tstr(n)}"),')
            out.append(")")
        else:
            # Compact 2-column table for large lists
            out.append("#table(")
            out.append("  columns: (1fr, 1fr),")
            out.append("  inset: 2pt,")
            out.append("  stroke: none,")
            for n in names:
                out.append(f'  text(7.5pt, raw("{tstr(n)}")),')
            # Pad odd count
            if len(names) % 2:
                out.append("  [],")
            out.append(")")
        out.append("#v(0.12cm)")
        return out

    parts: list[str] = []
    if persistent:
        parts.extend(_compact_name_list(persistent, "Persistent failures", "coral"))
    if always_err:
        parts.extend(_compact_name_list(always_err, "Always harness-errored", "ember"))
    return "\n".join(parts)


def _truncate(value: Any, limit: int = MAX_DETAIL_VALUE_CHARS) -> str:
    """Stringify and truncate a verdict.details value for embedding."""
    s = json.dumps(value, default=str) if isinstance(value, (list, dict)) else str(value)
    s = " ".join(s.split())  # collapse all whitespace
    if len(s) > limit:
        s = s[: limit - 3] + "..."
    return s


def emit_finding_card(
    verdict_doc: dict[str, Any],
    legend: dict[str, dict[str, str]],
    index: int,
    total: int,
) -> str:
    v = verdict_doc.get("verdict") or {}
    gt = verdict_doc.get("ground_truth") or {}
    refs = verdict_doc.get("ground_truth_references") or {}
    commit = verdict_doc.get("commit") or {}
    tc = verdict_doc.get("test_case") or {}

    category = v.get("category") or "unknown"
    rationale = v.get("rationale") or ""
    details = v.get("details") or {}
    test_name = tc.get("name") or "unknown"
    commit_short = commit.get("short") or (commit.get("sha") or "")[:8]
    verdict_sha = verdict_doc.get("_verdict_sha256") or ""

    label = category_label(category, legend)

    # Ground-truth narrative block — pulled from the FINDING / HAZARD_METRIC
    # / DERIVATION fields. FINDING_CATEGORY is omitted because it's
    # redundant with the category already shown in the card header.
    gt_fields: list[tuple[str, str]] = []
    for key in ("FINDING", "HAZARD_METRIC", "DERIVATION"):
        val = gt.get(key)
        if val:
            gt_fields.append((key, str(val)))

    # Citation resolution: ground truth might reference a named entry in
    # ground_truth_references (e.g. WAKEFIELD_REF="WAKEFIELD_2009") and also
    # carry an inline CITATION string. Surface both when present.
    citation_lines: list[str] = []
    inline_cit = gt.get("CITATION")
    if inline_cit:
        citation_lines.append(str(inline_cit))
    for ref_key, ref_val in refs.items():
        # Heuristic: a citation ref is interesting if its key appears
        # somewhere in ground_truth (e.g. WAKEFIELD_REF=WAKEFIELD_2009
        # points into refs[WAKEFIELD_2009]).
        if any(isinstance(v_, str) and ref_key == v_ for v_ in gt.values()):
            citation_lines.append(f"{ref_key}: {ref_val}")

    # Detail dict — cap length to prevent unbounded card growth.
    detail_items = list(details.items())
    detail_truncated = len(detail_items) > MAX_DETAIL_ENTRIES
    detail_items = detail_items[:MAX_DETAIL_ENTRIES]

    tier = category_tier(category)
    tier_fill = TIER_DEFS[tier]["fill"]
    tier_name = TIER_DEFS[tier]["name"]
    hash_display = short_hash(verdict_sha, 10) if verdict_sha else "—"

    lines: list[str] = []
    # Instrument-panel finding card — hard rectangular frame
    lines.append("  #ipanel(tint: white)[")

    # ── Header row: severity dot · test name · category · index ──
    lines.append("    #grid(columns: (auto, 1fr, auto, auto), column-gutter: 5pt, align: horizon,")
    lines.append(f'      box(width: 8pt, height: 8pt, fill: rgb("{tier_fill}"), stroke: none),')
    lines.append(f'      text(8.5pt, weight: "bold", fill: ink, raw("{tstr(test_name)}")),')
    lines.append(
        f'      text(7pt, fill: rgb("{tier_fill}"), font: "DejaVu Sans Mono",'
        f' raw("{tstr(category)}")),'
    )
    lines.append(f"      text(7pt, fill: slate)[{index}/{total}],")
    lines.append("    )")

    # ── Metadata strip: commit · hash · tier — single tight row ──
    lines.append("    #v(1pt)")
    lines.append(
        '    #text(6pt, fill: slate, font: "DejaVu Sans Mono")['
        f"commit {tesc(commit_short)} "
        f"#sym.dot.c hash {tesc(hash_display)} "
        f"#sym.dot.c {tesc(tier_name)} "
        f"#sym.dot.c {tesc(label)}]"
    )
    lines.append("    #v(2pt)")
    lines.append("    #line(length: 100%, stroke: 0.15pt + steel)")
    lines.append("    #v(2pt)")

    # ── Rationale — left-aligned, compact ──
    if rationale:
        lines.append('    #microcap("rationale", color: steel)')
        lines.append("    #v(1pt)")
        lines.append(f"    #par(justify: false)[#text(8pt, fill: ink)[{tesc(rationale)}]]")

    # ── Evidence compartments — use full width, adapt column count ──
    if gt_fields:
        lines.append("    #v(2pt)")
        gt_cols = min(len(gt_fields), 3)
        col_spec = ", ".join(["1fr"] * gt_cols)
        lines.append(f"    #grid(columns: ({col_spec}), column-gutter: 3pt,")
        for key, val in gt_fields:
            display_val = val if len(val) <= 200 else val[:197] + "..."
            lines.append(
                "      block(fill: pearl, width: 100%, inset: 3pt, stroke: 0.2pt + steel)["
            )
            lines.append(f'        #microcap("{tstr(key)}")')
            lines.append("        #v(1pt)")
            lines.append(f"        #par(justify: false)[#text(6.8pt)[{tesc(display_val)}]]")
            lines.append("      ],")
        lines.append("    )")

    # ── Verdict details — wider key column, proper spacing ──
    if detail_items:
        lines.append("    #block(fill: snow, width: 100%, inset: 4pt, stroke: 0.2pt + steel)[")
        lines.append('      #microcap("verdict details")')
        lines.append("      #v(1pt)")
        lines.append("      #table(columns: (3.8cm, 1fr), inset: (x: 2pt, y: 1pt), stroke: none,")
        for k, val in detail_items:
            lines.append(
                f'        [#mono("{tstr(k)}", color: slate, size: 6.5pt)], '
                f'[#mono("{tstr(_truncate(val))}", size: 6.5pt)],'
            )
        if detail_truncated:
            remaining = len(details) - MAX_DETAIL_ENTRIES
            lines.append(f"        [], [#text(6pt, fill: slate)[... {remaining} more omitted]],")
        lines.append("      )")
        lines.append("    ]")

    # ── Citations — inline, minimal ──
    if citation_lines:
        lines.append("    #v(2pt)")
        cit_text = " · ".join(citation_lines)
        if len(cit_text) > 140:
            cit_text = cit_text[:137] + "..."
        lines.append(f'    #text(6.5pt, fill: slate, style: "italic")[{tesc(cit_text)}]')
    lines.append("  ]")
    lines.append("  #v(0.1cm)")
    return "\n".join(lines)


def emit_findings_section(h: dict[str, Any]) -> str:
    entry = h["aggregate_entry"]
    pass_categories = list(entry.get("pass_categories") or [])
    verdicts = h.get("verdicts") or []
    heatmap = h.get("heatmap") or {}
    legend = heatmap.get("category_legend") or {}

    # Extract and sort by severity using the harness's own category sets
    # (palette-independent). Fail categories → tier 0, harness_error → tier 3,
    # non-pass/non-fail (warnings) → tier 1, unknown → tier 2.
    failing = [v for v in verdicts if is_failing(v, pass_categories)]
    fail_categories = set(entry.get("fail_categories") or [])

    def _severity_key(v: dict[str, Any]) -> tuple[int, str, str, str]:
        cat = v.get("verdict", {}).get("category", "zzz")
        if cat in fail_categories:
            tier = 0
        elif cat == "harness_error":
            tier = 3
        elif cat not in pass_categories:
            tier = 1  # warning tier (non-pass, non-fail)
        else:
            tier = 2  # unknown / fallback
        # Include category in sort key so same-category findings are contiguous
        # (required for the collapse-into-table logic to work correctly).
        return (
            tier,
            cat,
            v.get("test_case", {}).get("name", ""),
            v.get("commit", {}).get("short", ""),
        )

    failing.sort(key=_severity_key)

    if not failing:
        return "\n".join(
            [
                '#text(11pt, weight: "bold", fill: forest)[No findings]',
                "#v(0.1cm)",
                "#text(10pt, fill: slate)[Every evaluated test case landed in a pass category for this harness.]",
                "#v(0.4cm)",
            ]
        )

    # Group by severity tier for section headers (using unified tier system)
    tier_names = {0: "Critical", 1: "Warnings", 2: "Other", 3: "Harness errors"}
    tier_palette = {
        0: TIER_DEFS[3]["fill"],  # critical → tier 3 colors
        1: TIER_DEFS[2]["fill"],  # warnings → tier 2 colors
        2: TIER_DEFS[4]["fill"],  # other → tier 4 colors
        3: TIER_DEFS[4]["fill"],  # harness errors → tier 4 colors
    }

    lines: list[str] = [
        f'#text(11pt, weight: "bold", fill: coral)[Findings ({len(failing)})]',
        "#v(0.1cm)",
        "#text(8pt, fill: slate)[One card per non-passing verdict. "
        f"Same-category groups above {COLLAPSE_THRESHOLD} are collapsed into summary tables.]",
        "#v(0.15cm)",
    ]

    # Pre-compute per-category counts for collapse detection
    from collections import Counter

    cat_counts: Counter[str] = Counter()
    for fv in failing:
        cat_counts[fv.get("verdict", {}).get("category", "")] += 1

    current_tier: int | None = None
    card_index = 0
    i = 0
    while i < len(failing):
        v = failing[i]
        cat = v.get("verdict", {}).get("category", "")
        if cat in fail_categories:
            tier = 0
        elif cat == "harness_error":
            tier = 3
        elif cat not in pass_categories:
            tier = 1
        else:
            tier = 2

        # Severity group header
        if tier != current_tier:
            current_tier = tier
            tier_count = sum(1 for fv in failing if _severity_key(fv)[0] == tier)
            tc_hex = tier_palette.get(tier, TIER_DEFS[4]["fill"])
            tn = tier_names.get(tier, "Other")
            lines.append(
                f'#block(fill: rgb("{tc_hex}").lighten(92%), inset: (x: 6pt, y: 3pt), '
                f'width: 100%, stroke: 0.4pt + rgb("{tc_hex}"))['
            )
            lines.append(
                f'  #text(8pt, weight: "bold", fill: rgb("{tc_hex}"))[{tesc(tn)} ({tier_count})]'
            )
            lines.append("]")
            lines.append("#v(0.06cm)")

        # Collect consecutive same-category findings
        run: list[dict[str, Any]] = [v]
        j = i + 1
        while j < len(failing) and failing[j].get("verdict", {}).get("category", "") == cat:
            run.append(failing[j])
            j += 1

        if len(run) >= COLLAPSE_THRESHOLD:
            # Collapse into a compact instrument-panel summary
            cat_tier = category_tier(cat)
            cat_fill = TIER_DEFS[cat_tier]["fill"]
            lbl = category_label(cat, legend)
            lines.append("  #ipanel()[")
            lines.append(
                "    #grid(columns: (auto, 1fr, auto), column-gutter: 5pt, align: horizon, "
                f'box(width: 8pt, height: 8pt, fill: rgb("{cat_fill}"), stroke: none), '
                f'text(8.5pt, weight: "bold", fill: navy)'
                f"[{tesc(cat)} #text(7.5pt, fill: slate)[({len(run)} findings)]], "
                f'chip(raw("{tstr(cat)}"), rgb("{cat_fill}")),'
                ")"
            )
            lines.append("    #v(4pt)")
            lines.append(f"    #text(7.5pt, fill: slate)[{tesc(lbl)}]")
            lines.append("    #v(4pt)")
            # Compact table of test names + rationales
            lines.append("    #table(")
            lines.append("      columns: (auto, 1fr),")
            lines.append("      align: (left, left),")
            lines.append("      inset: 2.5pt,")
            lines.append("      stroke: (bottom: 0.1pt + pearl),")
            lines.append('      [#microcap("test")], [#microcap("rationale")],')
            for rv in run:
                card_index += 1
                tname = rv.get("test_case", {}).get("name", "?")
                rat = rv.get("verdict", {}).get("rationale", "")
                if len(rat) > 100:
                    rat = rat[:97] + "..."
                lines.append(
                    f'      [#mono("{tstr(tname)}", size: 6.8pt)], [#text(6.8pt)[{tesc(rat)}]],'
                )
            lines.append("    )")
            lines.append("  ]")
            lines.append("  #v(0.06cm)")
            i = j
        else:
            # Individual card
            for rv in run:
                card_index += 1
                lines.append(emit_finding_card(rv, legend, card_index, len(failing)))
            i = j

    return "\n".join(lines)


def emit_harness_section(h: dict[str, Any]) -> str:
    if h.get("missing"):
        return "\n".join(
            [
                "#pagebreak(weak: true)",
                f'#text(16pt, weight: "bold", fill: coral)[{tesc(h["name"])}]',
                "#v(0.2cm)",
                "#panel(accent: coral)[",
                '  #text(10pt, weight: "bold", fill: coral)[Harness output directory missing]',
                "  #v(0.1cm)",
                "  #text(9.5pt)[The aggregate report references this harness but no results "
                "files were found in the expected subdirectory. This usually means the harness "
                "crashed during setup — inspect the suite stdout for the failure.]",
                "]",
            ]
        )
    # Side-by-side rubric + persistent failures for density
    rubric = emit_rubric_table(h.get("heatmap"))
    persistent = emit_persistent_failures(h)
    if persistent:
        side_by_side = "\n".join(
            [
                "#two-col([",
                rubric,
                "], [",
                persistent,
                "])",
                "#v(0.08cm)",
            ]
        )
    else:
        side_by_side = rubric + "\n#v(0.08cm)"

    parts = [
        emit_harness_header(h),
        side_by_side,
        emit_commit_snapshot(h),
        emit_verdict_matrix(h),
        emit_findings_section(h),
    ]
    return "\n".join(p for p in parts if p)


def emit_chain_of_custody(loaded: dict[str, Any]) -> str:
    aggregate = loaded["aggregate"]
    harnesses = loaded["harnesses"]

    env = aggregate.get("environment") or {}
    rows: list[str] = []
    total_verdict_hashes = 0
    for h in harnesses:
        vh = h.get("verdict_hashes") or {}
        count = vh.get("count", 0) if isinstance(vh, dict) else 0
        total_verdict_hashes += count
        manifest = h.get("manifest") or {}
        rubric = manifest.get("rubric_categories") or []
        rows.append(
            '  [{name}], [{count}], [{rubric_n}], raw("{core}"), [{when}],'.format(
                name=tesc(h["name"]),
                count=human_int(count),
                rubric_n=human_int(len(rubric)),
                core=tstr(manifest.get("harness_core_version") or "—"),
                when=tesc((manifest.get("run_timestamp_utc") or "")[:19].replace("T", " ")),
            )
        )

    # Grab environment fingerprint from the first harness manifest that has one
    # (aggregate_report.json only stores python/platform; the full env lives in
    # per-harness manifest.json).
    env_fp: dict[str, Any] = {}
    for h in harnesses:
        mf_env = (h.get("manifest") or {}).get("environment") or {}
        if mf_env:
            env_fp = mf_env
            break

    return "\n".join(
        [
            "#pagebreak(weak: true)",
            "== Chain of custody",
            "#text(8pt, fill: slate)[",
            "Every verdict carries an embedded SHA-256 self-hash. "
            '#raw("verdict_hashes.json") indexes these per harness; '
            '#raw("clawbio-bench --verify <results/>") runs a three-layer check.]',
            "#v(0.15cm)",
            "// Two-column: hash table + environment",
            "#two-col([",
            '#show table.cell.where(y: 0): set text(weight: "bold", fill: white, size: 7pt)',
            "#table(",
            "  columns: (1fr, auto, auto, auto, auto),",
            "  align: (left, right, right, left, left),",
            "  inset: 3pt,",
            "  stroke: none,",
            "  fill: (x, y) => if y == 0 { navy } else if calc.odd(y) { pearl } else { white },",
            "  [Harness], [Hashes], [Rubric], [Core], [Timestamp],",
            *rows,
            ")",
            "#v(0.1cm)",
            f"#text(8pt, fill: slate)[Total hashes: *{human_int(total_verdict_hashes)}*]",
            "], [",
            '#microcap("environment")',
            "#v(3pt)",
            f'#kvrow("Python", raw("{tstr(env.get("python") or env_fp.get("python_version") or "—")}"))',
            f'#kvrow("Platform", [{tesc(env.get("platform") or env_fp.get("platform") or "—")}])',
            f'#kvrow("Host hash", raw("{tstr(env_fp.get("hostname_hash") or "—")}"))',
            f'#kvrow("Packages", [{human_int(env_fp.get("package_count") or 0)}])',
            f'#kvrow("Pkg SHA-256", raw("{tstr(short_hash(env_fp.get("package_set_sha256"), 24))}"))',
            f'#kvrow("Target SHA", raw("{tstr(short_hash(aggregate.get("clawbio_commit"), 12))}"))',
            f'#kvrow("Wall time", [{aggregate.get("wall_clock_seconds", 0)} s])',
            "])",
            "",
        ]
    )


def emit_appendix(loaded: dict[str, Any]) -> str:
    """Dense tables: category counts + test-case inventory in flowing columns."""
    harnesses = loaded["harnesses"]
    lines: list[str] = ["#pagebreak(weak: true)", "== Appendix"]

    # Category counts in flowing two columns
    lines.append('#text(10pt, weight: "bold", fill: steel)[Category counts]')
    lines.append("#v(0.08cm)")
    lines.append("#columns(2, gutter: 10pt)[")
    for h in harnesses:
        entry = h["aggregate_entry"]
        cats = entry.get("categories") or {}
        if not cats:
            continue
        lines.append(f'#text(8.5pt, weight: "bold")[{tesc(h["name"])}]')
        lines.append("#v(0.04cm)")
        lines.append("#table(")
        lines.append("  columns: (1fr, auto),")
        lines.append("  align: (left, right),")
        lines.append("  inset: 2pt,")
        lines.append("  stroke: (x, y) => (bottom: 0.1pt + pearl),")
        for cat in sorted(cats.keys()):
            tier = category_tier(cat)
            fill_hex = TIER_DEFS[tier]["fill"]
            lines.append(
                f'  [#box(width: 5pt, height: 5pt, fill: rgb("{fill_hex}"), stroke: none) '
                f'#text(6.5pt, raw("{tstr(cat)}"))], '
                f"[#text(6.5pt)[{human_int(cats[cat])}]],"
            )
        lines.append(")")
        lines.append("#v(0.12cm)")
    lines.append("]")

    lines.append("#v(0.15cm)")
    lines.append('#text(10pt, weight: "bold", fill: steel)[Test case inventory]')
    lines.append("#v(0.08cm)")
    lines.append("#columns(2, gutter: 10pt)[")
    for h in harnesses:
        manifest = h.get("manifest") or {}
        test_cases = manifest.get("test_cases") or []
        if not test_cases:
            continue
        lines.append(f'#text(8.5pt, weight: "bold")[{tesc(h["name"])} ({len(test_cases)})]')
        lines.append("#v(0.04cm)")
        for tc in test_cases:
            name = tc.get("name") if isinstance(tc, dict) else str(tc)
            tc_type = tc.get("type") if isinstance(tc, dict) else "file"
            if isinstance(tc, dict) and tc_type == "directory":
                file_count = len(tc.get("files") or {})
                lines.append(
                    f'#text(6.2pt, font: "DejaVu Sans Mono")[{tesc(name)}] '
                    f"#text(6pt, fill: slate)[{file_count}f]\\"
                )
            else:
                lines.append(f'#text(6.2pt, font: "DejaVu Sans Mono")[{tesc(name)}]\\')
        lines.append("#v(0.1cm)")
    lines.append("]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Delta computation (baseline comparison)
# ---------------------------------------------------------------------------


def _extract_finding_keys(aggregate: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Extract (harness, test, category) → finding dict from an aggregate."""
    keys: dict[str, dict[str, str]] = {}
    for hname, hdata in (aggregate.get("harnesses") or {}).items():
        if not isinstance(hdata, dict):
            continue
        for cf in hdata.get("critical_failures") or []:
            test = cf.get("test") or "?"
            cat = cf.get("category") or "?"
            key = f"{hname}\t{test}\t{cat}"
            keys[key] = {"harness": hname, "test": test, "category": cat}
    return keys


def compute_delta(
    current: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """Compute new/resolved/unchanged findings between current and baseline."""
    cur_keys = _extract_finding_keys(current)
    base_keys = _extract_finding_keys(baseline)
    new_set = set(cur_keys) - set(base_keys)
    resolved_set = set(base_keys) - set(cur_keys)
    unchanged_set = set(cur_keys) & set(base_keys)

    # Per-harness rate deltas
    harness_deltas: dict[str, float] = {}
    for hname in set(list(current.get("harnesses") or {}) + list(baseline.get("harnesses") or {})):
        cur_rate = ((current.get("harnesses") or {}).get(hname) or {}).get("pass_rate", 0)
        base_rate = ((baseline.get("harnesses") or {}).get(hname) or {}).get("pass_rate", 0)
        harness_deltas[hname] = cur_rate - base_rate

    return {
        "new": [cur_keys[k] for k in sorted(new_set)],
        "resolved": [base_keys[k] for k in sorted(resolved_set)],
        "unchanged_count": len(unchanged_set),
        "baseline_commit": short_hash(baseline.get("clawbio_commit"), 8),
        "harness_deltas": harness_deltas,
    }


def emit_delta_section(delta: dict[str, Any]) -> str:
    """Render a delta summary panel for the executive summary page."""
    new_findings = delta["new"]
    resolved = delta["resolved"]
    unchanged = delta["unchanged_count"]
    base_commit = delta.get("baseline_commit") or "?"

    lines: list[str] = [
        f'#text(9.5pt, weight: "bold", fill: steel)[Delta vs. baseline '
        f'#raw("{tstr(base_commit)}")]',
        "#v(0.06cm)",
        "#grid(columns: (1fr, 1fr, 1fr), column-gutter: 6pt,",
    ]
    # New findings cell
    new_color = "coral" if new_findings else "forest"
    lines.append(
        f'  dash-cell("new findings", '
        f'[#text(16pt, weight: "bold", fill: {new_color})[{len(new_findings)}]], '
        f"tint: {new_color}.lighten(92%), sc: {new_color}),"
    )
    # Resolved cell
    res_color = "forest" if resolved else "slate"
    lines.append(
        f'  dash-cell("resolved", '
        f'[#text(16pt, weight: "bold", fill: {res_color})[{len(resolved)}]], '
        f"tint: {res_color}.lighten(92%), sc: {res_color}),"
    )
    # Unchanged cell
    lines.append(
        f'  dash-cell("unchanged", '
        f'[#text(16pt, weight: "bold", fill: slate)[{unchanged}]], '
        f"tint: pearl, sc: slate),"
    )
    lines.append(")")

    # List new findings if any
    if new_findings:
        lines.append("#v(0.06cm)")
        lines.append('#microcap("new findings", color: coral)')
        lines.append("#v(0.03cm)")
        lines.append("#table(columns: (auto, auto, 1fr), inset: 2pt, stroke: none,")
        for f in new_findings[:8]:
            tier = category_tier(f["category"])
            fill_hex = TIER_DEFS[tier]["fill"]
            lines.append(
                f'  [#mono("{tstr(f["harness"][:16])}", size: 6.5pt, color: slate)], '
                f'  hm-cell(rgb("{fill_hex}")), '
                f'  [#mono("{tstr(f["test"])}", size: 6.5pt)], '
            )
        if len(new_findings) > 8:
            lines.append(f"  [], [], [#text(6pt, fill: slate)[+{len(new_findings) - 8} more]],")
        lines.append(")")

    # List resolved findings if any
    if resolved:
        lines.append("#v(0.06cm)")
        lines.append('#microcap("resolved", color: forest)')
        lines.append("#v(0.03cm)")
        lines.append("#table(columns: (auto, auto, 1fr), inset: 2pt, stroke: none,")
        for f in resolved[:5]:
            lines.append(
                f'  [#mono("{tstr(f["harness"][:16])}", size: 6.5pt, color: slate)], '
                f"  [#text(6pt, fill: forest)[\\u{{2713}}]], "
                f'  [#mono("{tstr(f["test"])}", size: 6.5pt)], '
            )
        if len(resolved) > 5:
            lines.append(f"  [], [], [#text(6pt, fill: slate)[+{len(resolved) - 5} more]],")
        lines.append(")")

    lines.append("#v(0.1cm)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level assembly
# ---------------------------------------------------------------------------


def build_typst(
    loaded: dict[str, Any],
    baseline_aggregate: dict[str, Any] | None = None,
) -> str:
    aggregate = loaded["aggregate"]
    harnesses = loaded["harnesses"]

    title = "ClawBio Benchmark Audit Report"
    subtitle = "Safety, correctness, and honesty findings"
    mode = aggregate.get("mode") or "unknown"
    date_str = aggregate.get("date") or datetime.now(UTC).strftime("%Y-%m-%d")
    commit_short = short_hash(aggregate.get("clawbio_commit"), 8) or "—"
    suite_version = aggregate.get("benchmark_suite_version") or "—"
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    overall = aggregate.get("overall") or {}

    # Audit target: prefer manifest.repo_path basename when available; fall
    # back to generic "ClawBio" otherwise.
    audit_target = "ClawBio"
    for h in harnesses:
        mf = h.get("manifest") or {}
        repo_path = mf.get("repo_path")
        if repo_path:
            audit_target = Path(str(repo_path)).name or audit_target
            break

    parts: list[str] = [
        emit_preamble(title, subtitle),
        emit_cover(
            title=title,
            subtitle=subtitle,
            audit_target=audit_target,
            commit_short=commit_short,
            mode=mode,
            date_str=date_str,
            suite_version=suite_version,
            generated_at=generated_at,
            overall=overall,
        ),
        emit_executive_summary(harnesses, aggregate),
    ]
    # Delta section if baseline provided
    if baseline_aggregate is not None:
        delta = compute_delta(aggregate, baseline_aggregate)
        parts.append(emit_delta_section(delta))
    for h in harnesses:
        parts.append(emit_harness_section(h))
    parts.append(emit_chain_of_custody(loaded))
    parts.append(emit_appendix(loaded))

    # Footer marker
    parts.append("#v(0.3cm)")
    parts.append(
        '#align(center)[#text(7.5pt, fill: slate, style: "italic")'
        "[Generated by clawbio-bench report generator #sym.dot.c "
        f"{tesc(generated_at)}]]"
    )

    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Typst compilation
# ---------------------------------------------------------------------------


def compile_typst(typ_path: Path, pdf_path: Path) -> None:
    """Compile a Typst source file to PDF using the ``typst`` CLI.

    Raises RuntimeError if typst is not on PATH or the compile fails. The
    error message includes the first ~40 lines of typst stderr so source
    errors are actionable without re-running.
    """
    typst_bin = shutil.which("typst")
    if not typst_bin:
        raise RuntimeError(
            "typst binary not found on PATH. Install from https://typst.app or via "
            "``brew install typst`` and retry, or pass --typ-only to skip compilation."
        )
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [typst_bin, "compile", str(typ_path), str(pdf_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[:40])
        raise RuntimeError(f"typst compile failed (exit {result.returncode}):\n{tail}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a structured PDF audit report from a clawbio-bench results directory."
        ),
    )
    parser.add_argument(
        "results_dir",
        type=Path,
        help="Path to results/suite/<timestamp>/ (the directory containing aggregate_report.json)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="PDF output path (default: <results_dir>/report.pdf)",
    )
    parser.add_argument(
        "--typ-output",
        type=Path,
        default=None,
        help="Typst source output path (default: <results_dir>/report.typ)",
    )
    parser.add_argument(
        "--typ-only",
        action="store_true",
        help="Write the .typ source only; skip PDF compilation",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Baseline results directory or aggregate_report.json for delta comparison",
    )
    args = parser.parse_args()

    try:
        loaded = load_results(args.results_dir)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    # Load baseline if provided
    baseline_agg: dict[str, Any] | None = None
    if args.baseline is not None:
        try:
            baseline_path = args.baseline.resolve()
            if baseline_path.is_dir():
                bp = baseline_path / "aggregate_report.json"
            else:
                bp = baseline_path
            if bp.exists():
                baseline_agg = _load_json(bp)
                print(f"  Baseline loaded: {bp}")
            else:
                print(f"  WARNING: baseline not found at {bp}", file=sys.stderr)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  WARNING: baseline unavailable ({exc})", file=sys.stderr)

    results_dir: Path = loaded["results_dir"]
    typ_path = args.typ_output or (results_dir / "report.typ")
    pdf_path = args.output or (results_dir / "report.pdf")

    typst_source = build_typst(loaded, baseline_aggregate=baseline_agg)
    typ_path.parent.mkdir(parents=True, exist_ok=True)
    typ_path.write_text(typst_source, encoding="utf-8")
    print(f"  Typst source written: {typ_path}")

    if args.typ_only:
        return 0

    try:
        compile_typst(typ_path, pdf_path)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"  PDF report written:   {pdf_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
