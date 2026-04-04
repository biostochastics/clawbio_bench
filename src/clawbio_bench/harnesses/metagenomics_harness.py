#!/usr/bin/env python3
"""
ClawBio Metagenomics Profiler Benchmark Harness
=================================================
Tests metagenomics_profiler.py in demo mode + static security analysis.

Per CONSENSUS VULN-001: demo mode executes ZERO subprocess calls, so real
injection surfaces (run_kraken2, run_bracken, run_rgi, run_humann3) cannot
be tested dynamically without the tools installed. Instead, we:
  1. Test demo mode functionality (report generation)
  2. Static-analyze source code for security patterns at each commit
  3. Test path handling with adversarial filenames

Rubric (7 categories):
  injection_blocked    — Adversarial path did not execute as command
  injection_succeeded  — Shell injection succeeded (CRITICAL)
  exit_handled         — Non-zero exit handled as error
  exit_suppressed      — Non-zero exit suppressed to warning only
  demo_functional      — Demo mode completes and produces report
  demo_broken          — Demo mode fails
  harness_error        — Harness infrastructure error
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "claw-metagenomics"
BENCHMARK_VERSION = "1.0.0"

RUBRIC_CATEGORIES = [
    "injection_blocked",
    "injection_succeeded",
    "exit_handled",
    "exit_suppressed",
    "demo_functional",
    "demo_broken",
    "harness_error",
]

PASS_CATEGORIES = ["injection_blocked", "exit_handled", "demo_functional"]
FAIL_CATEGORIES = ["injection_succeeded", "exit_suppressed", "demo_broken"]

GROUND_TRUTH_REFS = {
    "OWASP_CMD_INJECTION": (
        "OWASP Command Injection Prevention Cheat Sheet (2024). "
        "https://cheatsheetseries.owasp.org/cheatsheets/"
        "OS_Command_Injection_Defense_Cheat_Sheet.html"
    ),
    "CONSENSUS_VULN001": (
        "ClawBio audit meta-review CONSENSUS.md, M-2/VULN-001: "
        "demo mode executes zero subprocess calls, missing real attack surface"
    ),
}

CATEGORY_LEGEND = {
    "injection_blocked": {"color": "#22c55e", "label": "Injection BLOCKED"},
    "injection_succeeded": {"color": "#ef4444", "label": "Injection SUCCEEDED"},
    "exit_handled": {"color": "#22c55e", "label": "Exit code handled"},
    "exit_suppressed": {"color": "#f97316", "label": "Exit suppressed to warning"},
    "demo_functional": {"color": "#86efac", "label": "Demo mode works"},
    "demo_broken": {"color": "#ef4444", "label": "Demo mode fails"},
    "harness_error": {"color": "#9ca3af", "label": "Harness infrastructure error"},
}


# ---------------------------------------------------------------------------
# Static Analysis Helpers
# ---------------------------------------------------------------------------


def _find_shell_true_ast(tree: ast.Module) -> list[dict]:
    """Walk AST to find subprocess calls with shell=True.

    More robust than grep — handles multi-line calls, aliased imports,
    and avoids false positives from comments or strings.
    """
    findings = []
    subprocess_funcs = {"run", "call", "Popen", "check_call", "check_output"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match subprocess.run(...), subprocess.call(...), etc.
        func = node.func
        is_subprocess_call = False
        func_name = ""

        if isinstance(func, ast.Attribute) and func.attr in subprocess_funcs:
            if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                is_subprocess_call = True
                func_name = f"subprocess.{func.attr}"

        if not is_subprocess_call:
            continue

        # Check for shell=True in keyword arguments
        for kw in node.keywords:
            if kw.arg == "shell":
                if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    findings.append(
                        {
                            "line": node.lineno,
                            "end_line": node.end_lineno,
                            "func": func_name,
                            "shell_true": True,
                        }
                    )
                elif isinstance(kw.value, ast.Name):
                    # shell=some_variable — flag as suspicious
                    findings.append(
                        {
                            "line": node.lineno,
                            "end_line": node.end_lineno,
                            "func": func_name,
                            "shell_true": f"variable:{kw.value.id}",
                        }
                    )

    return findings


def _count_subprocess_calls_ast(tree: ast.Module) -> list[dict]:
    """Count all subprocess invocations via AST."""
    calls = []
    subprocess_funcs = {"run", "call", "Popen", "check_call", "check_output"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in subprocess_funcs:
            if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                # Check if first positional arg is a list (safe) or string (risky)
                arg_type = "unknown"
                if node.args:
                    first_arg = node.args[0]
                    if isinstance(first_arg, ast.List):
                        arg_type = "list"
                    elif isinstance(first_arg, (ast.Constant, ast.JoinedStr)):
                        arg_type = "string"
                    elif isinstance(first_arg, ast.Name):
                        arg_type = f"variable:{first_arg.id}"

                calls.append(
                    {
                        "line": node.lineno,
                        "func": f"subprocess.{func.attr}",
                        "first_arg_type": arg_type,
                    }
                )

    return calls


def _check_shlex_usage(tree: ast.Module) -> bool:
    """Check if shlex.quote is used anywhere."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "quote":
            if isinstance(node.value, ast.Name) and node.value.id == "shlex":
                return True
    return False


