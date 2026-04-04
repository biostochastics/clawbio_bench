# Changelog

## v1.0.0 (2026-04-04)

Initial public release. Extracted from the ClawBio deep audit project (`clawbio_r/benchmark-suite/`).

### Features
- 5 benchmark harnesses: orchestrator, equity, pharmgx, nutrigx, metagenomics
- 93 test cases with analytically derived or authority-referenced ground truth
- `clawbio-bench` CLI with `--smoke`, `--regression-window`, `--all-commits` modes
- SHA-256 chain of custody on all inputs, outputs, and ground truth files
- Git worktree isolation for safe testing
- Dirty repo safety gate (`--allow-dirty` required for destructive operations)
- JSON verdict documents with per-commit, per-test-case granularity
- Category-level rubrics (6-10 categories per harness, not binary pass/fail)

### New in v1.0.0 (vs. internal v0.1.0)
- **Heatmap visualization** (`--heatmap` flag, requires `[viz]` extra) — ported from standalone PGx benchmark
- **AST-based security analysis** for metagenomics harness (replaces grep-based `shell=True` detection)
- **Pre-commit hooks** (ruff format, ruff check, pytest smoke)
- **GitHub Actions CI** — lint, unit tests (3.11/3.12/3.13), smoke benchmarks, regression sweeps
- **Audit methodology documentation** (`docs/methodology.md`, `docs/ground-truth-derivation.md`)
- **Contributing guide** for adding harnesses to audit new tools
- Bumped to production-stable status after 6 rounds of multi-model review

### Provenance
- Developed during 16-task, 4-phase deep audit of ClawBio (github.com/manuelcorpas/ClawBio)
- Reviewed by: Crush, Gemini, Codex, OpenCode, Kimi (6 rounds) + GPT-5.2-pro (architectural consultation)
- PGx heatmap renderer ported from `clawbio-pgx-benchmark/` (187-commit longitudinal sweep)
- 0 harness errors across all 93 tests at HEAD
