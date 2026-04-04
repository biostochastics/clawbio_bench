#!/usr/bin/env python3
"""
ClawBio Benchmark Harness — Shared Core
=========================================
Extracted from the operational PGx benchmark harness (clawbio-pgx-benchmark/).
Provides git operations, ground truth parsing, execution capture, verdict
aggregation, and CLI modes shared by all tool-specific harnesses.

Two test case models supported:
  Model A: Self-contained file with # KEY: value headers (PGx .txt files)
  Model B: Directory with ground_truth.txt driver + payload sidecar file(s)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants & Exceptions
# ---------------------------------------------------------------------------

CORE_VERSION = "1.1.0"


class BenchmarkConfigError(Exception):
    """Raised when benchmark configuration is invalid (missing repo, bad args)."""


class DirtyRepoError(Exception):
    """Raised when the target repo has uncommitted changes and --allow-dirty not set."""


# ---------------------------------------------------------------------------
# Identity & Integrity
# ---------------------------------------------------------------------------


def sha256_file(filepath: Path) -> str:
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_string(s: str) -> str:
    """Compute SHA-256 of a string."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def artifact_info(filepath: Path) -> dict:
    """Return {exists, sha256, size_bytes} for chain of custody."""
    if not filepath.exists():
        return {"exists": False, "sha256": None, "size_bytes": 0}
    return {
        "exists": True,
        "sha256": sha256_file(filepath),
        "size_bytes": filepath.stat().st_size,
    }


# ---------------------------------------------------------------------------
# Git Operations
# ---------------------------------------------------------------------------


def get_commit_metadata(repo_path: Path, commit_sha: str) -> dict:
    """Get commit date, message, and full SHA."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", "-1", "--format=%H|%ai|%s", commit_sha],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split("|", 2)
            return {
                "full_sha": parts[0],
                "date": parts[1],
                "message": parts[2] if len(parts) > 2 else "",
            }
    except Exception:
        pass
    return {"full_sha": commit_sha, "date": "unknown", "message": "unknown"}


def get_all_commits(repo_path: Path, branch: str = "main") -> list[str]:
    """Get all commit SHAs in chronological order (oldest first)."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "log", "--format=%H", "--reverse", branch],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise BenchmarkConfigError(
            f"git log failed for branch '{branch}': {result.stderr.strip()}"
        )
    return [sha.strip() for sha in result.stdout.strip().split("\n") if sha.strip()]


def safe_checkout(repo_path: Path, commit_sha: str) -> bool:
    """Checkout a commit. Returns True on success."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "checkout", commit_sha, "--quiet"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode == 0


def restore_ref(repo_path: Path, starting_ref: str) -> None:
    """Restore to the starting ref (branch name or SHA)."""
    subprocess.run(
        ["git", "-C", str(repo_path), "checkout", starting_ref, "--quiet"],
        capture_output=True,
        timeout=15,
    )


def get_starting_ref(repo_path: Path) -> str:
    """Capture current ref for safe restoration."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    ref = result.stdout.strip() if result.returncode == 0 else "main"
    if ref == "HEAD":
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        ref = result.stdout.strip()
    return ref


