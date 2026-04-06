#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Markdown report renderer for clawbio-bench results.

Produces a PR-friendly summary of an ``aggregate_report.json`` suitable for
posting as a sticky pull-request comment. Optionally diffs against a baseline
aggregate report (e.g. the last green run from ``main``) to surface *new* and
*resolved* findings rather than absolute counts.

The renderer is intentionally standalone (stdlib only) so it can be invoked in
CI without pulling ``matplotlib`` or other optional dependencies.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path
from typing import Any

# Embedded in the rendered output so PR bots can find and update the existing
# comment instead of spamming a new one on every push.
STICKY_MARKER = "<!-- clawbio-bench-report -->"

# Cap the rendered "Unchanged findings" list to keep comments under GitHub's
# 65,536-char limit. New and resolved lists are shown in full (actionable);
# unchanged ones are truncated with a "(+N more)" indicator.
UNCHANGED_FINDINGS_CAP = 20

# Rationale text is included verbatim in a markdown bullet. Truncate to keep
# bullets one line long; newlines/tabs are stripped regardless.
MAX_RATIONALE_CHARS = 200


# ---------------------------------------------------------------------------
# Aggregate loading
# ---------------------------------------------------------------------------


def _load_aggregate(source: Path) -> dict[str, Any]:
    """Load an aggregate_report.json either from the file itself or its parent dir.

    Raises ``FileNotFoundError`` if no file is present and ``ValueError`` if
    the file exists but is not a valid JSON object. Callers that want to fall
    back gracefully on a corrupt baseline catch both.
    """
    source = source.resolve()
    candidate = source / "aggregate_report.json" if source.is_dir() else source
    if not candidate.exists():
        raise FileNotFoundError(f"aggregate_report.json not found at {candidate}")
    with open(candidate, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in aggregate report {candidate}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Aggregate report {candidate} is not a JSON object")
    return data


# ---------------------------------------------------------------------------
# Finding extraction
# ---------------------------------------------------------------------------


def _extract_findings(aggregate: dict[str, Any]) -> list[dict[str, str]]:
    """Return a flat, deterministic list of findings across all harnesses.

    A finding is any entry in a harness's ``critical_failures`` list — i.e. a
    test case whose verdict category is in ``FAIL_CATEGORIES``. The caller
    controls whether the aggregate report contains the complete list or just
    the first N (we render whatever is present).

    Harnesses are iterated in sorted order so render output is stable
    regardless of JSON key order in the source.
    """
    findings: list[dict[str, str]] = []
    harnesses = aggregate.get("harnesses") or {}
    for harness_name in sorted(harnesses.keys()):
        harness_data = harnesses[harness_name] or {}
        for cf in harness_data.get("critical_failures") or []:
            findings.append(
                {
                    "harness": harness_name,
                    "test": cf.get("test") or "unknown",
                    "category": cf.get("category") or "unknown",
                    "rationale": (cf.get("rationale") or "").strip(),
                }
            )
    findings.sort(key=lambda f: (f["harness"], f["test"], f["category"]))
    return findings


def _finding_key(f: dict[str, str]) -> str:
    """Stable identity for a finding across runs.

    A change in *category* at the same (harness, test) counts as a
    resolve-and-new pair, which is the behaviour we want: flipping from
    ``fst_incorrect`` to ``fst_mislabeled`` is a meaningful transition.
    """
    return f"{f['harness']}\t{f['test']}\t{f['category']}"


def _is_multi_commit(aggregate: dict[str, Any]) -> bool:
    """Heuristic: was this aggregate produced by a non-smoke (multi-commit) run?

    We check the ``mode`` field written by ``cli.main()`` — ``smoke`` is the
    only mode safe for markdown diffing, because ``(harness, test, category)``
    identity collapses duplicates across commits.
    """
    mode = str(aggregate.get("mode") or "").lower()
    return bool(mode) and mode != "smoke"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_pct(n: float) -> str:
    return f"{n:.1f}%"


def _sanitize_rationale(raw: str) -> str:
    """Flatten and escape rationale text for safe embedding in a markdown bullet.

    Rationales can contain tool stderr, stack traces, and arbitrary paths
    originating from the audited repo. We:

    * Collapse all whitespace (newlines, tabs) into single spaces so a
      rationale cannot break the enclosing ``- ...`` bullet structure.
    * Truncate to ``MAX_RATIONALE_CHARS`` to keep each finding to roughly
      one rendered line and to bound the overall comment size.
    * HTML-escape so stray tags (e.g. a literal ``</details>``) cannot
      mangle the surrounding ``<details>`` blocks in GitHub's renderer.
    """
    if not raw:
        return ""
    collapsed = " ".join(raw.split())
    if len(collapsed) > MAX_RATIONALE_CHARS:
        collapsed = collapsed[: MAX_RATIONALE_CHARS - 3] + "..."
    return html.escape(collapsed, quote=False)


def _summary_table(
    aggregate: dict[str, Any],
    harness_filter: str | None = None,
) -> list[str]:
    """Per-harness pass/fail table with status indicator and totals row."""
    lines = [
        "| Harness | Status | Pass | Fail | Errors | Rate |",
        "|---|:---:|---:|---:|---:|---:|",
    ]
    harnesses = aggregate.get("harnesses") or {}
    for name in sorted(harnesses.keys()):
        if harness_filter and name != harness_filter:
            continue
        data = harnesses[name] or {}
        evaluated = data.get("evaluated", 0)
        passed = data.get("pass_count", 0)
        failed = data.get("fail_count", 0)
        herr = data.get("harness_errors", 0)
        rate = _fmt_pct(data.get("pass_rate", 0.0))
        is_pass = bool(data.get("pass"))
        status = "\u2705" if is_pass else "\u274c"
        esc_name = html.escape(name, quote=False)
        lines.append(
            f"| `{esc_name}` | {status} | {passed}/{evaluated} | {failed} | {herr} | {rate} |"
        )

    if not harness_filter:
        overall = aggregate.get("overall") or {}
        total_pass = overall.get("total_pass", 0)
        total_eval = overall.get("total_evaluated", 0)
        total_fail = total_eval - total_pass
        total_herr = overall.get("total_harness_errors", 0)
        total_rate = _fmt_pct(overall.get("total_pass_rate", 0.0))
        all_pass = bool(overall.get("pass"))
        total_status = "\u2705" if all_pass else "\u274c"
        lines.append(
            f"| **total** | {total_status} | **{total_pass}/{total_eval}** | "
            f"**{total_fail}** | **{total_herr}** | **{total_rate}** |"
        )
    return lines


def _render_finding(f: dict[str, str]) -> str:
    rationale = _sanitize_rationale(f.get("rationale", ""))
    suffix = f" — {rationale}" if rationale else ""
    h = html.escape(f["harness"], quote=False)
    t = html.escape(f["test"], quote=False)
    c = html.escape(f["category"], quote=False)
    return f"- **`{h}` / `{t}`** — `{c}`{suffix}"


def _details_block(
    title: str,
    findings: list[dict[str, str]],
    open_: bool = False,
    cap: int | None = None,
) -> list[str]:
    """Render a ``<details>`` section.

    If ``cap`` is set and the list exceeds it, only the first ``cap`` entries
    are rendered followed by a ``(+N more; see full artifact)`` footer. The
    summary count reflects the *total* number of findings, not the displayed
    subset.
    """
    if not findings:
        return []
    open_attr = " open" if open_ else ""
    out = [f"<details{open_attr}><summary>{title} ({len(findings)})</summary>", ""]
    shown = findings if cap is None or len(findings) <= cap else findings[:cap]
    out.extend(_render_finding(f) for f in shown)
    if cap is not None and len(findings) > cap:
        remaining = len(findings) - cap
        out.append(f"- _+{remaining} more — see the full verdicts artifact_")
    out.append("")
    out.append("</details>")
    return out


def _category_breakdown(aggregate: dict[str, Any], harness_filter: str | None = None) -> list[str]:
    """Per-harness category breakdown tables."""
    lines: list[str] = []
    harnesses = aggregate.get("harnesses") or {}
    for name in sorted(harnesses.keys()):
        if harness_filter and name != harness_filter:
            continue
        data = harnesses[name] or {}
        cats = data.get("categories") or {}
        if not cats:
            continue
        lines.append(f"**`{name}`** category breakdown:")
        lines.append("")
        lines.append("| Category | Count |")
        lines.append("|---|---:|")
        for cat in sorted(cats.keys()):
            lines.append(f"| `{cat}` | {cats[cat]} |")
        lines.append("")
    return lines


def _build_severity_map(aggregate: dict[str, Any]) -> dict[str, int]:
    """Derive severity tiers from the aggregate's pass/fail category lists.

    Tier 0 = FAIL_CATEGORIES (red — correctness/safety failures)
    Tier 1 = any non-pass, non-fail category that isn't harness_error (orange — warnings)
    Tier 2 = harness_error (infrastructure)
    Tier 3 = unknown/unclassified

    This replaces a hardcoded category-to-tier dict, ensuring new harnesses
    and categories sort correctly without code changes.
    """
    severity: dict[str, int] = {"harness_error": 2}
    harnesses = aggregate.get("harnesses") or {}
    for harness_data in harnesses.values():
        if not isinstance(harness_data, dict):
            continue
        fail_cats = set(harness_data.get("fail_categories") or [])
        pass_cats = set(harness_data.get("pass_categories") or [])
        for cat in fail_cats:
            severity.setdefault(cat, 0)
        # Non-pass, non-fail categories that appear in the results are
        # warnings (tier 1). We derive them from the actual category counts.
        for cat in harness_data.get("categories") or {}:
            if cat not in fail_cats and cat not in pass_cats and cat != "harness_error":
                severity.setdefault(cat, 1)
    return severity


# Severity tier indicator emojis for PR comments (GitHub renders these)
_SEVERITY_EMOJI = {
    0: "\U0001f534",  # red circle — critical
    1: "\U0001f7e0",  # orange circle — warning
    2: "\u26aa",  # white circle — infra
    3: "\u2753",  # question mark — unknown
}


def _severity_key(
    f: dict[str, str],
    severity_map: dict[str, int],
) -> tuple[int, str, str]:
    return (
        severity_map.get(f.get("category", ""), 3),
        f.get("harness", ""),
        f.get("test", ""),
    )


def _render_detailed_finding(
    f: dict[str, str],
    index: int,
    severity_map: dict[str, int] | None = None,
) -> str:
    """Render a single finding with clinical context and severity indicator."""
    h = html.escape(f["harness"], quote=False)
    t = html.escape(f["test"], quote=False)
    c = html.escape(f["category"], quote=False)
    tier = (severity_map or {}).get(f.get("category", ""), 3)
    emoji = _SEVERITY_EMOJI.get(tier, "")
    parts = [f"{emoji} **{index}. `{h}` / `{t}`** — `{c}`"]
    rationale = _sanitize_rationale(f.get("rationale", ""))
    if rationale:
        parts.append(f"  - **Rationale:** {rationale}")
    # Surface ground-truth context if available
    finding = f.get("finding", "")
    if finding:
        parts.append(f"  - **Finding:** {html.escape(finding, quote=False)}")
    hazard_metric = f.get("hazard_metric", "")
    if hazard_metric:
        parts.append(f"  - **Hazard metric:** {_sanitize_rationale(hazard_metric)}")
    derivation = f.get("derivation", "")
    if derivation:
        parts.append(f"  - **Derivation:** {_sanitize_rationale(derivation)}")
    finding_category = f.get("finding_category", "")
    if finding_category:
        parts.append(f"  - **Finding category:** `{html.escape(finding_category, quote=False)}`")
    # Legacy clinical fields (pharmgx, equity harnesses)
    hazard_drug = f.get("hazard_drug", "")
    hazard_class = f.get("hazard_class", "")
    if hazard_drug:
        drug_line = f"  - **Hazard drug:** {html.escape(hazard_drug, quote=False)}"
        if hazard_class:
            drug_line += f" ({html.escape(hazard_class, quote=False)})"
        parts.append(drug_line)
    target_gene = f.get("target_gene", "")
    if target_gene:
        parts.append(f"  - **Target gene:** {html.escape(target_gene, quote=False)}")
    return "\n".join(parts)


def _extract_detailed_findings(
    aggregate: dict[str, Any],
    harness_filter: str | None = None,
    results_dir: Path | None = None,
) -> list[dict[str, str]]:
    """Extract findings with clinical ground-truth metadata from verdicts.

    When ``results_dir`` is available, loads ``all_verdicts.json`` per harness
    to pull ground-truth fields (FINDING, HAZARD_DRUG, TARGET_GENE, etc.)
    into each finding entry. Falls back to the aggregate
    ``critical_failures`` list when per-verdict data is not available.
    """
    # Build a lookup from all_verdicts.json if the results directory exists.
    # Keyed by (harness_name, test_name) → ground_truth dict.
    # Skip enrichment for multi-commit runs: the same (hname, tname) pair can
    # appear at multiple commits and the last one would win silently — the MD
    # renderer already warns about multi-commit mode being unreliable for diffs.
    gt_lookup: dict[tuple[str, str], dict[str, str]] = {}
    is_multi = _is_multi_commit(aggregate)
    if results_dir is not None and not is_multi:
        results_dir = results_dir.resolve()
        harnesses = aggregate.get("harnesses") or {}
        for hname in harnesses:
            if harness_filter and hname != harness_filter:
                continue
            verdicts_path = results_dir / hname / "all_verdicts.json"
            if not verdicts_path.exists():
                continue
            try:
                with open(verdicts_path, encoding="utf-8") as f:
                    vlist = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            for v in vlist:
                tname = v.get("test_case", {}).get("name", "")
                gt = v.get("ground_truth") or {}
                if tname:
                    gt_lookup[(hname, tname)] = gt

    findings: list[dict[str, str]] = []
    harnesses = aggregate.get("harnesses") or {}
    for harness_name in sorted(harnesses.keys()):
        if harness_filter and harness_name != harness_filter:
            continue
        harness_data = harnesses[harness_name] or {}
        for cf in harness_data.get("critical_failures") or []:
            test_name = cf.get("test") or "unknown"
            entry: dict[str, str] = {
                "harness": harness_name,
                "test": test_name,
                "category": cf.get("category") or "unknown",
                "rationale": (cf.get("rationale") or "").strip(),
            }
            # Enrich from ground truth if available
            gt = gt_lookup.get((harness_name, test_name)) or {}
            gt_map = {
                "finding": "FINDING",
                "hazard_metric": "HAZARD_METRIC",
                "derivation": "DERIVATION",
                "finding_category": "FINDING_CATEGORY",
                # Legacy keys for harnesses using the clinical schema
                "hazard_drug": "HAZARD_DRUG",
                "hazard_class": "HAZARD_CLASS",
                "target_gene": "TARGET_GENE",
            }
            for out_key, gt_key in gt_map.items():
                val = gt.get(gt_key) or cf.get(out_key)
                if val:
                    entry[out_key] = str(val)
            findings.append(entry)
    severity_map = _build_severity_map(aggregate)
    findings.sort(key=lambda f: _severity_key(f, severity_map))
    return findings


def _detailed_findings_block(
    findings: list[dict[str, str]],
    title: str = "Detailed findings",
    open_: bool = True,
    cap: int | None = None,
    severity_map: dict[str, int] | None = None,
) -> list[str]:
    """Render a ``<details>`` section with per-finding clinical context."""
    if not findings:
        return []
    open_attr = " open" if open_ else ""
    out = [f"<details{open_attr}><summary>{title} ({len(findings)})</summary>", ""]
    shown = findings if cap is None or len(findings) <= cap else findings[:cap]
    for i, f in enumerate(shown, 1):
        out.append(_render_detailed_finding(f, i, severity_map=severity_map))
        out.append("")
    if cap is not None and len(findings) > cap:
        remaining = len(findings) - cap
        out.append(f"_+{remaining} more — see the full verdicts artifact_")
        out.append("")
    out.append("</details>")
    return out


def render_markdown_report(
    results_dir: Path,
    baseline: Path | None = None,
    artifact_url: str | None = None,
    repo_url: str = "https://github.com/biostochastics/clawbio_bench",
    harness_filter: str | None = None,
) -> str:
    """Render a PR-comment-ready markdown report.

    Args:
        results_dir: Path to either a results directory containing
            ``aggregate_report.json`` or the report file itself.
        baseline: Optional path to a baseline ``aggregate_report.json`` to
            diff against. If omitted, only the current state is rendered.
        artifact_url: Optional URL to the full verdicts artifact (e.g. the
            GitHub Actions artifact URL). Rendered in the footer.
        repo_url: Link back to the benchmark repository.
        harness_filter: If set, render only the named harness (by its
            benchmark name, e.g. ``"pharmgx-reporter"``).

    Returns:
        A single markdown string. The first line is the sticky marker comment
        so CI bots can find and update an existing PR comment in place.
    """
    current = _load_aggregate(results_dir)
    baseline_data: dict[str, Any] | None = None
    if baseline is not None:
        try:
            baseline_data = _load_aggregate(baseline)
        except (FileNotFoundError, ValueError, OSError) as exc:
            # A corrupt or missing baseline is expected in CI (stale release
            # asset, intermittent download, HTML error page served instead of
            # JSON). Fall back to no-baseline rendering and log so the job
            # output makes the situation obvious.
            print(
                f"[markdown_report] baseline unavailable ({exc}); rendering absolute findings",
                file=sys.stderr,
            )
            baseline_data = None

    current_findings = _extract_findings(current)
    baseline_findings = _extract_findings(baseline_data) if baseline_data else []
    # Detailed findings with clinical context, severity-sorted.
    # Pass results_dir so ground-truth metadata can be loaded from verdicts.
    source_dir = results_dir.resolve() if results_dir.is_dir() else results_dir.parent.resolve()
    detailed_findings = _extract_detailed_findings(current, harness_filter, results_dir=source_dir)

    # Apply harness_filter to the diff sets too
    if harness_filter:
        current_findings = [f for f in current_findings if f["harness"] == harness_filter]
        baseline_findings = [f for f in baseline_findings if f["harness"] == harness_filter]

    current_keys = {_finding_key(f): f for f in current_findings}
    baseline_keys = {_finding_key(f): f for f in baseline_findings}

    new_keys = sorted(set(current_keys) - set(baseline_keys))
    resolved_keys = sorted(set(baseline_keys) - set(current_keys))
    unchanged_keys = sorted(set(current_keys) & set(baseline_keys))

    new_findings = [current_keys[k] for k in new_keys]
    resolved_findings = [baseline_keys[k] for k in resolved_keys]
    unchanged_findings = [current_keys[k] for k in unchanged_keys]

    # When harness_filter is active, derive status from the filtered harness
    # instead of the aggregate-level overall (which covers all harnesses).
    if harness_filter:
        h_data = (current.get("harnesses") or {}).get(harness_filter) or {}
        overall_pass = bool(h_data.get("pass"))
        total_harness_errors = int(h_data.get("harness_errors") or 0)
    else:
        overall = current.get("overall") or {}
        overall_pass = bool(overall.get("pass"))
        total_harness_errors = int(overall.get("total_harness_errors") or 0)
    # Three distinct status states matter in a PR comment:
    #   PASS             — everything green
    #   FINDINGS         — expected advisory state (exit 1)
    #   HARNESS ERRORS   — benchmark infrastructure broke (exit 2), actionable
    if overall_pass:
        status = "PASS"
    elif total_harness_errors > 0:
        status = "FINDINGS + HARNESS ERRORS"
    else:
        status = "FINDINGS"

    commit = current.get("clawbio_commit", "unknown")
    mode = current.get("mode", "unknown")
    wall = current.get("wall_clock_seconds", 0)
    date = current.get("date", "")
    suite_version = current.get("benchmark_suite_version", "?")
    multi_commit = _is_multi_commit(current)

    # ---- assemble ----
    lines: list[str] = [STICKY_MARKER, ""]
    title = "clawbio-bench audit"
    if harness_filter:
        title += f" — `{harness_filter}`"
    lines.append(f"## {title}")
    lines.append("")
    lines.append(
        f"**Status:** {status} · **Commit:** `{commit}` · **Mode:** `{mode}` · "
        f"**Version:** `{suite_version}` · **Runtime:** {wall}s · **Date:** {date}"
    )
    if total_harness_errors > 0:
        lines.append("")
        lines.append(
            f"> **Harness errors:** {total_harness_errors} test case(s) raised an "
            "infrastructure error — the benchmark runner itself needs attention. "
            "See the verdicts artifact for tracebacks."
        )
    if multi_commit:
        lines.append("")
        lines.append(
            f"> **Note:** this report was rendered from a `{mode}` run (multi-commit). "
            "The markdown renderer is designed for single-commit `--smoke` runs; "
            "findings appearing at the same test across multiple commits are "
            "collapsed by `(harness, test, category)` identity and diff counts "
            "may be misleading."
        )
    lines.append("")

    lines.append("### Summary")
    lines.append("")
    lines.extend(_summary_table(current, harness_filter))
    lines.append("")

    # Per-harness category breakdown
    cat_lines = _category_breakdown(current, harness_filter)
    if cat_lines:
        lines.append("### Category breakdown")
        lines.append("")
        lines.extend(cat_lines)

    if baseline_data is not None:
        baseline_commit = baseline_data.get("clawbio_commit", "unknown")
        lines.append(f"### vs. baseline (`{baseline_commit}`)")
        lines.append("")
        lines.append(f"- **New findings:** {len(new_findings)}")
        lines.append(f"- **Resolved findings:** {len(resolved_findings)}")
        lines.append(f"- **Unchanged findings:** {len(unchanged_findings)}")
        lines.append("")

        lines.extend(_details_block("New findings", new_findings, open_=bool(new_findings)))
        if new_findings:
            lines.append("")
        lines.extend(_details_block("Resolved findings", resolved_findings))
        if resolved_findings:
            lines.append("")
        # Unchanged findings are capped to keep the comment under GitHub's
        # 65,536-char limit. The full list remains in the verdicts artifact.
        lines.extend(
            _details_block(
                "Unchanged findings",
                unchanged_findings,
                cap=UNCHANGED_FINDINGS_CAP,
            )
        )
        if unchanged_findings:
            lines.append("")
    else:
        lines.append("### Findings")
        lines.append("")
        if current_findings:
            lines.append(
                f"_No baseline provided — showing all {len(current_findings)} current findings._"
            )
            lines.append("")
            lines.extend(_details_block("Current findings", current_findings, open_=True))
            lines.append("")
        else:
            lines.append("_No findings at this commit._")
            lines.append("")

    # Detailed per-test breakdown with clinical context (severity-sorted).
    # Cap to the same limit as unchanged findings to respect GitHub's comment
    # size limit when baseline diffing produces large finding lists.
    sev_map = _build_severity_map(current)
    if detailed_findings:
        lines.append("### Per-test breakdown")
        lines.append("")
        lines.extend(
            _detailed_findings_block(
                detailed_findings,
                "Detailed findings",
                open_=True,
                cap=UNCHANGED_FINDINGS_CAP if baseline_data else None,
                severity_map=sev_map,
            )
        )
        lines.append("")
        # Scope disclosure: this section only covers FAIL-tier categories
        # (critical_failures). Warning-tier verdicts appear in the full PDF
        # audit report but are omitted here to keep PR comments focused.
        lines.append(
            "_This breakdown covers critical findings only. "
            "Warning-tier verdicts and full ground-truth narratives are "
            "available in the PDF audit report._"
        )
        lines.append("")

    # Footer
    lines.append("---")
    footer_bits = [
        f"clawbio-bench v{suite_version}",
    ]
    if artifact_url:
        footer_bits.append(f"[full verdicts artifact]({artifact_url})")
    footer_bits.append(f"[source]({repo_url})")
    lines.append(f"<sub>{' · '.join(footer_bits)} · chain of custody: SHA-256 per file</sub>")

    return "\n".join(lines).rstrip() + "\n"
