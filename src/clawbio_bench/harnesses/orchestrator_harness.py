#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ClawBio Orchestrator Benchmark Harness
=======================================
Tests the bio-orchestrator's routing decisions against 44 test cases:
  - 15 extension-based routing (file type detection)
  - 18 keyword-based routing (natural language + M-3 unreachable)
  - 11 error handling (invalid inputs, path traversal, missing files)

Rubric (7 categories):
  routed_correct      — Correct skill selected for input
  routed_wrong        — Wrong skill selected
  stub_warned         — Routed to stub skill, warning emitted
  stub_silent         — Routed to stub skill, no warning
  unroutable_handled  — Unknown input, clean error message
  unroutable_crash    — Unknown input, crash/traceback
  harness_error       — Harness infrastructure error
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "bio-orchestrator"
BENCHMARK_VERSION = "0.1.0"

RUBRIC_CATEGORIES = [
    "routed_correct",
    "routed_wrong",
    "stub_warned",
    "stub_silent",
    "unroutable_handled",
    "unroutable_crash",
    "harness_error",
]

PASS_CATEGORIES = ["routed_correct", "stub_warned", "unroutable_handled"]
FAIL_CATEGORIES = ["routed_wrong", "stub_silent", "unroutable_crash"]

# Documented skill inventory baseline at the time this harness version was
# cut. These sets are kept for **drift detection only** and MUST NOT be used
# as scoring inputs. Verdict logic depends entirely on the per-test
# ``GROUND_TRUTH_EXECUTABLE`` field. See ``discover_clawbio_skills`` below,
# which scans the commit under audit to produce a live inventory, and
# ``compute_inventory_drift`` which compares the live inventory against this
# baseline and records divergence as verdict metadata.
#
# Baseline pinned at ClawBio HEAD 5cf83c5 (2026-04-04).
_BASELINE_EXECUTABLE_SKILLS: frozenset[str] = frozenset(
    {
        "equity-scorer",
        "pharmgx-reporter",
        "nutrigx_advisor",
        "bio-orchestrator",
        "claw-metagenomics",
        "claw-ancestry-pca",
        "scrna-orchestrator",
        "scrna-embedding",
        "genome-compare",
        "clinpgx",
        "gwas-prs",
        "gwas-lookup",
        "profile-report",
        "data-extractor",
        "ukb-navigator",
        "galaxy-bridge",
        "rnaseq-de",
        "proteomics-de",
        "methylation-clock",
        "clinical-variant-reporter",
        "variant-annotation",
        "clinical-trial-finder",
        "target-validation-scorer",
        "omics-target-evidence-mapper",
        "bioconductor-bridge",
        "illumina-bridge",
        "diff-visualizer",
        "fine-mapping",
        "genome-match",
        "recombinator",
        "soul2dna",
        "pubmed-summariser",
        "protocols-io",
        "bigquery-public",
        "cell-detection",
        "struct-predictor",
        "labstep",
    }
)

_BASELINE_STUB_SKILLS: frozenset[str] = frozenset(
    {
        "vcf-annotator",
        "lit-synthesizer",
        "repro-enforcer",
        "claw-semantic-sim",
        "drug-photo",
        "seq-wrangler",
    }
)


