# Contributing to clawbio-bench

This guide explains how to add a new benchmark harness for auditing a
computational biology tool. The framework is designed so that ClawBio is the
first (and reference) target, but any tool with a git history can be
benchmarked.

## Prerequisites

```bash
git clone https://github.com/biostochastics/clawbio_bench.git
cd clawbio_bench
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

Optional for PDF report generation:

```bash
brew install typst          # macOS
# or: cargo install typst-cli
```

## Adding a New Harness

### Step 1: Define Your Category Taxonomy

Each harness has its own rubric — a set of verdict categories that capture the
specific ways a tool can succeed or fail. Categories are more actionable than
binary pass/fail.

**Design principles:**

- Each category should map to a **distinct remediation path** — if two
  categories have the same fix, merge them.
- Include `harness_error` as the catch-all infrastructure category. It is
  excluded from the pass rate and sorted into its own severity tier in the
  PDF report.
- Separate "correct but for the wrong reason" from "correct" — this is the
  **honesty** dimension.
- Name categories as `noun_adjective` (e.g., `fst_mislabeled`,
  `injection_blocked`, `pip_nan_silent`).
- Define `FAIL_CATEGORIES` (critical tier, red in report) separately from
  non-pass/non-fail categories (warning tier, amber). Categories not in
  either `PASS_CATEGORIES` or `FAIL_CATEGORIES` become warnings.

**Example (equity scorer):**

```python
RUBRIC_CATEGORIES = [
    "fst_correct",       # FST value AND label correct
    "fst_incorrect",     # FST value wrong
    "fst_mislabeled",    # FST value correct, label wrong (honesty failure)
    "heim_bounded",      # HEIM score in [0, 100]
    "heim_unbounded",    # HEIM > 100
    "edge_handled",      # Edge case input handled gracefully
    "edge_crash",        # Edge case input crashed the tool
    "harness_error",     # Infrastructure failure
]

PASS_CATEGORIES = ["fst_correct", "heim_bounded", "edge_handled"]
FAIL_CATEGORIES = ["fst_incorrect", "fst_mislabeled", "heim_unbounded", "edge_crash"]
# Note: harness_error is implicitly in neither — it becomes a "harness errors" tier.
```

### Step 2: Create Test Cases

Two models are supported:

**Model A (self-contained file):** Best for tools that accept a single input
file. The test file contains both the input data and the ground truth headers.

```
# BENCHMARK: my-tool v0.1.1
# GROUND_TRUTH_VALUE: 42.0
# FINDING_CATEGORY: value_correct
# TOLERANCE: 0.001
# CITATION: Author (2024). Journal, vol(issue), pages.
<actual input data follows>
```

**Model B (directory with driver + payload):** Best for tools that need
structured inputs or multiple files. This is the preferred model for new
harnesses.

```
test_cases/my_tool/tc_01/
  ground_truth.txt     # Driver: headers defining expected results
  input.vcf            # Payload: the actual input file
  population_map.csv   # Additional payload files as needed
```

The `ground_truth.txt` driver:

```
# BENCHMARK: my-tool v0.1.1
# PAYLOAD: input.vcf
# GROUND_TRUTH_VALUE: 0.300
# FINDING: Description of what this test exists to detect
# FINDING_CATEGORY: value_correct
# HAZARD_METRIC: Why this matters for safety/correctness
# DERIVATION: Mathematical or logical steps to reproduce the reference value
# CITATION: Author (2024). Journal, vol(issue), pages.
```

### Step 3: Compute and Document Ground Truth

Every reference value must have:

1. **A derivation** — the mathematical or logical steps to reproduce it
2. **A citation** — the authority or formula it's based on
3. **A tier** — one of:
   - *Analytically derived*: computed from known inputs (e.g., FST from
     synthetic allele frequencies)
   - *Authority-derived*: from a published standard (e.g., CPIC guidelines
     for pharmacogenomics)
   - *Pattern-derived*: from established security patterns (e.g., OWASP for
     injection testing)

Document these in your test case headers (FINDING, HAZARD_METRIC, DERIVATION
fields) — the Typst PDF report extracts and renders them automatically.

### Step 4: Write the Harness Driver

Create `src/clawbio_bench/harnesses/my_tool_harness.py`:

```python
from __future__ import annotations

from pathlib import Path

from clawbio_bench import core as harness_core

BENCHMARK_NAME = "my-tool"
BENCHMARK_VERSION = "1.0.0"

RUBRIC_CATEGORIES = [...]
PASS_CATEGORIES = [...]
FAIL_CATEGORIES = [...]

GROUND_TRUTH_REFS = {
    "SOURCE_KEY": "Author (Year). Title. Journal. DOI.",
}

CATEGORY_LEGEND = {
    "value_correct":   {"color": "#22c55e", "label": "Value correct"},
    "value_incorrect": {"color": "#ef4444", "label": "Value incorrect"},
    "harness_error":   {"color": "#9ca3af", "label": "Harness error"},
}


