"""CLI exit code semantics.

Verifies the three-level exit-code contract the reusable workflow depends on:

* 0 — all harnesses pass
* 1 — findings exist (advisory; never fails CI by itself)
* 2 — at least one harness raised an infrastructure exception
  (e.g. ``run_single_harness`` threw), distinct from an in-matrix
  ``harness_error`` verdict emitted by the matrix runner
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clawbio_bench import cli


def _init_fake_repo(root: Path) -> Path:
    """Create a minimal clean git repo the benchmark can run against."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("hi")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"],
        cwd=root,
        check=True,
        env={"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "a@b.c", "PATH": "/usr/bin:/bin"},
    )
    return root


def test_cli_exits_2_when_harness_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A raising run_single_harness must yield exit code 2, not 1."""
    repo = _init_fake_repo(tmp_path / "repo")
    out = tmp_path / "out"

    # Force the harness runner to raise — simulates an infra-level bug in the
    # benchmark itself (e.g. a BenchmarkConfigError, a bad import, etc.)
    def boom(*args, **kwargs):
        raise RuntimeError("simulated harness infrastructure failure")

    monkeypatch.setattr(cli, "run_single_harness", boom)
    monkeypatch.setattr(
        "sys.argv",
        [
            "clawbio-bench",
            "--smoke",
            "--repo",
            str(repo),
            "--output",
            str(out),
            "--harness",
            "orchestrator",
            "--allow-dirty",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 2, (
        f"expected exit 2 on harness crash, got {exc_info.value.code}. "
        "The reusable workflow's `fail_on_harness_crash` gate depends on this."
    )


def test_cli_exits_0_when_all_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Clean pass must yield exit 0."""
    repo = _init_fake_repo(tmp_path / "repo")
    out = tmp_path / "out"

    def clean_pass(*args, **kwargs):
        return {
            "version": "1.0.0",
            "pass": True,
            "total_cases": 1,
            "evaluated": 1,
            "harness_errors": 0,
            "pass_count": 1,
            "fail_count": 0,
            "pass_rate": 100.0,
            "pass_categories": ["ok"],
            "categories": {"ok": 1},
            "critical_failures": [],
        }

    monkeypatch.setattr(cli, "run_single_harness", clean_pass)
    monkeypatch.setattr(
        "sys.argv",
        [
            "clawbio-bench",
            "--smoke",
            "--repo",
            str(repo),
            "--output",
            str(out),
            "--harness",
            "orchestrator",
            "--allow-dirty",
        ],
    )

    # SystemExit(0) with code=0 or code=None both count as success
    try:
        cli.main()
        exit_code = 0
    except SystemExit as e:
        exit_code = e.code or 0
    assert exit_code == 0