def discover_clawbio_skills(repo_path: Path) -> dict[str, set[str]]:
    """Scan ClawBio at ``repo_path`` and classify each skill by entrypoint.

    Returns a dict with three disjoint sets:
      - ``executable``: skills with a Python entrypoint
        (``<name>.py``, ``api.py``, or ``__main__.py`` directly inside the
        skill directory)
      - ``stub``: skills with a ``SKILL.md`` but no Python entrypoint
      - ``unknown``: directories that do not contain ``SKILL.md`` at all
        (build artifacts, documentation folders, etc.)

    This classification is used for **metadata and drift detection only**.
    Scoring depends on per-test ``GROUND_TRUTH_EXECUTABLE`` exclusively.
    """
    skills_dir = repo_path / "skills"
    inventory: dict[str, set[str]] = {"executable": set(), "stub": set(), "unknown": set()}
    if not skills_dir.is_dir():
        return inventory
    # A skill counts as executable if its root directory (or a shallow
    # ``src/`` subdirectory) contains at least one script-shaped file
    # that is not a test, conftest, or package marker. This catches:
    #   - top-level ``*.py`` entrypoints (most ClawBio skills)
    #   - ``src/<name>.py`` layouts (newer Python packaging)
    #   - R / bash entrypoints (``*.R``, ``*.sh``) — ClawBio is
    #     Python-first today but an audit target may ship non-Python
    #     skills in the future
    # Deeper subdirectories (``tests/``, ``examples/``, ``data/``) are
    # still not walked — helpers nested there are not entrypoints.
    _non_entrypoint_names = {"__init__.py", "conftest.py"}
    _entrypoint_globs = (
        "*.py",
        "*.R",
        "*.sh",
        "src/*.py",
        "src/*.R",
        "src/*.sh",
    )
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if not (child / "SKILL.md").exists():
            inventory["unknown"].add(name)
            continue
        has_entrypoint = False
        for pattern in _entrypoint_globs:
            for script in child.glob(pattern):
                if not script.is_file():
                    continue
                if script.name in _non_entrypoint_names:
                    continue
                if script.name.startswith("test_"):
                    continue
                has_entrypoint = True
                break
            if has_entrypoint:
                break
        if has_entrypoint:
            inventory["executable"].add(name)
        else:
            inventory["stub"].add(name)
    return inventory


def compute_inventory_drift(
    live_inventory: dict[str, set[str]],
) -> dict[str, list[str]]:
    """Compare a live inventory against the pinned baseline.

    Returns a drift report with three keys:
      - ``promoted``: skills the baseline calls stubs but the live repo
        implements (potential false ``stub_silent`` verdicts if not fixed)
      - ``demoted``: skills the baseline calls executable but the live repo
        has stripped (potential false ``routed_correct`` credit)
      - ``new``: skills present in the live repo but absent from the baseline
        (no verdict impact, but signals expansion of audit surface)

    This is informational: any drift >= 1 skill should be considered a signal
    that the baseline constants in this module are out of date and that new
    test cases may be needed.
    """
    live_executable = live_inventory.get("executable", set())
    live_stub = live_inventory.get("stub", set())
    baseline_all = _BASELINE_EXECUTABLE_SKILLS | _BASELINE_STUB_SKILLS
    live_all = live_executable | live_stub
    promoted = sorted(live_executable & _BASELINE_STUB_SKILLS)
    demoted = sorted(live_stub & _BASELINE_EXECUTABLE_SKILLS)
    new = sorted(live_all - baseline_all)
    return {"promoted": promoted, "demoted": demoted, "new": new}


GROUND_TRUTH_REFS = {
    "ORCHESTRATOR_SKILL_MD": ("bio-orchestrator SKILL.md, accessed 2026-04-03"),
    "ORCHESTRATOR_SOURCE": (
        "bio-orchestrator orchestrator.py EXTENSION_MAP and KEYWORD_MAP, accessed 2026-04-03"
    ),
}

CATEGORY_LEGEND = {
    "routed_correct": {"color": "#22c55e", "label": "Correct routing"},
    "routed_wrong": {"color": "#ef4444", "label": "WRONG routing"},
    "stub_warned": {"color": "#fbbf24", "label": "Stub routed, warned"},
    "stub_silent": {"color": "#f97316", "label": "Stub routed, NO warning"},
    "unroutable_handled": {"color": "#86efac", "label": "Unknown input handled"},
    "unroutable_crash": {"color": "#ef4444", "label": "Unknown input CRASH"},
    "harness_error": {"color": "#9ca3af", "label": "Harness infrastructure error"},
}


# ---------------------------------------------------------------------------
# Report Analyzer
# ---------------------------------------------------------------------------


