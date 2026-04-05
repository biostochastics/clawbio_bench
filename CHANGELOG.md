# Changelog

All notable changes to this project are documented here. The format loosely
follows [Keep a Changelog](https://keepachangelog.com/) and the project
adheres to [Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/biostochastics/clawbio_bench/releases/tag/v0.1.0