def clean_workspace(repo_path: Path) -> None:
    """Reset tracked files + remove untracked to prevent contamination.

    Called between commits in longitudinal sweeps.
    Uses -fd (not -fdx) to preserve .gitignore'd files like .env.
    Checks for dirty working tree before first use.
    """
    subprocess.run(
        ["git", "-C", str(repo_path), "checkout", "--", "."],
        capture_output=True,
        timeout=15,
    )
    # Use -fd instead of -fdx to preserve .gitignore'd files (Codex/Gemini/Kimi review)
    subprocess.run(
        ["git", "-C", str(repo_path), "clean", "-fd"],
        capture_output=True,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# Ground Truth Parsing
# ---------------------------------------------------------------------------


def parse_ground_truth(ground_truth_path: Path) -> dict:
    """Parse # KEY: value headers from a ground truth file.

    Reads all lines starting with '#' at the top of the file.
    Extracts KEY: value pairs where KEY is any uppercase/underscore token.
    Stops at the first non-comment line (or EOF).
    """
    gt = {}
    with open(ground_truth_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("#"):
                break
            # Strip leading '#' characters and whitespace (Gemini review: lstrip)
            content = line.lstrip("#").strip()
            if ": " in content:
                key, _, value = content.partition(": ")
                key = key.strip()
                if key and (key.replace("_", "").replace("-", "").isalnum()):
                    gt[key] = value.strip()
    if not gt.get("FINDING_CATEGORY"):
        # NF-7: Warn when FINDING_CATEGORY is missing (Crush review)
        print(f"  WARNING: No FINDING_CATEGORY in {ground_truth_path.name}", file=sys.stderr)
    return gt


def resolve_test_case(test_case_path: Path) -> tuple[dict, Path | None]:
    """Resolve a test case to (ground_truth_dict, payload_path).

    Model A (file): Parse the file as both ground truth and payload.
    Model B (directory): Read ground_truth.txt + resolve PAYLOAD reference.

    Returns:
        (ground_truth, payload_path) where payload_path may be None
        for keyword-only tests.
    """
    if test_case_path.is_file():
        # Model A: self-contained file (PGx style)
        gt = parse_ground_truth(test_case_path)
        return gt, test_case_path

    if test_case_path.is_dir():
        # Model B: driver/sidecar directory
        driver = test_case_path / "ground_truth.txt"
        if not driver.exists():
            raise FileNotFoundError(
                f"No ground_truth.txt in test case directory: {test_case_path}"
            )
        gt = parse_ground_truth(driver)
        payload_name = gt.get("PAYLOAD")
        if payload_name:
            payload_path = (test_case_path / payload_name).resolve()
            # Path traversal validation (Kimi VULN-001)
            test_case_root = test_case_path.resolve()
            try:
                payload_path.relative_to(test_case_root)
            except ValueError as exc:
                raise ValueError(
                    f"Path traversal detected in PAYLOAD reference: "
                    f"'{payload_name}' escapes {test_case_root}"
                ) from exc
            if not payload_path.exists():
                raise FileNotFoundError(
                    f"Payload file not found: {payload_path} (referenced in {driver})"
                )
            return gt, payload_path
        # No payload (keyword-only test)
        return gt, None

    raise FileNotFoundError(f"Test case not found: {test_case_path}")


# ---------------------------------------------------------------------------
# Output Capture
# ---------------------------------------------------------------------------


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    wall_seconds: float
    start_time: datetime
    end_time: datetime
    used_fallback: bool
    cmd: list[str]
    cwd: str
    timeout_seconds: int

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "stdout_lines": self.stdout.count("\n"),
            "stdout_sha256": sha256_string(self.stdout),
            "stderr_lines": self.stderr.count("\n"),
            "stderr_sha256": sha256_string(self.stderr),
            "wall_seconds": round(self.wall_seconds, 3),
            "start_time_utc": self.start_time.isoformat(),
            "end_time_utc": self.end_time.isoformat(),
            "used_fallback": self.used_fallback,
            "cmd": self.cmd,
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
        }


def capture_execution(
    cmd: list[str],
    cwd: Path,
    timeout: int = 60,
    env: dict | None = None,
    fallback_cmd: list[str] | None = None,
) -> ExecutionResult:
    """Execute a command and capture all output.

    If the primary cmd fails with exit code 2 (argparse error) and
    fallback_cmd is provided, retries with fallback_cmd.
    """
    run_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if env:
        run_env.update(env)

    start_time = datetime.now(UTC)
    wall_start = time.monotonic()
    used_fallback = False

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            env=run_env,
        )
        # Tightened fallback: only retry when argparse specifically rejected
        # the flag we're trying to remove (NF-8 fix per Crush review)
        if (
            result.returncode == 2
            and fallback_cmd
            and "error: unrecognized arguments:" in result.stderr
            and "usage:" in result.stderr
        ):
            result = subprocess.run(
                fallback_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd),
                env=run_env,
            )
            used_fallback = True
            cmd = fallback_cmd
    except subprocess.TimeoutExpired:
        result = type(
            "R",
            (),
            {
                "returncode": -1,
                "stdout": "",
                "stderr": f"TIMEOUT after {timeout}s",
            },
        )()
    except Exception as exc:
        # Catch FileNotFoundError, PermissionError, OSError (Crush review)
        result = type(
            "R",
            (),
            {
                "returncode": -2,
                "stdout": "",
                "stderr": f"EXECUTION_ERROR: {type(exc).__name__}: {exc}",
            },
        )()

    wall_elapsed = time.monotonic() - wall_start
    end_time = datetime.now(UTC)

    return ExecutionResult(
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        wall_seconds=wall_elapsed,
        start_time=start_time,
        end_time=end_time,
        used_fallback=used_fallback,
        cmd=cmd,
        cwd=str(cwd),
        timeout_seconds=timeout,
    )