def analyze_routing_output(stdout: str, stderr: str, exit_code: int) -> dict:
    """Parse orchestrator stdout/stderr for routing decision.

    The orchestrator outputs JSON with 'detected_skill' on success,
    or error messages on failure.
    """
    analysis: dict[str, Any] = {
        "selected_skill": None,
        "detection_method": None,
        "is_stub": None,
        "is_executable": None,
        "warning_emitted": False,
        "error_message": None,
        "available_skills": [],
        "exit_code": exit_code,
        "raw_stdout_excerpt": stdout[:500] if stdout else "",
        "json_parsed": False,
        "list_skills_output": False,
    }

    # Check for list-skills output
    if "Available skills:" in stdout:
        analysis["list_skills_output"] = True
        skills = []
        for line in stdout.split("\n"):
            line = line.strip()
            if line.startswith("- "):
                skills.append(line[2:].strip())
        analysis["available_skills"] = skills
        return analysis

    # Try to parse JSON output — find the LAST complete JSON object
    # (FLock may print braces in reasoning before the final JSON)
    try:
        # Walk backwards from end of stdout to find last complete JSON object
        last_brace = stdout.rfind("}")
        if last_brace >= 0:
            # Find the matching opening brace by counting nesting
            depth = 0
            json_start = -1
            for i in range(last_brace, -1, -1):
                if stdout[i] == "}":
                    depth += 1
                elif stdout[i] == "{":
                    depth -= 1
                    if depth == 0:
                        json_start = i
                        break
            if json_start >= 0:
                data = json.loads(stdout[json_start : last_brace + 1])
                analysis["json_parsed"] = True
                # Shape detection: single-skill result has ``detected_skill``;
                # multi-skill composition has a dict-of-dicts where each
                # value carries ``success`` / ``exit_code`` / ``output_dir``.
                if isinstance(data, dict) and "detected_skill" in data:
                    analysis["selected_skill"] = data.get("detected_skill")
                    analysis["detection_method"] = data.get("detection_method")
                    # Stub-ness is no longer inferred from harness-internal
                    # sets. The tool's stdout is the only authoritative
                    # source: if the orchestrator tags its own decision as
                    # a stub (e.g. via a ``stub=True`` field), we record
                    # it here. Otherwise ``is_stub`` stays ``None`` and
                    # scoring falls back to ``GROUND_TRUTH_EXECUTABLE``.
                    if "stub" in data:
                        analysis["is_stub"] = bool(data.get("stub"))
                elif (
                    isinstance(data, dict)
                    and data
                    and all(isinstance(v, dict) and "success" in v for v in data.values())
                ):
                    # Multi-skill composition output: dict keyed by
                    # skill registry name → per-skill result dict.
                    analysis["multi_skill_results"] = {
                        name: {
                            "success": bool(result.get("success")),
                            "exit_code": result.get("exit_code"),
                            "output_dir": result.get("output_dir"),
                            "files": result.get("files", []),
                        }
                        for name, result in data.items()
                    }
                    analysis["detection_method"] = "multi-skill"
    except (json.JSONDecodeError, ValueError):
        pass

    # Check stderr for warnings using specific patterns
    if stderr:
        for line in stderr.split("\n"):
            line_stripped = line.strip()
            if line_stripped.startswith("WARNING:") or "stub" in line_stripped.lower():
                analysis["warning_emitted"] = True
                break

    # Check stdout for error messages
    if "Could not determine skill" in stdout:
        analysis["error_message"] = "Could not determine skill"
    elif "not found" in stdout.lower() and exit_code != 0:
        analysis["error_message"] = "Skill not found"
    elif "Invalid skill name" in stdout:
        analysis["error_message"] = "Invalid skill name (path traversal blocked)"

    return analysis


# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------


