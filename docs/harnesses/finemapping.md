# Fine-Mapping Harness Specification

**Benchmark name**: `clawbio-finemapping`
**Version**: `0.1.0`
**Target**: `ClawBio/skills/fine-mapping/core/{abf,susie,credible_sets}.py`
**Contract**: same `run_single_<name>` shape as every other harness (see `core.py`).

## Purpose

Audit ClawBio's pure-Python fine-mapping skill (Approximate Bayes Factors
per Wakefield 2009, SuSiE IBSS per Wang et al. 2020, credible-set
construction with purity filter) for:

- Numerical correctness against pre-computed reference PIPs derived by
  hand from the published equations.
- Silent-failure modes where invalid input (NaN z-scores, zero or
  negative sample sizes, degenerate LD) propagates to PIPs without an
  exception.
- Claim-vs-reality mismatches where an output field (`pure`, `coverage`,
  `converged`, variant-level `pip`) does not match the math that
  produced it.
- Algorithmic pathologies where the IBSS update with no null component
  forces every configured effect to contribute mass, producing phantom
  signals on null loci.

## Finding sourcing

Fourteen of the rubric categories were seeded from an independent
review pass against the skill's `core/{abf,susie,credible_sets}.py`
implementations and the Wakefield 2009 / Wang et al. 2020 references.
Each failing category pins an exact numerical trigger derived from
the published equations and verified against the live code.

## Target invocation

The fine-mapping skill has no CLI entry point, so the harness ships a
subprocess driver shim at `src/clawbio_bench/drivers/finemapping_driver.py`.
The shim:

1. Accepts `--skill-dir <path>`, `--inputs <json>`, `--output <json>`.
2. Prepends `<skill-dir>` to `sys.path` then imports `core.abf`,
   `core.susie`, `core.credible_sets`.
3. Loads the inputs JSON (z, se, n, R, params, method).
4. Calls the requested function inside `try`/`except`, returning a
   structured JSON result with `status`, `error`, and all numeric
   outputs.
5. Never aborts, never writes to the target repo, never reads anything
   outside its `--inputs` file. Exit code 0 on every invocation the
   harness can score (including skill-side raises, which are captured
   and reported); non-zero only for driver-level failures (missing
   skill dir, unloadable inputs).

This keeps the "loose coupling" invariant: `clawbio_bench` Python code
never imports the target. The driver is a data file shipped with our
package and launched as a separate interpreter with `cwd=repo_path`.

## Dependencies

The driver requires `numpy` and `pandas`, which ClawBio itself depends
on. Users running the harness inside a ClawBio dev env will have them.
For standalone installs we expose an `[finemapping]` extra in
`pyproject.toml`. If imports fail at runtime the harness emits an
`edge_handled` verdict with a clear error — it does not crash.

## Rubric categories (16)

### Pass categories (3)

| Category | Meaning |
|---|---|
| `finemap_correct` | PIPs within numerical tolerance of reference AND all claim fields (`converged`, `coverage`, `pure`, variant `pip`) match the math that produced them. |
| `edge_handled` | An invalid input produces either the expected reference result or a clean raise — no NaN, no traceback, no phantom PIPs. |
| `harness_error` | Infrastructure failure (driver missing, numpy unavailable, skill module moved). Never counted as pass or fail in summary stats. |

### Fail categories (13)

| Category | Bug class |
|---|---|
| `pip_value_incorrect` | PIPs outside tolerance of reference |
| `pip_nan_silent` | NaN z, zero SE, or negative n → NaN PIPs with no raise |
| `susie_null_forced_signal` | Null locus (z≡0) returns PIP ≈ 0.7 per variant because IBSS has no null component |
| `susie_spurious_secondary_signal` | One-signal locus returns phantom PIP ≈ 0.3 on second variant |
| `susie_nonconvergence_suppressed` | `converged=False` but method still returns scored PIPs |
| `credset_pip_is_alpha_mismatch` | `variants[i]["pip"]` reports single-effect α, not true PIP = 1 − ∏(1−αₗ) |
| `credset_purity_mean_hides_weak` | Mean-absolute-r purity lets a weakly-linked variant hide behind tightly-linked pairs |
| `credset_purity_none_wrongly_pure` | `R=None` → `purity=None` → `pure=True` with no LD evidence |
| `credset_coverage_incorrect` | Reported `coverage` ≠ sum of included weights |
| `abf_variant_n_collapsed` | Per-variant `n` column collapsed to median `n_eff`; variant-specific variance lost |
| `susie_moment_field_mislabeled` | `mu`/`mu2` are α-weighted contributions, not posterior moments conditional on inclusion |
| `input_validation_missing` | `n ≤ 0`, `coverage ≤ 0`, or other out-of-range parameters silently accepted |
| `edge_crash` | Edge case that should be handled raises an uncaught traceback |

