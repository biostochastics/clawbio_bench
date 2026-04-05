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
    "navy": "#0f2440",
    "steel": "#2d4a6f",
    "sky": "#5b8dbf",
    "coral": "#d94f4f",
    "ember": "#cf7a30",
    "forest": "#3a8a5c",
    "slate": "#64748b",
    "pearl": "#f1f5f9",
    "snow": "#fafbfc",
    "ink": "#1e293b",
    "code_bg": "#1a1b2e",
    "code_fg": "#c9d1d9",
}

# Fallback colors for categories that somehow lack a legend entry (should
# not happen, but keeps the report from blowing up on malformed input).
CATEGORY_FALLBACK_COLOR = {
    "harness_error": "#64748b",
}
DEFAULT_UNKNOWN_COLOR = "#94a3b8"

# Cap on verdict.details dict entries rendered per finding — large SuSiE
# alpha / mu dumps would blow the page otherwise. The finding card notes
# how many were truncated.
MAX_DETAIL_ENTRIES = 8
MAX_DETAIL_VALUE_CHARS = 180


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


def category_color(category: str, legend: dict[str, dict[str, str]]) -> str:
    entry = legend.get(category)
    if entry and isinstance(entry, dict):
        color = entry.get("color")
        if color:
            return str(color)
    return CATEGORY_FALLBACK_COLOR.get(category, DEFAULT_UNKNOWN_COLOR)


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
    """Page setup + color palette + typographic defaults."""
    lines: list[str] = [
        "// clawbio-bench audit report — autogenerated, do not hand-edit.",
        f"// Title: {title}",
        "",
    ]
    for name, hexcolor in PALETTE.items():
        lines.append(typst_rgb_var(name, hexcolor))
    lines += [
        "",
        "#set document(",
        f'  title: "{tstr(title)}",',
        f'  description: "{tstr(subtitle)}",',
        ")",
        "#set page(",
        '  paper: "a4",',
        "  margin: (x: 2.0cm, y: 2.2cm),",
        "  fill: white,",
        '  numbering: "1 / 1",',
        "  footer: context [",
        "    #set text(9pt, fill: slate)",
        "    #line(length: 100%, stroke: 0.3pt + pearl)",
        "    #v(0.15cm)",
        "    #grid(columns: (1fr, auto),",
        f"      [clawbio-bench audit report #sym.dot.c {tesc(title)}],",
        "      [page #counter(page).display() / #counter(page).final().first()],",
        "    )",
        "  ],",
        ")",
        '#set text(font: "Libertinus Serif", size: 10.5pt, fill: ink)',
        "#set par(leading: 0.62em, justify: true)",
        "#set heading(numbering: none)",
        '#show heading.where(level: 1): set text(18pt, weight: "bold", fill: navy)',
        '#show heading.where(level: 2): set text(14pt, weight: "bold", fill: navy)',
        '#show heading.where(level: 3): set text(11.5pt, weight: "bold", fill: steel)',
        '#show raw: set text(font: "DejaVu Sans Mono", size: 9pt)',
        "",
        "// ── tasteful helpers ──",
        '#let eyebrow(txt) = text(9pt, weight: "bold", fill: sky, tracking: 0.1em, upper(txt))',
        "#let rule() = { v(0.15cm); line(length: 100%, stroke: 0.4pt + sky.lighten(50%)); v(0.2cm) }",
        "#let chip(label, color) = box(",
        "  fill: color.lighten(82%),",
        "  stroke: 0.5pt + color,",
        "  inset: (x: 5pt, y: 2pt),",
        "  outset: (y: 1pt),",
        "  radius: 2pt,",
        '  text(8.5pt, weight: "bold", fill: color.darken(20%), label),',
        ")",
        "#let kvrow(k, v) = grid(",
        "  columns: (3.5cm, 1fr), column-gutter: 0.4cm, row-gutter: 0.15cm,",
        '  text(9pt, weight: "bold", fill: slate, k),',
        "  text(9pt, fill: ink, v),",
        ")",
        "#let panel(body, accent: sky) = block(",
        "  fill: pearl, width: 100%, inset: 10pt, radius: 3pt,",
        "  stroke: (left: 2pt + accent), body,",
        ")",
        "#let codeblock(body) = block(",
        "  fill: code_bg, width: 100%, inset: 10pt, radius: 3pt,",
        '  text(fill: code_fg, font: "DejaVu Sans Mono", size: 8.5pt, body),',
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
    overall_pass = overall.get("pass", False)

    status_label = "OVERALL PASS" if overall_pass else "FINDINGS PRESENT"
    status_color = "forest" if overall_pass else "coral"

    return "\n".join(
        [
            "#v(0.6cm)",
            "#eyebrow[Reproducible safety benchmark]",
            "#v(0.4cm)",
            f'#text(26pt, weight: "bold", fill: navy)[{tesc(title)}]',
            "#v(0.1cm)",
            f"#text(13pt, fill: steel)[{tesc(subtitle)}]",
            "#v(0.35cm)",
            "#line(length: 30%, stroke: 1pt + sky)",
            "#v(0.5cm)",
            "#grid(columns: (1fr, 1fr), column-gutter: 0.8cm, row-gutter: 0.25cm,",
            f'  kvrow("Audit target", [{tesc(audit_target)}]),',
            f'  kvrow("HEAD commit", raw("{tstr(commit_short)}")),',
            f'  kvrow("Run mode", [{tesc(mode)}]),',
            f'  kvrow("Run date", [{tesc(date_str)}]),',
            f'  kvrow("Suite version", raw("{tstr(suite_version)}")),',
            f'  kvrow("Report generated", [{tesc(generated_at)}]),',
            ")",
            "#v(0.7cm)",
            f"#panel(accent: {status_color})[",
            f'  #text(10pt, weight: "bold", fill: {status_color}, tracking: 0.1em)[{tesc(status_label)}]',
            "  #v(0.15cm)",
            f'  #text(22pt, weight: "bold", fill: navy)[{human_int(total_pass)} / {human_int(total_eval)} test cases passing #h(0.3cm) #text(16pt, fill: slate)[({pct(pass_rate)})]]',
            "  #v(0.1cm)",
            (
                f"  #text(10pt, fill: slate)[{harness_errors} harness-level error"
                f"{'' if harness_errors == 1 else 's'} · {len(blocking)} blocking harness"
                f"{'' if len(blocking) == 1 else 'es'}]"
            ),
            "]",
            "#v(0.5cm)",
        ]
    )


def emit_executive_summary(harnesses: list[dict[str, Any]]) -> str:
    """Per-harness overview table."""
    rows: list[str] = []
    for h in harnesses:
        entry = h["aggregate_entry"]
        name = h["name"]
        total = entry.get("total_cases", 0)
        evaluated = entry.get("evaluated", 0)
        pass_count = entry.get("pass_count", 0)
        fail_count = entry.get("fail_count", 0)
        harness_errors = entry.get("harness_errors", 0)
        pass_rate = entry.get("pass_rate", 0.0)
        passed = entry.get("pass", False)
        status_chip = "forest" if passed else "coral"
        status_word = "PASS" if passed else "FINDINGS"
        rows.append(
            "  [{name}], [{status}], [{pass_count}/{eval}], [{rate}], "
            "[{fail}], [{errors}], [{total}],".format(
                name=tesc(name),
                status=f'chip("{status_word}", {status_chip})',
                pass_count=human_int(pass_count),
                eval=human_int(evaluated),
                rate=pct(pass_rate),
                fail=human_int(fail_count),
                errors=human_int(harness_errors),
                total=human_int(total),
            )
        )

    return "\n".join(
        [
            "== Executive summary",
            "#rule()",
            '#show table.cell.where(y: 0): set text(weight: "bold", fill: white)',
            "#table(",
            "  columns: (1fr, auto, auto, auto, auto, auto, auto),",
            "  align: (left, center, right, right, right, right, right),",
            "  inset: 7pt,",
            "  stroke: none,",
            "  fill: (x, y) => if y == 0 { navy } else if calc.odd(y) { pearl } else { white },",
            "  [Harness], [Status], [Pass], [Rate], [Fail], [Errors], [Total],",
            *rows,
            ")",
            "",
        ]
    )


def emit_rubric_table(heatmap: dict[str, Any] | None) -> str:
    """Render the category legend as a compact three-column table.

    Falls back to an empty note if the heatmap / legend is unavailable
    (e.g. a harness that crashed before writing heatmap_data.json).
    """
    if not heatmap:
        return (
            '#text(9.5pt, style: "italic", fill: slate)'
            "[No rubric available — harness did not emit heatmap\\_data.json.]\n"
        )
    legend = heatmap.get("category_legend") or {}
    if not legend:
        return (
            '#text(9.5pt, style: "italic", fill: slate)'
            "[Rubric legend missing from heatmap\\_data.json.]\n"
        )

    rows: list[str] = []
    for cat in sorted(legend.keys()):
        entry = legend[cat] or {}
        color_hex = entry.get("color", DEFAULT_UNKNOWN_COLOR)
        label = entry.get("label", cat.replace("_", " "))
        # Swatch is a bare `box(...)` expression — cells in a code-mode
        # `#table(...)` call are expressions, not markup blocks. Wrapping
        # in `[...]` would put the `#xxxxxx` hex string into markup mode,
        # where `#8...` is parsed as a (broken) code expression.
        rows.append(
            "  box(width: 10pt, height: 10pt, radius: 2pt, "
            f'fill: rgb("{color_hex}")), '
            f'raw("{tstr(cat)}"), [{tesc(label)}],'
        )

    return "\n".join(
        [
            '#text(10pt, weight: "bold", fill: steel)[Rubric]',
            "#v(0.15cm)",
            "#table(",
            "  columns: (auto, auto, 1fr),",
            "  align: (center, left, left),",
            "  inset: 5pt,",
            "  stroke: (x, y) => if y == 0 { none } else { (bottom: 0.2pt + pearl) },",
            "  [], [Category], [Remediation hint],",
            *rows,
            ")",
            "",
        ]
    )


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
            "#eyebrow[Harness]",
            f'#text(20pt, weight: "bold", fill: navy)[{tesc(name)}]',
            "#v(0.1cm)",
            "#rule()",
            "#grid(columns: (1fr, 1fr), column-gutter: 0.6cm,",
            "  [",
            '    #text(10pt, weight: "bold", fill: slate)[Pass rate]',
            "    #v(0.1cm)",
            "    #stack(dir: ltr,",
            "      box(width: 5cm, height: 0.5cm, radius: 2pt, fill: pearl)[",
            f"        #place(left + horizon, box(width: {bar_frac * 5:.2f}cm, height: 0.5cm, radius: 2pt, fill: {bar_color}))",
            "      ],",
            "      h(0.25cm),",
            f'      align(horizon, text(12pt, weight: "bold", fill: navy)[{pct(pass_rate)}]),',
            "    )",
            "  ],",
            "  [",
            f'    #kvrow("Total cases", [{human_int(total)}])',
            f'    #kvrow("Evaluated", [{human_int(evaluated)}])',
            f'    #kvrow("Passing", [{human_int(pass_count)}])',
            f'    #kvrow("Findings", [{human_int(fail_count)}])',
            f'    #kvrow("Harness errors", [{human_int(errors)}])',
            "  ],",
            ")",
            "#v(0.3cm)",
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
            '#text(10pt, weight: "bold", fill: steel)[Commits evaluated]',
            "#v(0.15cm)",
            '#show table.cell.where(y: 0): set text(weight: "bold", fill: white, size: 9pt)',
            "#table(",
            "  columns: (auto, auto, 1fr, auto, auto, auto),",
            "  align: (left, left, left, right, right, right),",
            "  inset: 5pt,",
            "  stroke: none,",
            "  fill: (x, y) => if y == 0 { navy } else if calc.odd(y) { pearl } else { white },",
            "  [Commit], [Date], [Message], [Pass], [Rate], [Errors],",
            *rows,
            ")",
            "#v(0.3cm)",
        ]
    )


