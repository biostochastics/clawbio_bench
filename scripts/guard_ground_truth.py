#!/usr/bin/env python3
"""PreToolUse guard: block edits to load-bearing audit artifacts.

Protected paths:
  - test_cases/**/ground_truth.txt  (audit contract ground truth)
  - schemas/*.json                  (canonical JSON Schema artifacts)
  - results/**                      (committed verdict baselines)

Exit 0  = allow
Exit 2  = block (with reason on stderr)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PROTECTED_PATTERNS: list[tuple[str, str]] = [
    # (path component test, human-readable reason)
    (
        "test_cases/",
        "Ground-truth files define the audit contract. "
        "Edits change what the benchmark measures. "
        "Run the ground-truth-auditor agent first, then edit deliberately.",
    ),
    (
        "schemas/",
        "JSON Schema artifacts are the external contract for verdict consumers. "
        "Regenerate via `python scripts/gen_schemas.py`, don't hand-edit.",
    ),
    (
        "results/",
        "Committed results are the baseline for regression detection. "
        "Use `clawbio-bench` to regenerate, don't hand-edit.",
    ),
]


def is_protected(file_path: str) -> tuple[bool, str]:
    """Check if a file path falls under a protected pattern."""
    try:
        resolved = Path(file_path).resolve()
        rel = resolved.relative_to(REPO_ROOT)
    except (ValueError, OSError):
        return False, ""

    rel_posix = rel.as_posix()

    for pattern, reason in PROTECTED_PATTERNS:
        if pattern in rel_posix:
            if pattern == "test_cases/" and resolved.name != "ground_truth.txt":
                continue
            if pattern == "schemas/" and resolved.suffix.lower() != ".json":
                continue
            return True, reason

    return False, ""


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(0)

    for file_path in sys.argv[1:]:
        blocked, reason = is_protected(file_path)
        if blocked:
            print(
                f"BLOCKED: {os.path.basename(file_path)} is a protected audit artifact.\n"
                f"Reason: {reason}",
                file=sys.stderr,
            )
            sys.exit(2)


if __name__ == "__main__":
    main()