Three findings surfaced during review but are **not** in this first pass:

- `susie_elbo_trace_missing` — requires computing a reference
  ELBO with the trace term, which is a significant numerical exercise
  and better deferred to v0.2.
- `benchmark_ld_zscore_mismatch` and `benchmark_failure_state_ignored`
  — these live in ClawBio's `tests/benchmark/finemapping_benchmark.py`,
  not in the skill itself. They belong in a future
  `finemapping_benchmark_harness` that audits the
  benchmark-of-the-benchmark.
- `benchmark_single_locus_no_power` — same scope as above.

## Test case corpus (16 Model B directories)

Each test case lives at
`src/clawbio_bench/test_cases/finemapping/fm_NN_<slug>/` and contains:

- `ground_truth.txt` — YAML frontmatter declaring METHOD,
  EXPECTED_PIPS, TOLERANCE, FINDING_CATEGORY, DERIVATION, and
  PAYLOAD: inputs.json.
- `inputs.json` — numeric inputs (z, se, n, R, params) the driver feeds
  to the skill.

| # | Slug | Method | Finding category | Trigger |
|---|---|---|---|---|
| 01 | `fm_01_abf_single_causal` | ABF | `finemap_correct` | z=[5,0.1,0.1], se=[0.1,0.1,0.1]; reference PIP[0] ≥ 0.999 |
| 02 | `fm_02_susie_single_causal` | SuSiE | `finemap_correct` | 5 variants, one causal at idx 0, R=I, n=5000, L=1; reference PIP[0] ≥ 0.99 |
| 03 | `fm_03_susie_null_locus` | SuSiE | `susie_null_forced_signal` | z=[0,0,0], R=I, n=100, L=3; reference PIP < 0.1, observed 19/27 ≈ 0.7037 |
| 04 | `fm_04_susie_phantom_secondary` | SuSiE | `susie_spurious_secondary_signal` | z=[5,0], R=I, n=100, L=2; reference PIP=[>0.999, <0.001], observed ≈ [0.99988, 0.29770] |
| 05 | `fm_05_abf_variant_n_collapsed` | ABF | `abf_variant_n_collapsed` | z=[5,5], n=[100,10000], no se; reference PIPs [0.43130, 0.56870]; observed [0.5, 0.5] |
| 06 | `fm_06_abf_nan_zero_se` | ABF | `pip_nan_silent` | z=[3,5,2], se=[0,0,0]; reference: raise ValueError; observed: [NaN,NaN,NaN] |
| 07 | `fm_07_abf_nonpositive_n` | ABF | `input_validation_missing` | z=[10,0], n=[0,0]; reference: raise; observed: uniform [0.5,0.5] |
| 08 | `fm_08_susie_negative_n` | SuSiE | `pip_nan_silent` | z=[3,0], R=I, n=-100, L=1; reference: raise; observed: [NaN,NaN] |
| 09 | `fm_09_credset_pip_mismatch` | SuSiE + credset | `credset_pip_is_alpha_mismatch` | 3 variants, L=2, both effects place mass on variant 0; check credset `variants[0]["pip"]` equals true PIP = 1-(1-α₀[0])(1-α₁[0]), not α_l[0] |
| 10 | `fm_10_credset_purity_mean_hides_weak` | SuSiE + credset | `credset_purity_mean_hides_weak` | α=[[0.4,0.35,0.25]], R=[[1,0.9,0],[0.9,1,0.9],[0,0.9,1]], min_purity=0.5; reference min=0.0 (fail), observed mean=0.6 pure=True |
| 11 | `fm_11_credset_purity_none_wrongly_pure` | SuSiE + credset | `credset_purity_none_wrongly_pure` | build_credible_sets_susie with R=None; expect `pure` to be null/false; observed `pure=True` with `purity=None` |
| 12 | `fm_12_susie_nonconvergence` | SuSiE | `susie_nonconvergence_suppressed` | 50 variants, block LD ρ=0.95, L=10, max_iter=3; check `converged=False` returned alongside scored PIPs |
| 13 | `fm_13_credset_coverage_zero` | credset | `input_validation_missing` | ABF-style credset with pips=[0.6,0.4], coverage=0.0; reference: empty or raise; observed: [0] |
| 14 | `fm_14_susie_moment_mislabeled` | SuSiE | `susie_moment_field_mislabeled` | z=[5,5,0], R=I, n=100, L=1, w=0.04; reference conditional posterior mean = 4.0 (= w/(V+w) · z); observed mu[0][0] ≈ 2.0 (α-weighted) |
| 15 | `fm_15_abf_extreme_z` | ABF | `finemap_correct` | z=[50,0.1,0.1], se=[0.01,0.01,0.01]; reference PIP[0] ≈ 1.0; pins log-space stability at \|z\|→∞ against regression to naive exp() |
| 16 | `fm_16_credset_coverage_field` | credset_susie | `credset_coverage_incorrect` | α=[[0.5,0.3,0.15,0.05]], coverage=0.95; the harness independently sums `variants[*]["alpha"]` and compares to the reported `coverage` field (tolerance 1e-4); pins current correct behaviour against a future regression where the skill starts returning the requested threshold instead of the measured sum |

