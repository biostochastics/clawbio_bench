# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

clawbio-bench is a benchmark suite that audits bioinformatics tools for safety, correctness, and honesty. It produces machine-readable JSON verdicts with SHA-256 chain of custody, category-level rubrics (not binary pass/fail), and longitudinal tracking across git history. The primary audit target is [ClawBio](https://github.com/ClawBio/ClawBio).

Three pinned runtime dependencies (`msgspec`, `regex`, `ruamel.yaml`) — each justified in `pyproject.toml` against the "audit tool trusted-base" constraint. Python 3.11+. `matplotlib` and `rich` are optional extras (`[viz]`, `[ui]`).

## Commands

```bash
# Install
pip install -e ".[dev]"           # pytest, ruff, pre-commit
pip install -e ".[viz]"           # + matplotlib

# Lint & format
ruff check src/ tests/
ruff format src/ tests/
ruff check src/ tests/ --fix      # auto-fix

# Unit tests (no ClawBio repo needed)
pytest tests/ -x -q -k "not test_harness_smoke"

# Single test
pytest tests/test_smoke_suite.py::test_core_ground_truth_parsing -x

# Smoke tests (requires ClawBio repo)
pytest tests/ --repo /path/to/ClawBio
pytest tests/ --repo /path/to/ClawBio -k "orchestrator"

# CLI usage
clawbio-bench --smoke --repo /path/to/ClawBio
clawbio-bench --smoke --harness equity --repo /path/to/ClawBio
clawbio-bench --regression-window 10 --repo /path/to/ClawBio
clawbio-bench --tagged-commits --repo /path/to/ClawBio
clawbio-bench --heatmap results/suite/20260404_120000/
clawbio-bench --list
clawbio-bench --dry-run --smoke --repo /path/to/ClawBio
```

## Architecture

### Core Framework (`core.py`)

The shared engine that all harnesses use. Provides:
- **Git operations**: checkout, clean, commit metadata, dirty-repo safety gate
- **Ground truth parsing**: `# KEY: value` headers from test case files (Model A: self-contained file; Model B: directory with `ground_truth.txt` + payload sidecars)
- **Execution capture**: subprocess runner with timeout, fallback, and full output capture
- **Verdict building**: standardized JSON docs with chain of custody (SHA-256 of every artifact)
- **Matrix runner** (`run_benchmark_matrix`): the (commits × test_cases) loop that never aborts — exceptions become `harness_error` verdicts
- **Aggregation**: heatmap data builder, per-commit summary with persistent-failure detection

### CLI (`cli.py`)

Entry point (`clawbio-bench`). Contains `HARNESS_REGISTRY` mapping harness names to their modules. Orchestrates running one or all harnesses, writing aggregate reports. Modes: `--smoke` (HEAD only), `--regression-window N`, `--all-commits`, `--tagged-commits` (releases only), `--commits SHA,...`.

### Harnesses (`harnesses/*.py`)

Each harness follows the same contract — module-level constants + a `run_single_<name>()` function:

```
BENCHMARK_NAME, BENCHMARK_VERSION
RUBRIC_CATEGORIES, PASS_CATEGORIES, FAIL_CATEGORIES
GROUND_TRUTH_REFS, CATEGORY_LEGEND

def run_single_<name>(repo_path, commit_sha, test_case_path,
                       ground_truth, payload_path, output_base, commit_meta) -> dict
```

Nine harnesses: orchestrator (54 tests), equity (15), pharmgx (44), nutrigx (10), metagenomics (7), clinical_variant_reporter Phase 1 (5), cvr_identity Phase 2c (6), cvr_correctness Phase 2a (13), finemapping (20). Total: 174 test cases.

The `cvr_identity` and `cvr_correctness` harnesses both audit the same ClawBio `clinical-variant-reporter` skill but with different rubrics — Phase 2c validates HGVS/transcript/assembly representation, Phase 2a validates ACMG/AMP criterion-level correctness with dual-layer ground truth (gold standard `EXPECTED_*` vs tool self-consistency `EXPECTED_TOOL_*`).

The `run_single_*` function must never raise — return `harness_error` verdicts for infrastructure failures.

### Test Cases (`test_cases/<harness>/`)

Bundled as package data. Model B directories (e.g., `eq_01/ground_truth.txt` + `input.vcf`). Naming: `{prefix}_{nn}` with categories in the ground truth headers.

### Tests (`tests/`)

- `conftest.py`: `--repo` option, session-scoped `clawbio_repo` fixture with git worktree isolation, auto-parametrizes `harness_name` from `HARNESS_REGISTRY`
- `test_smoke_suite.py`: runs each harness in `--smoke` mode, validates output structure. Also has unit tests for core parsing and path traversal blocking.

## Key Design Rules

- **Never abort**: every (commit, test_case) pair must produce a verdict. Exceptions → `harness_error`.
- **Category-level verdicts**: each category maps to a specific remediation. `fst_mislabeled` ≠ `fst_incorrect`.
- **Offline only**: no network calls at runtime. Reference values are pre-computed and embedded.
- **Chain of custody**: SHA-256 of every input, output, and ground truth file in every verdict.
- **Safe by default**: dirty repo gate, path traversal validation on PAYLOAD references, no `shell=True`.

## Ruff Configuration

Line length 99, target Python 3.11. Rules: E, F, W, I, UP, B, SIM. `SIM102` (nested ifs) is ignored because multi-condition scoring branches are clearer unnested. `E501` ignored (handled by formatter).

## CI

GitHub Actions: lint → unit tests (3.11/3.12/3.13) → smoke (pinned ClawBio ref) → regression (last 20 commits, main-only). Pre-commit hooks run ruff format, ruff check, and unit tests.

Exit code semantics: 0 = all pass, 1 = findings exist (expected), ≥2 = harness crash.

## Adding a New Harness

1. Define category taxonomy in `harnesses/new_harness.py` (see CONTRIBUTING.md)
2. Create test cases in `test_cases/new_tool/` using Model B format
3. Implement `run_single_new_tool()` using `harness_core.capture_execution()` and `harness_core.build_verdict_doc()`
4. Register in `HARNESS_REGISTRY` in `cli.py`
5. Smoke tests auto-discover via the registry — no test code changes needed