def emit_verdict_matrix(h: dict[str, Any]) -> str:
    """Compact colored matrix: rows = test cases, columns = commits.

    Only rendered when there is at least one commit × test case cell. For
    single-commit smoke runs this becomes a one-column strip, which is
    still useful as a visual index of per-test categories.
    """
    heatmap = h.get("heatmap") or {}
    commits = heatmap.get("commits") or []
    tests = heatmap.get("test_cases") or []
    matrix = heatmap.get("matrix") or {}
    legend = heatmap.get("category_legend") or {}
    if not commits or not tests:
        return ""

    # Column widths: first column is the test name, then one narrow column per commit.
    col_specs = ["4.5cm"] + ["1fr"] * len(commits)
    col_spec_str = "(" + ", ".join(col_specs) + ")"
    align_tuple = "(left, " + ", ".join(["center"] * len(commits)) + ")"

    # Header row — commit short SHAs. Each cell is a content block ``[...]``
    # so markup (``#text(...)``) is valid inside it.
    header_cells: list[str] = ['[#text(9pt, weight: "bold")[Test]],']
    for c in commits:
        short = c.get("short") or (c.get("sha") or "")[:8]
        header_cells.append(
            f'[#text(8pt, weight: "bold", font: "DejaVu Sans Mono")[{tesc(short)}]],'
        )
    body_rows: list[str] = []
    for tc in tests:
        # First column: test name in a raw() call — code-mode expression so
        # the name goes through a string literal (safe for arbitrary text).
        body_rows.append(f'  raw("{tstr(tc)}"),')
        for c in commits:
            sha = c.get("sha") or ""
            cell = matrix.get(f"{sha}:{tc}") or {}
            category = cell.get("category") or "—"
            color_hex = category_color(category, legend) if category != "—" else PALETTE["pearl"]
            # Bare ``box(...)`` is a code-mode expression that produces
            # content — valid as a table cell. Body is the last positional
            # argument, a ``text(...)`` call.
            body_rows.append(
                "  box("
                f'fill: rgb("{color_hex}").lighten(55%), '
                f'stroke: 0.3pt + rgb("{color_hex}"), '
                "inset: (x: 3pt, y: 2pt), radius: 2pt, "
                f'text(7.5pt, fill: rgb("{color_hex}").darken(25%), '
                f'raw("{tstr(category)}"))),'
            )

    lines: list[str] = [
        '#text(10pt, weight: "bold", fill: steel)[Verdict matrix]',
        "#v(0.15cm)",
        "#table(",
        f"  columns: {col_spec_str},",
        f"  align: {align_tuple},",
        "  inset: 4pt,",
        "  stroke: 0.25pt + pearl,",
    ]
    # Header row indented to match the existing 2-space style.
    lines.append("  " + " ".join(header_cells))
    lines.extend(body_rows)
    lines.append(")")
    lines.append("#v(0.3cm)")
    return "\n".join(lines)


