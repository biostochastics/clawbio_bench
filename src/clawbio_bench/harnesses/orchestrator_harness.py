#!/usr/bin/env python3
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
import sys
from pathlib import Path

from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "bio-orchestrator"
BENCHMARK_VERSION = "1.0.0"

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

# Skills that have actual Python implementations (not just SKILL.md)
# Verified 2026-04-04 by scanning skills/*/*.py
EXECUTABLE_SKILLS = {
    "equity-scorer",  # equity_scorer.py + api.py
    "pharmgx-reporter",  # pharmgx_reporter.py + api.py
    "nutrigx_advisor",  # nutrigx_advisor.py + 5 modules
    "bio-orchestrator",  # orchestrator.py
    "claw-metagenomics",  # metagenomics_profiler.py
    "claw-ancestry-pca",  # ancestry_pca.py
    "scrna-orchestrator",  # scrna_orchestrator.py
    "genome-compare",  # genome_compare.py + api.py
    "clinpgx",  # clinpgx.py + api.py
    "gwas-prs",  # gwas_prs.py + api.py
    "gwas-lookup",  # gwas_lookup.py
    "profile-report",  # profile_report.py
    "data-extractor",  # data_extractor.py + api.py
    "ukb-navigator",  # ukb_navigator.py
    "galaxy-bridge",  # tool_recommender.py + api.py + galaxy_bridge.py
}

# Skills that are stubs (SKILL.md only, no Python implementation)
# Verified 2026-04-04
STUB_SKILLS = {
    "vcf-annotator",
    "struct-predictor",
    "lit-synthesizer",
    "seq-wrangler",
    "repro-enforcer",
    "claw-semantic-sim",
    "labstep",
    "drug-photo",
}

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
    analysis = {
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
    # (Codex review: FLock may print braces in reasoning before the final JSON)
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
                analysis["selected_skill"] = data.get("detected_skill")
                analysis["detection_method"] = data.get("detection_method")
                if analysis["selected_skill"]:
                    analysis["is_stub"] = analysis["selected_skill"] in STUB_SKILLS
                    analysis["is_executable"] = analysis["selected_skill"] in EXECUTABLE_SKILLS
    except (json.JSONDecodeError, ValueError):
        pass

    # Check stderr for warnings (Crush review: use specific patterns)
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
    ground_truth.get("GROUND_TRUTH_EXECUTABLE", "true").lower()
    routing_method = ground_truth.get("GROUND_TRUTH_ROUTING_METHOD", "")
    expected_exit = int(ground_truth.get("EXPECTED_EXIT_CODE", "0"))
    ground_truth.get("FINDING_CATEGORY", "")

    observed_skill = analysis.get("selected_skill")
    exit_code = execution.exit_code

    details = {
        "expected_skill": expected_skill,
        "observed_skill": observed_skill,
        "expected_exit_code": expected_exit,
        "observed_exit_code": exit_code,
        "detection_method": analysis.get("detection_method"),
        "routing_method": routing_method,
        "is_stub": analysis.get("is_stub"),
        "warning_emitted": analysis.get("warning_emitted", False),
        "json_parsed": analysis.get("json_parsed", False),
    }

    # ── Crash detection ──
    if exit_code != 0 and exit_code != expected_exit:
        # Check if it's a traceback (crash) vs clean error
        if "Traceback" in execution.stderr or "Error" in execution.stderr:
            return {
                "category": "unroutable_crash",
                "rationale": (f"Tool crashed (exit {exit_code}) with traceback"),
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
        # Correct routing — but check if it's a stub
        if analysis.get("is_stub"):
            if analysis.get("warning_emitted"):
                return {
                    "category": "stub_warned",
                    "rationale": (f"Correctly routed to stub '{observed_skill}' with warning"),
                    "details": details,
                }
            return {
                "category": "stub_silent",
                "rationale": (
                    f"Correctly routed to stub '{observed_skill}' but NO warning that it's a stub"
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
    commit_short = commit_sha[:8]
    run_output_dir = output_base / commit_short / tc_name
    run_output_dir.mkdir(parents=True, exist_ok=True)

    tool_path = repo_path / "skills" / "bio-orchestrator" / "orchestrator.py"
    timeout = int(ground_truth.get("TIMEOUT", "30"))

    # Build command based on test type
    routing_method = ground_truth.get("GROUND_TRUTH_ROUTING_METHOD", "")

    if routing_method == "list_skills":
        cmd = [sys.executable, str(tool_path), "--list-skills"]
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


def main():
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
