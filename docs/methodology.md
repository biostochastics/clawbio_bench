# Audit Methodology

This document describes the benchmark design methodology used in clawbio-bench. It serves both as documentation for the ClawBio audit and as a reference for auditing other computational biology tools.

## Rubric Design Pattern

### Why Categories, Not Pass/Fail

Binary pass/fail obscures the nature of failures. Consider two tests that both "fail":

- **Test A fails**: The tool computes FST = 0.301 when the ground truth is 0.300. The formula is correct but numerical precision is slightly off. **Remediation**: adjust tolerance or rounding.
- **Test B fails**: The tool computes FST = 0.300 but labels it "Hudson FST" when it actually computes Nei's GST. **Remediation**: fix the label, review all metric names for accuracy.

In a pass/fail system, both are "fail." In our category system, Test A is `fst_incorrect` and Test B is `fst_mislabeled` — different categories with different severity and remediation paths.

### Category Taxonomy Design

Each harness defines 6-10 categories following these rules:

1. **Mutually exclusive**: every verdict maps to exactly one category
2. **Exhaustive**: every possible tool behavior has a category (including `harness_error` for infrastructure)
3. **Actionable**: each category implies a specific remediation
4. **Severity-ordered**: categories carry implicit severity (CRITICAL > HIGH > MEDIUM > LOW > INFO)

The `harness_error` category exists in every harness as the catch-all for infrastructure failures (timeout, parse error, missing file). It is excluded from pass rate calculations to avoid penalizing the tool for harness bugs.

## Ground Truth Standards

### Three Tiers

| Tier | Source | Example | Strength |
|------|--------|---------|----------|
| Analytically derived | Mathematical computation from known inputs | FST from synthetic VCF with known allele frequencies | Strongest — independently reproducible |
| Authority-derived | Published standard or guideline | CPIC phenotype-to-drug mappings | Strong — accepted by field |
| Pattern-derived | Established security or quality pattern | OWASP command injection patterns | Moderate — consensus-based |

Every test case documents its tier and citation in the ground truth header:
```
# DERIVATION: p_total=0.5, HT=0.5, HS=0.0, GST=1.0
# CITATION: Nei (1973). PNAS 70(12):3321-3323
```

### Derivation Requirements

- Show all intermediate values, not just the final answer
- Reference the specific formula (equation number, paper section)
- For authority-derived values, cite the specific guideline version and date
- For synthetic test data, document how it was generated and why it produces the expected result

## The Never-Abort Principle

Every (commit, test_case) pair must produce a verdict. This is non-negotiable for longitudinal analysis — gaps in the matrix make it impossible to distinguish "the tool was broken at this commit" from "the harness crashed."

Implementation:
1. `harness_error` is a verdict category, not an exception
2. `run_benchmark_matrix()` wraps every test case execution in try/except
3. Even `KeyboardInterrupt` produces a partial verdict before re-raising
4. Timeouts produce verdicts with `exit_code: -1`

## Chain of Custody

SHA-256 hashing at every step ensures tamper evidence:

1. **Test case inputs**: hash of ground truth file + payload files, recorded in manifest.json
2. **Tool outputs**: hash of stdout, stderr, and any output files
3. **Verdict documents**: contain hashes of both inputs and outputs
4. **Manifest**: records all test cases, commits, and environment at run start

This creates an auditable chain: given a verdict JSON, you can verify that the inputs haven't been modified since the benchmark was run, and that the outputs match what the tool actually produced.

## CI Integration

### Smoke Mode (PR gate)
- Runs all harnesses against HEAD only (~25s)
- Catches regressions in harness code itself
- Exits non-zero if any harness produces a harness_error

### Regression Mode (main push)
- Runs all harnesses against last 20 commits
- Catches longitudinal regressions in the target tool
- Uploads verdict JSON as artifacts for post-hoc analysis

### Full Sweep (manual)
- Runs all harnesses against entire git history
- Produces heatmap visualization
- Used for initial audit and periodic re-assessment