# ---------------------------------------------------------------------------
# Error-to-Verdict Conversion
# ---------------------------------------------------------------------------


def harness_error_verdict(
    test_case_name: str,
    commit_meta: dict,
    exception: Exception,
    ground_truth: dict | None = None,
) -> dict:
    """Produce a verdict with category='harness_error'.

    Never abort the matrix — always emit a verdict.
    """
    return {
        "test_case": {"name": test_case_name},
        "commit": commit_meta,
        "ground_truth": ground_truth or {},
        "verdict": {
            "category": "harness_error",
            "rationale": f"{type(exception).__name__}: {exception}",
            "details": {
                "exception_type": type(exception).__name__,
                "exception_message": str(exception),
                "traceback": traceback.format_exc(),
            },
        },
    }


# ---------------------------------------------------------------------------
# Manifest & Chain of Custody
# ---------------------------------------------------------------------------


def write_manifest(
    output_base: Path,
    benchmark_name: str,
    benchmark_version: str,
    repo_path: Path,
    commits: list[str],
    test_cases: list[Path],
    ground_truth_refs: dict[str, str],
    rubric_categories: list[str],
    pass_categories: list[str],
    fail_categories: list[str],
) -> dict:
    """Write manifest.json and return the manifest dict."""
    # Build test case inventory
    tc_inventory = []
    for tc in test_cases:
        if tc.is_file():
            tc_inventory.append(
                {
                    "name": tc.stem,
                    "type": "file",
                    "sha256": sha256_file(tc),
                }
            )
        elif tc.is_dir():
            entry = {"name": tc.name, "type": "directory", "files": {}}
            for f in sorted(tc.iterdir()):
                if f.is_file():
                    entry["files"][f.name] = sha256_file(f)
            tc_inventory.append(entry)

    manifest = {
        "harness_core_version": CORE_VERSION,
        "benchmark_name": benchmark_name,
        "benchmark_version": benchmark_version,
        "run_timestamp_utc": datetime.now(UTC).isoformat(),
        "repo_path": str(repo_path),
        "commits": commits,
        "commit_count": len(commits),
        "test_cases": tc_inventory,
        "test_case_count": len(test_cases),
        "total_runs": len(commits) * len(test_cases),
        "ground_truth_references": ground_truth_refs,
        "rubric_categories": rubric_categories,
        "pass_categories": pass_categories,
        "fail_categories": fail_categories,
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "hostname": platform.node(),
        },
    }
    with open(output_base / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def build_heatmap_data(
    verdicts: list[dict],
    category_legend: dict[str, dict],
) -> dict:
    """Build aggregated heatmap matrix from verdict list."""
    commits = []
    seen_commits: set[str] = set()
    test_cases = []
    seen_tests: set[str] = set()
    matrix = {}

    for v in verdicts:
        commit_sha = v.get("commit", {}).get("sha", "unknown")
        test_name = v.get("test_case", {}).get("name", "unknown")
        category = v.get("verdict", {}).get("category", "unknown")

        if commit_sha not in seen_commits:
            commits.append(
                {
                    "sha": commit_sha,
                    "short": commit_sha[:8],
                    "date": v.get("commit", {}).get("date", ""),
                    "message": v.get("commit", {}).get("message", ""),
                }
            )
            seen_commits.add(commit_sha)

        if test_name not in seen_tests:
            test_cases.append(test_name)
            seen_tests.add(test_name)

        matrix[f"{commit_sha}:{test_name}"] = {
            "category": category,
            "rationale": v.get("verdict", {}).get("rationale", ""),
        }

    return {
        "commits": commits,
        "test_cases": test_cases,
        "matrix": matrix,
        "category_legend": category_legend,
    }


def build_summary(
    verdicts: list[dict],
    pass_categories: list[str],
) -> dict:
    """Per-commit summary. Pass rate excludes harness_error."""
    by_commit: dict[str, list] = defaultdict(list)
    for v in verdicts:
        sha = v.get("commit", {}).get("sha", "unknown")
        by_commit[sha].append(v)

    summaries = {}
    test_across_commits: dict[str, list[bool]] = defaultdict(list)

    for sha, vlist in by_commit.items():
        cats = Counter(v.get("verdict", {}).get("category", "unknown") for v in vlist)
        total = len(vlist)
        harness_errors = cats.get("harness_error", 0)
        evaluated = total - harness_errors
        pass_count = sum(cats.get(c, 0) for c in pass_categories)
        pass_rate = round(pass_count / evaluated * 100, 1) if evaluated > 0 else 0.0

        summaries[sha[:8]] = {
            "total_tests": total,
            "evaluated": evaluated,
            "harness_errors": harness_errors,
            "pass_count": pass_count,
            "fail_count": evaluated - pass_count,
            "pass_rate": pass_rate,
            "categories": dict(cats),
            "commit_date": vlist[0].get("commit", {}).get("date", ""),
            "commit_message": vlist[0].get("commit", {}).get("message", ""),
        }

        for v in vlist:
            test_name = v.get("test_case", {}).get("name", "unknown")
            cat = v.get("verdict", {}).get("category", "unknown")
            is_pass = cat in pass_categories
            test_across_commits[test_name].append(is_pass)

    # Persistent failures
    persistent_failures = [
        name for name, results in test_across_commits.items() if not any(results)
    ]
    summaries["_meta"] = {
        "persistent_failures": sorted(persistent_failures),
        "total_commits": len(by_commit),
        "total_tests": len(test_across_commits),
    }

    return summaries


# ---------------------------------------------------------------------------
# Batch Execution
# ---------------------------------------------------------------------------


def check_repo_clean(repo_path: Path) -> None:
    """Verify the repo has no uncommitted changes. Raises DirtyRepoError if dirty."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.stdout.strip():
        raise DirtyRepoError(
            f"Repository has uncommitted changes:\n{result.stdout[:500]}\n"
            f"The benchmark suite checks out commits and runs git clean.\n"
            f"Pass --allow-dirty to proceed (WILL DESTROY local changes)."
        )


def validate_repo(repo_path: Path) -> None:
    """Validate that repo_path exists and is a git repository.

    Supports both regular repos (.git is a directory) and
    worktrees (.git is a file pointing to the main repo).
    """
    if not repo_path.exists():
        raise BenchmarkConfigError(f"Repo path does not exist: {repo_path}")
    git_path = repo_path / ".git"
    if not (git_path.is_dir() or git_path.is_file()):
        raise BenchmarkConfigError(f"Not a git repository: {repo_path}")


def run_benchmark_matrix(
    repo_path: Path,
    commits: list[str],
    test_cases: list[Path],
    output_base: Path,
    run_single_fn: Callable,
    benchmark_name: str,
    clean_between_commits: bool = True,
    allow_dirty: bool = False,
) -> list[dict]:
    """Run full benchmark matrix: commits x test_cases.

    Never aborts on tool or harness failure. Emits harness_error verdicts
    for exceptions. Calls clean_workspace() between commits if enabled.
    """
    # Safety gate: validate repo and check for dirty state
    validate_repo(repo_path)
    if clean_between_commits and len(commits) > 1 and not allow_dirty:
        check_repo_clean(repo_path)

    all_verdicts: list[dict] = []
    total_runs = len(commits) * len(test_cases)
    run_count = 0

    try:
        starting_ref = get_starting_ref(repo_path)
    except Exception:
        starting_ref = "main"

    try:
        for commit_idx, commit_sha in enumerate(commits):
            # Wrap commit-level setup in try/except (Codex review: truly never-abort)
            try:
                commit_meta = get_commit_metadata(repo_path, commit_sha)
            except Exception:
                commit_meta = {"full_sha": commit_sha, "date": "unknown", "message": "unknown"}
            commit_short = commit_sha[:8]

            print(f"\n{'=' * 60}")
            print(f"COMMIT: {commit_short} ({commit_meta.get('date', '?')})")
            print(f"  {commit_meta.get('message', '?')}")
            print(f"{'=' * 60}")

            # Clean workspace before checkout (except first commit)
            try:
                if clean_between_commits and commit_idx > 0:
                    clean_workspace(repo_path)
            except Exception as e:
                print(f"  WARNING: clean_workspace failed: {e}", file=sys.stderr)

            if not safe_checkout(repo_path, commit_sha):
                print(f"  WARNING: checkout failed for {commit_short}", file=sys.stderr)
                for tc in test_cases:
                    tc_name = tc.stem if tc.is_file() else tc.name
                    all_verdicts.append(
                        harness_error_verdict(
                            tc_name,
                            {"sha": commit_sha, "short": commit_short, **commit_meta},
                            RuntimeError(f"git checkout failed for {commit_sha}"),
                        )
                    )
                continue

            for tc in test_cases:
                run_count += 1
                tc_name = tc.stem if tc.is_file() else tc.name
                print(f"  [{run_count}/{total_runs}] {tc_name}...", end=" ", flush=True)

                try:
                    gt, payload = resolve_test_case(tc)
                    verdict = run_single_fn(
                        repo_path=repo_path,
                        commit_sha=commit_sha,
                        test_case_path=tc,
                        ground_truth=gt,
                        payload_path=payload,
                        output_base=output_base,
                        commit_meta={"sha": commit_sha, "short": commit_short, **commit_meta},
                    )
                    cat = verdict.get("verdict", {}).get("category", "???")
                    print(f"[{cat}]")
                    all_verdicts.append(verdict)
                except BaseException as e:
                    if isinstance(e, (KeyboardInterrupt, SystemExit)):
                        # Record partial result then re-raise (Crush review)
                        print("INTERRUPTED")
                        all_verdicts.append(
                            harness_error_verdict(
                                tc_name,
                                {"sha": commit_sha, "short": commit_short, **commit_meta},
                                e,
                                ground_truth=None,
                            )
                        )
                        raise
                    print(f"HARNESS_ERROR: {e}")
                    all_verdicts.append(
                        harness_error_verdict(
                            tc_name,
                            {"sha": commit_sha, "short": commit_short, **commit_meta},
                            e,
                            ground_truth=None,
                        )
                    )
    finally:
        try:
            restore_ref(repo_path, starting_ref)
        except Exception as e:
            print(f"  WARNING: restore_ref failed: {e}", file=sys.stderr)

    return all_verdicts


# ---------------------------------------------------------------------------
# CLI Helpers
# ---------------------------------------------------------------------------


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add standard CLI arguments shared by all harnesses."""
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Path to ClawBio repo (default: current directory)",
    )
    parser.add_argument(
        "--commits",
        type=str,
        default=None,
        help="Comma-separated commit SHAs (use HEAD for current)",
    )
    parser.add_argument(
        "--all-commits",
        action="store_true",
        help="Run against ALL commits (longitudinal sweep)",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run against HEAD only (quick CI gate)",
    )
    parser.add_argument(
        "--regression-window",
        type=int,
        default=None,
        metavar="N",
        help="Run against last N commits",
    )
    parser.add_argument(
        "--inputs",
        type=Path,
        default=None,
        help="Directory of test cases (or single file/dir)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory (default: results/<timestamp>)",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow running against a repo with uncommitted changes (destructive!)",
    )


