# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Live inventory-driven executable detection in orchestrator harness.**
  `score_routing_verdict()` now uses the live skill scan from
  `discover_clawbio_skills()` as the authoritative source for whether a
  skill is executable or a stub.  The manual `GROUND_TRUTH_EXECUTABLE`
  header in test case files is now a fallback, not the primary source.
  This prevents stale ground-truth files from producing false
  `stub_silent` verdicts when a ClawBio skill gains code between
  harness releases (e.g. `struct-predictor`, which gained 1,166 LOC
  but was still classified as a stub in 3 test cases).

### Fixed

- **3 false `stub_silent` findings for `struct-predictor` eliminated.**
  `ext_09_pdb`, `ext_10_cif`, and `kw_05_alphafold` ground truth files
  updated from `GROUND_TRUTH_EXECUTABLE: false` to `true` and
  `FINDING_CATEGORY` from `stub_silent` to `routed_correct`.
- **`FLOCK_API_KEY` now passed to daily audit smoke step.** The
  `inj_03_flock_routing_hijack` prompt-injection test previously always
  produced `unroutable_crash` in CI because FLock credentials were not
  available.  The daily-audit workflow now passes the secret, enabling
  the actual LLM routing path to be exercised.

## [0.1.3] — 2026-04-07

### Added

- **Braille logo with block-letter wordmark in README.** Lobster-claw-
  under-magnifying-glass braille art with `clawbio` / `_bench` in
  industrial block letters alongside.
- **Actual audit report PDF.** `clawbio_audit_report_20260406.pdf`
  (21-page, 7-harness smoke run, v0.1.2, 125/147 passing at ClawBio
  HEAD `bb9ffff`) replaces the broken `sample_audit_report.pdf` link.

### Changed

- **CI section restructured by repo ownership.** ASCII diagram showing
  which workflows live in `clawbio_bench` vs `ClawBio`, table of all 4
  bench-side workflows with triggers, and a separate section for the
  3-line ClawBio stub. Daily audit and secrets callouts now explicitly
  name their source repo.
- **Project name standardized to `clawbio_bench`** (underscore) across
  README headings, prose, and CLI references.

### Fixed

- **`.gitignore` negation pattern.** `!sample_audit_report.pdf` replaced
  with `!clawbio_audit_report_20260406.pdf` so the actual report is
  tracked by git.

## [0.1.2] — 2026-04-06

### Added

- **Daily automated audit workflow.** `.github/workflows/daily-audit.yml`
  cron (8 AM UTC daily + `workflow_dispatch`): smoke suite against ClawBio
  HEAD, markdown + PDF delta reports, baseline promotion on improvement,
  artifact upload (90/30-day retention), deduplicated regression issues,
  per-commit attribution via `--regression-window 5`. Accepts an optional
  `clawbio_ref` input for auditing historical commits (e.g.
  `clawbio_ref: 349fb98` to audit the pre-remediation state). Historical
  runs produce full reports, baselines, and digests but suppress automatic
  issue creation to avoid false alarms. A second optional input
  `clawbio_baseline_ref` runs the bench against a baseline commit first,
  then uses its aggregate as the `--baseline` for the main run's delta
  report — producing a single workflow run with a proper before/after
  comparison (e.g. `clawbio_baseline_ref: 349fb98` + default
  `clawbio_ref` = "April 2 vs HEAD").
- **`scripts/update_baseline.py`.** Baseline manager: promotes on strict
  improvement, initializes on first run, handles corrupt baselines.
- **`scripts/post_summary.py` with multi-model LLM swarm.** `--llm
  openrouter` fans out to 3 analyst models (deepseek-v3.2-exp,
  minimax-m2.7, gpt-5-nano), synthesizes via mimo-v2-pro with number
  verification. Thinking-model aware (extracts `reasoning` when `content`
  is null). Consolidated daily reports (`--log-dir`) with 4 sections:
  audit digest, ClawBio git diff analysis (`--clawbio-repo`),
  clawbio-bench self-changelog (`--self-changelog`), and coverage
  investigation (`--investigate`). Final polish pass for coherence.
  Weekly/monthly trend summaries (`--weekly`, `--monthly`). Graceful
  degradation to structured digest on any failure.
