"""Pytest configuration for the ClawBio benchmark suite.

Provides:
  - --repo CLI option (or CLAWBIO_REPO env var)
  - --allow-dirty flag for non-isolated runs
  - Session-scoped clawbio_repo fixture with git worktree isolation
  - Auto-discovery of harnesses from HARNESS_REGISTRY
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from clawbio_bench.cli import HARNESS_REGISTRY

# ---------------------------------------------------------------------------
# CLI options
# ---------------------------------------------------------------------------


def pytest_addoption(parser):
    parser.addoption(
        "--repo",
        type=lambda p: Path(p).resolve(),
        default=None,
        help="Path to the ClawBio git repository",
    )
    parser.addoption(
        "--allow-dirty",
        action="store_true",
        default=False,
        help="Allow running directly against the repo (will mutate checkout!)",
    )


# ---------------------------------------------------------------------------
# Auto-discover harnesses for parametrization
# ---------------------------------------------------------------------------


def pytest_generate_tests(metafunc):
    if "harness_name" in metafunc.fixturenames:
        names = list(HARNESS_REGISTRY.keys())
        metafunc.parametrize("harness_name", names, ids=names)


# ---------------------------------------------------------------------------
# Repo fixture with worktree isolation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def clawbio_repo(request):
    """Yield an isolated copy of the ClawBio repo.

    Prefers a git worktree so the developer's original checkout is untouched.
    Falls back to the original path only when --allow-dirty is passed.
    """
    repo = request.config.getoption("--repo")
    if repo is None:
        env = os.getenv("CLAWBIO_REPO")
        if env:
            repo = Path(env).resolve()
        else:
            # Default: look for ClawBio sibling to benchmark-suite
            repo = (Path(__file__).resolve().parent.parent.parent / "ClawBio").resolve()

    if not repo.exists():
        pytest.skip(f"Repository path does not exist: {repo}")
    git_path = repo / ".git"
    if not (git_path.is_dir() or git_path.is_file()):
        pytest.skip(f"Not a git repository: {repo}")

    allow_dirty = request.config.getoption("--allow-dirty")

    # Try worktree isolation
    tmpdir = tempfile.mkdtemp(prefix="clawbio_bench_")
    worktree_path = Path(tmpdir) / "wt"
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree_path), "HEAD"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            yield worktree_path
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", "-f", str(worktree_path)],
                capture_output=True,
                timeout=60,
            )
            shutil.rmtree(tmpdir, ignore_errors=True)
            return
    except Exception:
        pass

    # Worktree failed — require explicit opt-in
    shutil.rmtree(tmpdir, ignore_errors=True)
    if not allow_dirty:
        pytest.skip(
            "Git worktree creation failed and --allow-dirty not set. "
            "The benchmark suite mutates repo state. "
            "Pass --allow-dirty to proceed."
        )

    # Dirty guard even with --allow-dirty
    dirty_check = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if dirty_check.stdout.strip():
        pytest.skip(
            f"Repository has uncommitted changes: {repo}\n"
            "Commit or stash changes before running benchmarks."
        )

    yield repo
