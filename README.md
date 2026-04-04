# clawbio-bench

Machine-readable benchmark harnesses for auditing open-source computational biology tools. Produces per-commit JSON verdicts with SHA-256 chain of custody, category-level rubrics, and longitudinal safety tracking across git history.

Built as part of the [ClawBio deep audit](https://github.com/manuelcorpas/ClawBio), validated through 6 rounds of multi-model AI review (Crush, Gemini, Codex, OpenCode, Kimi) plus GPT-5.2-pro architectural consultation.

## Why This Exists

Most bioinformatics benchmarks answer "does it run?" This suite answers **"is it safe, correct, and honest?"** — with machine-verifiable evidence at every step.

- **For tool authors**: Run `clawbio-bench --smoke` in CI to catch regressions in safety and correctness across commits.
- **For auditors**: Use the rubric design, ground truth methodology, and verdict format as a reference for auditing other computational biology tools.

## Features

- **5 harnesses** covering pharmacogenomics, population genetics, nutrigenomics, metagenomics, and orchestration routing
- **93 test cases** with analytically derived or authority-referenced ground truth
- **Category-level verdicts** (not just pass/fail) — a `fst_mislabeled` finding carries different remediation than `fst_incorrect`
- **Longitudinal sweeps** across git history to track safety trajectory over time
- **SHA-256 chain of custody** on every input, output, and ground truth file
- **AST-based security analysis** for subprocess and shell injection detection
- **Heatmap visualization** of commit x test_case results
- **Zero runtime dependencies** — stdlib only (matplotlib optional for visualization)

## Install

```bash
pip install -e .                    # core (stdlib only)
pip install -e ".[dev]"             # + pytest, ruff, pre-commit
pip install -e ".[viz]"             # + matplotlib for heatmaps
```

## Quick Start

```bash
# Smoke test — HEAD only (~25s)
clawbio-bench --smoke --repo /path/to/ClawBio

# Single harness
clawbio-bench --smoke --harness orchestrator --repo /path/to/ClawBio

# Last 10 commits
clawbio-bench --regression-window 10 --repo /path/to/ClawBio

# Full longitudinal sweep
clawbio-bench --all-commits --repo /path/to/ClawBio

# Render heatmap from results
clawbio-bench --heatmap results/suite/20260404_120000/

# Also works as module
python -m clawbio_bench --smoke --repo /path/to/ClawBio
```

## Run Tests

```bash
# Unit tests (no ClawBio repo needed)
pytest tests/ -k "not test_harness_smoke"

# Full smoke tests (needs ClawBio repo)
pytest tests/ --repo /path/to/ClawBio

# Tests use git worktree isolation — your repo is never modified
```

## Harnesses

### Bio-Orchestrator (44 tests, 7 categories)

Tests routing decisions: extension-based (15), keyword-based (19), error handling (10).

| Category | Pass? | Description |
|----------|:-----:|-------------|
| routed_correct | Yes | Correct skill selected |
| routed_wrong | No | Wrong skill selected |
| stub_warned | Yes | Stub routed with warning |
| stub_silent | No | Stub routed silently |
| unroutable_handled | Yes | Unknown input, clean error |
| unroutable_crash | No | Unknown input, crash |
| harness_error | -- | Infrastructure error |

### Equity Scorer (14 tests, 10 categories)

Tests FST accuracy (Nei's GST), estimator label correctness, HEIM bounds, CSV mode, edge cases.

| Category | Pass? | Description |
|----------|:-----:|-------------|
| fst_correct | Yes | FST value + label correct |
| fst_incorrect | No | FST outside tolerance |
| fst_mislabeled | No | FST correct, label wrong |
| heim_bounded | Yes | HEIM in [0, 100] |
| heim_unbounded | No | HEIM > 100 |
| csv_honest | Yes | CSV mode honest about coverage |
| csv_inflated | No | CSV inflates genomic coverage |
| edge_handled | Yes | Edge case handled |
| edge_crash | No | Edge case crash |
| harness_error | -- | Infrastructure error |

### PharmGx Reporter (18 tests, 6 categories)

Tests pharmacogenomic phenotype calling and drug safety classification against CPIC guidelines.

| Category | Pass? | Description |
|----------|:-----:|-------------|
| correct_determinate | Yes | Right phenotype + drug class |
| correct_indeterminate | Yes | Correctly indeterminate |
| incorrect_determinate | No | Wrong phenotype (false Normal) |
| incorrect_indeterminate | No | Unnecessary indeterminate |
| omission | No | Drug missing from report |
| disclosure_failure | No | Warning on stderr only, not in report |
| harness_error | -- | Infrastructure error |

### NutriGx Advisor (10 tests, 9 categories)

Tests nutrigenomics score accuracy, reproducibility bundle integrity, SNP panel validation.

| Category | Pass? | Description |
|----------|:-----:|-------------|
| score_correct | Yes | Score matches expected |
| score_incorrect | No | Score diverges |
| repro_functional | Yes | Repro bundle complete |
| repro_broken | No | Repro artifacts missing |
| snp_valid | Yes | Panel SNPs found |
| snp_invalid | No | Panel SNPs missing |
| threshold_consistent | Yes | Categories match thresholds |
| threshold_mismatch | No | Categories wrong |
| harness_error | -- | Infrastructure error |

### Metagenomics Profiler (7 tests, 7 categories)

Tests demo mode functionality + AST-based static security analysis (no external tools needed).

| Category | Pass? | Description |
|----------|:-----:|-------------|
| injection_blocked | Yes | No shell=True found (AST-verified) |
| injection_succeeded | No | Shell injection vector exists |
| exit_handled | Yes | Exit code treated as error |
| exit_suppressed | No | Exit suppressed to warning |
| demo_functional | Yes | Demo mode works |
| demo_broken | No | Demo mode fails |
| harness_error | -- | Infrastructure error |

## Design Principles

1. **Never abort** — every (commit, test_case) pair produces a verdict. Infrastructure failures become `harness_error` verdicts excluded from pass rate.
2. **Offline only** — no network calls at runtime. Reference values are analytically pre-computed and embedded in ground truth files.
3. **Chain of custody** — SHA-256 of every input, output, and ground truth file. Git metadata, timestamps, and environment recorded per verdict.
4. **Safe by default** — dirty repo safety gate (`--allow-dirty` required), git worktree isolation in tests, path traversal validation, no `shell=True`.
5. **Category-level verdicts** — not binary pass/fail. Each category maps to a specific remediation path.

## Ground Truth Format

Each test case is a directory (Model B) with `ground_truth.txt` + payload:

```
# BENCHMARK: equity-scorer v1.0.0
# PAYLOAD: input.vcf
# GROUND_TRUTH_FST: 1.000
# GROUND_TRUTH_FST_PAIR: POP_A_vs_POP_B
# GROUND_TRUTH_FST_ESTIMATOR: Nei's GST
# FST_TOLERANCE: 0.001
# FINDING_CATEGORY: fst_mislabeled
# DERIVATION: p_total=0.5, HT=0.5, HS=0.0, GST=1.0
# CITATION: Nei (1973). PNAS 70(12):3321-3323
```

See [docs/ground-truth-derivation.md](docs/ground-truth-derivation.md) for detailed derivation methodology.

## Documentation

- **[docs/methodology.md](docs/methodology.md)** — Audit methodology, rubric design, multi-model review process
- **[docs/ground-truth-derivation.md](docs/ground-truth-derivation.md)** — How reference values are computed per harness
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — How to add harnesses for new tools
- **[CHANGELOG.md](CHANGELOG.md)** — Release history

## Confirmed Findings (at ClawBio HEAD)

| ID | Finding | Harness Evidence |
|----|---------|-----------------|
| C-06 | FST labeled "Hudson" but computes Nei's GST | eq_01, eq_02: `fst_mislabeled` |
| U-2 | HEIM unbounded with custom weights | eq_09: `heim_unbounded` |
| F-29 | Haploid genotypes crash equity scorer | eq_12: `edge_crash` |
| M-3 | PharmGx/NutriGx/metagenomics unreachable via orchestrator | kw_16-18: `unroutable_handled` |
| NEW | NutriGx hom-ref allele_mismatch bug | ng_09: `score_incorrect` |
| NEW | Metagenomics exit_suppressed (critical=False default) | mg_05: `exit_suppressed` |
| PGx | 44% pass rate confirms prior CPIC compliance audit | 10/18 tests fail |

## References

- Nei, M. (1973). Analysis of gene diversity in subdivided populations. *PNAS*, 70(12), 3321-3323.
- Hudson, R.R. et al. (1992). Estimation of levels of gene flow from DNA sequence data. *Genetics*, 132(2), 583-589.
- 1000 Genomes Project Consortium (2015). A global reference for human genetic variation. *Nature*, 526, 68-74.
- CPIC Guidelines (2017-2024). cpicpgx.org
- OWASP Command Injection Prevention Cheat Sheet (2024). owasp.org

## Roadmap

- **Shared AST security utilities**: The metagenomics harness has AST-based static analysis for subprocess and shell injection detection (handles aliased imports, OS-level shell functions). Currently harness-local — planned to extract into `core.py` as a reusable utility for any harness auditing tools that invoke external processes.
- **Entry-point harness discovery**: Plugin architecture via `[project.entry-points]` so third-party harnesses can register without modifying core code.
- **Parallel execution**: `--jobs/-j` flag for concurrent test case execution within a commit.
- **Config file support**: YAML/TOML for complex benchmark configurations.
- **Benchmark diff tool**: Compare two longitudinal runs to identify regressions.

## License

MIT
