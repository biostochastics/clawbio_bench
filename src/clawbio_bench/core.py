#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
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
import re
import subprocess
import sys
import time
import traceback
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# ---------------------------------------------------------------------------
# Constants & Exceptions
# ---------------------------------------------------------------------------

CORE_VERSION: str  # Set from package metadata — no hardcoded string to drift.
try:
    from clawbio_bench import __version__ as CORE_VERSION
except Exception:
    CORE_VERSION = "0.0.0"  # Fallback if package not installed editable


class BenchmarkConfigError(Exception):
    """Raised when benchmark configuration is invalid (missing repo, bad args)."""


class DirtyRepoError(Exception):
    """Raised when the target repo has uncommitted changes and --allow-dirty not set."""


class VerdictSchemaError(Exception):
    """Raised when a verdict document fails schema validation."""


# ---------------------------------------------------------------------------
# Unified severity tier system
# ---------------------------------------------------------------------------
# Five canonical tiers used across all harnesses and both report generators.
# Each harness's CATEGORY_LEGEND entry carries a "tier" field set to one of
# these strings; the generators map it to the numeric rank below at render
# time. New harnesses add tier annotations to their legend — no code edits
# are required in the report generators.

TIER_NAMES: tuple[str, ...] = ("pass", "advisory", "warning", "critical", "infra")

# Numeric ranks — higher = more severe. Report generators use these to sort
# findings and pick colors. Keep stable: baselines and aggregates carry the
# string name, not the int, so renumbering tiers requires a migration.
TIER_RANKS: dict[str, int] = {name: idx for idx, name in enumerate(TIER_NAMES)}


def tier_rank(tier: str | None) -> int:
    """Return the integer rank (0..4) for a tier name, default ``infra``."""
    if tier is None:
        return TIER_RANKS["infra"]
    return TIER_RANKS.get(tier, TIER_RANKS["infra"])


def derive_tier_from_category_sets(
    category: str,
    pass_categories: list[str] | set[str],
    fail_categories: list[str] | set[str],
) -> str:
    """Smart fallback when a category lacks an explicit tier in the legend.

    This is the heuristic the dynamic markdown renderer used to bake in; we
    expose it here so every consumer agrees on the fallback semantics. The
    heuristic is deliberately coarse:

    * ``harness_error``          → ``"infra"``
    * in ``pass_categories``     → ``"pass"``
    * in ``fail_categories``     → ``"critical"``
    * anything else              → ``"warning"``

    Harnesses SHOULD set ``tier`` explicitly in their legend so this is not
    used; it only matters for unknown categories appearing at runtime.
    """
    if category == "harness_error":
        return "infra"
    if category in pass_categories:
        return "pass"
    if category in fail_categories:
        return "critical"
    return "warning"


# ---------------------------------------------------------------------------
# Input Validation
# ---------------------------------------------------------------------------

# Minimum 7 hex chars matches git's default short-SHA length and reduces the
# collision space from 16-bit to 28-bit.
_COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
# WEIGHTS must start with an alphanumeric char (no leading '-') so argparse
# cannot reinterpret it as a new option. Interior hyphens are allowed.
_SAFE_WEIGHTS_RE = re.compile(r"^[\d.a-zA-Z_][\d.,=:a-zA-Z_\-]*$")

MAX_TIMEOUT = 600  # 10 minutes — hard cap for any single test case
# Maximum captured stdout/stderr size to prevent OOM when tools emit huge output.
# 10 MB is enough for any reasonable diagnostic output; beyond this we truncate
# and record the original byte count for chain of custody.
MAX_CAPTURE_BYTES = 10 * 1024 * 1024  # 10 MB


def _environment_signature() -> dict[str, Any]:
    """Capture a reproducibility signature for the current Python environment.

    Records interpreter path, Python version, platform, hostname hash, and a
    stable hash of all installed distributions (name + version pairs).
    Stdlib-only via importlib.metadata.
    """
    from importlib.metadata import distributions

    # Build sorted list of "name==version" for stable hashing. PackageMetadata
    # is an email.message.Message subclass: it supports __contains__ /
    # __getitem__ but typeshed does not expose a `.get()` method, so mypy
    # flags d.metadata.get("Name"). We use the __contains__/__getitem__ path
    # instead, which is both typeshed-clean and equivalent at runtime.
    pkgs = sorted(
        f"{d.metadata['Name']}=={d.version}"
        for d in distributions()
        if d.metadata is not None and "Name" in d.metadata and d.metadata["Name"]
    )
    pkg_hash = hashlib.sha256("\n".join(pkgs).encode("utf-8")).hexdigest()
    return {
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "hostname_hash": hashlib.sha256(platform.node().encode()).hexdigest()[:12],
        "package_count": len(pkgs),
        "package_set_sha256": pkg_hash,
    }


def validate_timeout(value: str | int | None, default: int = 60) -> int:
    """Parse and clamp a timeout value to [1, MAX_TIMEOUT].

    Accepts ``None`` and returns ``default`` — the function is defensive
    against unparseable input (env vars, CLI args) so callers can pass
    raw values without pre-validating.
    """
    try:
        t = int(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default
    return max(1, min(t, MAX_TIMEOUT))


def validate_commit_sha(sha: str) -> str:
    """Validate that a string looks like a hex commit SHA or 'HEAD'.

    Raises BenchmarkConfigError for invalid formats.
    """
    sha = sha.strip()
    if sha == "HEAD":
        return sha
    if not _COMMIT_SHA_RE.match(sha):
        raise BenchmarkConfigError(
            f"Invalid commit SHA format: {sha!r} (expected 7-40 hex characters or 'HEAD')"
        )
    return sha


def validate_weights(value: str) -> str:
    """Validate WEIGHTS ground truth value is safe for CLI argument injection."""
    if not _SAFE_WEIGHTS_RE.match(value):
        raise ValueError(f"Invalid WEIGHTS format (possible argument injection): {value!r}")
    return value


def validate_payload_path(payload_name: str, test_case_root: Path) -> Path:
    """Resolve and validate a payload/sidecar file path against traversal.

    Returns the resolved path if safe, raises ValueError if traversal detected.
    """
    payload_path = (test_case_root / payload_name).resolve()
    resolved_root = test_case_root.resolve()
    try:
        payload_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"Path traversal detected: '{payload_name}' escapes {resolved_root}"
        ) from exc
    return payload_path


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


def artifact_info(filepath: Path) -> dict[str, Any]:
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


def get_commit_metadata(repo_path: Path, commit_sha: str) -> dict[str, Any]:
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
        pass  # Best-effort metadata — never abort the benchmark for git log failures
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


def get_tagged_commits(repo_path: Path, branch: str = "main") -> list[str]:
    """Get SHAs of commits that carry at least one tag, in chronological order.

    Only includes tags reachable from *branch*. Lightweight and annotated
    tags are both included. The returned list is a subset of
    ``get_all_commits()`` preserving its chronological (oldest-first) order.
    """
    # 1. All SHAs on the branch (ordered oldest-first).
    all_shas = get_all_commits(repo_path, branch=branch)

    # 2. Resolve every tag to its target commit SHA.
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "tag",
                "--list",
                "--format=%(objectname) %(*objectname)",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        raise BenchmarkConfigError("Timed out resolving tagged commits") from exc
    if result.returncode != 0:
        raise BenchmarkConfigError(f"git tag failed: {result.stderr.strip()}")

    tagged_shas: set[str] = set()
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.strip().split()
        # Annotated tags have a dereferenced (*objectname) field; lightweight
        # tags only have objectname.  Use the deref if present.
        sha = parts[-1] if len(parts) > 1 and parts[-1] else parts[0]
        tagged_shas.add(sha)

    # 3. Intersect with branch history to keep only reachable tagged commits,
    #    preserving chronological order from get_all_commits().
    return [sha for sha in all_shas if sha in tagged_shas]


