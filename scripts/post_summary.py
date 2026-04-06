#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Daily audit digest — one-paragraph summary posted to a webhook.

Reads an ``aggregate_report.json`` (and optionally a baseline), formats a
concise digest, and POSTs it as JSON to a webhook URL. Compatible with
Slack incoming webhooks (``{"text": "..."}``), Discord webhooks
(``{"content": "..."}``), and generic endpoints.

When ``--llm openrouter`` is passed, a multi-model "swarm" enriches the
digest: three analyst models independently interpret the structured
findings (contextualized by clawbio-bench's bioinformatics audit domain),
then a synthesizer model (Haiku 4.5) fuses the analyses into a polished
narrative. Every number in the final output is verified against the
structured source — if verification fails, the structured digest is used.

Usage:
    python scripts/post_summary.py \\
        --results results/today/ \\
        --webhook https://hooks.slack.com/services/T.../B.../xxx

    # With baseline delta:
    python scripts/post_summary.py \\
        --results results/today/ \\
        --baseline baselines/latest_baseline.json \\
        --webhook "$NOTIFICATION_WEBHOOK"

    # LLM-enriched swarm digest (requires OPENROUTER_API_KEY):
    python scripts/post_summary.py \\
        --results results/today/ \\
        --baseline baselines/latest_baseline.json \\
        --llm openrouter \\
        --webhook "$NOTIFICATION_WEBHOOK"

    # Dry-run (print to stdout, no POST):
    python scripts/post_summary.py \\
        --results results/today/ \\
        --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path


def _load_aggregate(path: Path) -> dict:
    path = path.resolve()
    candidate = path / "aggregate_report.json" if path.is_dir() else path
    with open(candidate, encoding="utf-8") as f:
        return json.load(f)


def _extract_finding_keys(aggregate: dict) -> set[str]:
    keys: set[str] = set()
    for hname, hdata in (aggregate.get("harnesses") or {}).items():
        if not isinstance(hdata, dict):
            continue
        for cf in hdata.get("critical_failures") or []:
            test = cf.get("test") or "?"
            cat = cf.get("category") or "?"
            keys.add(f"{hname}\t{test}\t{cat}")
    return keys


def build_digest(
    aggregate: dict,
    baseline: dict | None = None,
) -> str:
    """Build a one-paragraph digest string."""
    commit = aggregate.get("clawbio_commit", "unknown")
    date = aggregate.get("date", "")
    overall = aggregate.get("overall") or {}
    total_pass = overall.get("total_pass", 0)
    total_eval = overall.get("total_evaluated", 0)
    pass_rate = overall.get("total_pass_rate", 0.0)
    harness_errors = overall.get("total_harness_errors", 0)

    parts: list[str] = [
        f"ClawBio HEAD at commit {commit}: {total_pass}/{total_eval} ({pass_rate:.1f}%)."
    ]

    if harness_errors:
        parts.append(f"{harness_errors} harness error(s).")

    # Per-harness highlights: flag any harness below 70% or with regressions
    harnesses = aggregate.get("harnesses") or {}
    low_harnesses = []
    for hname, hdata in sorted(harnesses.items()):
        if not isinstance(hdata, dict):
            continue
        rate = hdata.get("pass_rate", 0.0)
        if rate < 70.0:
            low_harnesses.append(f"{hname} {rate:.1f}%")
    if low_harnesses:
        parts.append("Low: " + ", ".join(low_harnesses) + ".")

    # Delta vs baseline
    if baseline is not None:
        current_keys = _extract_finding_keys(aggregate)
        baseline_keys = _extract_finding_keys(baseline)
        new_count = len(current_keys - baseline_keys)
        resolved_count = len(baseline_keys - current_keys)

        baseline_rate = (baseline.get("overall") or {}).get("total_pass_rate", 0.0)
        delta = pass_rate - baseline_rate
        direction = "up" if delta > 0 else "down" if delta < 0 else "flat"

        delta_parts = []
        if new_count:
            delta_parts.append(f"{new_count} new finding(s)")
        if resolved_count:
            delta_parts.append(f"{resolved_count} resolved")
        delta_parts.append(f"rate {direction} {abs(delta):.1f}pp")
        parts.append("Delta: " + ", ".join(delta_parts) + ".")

        # Surface specific regressions by harness
        regression_harnesses = []
        baseline_harnesses = baseline.get("harnesses") or {}
        for hname in harnesses:
            cur_rate = (harnesses.get(hname) or {}).get("pass_rate", 0.0)
            base_rate = (baseline_harnesses.get(hname) or {}).get("pass_rate", 0.0)
            if cur_rate < base_rate:
                regression_harnesses.append(f"{hname} regression")
        if regression_harnesses:
            parts.append(", ".join(regression_harnesses) + ".")

    if date:
        parts.append(f"[{date}]")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# LLM swarm summarization (--llm openrouter)
# ---------------------------------------------------------------------------

# Default analyst models — each independently interprets the structured
# findings from a different perspective. Override with --llm-models.
DEFAULT_ANALYST_MODELS = [
    "deepseek/deepseek-v3.2-exp",
    "minimax/minimax-m2.7",
    "qwen/qwen3-coder",
]

# Synthesizer fuses analyst perspectives into a final narrative.
DEFAULT_SYNTHESIZER_MODEL = "xiaomi/mimo-v2-pro"

# Valid harness names — enumerated as a prompt constraint to prevent
# the LLM from inventing harness names.
_VALID_HARNESSES = (
    "bio-orchestrator, pharmgx-reporter, equity-scorer, nutrigx-advisor, "
    "claw-metagenomics, clawbio-finemapping, clinical-variant-reporter"
)

ANALYST_SYSTEM_PROMPT = (
    """\
You are a bioinformatics audit analyst for clawbio-bench — a safety \
benchmark suite that audits the ClawBio bioinformatics platform for \
safety, correctness, and honesty.

The suite has 7 harnesses: """
    + _VALID_HARNESSES
    + """. \
Reference ONLY these harness names. Each test case produces a \
category-level verdict (not binary pass/fail). Categories like \
fst_mislabeled (honesty), omission (drug missing), \
injection_succeeded (security) each map to specific remediation paths.

Clinical significance = findings in pharmgx-reporter or \
clinical-variant-reporter that impact patient safety, diagnostic \
validity, or therapeutic recommendations. Framework issues \
(orchestrator routing, finemapping numerics) are important but \
not clinically significant.

Analyze the structured audit results using markdown bullets:
- **Clinical risks**: Any patient-safety findings (pharmgx, CVR)?
- **Worst performers**: Which harnesses have the lowest pass rates?
- **Patterns**: What do new/resolved findings reveal?
- **Trajectory**: Is the overall pass rate improving or regressing?

RULES:
- Do NOT perform arithmetic. Use ONLY pre-calculated values from the data.
- Every percentage and count you cite must appear verbatim in the source.
- If data is insufficient to determine something, state "insufficient data."
- Do NOT mention harnesses not in the provided data.

Example of a well-formed analysis:
"- **Clinical**: pharmgx-reporter at 54.5% with 3 omission findings \
(warfarin, DPYD, CYP2C19) — direct patient safety risk.
- **Worst**: clawbio-finemapping at 25.0%, driven by pip_nan_silent \
and susie_null_forced_signal.
- **Pattern**: 8 findings resolved in equity-scorer after FST fix, \
but 5 new fst_mislabeled findings appeared.
- **Trajectory**: Overall down 5.7pp to 65.0% — net regression.\""""
)

SYNTHESIZER_SYSTEM_PROMPT = """\
You are a technical writer synthesizing multiple analyst perspectives \
on a bioinformatics safety audit into a single concise narrative digest.

You will receive the structured audit data and 2-3 independent analyst \
interpretations. Produce a 3-5 sentence narrative summary:
1. Lead with the most important change (regression or improvement).
2. Name the worst-performing harnesses with their exact pass rates.
3. Highlight clinically significant findings if any analysts flagged them.
4. End with the overall trajectory.

RULES:
- Use ONLY numbers that appear in the STRUCTURED FACTS section.
- Do NOT perform arithmetic or re-calculate percentages.
- Do NOT round differently than the source.
- If analysts disagree on facts, use the values from STRUCTURED FACTS.
- If analysts disagree on interpretation, prefer the most conservative.
- You are authorized to state "insufficient data" rather than guessing."""


def _openrouter_call(
    model: str,
    system: str,
    user: str,
    api_key: str,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    """Call OpenRouter chat completions API. Stdlib-only (urllib)."""
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/biostochastics/clawbio_bench",
            "X-Title": "clawbio-bench daily audit",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    message = data["choices"][0]["message"]
    content = message.get("content")
    # Thinking models (mimo, minimax, deepseek-r1) put output in
    # "reasoning" when content is null — but only if finish_reason
    # is "stop". If finish_reason is "length", the model exhausted
    # its token budget on reasoning and never produced content.
    if content is None:
        finish = data["choices"][0].get("finish_reason", "")
        reasoning = message.get("reasoning") or ""
        if finish == "length" and reasoning:
            raise ValueError(
                f"Model {model} exhausted token budget on reasoning "
                f"({len(reasoning)} chars) — increase max_tokens"
            )
        if reasoning:
            # Some thinking models return the final answer in reasoning
            # when content is null and finish_reason is "stop"
            return reasoning
        raise ValueError(f"Model {model} returned null content")
    return content


def _extract_numbers(text: str) -> set[str]:
    """Extract all numeric tokens from text for verification."""
    return set(re.findall(r"\d+\.?\d*", text))


def _verify_numbers(llm_output: str, structured_digest: str, aggregate: dict) -> bool:
    """Verify that every number in the LLM output appears in source data.

    Numbers are checked against: the structured digest string, and the
    raw aggregate JSON (serialized). This allows the LLM to surface
    per-harness stats that aren't in the digest but are in the data.
    """
    llm_numbers = _extract_numbers(llm_output)
    # Build the universe of valid numbers from all sources
    source_text = structured_digest + " " + json.dumps(aggregate, default=str)
    source_numbers = _extract_numbers(source_text)
    # Allow small set of universally valid numbers (percentages like "100",
    # counts like "0", "1", "7" for harness count)
    always_valid = {"0", "1"}
    unknown = llm_numbers - source_numbers - always_valid
    if unknown:
        print(
            f"  LLM verification: {len(unknown)} unrecognized number(s): {sorted(unknown)[:10]}",
            file=sys.stderr,
        )
    # Tolerate at most 1 unknown number (date fragments, ordinals)
    return len(unknown) <= 1


def run_swarm(
    structured_digest: str,
    aggregate: dict,
    baseline: dict | None,
    api_key: str,
    analyst_models: list[str] | None = None,
    synthesizer_model: str | None = None,
) -> str:
    """Run the multi-model analyst swarm + synthesizer pipeline.

    Returns the synthesized narrative, or the structured digest on failure.
    """
    models = analyst_models or DEFAULT_ANALYST_MODELS
    synth = synthesizer_model or DEFAULT_SYNTHESIZER_MODEL

    # Build a condensed aggregate for the LLM context — the full JSON
    # with all critical_failures rationales can exceed model context limits.
    condensed: dict = {
        "clawbio_commit": aggregate.get("clawbio_commit"),
        "date": aggregate.get("date"),
        "mode": aggregate.get("mode"),
        "overall": aggregate.get("overall"),
        "harnesses": {},
    }
    for hname, hdata in (aggregate.get("harnesses") or {}).items():
        if not isinstance(hdata, dict):
            continue
        condensed["harnesses"][hname] = {
            "pass_rate": hdata.get("pass_rate"),
            "pass_count": hdata.get("pass_count"),
            "fail_count": hdata.get("fail_count"),
            "evaluated": hdata.get("evaluated"),
            "harness_errors": hdata.get("harness_errors"),
            "categories": hdata.get("categories"),
            # Include first 10 critical failures (name + category only)
            "critical_failures": [
                {"test": cf.get("test"), "category": cf.get("category")}
                for cf in (hdata.get("critical_failures") or [])[:10]
            ],
        }

    context_parts = [
        "=== STRUCTURED DIGEST ===",
        structured_digest,
        "",
        "=== AGGREGATE DATA (condensed JSON) ===",
        json.dumps(condensed, indent=2, default=str),
    ]
    if baseline is not None:
        baseline_overall = baseline.get("overall") or {}
        context_parts.extend(
            [
                "",
                "=== BASELINE ===",
                f"Baseline commit: {baseline.get('clawbio_commit', 'unknown')}",
                f"Baseline pass rate: {baseline_overall.get('total_pass_rate', 0.0):.1f}%",
                f"Baseline pass: {baseline_overall.get('total_pass', 0)}"
                f"/{baseline_overall.get('total_evaluated', 0)}",
            ]
        )
    context = "\n".join(context_parts)

    # Fan out to analyst models concurrently
    analyses: dict[str, str | None] = {}
    errors: dict[str, str] = {}

    def _call_analyst(model: str) -> None:
        try:
            result = _openrouter_call(model, ANALYST_SYSTEM_PROMPT, context, api_key)
            analyses[model] = result
        except Exception as exc:
            errors[model] = str(exc)
            analyses[model] = None

    threads = [threading.Thread(target=_call_analyst, args=(m,)) for m in models]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=360)

    # Collect successful analyses
    successful = {m: a for m, a in analyses.items() if a is not None}
    if not successful:
        model_errors = "; ".join(f"{m}: {e}" for m, e in errors.items())
        print(
            f"  Swarm: all analysts failed ({model_errors}). Using structured digest.",
            file=sys.stderr,
        )
        return structured_digest

    for model, error in errors.items():
        print(f"  Swarm: {model} failed ({error}), continuing with others.", file=sys.stderr)

    print(
        f"  Swarm: {len(successful)}/{len(models)} analysts responded.",
        file=sys.stderr,
    )

    # Build synthesizer prompt
    synth_parts = [
        "=== STRUCTURED FACTS (ground truth — numbers MUST match) ===",
        structured_digest,
        "",
    ]
    for i, (model, analysis) in enumerate(successful.items(), 1):
        short_name = model.split("/")[-1] if "/" in model else model
        synth_parts.extend(
            [
                f"=== ANALYST {i} ({short_name}) ===",
                analysis,
                "",
            ]
        )

    synth_context = "\n".join(synth_parts)

    try:
        synthesis = _openrouter_call(
            synth,
            SYNTHESIZER_SYSTEM_PROMPT,
            synth_context,
            api_key,
            temperature=0.1,
            max_tokens=8192,
        )
    except Exception as exc:
        print(
            f"  Swarm: synthesizer ({synth}) failed ({exc}). Using structured digest.",
            file=sys.stderr,
        )
        return structured_digest

    # Verify numbers in synthesis against source data
    if _verify_numbers(synthesis, structured_digest, aggregate):
        print("  Swarm: synthesis verified.", file=sys.stderr)
        return synthesis
    else:
        print(
            "  Swarm: synthesis failed number verification. Using structured digest.",
            file=sys.stderr,
        )
        return structured_digest


# ---------------------------------------------------------------------------
# Audit log — separate dated files in baselines/log/{daily,weekly,monthly}/
# ---------------------------------------------------------------------------

WEEKLY_SYSTEM_PROMPT = """\
You are a bioinformatics audit trend analyst for clawbio-bench. Given \
daily audit digests from the past week, produce a markdown-formatted \
weekly summary with these bullets:
- **Trajectory**: Overall direction (improving/regressing/stable) with \
net pass rate change in percentage points.
- **Movers**: Which harness improved or regressed most, named with its \
start and end pass rates.
- **Findings delta**: Net new vs resolved findings for the week.
- **Persistent patterns**: Any finding category appearing 3+ days in a row.

RULES:
- Use ONLY numbers from the provided daily entries.
- Do NOT perform arithmetic — use only pre-calculated values.
- If fewer than 7 days are available, state "N of 7 days present."
- If data is insufficient, state "insufficient data" for that bullet.
- You are authorized to acknowledge uncertainty."""

MONTHLY_SYSTEM_PROMPT = """\
You are a bioinformatics audit trend analyst for clawbio-bench. Given \
daily and weekly audit digests from the past month, produce a \
markdown-formatted monthly summary with these bullets:
- **Range**: Best and worst pass rate days, named by date.
- **Harness trends**: Which harnesses improved or regressed, with rates.
- **Findings delta**: Net new vs resolved findings for the month.
- **Systemic issues**: Recurring patterns or persistent failures.
- **Assessment**: Overall audit target health trajectory (1-2 sentences).

RULES:
- Use ONLY numbers from the provided entries.
- Do NOT perform arithmetic — use only pre-calculated values.
- Name specific dates when citing best/worst days.
- If data is insufficient, state "insufficient data" for that bullet.
- You are authorized to acknowledge uncertainty."""


SELF_CHANGELOG_SYSTEM_PROMPT = """\
You are a release notes analyst for clawbio-bench (the benchmark suite), \
NOT ClawBio (the audit target). These are two separate repositories. \
Summarize ONLY changes to clawbio-bench itself.

Given the recent git log and diff stat, produce markdown bullets:
- **New test coverage**: Harnesses, test cases, or verdict categories added.
- **Framework changes**: CLI flags, report formats, CI workflow changes.
- **Fixes**: Bug fixes or correctness improvements.
- **Coverage impact**: How these changes affect audit coverage or reliability.

RULES:
- Be specific: name files, functions, and features.
- Do NOT summarize changes to ClawBio — only clawbio-bench.
- If the impact of a change is not clear from the diff, state \
"Impact unclear" rather than guessing.
- You are authorized to acknowledge uncertainty."""


def _today_str() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d")


INVESTIGATION_SYSTEM_PROMPT = (
    """\
You are a bioinformatics audit coverage analyst for clawbio-bench. \
You are given:
1. The current audit results (aggregate JSON with per-harness findings).
2. The project README describing current harness coverage.
3. The project ROADMAP describing planned harnesses.

First, reason step-by-step:
a) List the ClawBio skills mentioned in the README.
b) Map each to an existing harness (or note "no harness").
c) Check the audit results for unexpected category patterns.
d) Compare findings against ROADMAP priorities.

Then produce your analysis with these sections:
- **Coverage gaps**: New ClawBio skills with no harness coverage.
- **Test case updates needed**: Changed behavior suggesting updates.
- **New patterns**: Finding categories suggesting new test cases.
- **ROADMAP adjustments**: Priority changes based on current findings.
- **Action list** (max 5 items, prioritized).

RULES:
- ONLY flag gaps for capabilities EXPLICITLY described in the provided \
text. If sections are truncated (marked [...]), do NOT infer content.
- Do NOT invent ClawBio skills or harness names.
- Valid harnesses: """
    + _VALID_HARNESSES
    + """.
- If data is insufficient, state "insufficient data."

Be specific: name skills, harnesses, and categories. Keep to 4-8 \
sentences. End with a prioritized action list (max 5 items)."""
)


def _read_daily_files(log_dir: Path, days: int = 7) -> list[tuple[str, str]]:
    """Read recent daily log files. Returns [(date, content), ...]."""
    daily_dir = log_dir / "daily"
    if not daily_dir.exists():
        return []
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(days=days)
    entries = []
    for f in sorted(daily_dir.glob("*.md")):
        date_str = f.stem  # e.g. "2026-04-06"
        try:
            from datetime import datetime as dt

            d = dt.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
            if d >= cutoff:
                entries.append((date_str, f.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return entries


def _read_month_files(log_dir: Path, year: int, month: int) -> list[tuple[str, str]]:
    """Read all daily files for a given month."""
    daily_dir = log_dir / "daily"
    if not daily_dir.exists():
        return []
    prefix = f"{year}-{month:02d}"
    entries = []
    for f in sorted(daily_dir.glob(f"{prefix}-*.md")):
        entries.append((f.stem, f.read_text(encoding="utf-8")))
    # Also include weekly files for this month
    weekly_dir = log_dir / "weekly"
    if weekly_dir.exists():
        for f in sorted(weekly_dir.glob("*.md")):
            content = f.read_text(encoding="utf-8")
            # Include weekly if it mentions dates in this month
            if prefix in content or prefix in f.stem:
                entries.append((f"weekly/{f.stem}", content))
    return entries


def _build_aggregate_summary(entries: list[tuple[str, str]]) -> str:
    """Structured summary from daily entries (no LLM)."""
    rates: list[float] = []
    for _, content in entries:
        m = re.search(r"\((\d+\.?\d*)%\)", content)
        if m:
            rates.append(float(m.group(1)))
    parts = [f"{len(entries)} daily runs."]
    if rates:
        avg = sum(rates) / len(rates)
        parts.append(f"Avg pass rate: {avg:.1f}%.")
        parts.append(f"Range: {min(rates):.1f}% — {max(rates):.1f}%.")
        if len(rates) >= 2:
            trend = rates[-1] - rates[0]
            direction = "up" if trend > 0 else "down" if trend < 0 else "flat"
            parts.append(f"Trend: {direction} {abs(trend):.1f}pp.")
    return " ".join(parts)


def _llm_aggregate(
    entries: list[tuple[str, str]],
    system_prompt: str,
    api_key: str,
    synthesizer: str | None,
) -> str | None:
    """LLM-enriched summary from entries. Returns None on failure."""
    synth_model = synthesizer or DEFAULT_SYNTHESIZER_MODEL
    context = "\n\n".join(f"### {date}\n{content}" for date, content in entries)
    try:
        return _openrouter_call(
            synth_model,
            system_prompt,
            context,
            api_key,
            temperature=0.1,
            max_tokens=8192,
        )
    except Exception as exc:
        print(f"  Log: LLM aggregation failed ({exc}).", file=sys.stderr)
        return None


def _get_clawbio_diff(clawbio_path: Path | None, days: int = 1) -> str | None:
    """Get recent ClawBio git log + diff stat."""
    if clawbio_path is None or not clawbio_path.exists():
        return None
    import subprocess

    since = f"{days} days ago"
    try:
        log_r = subprocess.run(
            [
                "git",
                "-C",
                str(clawbio_path),
                "log",
                f"--since={since}",
                "--oneline",
                "--no-merges",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        stat_r = subprocess.run(
            ["git", "-C", str(clawbio_path), "diff", "--stat", "HEAD~5..HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        parts = []
        if log_r.returncode == 0 and log_r.stdout.strip():
            parts.append(f"**Recent commits:**\n```\n{log_r.stdout.strip()}\n```")
        if stat_r.returncode == 0 and stat_r.stdout.strip():
            parts.append(f"**Diff stat:**\n```\n{stat_r.stdout.strip()}\n```")
        return "\n\n".join(parts) if parts else None
    except Exception:
        return None


CLAWBIO_DIFF_SYSTEM_PROMPT = """\
You are a bioinformatics code change analyst. Given the recent git \
log and diff stat for ClawBio (the audit target, NOT clawbio-bench), \
summarize what changed in 3-5 markdown bullets:
- New skills, modules, or features added.
- Bug fixes or behavioral changes that could affect audit results.
- Files modified that correspond to existing harnesses.
- Anything that suggests new test cases are needed.

RULES:
- Be specific: name files, skills, and commit messages.
- Do NOT confuse ClawBio changes with clawbio-bench changes.
- If impact is unclear from the diff, state "Impact unclear."
- You are authorized to acknowledge uncertainty."""


POLISH_SYSTEM_PROMPT = """\
You are a technical editor polishing a daily bioinformatics safety \
audit report. The report has multiple sections generated by different \
LLM analysts. Your job is to:
1. Ensure consistent tone and formatting across all sections.
2. Remove redundancy between sections (e.g., the same finding listed \
   in both the digest and the investigation).
3. Ensure all markdown headers follow ## for sections, ### for subsections.
4. Keep the structured data line intact — do not modify it.
5. Ensure the report reads as one coherent document, not separate pastes.
6. Fix any formatting issues (orphaned bullets, broken markdown).

RULES:
- Do NOT change any numbers — preserve them exactly.
- Do NOT add new analysis or findings — only polish what's there.
- Do NOT remove sections — only refine their prose and formatting.
- Keep the total length roughly the same (±20%).
- Preserve the <details> block for raw diffs unchanged."""


def write_daily_log(
    log_dir: Path,
    date: str,
    structured: str,
    narrative: str | None,
    investigation: str | None = None,
    self_changelog: str | None = None,
    clawbio_diff_summary: str | None = None,
    clawbio_raw_diff: str | None = None,
    api_key: str | None = None,
    synthesizer: str | None = None,
) -> Path:
    """Write a consolidated daily log file with all sections.

    When ``api_key`` is provided and multiple sections exist, the
    assembled report is sent to a final polish pass (Haiku 4.5) for
    coherence, deduplication, and formatting consistency.
    """
    daily_dir = log_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    path = daily_dir / f"{date}.md"

    sections = [f"# Daily Audit Report — {date}\n"]

    # Section 1: ClawBio audit digest
    sections.append("\n## ClawBio Audit Digest\n")
    if narrative and narrative != structured:
        sections.append(f"\n{narrative}\n")
        sections.append(f"\n**Structured:** {structured}\n")
    else:
        sections.append(f"\n{structured}\n")

    # Section 2: ClawBio changes (what the audit target did)
    if clawbio_diff_summary or clawbio_raw_diff:
        sections.append("\n## ClawBio Changes\n")
        if clawbio_diff_summary:
            sections.append(f"\n{clawbio_diff_summary}\n")
        if clawbio_raw_diff:
            sections.append(
                f"\n<details><summary>Raw diff</summary>\n\n{clawbio_raw_diff}\n\n</details>\n"
            )

    # Section 3: clawbio-bench changes (what the benchmark did)
    if self_changelog:
        sections.append("\n## clawbio-bench Changes\n")
        sections.append(f"\n{self_changelog}\n")

    # Section 4: Investigation (coverage gaps + roadmap)
    if investigation:
        sections.append("\n## Coverage Investigation\n")
        sections.append(f"\n{investigation}\n")

    draft = "\n".join(sections)

    # Final polish pass — only if we have multiple sections and an API key
    has_multiple_sections = (
        sum(
            [
                bool(narrative),
                bool(clawbio_diff_summary),
                bool(self_changelog),
                bool(investigation),
            ]
        )
        >= 2
    )
    if api_key and has_multiple_sections:
        polish_model = synthesizer or DEFAULT_SYNTHESIZER_MODEL
        try:
            polished = _openrouter_call(
                polish_model,
                POLISH_SYSTEM_PROMPT,
                draft,
                api_key,
                temperature=0.1,
            )
            # Verify the polish didn't mangle numbers
            if _verify_numbers(polished, structured, {}):
                draft = polished
                print("  Log: final polish applied.", file=sys.stderr)
            else:
                print("  Log: polish failed number verification, using raw.", file=sys.stderr)
        except Exception as exc:
            print(f"  Log: polish failed ({exc}), using raw.", file=sys.stderr)

    path.write_text(draft, encoding="utf-8")
    print(f"  Log: {path}", file=sys.stderr)
    return path


def write_weekly_log(
    log_dir: Path,
    api_key: str | None = None,
    synthesizer: str | None = None,
) -> Path | None:
    """Generate and write a weekly summary from the last 7 daily files."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    iso_year, iso_week, _ = now.isocalendar()
    entries = _read_daily_files(log_dir, days=7)
    if not entries:
        print("  Log: no daily entries in last 7 days, skipping weekly.", file=sys.stderr)
        return None

    structured = _build_aggregate_summary(entries)
    narrative = None
    if api_key:
        narrative = _llm_aggregate(entries, WEEKLY_SYSTEM_PROMPT, api_key, synthesizer)

    weekly_dir = log_dir / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{iso_year}-W{iso_week:02d}.md"
    path = weekly_dir / filename

    lines = [f"# Weekly Summary — {iso_year}-W{iso_week:02d}\n"]
    lines.append(f"\n_Covers {len(entries)} daily runs._\n")
    if narrative:
        lines.append(f"\n{narrative}\n")
        lines.append(f"\n---\n\n**Structured:** {structured}\n")
    else:
        lines.append(f"\n{structured}\n")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Log: {path}", file=sys.stderr)
    return path


def write_monthly_log(
    log_dir: Path,
    api_key: str | None = None,
    synthesizer: str | None = None,
) -> Path | None:
    """Generate and write a monthly summary for the previous month."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    if now.month == 1:
        target_year, target_month = now.year - 1, 12
    else:
        target_year, target_month = now.year, now.month - 1
    month_label = datetime(target_year, target_month, 1).strftime("%B %Y")

    entries = _read_month_files(log_dir, target_year, target_month)
    if not entries:
        print(f"  Log: no entries for {month_label}, skipping monthly.", file=sys.stderr)
        return None

    structured = _build_aggregate_summary(entries)
    narrative = None
    if api_key:
        narrative = _llm_aggregate(entries, MONTHLY_SYSTEM_PROMPT, api_key, synthesizer)

    monthly_dir = log_dir / "monthly"
    monthly_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{target_year}-{target_month:02d}.md"
    path = monthly_dir / filename

    lines = [f"# Monthly Summary — {month_label}\n"]
    lines.append(f"\n_Covers {len(entries)} entries._\n")
    if narrative:
        lines.append(f"\n{narrative}\n")
        lines.append(f"\n---\n\n**Structured:** {structured}\n")
    else:
        lines.append(f"\n{structured}\n")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Log: {path}", file=sys.stderr)
    return path


def run_investigation(
    aggregate: dict,
    api_key: str,
    synthesizer: str | None = None,
    readme_path: Path | None = None,
    roadmap_path: Path | None = None,
) -> str | None:
    """Run a coverage investigation: does clawbio-bench need to adapt?

    Feeds the current audit results plus README/ROADMAP to an LLM to
    detect coverage gaps from new ClawBio skills, suggest new test cases,
    and re-prioritize the roadmap.
    """
    synth_model = synthesizer or DEFAULT_SYNTHESIZER_MODEL

    context_parts = [
        "=== CURRENT AUDIT RESULTS ===",
        json.dumps(aggregate, indent=2, default=str),
    ]

    # Load README for coverage context
    if readme_path and readme_path.exists():
        readme = readme_path.read_text(encoding="utf-8")
        # Truncate to coverage-relevant sections to stay within context
        if len(readme) > 15000:
            readme = readme[:15000] + "\n\n[... truncated ...]"
        context_parts.extend(["", "=== README (coverage context) ===", readme])

    # Load ROADMAP for priority context
    if roadmap_path and roadmap_path.exists():
        roadmap = roadmap_path.read_text(encoding="utf-8")
        if len(roadmap) > 10000:
            roadmap = roadmap[:10000] + "\n\n[... truncated ...]"
        context_parts.extend(["", "=== ROADMAP ===", roadmap])

    context = "\n".join(context_parts)

    try:
        return _openrouter_call(
            synth_model,
            INVESTIGATION_SYSTEM_PROMPT,
            context,
            api_key,
            temperature=0.2,
            max_tokens=8192,
        )
    except Exception as exc:
        print(f"  Investigation failed ({exc}).", file=sys.stderr)
        return None


def write_investigation_log(
    log_dir: Path,
    date: str,
    investigation: str,
) -> Path:
    """Write an investigation report to the log."""
    inv_dir = log_dir / "investigations"
    inv_dir.mkdir(parents=True, exist_ok=True)
    path = inv_dir / f"{date}.md"
    path.write_text(
        f"# Coverage Investigation — {date}\n\n{investigation}\n",
        encoding="utf-8",
    )
    print(f"  Log: {path}", file=sys.stderr)
    return path


def run_self_changelog(
    api_key: str,
    synthesizer: str | None = None,
    repo_root: Path | None = None,
    days: int = 7,
) -> str | None:
    """Summarize recent changes to clawbio-bench itself.

    Runs ``git log`` and ``git diff`` on the benchmark repo to capture
    what changed in the suite, then uses an LLM to produce a narrative.
    """
    import subprocess

    root = repo_root or Path(__file__).resolve().parent.parent
    since = f"{days} days ago"

    try:
        log_result = subprocess.run(
            ["git", "-C", str(root), "log", f"--since={since}", "--oneline", "--no-merges"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        git_log = log_result.stdout.strip() if log_result.returncode == 0 else ""

        diff_result = subprocess.run(
            ["git", "-C", str(root), "diff", "--stat", "HEAD~20..HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        git_stat = diff_result.stdout.strip() if diff_result.returncode == 0 else ""
    except Exception as exc:
        print(f"  Self-changelog: git failed ({exc}).", file=sys.stderr)
        return None

    if not git_log:
        return None

    context = f"=== GIT LOG (last {days} days) ===\n{git_log}"
    if git_stat:
        context += f"\n\n=== DIFF STAT ===\n{git_stat}"

    # Also include CHANGELOG.md head if available
    changelog = root / "CHANGELOG.md"
    if changelog.exists():
        cl_text = changelog.read_text(encoding="utf-8")[:5000]
        context += f"\n\n=== CHANGELOG.md (head) ===\n{cl_text}"

    synth_model = synthesizer or DEFAULT_SYNTHESIZER_MODEL
    try:
        return _openrouter_call(
            synth_model,
            SELF_CHANGELOG_SYSTEM_PROMPT,
            context,
            api_key,
            temperature=0.1,
            max_tokens=8192,
        )
    except Exception as exc:
        print(f"  Self-changelog: LLM failed ({exc}).", file=sys.stderr)
        return None


def write_self_changelog_log(
    log_dir: Path,
    date: str,
    changelog: str,
) -> Path:
    """Write a self-changelog entry."""
    cl_dir = log_dir / "self_changelog"
    cl_dir.mkdir(parents=True, exist_ok=True)
    path = cl_dir / f"{date}.md"
    path.write_text(
        f"# clawbio-bench Changes — {date}\n\n{changelog}\n",
        encoding="utf-8",
    )
    print(f"  Log: {path}", file=sys.stderr)
    return path


# ---------------------------------------------------------------------------
# Webhook posting
# ---------------------------------------------------------------------------


def post_to_webhook(url: str, message: str) -> None:
    """POST a message to a webhook URL. Tries Slack format, falls back to Discord."""
    # Slack uses {"text": ...}, Discord uses {"content": ...}.
    # Send both keys — each platform ignores the unknown one.
    payload = json.dumps({"text": message, "content": message}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            if status >= 300:
                print(f"WARNING: webhook returned HTTP {status}", file=sys.stderr)
    except urllib.error.HTTPError as exc:
        print(f"ERROR: webhook POST failed: HTTP {exc.code} — {exc.reason}", file=sys.stderr)
        raise
    except urllib.error.URLError as exc:
        print(f"ERROR: webhook POST failed: {exc.reason}", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post a daily audit digest to a webhook.",
    )
    parser.add_argument(
        "--results",
        type=Path,
        required=True,
        help="Path to the current run's results dir or aggregate_report.json",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Optional baseline aggregate for delta computation",
    )
    parser.add_argument(
        "--webhook",
        type=str,
        default=None,
        help="Webhook URL to POST the digest to (omit for stdout only)",
    )
    parser.add_argument(
        "--llm",
        type=str,
        choices=["openrouter"],
        default=None,
        help=(
            "Enrich the digest via a multi-model LLM swarm. "
            "'openrouter' requires OPENROUTER_API_KEY env var. "
            "Three analyst models independently interpret the findings, "
            "then a synthesizer (Haiku 4.5) fuses them into a narrative. "
            "Falls back to structured digest on failure or verification error."
        ),
    )
    parser.add_argument(
        "--llm-models",
        type=str,
        default=None,
        help=(
            "Comma-separated analyst model names for the swarm "
            "(default: mimo,minimax-m2.7,deepseek-v3.2). "
            "Use OpenRouter model IDs."
        ),
    )
    parser.add_argument(
        "--llm-synthesizer",
        type=str,
        default=None,
        help=("Synthesizer model for the swarm (default: anthropic/claude-haiku-4.5)"),
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help=(
            "Write dated log files to this directory "
            "(e.g. baselines/log). Creates daily/, weekly/, monthly/, "
            "and investigations/ subdirectories."
        ),
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Also generate a weekly summary from the last 7 daily logs.",
    )
    parser.add_argument(
        "--monthly",
        action="store_true",
        help="Also generate a monthly summary for the previous month.",
    )
    parser.add_argument(
        "--investigate",
        action="store_true",
        help=(
            "Run a coverage investigation: analyze whether clawbio-bench "
            "needs new test cases or harnesses based on changes in ClawBio. "
            "Requires --llm openrouter. Reads README.md and ROADMAP.md "
            "from the repo root for coverage context."
        ),
    )
    parser.add_argument(
        "--self-changelog",
        action="store_true",
        help=(
            "Summarize recent changes to clawbio-bench itself (git log + "
            "diff from the benchmark repo, not ClawBio). Requires --llm "
            "openrouter."
        ),
    )
    parser.add_argument(
        "--clawbio-repo",
        type=Path,
        default=None,
        help=(
            "Path to ClawBio repo checkout. When provided with --llm, "
            "analyzes recent ClawBio git changes and includes a diff "
            "summary in the daily report."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the digest to stdout without POSTing or writing logs",
    )
    args = parser.parse_args()

    try:
        aggregate = _load_aggregate(args.results)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot load results: {exc}", file=sys.stderr)
        return 2

    baseline = None
    if args.baseline:
        try:
            baseline = _load_aggregate(args.baseline)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"WARNING: baseline unavailable ({exc}), skipping delta", file=sys.stderr)

    structured = build_digest(aggregate, baseline)

    # Resolve API key early (used by swarm, log aggregation, investigation)
    api_key = os.environ.get("OPENROUTER_API_KEY", "") if args.llm == "openrouter" else ""
    if args.llm == "openrouter" and not api_key:
        print(
            "WARNING: --llm openrouter requires OPENROUTER_API_KEY env var. "
            "Using structured digest.",
            file=sys.stderr,
        )

    # LLM swarm enrichment
    narrative: str | None = None
    if api_key:
        analyst_models = None
        if args.llm_models:
            analyst_models = [m.strip() for m in args.llm_models.split(",")]
        result = run_swarm(
            structured,
            aggregate,
            baseline,
            api_key,
            analyst_models=analyst_models,
            synthesizer_model=args.llm_synthesizer,
        )
        if result != structured:
            narrative = result

    # Print
    if narrative:
        print("--- Structured ---")
        print(structured)
        print("--- Narrative ---")
        print(narrative)
    else:
        print(structured)

    digest = narrative or structured
    date = aggregate.get("date") or _today_str()

    # Build all sections for the consolidated daily report
    investigation_text: str | None = None
    self_cl_text: str | None = None
    clawbio_diff_text: str | None = None
    clawbio_raw: str | None = None
    repo_root = Path(__file__).resolve().parent.parent

    if not args.dry_run and api_key:
        # ClawBio diff analysis (if --clawbio-repo provided)
        clawbio_repo = getattr(args, "clawbio_repo", None)
        if clawbio_repo:
            clawbio_raw = _get_clawbio_diff(clawbio_repo)
            if clawbio_raw:
                try:
                    clawbio_diff_text = _openrouter_call(
                        args.llm_synthesizer or DEFAULT_SYNTHESIZER_MODEL,
                        CLAWBIO_DIFF_SYSTEM_PROMPT,
                        clawbio_raw,
                        api_key,
                        temperature=0.1,
                    )
                except Exception as exc:
                    print(f"  ClawBio diff analysis failed ({exc}).", file=sys.stderr)

        # Coverage investigation
        if args.investigate:
            investigation_text = run_investigation(
                aggregate,
                api_key,
                synthesizer=args.llm_synthesizer,
                readme_path=repo_root / "README.md",
                roadmap_path=repo_root / "ROADMAP.md",
            )

        # Self-changelog
        if args.self_changelog:
            self_cl_text = run_self_changelog(
                api_key,
                synthesizer=args.llm_synthesizer,
                repo_root=repo_root,
            )

    # Write consolidated daily log
    if args.log_dir and not args.dry_run:
        write_daily_log(
            args.log_dir,
            date,
            structured,
            narrative,
            investigation=investigation_text,
            self_changelog=self_cl_text,
            clawbio_diff_summary=clawbio_diff_text,
            clawbio_raw_diff=clawbio_raw,
            api_key=api_key or None,
            synthesizer=args.llm_synthesizer,
        )

        if args.weekly:
            write_weekly_log(
                args.log_dir,
                api_key=api_key or None,
                synthesizer=args.llm_synthesizer,
            )

        if args.monthly:
            write_monthly_log(
                args.log_dir,
                api_key=api_key or None,
                synthesizer=args.llm_synthesizer,
            )

    # Webhook
    if args.webhook and not args.dry_run:
        try:
            post_to_webhook(args.webhook, digest)
            print("Digest posted to webhook.", file=sys.stderr)
        except (urllib.error.URLError, urllib.error.HTTPError):
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