def score_routing_verdict(
    ground_truth: dict,
    analysis: dict,
    execution: harness_core.ExecutionResult,
) -> dict:
    """Score a routing decision against ground truth.

    Ground truth headers:
        GROUND_TRUTH_SKILL: expected skill name
        GROUND_TRUTH_EXECUTABLE: true/false (whether skill has Python code)
        GROUND_TRUTH_ROUTING_METHOD: extension/keyword/error
        HAZARD_ROUTING: description of what goes wrong
        QUERY_TEXT: natural language query (keyword tests)
        EXPECTED_EXIT_CODE: 0 or 1
    """
    expected_skill = ground_truth.get("GROUND_TRUTH_SKILL", "").strip()
    expected_executable = ground_truth.get("GROUND_TRUTH_EXECUTABLE", "true").lower() == "true"
    routing_method = ground_truth.get("GROUND_TRUTH_ROUTING_METHOD", "")
    expected_exit = int(ground_truth.get("EXPECTED_EXIT_CODE", "0"))
    finding_category = ground_truth.get("FINDING_CATEGORY", "")
    expected_skill_list_raw = ground_truth.get("GROUND_TRUTH_SKILL_LIST", "").strip()
    expected_skill_list = (
        [s.strip() for s in expected_skill_list_raw.split(",") if s.strip()]
        if expected_skill_list_raw
        else []
    )

    observed_skill = analysis.get("selected_skill")
    multi_skill_results = analysis.get("multi_skill_results") or {}
    exit_code = execution.exit_code

    # ── Multi-skill composition mode (``--skills A,B,C``) ──
    # Score independently of single-skill logic because the tool's output
    # shape is different: a results dict keyed by skill registry name.
    # The registry map lives in orchestrator.py (SKILL_REGISTRY_MAP) and
    # translates skill directory names (e.g. ``pharmgx-reporter``) to
    # short registry names (``pharmgx``). Tests supply the registry name
    # in ``GROUND_TRUTH_SKILL_LIST`` so scoring can compare apples to
    # apples without duplicating the mapping here.
    if expected_skill_list:
        details = {
            "expected_skill_list": expected_skill_list,
            "observed_skills": sorted(multi_skill_results.keys()),
            "expected_exit_code": expected_exit,
            "observed_exit_code": exit_code,
            "finding_category": finding_category,
            "detection_method": analysis.get("detection_method"),
        }
        if exit_code != expected_exit:
            return {
                "category": "unroutable_crash",
                "rationale": (
                    f"Multi-skill composition exited {exit_code}, expected {expected_exit}"
                ),
                "details": details,
            }
        missing = [s for s in expected_skill_list if s not in multi_skill_results]
        unexpected = [s for s in multi_skill_results if s not in expected_skill_list]
        if missing:
            return {
                "category": "routed_wrong",
                "rationale": f"Multi-skill output missing expected skills: {missing}",
                "details": details,
            }
        if unexpected:
            return {
                "category": "routed_wrong",
                "rationale": f"Multi-skill output contains unexpected skills: {unexpected}",
                "details": details,
            }
        # Structural check passed — cross-skill leakage detection is a
        # content-level concern handled by downstream test case hazards
        # that inspect per-skill output directories.
        return {
            "category": "routed_correct",
            "rationale": (
                f"Multi-skill composition dispatched {len(expected_skill_list)} skills correctly"
            ),
            "details": details,
        }

    details = {
        "expected_skill": expected_skill,
        "observed_skill": observed_skill,
        "expected_executable": expected_executable,
        "expected_exit_code": expected_exit,
        "observed_exit_code": exit_code,
        "finding_category": finding_category,
        "detection_method": analysis.get("detection_method"),
        "routing_method": routing_method,
        "is_stub": analysis.get("is_stub"),
        "warning_emitted": analysis.get("warning_emitted", False),
        "json_parsed": analysis.get("json_parsed", False),
    }

    # ── Crash detection ──
    # A traceback in stderr is a crash even if the exit code matches expected
    # (parallel to equity_harness — don't credit traceback crashes as handled).
    # We match on the canonical Python traceback header instead of bare
    # "Traceback" to avoid false positives on stderr text that happens to
    # contain the word (e.g. "traceback not available").
    if exit_code != 0 and "Traceback (most recent call last):" in execution.stderr:
        return {
            "category": "unroutable_crash",
            "rationale": (f"Tool crashed (exit {exit_code}) with traceback"),
            "details": details,
        }
    # Second-pass crash detection: line-anchored error markers instead of a
    # naive ``"Error" in stderr`` substring, which would false-match benign
    # content like "no errors found" or "Error correcting code".
    if exit_code != 0 and exit_code != expected_exit:
        for _line in execution.stderr.split("\n"):
            _stripped = _line.strip()
            if _stripped.startswith(("Error:", "Exception:", "Fatal:", "FATAL:", "CRITICAL:")):
                return {
                    "category": "unroutable_crash",
                    "rationale": (f"Tool crashed (exit {exit_code})"),
                    "details": details,
                }

    # ── Expected error cases (unroutable inputs) ──
    if expected_exit != 0:
        if exit_code == expected_exit:
            if analysis.get("error_message"):
                return {
                    "category": "unroutable_handled",
                    "rationale": (
                        f"Clean error for unroutable input: {analysis['error_message']}"
                    ),
                    "details": details,
                }
            return {
                "category": "unroutable_handled",
                "rationale": f"Exited with expected code {exit_code}",
                "details": details,
            }
        if exit_code == 0 and observed_skill:
            # Tool routed when it shouldn't have
            return {
                "category": "routed_wrong",
                "rationale": (
                    f"Expected error exit but tool routed to '{observed_skill}' instead"
                ),
                "details": details,
            }
        return {
            "category": "unroutable_crash",
            "rationale": (f"Expected exit {expected_exit}, got {exit_code}"),
            "details": details,
        }

    # ── Successful routing ──
    if observed_skill is None:
        # No skill detected but we expected one
        if expected_skill:
            return {
                "category": "unroutable_crash" if exit_code != 0 else "routed_wrong",
                "rationale": (f"No skill detected (expected '{expected_skill}')"),
                "details": details,
            }
        return {
            "category": "unroutable_handled",
            "rationale": "No skill detected (expected: none)",
            "details": details,
        }

    # ── Check if routed to correct skill ──
    if observed_skill == expected_skill:
        # Correct routing — but check if it's a stub. The authoritative
        # source is the per-test ``GROUND_TRUTH_EXECUTABLE`` field: that is
        # the auditor's explicit declaration of whether the target skill
        # has executable code at the commit under audit. The tool's own
        # ``stub=True`` signal is NOT a secondary override — if we honored
        # it on top of ground truth, a tool that mislabeled its own skill
        # as a stub would corrupt the verdict in exactly the same way the
        # stale ``STUB_SKILLS`` frozenset used to. Ground truth wins.
        # ``observed_is_stub`` is retained purely in the verdict details
        # for post-hoc analysis (detecting tool-vs-auditor disagreements).
        expected_is_stub = not expected_executable
        details["observed_is_stub"] = analysis.get("is_stub")
        if expected_is_stub:
            if analysis.get("warning_emitted"):
                return {
                    "category": "stub_warned",
                    "rationale": (
                        f"Correctly routed to stub '{observed_skill}' with warning "
                        f"(expected_executable={expected_executable})"
                    ),
                    "details": details,
                }
            return {
                "category": "stub_silent",
                "rationale": (
                    f"Correctly routed to stub '{observed_skill}' but NO warning "
                    f"that it's a stub (expected_executable={expected_executable})"
                ),
                "details": details,
            }
        return {
            "category": "routed_correct",
            "rationale": f"Correctly routed to '{observed_skill}'",
            "details": details,
        }

    # ── Wrong skill ──
    return {
        "category": "routed_wrong",
        "rationale": (f"Routed to '{observed_skill}' instead of expected '{expected_skill}'"),
        "details": details,
    }