## Ground truth YAML schema

```yaml
# ---
# BENCHMARK: clawbio-finemapping v0.1.0
# PAYLOAD: inputs.json
# METHOD: abf | susie | credset_abf | credset_susie
# FINDING_CATEGORY: <one of the 16 categories>
# EXPECTED_PIPS: "[0.43130, 0.56870]"          # stringified JSON array
# EXPECTED_ALPHA: "[[..],[..]]"                 # optional, susie only
# EXPECTED_CONVERGED: "true" | "false"          # optional
# EXPECTED_PURE: "true" | "false" | "null"      # optional
# EXPECTED_CREDSET_SIZE: "2"                    # optional
# EXPECTED_COVERAGE: "0.95"                     # optional
# EXPECTED_MOMENT_MU_00: "4.0"                  # optional, susie only
# PIP_TOLERANCE: "0.01"
# MOMENT_TOLERANCE: "0.05"
# EXPECTED_EXIT_STATUS: "ok" | "raise"          # "raise" means skill SHOULD raise
# DERIVATION: "Wakefield 2009 eq. 3 with V_i=1/n_i..."
# WAKEFIELD_REF: WAKEFIELD_2009
# WANG_REF: WANG_2020
# ---
```

All numeric fields are stored as strings (YAML frontmatter normalises
scalars per `core._normalize_yaml_value`). The harness parses them with
`json.loads` so arrays/matrices survive intact.

## `inputs.json` schema

```json
{
  "method": "abf" | "susie" | "credset_abf" | "credset_susie",
  "z": [float, ...],
  "se": [float, ...] | null,
  "n": int | [int, ...] | null,
  "R": [[float, ...], ...] | null,
  "w": float | null,
  "L": int | null,
  "max_iter": int | null,
  "tol": float | null,
  "min_purity": float | null,
  "coverage": float | null,
  "alpha": [[float, ...], ...] | null,
  "pips": [float, ...] | null,
  "rsids": [str, ...] | null
}
```

Only the keys relevant to the method are used; the driver ignores
extras. `alpha`/`pips`/`rsids` are only used for credible-set tests
that supply pre-computed weights directly to bypass the full SuSiE run
(e.g. `fm_10`, `fm_11`).

## Driver output JSON schema

