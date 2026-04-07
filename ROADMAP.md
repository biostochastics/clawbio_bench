# Roadmap

> Last updated: 2026-04-06.
> Canonical source for all planned work. The README `## Roadmap` section
> is a summary pointing here. See also
> [`docs/plans/GAP_ANALYSIS_2026-04-04.md`](docs/plans/GAP_ANALYSIS_2026-04-04.md)
> for the audit-framework failure-class analysis that informed the initial
> priority ordering.

---

## Table of Contents

- [Status legend](#status-legend)
- [Release history](#release-history)
- [Milestone: v0.2.0 — Harness expansion + framework upgrades](#milestone-v020--harness-expansion--framework-upgrades)
- [P0 — Suite integrity (completed)](#p0--suite-integrity)
- [P1 — Clinical safety / highest harm potential](#p1--clinical-safety--highest-harm-potential)
  - [New harnesses](#p1-new-harnesses)
  - [Existing harness expansion](#p1-existing-harness-expansion)
- [P2 — Population / research integrity](#p2--population--research-integrity)
- [P3 — Workflow / data handling](#p3--workflow--data-handling)
- [Framework and tooling](#framework-and-tooling)
- [Audit-framework failure-class coverage](#audit-framework-failure-class-coverage)
- [ClawBio skill inventory](#clawbio-skill-inventory)
- [Open questions](#open-questions)

---

## Status legend

| Icon | Meaning |
|:----:|---------|
| ✅ | Done — shipped in a tagged release |
| 🚧 | In progress — code exists on a branch or in uncommitted changes |
| 📋 | Planned — scoped and prioritized but no code yet |
| 💡 | Proposed — needs scoping or design work before implementation |
| ⏸️ | Deferred — explicitly deprioritized with rationale |

---

## Release history

| Version | Date | Highlights |
|---------|------|------------|
| **v0.1.0** | 2026-04-04 | Initial release. 7 harnesses, 140 tests, full framework. |
| **v0.1.1** | 2026-04-06 | Report redesign (80→30pp), CPIC citation corrections, PharmGx test fixes. |
| **v0.1.2** | 2026-04-06 | `--tagged-commits`, 5-tier severity, bento dashboard, `scope_honest_indeterminate`, delta comparison. |
| **v0.1.3** | 2026-04-07 | CVR Phase 2c (variant identity, 6 tests) + Phase 2a (ACMG correctness, 13 tests), PharmGx 36→44 tests, multi-line REFERENCE headers, `tier_rank` core utilities, live inventory-driven executable detection. |

---

## Milestone: v0.2.0 — Harness expansion + framework upgrades

Target: close the highest-clinical-harm coverage gaps and ship the
framework features most requested by ClawBio's remediation workflow.

### Summary of planned work

- 4–6 new dedicated harnesses (P1 tier)
- YAML-only ground truth migration (all 140+ test cases)
- Parallel test execution (`--jobs`)
- Shared AST security sweep
- Re-verification of ClawBio pass rate after their remediation fixes

---

## P0 — Suite integrity

> All P0 items were completed in v0.1.0. Kept here for traceability.

| # | Item | Status | Release | Notes |
|---|------|:------:|---------|-------|
| P0-1 | Remove `STUB_SKILLS`/`EXECUTABLE_SKILLS` from verdict logic | ✅ | v0.1.0 | Verdicts now depend on per-test `GROUND_TRUTH_EXECUTABLE` only. Sets retained for drift detection. |
| P0-2 | Dynamic skill inventory via `discover_clawbio_skills()` | ✅ | v0.1.0 | Scans `skills/*/SKILL.md` + `*.py` at the target commit. Drift report in every verdict. |
| P0-3 | `--skill NAME` direct invocation tests | ✅ | v0.1.0 | 5 force-routing tests for previously-unreachable clinical skills. |
| P0-4 | `--skills A,B,C` multi-skill composition tests | ✅ | v0.1.0 | 2 composition-mode test cases. |

---

## P1 — Clinical safety / highest harm potential

### P1 New harnesses

Each new harness follows the contract in
[CONTRIBUTING.md](CONTRIBUTING.md) — module-level constants, a
`run_single_<name>()` function, bundled test cases, and registration
in `HARNESS_REGISTRY`.

| # | Harness | Status | ClawBio skill | Clinical-harm tier | Planned rubric | Notes |
|---|---------|:------:|---------------|:------------------:|----------------|-------|
| P1-1 | **`clinical-variant-reporter` Phase 2** | 📋 | `clinical-variant-reporter` | **Tier 1** | Small unambiguous-subset correctness: BA1 very-common benign, PVS1 clear LoF (established LoF-intolerant genes), ClinGen VCEP 3-star+ stable P/LP. Score classification direction only. | Phase 1 (structural/traceability, 5 tests) shipped in v0.1.0. Do **not** use MAVEdb as gold for PS3/BS3 — assay-to-strength mapping needs expert calibration. |
| P1-2 | **`variant-annotation`** | 📋 | `variant-annotation` | **Tier 1** | `annotation_correct` / `consequence_wrong` / `clinvar_stale` / `frequency_mislabeled` | Distinct from `vcf-annotator` stub. VEP consequence, ClinVar significance, gnomAD AF, prioritization. |
| P1-3 | **`clinpgx`** | 📋 | `clinpgx` | **Tier 1** | `annotation_correct` / `annotation_stale` / `annotation_missing` / `level_miscategorized` | Overlaps pharmgx — can reuse phenotype-matching infrastructure. Ground truth: pinned PharmGKB/CPIC for CYP2C19, CYP2D6, DPYD, TPMT, HLA-B. |
| P1-4 | **`gwas-prs`** | 📋 | `gwas-prs` | **Tier 1** | `prs_correct` / `prs_sign_flipped` / `prs_strand_flipped` / `prs_uncalibrated` / `prs_ancestry_mismatch` / `prs_coverage_inflated` | PRS miscalibration is a documented clinical harm vector. Strand flips and ancestry transferability are highest-priority sub-tests. |
| P1-5 | **`clinical-trial-finder`** | 📋 | `clinical-trial-finder` | **Tier 1** | `trial_enumeration_correct` / `fhir_schema_invalid` / `eligibility_parse_wrong` / `euctr_ctgov_dedup_wrong` | FHIR R4 eligibility matching output. |
| P1-6 | **`target-validation-scorer`** | 📋 | `target-validation-scorer` | **Tier 2** | `score_reproducible` / `evidence_hallucinated` / `threshold_undocumented` | GO/NO-GO decision reproducibility, citation hallucination detection. |
| P1-7 | **`genome-compare`** | 📋 | `genome-compare` | **Tier 2** | `diff_correct` / `diff_missed_variant` / `diff_spurious` / `coord_normalization_failed` | Deterministic two-VCF pairs with known intersection/union/symmetric-difference. |
| P1-8 | **`methylation-clock`** | 📋 | `methylation-clock` | **Tier 2** | `clock_reproducible` / `missing_feature_silent` / `tissue_specificity_disclosed` / `batch_effect_disclosed` | PyAging clocks (GrimAge, DunedinPACE, Horvath, AltumAge) make disease-linked aging claims. |
| P1-9 | **`wes-clinical-report`** | 💡 | `wes-clinical-report-en`, `wes-clinical-report-es` | **Tier 1** | TBD — likely mirrors CVR Phase 1 structural checks plus multi-language disclaimer verification. | **New skill added to ClawBio 2026-04-05.** Generates clinical PDF reports from WES data. HIGH clinical harm — needs scoping. |

### P1 Existing harness expansion

| # | Item | Status | Harness | Notes |
|---|------|:------:|---------|-------|
| P1-E1 | CYP2D6 CNV / hybrid / *5 deletion tests | ✅ | pharmgx | 3 tests shipped v0.1.0. Reclassified to `scope_honest_indeterminate` in v0.1.2. |
| P1-E2 | NUDT15, G6PD, CYP2B6, MT-RNR1 tests | ✅ | pharmgx | 4 tests shipped v0.1.0. |
| P1-E3 | Prompt-injection regression pins | ✅ | orchestrator | 3 pins + 1 genuine LLM-path test (`inj_03_flock_routing_hijack`). |
| P1-E4 | `scope_honest_indeterminate` category | ✅ | pharmgx | Split from `disclosure_failure` in v0.1.2. 5 cases reclassified. |
| P1-E5 | Prompt injection with behavioral scoring | 📋 | cross-harness | Routing hijack, output tampering, exfil canary (`CLAWBIO_BENCH_CANARY` env var), VCF INFO/sample-name injection. Scored on *behavioral change*, not string presence. |
| P1-E6 | PHI persistence sentinels | 📋 | cross-harness | Injected fake MRN/DOB/name in inputs; assert none appear verbatim in stdout/stderr/report AND in benchmark's own `results/` artifacts. |
| P1-E7 | Diplotype → activity score → phenotype chain | 📋 | pharmgx | Validate intermediate chain against PharmVar, catch tools that get the right phenotype via the wrong haplotype call. |
| P1-E8 | Re-verify ClawBio pass rate post-remediation | 📋 | all | ClawBio shipped fixes for P0/P1 items from their REMEDIATION-PLAN.md. Need a clean re-run to establish new baseline. Last known: 65% (regression from 80%). |

---

## P2 — Population / research integrity

| # | Harness | Status | ClawBio skill | Planned rubric | Notes |
|---|---------|:------:|---------------|----------------|-------|
| P2-1 | **`claw-ancestry-pca`** | 📋 | `claw-ancestry-pca` | `eigenvectors_stable` / `projection_correct` / `outlier_flagged` / `singular_crash` | PCA stability, 1000G/SGDP projection, outlier handling. Parallels equity harness. |
| P2-2 | **`gwas-lookup`** | 📋 | `gwas-lookup` | `lookup_correct` / `lookup_stale` / `trait_mismatched` / `pvalue_extraction_wrong` | Pinned GWAS Catalog snapshot as ground truth. |
| P2-3 | **`scrna-orchestrator`** | 📋 | `scrna-orchestrator` | `qc_bounded` / `qc_unbounded` / `doublet_disclosed` / `doublet_silent` / `routing_correct` | Major contrast API refactor (PR #78) landed — may affect rubric design. |
| P2-4 | **`rnaseq-de`** | 💡 | `rnaseq-de` | TBD — DE result correctness, normalization method labeling, batch correction disclosure. | Bulk RNA-seq skill has been stable. |
| P2-5 | **`proteomics-de`** | 💡 | `proteomics-de` | TBD — LFQ correctness, missing value imputation disclosure. | |
| P2-6 | FST variance-aware Z-score | 📋 | (equity harness) | Replace hardcoded `FST_TOLERANCE` absolute-value with estimator-SE-based Z-score. | README limitation: false failures on small-*n* studies. |
| P2-7 | Hallucinated citation detection | 📋 | LLM-mediated skills | Cross-check every cited DOI/NCT against a cached corpus. | Applies to `pubmed-summariser`, `clinical-trial-finder`, `target-validation-scorer`. |
| P2-8 | PRS strand-flip / ancestry transferability | 📋 | (gwas-prs harness) | Test EUR-derived score applied to AFR cohort without transferability warning. | Extends P1-4 once the base harness exists. |

---

## P3 — Workflow / data handling

| # | Harness | Status | ClawBio skill | Notes |
|---|---------|:------:|---------------|-------|
| P3-1 | **`data-extractor`** | 💡 | `data-extractor` | Structured field extraction from semi-structured inputs. `extraction_correct` / `field_missing` / `schema_violation` / `hallucinated_field`. |
| P3-2 | **`ukb-navigator`** | 💡 | `ukb-navigator` | UK Biobank field mapping + phenotype coding. `mapping_correct` / `phenotype_miscoded` / `field_deprecated_not_flagged`. |
| P3-3 | **`profile-report`** | 💡 | `profile-report` | Report completeness, mandatory disclaimers, sensitive field leakage. Aggregates clinical content from other skills — HIGH potential harm if disclaimers missing. |
| P3-4 | **`galaxy-bridge`** | 💡 | `galaxy-bridge` | Galaxy tool recommendation, XML wrapper generation validity. `wrapper_valid` / `wrapper_malformed` / `tool_mismatched`. |
| P3-5 | **`illumina-bridge`** | 💡 | `illumina-bridge` | DRAGEN bundle import correctness. Clinical lab data pathway — potential harm if ICA metadata misinterpreted. Merged 2026-03-18. |
| P3-6 | **`struct-predictor`** | 💡 | `struct-predictor` | Boltz-2 protein structure prediction. LOW clinical harm. New skill (merged 2026-04-04). |
| P3-7 | **`cell-detection`** | 💡 | `cell-detection` | Cell segmentation in microscopy images. LOW tier. New skill (merged 2026-04-04). Open PR #108 for threshold params. |

### Skills watchlist (not yet scoped)

These ClawBio skills exist but are not yet prioritized for dedicated
harnesses. Re-evaluate as the skills mature or clinical claims change.

| Skill | Notes |
|-------|-------|
| `omics-target-evidence-mapper` | Target-level evidence aggregation. Tier 2. |
| `scrna-embedding` | scVI/scANVI latent embeddings. Tier 3. |
| `bioconductor-bridge` | R ecosystem bridge. Tier 3. |
| `diff-visualizer` | DE result visualization. Tier 3. |
| `pubmed-summariser` | Literature search/summary. Tier 3. LLM hallucination risk. |
| `protocols-io` | Protocol search. Tier 3. |
| `labstep` | Labstep ELN API bridge. Tier 3. |
| `bigquery-public` | Read-only BigQuery datasets. Tier 3. New (2026-04-04). |
| `genome-match` | Genetic compatibility scoring. Tier 2 (eugenics-adjacent). |
| `recombinator` | Simulated meiotic recombination. Tier 2 (synthetic genomes). |
| `soul2dna` | Character profiles to synthetic genomes. Tier 2 (pseudoscience risk). |
| `de-summary` | Alpha diversity metrics for metagenomics. New (2026-04-06). May extend existing metagenomics harness. |

### Incoming skills (open PRs on ClawBio)

| PR | Skill | Author | Notes |
|----|-------|--------|-------|
| #110 | `ncbi-datasets` | nullvoid42 | NCBI Datasets API bridge. |
| #96 | `affinity-proteomics` | RezaJF | Affinity proteomics pipeline. |
| #92 | `gwas-pipeline` | RezaJF | Full GWAS pipeline. |
| #76 | `flow` | alexharston | flow.bio API bridge. |

### Proposed new skills (open issues on ClawBio)

| Issue | Skill | Notes |
|-------|-------|-------|
| #109 | Mendelian Randomisation pipeline | Future skill — potential harness. |
| #13 | `claw-semantic-sim` | Stub — seeking implementation. |
| #11 | `repro-enforcer` | Stub — seeking implementation. |
| #10 | `seq-wrangler` | Stub — seeking implementation. |
| #7 | `lit-synthesizer` | Stub — seeking implementation. |
| #6 | `vcf-annotator` | Stub — partially fulfilled by `variant-annotation`. |

---

## Framework and tooling

| # | Item | Status | Notes |
|---|------|:------:|-------|
| F-1 | **YAML-only ground truth migration** | 📋 | Plan at `docs/plans/YAML_MIGRATION_PLAN.md`. Two-commit approach: (1) parser fixes (backward-compat), (2) convert 140 files + remove legacy parser. |
| F-2 | **Shared AST security utilities** | 📋 | Extract metagenomics AST-based `shell=True` / aliased-import detection into `core.py` as reusable `ast_security_sweep()`. Run across all skills, not just metagenomics. |
| F-3 | **Entry-point harness discovery** | 📋 | Plugin architecture via `[project.entry-points]` so third-party harnesses can register without modifying `HARNESS_REGISTRY`. |
| F-4 | **Parallel test execution (`--jobs`)** | 📋 | `-j N` flag for concurrent test case execution within a commit. |
| F-5 | **Config file support** | 💡 | YAML / TOML for complex benchmark configurations. |
| F-6 | **Benchmark diff tool** | 📋 | Compare two longitudinal runs and surface new/resolved findings with category-level remediation context. |
| F-7 | **Cross-harness Tier-1 safety gate** | 📋 | `--tier1-only` mode: run only CPIC Tier 1 clinical safety tests (CYP2D6, DPYD, CYP2C19+clopidogrel, HLA-B*57:01, SLCO1B1) across all harnesses for fast CI gating. |
| F-8 | **Independent review pipeline** | 💡 | `make review` target for external sanity-checking of new harnesses before landing. |
| F-9 | **`--tagged-commits` mode** | ✅ | Shipped in v0.1.2. Run benchmarks against tagged commits only. |
| F-10 | **Heatmap hierarchy + per-harness sub-heatmaps** | ✅ | Shipped in v0.1.2. |
| F-11 | **5-tier severity system** | ✅ | Shipped in v0.1.2. Pass / Advisory / Warning / Critical / Infra. |
| F-12 | **Delta comparison in Typst report** | ✅ | Shipped in v0.1.2. `--baseline` flag on `generate_report.py`. |
| F-13 | **Bento-grid executive dashboard** | ✅ | Shipped in v0.1.2. 6-cell instrument panel on page 2 of PDF. |

---

## Audit-framework failure-class coverage

From the gap analysis (`docs/plans/GAP_ANALYSIS_2026-04-04.md`), mapped
to the `bioinfo-audit-skill` v4 failure classes. This table tracks which
classes have test coverage and which remain gaps.

### Variant analysis classes (A-*)

| Class | Definition | Coverage | Status |
|-------|------------|----------|:------:|
| A-CNV | CNV/copy-number non-detection | pharmgx: CYP2D6 xN, *5, hybrid. Scored as `scope_honest_indeterminate`. | ✅ |
| A-INDEL | Indel handling | None explicit | 📋 |
| A-STR | Short tandem repeats | UGT1A1 TA repeat (warning check only) | 📋 |
| A-HOMOLOG | Homolog/pseudogene confusion | CYP2D6 *13 hybrid test | ✅ |
| A-PHASE | Phase ambiguity | `cyp2d6_phase_ambiguous`, `tpmt_compound_het` | ✅ |
| A-NORM | Variant normalization | None | 📋 |
| A-REF | Reference genome mismatch | `grch37_reference_mismatch` (1 case) | 🚧 |
| A-COVG | Coverage/missingness | `dpyd_absent`, `dpyd_partial`, `ng_10_low_coverage` | 🚧 |
| A-SV | Structural variants | None | 📋 |

### Knowledge-base classes (B-*)

| Class | Definition | Coverage | Status |
|-------|------------|----------|:------:|
| B-SOURCE | Wrong guideline cited | None | 📋 |
| B-STALE | Out-of-date database | None | 📋 |
| B-PHENOTYPE | Activity-score-to-phenotype lookup | Phenotype string match only | 📋 |

### Honesty classes (C-*)

| Class | Definition | Coverage | Status |
|-------|------------|----------|:------:|
| C-MISLABEL | Method mislabeling | `fst_mislabeled` (eq_01/02) | ✅ |
| C-REIFY | Concept reification | `heim_unbounded` (eq_09) | ✅ |
| C-NOVEL | False claim of novelty | None | 💡 |

### Security classes (D-*)

| Class | Definition | Coverage | Status |
|-------|------------|----------|:------:|
| D-INJECT | Prompt injection | Path traversal (err_04), 3 regression pins, 1 LLM-path test | 🚧 |
| D-SUBPROC | shell=True / exec | Metagenomics AST scan only | 🚧 |
| D-SANDBOX | Sandbox escape | None | 📋 |
| D-SUPPLY | Supply chain | None | 💡 |
| D-PARAM | CLI parameter injection | Metagenomics dash-prefix only | 📋 |
| D-LEAK | Credential/PHI leakage | PHI sentinel (1 test, regression pin only) | 📋 |

### Regulatory classes (E-*)

| Class | Definition | Coverage | Status |
|-------|------------|----------|:------:|
| E-RUO | Research-Use-Only missing | pharmgx disclaimer check | 🚧 |
| E-VALIDATION | Unvalidated clinical claim | None | 📋 |
| E-REPORTING | ACMG lab reporting standard | CVR Phase 1 (5 tests) | ✅ |
| E-PRIVACY | PHI handling | None (beyond PHI sentinel regression pin) | 📋 |

### Functional classes (F-*)

| Class | Definition | Coverage | Status |
|-------|------------|----------|:------:|
| F-ROUTE | Wrong skill routing | Orchestrator (54 tests) | ✅ |
| F-FORMAT | Format/content mismatch | Extension-based routing (15 tests) | ✅ |
| F-COLLISION | Keyword collision | `kw_14_variant` | 🚧 |
| F-CONF | Confidence inflation | None | 📋 |
| F-COMP | Multi-skill composition | 2 composition tests | ✅ |
| F-TYPE | Type coercion | `eq_13_csv_honest` | ✅ |
| F-TIMEOUT | Timeout suppression | `validate_timeout` helper exists | 🚧 |

---

## ClawBio skill inventory

Complete inventory of ClawBio skills (47 directories at HEAD
`5cf83c5`+, as of 2026-04-06) and their coverage status in
clawbio-bench.

### Skills with dedicated behavioral harnesses (7)

| Skill | Harness | Tests | Pass rate (last known) |
|-------|---------|:-----:|:---------------------:|
| `bio-orchestrator` | orchestrator | 54 | 75.9% |
| `pharmgx-reporter` | pharmgx | 33 | 42.4% |
| `equity-scorer` | equity | 15 | 20.0%* |
| `nutrigx_advisor` | nutrigx | 10 | 80.0% |
| `claw-metagenomics` | metagenomics | 7 | 85.7% |
| `clinical-variant-reporter` | cvr (Phase 1) | 5 | TBD |
| `fine-mapping` | finemapping | 16 | 25.0%* |

*\* Pass rates reflect pre-remediation ClawBio HEAD. ClawBio has shipped
fixes for equity-scorer (FST relabeling, HEIM, edge cases) and
fine-mapping (SuSiE null component, purity, PIP formula, input
validation). Re-verification needed.*

### Skills routing-tested only (23 via orchestrator)

Auto-detected (17): `claw-ancestry-pca`, `clinpgx`, `data-extractor`,
`diff-visualizer`, `equity-scorer`, `galaxy-bridge`, `genome-compare`,
`gwas-lookup`, `gwas-prs`, `illumina-bridge`, `bioconductor-bridge`,
`methylation-clock`, `profile-report`, `rnaseq-de`,
`scrna-orchestrator`, `scrna-embedding`, `ukb-navigator`.

Force-routed via `--skill NAME` (5): `clinical-variant-reporter`,
`variant-annotation`, `clinical-trial-finder`,
`target-validation-scorer`, `methylation-clock`.

Stub-warning checked (6): `vcf-annotator`, `lit-synthesizer`,
`repro-enforcer`, `claw-semantic-sim`, `drug-photo`, `seq-wrangler`.

### Skills with no coverage (11 executable)

| Skill | Clinical-harm tier | Notes |
|-------|--------------------|-------|
| `wes-clinical-report-en` | **Tier 1** | NEW (2026-04-05). Clinical PDFs from WES data. |
| `wes-clinical-report-es` | **Tier 1** | NEW (2026-04-05). Spanish-language variant. |
| `omics-target-evidence-mapper` | Tier 2 | Not auto-detected by orchestrator. |
| `proteomics-de` | Tier 2 | Not auto-detected. |
| `recombinator` | Tier 2 | Synthetic genomes — privacy/consent review pending. |
| `soul2dna` | Tier 2 | Pseudoscience risk. |
| `genome-match` | Tier 2 | Eugenics-adjacent concerns. |
| `pubmed-summariser` | Tier 3 | LLM hallucination risk for citations. |
| `bigquery-public` | Tier 3 | NEW (2026-04-04). Read-only data access. |
| `cell-detection` | Tier 3 | NEW (2026-04-04). Microscopy image analysis. |
| `struct-predictor` | Tier 3 | NEW (2026-04-04). Boltz-2 protein structure. |

### Stub skills (5, no Python implementation)

`claw-semantic-sim`, `drug-photo`, `lit-synthesizer`, `repro-enforcer`,
`vcf-annotator`.

*Note: `seq-wrangler`, `struct-predictor`, and `labstep` were previously
classified as stubs by the orchestrator harness but have Python
implementations in ClawBio as of 5cf83c5. The stale classification was
fixed in v0.1.0 (P0-1).*

---

## ClawBio collaboration context

- **Issue biostochastics/clawbio_bench#5**: Manuel Corpas (ClawBio lead)
  opened a coordination issue. Contributor access granted. Ongoing
  discussion on: per-test PharmGx breakdown, `disclosure_failure` split
  (resolved in v0.1.2), regression detection, CI integration model.
- **ClawBio issue #106**: Tracks remediation against clawbio_bench audit
  findings. 13 tasks across P0/P1/P2. Most P0/P1 fixes shipped.
- **ClawBio CI**: `scientific-audit` job runs `clawbio-bench --smoke` on
  every PR. Currently **advisory-only** (does not block merge). 14 real
  regressions slipped through unblocked.
- **REMEDIATION-PLAN.md**: Published in ClawBio repo. Target: raise pass
  rate from 57.1% to >90% within 3 weeks. Protocol: red/green TDD
  against bench findings, commit references to finding IDs.

---

## Open questions

1. **Should new ClawBio skills ship with harness scaffold test cases?**
   Manuel proposed this in issue #5. Would accelerate coverage but shifts
   ground-truth authorship to the auditee — needs a validation protocol.

2. **Should ClawBio CI block merges on audit findings?** Currently
   advisory-only. 14 real regressions slipped through. The
   external-auditor-only regression detection model has a demonstrated
   latency gap.

3. **WES clinical report scope**: Two new Tier 1 skills
   (`wes-clinical-report-en/es`) added 2026-04-05. Need to decide
   whether they share the CVR harness (report structure checks) or get a
   separate harness (different input format, multi-language concerns).

4. **SuSiE-inf algorithm path**: ClawBio PR #105 added SuSiE-inf to
   fine-mapping. The existing finemapping harness may need new test cases
   to cover the `-inf` variant.

5. **de-summary**: New metagenomics alpha-diversity skill added
   2026-04-06. Does it extend the existing metagenomics harness or
   warrant a new one?

6. **MTHFR in pharmgx**: ClawBio PR #90 added MTHFR support to
   pharmgx-reporter. Need new test cases with MTHFR ground truth.

---

*This document is maintained alongside the codebase. For the
audit-framework failure-class analysis, see
[`docs/plans/GAP_ANALYSIS_2026-04-04.md`](docs/plans/GAP_ANALYSIS_2026-04-04.md).
For the YAML migration plan, see
[`docs/plans/YAML_MIGRATION_PLAN.md`](docs/plans/YAML_MIGRATION_PLAN.md).*
