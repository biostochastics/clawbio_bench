# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] — 2026-04-06

### Added

- **Sample audit report.** `sample_audit_report.pdf` checked in at repo
  root — a full 30-page report from a 7-harness smoke run against
  ClawBio HEAD, linked from README.
- **Enhanced markdown report.** `--render-markdown` now renders:
  - Per-harness category breakdown tables.
  - Per-test detailed findings with severity-sorted clinical context
    (FINDING, HAZARD_METRIC, DERIVATION, FINDING_CATEGORY from ground
    truth) plus legacy clinical fields (HAZARD_DRUG, TARGET_GENE).
  - Severity tiers derived dynamically from each harness's
    `FAIL_CATEGORIES` / `PASS_CATEGORIES` instead of a hardcoded dict.
  - Scope disclosure note: "critical findings only; warnings in PDF."
- **`--harness` filter for `--render-markdown`.** Renders only the named
  harness section, making it easy to extract a single-harness breakdown
  (e.g. `clawbio-bench --render-markdown results/ --harness pharmgx`).
- **HTML escaping for harness/test/category names** in markdown output,
  preventing `</details>` structural injection via backtick breakout.
- **Color hex validation** in Typst report generator. Legend colors are
  validated with a `#RRGGBB` / `#RGB` regex; malformed values fall back
  to `DEFAULT_UNKNOWN_COLOR` instead of breaking Typst compilation.

### Changed

- **Typst PDF report redesign.** Near-monochrome palette, clickable TOC,
  same-category finding collapse, severity group headers, running page
  headers. 80 pages → 30 for a 7-harness / 140-test run.
- **4-state cover status.** OVERALL PASS / FINDINGS PRESENT / HARNESS
  ERRORS / FINDINGS + HARNESS ERRORS.
- **Severity sort uses category sets** (`fail_categories` /
  `pass_categories`) instead of hex color substring matching.

### Fixed

- **`glob_pattern` threading through CLI.** The harness registry now carries
  an optional `glob_pattern` per harness (e.g. `"*.txt"` for PharmGx), and
  `run_single_harness`, `--dry-run`, `--list`, and `--list --json` all
  respect it. Previously the CLI path used `"*"` regardless, which could
  diverge from the standalone harness's explicit pattern. A dotfile filter
  (`.DS_Store`, editor swap files) was added to `resolve_test_cases` to
  prevent silent contamination on macOS.
- **GRCh37/GRCh38 coordinate correction in `grch37_reference_mismatch`
  test case.** The GRCh38 position for rs3892097 was corrected from 42524947
  to 42128945 per Ensembl REST API verification. The previous value was
  actually the GRCh37 coordinate (labels were inverted). The ~396 kb shift
  between assemblies is now documented with source citation.
- **Pre-existing schema drift.** Regenerated `verdict-minimal.schema.json`
  and `verdict-full.schema.json` via `gen_schemas.py` to match current
  Struct definitions.
- **CPIC guideline version citations.** Corrected fabricated version numbers:
  DPYD/fluoropyrimidines reference now cites Amstutz et al. 2018 (PMID
  29152729) instead of non-existent "v3.0 (2023)"; HLA-B/abacavir now cites
  Martin et al. 2012/2014 instead of non-existent "2020 update"; UGT1A1/
  irinotecan now correctly attributes to DPWG (CPIC irinotecan guideline is
  pending). All citations verified against cpicpgx.org and PubMed.
- **DPYD*2A test case wording.** Replaced "CONTRAINDICATION" (FDA label
  language) with CPIC's actual recommendation: "Avoid use." Added mortality
  OR citation (de Moraes et al. 2024 meta-analysis).
- **HLA-B*57:01 HSR risk range.** Updated from single "~48%" to "~48-61%"
  with source attribution (DPWG 48%; PREDICT-1 ~61%).
- **`cyp2c19_rapid_clopidogrel` HAZARD_DRUG.** Changed from Voriconazole to
  Clopidogrel to match filename and FINDING description.
- **Phenotype matching Rapid/Ultrarapid.** Added "rapid metabolizer" to
  `_KEY_TERMS` so the regex lookbehind correctly distinguishes Rapid from
  Ultrarapid (previously "rapid" substring-matched inside "ultrarapid").
- **Typst report `_severity_key` unused variable.** Removed dead
  `overall_pass` assignment.