def resolve_commits(args: argparse.Namespace, repo_path: Path) -> list[str]:
    """Resolve commit list from CLI args."""
    if args.smoke:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return [result.stdout.strip()]

    if args.all_commits:
        commits = get_all_commits(repo_path)
        print(f"Longitudinal sweep: {len(commits)} commits")
        return commits

    if args.regression_window:
        all_commits = get_all_commits(repo_path)
        n = min(args.regression_window, len(all_commits))
        commits = all_commits[-n:]
        print(f"Regression window: last {n} commits")
        return commits

    if args.commits:
        raw = args.commits.split(",")
        commits = []
        for c in raw:
            c = c.strip()
            if c == "HEAD":
                result = subprocess.run(
                    ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                )
                c = result.stdout.strip()
            commits.append(c)
        return commits

    raise BenchmarkConfigError(
        "Must specify --smoke, --commits, --regression-window, or --all-commits"
    )


def resolve_test_cases(inputs_path: Path, glob_pattern: str = "*") -> list[Path]:
    """Resolve test case paths from an inputs directory.

    For Model B (directory-based test cases), returns directories that
    contain ground_truth.txt. For Model A, returns matching files.
    """
    inputs_path = inputs_path.resolve()
    if inputs_path.is_file():
        return [inputs_path]

    if not inputs_path.is_dir():
        raise BenchmarkConfigError(f"Input path does not exist: {inputs_path}")

    # Model B: look for directories with ground_truth.txt
    dirs = sorted(
        [d for d in inputs_path.iterdir() if d.is_dir() and (d / "ground_truth.txt").exists()]
    )
    if dirs:
        return dirs

    # Model A fallback: glob for files
    files = sorted(inputs_path.glob(glob_pattern))
    if files:
        return [f for f in files if f.is_file()]

    raise BenchmarkConfigError(f"No test cases found in {inputs_path}")