# ---------------------------------------------------------------------------
# Single Run Executor
# ---------------------------------------------------------------------------


def run_single_orchestrator(
    repo_path: Path,
    commit_sha: str,
    test_case_path: Path,
    ground_truth: dict,
    payload_path: Path | None,
    output_base: Path,
    commit_meta: dict,
) -> dict:
    """Execute orchestrator.py for one (commit, test_case) pair."""
    tc_name = test_case_path.name if test_case_path.is_dir() else test_case_path.stem
    run_output_dir = output_base / commit_sha / tc_name
    run_output_dir.mkdir(parents=True, exist_ok=True)

    tool_path = repo_path / "skills" / "bio-orchestrator" / "orchestrator.py"
    timeout = harness_core.validate_timeout(ground_truth.get("TIMEOUT", "30"))

    # Build command based on test type
    routing_method = ground_truth.get("GROUND_TRUTH_ROUTING_METHOD", "")
    skill_list = ground_truth.get("GROUND_TRUTH_SKILL_LIST", "")
    provider = ground_truth.get("PROVIDER", "").strip().lower()
    provider_args: list[str] = []
    if provider in ("flock",):
        # ``--provider flock`` routes through an external open-source LLM.
        # Only the orchestrator honors this flag today; other command shapes
        # (--skills, --list-skills) ignore it. Tests that set PROVIDER: flock
        # are the ONLY ones that exercise the LLM routing path where prompt
        # injection in the user query can meaningfully change behavior.
        provider_args = ["--provider", provider]

    if routing_method == "list_skills":
        cmd = [sys.executable, str(tool_path), "--list-skills"]
    elif skill_list:
        # Multi-skill composition mode: --skills A,B,C
        # The skill list is validated to contain only comma-separated
        # lowercase identifiers (no shell metacharacters, no path
        # separators). Empty entries are dropped.
        skills_parsed = [s.strip() for s in str(skill_list).split(",") if s.strip()]
        for s in skills_parsed:
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", s):
                # Invalid skill name — let the tool reject it at runtime.
                # The harness doesn't fabricate a synthetic error.
                pass
        cmd = [
            sys.executable,
            str(tool_path),
            "--skills",
            ",".join(skills_parsed),
            "--input",
            ground_truth.get("INPUT_ARG", ""),
            "--output",
            str(run_output_dir / "tool_output"),
        ]
    elif payload_path:
        # Extension-based routing: pass the file
        cmd = [
            sys.executable,
            str(tool_path),
            "--input",
            str(payload_path),
            "--output",
            str(run_output_dir / "tool_output"),
        ]
    elif ground_truth.get("QUERY_TEXT"):
        # Keyword-based routing: pass the query text
        cmd = [
            sys.executable,
            str(tool_path),
            "--input",
            ground_truth["QUERY_TEXT"],
            "--output",
            str(run_output_dir / "tool_output"),
            *provider_args,
        ]
    elif ground_truth.get("SKILL_OVERRIDE"):
        # Force-skill tests
        cmd = [
            sys.executable,
            str(tool_path),
            "--input",
            ground_truth.get("INPUT_ARG", "test"),
            "--skill",
            ground_truth["SKILL_OVERRIDE"],
            "--output",
            str(run_output_dir / "tool_output"),
        ]
    else:
        cmd = [
            sys.executable,
            str(tool_path),
            "--input",
            ground_truth.get("INPUT_ARG", ""),
            "--output",
            str(run_output_dir / "tool_output"),
        ]

    # Execute
    execution = harness_core.capture_execution(
        cmd=cmd,
        cwd=repo_path,
        timeout=timeout,
    )

    # Save logs
    harness_core.save_execution_logs(execution, run_output_dir)

    # Analyze
    analysis = analyze_routing_output(
        execution.stdout,
        execution.stderr,
        execution.exit_code,
    )

    # Drift detection: scan the commit under audit for its actual skill
    # inventory and attach the diff against our pinned baseline as verdict
    # metadata. Informational only — does NOT affect the verdict category.
    try:
        live_inventory = discover_clawbio_skills(repo_path)
        drift = compute_inventory_drift(live_inventory)
        analysis["skill_inventory_drift"] = drift
        analysis["skill_inventory_live_counts"] = {
            "executable": len(live_inventory["executable"]),
            "stub": len(live_inventory["stub"]),
        }
    except OSError:
        # Drift detection must never fail the verdict — it is metadata.
        analysis["skill_inventory_drift"] = None

    # Score
    verdict = score_routing_verdict(ground_truth, analysis, execution)

    # Determine driver path for chain of custody
    driver_path = (
        test_case_path / "ground_truth.txt" if test_case_path.is_dir() else test_case_path
    )

    # Build verdict document
    verdict_doc = harness_core.build_verdict_doc(
        benchmark_name=BENCHMARK_NAME,
        benchmark_version=BENCHMARK_VERSION,
        commit_meta=commit_meta,
        test_case_name=tc_name,
        ground_truth=ground_truth,
        ground_truth_refs=GROUND_TRUTH_REFS,
        execution=execution,
        outputs={"routing_analysis": analysis},
        report_analysis=analysis,
        verdict=verdict,
        driver_path=driver_path,
        payload_path=payload_path,
    )

    # Save
    harness_core.save_verdict(verdict_doc, run_output_dir)
    return verdict_doc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    harness_core.run_harness_main(
        benchmark_name=BENCHMARK_NAME,
        benchmark_version=BENCHMARK_VERSION,
        default_inputs_dir="orchestrator",
        run_single_fn=run_single_orchestrator,
        rubric_categories=RUBRIC_CATEGORIES,
        pass_categories=PASS_CATEGORIES,
        fail_categories=FAIL_CATEGORIES,
        ground_truth_refs=GROUND_TRUTH_REFS,
        category_legend=CATEGORY_LEGEND,
        description="ClawBio Bio-Orchestrator Benchmark Harness",
    )


if __name__ == "__main__":
    main()