- **Typst `chip()` rendering bug.** Executive summary status column rendered
  literal `chip("FINDINGS", coral)` text instead of a colored badge.
  Caused by wrapping the function call in content brackets `[...]` inside
  a `#table()`. Fixed by emitting `chip(...)` as a bare code expression.
- **Typst `raw()` in persistent failures.** List items rendered literal
  `raw("fm_03_...")` text. Same content-bracket issue — fixed by removing
  `[...]` wrapper in `emit_persistent_failures()`.
- **Markdown `gt_map` key mismatch.** `_extract_detailed_findings()` mapped
  to `HAZARD_DRUG` / `HAZARD_CLASS` / `TARGET_GENE` but the finemapping
  harness uses `HAZARD_METRIC` / `DERIVATION` / `FINDING_CATEGORY`.
  Extended the map to cover both schemas (old clinical + new finemapping).
- **Markdown multi-commit ground-truth collision.** `gt_lookup` keyed by
  `(harness, test)` without commit dimension, so multi-commit runs could
  silently attach wrong ground truth. Now skips enrichment when
  `mode != smoke`.
- **Long SHA overflow in Typst.** Cover commit and chain-of-custody SHA
  values are now capped via `short_hash()` (8 and 12 chars respectively)
  to prevent `raw()` overflow in narrow kvrow columns.
- **Hardcoded `CORE_VERSION` in `core.py`.** Replaced `"0.1.0"` string
  with dynamic import from `clawbio_bench.__version__` (which reads
  `pyproject.toml` via `importlib.metadata`). Version now stays in sync
  automatically — no more drift between pyproject.toml and report output.
- **numpy/pandas dependency note in README.** Added requirement note and
  updated Quick Start to use `pip install -e ".[dev]"` by default.
- **Typst CLI requirement in README.** Documented as optional dependency
  for PDF report generation.
- **`.mypy_cache/` added to `.gitignore`.**
- **Missing `timeout` in `resolve_commits` for `--commits HEAD` path.**
  `subprocess.run` call in `core.py` lacked a `timeout` kwarg, unlike
  every other git subprocess in the codebase. Could hang indefinitely on a
  stalled git process. Now passes `timeout=10`.
- **`save_execution_logs` locale-dependent encoding.** `write_text()` calls
  for `stdout.log` / `stderr.log` relied on the platform default encoding
  instead of explicit `encoding="utf-8"`. On non-UTF-8 locales (Windows,
  some CI images) this could produce different bytes for the same run,
  breaking chain-of-custody hash portability during `--verify`.
- **`--heatmap` raw `ImportError` when matplotlib not installed.** Now
  catches `ImportError` and prints a user-friendly message directing to
  `pip install clawbio-bench[viz]` instead of a raw traceback.
- **Infra crash message dropped in rich mode with non-TTY stderr.** In
  `render_suite_summary`, the rich branch only emitted the `HARNESS CRASH`
  line when `get_console(stderr=True)` returned a console. When stderr was
  piped (non-TTY) while stdout was a TTY, the message was silently lost.
  Now falls back to plain `print(..., file=sys.stderr)`.

## [0.1.0] — 2026-04-04