def make_output_dir(args: argparse.Namespace, benchmark_name: str) -> Path:
    """Create and return the output directory."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_base = (args.output or Path.cwd() / "results" / benchmark_name / timestamp).resolve()
    output_base.mkdir(parents=True, exist_ok=True)
    return output_base


# ---------------------------------------------------------------------------
# Standard Verdict Document Builder
# ---------------------------------------------------------------------------


def build_verdict_doc(
    benchmark_name: str,
    benchmark_version: str,
    commit_meta: dict,
    test_case_name: str,
    ground_truth: dict,
    ground_truth_refs: dict[str, str],
    execution: ExecutionResult | None,
    outputs: dict,
    report_analysis: dict,
    verdict: dict,
    driver_path: Path | None = None,
    payload_path: Path | None = None,
) -> dict:
    """Build a standardized verdict JSON document."""
    test_case_info = {"name": test_case_name}
    if driver_path and driver_path.exists():
        test_case_info["driver"] = str(driver_path)
        test_case_info["driver_sha256"] = sha256_file(driver_path)
    if payload_path and payload_path.exists():
        test_case_info["payload"] = payload_path.name
        test_case_info["payload_sha256"] = sha256_file(payload_path)

    doc = {
        "benchmark_version": benchmark_version,
        "benchmark_name": benchmark_name,
        "start_time_utc": (
            execution.start_time.isoformat() if execution else datetime.now(UTC).isoformat()
        ),
        "timestamp_utc": (
            execution.end_time.isoformat() if execution else datetime.now(UTC).isoformat()
        ),
        "wall_clock_seconds": (round(execution.wall_seconds, 3) if execution else 0.0),
        "commit": commit_meta,
        "test_case": test_case_info,
        "ground_truth": ground_truth,
        "ground_truth_references": ground_truth_refs,
        "execution": execution.to_dict() if execution else {},
        "outputs": outputs,
        "report_analysis": report_analysis,
        "verdict": verdict,
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "hostname": platform.node(),
        },
    }
    return doc


def save_verdict(verdict_doc: dict, output_dir: Path) -> Path:
    """Write verdict.json to the output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "verdict.json"
    with open(path, "w") as f:
        json.dump(verdict_doc, f, indent=2, default=str)
    return path


