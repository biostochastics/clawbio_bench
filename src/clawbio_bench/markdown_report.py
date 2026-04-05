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


def _summary_table(aggregate: dict[str, Any]) -> list[str]:
    """Per-harness pass/fail table with a totals row. Harnesses sorted by name."""
    lines = [
        "| Harness | Pass | Fail | Harness errors | Rate |",
        "|---|---:|---:|---:|---:|",
    ]
    harnesses = aggregate.get("harnesses") or {}
    for name in sorted(harnesses.keys()):
        data = harnesses[name] or {}
        evaluated = data.get("evaluated", 0)
        passed = data.get("pass_count", 0)
        failed = data.get("fail_count", 0)
        herr = data.get("harness_errors", 0)
        rate = _fmt_pct(data.get("pass_rate", 0.0))
        lines.append(f"| `{name}` | {passed}/{evaluated} | {failed} | {herr} | {rate} |")

    overall = aggregate.get("overall") or {}
    total_pass = overall.get("total_pass", 0)
    total_eval = overall.get("total_evaluated", 0)
    total_fail = total_eval - total_pass
    total_herr = overall.get("total_harness_errors", 0)
    total_rate = _fmt_pct(overall.get("total_pass_rate", 0.0))
    lines.append(
        f"| **total** | **{total_pass}/{total_eval}** | **{total_fail}** | "
        f"**{total_herr}** | **{total_rate}** |"
    )
    return lines


def _render_finding(f: dict[str, str]) -> str:
    rationale = _sanitize_rationale(f.get("rationale", ""))
    suffix = f" — {rationale}" if rationale else ""
    return f"- **`{f['harness']}` / `{f['test']}`** — `{f['category']}`{suffix}"


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


def render_markdown_report(
    results_dir: Path,
    baseline: Path | None = None,
    artifact_url: str | None = None,
    repo_url: str = "https://github.com/biostochastics/clawbio_bench",
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

    current_keys = {_finding_key(f): f for f in current_findings}
    baseline_keys = {_finding_key(f): f for f in baseline_findings}

    new_keys = sorted(set(current_keys) - set(baseline_keys))
    resolved_keys = sorted(set(baseline_keys) - set(current_keys))
    unchanged_keys = sorted(set(current_keys) & set(baseline_keys))

    new_findings = [current_keys[k] for k in new_keys]
    resolved_findings = [baseline_keys[k] for k in resolved_keys]
    unchanged_findings = [current_keys[k] for k in unchanged_keys]

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
    lines.append("## clawbio-bench audit")
    lines.append("")
    lines.append(
        f"**Status:** {status} · **Commit:** `{commit}` · **Mode:** `{mode}` · "
        f"**Runtime:** {wall}s · **Date:** {date}"
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
    lines.extend(_summary_table(current))
    lines.append("")

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