- **`[all]` optional extra** — combines `viz`, `ui`, `finemapping`, and
  `scikit-learn` for CI.
- **`--tagged-commits` CLI mode.** New commit-selection mode that runs
  benchmarks against only tagged (release / milestone) commits. Works
  with both lightweight and annotated git tags. Usage:
  `clawbio-bench --tagged-commits --repo /path/to/ClawBio`.
- **Release markers on heatmap timeline.** When heatmap data includes
  tag metadata (automatically captured in all modes), tagged commits
  are highlighted with purple bold labels, and dashed horizontal lines
  demarcate releases on the Y-axis.
- **Hierarchical harness grouping in aggregate heatmaps.** Multi-harness
  heatmaps now draw vertical separator lines between harnesses and label
  each harness above the X-axis, making large grids easier to navigate.
- **Per-harness sub-heatmaps.** When rendering an aggregate (multi-harness)
  heatmap, individual per-harness heatmap PNGs are also generated in each
  harness subdirectory alongside the aggregate view.
- **`get_tagged_commits()` and `get_commit_tags()` in core.** Two new git
  helper functions for resolving tagged commits and mapping SHAs to tag
  names. Both handle annotated and lightweight tags correctly.
- **Missing-cell color in heatmaps.** Cells with no data (e.g. test cases
  that didn't exist at earlier commits) now render in a distinct light
  slate color (`#f1f5f9`) instead of falling through to category zero.
- **Delta comparison in Typst report.** `--baseline` CLI flag on
  `generate_report.py` accepts a baseline results directory or
  `aggregate_report.json`. When provided, a "Delta vs. baseline" section
  appears on the executive summary page with new/resolved/unchanged
  finding counts, per-finding lists with tier-colored cells, and
  checkmarks for resolved items.
- **Unified 5-tier severity system.** All ~50 verdict categories across 7
  harnesses now map to exactly one of five tiers: Pass, Advisory,
  Warning, Critical, Infra. `SEVERITY_TIERS` and `TIER_DEFS` in
  `generate_report.py` provide the canonical mapping; colors are
  tier-consistent across all harnesses instead of per-category ad-hoc.
- **Bento-grid executive dashboard.** Page 2 of the PDF report is now a
  6-cell instrument-panel dashboard: suite status (pass/total),
  blocking harnesses (count + names), persistent failures (count),
  top failure classes (category × count), per-harness pass rates
  (color-coded), and audit target metadata — all above the existing
  per-harness summary table.
- **Two-column pass/findings verdict matrix.** The flat single-column
  verdict list is replaced by a split layout: passing tests grouped by
  gene/module on the left, findings on the right. Colored heatmap
  squares (8 pt) replace verbose text badges. Single-test groups are
  merged into an "OTHER" bucket. Group headers show name, pass rate,
  and test count inline.
- **Severity indicators in markdown report.** Summary table gains a
  Status column with pass/fail emoji (✅/❌). Detailed findings gain
  per-tier colored circle indicators (🔴 critical, 🟠 warning, ⚪ infra).
- **`scope_honest_indeterminate` category in PharmGx harness.** Split the
  former `disclosure_failure` bucket into two distinct categories:
  - `disclosure_failure` (4 cases) — tool returns a wrong determinate
    answer with a stderr warning that is NOT surfaced in the user-facing
    report. This remains a safety failure.
  - `scope_honest_indeterminate` (5 cases) — tool correctly returns
    Indeterminate (or discloses the limitation) for variants that DTC/SNP
    arrays fundamentally cannot resolve: whole-gene CNV, hybrid alleles,
    phasing ambiguity. This is correct clinical behavior and now scores
    as a **pass**.
  Reclassified cases: `cyp2d6_star5_deletion`, `cyp2d6_phase_ambiguous`,
  `cyp2d6_duplication_xn`, `cyp2d6_star13_hybrid`, `cyp2d6_normal`.
- **Gene-specific scope disclosure check.** The `scope_honest_indeterminate`
  scoring branch now requires that a report warning names the target gene,
  preventing a generic disclaimer for Gene B from crediting silence about
  Gene A.
- **scikit-learn** added to `[dev]` optional dependencies.
- **Consolidated `ROADMAP.md`.** Standalone roadmap document replacing
  the inline README roadmap section. Tracks all planned harnesses (P1–P3),
  framework features, audit-framework failure-class coverage matrix, full
  ClawBio skill inventory (47 skills), incoming skills from open PRs,
  and collaboration context. README Roadmap section now summarizes and
  points to the full document.

### Changed

- **Typst PDF report redesign.** Industrial audit-console aesthetic with
  tighter margins (1.5 cm), two-column layouts, instrument-panel finding
  cards, and pass/findings split verdict matrix. 29 → 21 pages for the
  same 7-harness / 140-test suite.
- **`category_color()` uses tier-based lookup** instead of per-category
  legend colors.
- **`--branch` help text** now mentions `--tagged-commits` alongside
  `--all-commits` and `--regression-window`.
- **Standalone harness entry point (`run_harness_main`)** now resolves
  tag metadata and passes it to `build_heatmap_data`, matching the
  suite CLI behavior.
- **CI regression window reduced from 20 to 2 commits.** The sweep now
  compares only HEAD vs its parent, preventing the 15-minute timeout
  that occurred when running 93 test cases × 20 commits. Deeper sweeps
  remain available via `clawbio-bench --regression-window N` locally.

### Fixed

- **`get_tagged_commits()` handles `TimeoutExpired`.** Wraps `git tag`
  subprocess in try/except and re-raises as `BenchmarkConfigError` for
  consistent CLI error handling.
- **Finemapping ImportError restricted to `susie_inf` sentinel.** The
  `edge_handled` downgrade now requires `method == "susie_inf"` AND
  `"core.susie_inf"` in the error message, preventing unrelated import
  failures from being silently credited as passes.
- **Finemapping driver `susie_inf` import narrowed.** Only suppresses
  `ImportError` when `"core.susie_inf"` is in the message; transitive
  dependency failures now re-raise correctly.
- **PharmGx scope_terms expanded.** Added `"deletion"`, `"structural
  variant"`, `"cannot interpret"` to match the warning extraction
  vocabulary, preventing legitimate disclosures from being scored as
  `disclosure_failure`.
- **PharmGx DQW requires gene-specific match.** `data_quality_warning_present`
  alone no longer credits `scope_honest_indeterminate` — the warning
  text must also name the target gene, preventing a generic disclaimer
  for Gene B from crediting silence about Gene A.
- **Markdown status icons account for harness_errors.** Per-harness and
  total-row status icons now show `\u274c` when `harness_errors > 0`,
  even if `pass` is True.
- **Severity map fallback for missing `fail_categories`.** When
  `fail_categories` is absent from aggregate data (older baselines),
  categories from `critical_failures` are inferred as tier 0 (critical)
  instead of defaulting to tier 1 (warning).
- **`get_commit_tags()` no longer silently swallows git failures.**
  Returns an empty dict on error (tag enrichment is best-effort) but
  now prints a stderr warning so users can distinguish "no tags exist"
  from "tag resolution broke."
- **`get_commit_tags()` warns on malformed tag lines** instead of
  silently skipping them.
- **`cli.py` tag resolution wrapped in try/except.** A
  `subprocess.TimeoutExpired` from a hanging `git for-each-ref` no
  longer crashes the entire benchmark run; it degrades gracefully with
  a warning and an empty tag map.
- **All-missing heatmap matrix** now prints a stderr warning when no
  verdict data matched any commit/test combination, instead of
  rendering a blank gray rectangle with no explanation.

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
  smoke (pinned ClawBio ref) → regression (HEAD vs parent, main only).
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

[0.1.3]: https://github.com/biostochastics/clawbio_bench/releases/tag/v0.1.3
[0.1.2]: https://github.com/biostochastics/clawbio_bench/releases/tag/v0.1.2
[0.1.1]: https://github.com/biostochastics/clawbio_bench/releases/tag/v0.1.1
[0.1.0]: https://github.com/biostochastics/clawbio_bench/releases/tag/v0.1.0