def get_commit_tags(repo_path: Path) -> dict[str, list[str]]:
    """Return a mapping of commit SHA → list of tag names.

    Both lightweight and annotated tags are included. Annotated tags are
    dereferenced to their target commit SHA so the mapping is always keyed
    by commit, not tag-object, SHA.

    Returns an empty dict (with a stderr warning) if git fails. This is
    best-effort metadata — tag enrichment is additive, never blocking.
    """
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "for-each-ref",
            "--format=%(refname:short) %(objecttype) %(*objectname) %(objectname)",
            "refs/tags/",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        print(
            f"  WARNING: git for-each-ref failed (exit {result.returncode}): "
            f"{result.stderr.strip()!r} — tag enrichment disabled for this run",
            file=sys.stderr,
        )
        return {}

    tag_map: dict[str, list[str]] = {}
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.strip().split()
        if len(parts) < 3:
            print(
                f"  WARNING: skipping malformed tag line: {line.strip()!r}",
                file=sys.stderr,
            )
            continue
        tag_name = parts[0]
        obj_type = parts[1]
        # Annotated: deref (*objectname) is the commit SHA; lightweight: objectname.
        commit_sha = parts[2] if obj_type == "tag" and len(parts) >= 4 else parts[-1]
        tag_map.setdefault(commit_sha, []).append(tag_name)

    return tag_map


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