def save_execution_logs(execution: ExecutionResult, output_dir: Path) -> None:
    """Save stdout.log and stderr.log."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "stdout.log").write_text(execution.stdout)
    (output_dir / "stderr.log").write_text(execution.stderr)


# ---------------------------------------------------------------------------
# Standard Harness Runner
# ---------------------------------------------------------------------------


def run_harness_main(
    benchmark_name: str,
    benchmark_version: str,
    default_inputs_dir: str,
    run_single_fn: Callable,
    rubric_categories: list[str],
    pass_categories: list[str],
    fail_categories: list[str],
    ground_truth_refs: dict[str, str],
    category_legend: dict[str, dict],
    description: str = "",
    glob_pattern: str = "*",
) -> None:
    """Standard main() for all harnesses. Wire up CLI, execute matrix, write outputs."""
    parser = argparse.ArgumentParser(
        description=description or f"ClawBio {benchmark_name} Benchmark Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_common_args(parser)
    args = parser.parse_args()

    repo_path = args.repo.resolve()

    try:
        validate_repo(repo_path)
        commits = resolve_commits(args, repo_path)
    except (BenchmarkConfigError, DirtyRepoError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Resolve test cases
    inputs_path = args.inputs or (
        Path(__file__).resolve().parent / "test_cases" / default_inputs_dir
    )
    try:
        test_cases = resolve_test_cases(inputs_path, glob_pattern)
    except BenchmarkConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    output_base = make_output_dir(args, benchmark_name)

    # Write manifest
    write_manifest(
        output_base,
        benchmark_name,
        benchmark_version,
        repo_path,
        commits,
        test_cases,
        ground_truth_refs,
        rubric_categories,
        pass_categories,
        fail_categories,
    )

    print(f"\nClawBio {benchmark_name} Benchmark v{benchmark_version}")
    print(f"  Repo: {repo_path}")
    print(f"  Commits: {len(commits)}")
    print(f"  Test cases: {len(test_cases)}")
    print(f"  Total runs: {len(commits) * len(test_cases)}")
    print(f"  Output: {output_base}")
    print()

    # Execute
    allow_dirty = getattr(args, "allow_dirty", False)
    verdicts = run_benchmark_matrix(
        repo_path,
        commits,
        test_cases,
        output_base,
        run_single_fn,
        benchmark_name,
        allow_dirty=allow_dirty,
    )

    # Write aggregated outputs
    heatmap = build_heatmap_data(verdicts, category_legend)
    with open(output_base / "heatmap_data.json", "w") as f:
        json.dump(heatmap, f, indent=2, default=str)

    summary = build_summary(verdicts, pass_categories)
    with open(output_base / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    with open(output_base / "all_verdicts.json", "w") as f:
        json.dump(verdicts, f, indent=2, default=str)

    # Print summary
    print(f"\n{'=' * 60}")
    print("BENCHMARK COMPLETE")
    print(f"{'=' * 60}")
    for sha_short, s in summary.items():
        if sha_short == "_meta":
            continue
        print(
            f"\n  {sha_short} ({s['commit_date'][:10]}): "
            f"{s['pass_count']}/{s['evaluated']} pass ({s['pass_rate']}%) "
            f"[{s['harness_errors']} harness errors]"
        )
        for cat, count in sorted(s["categories"].items()):
            print(f"    {cat}: {count}")

    meta = summary.get("_meta", {})
    pf = meta.get("persistent_failures", [])
    if pf:
        print(f"\n  Persistent failures ({len(pf)}):")
        for name in pf:
            print(f"    - {name}")

    print(f"\nResults: {output_base}")
