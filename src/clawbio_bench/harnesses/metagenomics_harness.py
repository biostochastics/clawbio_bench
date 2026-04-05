#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ClawBio Metagenomics Profiler Benchmark Harness
=================================================
Tests metagenomics_profiler.py in demo mode + static security analysis.

Per VULN-001: demo mode executes ZERO subprocess calls, so real
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "claw-metagenomics"
BENCHMARK_VERSION = "0.1.0"

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
    "VULN001": (
        "ClawBio audit M-2/VULN-001: "
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


def _resolve_import_aliases(tree: ast.Module) -> dict[str, str]:
    """Build a mapping of local names to their module origins.

    Handles aliased imports like `import subprocess as sp` and
    direct imports like `from subprocess import run`.
    """
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name
                aliases[local_name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local_name = alias.asname or alias.name
                aliases[local_name] = f"{module}.{alias.name}"
    return aliases


# Shell-executing os functions to detect
_OS_SHELL_FUNCS = frozenset({"system", "popen"})


def _find_shell_true_ast(tree: ast.Module) -> list[dict]:
    """Walk AST to find subprocess calls with shell=True.

    Handles aliased imports (import subprocess as sp), direct imports
    (from subprocess import run), and os shell functions.
    """
    findings = []
    subprocess_funcs = {"run", "call", "Popen", "check_call", "check_output"}
    aliases = _resolve_import_aliases(tree)

    subprocess_aliases = {name for name, origin in aliases.items() if origin == "subprocess"}
    direct_subprocess_funcs = {}
    for local_name, origin in aliases.items():
        parts = origin.split(".")
        if len(parts) == 2 and parts[0] == "subprocess" and parts[1] in subprocess_funcs:
            direct_subprocess_funcs[local_name] = origin

    os_aliases = {name for name, origin in aliases.items() if origin == "os"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        is_subprocess_call = False
        func_name = ""

        # Pattern 1: module.func() — subprocess.run(), sp.run()
        if isinstance(func, ast.Attribute) and func.attr in subprocess_funcs:
            if isinstance(func.value, ast.Name):
                if func.value.id in subprocess_aliases or func.value.id == "subprocess":
                    is_subprocess_call = True
                    func_name = f"subprocess.{func.attr}"

        # Pattern 2: direct import — run() from "from subprocess import run"
        if isinstance(func, ast.Name) and func.id in direct_subprocess_funcs:
            is_subprocess_call = True
            func_name = direct_subprocess_funcs[func.id]

        # Pattern 3: os shell functions — always shell execution
        if isinstance(func, ast.Attribute) and func.attr in _OS_SHELL_FUNCS:
            if isinstance(func.value, ast.Name):
                if func.value.id in os_aliases or func.value.id == "os":
                    findings.append(
                        {
                            "line": node.lineno,
                            "end_line": node.end_lineno,
                            "func": f"os.{func.attr}",
                            "shell_true": True,
                        }
                    )
                    continue

        if not is_subprocess_call:
            continue

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
    """Count all subprocess invocations via AST, including aliased imports."""
    calls = []
    subprocess_funcs = {"run", "call", "Popen", "check_call", "check_output"}
    aliases = _resolve_import_aliases(tree)

    subprocess_aliases = {name for name, origin in aliases.items() if origin == "subprocess"}
    direct_subprocess_funcs = {}
    for local_name, origin in aliases.items():
        parts = origin.split(".")
        if len(parts) == 2 and parts[0] == "subprocess" and parts[1] in subprocess_funcs:
            direct_subprocess_funcs[local_name] = origin

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        matched = False
        func_name = ""

        if isinstance(func, ast.Attribute) and func.attr in subprocess_funcs:
            if isinstance(func.value, ast.Name):
                if func.value.id in subprocess_aliases or func.value.id == "subprocess":
                    matched = True
                    func_name = f"subprocess.{func.attr}"

        if isinstance(func, ast.Name) and func.id in direct_subprocess_funcs:
            matched = True
            func_name = direct_subprocess_funcs[func.id]

        if not matched:
            continue

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
                "func": func_name,
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


def _find_run_command_critical_default(tree: ast.Module) -> str | None:
    """Return the default value of the ``critical`` parameter on any
    ``run_command`` function definition found in the module.

    Used to detect whether non-zero subprocess exits are suppressed to
    warnings (``critical=False``) or raised as errors (``critical=True``).
    Returns:
      * ``"True"`` / ``"False"`` — literal default
      * ``"<unknown>"`` — parameter exists but default is non-literal
      * ``None``         — function or parameter not found (tool does not
                           have the run_command indirection at this commit)

    Walks both ``FunctionDef`` and ``AsyncFunctionDef`` so a future async
    migration of the helper would still be analyzed correctly.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "run_command":
            continue
        args = node.args
        # Map every arg name to its default. Defaults align to the TAIL of
        # the positional-or-keyword args list, and kw-only defaults are a
        # separate list aligned to kwonlyargs.
        pos_args = args.args
        pos_defaults = args.defaults
        kwonly_args = args.kwonlyargs
        kw_defaults = args.kw_defaults
        # Build (arg_name, default_node) pairs for every arg that has one.
        pairs: list[tuple[str, ast.expr | None]] = []
        # Positional-or-keyword: defaults align to the last N.
        n_pos_defaults = len(pos_defaults)
        for i, arg in enumerate(pos_args):
            default_idx = i - (len(pos_args) - n_pos_defaults)
            if default_idx >= 0:
                pairs.append((arg.arg, pos_defaults[default_idx]))
        # Kw-only: one-to-one with kw_defaults (None means required).
        for arg, default in zip(kwonly_args, kw_defaults, strict=False):
            if default is not None:
                pairs.append((arg.arg, default))

        for name, default in pairs:
            if name != "critical":
                continue
            if isinstance(default, ast.Constant):
                if default.value is True:
                    return "True"
                if default.value is False:
                    return "False"
                return repr(default.value)
            return "<unknown>"
        # run_command exists but no critical parameter — nothing to report
        return None
    return None


def analyze_source_security(source_path: Path) -> dict[str, Any]:
    """AST-based static analysis of metagenomics_profiler.py for security patterns.

    Uses Python's ast module instead of grep for robust detection of:
    - shell=True in subprocess calls (handles multi-line, ignores comments/strings)
    - subprocess call argument types (list vs string)
    - shlex.quote usage
    - os.environ / os.getenv reads
    """
    analysis: dict[str, Any] = {
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
        # None when the tool does not define run_command at this commit;
        # "True"/"False" or "<unknown>" otherwise. Consumed by the scoring
        # engine to decide whether exits are suppressed or raised.
        "run_command_critical_default": None,
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

        # run_command critical= default (None if run_command isn't defined)
        analysis["run_command_critical_default"] = _find_run_command_critical_default(tree)

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
    source_analysis: dict[str, Any],
) -> dict[str, Any]:
    """Parse metagenomics demo output."""
    analysis: dict[str, Any] = {
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
    # Match on the canonical Python traceback header and line-anchored error
    # markers instead of a naive ``"Error" in stderr`` substring, which would
    # false-match benign text like "no errors found".
    if exit_code != 0 and exit_code != expected_exit:
        stderr = execution.stderr or ""
        is_crash = "Traceback (most recent call last):" in stderr
        if not is_crash:
            for _line in stderr.split("\n"):
                _stripped = _line.strip()
                if _stripped.startswith(("Error:", "Exception:", "Fatal:", "FATAL:", "CRITICAL:")):
                    is_crash = True
                    break
        if is_crash:
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
    # Previously this branch hardcoded ``"False (suppressed to warning)"``
    # without re-verifying per-commit. That meant the scorer would still
    # report ``exit_suppressed`` even AFTER the audited tool fixed
    # ``critical=True`` — a false finding that masks the fix.
    #
    # We now read the AST-derived default from ``source_security`` and
    # score three distinct states:
    #   * default == "True"        → exit_handled (tool raises on non-zero)
    #   * default == "False"       → exit_suppressed (classic audit finding)
    #   * default in (None,
    #                 "<unknown>") → exit_handled-with-note (nothing to
    #                                credit or debit; run_command is absent
    #                                or the default is not a literal)
    if finding_category in ("exit_handled", "exit_suppressed"):
        sec = analysis.get("source_security", {})
        critical_default = sec.get("run_command_critical_default")
        details["run_command_critical_default"] = critical_default

        if ground_truth.get("GROUND_TRUTH_EXIT_HANDLING") == "error":
            if critical_default == "False":
                return {
                    "category": "exit_suppressed",
                    "rationale": (
                        "run_command(critical=False) suppresses non-zero exits to "
                        "WARNING instead of raising error"
                    ),
                    "details": details,
                }
            if critical_default == "True":
                return {
                    "category": "exit_handled",
                    "rationale": (
                        "run_command(critical=True) — non-zero exits raise; "
                        "tool fixed the exit_suppressed finding at this commit"
                    ),
                    "details": details,
                }
            # None / "<unknown>" — no basis to flag or credit
            return {
                "category": "exit_handled",
                "rationale": (
                    f"run_command indirection not found at this commit "
                    f"(critical_default={critical_default!r}); nothing to score"
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
    run_output_dir = output_base / commit_sha / tc_name
    tool_output_dir = run_output_dir / "tool_output"
    tool_output_dir.mkdir(parents=True, exist_ok=True)

    tool_path = repo_path / "skills" / "claw-metagenomics" / "metagenomics_profiler.py"
    timeout = harness_core.validate_timeout(ground_truth.get("TIMEOUT", "60"))

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
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
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
        return harness_core.harness_error_verdict(
            tc_name,
            commit_meta,
            ValueError(f"Unknown TEST_TYPE: {test_type}"),
            ground_truth=ground_truth,
        )

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


def main() -> None:
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