def _has_submodules(repo_path: Path) -> bool:
    """Return True iff ``repo_path`` has at least one initialized submodule.

    Uses ``git config -f .gitmodules`` which is inexpensive and doesn't
    require network access. The absence of ``.gitmodules`` (exit != 0)
    means there are no submodules to worry about.
    """
    gitmodules = repo_path / ".gitmodules"
    if not gitmodules.exists():
        return False
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "config",
            "-f",
            ".gitmodules",
            "--name-only",
            "--get-regexp",
            r"^submodule\..*\.path$",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def clean_workspace(repo_path: Path, purge_ignored: bool = False) -> None:
    """Reset tracked files + remove untracked to prevent contamination.

    Called between commits in longitudinal sweeps.

    Args:
        repo_path: Repository (or worktree) to clean.
        purge_ignored: If True, also remove .gitignore'd files (-fdx).
            Set True for isolated longitudinal sweeps where stale ignored
            caches (pytest, venvs, build artifacts) would contaminate
            downstream commits. Default False preserves .env and other
            secrets in developer checkouts.

    Raises RuntimeError if any git command fails, so stale files
    from a previous commit cannot silently contaminate the next run.

    Submodules: if the repo has submodules, this function also resets and
    cleans EVERY submodule recursively. Without this, a submodule working
    tree modified during the audited tool's execution (e.g. build
    artifacts generated inside a vendored dep) would carry over into the
    next commit's run and poison longitudinal comparisons.
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "checkout", "--", "."],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git checkout -- . failed: {result.stderr.strip()}")
    clean_flags = "-fdx" if purge_ignored else "-fd"
    result = subprocess.run(
        ["git", "-C", str(repo_path), "clean", clean_flags],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clean {clean_flags} failed: {result.stderr.strip()}")

    # Submodule cleanup — only run if submodules exist. ``foreach --recursive``
    # handles nested submodules too. We reset hard to the recorded SHA of
    # each submodule and clean untracked/ignored files with the same flags.
    if _has_submodules(repo_path):
        reset_cmd = [
            "git",
            "-C",
            str(repo_path),
            "submodule",
            "foreach",
            "--recursive",
            "git reset --hard HEAD --quiet",
        ]
        result = subprocess.run(reset_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"git submodule foreach reset failed: {result.stderr.strip()}")
        sub_clean_cmd = [
            "git",
            "-C",
            str(repo_path),
            "submodule",
            "foreach",
            "--recursive",
            f"git clean {clean_flags} --quiet",
        ]
        result = subprocess.run(sub_clean_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(
                f"git submodule foreach clean {clean_flags} failed: {result.stderr.strip()}"
            )


# ---------------------------------------------------------------------------
# Ground Truth Parsing
# ---------------------------------------------------------------------------


# Header keys are strictly UPPER_SNAKE_CASE (possibly with digits or hyphens).
# This prevents mis-parsing narrative lines like '# Tamoxifen: avoid' as headers.
_KEY_VALUE_RE = re.compile(r"^([A-Z][A-Z0-9_\-]*)\s*:\s*(.*)$")

# YAML frontmatter sentinel. A file whose first non-blank `#` line is exactly
# `# ---` opens a YAML block; the next `# ---` closes it. Everything in
# between is `#`-stripped and parsed as YAML. The `#` prefix keeps the block
# invisible to downstream tools that treat the file as input (e.g., ClawBio
# reading a 23andMe-style TSV), preserving the Model A invariant where the
# ground-truth file IS the audited-tool payload.
_YAML_FENCE = "# ---"


def _parse_legacy_key_value(lines: list[str], ground_truth_path: Path) -> dict[str, str]:
    """Parse the legacy `# KEY: value` header format.

    Kept as the fallback parser after the YAML frontmatter migration lands —
    every legacy test case remains valid forever. The parser reads until the
    first non-blank non-comment line (or EOF), tolerates blank lines before
    the header block, and warns on duplicate keys (last value wins).
    """
    gt: dict[str, str] = {}
    seen_keys: set[str] = set()
    header_started = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if header_started:
                break
            continue
        if not stripped.startswith("#"):
            break
        header_started = True
        content = stripped.lstrip("#").strip()
        if not content:
            continue
        m = _KEY_VALUE_RE.match(content)
        if not m:
            continue
        key, value = m.group(1).strip(), m.group(2).strip()
        # REFERENCE is allowed to repeat (multiple citations per test case);
        # subsequent values are appended with a separator rather than warned.
        if key == "REFERENCE" and key in seen_keys:
            gt[key] = f"{gt[key]},{value}"
            continue
        if key in seen_keys:
            print(
                f"  WARNING: Duplicate header '{key}' in {ground_truth_path.name} "
                f"(using last value)",
                file=sys.stderr,
            )
        seen_keys.add(key)
        gt[key] = value
    return gt


def _parse_yaml_frontmatter(lines: list[str], ground_truth_path: Path) -> dict[str, Any]:
    """Parse YAML frontmatter sandwiched between `# ---` sentinels.

    Every line inside the block is expected to start with `#` (so the audited
    tool still sees a comment block). The `#` and exactly one optional space
    are stripped before the remaining text is joined and passed to
    ``ruamel.yaml``'s safe loader. The parser raises ``BenchmarkConfigError``
    if the closing sentinel is missing — open-ended frontmatter is almost
    always a typo and should fail loudly rather than silently truncate.

    UPPER_SNAKE keys are preserved as-is; downstream code indexes the returned
    dict with UPPER_SNAKE strings (e.g., ``gt["FINDING_CATEGORY"]``).
    """
    from ruamel.yaml import YAML

    # Find the opening and closing sentinels. The opening sentinel is the
    # caller's responsibility (they only dispatched here because they saw it);
    # we still scan for it to compute the slice.
    start_idx: int | None = None
    end_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == _YAML_FENCE:
            if start_idx is None:
                start_idx = i
            else:
                end_idx = i
                break

    if start_idx is None:
        raise BenchmarkConfigError(
            f"Expected YAML frontmatter but no '# ---' sentinel found in {ground_truth_path.name}"
        )
    if end_idx is None:
        raise BenchmarkConfigError(
            f"Unterminated YAML frontmatter in {ground_truth_path.name}: "
            f"missing closing '# ---' sentinel"
        )

    body_lines: list[str] = []
    for raw in lines[start_idx + 1 : end_idx]:
        stripped = raw.rstrip("\n")
        if not stripped.lstrip().startswith("#"):
            raise BenchmarkConfigError(
                f"Non-comment line inside YAML frontmatter in "
                f"{ground_truth_path.name}: {stripped!r}"
            )
        # Strip leading `#` and exactly one optional space. We preserve any
        # additional leading spaces so YAML block scalar indentation survives.
        content = stripped.lstrip()[1:]  # drop the '#'
        if content.startswith(" "):
            content = content[1:]
        body_lines.append(content)

    # Pre-parse alias/merge-key rejection: ruamel's safe loader honors YAML
    # aliases (``&anchor`` / ``*anchor``) and merge keys (``<<:``), which let
    # one header silently copy another. For an auditor-grade benchmark we
    # want each field to be literal and self-contained, so we reject these
    # tokens at the source level before calling the YAML parser. The
    # regexes are deliberately conservative — they match the tokens only
    # where YAML's grammar allows them (start-of-token, after whitespace).
    yaml_text = "\n".join(body_lines)
    if re.search(r"(^|\s)[*&][A-Za-z_][A-Za-z0-9_-]*", yaml_text):
        raise BenchmarkConfigError(
            f"YAML frontmatter in {ground_truth_path.name} uses anchors or "
            f"aliases (``&name`` / ``*name``); these are not allowed in "
            f"benchmark inputs because they break the one-line-per-fact "
            f"audit invariant."
        )
    if re.search(r"^\s*<<\s*:", yaml_text, flags=re.MULTILINE):
        raise BenchmarkConfigError(
            f"YAML frontmatter in {ground_truth_path.name} uses a merge key "
            f"(``<<:``); merge keys are not allowed in benchmark inputs."
        )

    yaml_parser = YAML(typ="safe")
    try:
        data = yaml_parser.load(yaml_text)
    except Exception as exc:  # ruamel raises a variety of parse errors
        raise BenchmarkConfigError(
            f"Failed to parse YAML frontmatter in {ground_truth_path.name}: {exc}"
        ) from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise BenchmarkConfigError(
            f"YAML frontmatter in {ground_truth_path.name} must be a mapping, "
            f"got {type(data).__name__}"
        )

    # Key-name validation and recursive normalization. Keys must match the
    # same UPPER_SNAKE convention enforced by the legacy ``# KEY: value``
    # parser so downstream code can index with the same strings regardless
    # of which format a test case uses. Nested dict/list values are
    # normalized recursively to plain JSON-native types (str/int/float/bool/
    # None) with scalars coerced to strings — matching the top-level
    # contract.
    out: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            raise BenchmarkConfigError(
                f"YAML frontmatter key in {ground_truth_path.name} must be a "
                f"string, got {type(key).__name__}: {key!r}"
            )
        if not _KEY_VALUE_RE.match(f"{key}:"):
            raise BenchmarkConfigError(
                f"YAML frontmatter key {key!r} in {ground_truth_path.name} "
                f"does not match the UPPER_SNAKE_CASE convention "
                f"``[A-Z][A-Z0-9_-]*``; rename to avoid silent typos (e.g. "
                f"``finding_category`` should be ``FINDING_CATEGORY``)."
            )
        out[key] = _normalize_yaml_value(value, ground_truth_path, key)
    return out


def _normalize_yaml_value(value: Any, ground_truth_path: Path, key_path: str) -> Any:
    """Recursively normalize a YAML-loaded value to plain Python primitives.

    * Scalars (str, int, float, bool) are coerced to ``str`` so downstream
      code that calls ``int(gt["KEY"])`` or string-equality still works.
    * ``None`` becomes an empty string.
    * Dicts and lists are walked recursively with the same rules.
    * Any other type (e.g. ``datetime.date`` that snuck through ruamel)
      raises ``BenchmarkConfigError`` — benchmark inputs must be boring.

    ``key_path`` is threaded through for error messages so a failure deep
    inside a nested structure points at the offending location.
    """
    if isinstance(value, dict):
        nested: dict[str, Any] = {}
        for sub_key, sub_value in value.items():
            if not isinstance(sub_key, str):
                raise BenchmarkConfigError(
                    f"Nested YAML key under {key_path!r} in "
                    f"{ground_truth_path.name} must be a string, got "
                    f"{type(sub_key).__name__}: {sub_key!r}"
                )
            nested[sub_key] = _normalize_yaml_value(
                sub_value, ground_truth_path, f"{key_path}.{sub_key}"
            )
        return nested
    if isinstance(value, list):
        return [
            _normalize_yaml_value(item, ground_truth_path, f"{key_path}[{i}]")
            for i, item in enumerate(value)
        ]
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    raise BenchmarkConfigError(
        f"Unsupported YAML value type under {key_path!r} in "
        f"{ground_truth_path.name}: {type(value).__name__}"
    )


def parse_ground_truth(
    ground_truth_path: Path,
    required_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Parse a ground truth file, dispatching on format.

    Two formats are accepted:

    * **YAML frontmatter** (preferred): file opens with `# ---`, then YAML
      key/value pairs on `#`-prefixed lines, closed by a second `# ---`.
      Lets us use block scalars for multi-line narrative fields without the
      continuation-line gymnastics the legacy format requires.

    * **Legacy `# KEY: value`**: every line starting with `#` is scanned for
      ``^([A-Z][A-Z0-9_-]*)\\s*:\\s*(.*)$``. The header block ends at the
      first non-blank non-comment line. This is the historical format and
      remains valid forever — migration is per-file and voluntary.

    Both formats stop scanning at the first non-blank non-comment line, so
    downstream payload (TSV rows, VCF records, etc.) is never mistaken for
    ground truth headers.

    Args:
        ground_truth_path: path to the ground-truth file.
        required_fields: if given, ``BenchmarkConfigError`` is raised when
            any of these fields are missing after parsing.
    """
    with open(ground_truth_path, encoding="utf-8-sig") as f:
        lines = list(f)

    # Locate first non-blank line to decide format. Blank lines before the
    # header block are allowed in both formats.
    first_nonblank: str | None = None
    for line in lines:
        if line.strip():
            first_nonblank = line.strip()
            break

    if first_nonblank == _YAML_FENCE:
        gt: dict[str, Any] = _parse_yaml_frontmatter(lines, ground_truth_path)
    else:
        gt = dict(_parse_legacy_key_value(lines, ground_truth_path))

    if not gt.get("FINDING_CATEGORY"):
        # NF-7: Warn when FINDING_CATEGORY is missing
        print(
            f"  WARNING: No FINDING_CATEGORY in {ground_truth_path.name}",
            file=sys.stderr,
        )
    if required_fields:
        missing = [f for f in required_fields if f not in gt]
        if missing:
            raise BenchmarkConfigError(
                f"Ground truth {ground_truth_path.name} missing required fields: {missing}"
            )
    return gt