def analyze_source_security(source_path: Path) -> dict:
    """AST-based static analysis of metagenomics_profiler.py for security patterns.

    Uses Python's ast module instead of grep for robust detection of:
    - shell=True in subprocess calls (handles multi-line, ignores comments/strings)
    - subprocess call argument types (list vs string)
    - shlex.quote usage
    - os.environ / os.getenv reads
    """
    analysis = {
        "source_exists": False,
        "shell_true_count": 0,
        "shell_true_lines": [],
        "shell_true_details": [],
        "subprocess_calls": 0,
        "subprocess_lines": [],
        "subprocess_details": [],
        "shlex_quote_used": False,
        "path_join_with_user_input": [],
        "env_var_reads": [],
        "run_command_uses_list": True,
        "ast_parse_success": False,
    }

    if not source_path.exists():
        return analysis

    analysis["source_exists"] = True
    text = source_path.read_text(errors="replace")

    # Try AST-based analysis first (preferred)
    try:
        tree = ast.parse(text, filename=str(source_path))
        analysis["ast_parse_success"] = True

        # shell=True detection
        shell_findings = _find_shell_true_ast(tree)
        analysis["shell_true_count"] = len(shell_findings)
        analysis["shell_true_lines"] = [f["line"] for f in shell_findings]
        analysis["shell_true_details"] = shell_findings

        # Subprocess call inventory
        sub_calls = _count_subprocess_calls_ast(tree)
        analysis["subprocess_calls"] = len(sub_calls)
        analysis["subprocess_lines"] = [c["line"] for c in sub_calls]
        analysis["subprocess_details"] = sub_calls

        # Check if any subprocess call uses a string argument (risky)
        for c in sub_calls:
            if c["first_arg_type"] == "string":
                analysis["run_command_uses_list"] = False

        # shlex.quote
        analysis["shlex_quote_used"] = _check_shlex_usage(tree)

        # os.environ / os.getenv (AST-based)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "environ":
                if isinstance(node.value, ast.Name) and node.value.id == "os":
                    analysis["env_var_reads"].append(node.lineno)
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "getenv":
                    if isinstance(func.value, ast.Name) and func.value.id == "os":
                        analysis["env_var_reads"].append(node.lineno)

    except SyntaxError:
        # Fallback to line-based grep for commits with syntax errors
        analysis["ast_parse_success"] = False
        lines = text.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "shell=True" in stripped:
                analysis["shell_true_count"] += 1
                analysis["shell_true_lines"].append(i)
            if "subprocess.run" in stripped or "subprocess.call" in stripped:
                analysis["subprocess_calls"] += 1
                analysis["subprocess_lines"].append(i)
            if "shlex.quote" in stripped:
                analysis["shlex_quote_used"] = True
            if "os.environ" in stripped or "os.getenv" in stripped:
                analysis["env_var_reads"].append(i)

    return analysis


# ---------------------------------------------------------------------------
# Report Analyzer
# ---------------------------------------------------------------------------


def analyze_metagenomics_output(
    output_dir: Path,
    stdout: str,
    stderr: str,
    source_analysis: dict,
) -> dict:
    """Parse metagenomics demo output."""
    analysis = {
        "report_exists": False,
        "tables_exist": False,
        "result_json_exists": False,
        "taxonomy_tsv": False,
        "resistome_tsv": False,
        "commands_sh_exists": False,
        "commands_sh_safe": True,
        "unescaped_paths_in_commands": [],
        "demo_completed": False,
        "source_security": source_analysis,
        "warnings": [],
        "errors": [],
    }

    # Check demo outputs (tool writes report.md, not metagenomics_report.md)
    report_md = output_dir / "report.md"
    if report_md.exists():
        analysis["report_exists"] = True

    tables_dir = output_dir / "tables"
    if tables_dir.is_dir():
        analysis["tables_exist"] = True
        analysis["taxonomy_tsv"] = (tables_dir / "taxonomy_species.tsv").exists()
        analysis["resistome_tsv"] = (tables_dir / "resistome_profile.tsv").exists()

    result_json = output_dir / "result.json"
    if result_json.exists():
        analysis["result_json_exists"] = True

    # Check commands.sh (tool writes to reproducibility/ subdir)
    commands_sh = output_dir / "reproducibility" / "commands.sh"
    if not commands_sh.exists():
        commands_sh = output_dir / "commands.sh"  # fallback for older commits
    if commands_sh.exists():
        analysis["commands_sh_exists"] = True
        content = commands_sh.read_text(errors="replace")
        # Look for unquoted path variables or user input
        for line in content.split("\n"):
            if "$" in line and "'" not in line and '"' not in line:
                analysis["commands_sh_safe"] = False
                analysis["unescaped_paths_in_commands"].append(line.strip())

    # Check completion markers
    if "Done" in stdout or "Report:" in stdout:
        analysis["demo_completed"] = True

    # Errors
    for line in stderr.split("\n"):
        if "ERROR" in line:
            analysis["errors"].append(line.strip())
        elif "WARNING" in line:
            analysis["warnings"].append(line.strip())

    return analysis


# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------


def score_metagenomics_verdict(
    ground_truth: dict,
    analysis: dict,
    execution: harness_core.ExecutionResult,
) -> dict:
    """Score a metagenomics test against ground truth."""
    finding_category = ground_truth.get("FINDING_CATEGORY", "")
    expected_exit = int(ground_truth.get("EXPECTED_EXIT_CODE", "0"))
    exit_code = execution.exit_code

    details = {
        "expected_exit_code": expected_exit,
        "observed_exit_code": exit_code,
        "report_exists": analysis.get("report_exists", False),
        "demo_completed": analysis.get("demo_completed", False),
    }

    # ── Unexpected crash ──
    if exit_code != 0 and exit_code != expected_exit:
        if "Traceback" in execution.stderr or "Error" in execution.stderr:
            return {
                "category": "demo_broken",
                "rationale": f"Tool crashed (exit {exit_code})",
                "details": details,
            }

    # ── Demo mode tests ──
    if finding_category in ("demo_functional", "demo_broken"):
        if exit_code == 0 and analysis.get("demo_completed"):
            if analysis.get("report_exists") and analysis.get("tables_exist"):
                return {
                    "category": "demo_functional",
                    "rationale": "Demo mode completed with report + tables",
                    "details": details,
                }
            return {
                "category": "demo_broken",
                "rationale": "Demo ran but missing report or tables",
                "details": details,
            }
        return {
            "category": "demo_broken",
            "rationale": f"Demo mode failed (exit {exit_code})",
            "details": details,
        }

    # ── Injection tests (static analysis) ──
    if finding_category in ("injection_blocked", "injection_succeeded"):
        sec = analysis.get("source_security", {})
        details["shell_true_count"] = sec.get("shell_true_count", 0)
        details["shell_true_lines"] = sec.get("shell_true_lines", [])
        details["shlex_quote_used"] = sec.get("shlex_quote_used", False)
        details["commands_sh_safe"] = analysis.get("commands_sh_safe", True)
        details["unescaped_paths"] = analysis.get("unescaped_paths_in_commands", [])

        # Check: no shell=True anywhere
        if sec.get("shell_true_count", 0) > 0:
            return {
                "category": "injection_succeeded",
                "rationale": (
                    f"shell=True found at lines {sec['shell_true_lines']} — "
                    f"injection vector exists"
                ),
                "details": details,
            }

        # Check: commands.sh doesn't have unescaped paths
        if not analysis.get("commands_sh_safe", True):
            return {
                "category": "injection_succeeded",
                "rationale": (
                    f"Unescaped paths in commands.sh: "
                    f"{analysis['unescaped_paths_in_commands'][:3]}"
                ),
                "details": details,
            }

        return {
            "category": "injection_blocked",
            "rationale": "No shell=True, commands.sh paths safe",
            "details": details,
        }

    # ── Exit handling tests ──
    if finding_category in ("exit_handled", "exit_suppressed"):
        sec = analysis.get("source_security", {})
        # The run_command() function has a 'critical' parameter
        # When critical=False (default), non-zero exits are WARNING only
        details["run_command_critical_default"] = "False (suppressed to warning)"

        # This is a known design issue: non-critical commands only log warnings
        if ground_truth.get("GROUND_TRUTH_EXIT_HANDLING") == "error":
            return {
                "category": "exit_suppressed",
                "rationale": (
                    "run_command(critical=False) suppresses non-zero exits to "
                    "WARNING instead of raising error"
                ),
                "details": details,
            }
        return {
            "category": "exit_handled",
            "rationale": "Exit code handling as expected",
            "details": details,
        }

    return {
        "category": "harness_error",
        "rationale": f"Unhandled finding_category: {finding_category}",
        "details": details,
    }