def run_single_my_tool(
    repo_path: Path,
    commit_sha: str,
    test_case_path: Path,
    ground_truth: dict,
    payload_path: Path | None,
    output_base: Path,
    commit_meta: dict,
) -> dict:
    """Execute my-tool for one (commit, test_case) pair.

    Must NEVER raise — return a harness_error verdict for infrastructure
    failures so the matrix runner can continue to the next test case.
    """
    try:
        # 1. Set up output directories
        output_dir = harness_core.prepare_output_dir(output_base, commit_sha, test_case_path)

        # 2. Build and execute the command
        cmd = [str(repo_path / "my_tool.py"), "--input", str(payload_path or test_case_path)]
        execution = harness_core.capture_execution(cmd, output_dir, timeout=30)

        # 3. Parse the tool's output
        # ... (tool-specific parsing)

        # 4. Score against ground truth
        category = "value_correct"  # or determine from comparison
        rationale = "Value matches reference within tolerance"

        # 5. Build and save verdict
        verdict_doc = harness_core.build_verdict_doc(
            benchmark_name=BENCHMARK_NAME,
            benchmark_version=BENCHMARK_VERSION,
            commit_sha=commit_sha,
            commit_meta=commit_meta,
            test_case_path=test_case_path,
            ground_truth=ground_truth,
            ground_truth_refs=GROUND_TRUTH_REFS,
            category=category,
            rationale=rationale,
            details={...},              # key-value dict of verdict specifics
            pass_categories=PASS_CATEGORIES,
            fail_categories=FAIL_CATEGORIES,
            payload_path=payload_path,
        )
        harness_core.save_verdict(verdict_doc, output_dir)
        harness_core.save_execution_logs(execution, output_dir)
        return verdict_doc

    except Exception as exc:
        return harness_core.build_verdict_doc(
            benchmark_name=BENCHMARK_NAME,
            benchmark_version=BENCHMARK_VERSION,
            commit_sha=commit_sha,
            commit_meta=commit_meta,
            test_case_path=test_case_path,
            ground_truth=ground_truth,
            ground_truth_refs=GROUND_TRUTH_REFS,
            category="harness_error",
            rationale=f"Harness infrastructure error: {exc}",
            details={"exception": str(exc)},
            pass_categories=PASS_CATEGORIES,
            fail_categories=FAIL_CATEGORIES,
        )
```

### Step 5: Register in CLI

Add your harness to `HARNESS_REGISTRY` in `src/clawbio_bench/cli.py`:

```python
HARNESS_REGISTRY = {
    ...
    "my_tool": {
        "module": "clawbio_bench.harnesses.my_tool_harness",
        "run_fn": "run_single_my_tool",          # must match the function name
        "benchmark_name": "my-tool",
        "default_inputs_dir": "my_tool",          # under test_cases/
        "description": "My tool benchmark description",
        # Optional: "glob_pattern": "*.txt" (default is "*")
    },
}
```

Place test cases under `src/clawbio_bench/test_cases/my_tool/`.

### Step 6: Verify

Your harness is automatically discovered by `test_smoke_suite.py` via
`HARNESS_REGISTRY` — no test code changes needed.

```bash
# Unit tests (no target repo needed)
pytest tests/ -x -q -k "not test_harness_smoke"

# Smoke test against target repo
pytest tests/ --repo /path/to/target -k "my_tool"

# Generate PDF report
python scripts/generate_report.py results/suite/<timestamp>/
```

### Step 7: Update Documentation

- Add your harness to the **Coverage Scope** section of `README.md`
- Add a CHANGELOG entry under `[Unreleased]` following
  [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format
  (use `Added` / `Changed` / `Fixed` sections)
- Ground truth derivations are embedded in test case headers (FINDING,
  HAZARD_METRIC, DERIVATION) and rendered automatically in the PDF report

## Development Workflow

```bash
# Format and lint
ruff format src/ tests/ scripts/
ruff check src/ tests/ scripts/ --fix

# Typecheck
mypy src/clawbio_bench/markdown_report.py --ignore-missing-imports
mypy scripts/generate_report.py --ignore-missing-imports

# Run unit tests
pytest tests/ -x -q -k "not test_harness_smoke"

# Pre-commit runs automatically on git commit
git commit -m "feat: add my-tool harness"
```

## Code Style

- **Line length**: 99 (configured in `pyproject.toml`)
- **Target**: Python 3.11+
- **Linter**: ruff (E, F, W, I, UP, B, SIM rules)
- **Formatter**: ruff format
- **Type annotations**: encouraged, checked with mypy
- **Docstrings**: required for public functions

## Commit Messages

Follow conventional commits:

- `feat:` new harness or feature
- `fix:` bug fix
- `docs:` documentation only
- `refactor:` code change that neither fixes a bug nor adds a feature
- `test:` adding or correcting tests
- `chore:` maintenance tasks

## Code of Conduct

Be rigorous. Findings that could lead to clinical harm get CRITICAL severity
regardless of likelihood. Do not soften findings to be diplomatic. The burden
of proof is on the tool to demonstrate safety, not on the auditor to
demonstrate harm.
