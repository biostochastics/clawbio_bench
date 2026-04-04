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

### Post-extraction multi-model review (Crush, Gemini, Codex, Kimi)
11 issues identified and fixed:
- `CORE_VERSION` aligned to package version (was 1.1.0, should be 1.0.0)
- `harness_core.timezone.utc` AttributeError bug in metagenomics static path
- CLI mode flags now mutually exclusive via argparse group
- `resolve_commits` validates git rev-parse HEAD return code
- `--regression-window` validates positive integer
- `conftest.py` accepts git worktree repos (`.git` as file)
- AST analysis handles aliased imports and additional shell-executing functions
- Hostname PII replaced with SHA-256 hash in manifest/verdict output
- Removed `Typing::Typed` classifier (no py.typed marker)
- Pre-commit hook uses `python3` for portability
- CI pins ClawBio to known ref, validates artifact generation not exit code

### Provenance
- Developed during 16-task, 4-phase deep audit of ClawBio (github.com/manuelcorpas/ClawBio)
- Reviewed by: Crush, Gemini, Codex, OpenCode, Kimi (7 rounds) + GPT-5.2-pro (architectural consultation)
- PGx heatmap renderer ported from `clawbio-pgx-benchmark/` (187-commit longitudinal sweep)
- 0 harness errors across all 93 tests at HEAD