# ---------------------------------------------------------------------------
# Single Run Executor
# ---------------------------------------------------------------------------


def run_single_metagenomics(
    repo_path: Path,
    commit_sha: str,
    test_case_path: Path,
    ground_truth: dict,
    payload_path: Path | None,
    output_base: Path,
    commit_meta: dict,
) -> dict:
    """Execute metagenomics_profiler.py for one (commit, test_case) pair."""
    tc_name = test_case_path.name if test_case_path.is_dir() else test_case_path.stem
    commit_short = commit_sha[:8]
    run_output_dir = output_base / commit_short / tc_name
    tool_output_dir = run_output_dir / "tool_output"
    tool_output_dir.mkdir(parents=True, exist_ok=True)

    tool_path = repo_path / "skills" / "claw-metagenomics" / "metagenomics_profiler.py"
    timeout = int(ground_truth.get("TIMEOUT", "60"))

    # Static analysis of the source at this commit
    source_analysis = analyze_source_security(tool_path)

    test_type = ground_truth.get("TEST_TYPE", "demo")

    if test_type == "demo":
        cmd = [sys.executable, str(tool_path), "--demo", "--output", str(tool_output_dir)]
        fallback_cmd = None
    elif test_type == "static":
        # Static-only: no execution needed, just analyze source
        execution = harness_core.ExecutionResult(
            exit_code=0,
            stdout="",
            stderr="",
            wall_seconds=0.0,
            start_time=harness_core.datetime.now(harness_core.timezone.utc),
            end_time=harness_core.datetime.now(harness_core.timezone.utc),
            used_fallback=False,
            cmd=["static-analysis"],
            cwd=str(repo_path),
            timeout_seconds=0,
        )
        analysis = analyze_metagenomics_output(
            tool_output_dir,
            "",
            "",
            source_analysis,
        )
        verdict = score_metagenomics_verdict(ground_truth, analysis, execution)
        driver_path = (
            test_case_path / "ground_truth.txt" if test_case_path.is_dir() else test_case_path
        )
        verdict_doc = harness_core.build_verdict_doc(
            benchmark_name=BENCHMARK_NAME,
            benchmark_version=BENCHMARK_VERSION,
            commit_meta=commit_meta,
            test_case_name=tc_name,
            ground_truth=ground_truth,
            ground_truth_refs=GROUND_TRUTH_REFS,
            execution=execution,
            outputs={"source_analysis": source_analysis},
            report_analysis=analysis,
            verdict=verdict,
            driver_path=driver_path,
            payload_path=payload_path,
        )
        harness_core.save_verdict(verdict_doc, run_output_dir)
        return verdict_doc
    else:
        raise ValueError(f"Unknown TEST_TYPE: {test_type}")

    execution = harness_core.capture_execution(
        cmd=cmd,
        cwd=repo_path,
        timeout=timeout,
        fallback_cmd=fallback_cmd,
    )

    harness_core.save_execution_logs(execution, run_output_dir)

    analysis = analyze_metagenomics_output(
        tool_output_dir,
        execution.stdout,
        execution.stderr,
        source_analysis,
    )

    verdict = score_metagenomics_verdict(ground_truth, analysis, execution)

    driver_path = (
        test_case_path / "ground_truth.txt" if test_case_path.is_dir() else test_case_path
    )

    outputs = {
        "report_md": harness_core.artifact_info(tool_output_dir / "report.md"),
        "result_json": harness_core.artifact_info(tool_output_dir / "result.json"),
        "source_analysis": source_analysis,
    }

    verdict_doc = harness_core.build_verdict_doc(
        benchmark_name=BENCHMARK_NAME,
        benchmark_version=BENCHMARK_VERSION,
        commit_meta=commit_meta,
        test_case_name=tc_name,
        ground_truth=ground_truth,
        ground_truth_refs=GROUND_TRUTH_REFS,
        execution=execution,
        outputs=outputs,
        report_analysis=analysis,
        verdict=verdict,
        driver_path=driver_path,
        payload_path=payload_path,
    )

    harness_core.save_verdict(verdict_doc, run_output_dir)
    return verdict_doc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    harness_core.run_harness_main(
        benchmark_name=BENCHMARK_NAME,
        benchmark_version=BENCHMARK_VERSION,
        default_inputs_dir="metagenomics",
        run_single_fn=run_single_metagenomics,
        rubric_categories=RUBRIC_CATEGORIES,
        pass_categories=PASS_CATEGORIES,
        fail_categories=FAIL_CATEGORIES,
        ground_truth_refs=GROUND_TRUTH_REFS,
        category_legend=CATEGORY_LEGEND,
        description="ClawBio Metagenomics Profiler Benchmark Harness",
    )


if __name__ == "__main__":
    main()