Initial public release. `clawbio-bench` is a standalone benchmark suite that
audits bioinformatics tools for safety, correctness, and honesty, emitting
tamper-evident JSON verdicts with a SHA-256 chain of custody over every input,
output, and artifact. The primary audit target is
[ClawBio](https://github.com/ClawBio/ClawBio).

### Harnesses

Seven harnesses, **140 test cases** total, with analytically derived or
authority-referenced ground truth per case:

- **`bio-orchestrator`** (54 tests) — routing correctness across
  extension-based, keyword-based, `--skill NAME` force-routing, and
  `--skills A,B,C` multi-skill composition paths, plus stub-warning checks
  and one genuine LLM-path prompt-injection test via `--provider flock`.
- **`pharmgx-reporter`** (33 tests) — phenotype calling and drug-safety
  classification against CPIC guidelines, including CYP2D6 CNV / hybrid /
  whole-gene deletion disclosure tests and CPIC Tier-1 gene coverage for
  NUDT15, CYP2B6, G6PD, and MT-RNR1.
- **`equity-scorer`** (15 tests) — FST accuracy and estimator-label honesty
  (Nei vs Hudson), HEIM bounds, CSV-mode coverage honesty, edge cases
  (monomorphic sites, single-sample, haploid).
- **`nutrigx-advisor`** (10 tests) — nutrigenomics score accuracy,
  reproducibility bundle integrity, SNP panel validation, threshold
  consistency.
- **`claw-metagenomics`** (7 tests) — demo-mode functionality plus
  AST-based static security analysis of the audited source (unsafe shell
  invocation detection, per-commit `run_command(critical=...)` default
  extraction).
- **`clinical-variant-reporter`** — Phase 1 (5 tests) — structural and
  traceability checks (reference build, transcript citation, ClinVar/gnomAD
  version pinning, limitations section, RUO disclaimer in report body,
  per-variant ACMG criterion audit trail, disease/inheritance context) per
  Rehm et al. 2013, Richards et al. 2015, and Abou Tayoun et al. 2018. Phase
  1 deliberately does **not** score 28-criteria adjudication correctness;
  that is scoped for a future Phase 2 release against ClinGen VCEP 3-star+
  consensus variants.
- **`clawbio-finemapping`** (16 tests) — Wakefield 2009 Approximate Bayes
  Factors and Wang et al. 2020 SuSiE IBSS posterior inclusion probabilities
  and credible sets, with hand-derived reference values per published
  equations. Runs via a subprocess driver shim so `numpy`/`pandas` stay an
  optional `[finemapping]` extra.

### Core framework

- **Category-level verdicts, not pass/fail.** Each harness rubric has 6–10
  named categories; `fst_mislabeled` ≠ `fst_incorrect`, and the tooling
  never collapses the two. Each category maps to a distinct remediation
  path.
- **Tamper-evident chain of custody.** SHA-256 of every input, output,
  ground-truth file, stdout, stderr, and of the verdict document itself.
  `clawbio-bench --verify` runs a three-layer reconciliation: per-verdict
  self-hash, `verdict_hashes.json` sidecar index, and `stdout.log` /
  `stderr.log` integrity.
- **Hash-before-truncate.** When `stdout` / `stderr` exceeds the 10 MB cap,
  the pre-truncation SHA-256 is preserved alongside the post-truncation
  hash and a `stdout_truncated` flag. Truncation happens at an encoded-byte
  boundary with `errors="replace"` so multi-byte streams cannot drift past
  the cap.
- **Canonical byte-stable serialization.** `msgspec.json.encode(order="sorted")`
  + trailing newline + atomic write-to-temp-then-replace. Identical bytes
  on every run, every Python version, every machine — a prerequisite for
  meaningful longitudinal diffs.
- **Longitudinal sweeps across git history.** `--smoke` (HEAD only),
  `--regression-window N`, `--all-commits`, and `--commits SHA,...` modes.
  Every commit is isolated via git worktree checkout with submodule-aware
  `clean_workspace` between commits, so a dirty submodule from commit *N*
  cannot poison commit *N+1*.
- **Never-abort execution.** Every `(commit, test_case)` pair produces a
  verdict — infrastructure failures become `harness_error` verdicts
  excluded from pass-rate calculations rather than aborting the sweep.
- **Two-tier verdict validation.** A minimum-contract check always runs on
  every verdict; the strict full-schema check runs on non-error verdicts
  via `msgspec.convert` against `FullVerdictDoc`.
- **JSON Schema as external contract.** `schemas/verdict-minimal.schema.json`
  and `schemas/verdict-full.schema.json` are committed artifacts
  auto-generated from `msgspec.Struct` definitions via
  `scripts/gen_schemas.py`. A CI drift gate fails if the committed files
  fall out of sync. Auditors using Rust, Go, TypeScript, or a plain JSON
  Schema validator can verify verdicts in any language without running
  Python.
- **Dual ground-truth parser.** YAML frontmatter (preferred for new cases)
  and legacy `# KEY: value` format, dispatched per file at parse time. YAML
  path validates keys against the same `UPPER_SNAKE_CASE` regex as legacy,
  rejects anchors (`&`), aliases (`*`), and merge keys (`<<:`) as a
  hardening measure, and recursively normalizes nested values to strings.
- **Safe by default.** Dirty-repo safety gate (`--allow-dirty` required),
  path-traversal validation on `PAYLOAD` / `POP_MAP_FILE` / `TIMEOUT` /
  `WEIGHTS` fields, git worktree isolation in tests, no `shell=True`
  anywhere.
- **Reference genome tracking.** `REFERENCE_GENOME` field surfaced in every
  verdict for GRCh37 / GRCh38 traceability.
- **Reproducibility signature.** Each verdict records a SHA-256 hash of the
  installed Python environment (sorted `name==version` set).

### Dependencies

Three pinned runtime dependencies (core) plus four optional extras. Each
core entry is justified against the cost of expanding an audit tool's
trusted computing base:

- **`msgspec>=0.18`** *(core)* — verdict schema validation (`Struct`) and
  deterministic JSON serialization in a single C extension. Replaces what
  would otherwise be separate `pydantic` and `orjson` deps.
- **`regex>=2024.0.0`** *(core)* — variable-length lookbehind and
  Unicode-aware word boundaries for pharmgx phenotype matching. Stdlib
  `re` cannot express these cleanly and false-matches `"expressor"` inside
  `"non-expressor"` because `\b` treats `-` as a word boundary.
- **`ruamel.yaml>=0.18`** *(core)* — safe YAML loader for the YAML
  frontmatter ground-truth format.
- **`rich>=13.0`** *(optional `[ui]`)* — styled `--list` and summary
  tables. `--no-rich` is a kill switch; plain-text fallback is byte-stable
  for CI log diffs.
- **`matplotlib>=3.8`** *(optional `[viz]`)* — `--heatmap` rendering.
- **`numpy>=1.26` + `pandas>=2.0`** *(optional `[finemapping]`)* — loaded
  exclusively by the fine-mapping subprocess driver shim;
  `clawbio_bench.*` itself never imports either.

### CLI

- `clawbio-bench --smoke | --regression-window N | --all-commits | --commits SHA,...`
- `--harness NAME` (single harness), `--inputs PATH` (override test case
  directory), `--output DIR`, `--repo PATH` (required for real runs),
  `--branch NAME`, `--allow-dirty` (safety override).
- `--list` and `--list --json` (machine-readable harness inventory).
- `--dry-run`, `-q` / `--quiet`, `--no-rich`, `--version`.
- `--heatmap DIR` (requires `[viz]` extra), `--render-markdown DIR`,
  `--baseline PATH`, `--verify DIR` (three-layer chain-of-custody
  reconciliation).

### Reusable GitHub Actions workflow

Downstream repositories can call `audit-reusable.yml` from their own
`.github/workflows/audit.yml` to get the full smoke suite on every PR with
a sticky-comment summary, baseline diffing against a rolling nightly
`main`-branch baseline, and 30-day artifact retention of the complete
verdicts tree. Advisory exit codes: `0` = clean, `1` = findings exist
(comment stays green), `≥2` = infrastructure failure (hard red). The
`clawbio_bench_ref` input is validated against a conservative character
set before interpolation into `pip install`.

### Tests and CI

- **224 unit tests** covering scoring, validators, dual parser, chain of
  custody, schema drift, YAML frontmatter hardening, canonical byte
  determinism, and deep-verify reconciliation. Full unit suite runs in
  under one second.
- **Full `mypy --strict` compliance** across every source file.
- **CI matrix**: lint → unit tests (Python 3.11 / 3.12 / 3.13 / 3.14) →
  smoke (pinned ClawBio ref) → regression (last 20 commits, main only).
- **Pre-commit hooks**: `ruff format`, `ruff check`, unit tests.
- **SPDX-License-Identifier headers** on every source file.

### Known limitations at release

- Behavioral coverage is **6 / 37 executable ClawBio skills (~16%)**; the
  remaining 31 are routing-tested only. Absence of a finding on a
  non-covered skill is not evidence of correctness. See the Roadmap
  section of `README.md`.
- `clinical-variant-reporter` harness is Phase 1 only (structural /
  traceability). 28-criteria adjudication correctness is deliberately
  out of scope for v0.1.0.
- Prompt-injection tests against ClawBio's current deterministic parsers
  are regression pins, not live adversarial tests. The live LLM-path test
  is gated on FLock credentials.
- `FST_TOLERANCE` is a hardcoded absolute delta — false failures possible
  on small-*n* studies. A variance-aware Z-score replacement is on the
  roadmap.
- Platform coverage: Linux and macOS only. Windows is untested.

[0.1.1]: https://github.com/biostochastics/clawbio_bench/releases/tag/v0.1.1
[0.1.0]: https://github.com/biostochastics/clawbio_bench/releases/tag/v0.1.0
