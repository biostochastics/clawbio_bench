# Contributing to clawbio-bench

This guide explains how to add a new benchmark harness for auditing a computational biology tool. The framework is designed so that ClawBio is the first (and reference) target, but any tool with a git history can be benchmarked.

## Prerequisites

```bash
git clone https://github.com/biostochastics/clawbio_benchmark.git
cd clawbio_benchmark
pip install -e ".[dev]"
pre-commit install
```

## Adding a New Harness

### Step 1: Define Your Category Taxonomy

Each harness has its own rubric — a set of verdict categories that capture the specific ways a tool can succeed or fail. Categories are more actionable than binary pass/fail.

**Design principles:**
- Each category should map to a distinct remediation path
- Include `harness_error` as the catch-all infrastructure category (excluded from pass rate)
- Separate "correct but for the wrong reason" from "correct"
- Name categories as `noun_adjective` (e.g., `fst_mislabeled`, `injection_blocked`)

**Example (equity scorer):**
```python
RUBRIC_CATEGORIES = [
    "fst_correct",       # FST value AND label correct
    "fst_incorrect",     # FST value wrong
    "fst_mislabeled",    # FST value correct, label wrong
    "heim_bounded",      # HEIM score in [0, 100]
    "heim_unbounded",    # HEIM > 100
    "harness_error",     # Infrastructure failure
]

PASS_CATEGORIES = ["fst_correct", "heim_bounded"]
FAIL_CATEGORIES = ["fst_incorrect", "fst_mislabeled", "heim_unbounded"]
```

### Step 2: Create Test Cases

Two models are supported:

**Model A (self-contained file):** Best for tools that accept a single input file. The test file contains both the input data and the ground truth headers.

```
# BENCHMARK: my-tool v1.0.0
# GROUND_TRUTH_VALUE: 42.0
# FINDING_CATEGORY: value_correct
# TOLERANCE: 0.001
# CITATION: Author (2024). Journal, vol(issue), pages.
<actual input data follows>
```

**Model B (directory with driver + payload):** Best for tools that need structured inputs or multiple files.

```
test_cases/my_tool/tc_01/
  ground_truth.txt     # Driver: headers defining expected results
  input.vcf            # Payload: the actual input file
  population_map.csv   # Additional payload files as needed
```

The `ground_truth.txt` driver:
```
# BENCHMARK: my-tool v1.0.0
# PAYLOAD: input.vcf
# GROUND_TRUTH_VALUE: 0.300
# FINDING_CATEGORY: value_correct
# DERIVATION: <how the reference value was computed>
# CITATION: Author (2024). Journal, vol(issue), pages.
```

### Step 3: Compute and Document Ground Truth

Every reference value must have:
1. **A derivation** — the mathematical or logical steps to reproduce it
2. **A citation** — the authority or formula it's based on
3. **A tier** — one of:
   - *Analytically derived*: computed from known inputs (e.g., FST from synthetic allele frequencies)
   - *Authority-derived*: from a published standard (e.g., CPIC guidelines for PGx)
   - *Pattern-derived*: from established security patterns (e.g., OWASP for injection)

Document these in your test case headers AND in `docs/ground-truth-derivation.md`.

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
    "value_correct": {"color": "#22c55e", "label": "Value correct"},
    "value_incorrect": {"color": "#ef4444", "label": "Value incorrect"},
    "harness_error": {"color": "#9ca3af", "label": "Harness error"},
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
    """Execute my-tool for one (commit, test_case) pair."""
    # 1. Set up output directories
    # 2. Build and execute the command
    # 3. Parse the tool's output
    # 4. Score against ground truth
    # 5. Build and save verdict document
    ...
```

Key requirements:
- **Never raise** — return `harness_error` verdicts for infrastructure failures
- Use `harness_core.capture_execution()` for subprocess calls
- Use `harness_core.build_verdict_doc()` for standardized output
- Use `harness_core.save_verdict()` and `harness_core.save_execution_logs()`

### Step 5: Register in CLI

Add your harness to `HARNESS_REGISTRY` in `src/clawbio_bench/cli.py`:

```python
HARNESS_REGISTRY = {
    ...
    "my_tool": {
        "module": "clawbio_bench.harnesses.my_tool_harness",
        "benchmark_name": "my-tool",
        "default_inputs_dir": "my_tool",
        "description": "My tool benchmark description",
    },
}
```

### Step 6: Add Smoke Tests

Your harness is automatically picked up by `test_smoke_suite.py` via `HARNESS_REGISTRY`. Verify:

```bash
# Unit tests (no target repo needed)
pytest tests/ -k "not test_harness_smoke"

# Smoke test against target repo
pytest tests/ --repo /path/to/target --allow-dirty -k "my_tool"
```

### Step 7: Update Documentation

- Add your harness to `README.md`
- Document ground truth derivations in `docs/ground-truth-derivation.md`
- Add a CHANGELOG entry

## Development Workflow

```bash
# Format and lint
ruff format src/ tests/
ruff check src/ tests/ --fix

# Run unit tests
pytest tests/ -x -q -k "not test_harness_smoke"

# Pre-commit runs automatically on git commit
git commit -m "Add my-tool harness"
```

## Multi-Model Review (Recommended)

For harnesses that will be used in safety-critical contexts, we recommend multi-model review before merging. The ClawBio harnesses went through 6 rounds of review across 5+ AI models, catching issues that single-reviewer processes missed (e.g., FST fallback logic, harness_error categorization, path traversal validation).

## Code of Conduct

Be rigorous. Findings that could lead to clinical harm get CRITICAL severity regardless of likelihood. Do not soften findings to be diplomatic. The burden of proof is on the tool to demonstrate safety, not on the auditor to demonstrate harm.