def resolve_test_case(test_case_path: Path) -> tuple[dict[str, Any], Path | None]:
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
            payload_path = validate_payload_path(payload_name, test_case_path)
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
    """Captured result of running a single audited subprocess.

    ``stdout`` / ``stderr`` hold the (possibly truncated) text the rest of
    the harness operates on. ``stdout_full_sha256`` / ``stderr_full_sha256``
    are computed over the ORIGINAL pre-truncation bytes so chain-of-custody
    is preserved even when the tool emits runaway output. ``stdout_truncated``
    / ``stderr_truncated`` flag whether truncation actually happened, so
    downstream consumers can distinguish "short and clean" from "truncated
    for cap".
    """

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
    # Chain-of-custody hashes over the ORIGINAL pre-truncation bytes. Defaults
    # to the empty-string hash so existing test fixtures and callers that
    # construct ExecutionResult directly (e.g. metagenomics static-only path,
    # unit tests) don't need to supply them.
    stdout_full_sha256: str = ""
    stderr_full_sha256: str = ""
    stdout_full_byte_len: int = 0
    stderr_full_byte_len: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout_lines": self.stdout.count("\n"),
            # Hash of the (possibly truncated) text actually held in memory.
            "stdout_sha256": sha256_string(self.stdout),
            # Hash of the ORIGINAL pre-truncation bytes — chain of custody.
            # Falls back to the post-truncation hash only when the caller
            # constructed ExecutionResult directly (e.g. static-analysis
            # path) and left stdout_full_sha256 empty.
            "stdout_full_sha256": (self.stdout_full_sha256 or sha256_string(self.stdout)),
            "stdout_full_byte_len": self.stdout_full_byte_len
            or len(self.stdout.encode("utf-8", errors="replace")),
            "stdout_truncated": self.stdout_truncated,
            "stderr_lines": self.stderr.count("\n"),
            "stderr_sha256": sha256_string(self.stderr),
            "stderr_full_sha256": (self.stderr_full_sha256 or sha256_string(self.stderr)),
            "stderr_full_byte_len": self.stderr_full_byte_len
            or len(self.stderr.encode("utf-8", errors="replace")),
            "stderr_truncated": self.stderr_truncated,
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
    env: dict[str, str] | None = None,
    fallback_cmd: list[str] | None = None,
    fallback_flag: str | None = None,
) -> ExecutionResult:
    """Execute a command and capture all output.

    If the primary cmd fails with exit code 2 (argparse error) and
    fallback_cmd is provided, retries with fallback_cmd.

    Fallback contract (tightened from the original v0.1 heuristic):

    * The caller MUST pass ``fallback_flag`` identifying the exact argparse
      flag being stripped from ``cmd`` to produce ``fallback_cmd`` (e.g.
      ``"--no-figures"``).
    * The fallback fires ONLY when the primary run exits 2 AND stderr
      contains the argparse rejection line ``error: unrecognized arguments:
      <flag>``.

    This prevents the fallback from false-triggering on tools that
    legitimately emit ``"usage:"`` and ``"error: unrecognized arguments:"``
    in their stderr for unrelated reasons (nested argparse tools, wrappers,
    even this harness when auditing itself). If ``fallback_flag`` is None
    we fall back to the looser legacy heuristic for backward compatibility
    with existing callers, but a DeprecationWarning is emitted so the
    transition can be completed.
    """
    run_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if env:
        run_env.update(env)

    start_time = datetime.now(UTC)
    wall_start = time.monotonic()
    used_fallback = False

    # result: either CompletedProcess or SimpleNamespace fallback (same attrs)
    result: subprocess.CompletedProcess[str] | SimpleNamespace
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
        # the exact flag the caller declared it was stripping. Falls back to
        # the legacy loose heuristic only when fallback_flag is not provided.
        should_fallback = False
        if result.returncode == 2 and fallback_cmd:
            if fallback_flag:
                # Strict mode: require the exact flag in the rejection line.
                reject_line = f"error: unrecognized arguments: {fallback_flag}"
                if reject_line in result.stderr:
                    should_fallback = True
            else:
                import warnings

                warnings.warn(
                    "capture_execution(fallback_cmd=...) called without "
                    "fallback_flag; this loose-match path is deprecated "
                    "and may false-trigger on tools that emit benign "
                    "argparse-shaped stderr. Pass fallback_flag to pin "
                    "the retry to a specific rejected flag.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                if "error: unrecognized arguments:" in result.stderr and "usage:" in result.stderr:
                    should_fallback = True
        if should_fallback:
            # should_fallback is only True when fallback_cmd is non-None, but
            # mypy can't narrow through the should_fallback flag. Assert for
            # the type checker and document the invariant.
            assert fallback_cmd is not None
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
        result = SimpleNamespace(
            returncode=-1,
            stdout="",
            stderr=f"TIMEOUT after {timeout}s",
        )
    except Exception as exc:
        # Catch FileNotFoundError, PermissionError, OSError
        result = SimpleNamespace(
            returncode=-2,
            stdout="",
            stderr=f"EXECUTION_ERROR: {type(exc).__name__}: {exc}",
        )

    wall_elapsed = time.monotonic() - wall_start
    end_time = datetime.now(UTC)

    # Tools that legitimately emit >10 MB should log to files, not stdout.
    # Truncate runaway output to prevent OOM and huge verdict JSONs, but
    # record the hash of the ORIGINAL pre-truncation bytes AND truncate at
    # an encoded-byte boundary so multi-byte output actually stays within
    # the cap. Character-based slicing could let a 4-byte-per-codepoint
    # stream drift well past MAX_CAPTURE_BYTES.
    stdout_raw = result.stdout or ""
    stderr_raw = result.stderr or ""
    stdout, stdout_full_sha, stdout_full_len, stdout_truncated = _truncate_with_hash(stdout_raw)
    stderr, stderr_full_sha, stderr_full_len, stderr_truncated = _truncate_with_hash(stderr_raw)

    return ExecutionResult(
        exit_code=result.returncode,
        stdout=stdout,
        stderr=stderr,
        wall_seconds=wall_elapsed,
        start_time=start_time,
        end_time=end_time,
        used_fallback=used_fallback,
        cmd=cmd,
        cwd=str(cwd),
        timeout_seconds=timeout,
        stdout_full_sha256=stdout_full_sha,
        stderr_full_sha256=stderr_full_sha,
        stdout_full_byte_len=stdout_full_len,
        stderr_full_byte_len=stderr_full_len,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def _truncate_with_hash(text: str) -> tuple[str, str, int, bool]:
    """Hash-before-truncate, truncate at an encoded-byte boundary.

    Returns ``(possibly_truncated_text, full_sha256, full_byte_len, truncated)``
    where:
      * ``possibly_truncated_text`` is safe for embedding in a verdict;
      * ``full_sha256`` is the SHA-256 of the ORIGINAL pre-truncation bytes;
      * ``full_byte_len`` is the UTF-8 byte length of the original string;
      * ``truncated`` indicates whether the cap fired.

    The chain-of-custody contract is: an auditor who receives the truncated
    text AND the full_sha256 can verify the hash against the tool's raw
    output if they re-run the benchmark — the hash describes the ORIGINAL
    stream, not the post-truncation artifact.
    """
    encoded = text.encode("utf-8", errors="replace")
    full_byte_len = len(encoded)
    full_sha = hashlib.sha256(encoded).hexdigest()
    if full_byte_len <= MAX_CAPTURE_BYTES:
        return text, full_sha, full_byte_len, False
    # Truncate encoded bytes at the cap, then decode with replacement so any
    # trailing multi-byte sequence cut in half becomes the Unicode
    # replacement character instead of raising. This guarantees the
    # resulting text, re-encoded, is <= MAX_CAPTURE_BYTES + a small constant
    # for the truncation marker.
    truncated_bytes = encoded[:MAX_CAPTURE_BYTES]
    truncated_text = truncated_bytes.decode("utf-8", errors="replace")
    marker = (
        f"\n[...TRUNCATED: original {full_byte_len} bytes exceeded "
        f"{MAX_CAPTURE_BYTES} bytes; full_sha256={full_sha}...]"
    )
    return truncated_text + marker, full_sha, full_byte_len, True


# ---------------------------------------------------------------------------
# Error-to-Verdict Conversion
# ---------------------------------------------------------------------------


def harness_error_verdict(
    test_case_name: str,
    commit_meta: dict[str, Any],
    exception: BaseException,
    ground_truth: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
) -> dict[str, Any]:
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
            files: dict[str, str] = {}
            for child in sorted(tc.iterdir()):
                if child.is_file():
                    files[child.name] = sha256_file(child)
            tc_inventory.append(
                {"name": tc.name, "type": "directory", "files": files}  # type: ignore[dict-item]
            )

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
        "environment": _environment_signature(),
    }
    with open(output_base / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return manifest


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def build_heatmap_data(
    verdicts: list[dict[str, Any]],
    category_legend: dict[str, dict[str, Any]],
    tag_map: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Build aggregated heatmap matrix from verdict list.

    If *tag_map* is provided (SHA → tag names), each commit entry in the
    returned dict will carry a ``"tags"`` list so downstream renderers can
    annotate releases on the timeline axis.
    """
    commits = []
    seen_commits: set[str] = set()
    test_cases = []
    seen_tests: set[str] = set()
    matrix = {}
    tag_map = tag_map or {}

    for v in verdicts:
        commit_sha = v.get("commit", {}).get("sha", "unknown")
        test_name = v.get("test_case", {}).get("name", "unknown")
        category = v.get("verdict", {}).get("category", "unknown")

        if commit_sha not in seen_commits:
            entry = {
                "sha": commit_sha,
                "short": commit_sha[:8],
                "date": v.get("commit", {}).get("date", ""),
                "message": v.get("commit", {}).get("message", ""),
            }
            if commit_sha in tag_map:
                entry["tags"] = tag_map[commit_sha]
            commits.append(entry)
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
    verdicts: list[dict[str, Any]],
    pass_categories: list[str],
) -> dict[str, Any]:
    """Per-commit summary. Pass rate excludes harness_error.

    ``_meta`` carries three distinct buckets for longitudinal analysis:

    * ``persistent_failures`` — tests evaluated at least once AND never passed.
    * ``always_harness_errored`` — tests that were ONLY ever seen with
      ``category=="harness_error"``, never producing a scorable verdict.
      Previously these were silently dropped from both pass-rate and
      persistent-failure reporting, hiding chronically broken infrastructure
      from auditors. Reporting them separately lets an auditor see both
      tool regressions and harness instability at a glance.
    * ``total_tests`` — all unique test names observed (including
      always-errored ones).
    """
    # Key by FULL SHA to avoid short-SHA collisions in longitudinal sweeps.
    by_commit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for v in verdicts:
        sha = v.get("commit", {}).get("sha", "unknown")
        by_commit[sha].append(v)

    summaries = {}
    # Track (evaluated_results_only, had_any_evaluated_run) per test
    test_runs: dict[str, list[bool]] = defaultdict(list)
    test_had_eval: dict[str, bool] = defaultdict(bool)
    # Separately track every test name ever seen, regardless of whether its
    # verdict was harness_error. Needed to compute the always-errored bucket.
    all_test_names: set[str] = set()

    for sha, vlist in by_commit.items():
        cats = Counter(v.get("verdict", {}).get("category", "unknown") for v in vlist)
        total = len(vlist)
        harness_errors = cats.get("harness_error", 0)
        evaluated = total - harness_errors
        pass_count = sum(cats.get(c, 0) for c in pass_categories)
        pass_rate = round(pass_count / evaluated * 100, 1) if evaluated > 0 else 0.0

        summaries[sha] = {
            "short_sha": sha[:8],
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
            all_test_names.add(test_name)
            cat = v.get("verdict", {}).get("category", "unknown")
            if cat == "harness_error":
                # Don't let infrastructure errors poison persistent-failure detection
                continue
            test_had_eval[test_name] = True
            test_runs[test_name].append(cat in pass_categories)

    # Persistent failures: test was evaluated at least once AND never passed.
    # Excludes tests that only had harness_error runs (those are infra issues).
    persistent_failures = [
        name for name, results in test_runs.items() if test_had_eval[name] and not any(results)
    ]
    # Always-harness-errored: test was observed but NEVER produced a scorable
    # verdict. This is a distinct class of problem from persistent scoring
    # failure and surfaces chronic infrastructure breakage to auditors.
    always_harness_errored = sorted(name for name in all_test_names if not test_had_eval[name])
    summaries["_meta"] = {
        "persistent_failures": sorted(persistent_failures),
        "always_harness_errored": always_harness_errored,
        "total_commits": len(by_commit),
        "total_tests": len(all_test_names),
        "evaluated_test_count": len(test_runs),
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


def _record_harness_error(
    all_verdicts: list[dict[str, Any]],
    output_base: Path,
    commit_sha: str,
    commit_short: str,
    commit_meta: dict[str, Any],
    tc_name: str,
    exception: BaseException,
    ground_truth: dict[str, Any] | None = None,
) -> None:
    """Build a harness_error verdict, append it to the in-memory list, AND
    persist it through ``save_verdict`` to the canonical per-case path.

    This closes the chain-of-custody gap that failure-path verdicts used to
    have: previously they appeared only in the aggregate ``all_verdicts.json``
    with no ``_verdict_sha256`` and no corresponding per-case ``verdict.json``,
    so ``--verify`` silently ignored them. Now every verdict — success OR
    failure — lives under ``output_base/<commit_sha>/<tc_name>/verdict.json``
    with a canonical serializer and self-hash.

    Best-effort: if the filesystem write fails for any reason, we still keep
    the in-memory verdict so the aggregate report and exit-code semantics
    remain correct. The chain-of-custody guarantee is additive; it must never
    regress the never-abort contract.
    """
    verdict = harness_error_verdict(
        tc_name,
        {"sha": commit_sha, "short": commit_short, **commit_meta},
        exception,
        ground_truth=ground_truth,
    )
    run_output_dir = output_base / commit_sha / tc_name
    try:
        save_verdict(verdict, run_output_dir)
    except Exception as save_err:
        print(
            f"  WARNING: failed to persist harness_error verdict for "
            f"{commit_short}/{tc_name}: {save_err}",
            file=sys.stderr,
        )
    all_verdicts.append(verdict)


def run_benchmark_matrix(
    repo_path: Path,
    commits: list[str],
    test_cases: list[Path],
    output_base: Path,
    run_single_fn: Callable[..., dict[str, Any]],
    clean_between_commits: bool = True,
    allow_dirty: bool = False,
    quiet: bool = False,
    rubric_categories: list[str] | None = None,
    pass_categories: list[str] | None = None,
    fail_categories: list[str] | None = None,
    progress: Any = None,
) -> list[dict[str, Any]]:
    """Run full benchmark matrix: commits x test_cases.

    Never aborts on tool or harness failure. Emits harness_error verdicts
    for exceptions. Calls clean_workspace() between commits if enabled.

    Progress reporting flows through a ``ui.MatrixProgress`` context manager
    so the plain/rich split lives in a single module. Callers that do not
    pass ``progress`` get the default renderer, which auto-detects TTY and
    rich availability. ``pass_categories`` / ``fail_categories`` are used
    exclusively by the default renderer for verdict coloring — they never
    affect the verdict matrix itself.
    """
    # Safety gate: validate repo and check for dirty state
    validate_repo(repo_path)
    if clean_between_commits and len(commits) > 1 and not allow_dirty:
        check_repo_clean(repo_path)

    # Lazy import so core keeps its "no presentation state" guarantee even
    # when callers don't touch the matrix runner. ``ui`` lazy-imports rich,
    # so this remains cheap when rich is not installed.
    if progress is None:
        from clawbio_bench.ui import MatrixProgress

        progress = MatrixProgress(
            total_runs=len(commits) * len(test_cases),
            quiet=quiet,
            pass_categories=pass_categories,
            fail_categories=fail_categories,
        )

    all_verdicts: list[dict[str, Any]] = []

    try:
        starting_ref = get_starting_ref(repo_path)
    except Exception:
        starting_ref = "main"

    with progress:
        try:
            for commit_sha in commits:
                # Wrap commit-level setup in try/except for truly never-abort behavior
                try:
                    commit_meta = get_commit_metadata(repo_path, commit_sha)
                except Exception:
                    commit_meta = {
                        "full_sha": commit_sha,
                        "date": "unknown",
                        "message": "unknown",
                    }
                commit_short = commit_sha[:8]

                progress.commit_header(
                    commit_short,
                    str(commit_meta.get("date", "?")),
                    str(commit_meta.get("message", "?")),
                )

                # Clean workspace before every checkout — including the first.
                # Skipping the first commit left stale ignored caches from whatever
                # state the repo was in before the sweep started.
                # For multi-commit sweeps we also purge ignored files (caches,
                # venvs, build artifacts) because they can carry over and
                # contaminate downstream commits.
                if clean_between_commits:
                    try:
                        clean_workspace(repo_path, purge_ignored=len(commits) > 1)
                    except Exception as e:
                        progress.warn(f"clean_workspace failed, skipping commit: {e}")
                        for tc in test_cases:
                            tc_name = tc.stem if tc.is_file() else tc.name
                            _record_harness_error(
                                all_verdicts,
                                output_base,
                                commit_sha,
                                commit_short,
                                commit_meta,
                                tc_name,
                                RuntimeError(f"clean_workspace failed for {commit_sha}: {e}"),
                            )
                        continue

                if not safe_checkout(repo_path, commit_sha):
                    progress.warn(f"checkout failed for {commit_short}")
                    for tc in test_cases:
                        tc_name = tc.stem if tc.is_file() else tc.name
                        _record_harness_error(
                            all_verdicts,
                            output_base,
                            commit_sha,
                            commit_short,
                            commit_meta,
                            tc_name,
                            RuntimeError(f"git checkout failed for {commit_sha}"),
                        )
                    continue

                for tc in test_cases:
                    tc_name = tc.stem if tc.is_file() else tc.name
                    progress.start_test(tc_name)

                    try:
                        gt, payload = resolve_test_case(tc)
                        verdict = run_single_fn(
                            repo_path=repo_path,
                            commit_sha=commit_sha,
                            test_case_path=tc,
                            ground_truth=gt,
                            payload_path=payload,
                            output_base=output_base,
                            commit_meta={
                                "sha": commit_sha,
                                "short": commit_short,
                                **commit_meta,
                            },
                        )
                        # Validate verdict structure and category before trusting it.
                        # strict=True runs the msgspec FullVerdictDoc check on non-error
                        # verdicts so any drift from build_verdict_doc's shape is caught
                        # here, not hours later when an auditor tries to parse the JSON.
                        try:
                            validate_verdict_schema(verdict, rubric_categories, strict=True)
                        except VerdictSchemaError as schema_err:
                            _record_harness_error(
                                all_verdicts,
                                output_base,
                                commit_sha,
                                commit_short,
                                commit_meta,
                                tc_name,
                                schema_err,
                                ground_truth=gt,
                            )
                            progress.test_schema_error()
                            continue
                        cat = verdict.get("verdict", {}).get("category", "???")
                        progress.end_test(cat)
                        all_verdicts.append(verdict)
                    except BaseException as e:
                        if isinstance(e, (KeyboardInterrupt, SystemExit)):
                            # Record partial result then re-raise
                            progress.test_interrupted()
                            _record_harness_error(
                                all_verdicts,
                                output_base,
                                commit_sha,
                                commit_short,
                                commit_meta,
                                tc_name,
                                e,
                            )
                            raise
                        progress.test_failed(e)
                        _record_harness_error(
                            all_verdicts,
                            output_base,
                            commit_sha,
                            commit_short,
                            commit_meta,
                            tc_name,
                            e,
                        )
        finally:
            try:
                restore_ref(repo_path, starting_ref)
            except Exception as e:
                progress.warn(f"restore_ref failed: {e}")

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
    mode_group = parser.add_mutually_exclusive_group(required=False)
    mode_group.add_argument(
        "--commits",
        type=str,
        default=None,
        help="Comma-separated commit SHAs (use HEAD for current)",
    )
    mode_group.add_argument(
        "--all-commits",
        action="store_true",
        help="Run against ALL commits (longitudinal sweep)",
    )
    mode_group.add_argument(
        "--smoke",
        action="store_true",
        help="Run against HEAD only (quick CI gate)",
    )
    mode_group.add_argument(
        "--regression-window",
        type=int,
        default=None,
        metavar="N",
        help="Run against last N commits (must be > 0)",
    )
    mode_group.add_argument(
        "--tagged-commits",
        action="store_true",
        help="Run against tagged commits only (releases / milestones)",
    )
    parser.add_argument(
        "--branch",
        type=str,
        default="main",
        help="Git branch for --all-commits, --regression-window, and --tagged-commits (default: main)",
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
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress per-test-case output (show only commit and final summary)",
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
        if result.returncode != 0 or not result.stdout.strip():
            raise BenchmarkConfigError(f"Failed to resolve HEAD: {result.stderr.strip()}")
        return [result.stdout.strip()]

    branch = getattr(args, "branch", "main") or "main"

    if args.all_commits:
        commits = get_all_commits(repo_path, branch=branch)
        print(f"Longitudinal sweep: {len(commits)} commits (branch: {branch})")
        return commits

    if args.regression_window:
        if args.regression_window < 1:
            raise BenchmarkConfigError("--regression-window must be a positive integer")
        all_commits = get_all_commits(repo_path, branch=branch)
        n = min(args.regression_window, len(all_commits))
        commits = all_commits[-n:]
        print(f"Regression window: last {n} commits (branch: {branch})")
        return commits

    if getattr(args, "tagged_commits", False):
        commits = get_tagged_commits(repo_path, branch=branch)
        if not commits:
            raise BenchmarkConfigError(f"No tagged commits found on branch '{branch}'")
        print(f"Tagged-commits sweep: {len(commits)} releases (branch: {branch})")
        return commits

    if args.commits:
        raw = args.commits.split(",")
        commits = []
        for c in raw:
            c = validate_commit_sha(c)
            if c == "HEAD":
                try:
                    result = subprocess.run(
                        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise BenchmarkConfigError("Timed out resolving HEAD commit") from exc
                if result.returncode != 0 or not result.stdout.strip():
                    raise BenchmarkConfigError(f"Failed to resolve HEAD: {result.stderr.strip()}")
                c = result.stdout.strip()
            commits.append(c)
        return commits

    raise BenchmarkConfigError(
        "Must specify --smoke, --commits, --regression-window, --all-commits, or --tagged-commits"
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

    # Model A fallback: glob for files. Filter dotfiles (macOS .DS_Store,
    # editor swap files) so they cannot silently enter the test matrix.
    files = sorted(
        f for f in inputs_path.glob(glob_pattern) if f.is_file() and not f.name.startswith(".")
    )
    if files:
        return files

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
    commit_meta: dict[str, Any],
    test_case_name: str,
    ground_truth: dict[str, Any],
    ground_truth_refs: dict[str, str],
    execution: ExecutionResult | None,
    outputs: dict[str, Any],
    report_analysis: dict[str, Any],
    verdict: dict[str, Any],
    driver_path: Path | None = None,
    payload_path: Path | None = None,
) -> dict[str, Any]:
    """Build a standardized verdict JSON document."""
    test_case_info = {"name": test_case_name}
    if driver_path and driver_path.exists():
        test_case_info["driver"] = str(driver_path)
        test_case_info["driver_sha256"] = sha256_file(driver_path)
    if payload_path and payload_path.exists():
        test_case_info["payload"] = payload_path.name
        test_case_info["payload_sha256"] = sha256_file(payload_path)

    # Surface reference genome for coordinate-sensitive analyses (GRCh37/38).
    # Defaults to "unspecified" rather than silently omitting, so downstream
    # consumers can flag tests that didn't declare a reference.
    reference_genome = ground_truth.get("REFERENCE_GENOME", "unspecified")

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
        "reference_genome": reference_genome,
        "execution": execution.to_dict() if execution else {},
        "outputs": outputs,
        "report_analysis": report_analysis,
        "verdict": verdict,
        "environment": _environment_signature(),
    }
    return doc


def validate_verdict_schema(
    verdict_doc: object,
    rubric_categories: list[str] | None = None,
    *,
    strict: bool = False,
) -> None:
    """Validate that a verdict document matches the expected schema.

    Two validation tiers:

    **Minimum contract** (always enforced; hand-rolled so error messages
    remain stable for callers that match on them — see test_chain_of_custody):
        - verdict: dict with 'category' (str) and 'rationale' (str)
        - test_case: dict with 'name' (str)
        - commit: dict (may be empty but must be present)

    **Strict full-schema check** (opt-in via ``strict=True``):
        Runs ``msgspec.convert(verdict_doc, FullVerdictDoc)`` after the
        minimum check, catching schema drift in successfully-executed test
        cases. Skipped automatically for ``harness_error`` verdicts, which
        are intentionally minimal. ``run_benchmark_matrix`` passes
        ``strict=True`` so real verdicts get the tighter gate; callers that
        only want to verify the minimum contract can leave ``strict=False``.

    If ``rubric_categories`` is given, ``verdict.category`` must be in that
    list OR equal to 'harness_error'.

    Raises ``VerdictSchemaError`` on any violation.
    """
    if not isinstance(verdict_doc, dict):
        raise VerdictSchemaError(f"Verdict must be a dict, got {type(verdict_doc).__name__}")

    for key in ("verdict", "test_case", "commit"):
        if key not in verdict_doc:
            raise VerdictSchemaError(f"Verdict missing required key: {key!r}")

    verdict = verdict_doc["verdict"]
    if not isinstance(verdict, dict):
        raise VerdictSchemaError(
            f"verdict_doc['verdict'] must be a dict, got {type(verdict).__name__}"
        )
    if "category" not in verdict:
        raise VerdictSchemaError("verdict missing 'category'")
    if "rationale" not in verdict:
        raise VerdictSchemaError("verdict missing 'rationale'")
    if not isinstance(verdict["category"], str):
        raise VerdictSchemaError(
            f"verdict.category must be str, got {type(verdict['category']).__name__}"
        )
    if not isinstance(verdict["rationale"], str):
        raise VerdictSchemaError(
            f"verdict.rationale must be str, got {type(verdict['rationale']).__name__}"
        )

    tc = verdict_doc["test_case"]
    if not isinstance(tc, dict) or "name" not in tc:
        raise VerdictSchemaError("test_case must be a dict with 'name'")
    if not isinstance(tc["name"], str):
        raise VerdictSchemaError(f"test_case.name must be str, got {type(tc['name']).__name__}")

    commit = verdict_doc["commit"]
    if not isinstance(commit, dict):
        raise VerdictSchemaError(f"commit must be a dict, got {type(commit).__name__}")

    if rubric_categories is not None:
        allowed = set(rubric_categories) | {"harness_error"}
        if verdict["category"] not in allowed:
            raise VerdictSchemaError(
                f"Unknown verdict category {verdict['category']!r}; "
                f"expected one of {sorted(allowed)}"
            )

    # Opt-in strict full-schema validation via msgspec. Skipped for
    # harness_error verdicts which are intentionally minimal, and for
    # callers that only want the minimum contract enforced (tests).
    if strict and verdict["category"] != "harness_error":
        # Lazy import so the minimum-contract path stays dependency-free
        # and module-import cycles can't form.
        import msgspec

        from clawbio_bench.schemas import FullVerdictDoc

        # ``save_verdict`` mutates the dict to embed ``_verdict_sha256``
        # before returning, and harnesses call ``save_verdict`` inside
        # ``run_single_fn``. By the time the matrix reaches this
        # strict-validation step the hash sidecar is already present,
        # but ``FullVerdictDoc`` uses ``forbid_unknown_fields=True`` and
        # rejects it. Validate a shallow copy with the sidecar stripped
        # so the schema gate catches real drift without fighting its
        # own chain-of-custody mechanism.
        to_validate = {k: v for k, v in verdict_doc.items() if k != "_verdict_sha256"}
        try:
            msgspec.convert(to_validate, type=FullVerdictDoc)
        except msgspec.ValidationError as exc:
            raise VerdictSchemaError(f"Verdict failed full schema: {exc}") from exc


def _enc_hook(obj: object) -> object:
    """Fallback encoder for types msgspec doesn't know about.

    msgspec natively handles ``datetime``, ``uuid.UUID``, ``decimal.Decimal``,
    ``enum.Enum``, and plain collections. This hook only fires for leftovers
    like ``pathlib.Path`` or custom classes, which get stringified — the same
    semantics the previous stdlib path used via ``json.dumps(..., default=str)``.
    """
    return str(obj)


def _canonical_verdict_bytes(verdict_doc: dict[str, Any]) -> bytes:
    """Serialize a verdict dict to canonical, deterministic bytes for hashing.

    Uses ``msgspec.json.encode(order="sorted")`` — the single canonical
    serializer for every verdict this project produces. ``order="sorted"``
    sorts both dict keys and Struct field names so byte output is stable
    across runs, across Python versions, and across machines. A trailing
    newline is appended so the on-disk file is POSIX-compliant without
    affecting the hash scheme, which covers the full file contents.
    """
    import msgspec

    # msgspec.json.encode return type is imprecise in the installed stubs
    # (effectively Any in msgspec 0.20), so concatenating b"\n" widens the
    # inferred type away from bytes. Cast explicitly.
    encoded: bytes = msgspec.json.encode(verdict_doc, order="sorted", enc_hook=_enc_hook)
    return encoded + b"\n"


def save_verdict(verdict_doc: dict[str, Any], output_dir: Path) -> Path:
    """Write verdict.json to the output directory and record its own hash.

    Computes the SHA-256 in memory over the canonical msgspec bytes, embeds
    it under ``_verdict_sha256``, then writes the final file atomically via
    write-to-temp-then-rename. This prevents TOCTOU races between the hash
    computation and the file being read by downstream consumers.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "verdict.json"
    # Strip any existing self-hash before hashing (idempotent).
    verdict_doc.pop("_verdict_sha256", None)

    # Compute the hash over the exact bytes we'll write.
    payload_bytes = _canonical_verdict_bytes(verdict_doc)
    self_hash = hashlib.sha256(payload_bytes).hexdigest()
    verdict_doc["_verdict_sha256"] = self_hash

    # Write final payload with embedded hash, atomically.
    final_bytes = _canonical_verdict_bytes(verdict_doc)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_bytes(final_bytes)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return path


def collect_verdict_hashes(output_base: Path) -> dict[str, str]:
    """Walk an output directory and collect {relative_path: _verdict_sha256}.

    Used to populate verdict_hashes.json at the end of a run, giving a
    tamper-evident index independent of the manifest.
    """
    hashes: dict[str, str] = {}
    for verdict_path in sorted(output_base.rglob("verdict.json")):
        try:
            with open(verdict_path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        stored = doc.get("_verdict_sha256")
        if stored:
            rel = verdict_path.relative_to(output_base)
            hashes[str(rel)] = stored
    return hashes


def write_verdict_hashes(output_base: Path) -> Path:
    """Write verdict_hashes.json with hashes of every verdict.json under output_base."""
    hashes = collect_verdict_hashes(output_base)
    path = output_base / "verdict_hashes.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "collected_at_utc": datetime.now(UTC).isoformat(),
                "count": len(hashes),
                "hashes": hashes,
            },
            f,
            indent=2,
            sort_keys=True,
        )
    return path


def verify_verdict_file(verdict_path: Path) -> tuple[bool, str]:
    """Verify a saved verdict.json matches its embedded _verdict_sha256.

    Returns (ok, message). Detects both semantic tampering (field changes,
    category swaps) AND byte-level tampering (trailing whitespace, reordered
    keys, reformatted indentation) by re-serializing canonically AND
    comparing the resulting bytes to the on-disk bytes.
    """
    if not verdict_path.exists():
        return False, f"Verdict file does not exist: {verdict_path}"
    try:
        raw_bytes = verdict_path.read_bytes()
        doc = json.loads(raw_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"Failed to load verdict: {exc}"
    stored_hash = doc.get("_verdict_sha256")
    if not stored_hash:
        return False, "Verdict has no _verdict_sha256 field"
    # Semantic check: re-serialize hash-stripped doc and compare
    doc_copy = {k: v for k, v in doc.items() if k != "_verdict_sha256"}
    try:
        payload_bytes = _canonical_verdict_bytes(doc_copy)
    except Exception as exc:
        return False, f"Serialization failed: {exc}"
    recomputed = hashlib.sha256(payload_bytes).hexdigest()
    if recomputed != stored_hash:
        return False, f"Hash mismatch: stored={stored_hash[:12]} computed={recomputed[:12]}"
    # Byte-level check: the file on disk must equal the canonical form of
    # the loaded doc (including the embedded hash). If someone appended
    # bytes, reordered keys, or reformatted indentation, this will detect it.
    canonical_with_hash = _canonical_verdict_bytes(doc)
    if raw_bytes != canonical_with_hash:
        return False, "Byte-level mismatch: file contents deviate from canonical form"
    return True, "ok"


def save_execution_logs(execution: ExecutionResult, output_dir: Path) -> None:
    """Save stdout.log and stderr.log."""
    output_dir.mkdir(parents=True, exist_ok=True)
    # Write as raw UTF-8 bytes so on-disk content matches hashed payload
    # exactly — write_text() can normalize newlines on Windows.
    (output_dir / "stdout.log").write_bytes(execution.stdout.encode("utf-8"))
    (output_dir / "stderr.log").write_bytes(execution.stderr.encode("utf-8"))


def verify_results_directory(results_dir: Path) -> tuple[int, int, list[str]]:
    """Deep verification of a results directory's chain of custody.

    Runs the following checks, in order:

    1. **Per-verdict self-hash** (via ``verify_verdict_file``): semantic
       re-serialization + byte-level canonical form comparison.
    2. **verdict_hashes.json sidecar index**: every entry must reference an
       existing verdict.json with a matching ``_verdict_sha256`` field, and
       every verdict.json under the tree must be listed.
    3. **Log file integrity**: ``stdout.log`` / ``stderr.log`` adjacent to
       each verdict.json are hashed and compared against the verdict's
       ``execution.stdout_sha256`` / ``stderr_sha256``. Mismatch means the
       log files were tampered with post-run.

    Returns ``(ok_count, fail_count, error_messages)``. Does not raise;
    callers decide what to do with the failure list. A non-existent
    results directory still counts as a single failure rather than an
    exception so CLI callers can emit a clean error.
    """
    errors: list[str] = []
    if not results_dir.exists():
        return 0, 1, [f"Results directory not found: {results_dir}"]

    verdict_files = sorted(results_dir.rglob("verdict.json"))
    if not verdict_files:
        return 0, 1, [f"No verdict.json files under {results_dir}"]

    ok_count = 0
    fail_count = 0

    # ── Layer 1: per-verdict self-hash ──
    for vp in verdict_files:
        ok, msg = verify_verdict_file(vp)
        if ok:
            ok_count += 1
        else:
            fail_count += 1
            errors.append(f"{vp.relative_to(results_dir)}: {msg}")

    # ── Layer 2: verdict_hashes.json sidecar reconciliation ──
    sidecars = sorted(results_dir.rglob("verdict_hashes.json"))
    for sidecar in sidecars:
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail_count += 1
            errors.append(f"{sidecar.relative_to(results_dir)}: failed to load sidecar: {exc}")
            continue
        sidecar_parent = sidecar.parent
        listed = data.get("hashes") or {}
        if not isinstance(listed, dict):
            fail_count += 1
            errors.append(f"{sidecar.relative_to(results_dir)}: 'hashes' is not a dict")
            continue
        for rel, expected_hash in listed.items():
            referenced = sidecar_parent / rel
            if not referenced.exists():
                fail_count += 1
                errors.append(f"{sidecar.relative_to(results_dir)}: references missing file {rel}")
                continue
            try:
                doc = json.loads(referenced.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                fail_count += 1
                errors.append(f"{referenced.relative_to(results_dir)}: {exc}")
                continue
            stored = doc.get("_verdict_sha256")
            if stored != expected_hash:
                fail_count += 1
                errors.append(
                    f"{sidecar.relative_to(results_dir)}: sidecar expected "
                    f"{str(expected_hash)[:12]} for {rel}, verdict stores "
                    f"{str(stored)[:12]}"
                )
            else:
                ok_count += 1

    # ── Layer 3: execution log file hash reconciliation ──
    for vp in verdict_files:
        try:
            doc = json.loads(vp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Already counted in layer 1 — skip
            continue
        exec_doc = doc.get("execution") or {}
        if not isinstance(exec_doc, dict):
            continue
        run_dir = vp.parent
        for stream in ("stdout", "stderr"):
            log_path = run_dir / f"{stream}.log"
            expected_hash = exec_doc.get(f"{stream}_sha256")
            if not expected_hash:
                continue
            if not log_path.exists():
                # Not every harness writes log files (e.g. static-only
                # metagenomics path). Skip silently.
                continue
            actual_hash = sha256_file(log_path)
            if actual_hash != expected_hash:
                fail_count += 1
                errors.append(
                    f"{log_path.relative_to(results_dir)}: hash mismatch "
                    f"(stored={str(expected_hash)[:12]} "
                    f"actual={str(actual_hash)[:12]})"
                )
            else:
                ok_count += 1

    return ok_count, fail_count, errors


# ---------------------------------------------------------------------------
# Standard Harness Runner
# ---------------------------------------------------------------------------


def run_harness_main(
    benchmark_name: str,
    benchmark_version: str,
    default_inputs_dir: str,
    run_single_fn: Callable[..., dict[str, Any]],
    rubric_categories: list[str],
    pass_categories: list[str],
    fail_categories: list[str],
    ground_truth_refs: dict[str, str],
    category_legend: dict[str, dict[str, Any]],
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
    quiet = getattr(args, "quiet", False)
    verdicts = run_benchmark_matrix(
        repo_path,
        commits,
        test_cases,
        output_base,
        run_single_fn,
        allow_dirty=allow_dirty,
        quiet=quiet,
        rubric_categories=rubric_categories,
        pass_categories=pass_categories,
        fail_categories=fail_categories,
    )

    # Write aggregated outputs — include tag metadata when available.
    try:
        tag_map = get_commit_tags(repo_path)
    except Exception:
        tag_map = {}
    heatmap = build_heatmap_data(verdicts, category_legend, tag_map=tag_map)
    with open(output_base / "heatmap_data.json", "w", encoding="utf-8") as f:
        json.dump(heatmap, f, indent=2, default=str)

    summary = build_summary(verdicts, pass_categories)
    with open(output_base / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    with open(output_base / "all_verdicts.json", "w", encoding="utf-8") as f:
        json.dump(verdicts, f, indent=2, default=str)

    # Chain of custody: collect per-verdict self-hashes into a sidecar index
    write_verdict_hashes(output_base)

    # Print summary
    print(f"\n{'=' * 60}")
    print("BENCHMARK COMPLETE")
    print(f"{'=' * 60}")
    for sha_key, s in summary.items():
        if sha_key == "_meta":
            continue
        display_sha = s.get("short_sha") or sha_key[:8]
        print(
            f"\n  {display_sha} ({s['commit_date'][:10]}): "
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
    always_err = meta.get("always_harness_errored", [])
    if always_err:
        print(
            f"\n  Always harness_error — infrastructure broken for these tests "
            f"({len(always_err)}):"
        )
        for name in always_err:
            print(f"    - {name}")

    print(f"\nResults: {output_base}")
