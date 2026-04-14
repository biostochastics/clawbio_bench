# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.5] — 2026-04-14

### Added

- **GWAS-PRS benchmark harness** (`gwas_prs_harness.py`, 8 test cases).
  Validates the `gwas-prs` skill (polygenic risk score calculator) against
  analytically derived ground truth using the standard additive dosage model.
  7 verdict categories: `score_exact_match`, `percentile_correct`,
  `coverage_correctly_flagged`, `score_incorrect`, `percentile_incorrect`,
  `coverage_not_flagged`, `missing_output`. Tests cover full/partial/zero
  coverage, homozygous/heterozygous dosages, hemizygous genotype parsing,
  and percentile boundary validation. Total suite: 10 harnesses, 183 test
  cases. (PR #19, Manuel Corpas)

### Changed

- **Softened daily report language.** All LLM system prompts now use
  neutral, constructive phrasing: "areas for improvement" instead of
  "worst performers", "clinical considerations" instead of "clinical
  risks", "declined" instead of "regression", "recurring themes" instead
  of "systemic issues". GitHub issue titles changed from "Regression
  detected" to "Audit findings" with matching label rename.

- **Investigation prompt scoped to coverage gaps only.** The
  investigation section no longer re-analyzes per-harness pass rates
  (already covered by the digest). It receives a compact category-only
  summary instead of the full aggregate JSON, eliminating redundant LLM
  analysis and reducing token consumption.

- **Self-changelog is now incremental.** Tracks the last-summarized
  clawbio-bench commit SHA in `baselines/log/state.json` and uses
  `git log <last_sha>..HEAD` instead of a 7-day sliding window. Each
  daily report now contains only new changes, eliminating the 6/7-day
  content overlap between consecutive runs.

- **ClawBio diff uses explicit commit ranges.** Tracks the last-audited
  ClawBio commit in `baselines/log/state.json` and diffs against it
  instead of using a rolling `HEAD~5` window. No more stale changes
  reappearing in consecutive daily reports.

- **Regression sweep gated on actual decline.** The `regression-sweep`
  CI job now runs only when the daily audit detects a pass-rate drop
  vs baseline, instead of running unconditionally on every daily run.

- **Daily audit downloads rolling baseline.** `daily-audit.yml` now
  downloads the `baseline-main` release asset (published by
  `audit-baseline.yml` at 04:17 UTC) instead of relying solely on a
  repo-committed baseline, ensuring consistent delta comparisons.

### Fixed (review of PR #19)

- **`harness_error_verdict` called with wrong kwargs** — PR used `error=`
  and `output_dir=` but core.py signature is `(test_case_name, commit_meta,
  exception, ground_truth)`. Would have raised `TypeError` on every
  infrastructure failure, violating the "never abort" contract.
- **`driver_path` passed Model B directory to `sha256_file()`** — caused
  `IsADirectoryError` on every successful verdict. Now passes
  `ground_truth.txt` file per the cvr_identity pattern.
- **PGS000013 citation was wrong** — PGS000013 is GPS_CAD (Khera 2018,
  coronary artery disease, 6.6M variants), not T2D. Vassy et al. 2014 T2D
  scores are PGS000031/32/33. `GROUND_TRUTH_REFS` now documents the
  benchmark as a synthetic 8-variant T2D subset derived from DIAGRAM/GWAS
  Catalog loci.
- **Non-zero exit falsely passed coverage tests** — a tool crash on a
  low-coverage input was classified as `coverage_correctly_flagged`.
  Now always returns `missing_output` on non-zero exit.
- **Coverage mismatch silently passed** — when `observed_coverage !=
  expected_coverage`, the code only set a detail flag. Now returns
  `score_incorrect`.
- **`None` format crash in success rationale** — `f"PRS={observed_prs:.6f}"`
  raised `TypeError` when `observed_prs` was `None`.
- **Unused `import math`** removed (ruff F401).
- **Misleading test case directory names** renamed:
  `gwas_05_cad_partial_coverage` -> `gwas_05_all_hom_ref_zero_dosage`,
  `gwas_07_hemizygous_x` -> `gwas_07_hemizygous_genotype`,
  `gwas_08_all_ref_genotype` -> `gwas_08_single_variant_below_threshold`.
- **Numeric coercion** for `observed_prs` and `observed_percentile` — tool
  JSON may emit strings; now coerced via `float()` with
  `contextlib.suppress`.
- **JSON parse error handling** in `parse_prs_results` — malformed tool
  output no longer raises `JSONDecodeError`.
- **Removed unused `variants` parameter** from `score_prs_verdict` and
  removed dead `parse_prs_variants` function.

### Fixed

- **Polish pass verified against original aggregate.** `_verify_numbers`
  in `write_daily_log` was passing an empty `{}` for the aggregate
  parameter, allowing numbers fabricated by the LLM swarm to survive
  the polish pass unchecked. Now passes the original `aggregate` dict
  so all numbers are verified against ground truth.

## [0.1.4] — 2026-04-07

### Fixed (post-PDF-review batch)

The first v0.1.4 PDF report against ClawBio HEAD `e7590141` surfaced
five incorrect bench findings that were misclassifying tool behavior.
All five are now fixed and the suite-level pass rate improved from
158/175 (90.3%) to **163/175 (93.1%)**.

- **`_phenotype_matches` rejected byte-identical phenotype strings.**
  When `observed == expected` (e.g. MTHFR `"Strongly reduced MTHFR
  enzyme activity (677TT)"`), the function lowercased + stripped both
  sides, then iterated CPIC-oriented `_KEY_PATTERNS` and `_KEY_TERMS`,
  found no match (because MTHFR uses DPWG vocabulary not CPIC), and
  fell through to the substring fallback — which is gated by
  `_MAX_SUBSTRING_MATCH_LEN = 40` and rejects strings longer than that.
  Both equal strings then returned `False`, producing the absurd verdict
  `"Wrong: X (expected: X)"`. Added a byte-equality fast path at the
  top of `_phenotype_matches` plus a whitespace-normalised identity
  check immediately after. Affects: `mthfr_677tt_methotrexate`.

- **`phi_patient_identifiers_in_header` had an invalid rs5030655
  genotype.** The test wrote `rs5030655 22 42524244 CC` thinking C was
  the reference allele. dbSNP `rs5030655` is the CYP2D6*6 frameshift
  deletion (1707delT, NCBI `delA` on the forward strand, `delT` on the
  minus / 23andMe strand). The reference genotype on the 23andMe
  convention is `TT`, not `CC`. ClawBio's `pharmgx_reporter` lookup
  table also has a latent data bug — it lists `{"alt": "C"}` for
  rs5030655, which is incorrect — and the bug compounds: ClawBio
  interprets the bench's invalid `CC` as homozygous *6 → Poor
  Metabolizer, defeating the PHI test's *1/*1 ground truth.
  Corrected the bench input to `TT` (the actual reference per
  SNPedia + dbSNP); ClawBio now correctly returns *1/*1 Normal
  Metabolizer for the panel and PHI is still blocked from all output
  files. The ClawBio-side rs5030655 mis-encoding is a separate
  pharmgx finding to be filed against the harness rather than the
  PHI test.

- **fm_18 ground truth was the standard-SuSiE PIP baseline (0.488)
  but ClawBio now uses null-weighted SuSiE (0.385).** Manuel's
  237cbd9 patch added a `null_weight = 1/(L+1)` default to ClawBio's
  `run_susie_inf`, which puts prior mass on a "no effect" bucket
  alongside the p variants. The per-row inclusion alpha drops from
  `1/p` to `(1 - null_weight)/p` and the aggregated PIP for a null
  locus drops from `1 - (1 - 1/p)^L` to `1 - (1 - (1-nw)/p)^L`. Ported
  Manuel's `null_weight` extension into the vendored gentropy oracle
  (`scripts/_reference/gentropy_susie_inf.py`), pinned `null_weight =
  1/(L+1)` in fm_18 / fm_20 / fm_21 inputs.json so the oracle and
  ClawBio agree, and re-derived all three test cases via
  `scripts/derive_finemapping_ground_truth.py`. fm_18 now passes
  `finemap_correct` against ClawBio HEAD.

- **CVR-ACMG harness aggregated criteria across the entire 20-variant
  demo panel.** `analyze_acmg_correctness` iterated all variants and
  merged their `triggered_criteria` into a single panel-wide
  `criteria_found` dict. Tests like `cvr_29_benign_combo` (asserting
  BS1+BP1 = Likely Benign) then saw the union of criteria across all
  20 variants — `{BA1, BP4, BP6, BP7, BS1, PM1, PM2, PP3, PP5, PS1,
  PVS1}` — and fired `classification_aggregation_error` even when the
  intended single variant carried the right criteria. Added optional
  `target_rsid` and `target_gene` parameters to
  `analyze_acmg_correctness`; the variant loop now restricts criterion
  / classification extraction to the matching variant. Ground truth
  files declare `TARGET_RSID:` or `TARGET_GENE:` to scope each test.
  Affects: `cvr_24_pvs1_wrong_mechanism` (now targets RYR2 — a
  textbook gain-of-function gene where PVS1 should NOT apply),
  `cvr_29_benign_combo` (now targets APC and asserts BS1+BP4 instead
  of BS1+BP1, since ClawBio's demo doesn't include any BP1 variants).

- **Four CVR-ACMG tests asserted evidence that ClawBio's demo panel
  cannot express.** `cvr_25_pvs1_strength_mod` (PVS1_Moderate),
  `cvr_26_pp3_single_tool` (PP3 at calibrated strength),
  `cvr_30_vcep_brca1` (ENIGMA VCEP citation), and
  `cvr_31_vcep_lynch_mlh1` (InSiGHT VCEP citation) all probe ACMG
  features that ClawBio's `--demo` mode does not currently emit.
  These were firing as critical findings but should have been advisory
  pending data. Added a `KNOWN_LIMITATION_DEMO_LACKS_EVIDENCE: true`
  ground-truth flag and a harness check that routes such tests to the
  existing advisory `criteria_not_machine_parseable` category. Each
  test will auto-flip to a real verdict the moment ClawBio's demo
  grows the missing evidence (or the bench gains a per-variant input
  mode for CVR tests).

- **`scripts/_reference/gentropy_susie_inf.py` `ssq_range` lower bound
  was 0** — flagged by CodeAnt review on PR #13 as a possible
  divide-by-zero bug. The bounded optimizer can land on the boundary
  and feed `ssq=0` into `1/ssq` and `log(ssq*omega)` downstream,
  producing Inf/NaN. Tightened the lower bound to `1e-12`.

- **Oracle / driver `ssq` initialization mismatch** — second CodeAnt
  finding on PR #13. The bench driver computes `ssq_init = w` (from
  `inputs.json`, typically 0.04) but `_run_oracle` was calling
  gentropy's `susie_inf` with the default `ssq=None` which becomes
  `np.ones(L) * 0.2`. Threading `ssq_init = inputs.get("ssq_init",
  inputs.get("w"))` into the oracle call so derived ground truth
  matches the runtime configuration on every test.

### Added

#### Fine-mapping: SuSiE-inf est_tausq activation honesty test (fm_20 + fm_21)

- **New harness category `susie_inf_est_tausq_ignored`** (critical
  tier).  Catches a subtle but consequential defect: a tool that
  advertises SuSiE-inf but never actually estimates and applies the
  infinitesimal variance component τ². The category covers two
  observationally identical failure modes:
    * **Mode A — dead code in the IBSS loop:** `_mom_update` is called
      with `est_tausq=False` hardcoded, OR `run_susie_inf` doesn't
      expose the `est_tausq` parameter at all, OR the parameter is
      never propagated from the public API. The τ²-estimation branch
      is literally unreachable. ClawBio's pre-237cbd9 build exhibited
      this defect.
    * **Mode B — defensive threshold suppression:** A "noise filter"
      that zeros out the correctly-estimated τ² before applying it to
      the variance structure (e.g.
      `effective_tausq = tausq if tausq >= 1e-3 else 0.0`). In
      practice the gentropy reference produces τ² estimates in the
      1e-5 to 1e-4 range on realistic SuSiE-inf inputs, so any
      threshold above ~1e-4 nullifies activation across all
      geometries. The output is byte-equivalent to mode A. ClawBio's
      post-237cbd9 build exhibits this defect.
- **fm_20 redesigned** as a deterministic activation honesty test.
  New geometry: p=200, n=5000, L=5, two-block LD with ρ=0.20,
  block-uniform alternating z (±1.5) with two opposite-sign bumps at
  variant indices 17 and 123 (±3.5).  Test passes iff the tool reports
  `tausq > 0` AND PIP[17] / PIP[123] match the gentropy reference
  oracle within tolerance 0.05.  ClawBio's current build fires
  `susie_inf_est_tausq_ignored` because both conditions fail
  simultaneously (the dead-code signature).
- **fm_21 added** as the est_tausq=False guard partner.  Same input
  geometry but with `est_tausq=False`.  On a correct implementation
  this produces τ²=0 and PIPs at the standard-SuSiE diffuse-row
  baseline ~0.16 for the bumps.  ClawBio's buggy build coincidentally
  passes fm_21 because its "always est_tausq=False" behavior matches
  what est_tausq=False is supposed to produce — fm_21's job is to
  cross-check the dead-code diagnosis from a second angle and to
  preserve toggle test discriminating power once ClawBio fixes the
  defect.

#### Reference oracle vendoring

- **`scripts/_reference/gentropy_susie_inf.py`.**  Vendored copy of
  Open Targets gentropy's SuSiE-inf reference port (Apache-2.0,
  attribution preserved in-file).  Pure numpy/scipy — no PySpark, no
  Hail, no GCP libs.  Used **only** by the offline ground-truth
  derivation script and never imported by `clawbio_bench` at runtime,
  preserving the loose-coupling invariant.  The gentropy port is
  itself a copy of FinucaneLab/fine-mapping-inf (the Cui 2023
  reference implementation) with the algorithm methods promoted out
  of their Spark wrapper class.
- **`scripts/derive_finemapping_ground_truth.py`.**  Deterministic
  ground-truth derivation for fm_20 and fm_21.  Constructs the
  geometry from typed Python (no random seeds), runs the gentropy
  oracle in both `est_tausq=True` and `est_tausq=False` modes,
  captures full PIP arrays, and writes both `inputs.json` and
  `derived/oracle_expected.json` to each test case directory.  Also
  templates fm_21's full 200-element `EXPECTED_PIPS` directly into
  its `ground_truth.txt` so the array length and bump positions can
  never drift from the inputs.  Cross-pair sanity checks (max ‖dPIP‖,
  τ²(20)>0, τ²(21)==0) run inline.

### Fixed

- **fm_18 EXPECTED_PIPS transcription bug.**  Was `[0.2, ...]`,
  corrected to `[0.488, ...]`.  The 0.2 value confused per-row
  inclusion alpha (1/p) with the aggregated SuSiE PIP across L=3
  single-effect rows; the correct formula is `1 − (1 − 1/p)^L =
  1 − 0.8^3 = 0.488` (Wang et al. 2020 Eq. 3, matches
  `susieR::susie_get_pip`).  The DERIVATION block in the same file
  already had this formula correct; only the EXPECTED scalar was
  wrong.  Tightened `PIP_TOLERANCE` 0.15 → 0.05.
- **fm_17 EXPECTED_PIPS transcription bug.**  Was
  `[0.95, 0.01, 0.01, ...]`, corrected to `[0.95, 0.34, 0.34, ...]`.
  The original "0.01" expectation implicitly assumed MoM activates
  τ²>0 and absorbs the polygenic background.  On this geometry MoM
  correctly truncates to 0 per Cui 2023 Methods, so SuSiE-inf
  collapses to standard SuSiE-RSS, and the L−1=4 unused single-effect
  rows produce a diffuse baseline `1 − 0.9^4 = 0.3439` per non-causal
  variant.  Tool's observed mean for variants 1-9 = 0.3514 — textbook
  diffuse-row baseline + small LD-induced inflation.
- **fm_20 (old design) EXPECTED_PIPS transcription bug.**  Was
  `[0.60, 0.10, ...]`, corrected during the redesign.  The old test
  is fully replaced by the new activation honesty test (above).
- **eq_15 GROUND_TRUTH_FST transcription bug.**  Was `0.500`,
  corrected to `1.000`.  Caught by Manuel/POP during the v0.1.3
  remediation re-run against ClawBio HEAD.  Nei's GST for
  POP_A_vs_POP_B on the eq_15 VCF is mathematically 1.0 (perfect
  differentiation of POP_A all `1/1` vs POP_B all `0/0`); the test's
  own DERIVATION comment already had this correct.  See PR #11.
- **README test counts corrected throughout.**  Total test cases
  170 → 174 → 175 (with the addition of fm_21).  Fine-mapping count
  16 → 20 → 21.  "Six harnesses" / "all six" → nine, where it was
  claimed.  Unit tests 223 → 245.  CVR Phase 2 status updated to
  reflect that all three CVR harnesses (Phase 1 / 2c / 2a) shipped
  in v0.1.3.  See PR #10.
- **Fine-mapping harness rationale for missing-numpy errors.**  When
  the driver subprocess can't import numpy/pandas (because the bench
  was installed without the `[finemapping]` or `[dev]` extra), the
  verdict now returns an actionable rationale pointing at the
  install command instead of the generic "Driver infrastructure
  error" — preventing future auditors from wasting time triaging it
  as a tool-side bug.

### Changed

- **`[finemapping]` extra now requires scipy.**  ClawBio's
  `core/susie_inf.py` imports `scipy.linalg` and `scipy.special` at
  module load time, so the driver subprocess interpreter needs scipy
  available.  Without it, fm_17..fm_21 all report `harness_error`
  with "ModuleNotFoundError: No module named 'scipy'".  Added
  `scipy>=1.11` to both the `[finemapping]` and `[dev]` extras.
- **All harness `BENCHMARK_VERSION` constants bumped to 0.1.1.**
  Marks the v0.1.4 release across orchestrator, equity, pharmgx,
  nutrigx, metagenomics, finemapping, clinical_variant_reporter,
  cvr_identity, and cvr_correctness.  No verdict-shape changes; the
  bump exists so longitudinal sweeps can identify which fine-mapping
  test cases were derived from the gentropy oracle vs hand-written.

### Process notes

- **Triple-checked the dead-code finding.**  Confirmed by reading
  ClawBio's `core/susie_inf.py` directly (line 158: `est_tausq=False`
  hardcoded at the only `_mom_update` call site, and `run_susie_inf`
  has no `est_tausq` parameter at all in its public signature),
  cross-checked against the upstream gentropy reference (which
  exposes `est_tausq` and propagates it through `_MoM`), and
  validated end-to-end by running ClawBio's driver against the new
  fm_20 / fm_21 inputs and confirming byte-identical output between
  the two modes (stdout SHA-256 collision).  Notified the dev
  separately; this PR is bench-side only and does not patch ClawBio.
- **Ground-truth derivation methodology change.**  Three of the four
  fine-mapping ground-truth fixes in this release (fm_17, fm_18, the
  old fm_20) plus eq_15 in PR #11 were the same class of bug:
  hand-transcription of EXPECTED_* fields produced values
  inconsistent with the test's own DERIVATION comments.  Going
  forward, fm_20 and fm_21 derive their ground truth from the
  vendored gentropy oracle via `derive_finemapping_ground_truth.py`,
  and the ground truth files carry `DERIVED_FROM` annotations so
  drift is detectable by re-running the script.

## [0.1.3] — 2026-04-07

### Added

#### Report flow refactor — legend-driven severity tiers

- **Severity tiers are now declared per category in each harness's
  `CATEGORY_LEGEND`.**  Every entry carries a `"tier"` field set to one
  of `"pass" | "advisory" | "warning" | "critical" | "infra"`.  Both
  report generators (`markdown_report.py` and `scripts/generate_report.py`)
  read the tier directly from the legend instead of hardcoding a
  category-to-tier mapping — adding a new harness now requires zero
  edits in either renderer.  93/93 categories across all 9 harnesses
  carry tier annotations.
- **`TIER_NAMES` / `TIER_RANKS` constants in `core.py`.**  Single source
  of truth for the 5-tier severity system shared across report
  generators.  Includes `derive_tier_from_category_sets()` helper for
  categories that lack explicit tier annotation (fallback: fail →
  critical, pass → pass, harness_error → infra, else → warning).
- **Aggregate self-contained.**  `cli.run_single_harness` now echoes
  `fail_categories` and the full `category_legend` (with tier info)
  into each per-harness block of `aggregate_report.json`, so the
  markdown renderer no longer needs to read 9 separate
  `heatmap_data.json` files to resolve tier metadata.
- **`build_tier_lookup()` in `scripts/generate_report.py`.**  Resolves
  every category seen in the run to a numeric tier rank at render
  time, with per-harness heatmap legends as the primary source and
  algorithmic fallback for stragglers.  Uses "most severe wins" when
  the same category name appears in multiple harness legends.  Tier
  names are normalized (case + whitespace) so `"Critical"` /
  ` critical ` resolve identically.
- **Markdown renderer emits 4-level severity indicators.**  PR comments
  now distinguish 🔴 critical / 🟠 warning / 🟡 advisory / ⚪ infra
  instead of collapsing warnings and criticals into one bucket.
  Sort order mirrors the Typst report (critical first, then warning,
  advisory, infra).
- **Typst renderer uses canonical tier names + colors.**  The
  `emit_findings_section` severity grouping now reads tier names and
  fills from `TIER_DEFS` instead of a local palette map, so group
  headers and cell fills stay in lockstep with every other tier-aware
  rendering path in the report.

#### CVR Phase 2 — ACMG correctness benchmark suite

- **CVR Phase 2c harness: variant identity / HGVS validation.**
  New harness `cvr_identity_harness.py` with 9 rubric categories
  validates HGVS v21.1 syntax (Hart 2024, PMID 39702242), MANE Select
  transcript usage, versioned accessions, indel normalization, and
  assembly coordinate consistency.  6 test cases (`cvr_10`–`cvr_15`).
  Registered in CLI as `cvr_identity`.

- **CVR Phase 2a harness: ACMG classification correctness.**
  New harness `cvr_correctness_harness.py` with 16 rubric categories
  validates criterion-level correctness (PVS1 strength per Abou Tayoun
  2018, PP3/BP4 calibration per Pejaver 2022, BA1/BS1/PM2 thresholds),
  VCEP supersession (ENIGMA, InSiGHT), SF v3.3 (84 genes), and
  ClinGen gene-disease validity.  13 test cases (`cvr_20`–`cvr_32`)
  with Gold/Silver truth tiers.  Registered in CLI as `cvr_correctness`.

- **Dual-layer ground truth architecture.**  Both Phase 2 harnesses
  support two independent layers: `EXPECTED_*` headers capture the
  clinical gold standard, `EXPECTED_TOOL_*` headers capture what the
  tool is documented to produce.  New `self_consistency_error` rubric
  category fires when tool output contradicts its own documented
  behavior.  Gold-standard checks always run before self-consistency
  to preserve safety priority.

- **Phase 2 PRD document.** `docs/plans/CVR_PHASE2_PRD.md` with
  triple-verified standards ground truth (ACMG/AMP, ClinGen SVI,
  HGVS, GA4GH, CPIC/PharmVar), architectural review by GPT-5.2-pro,
  and phased roadmap (2c → 2a → 2b/Phase 3).  Standards independently
  verified across three research passes (Exa + ref.tools + Tavily).

#### PharmGx expansion (separate from Phase 2 — see "PGx categories" note below)

- **PharmGx harness: 8 new test cases (36 → 44 total).**
  - CYP1A2 coverage (3 tests): `cyp1a2_normal`, `cyp1a2_ultrarapid_1f1f`,
    `cyp1a2_poor_1c1c` — fills zero-test gene gap covering clozapine
    (DPWG CYP1A2/clozapine guidance).
  - CYP2C9 standalone (2 tests): `cyp2c9_pm_star3_star3` (phenytoin/NSAIDs),
    `cyp2c9_im_star1_star2` (celecoxib/ibuprofen) — validates non-warfarin
    CYP2C9 drug coverage per CPIC (Theken 2020, PMID 32189324).
  - CYP2D6*10 (1 test): `cyp2d6_10_het` — catches CPIC 2020 activity score
    boundary error (Caudle 2020, PMID 31647186: AS 1.25 = NM, not IM).
  - CPIC Level A scope honesty (2 tests): `hla_a3101_carbamazepine_indeterminate`
    (Phillips 2018, PMID 29392710), `hla_b5801_allopurinol_indeterminate`
    (Hershfield 2013, PMID 23232549) — validates tool now discloses
    HLA-A and HLA-B as "Indeterminate (not in panel)".

> **Note: PGx vs CVR rubric scopes are intentionally separate.**  The
> PharmGx harness uses its own 7-category rubric (`correct_determinate`,
> `scope_honest_indeterminate`, `disclosure_failure`, etc.) tuned to
> phenotype/drug-action validation against CPIC guidelines.  CVR Phase 2
> uses ACMG-specific categories (`pvs1_strength_error`,
> `vcep_rules_ignored`, etc.) tuned to germline variant interpretation.
> Phase 3 may cross-pollinate (CPIC compliance for the CVR module's
> pharmacogenomic findings) but the harnesses remain separate modules.

- **Braille logo with block-letter wordmark in README.** Lobster-claw-
  under-magnifying-glass braille art with `clawbio` / `_bench` in
  industrial block letters alongside.
- **Actual audit report PDF.** `clawbio_audit_report_20260406.pdf`
  (21-page, 7-harness smoke run, v0.1.2, 125/147 passing at ClawBio
  HEAD `bb9ffff`) replaces the broken `sample_audit_report.pdf` link.

### Changed

- **Phase 1 CVR references updated.** Added `REHDER_2021` (PMID
  33927380, supersedes Rehm 2013 for NGS technical standards),
  `PEJAVER_2022` (PMID 36413997, PP3/BP4 calibration), and `LEE_2025`
  (PMID 40568962, SF v3.3, 84 genes) to `GROUND_TRUTH_REFS`.  Fixed
  Abou Tayoun 2018 journal reference (Human Mutation, not Genetics in
  Medicine).  Added PMIDs to existing references.

- **PharmGx: 3 existing tests updated for ClawBio HEAD SV handling.**
  `cyp2d6_del`, `cyp3a5_7_ins`, `ugt1a1_28_het` changed from
  `disclosure_failure` to `scope_honest_indeterminate` — tool now returns
  "Indeterminate (structural variant not assessed)" for het calls at
  DEL/INS/TA7 loci instead of silently skipping.

- **PharmGx ground truths triple-verified.** All 44 test cases validated
  against CPIC guidelines, PharmVar allele definitions, and published
  literature (Exa search + Droid/Gemini/GLM-5 multi-model review).
  Corrections applied: CYP2D6*10 AS boundary (IM → NM per CPIC 2020),
  CYP2C9*3 terminology (decreased → no-function per CPIC 2020),
  CYP1A2*1C European frequency (4% → 1.6% per CDC data), phenytoin PM
  dose (25% → 50%), CPIC_REF tag (CPIC_STATINS → CPIC_NSAIDS).

- **PharmGx: diclofenac/naproxen overclaim documented.** Tool classifies
  diclofenac as "avoid" for CYP2C9 PM, but CPIC Table S9 (Theken 2020)
  explicitly states "no recommendation" — diclofenac PK is "not
  significantly impacted by CYP2C9 genetic variants in vivo."

- **Live inventory-driven executable detection in orchestrator harness.**
  `score_routing_verdict()` now uses the live skill scan from
  `discover_clawbio_skills()` as the authoritative source for whether a
  skill is executable or a stub.  The manual `GROUND_TRUTH_EXECUTABLE`
  header in test case files is now a fallback, not the primary source.
  This prevents stale ground-truth files from producing false
  `stub_silent` verdicts when a ClawBio skill gains code between
  harness releases.

- **CI section restructured by repo ownership.** ASCII diagram showing
  which workflows live in `clawbio_bench` vs `ClawBio`, table of all 4
  bench-side workflows with triggers, and a separate section for the
  3-line ClawBio stub.

- **Project name standardized to `clawbio_bench`** (underscore) across
  README headings, prose, and CLI references.

- **Multi-line REFERENCE headers now supported.** Ground truth parser
  no longer warns on duplicate `REFERENCE` keys; subsequent values are
  appended with comma separators, allowing test cases to cite multiple
  primary sources cleanly.

### Fixed

#### CVR Phase 2 harness bugs (caught by 6-model code review + ClawBio HEAD test run)

- **HGVS protein regex crash.** Fixed `[a-z]{2}+` (re.error: multiple
  repeat) in `_HGVS_PROTEIN_RE` insertion pattern — changed to
  `(?:[A-Z][a-z]{2})+`.  Added extension (`ext`), synonymous with
  position, and unknown (`?`) patterns per HGVS v21.1.

- **Unpredicted protein false positive.** `_UNPREDICTED_PROTEIN_RE`
  now checks for missing opening paren (not closing paren), preventing
  false positives on properly parenthesized `p.(Ser42Cys)` expressions.
  Per-paren check is now opt-in via `CHECK_PROTEIN_PARENS: true` since
  HGVS v21.1 only RECOMMENDS parens; many clinical tools omit them.

- **In-silico tool name false positives.** Replaced substring matching
  with word-boundary regex for PP3/BP4 tool detection — "SIFT" no
  longer matches "sifting", "VEST" no longer matches "investigate".

- **PP3 overcounting cross-variant false positive (CRITICAL).**  The
  pp3_count tracker previously incremented once per `triggered_criteria`
  entry across ALL variants in a panel, causing every multi-variant
  demo run to falsely report `in_silico_overcounting` (15+ PP3 mentions
  across 20 variants).  Now tracks max PP3 per single variant, which is
  the actual ClinGen SVI Pejaver 2022 violation.

- **JSON field name mismatch (CRITICAL).**  Analyzer was looking for
  `criteria` field in `result.json` but ClawBio's CVR uses
  `triggered_criteria`.  Added fallback to support both naming
  conventions.  Without this fix, JSON parsing yielded zero criteria
  and the harness silently fell through to text-based regex extraction
  of report.md prose tables, causing every Phase 2a test to misfire.

- **Text fallback no longer counts PP3/BP4 mentions.**  Clinical reports
  list per-variant PP3 evaluation rows in their narrative tables, which
  is not the same as multiple PP3 *applications* to a single variant.
  Per-variant overcounting can only be detected from structured JSON.

- **REVEL thresholds corrected.** Pejaver 2022 Table 2 calibrated
  thresholds are [0.644,0.773)/[0.773,0.932)/≥0.932, not the
  previously stated 0.5/0.75/0.9.

- **VCEP supersession check tightened.**  Now requires the SPECIFIC
  expected VCEP name (ENIGMA, InSiGHT) in the output, not just any
  "expert panel" mention.  ClinVar review status often says "reviewed
  by expert panel" without the tool actually applying VCEP-specific
  rules.

- **MANE Select check relaxed by default.**  `CHECK_MANE_SELECT_STRICT`
  must be opt-in to require the literal "MANE Select" string.  Default
  mode accepts MANE-aligned transcripts cited by accession.  Recognizes
  that many tools cite MANE transcripts without spelling out the name.

- **Transcript versioning check is now panel-wide.**  Fails only if
  there are NO versioned transcripts at all in the report (not on
  first unversioned mention).  A mix is tolerated since many tools
  cite both forms in different sections.

- **Range hyphen regex restricted to ≥3-digit positions.**  The
  `_RANGE_HYPHEN_ERROR_RE` pattern previously false-positived on valid
  intronic offsets like `c.123-1del`.  Now requires both numbers to be
  ≥3 digits, which excludes intronic offsets (typically 1-2 digits)
  while still catching genuine range errors.

- **PVS1 missing reclassified to `classification_aggregation_error`.**
  Previously emitted `pvs1_strength_error` when PVS1 was entirely
  absent from output.  Strength error means PVS1 was applied at the
  wrong tier; missing entirely is a different failure mode and now
  gets a clearer rationale.

- **ClinGen GDV tiers clarified.** Changed "7 tiers" to "6
  classification tiers" — "No Known Disease Relationship" is the
  default uncurated state, not a scored classification tier.

- **Ghasemnejad 2026 misattribution removed.** The paper (PMC12916173)
  benchmarks variant prioritization, not criterion-level implementation
  errors.  Removed as reference for PP3 overcounting and PM2 claims
  in test cases `cvr_22` and `cvr_27`.

- **BA1 exception list noted.** Test case `cvr_20` hazard metric now
  mentions the ClinGen SVI BA1 exception list (Ghosh 2018, PMID
  30311383).

- **JSON parse errors recorded and surfaced in verdict details.**
  Both `analyze_variant_identity()` and `analyze_acmg_correctness()`
  now record `result_json_parse_error` instead of silently swallowing
  parse failures, and the field is propagated to verdict details for
  diagnosability.

#### Type-safety hardening

- **Full `mypy --strict` compliance restored.**  Fixed 99 `type-arg`
  errors across `core.py`, `cli.py`, `viz.py`, every harness module,
  and `finemapping_driver.py` — every `dict` / `list` / `Callable`
  annotation now carries explicit type arguments.  `regex` and
  `pandas` imports get targeted `type: ignore[import-untyped]`
  suppressions where stubs are unavailable.  20/20 source files pass
  mypy strict.

#### Pre-existing v0.1.3 fixes

- **3 false `stub_silent` findings for `struct-predictor` eliminated.**
  `ext_09_pdb`, `ext_10_cif`, and `kw_05_alphafold` ground truth files
  updated from `GROUND_TRUTH_EXECUTABLE: false` to `true` and
  `FINDING_CATEGORY` from `stub_silent` to `routed_correct`.
- **`FLOCK_API_KEY` now passed to daily audit smoke step.** The
  `inj_03_flock_routing_hijack` prompt-injection test previously always
  produced `unroutable_crash` in CI because FLock credentials were not
  available.  The daily-audit workflow now passes the secret, enabling
  the actual LLM routing path to be exercised.
- **`.gitignore` negation pattern.** `!sample_audit_report.pdf` replaced
  with `!clawbio_audit_report_20260406.pdf` so the actual report is
  tracked by git.

### Smoke Run Results (ClawBio HEAD `e3443f8`)

After all fixes, Phase 2 harnesses report real findings against ClawBio HEAD:

| Harness | Pass Rate | Real Findings |
|---------|-----------|---------------|
| Phase 1 (structural) | 4/5 (80%) | `data_source_version_missing` × 1 |
| Phase 2c (identity) | 3/6 (50%) | `transcript_selection_error` × 3 (Ensembl IDs unversioned, MANE not cited) |
| Phase 2a (correctness) | 7/13 (53.8%) | `vcep_rules_ignored` × 2 (BRCA1, MLH1), `pvs1_strength_error`, `pvs1_applicability_error`, `classification_aggregation_error`, `pp3_bp4_calibration_error` |

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