def emit_persistent_failures(h: dict[str, Any]) -> str:
    summary = h.get("summary") or {}
    meta = summary.get("_meta") or {}
    persistent = meta.get("persistent_failures") or []
    always_err = meta.get("always_harness_errored") or []
    if not persistent and not always_err:
        return ""
    parts: list[str] = []
    if persistent:
        parts.append(
            '#text(10pt, weight: "bold", fill: coral)[Persistent failures '
            f"(evaluated, never passed — {len(persistent)})]"
        )
        parts.append("#v(0.1cm)")
        parts.append("#list(")
        for name in persistent:
            parts.append(f'  [raw("{tstr(name)}")],')
        parts.append(")")
        parts.append("#v(0.2cm)")
    if always_err:
        parts.append(
            '#text(10pt, weight: "bold", fill: ember)[Always harness-errored '
            f"(infrastructure broken — {len(always_err)})]"
        )
        parts.append("#v(0.1cm)")
        parts.append("#list(")
        for name in always_err:
            parts.append(f'  [raw("{tstr(name)}")],')
        parts.append(")")
        parts.append("#v(0.2cm)")
    return "\n".join(parts)


def _truncate(value: Any, limit: int = MAX_DETAIL_VALUE_CHARS) -> str:
    """Stringify and truncate a verdict.details value for embedding."""
    s = json.dumps(value, default=str) if isinstance(value, (list, dict)) else str(value)
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
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

    color_hex = category_color(category, legend)
    label = category_label(category, legend)

    # Ground-truth narrative block — pulled from the FINDING / HAZARD_METRIC
    # / DERIVATION fields that harnesses use to describe what the test exists
    # to detect. Not every test has every field; skip empties.
    gt_fields: list[tuple[str, str]] = []
    for key in ("FINDING", "HAZARD_METRIC", "DERIVATION", "FINDING_CATEGORY"):
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

    lines: list[str] = []
    lines.append("#block(width: 100%, breakable: true, inset: 0pt)[")
    lines.append("  #grid(columns: (auto, 1fr, auto), column-gutter: 0.4cm, align: horizon,")
    lines.append(f'    text(10pt, weight: "bold", fill: slate)[Finding #{index}/{total}],')
    lines.append(f'    text(12pt, weight: "bold", fill: navy, raw("{tstr(test_name)}")),')
    lines.append(f'    chip(raw("{tstr(category)}"), rgb("{color_hex}")),')
    lines.append("  )")
    lines.append("  #v(0.1cm)")
    lines.append(
        "  #text(9pt, fill: slate)[commit "
        f'#raw("{tstr(commit_short)}") #sym.dot.c category label: {tesc(label)}'
        + (f" #sym.dot.c verdict SHA {tesc(short_hash(verdict_sha, 12))}" if verdict_sha else "")
        + "]"
    )
    lines.append("  #v(0.2cm)")
    # Rationale
    if rationale:
        lines.append(f'  #panel(accent: rgb("{color_hex}"))[')
        lines.append('    #text(9.5pt, weight: "bold", fill: slate)[Rationale]')
        lines.append("    #v(0.1cm)")
        lines.append(f"    #text(10pt)[{tesc(rationale)}]")
        lines.append("  ]")
        lines.append("  #v(0.2cm)")
    # Ground-truth narrative
    if gt_fields:
        lines.append("  #block(width: 100%, inset: (left: 4pt), stroke: (left: 1.5pt + pearl))[")
        for key, val in gt_fields:
            lines.append(
                f'    #text(8.5pt, weight: "bold", fill: slate, tracking: 0.05em)[{tesc(key)}]'
            )
            lines.append(f"    #text(9.5pt)[ {tesc(val)}]")
            lines.append("    #v(0.1cm)")
        lines.append("  ]")
        lines.append("  #v(0.15cm)")
    # Verdict details — compact key:value block
    if detail_items:
        lines.append("  #codeblock[")
        for k, val in detail_items:
            lines.append(f"    {tesc(k)} = {tesc(_truncate(val))} \\")
        if detail_truncated:
            remaining = len(details) - MAX_DETAIL_ENTRIES
            lines.append(
                f"    ... ({remaining} more detail field{'' if remaining == 1 else 's'} omitted) \\"
            )
        lines.append("  ]")
        lines.append("  #v(0.15cm)")
    # Citations
    if citation_lines:
        lines.append('  #text(8.5pt, style: "italic", fill: slate)[')
        lines.append(f"    Citation — {tesc(' · '.join(citation_lines))}")
        lines.append("  ]")
        lines.append("  #v(0.1cm)")
    lines.append("]")
    lines.append("#v(0.3cm)")
    lines.append("#line(length: 100%, stroke: 0.25pt + pearl)")
    lines.append("#v(0.2cm)")
    return "\n".join(lines)


