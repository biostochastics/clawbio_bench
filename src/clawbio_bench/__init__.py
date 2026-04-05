# SPDX-License-Identifier: MIT
"""ClawBio Benchmark Suite \u2014 machine-readable harnesses for auditing bioinformatics tools."""

from importlib.metadata import version as _pkg_version

__version__ = _pkg_version("clawbio-bench")

# Canonical project metadata. Kept here as module-level constants so the CLI
# can surface them via --about / --version / --list without re-parsing
# pyproject.toml at runtime (which isn't available after a wheel install).
# Keep in sync with pyproject.toml [project] / [project.urls].
AUTHOR = "Sergey A. Kornilov"
AUTHOR_EMAIL = "sergey@biostochastics.com"
LICENSE = "MIT"
HOMEPAGE_URL = "https://github.com/biostochastics/clawbio_bench"
ISSUES_URL = "https://github.com/biostochastics/clawbio_bench/issues"
# The system under audit. Explicit so reviewers can pivot from a verdict
# back to the target repository without leaving the terminal.
AUDIT_TARGET_URL = "https://github.com/ClawBio/ClawBio"

PROJECT_METADATA: dict[str, str] = {
    "name": "clawbio-bench",
    "version": __version__,
    "description": ("Machine-readable benchmark harnesses for auditing bioinformatics tools"),
    "author": AUTHOR,
    "email": AUTHOR_EMAIL,
    "license": LICENSE,
    "homepage": HOMEPAGE_URL,
    "issues": ISSUES_URL,
    "audit_target": AUDIT_TARGET_URL,
}