```json
{
  "method": "abf" | "susie" | "credset_abf" | "credset_susie",
  "status": "ok" | "import_error" | "raised" | "driver_error",
  "error": null | {"type": "str", "message": "str", "traceback": "str"},
  "pips": [float, ...] | null,
  "alpha": [[float, ...], ...] | null,
  "mu": [[float, ...], ...] | null,
  "mu2": [[float, ...], ...] | null,
  "converged": bool | null,
  "n_iter": int | null,
  "elbo_history": [float, ...] | null,
  "credible_sets": [{"cs_id": str, "size": int, "coverage": float, "pure": bool, "purity": float | null, "variants": [{"rsid": str, "pip": float, "alpha": float, ...}, ...]}, ...] | null,
  "warnings": [str, ...],
  "driver_version": "0.1.0"
}
```

`status="raised"` means the skill itself raised; `error` describes
the exception. `status="ok"` means the call returned without raising.
The harness consults both `status` and the numeric fields to score.

## Scoring decision tree

Per test case, the harness:

1. Parses `ground_truth.txt` for `FINDING_CATEGORY` and expected values.
2. Runs the driver via `core.capture_execution`.
3. If the driver exit code is non-zero: return `harness_error` (driver
   infra broken).
4. Parses driver stdout as JSON.
5. Dispatches to a per-category scorer:
   - `finemap_correct`: verify `status="ok"`, compare each expected
     field within tolerance, return `finemap_correct` or
     `pip_value_incorrect`.
   - `pip_nan_silent`: look for NaN/inf in returned PIPs AND
     `status="ok"` (skill silently returned garbage). If `status="raised"`
     with a value-error-shaped exception, return `edge_handled`.
   - `susie_null_forced_signal`: check `max(pips) > 0.1`; if yes, fire.
   - `susie_spurious_secondary_signal`: check `pips[1] > 0.05` while
     `pips[0] > 0.9`.
   - `credset_pip_is_alpha_mismatch`: recompute true PIPs from `alpha`,
     compare to the credset's `variants[*]["pip"]` field; mismatch fires.
   - `credset_purity_mean_hides_weak`: recompute min-pair-|r| from the
     supplied `R`, compare to `min_purity`, assert `pure=True` is
     incorrect.
   - `credset_purity_none_wrongly_pure`: check `purity is None and
     pure is True`.
   - `credset_coverage_incorrect`: sum `variants[*]["alpha"]` and
     compare to reported `coverage`.
   - `abf_variant_n_collapsed`: direct PIP comparison against pre-computed
     reference values.
   - `susie_moment_field_mislabeled`: compare returned `mu[0][0]`
     against expected conditional posterior mean.
   - `susie_nonconvergence_suppressed`: require `converged=False` in
     output AND `pips` non-null (skill produced numerics despite
     non-convergence).
   - `input_validation_missing`: expect `status="raised"`; if skill
     returned normal output, fire the fail category.
   - `edge_crash`: any uncaught traceback in stderr or exit code < 0.

The decision tree mirrors the equity harness (see
`equity_harness.score_equity_verdict`) — one function per category with
clear fall-throughs.

## Chain of custody

Every test case produces a verdict JSON under
`results/finemapping/<commit>/<tc_name>/verdict.json` with:

- `driver_path` + SHA-256 of the driver script (proves which driver was
  run),
- `inputs_sha256` of the inputs JSON,
- `stdout_sha256` / `stderr_sha256` of the driver's output,
- `reference_values` field embedded from ground truth so the verdict is
  self-contained even if the test case file is later edited.

Same `build_verdict_doc()` call as every other harness; no new
machinery.

## References

- **WAKEFIELD_2009**: Wakefield J. (2009). "Bayes factors for
  genome-wide association studies: comparison with p-values."
  Genet Epidemiol 33(1):79-86. doi:10.1002/gepi.20359.
- **WANG_2020**: Wang G., Sarkar A., Carbonetto P., Stephens M. (2020).
  "A simple new approach to variable selection in regression, with
  application to genetic fine mapping." J R Stat Soc Series B
  82(5):1273-1300. doi:10.1111/rssb.12388.

## Out of scope for v0.1

- ELBO correctness — requires a reference ELBO implementation with the
  trace term.
- Benchmark-of-the-benchmark audits — deserve their own harness that
  targets `tests/benchmark/finemapping_benchmark.py` directly.
- SuSiE heteroscedasticity — requires a reference comparison against
  `susieR`, which is a significant dependency to take on.
- Credible-set overcoverage — low severity, deferred.

These are tracked as follow-ups in `CHANGELOG.md` under the v0.2
planning section.