def emit_findings_section(h: dict[str, Any]) -> str:
    entry = h["aggregate_entry"]
    pass_categories = list(entry.get("pass_categories") or [])
    verdicts = h.get("verdicts") or []
    heatmap = h.get("heatmap") or {}
    legend = heatmap.get("category_legend") or {}

    # Extract and sort: coral (fail) categories before slate (harness_error),
    # then by test name + commit for stability across runs.
    failing = [v for v in verdicts if is_failing(v, pass_categories)]
    failing.sort(
        key=lambda v: (
            v.get("verdict", {}).get("category", "zzz") == "harness_error",
            v.get("test_case", {}).get("name", ""),
            v.get("commit", {}).get("short", ""),
        )
    )

    if not failing:
        return "\n".join(
            [
                '#text(11pt, weight: "bold", fill: forest)[No findings]',
                "#v(0.1cm)",
                "#text(10pt, fill: slate)[Every evaluated test case landed in a pass category for this harness.]",
                "#v(0.4cm)",
            ]
        )

    lines: list[str] = [
        f'#text(12pt, weight: "bold", fill: coral)[Findings ({len(failing)})]',
        "#v(0.15cm)",
        "#text(9.5pt, fill: slate)[Enumerated in full — one card per failing or erroring verdict. "
        "Each card carries the verbatim rationale, the ground-truth hazard metric the test "
        "exists to detect, key verdict detail fields, and the upstream citation where available.]",
        "#v(0.3cm)",
    ]
    for i, v in enumerate(failing, 1):
        lines.append(emit_finding_card(v, legend, i, len(failing)))
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
    parts = [
        emit_harness_header(h),
        emit_rubric_table(h.get("heatmap")),
        "#v(0.2cm)",
        emit_commit_snapshot(h),
        emit_verdict_matrix(h),
        emit_persistent_failures(h),
        "#v(0.2cm)",
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
            "#rule()",
            "#text(10pt, fill: slate)[",
            "  Every verdict written by clawbio-bench carries an embedded SHA-256 self-hash. "
            'The sidecar #raw("verdict_hashes.json") under each harness directory indexes '
            "those hashes independently, and "
            '#raw("clawbio-bench --verify <results/>") runs a three-layer check '
            "(self-hash + sidecar + stdout/stderr log reconciliation).",
            "]",
            "#v(0.3cm)",
            '#show table.cell.where(y: 0): set text(weight: "bold", fill: white, size: 9pt)',
            "#table(",
            "  columns: (1fr, auto, auto, auto, auto),",
            "  align: (left, right, right, left, left),",
            "  inset: 6pt,",
            "  stroke: none,",
            "  fill: (x, y) => if y == 0 { navy } else if calc.odd(y) { pearl } else { white },",
            "  [Harness], [Verdict hashes], [Rubric size], [Core ver], [Run timestamp],",
            *rows,
            ")",
            "#v(0.3cm)",
            f"#text(9.5pt, fill: slate)[Total verdict hashes across all harnesses: "
            f"*{human_int(total_verdict_hashes)}*.]",
            "#v(0.4cm)",
            "=== Environment fingerprint",
            "#v(0.15cm)",
            f'#kvrow("Python", raw("{tstr(env.get("python") or env_fp.get("python_version") or "—")}"))',
            f'#kvrow("Platform", [{tesc(env.get("platform") or env_fp.get("platform") or "—")}])',
            f'#kvrow("Hostname hash", raw("{tstr(env_fp.get("hostname_hash") or "—")}"))',
            f'#kvrow("Installed packages", [{human_int(env_fp.get("package_count") or 0)}])',
            f'#kvrow("Package-set SHA-256", raw("{tstr(short_hash(env_fp.get("package_set_sha256"), 24))}"))',
            f'#kvrow("Audit target SHA", raw("{tstr(aggregate.get("clawbio_commit") or "—")}"))',
            f'#kvrow("Suite wall time", [{aggregate.get("wall_clock_seconds", 0)} s])',
            "",
        ]
    )


def emit_appendix(loaded: dict[str, Any]) -> str:
    """Dense tables: category counts per harness + test-case inventory."""
    harnesses = loaded["harnesses"]
    lines: list[str] = ["#pagebreak(weak: true)", "== Appendix", "#rule()"]

    lines.append('#text(11pt, weight: "bold", fill: steel)[Category counts]')
    lines.append("#v(0.15cm)")
    for h in harnesses:
        entry = h["aggregate_entry"]
        cats = entry.get("categories") or {}
        if not cats:
            continue
        lines.append(f'#text(10pt, weight: "bold")[{tesc(h["name"])}]')
        lines.append("#v(0.1cm)")
        lines.append("#table(")
        lines.append("  columns: (1fr, auto),")
        lines.append("  align: (left, right),")
        lines.append("  inset: 4pt,")
        lines.append("  stroke: (x, y) => (bottom: 0.2pt + pearl),")
        for cat in sorted(cats.keys()):
            lines.append(f'  raw("{tstr(cat)}"), [{human_int(cats[cat])}],')
        lines.append(")")
        lines.append("#v(0.25cm)")

    lines.append("#v(0.3cm)")
    lines.append('#text(11pt, weight: "bold", fill: steel)[Test case inventory]')
    lines.append("#v(0.15cm)")
    for h in harnesses:
        manifest = h.get("manifest") or {}
        test_cases = manifest.get("test_cases") or []
        if not test_cases:
            continue
        lines.append(f'#text(10pt, weight: "bold")[{tesc(h["name"])} ({len(test_cases)} cases)]')
        lines.append("#v(0.1cm)")
        lines.append("#list(")
        for tc in test_cases:
            name = tc.get("name") if isinstance(tc, dict) else str(tc)
            tc_type = tc.get("type") if isinstance(tc, dict) else "file"
            # Model B directories ship multiple files; show name + type + file count
            if isinstance(tc, dict) and tc_type == "directory":
                file_count = len(tc.get("files") or {})
                lines.append(
                    f'  [raw("{tstr(name)}") #sym.dot.c {tesc(tc_type)} '
                    f"#sym.dot.c {file_count} file{'' if file_count == 1 else 's'}],"
                )
            else:
                lines.append(f'  [raw("{tstr(name)}") #sym.dot.c {tesc(tc_type)}],')
        lines.append(")")
        lines.append("#v(0.2cm)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level assembly
# ---------------------------------------------------------------------------


def build_typst(loaded: dict[str, Any]) -> str:
    aggregate = loaded["aggregate"]
    harnesses = loaded["harnesses"]

    title = "ClawBio Benchmark Audit Report"
    subtitle = "Safety, correctness, and honesty findings"
    mode = aggregate.get("mode") or "unknown"
    date_str = aggregate.get("date") or datetime.now(UTC).strftime("%Y-%m-%d")
    commit_short = aggregate.get("clawbio_commit") or "—"
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
        emit_executive_summary(harnesses),
    ]
    for h in harnesses:
        parts.append(emit_harness_section(h))
    parts.append(emit_chain_of_custody(loaded))
    parts.append(emit_appendix(loaded))

    # Footer marker
    parts.append("#v(0.5cm)")
    parts.append(
        '#align(center)[#text(8.5pt, fill: slate, style: "italic")'
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
    args = parser.parse_args()

    try:
        loaded = load_results(args.results_dir)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    results_dir: Path = loaded["results_dir"]
    typ_path = args.typ_output or (results_dir / "report.typ")
    pdf_path = args.output or (results_dir / "report.pdf")

    typst_source = build_typst(loaded)
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
