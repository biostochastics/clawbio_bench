# Clinical Variant Reporter Benchmark — Phase 2 PRD

**Document**: Product Requirements Document & Roadmap
**Version**: 0.1.0-draft
**Date**: 2026-04-07
**Author**: clawbio_bench team
**Status**: Draft — pending review

---

## 1. Executive Summary

Phase 1 of the Clinical Variant Reporter (CVR) benchmark harness validates
**structural completeness and traceability** of ACMG reports — assembly
disclosure, disclaimers, evidence trails, data source versioning. It is live
with 10 rubric categories and 5 test cases.

Phase 2 extends the harness to validate **correctness** — whether the tool
applies ACMG/AMP criteria correctly, respects ClinGen SVI refinements,
honors VCEP supersession rules, and produces valid variant representations.

This document is grounded in a triple-verified standards landscape (April
2026) using Exa, Tavily, and PubMed primary sources, with architectural
review by GPT-5.2-pro.

---

## 2. Verified Standards Ground Truth

Every citation below was independently verified against PubMed, PMC, DOI
resolver, and/or publisher pages across three separate research passes.
Corrections from initial claims are noted.

### 2.1 Core Classification Framework

| Standard | Citation | Verified IDs |
|----------|----------|-------------|
| ACMG/AMP 2015 (28 criteria, 5-tier) | Richards S et al. Genet Med 17(5):405-424, 2015 | PMID 25741868, PMC4544753, doi:10.1038/gim.2015.30 |
| ClinGen SVI PVS1 decision tree | Abou Tayoun AN et al. **Hum Mutat** 39(11):1517-1524, 2018 | PMID 30192042, PMC6185798, doi:10.1002/humu.23626 |
| ClinGen SVI PM2 v1.0 (absent OR extremely low) | ClinGen SVI Recommendation, approved 2020-09-04 | clinicalgenome.org/docs/pm2-recommendation-for-absence-rarity |
| ClinGen SVI PP3/BP4 calibration | Pejaver V et al. Am J Hum Genet 109(12):2163-2177, 2022 | PMID 36413997, PMC9748256, doi:10.1016/j.ajhg.2022.10.013 |

**Key verification corrections**:
- Abou Tayoun 2018 journal is **Human Mutation**, not Genetics in Medicine
- PP3/BP4 per Pejaver 2022: a calibrated single tool can reach
  Supporting/Moderate/**Strong** level — NOT "always supporting only."
  Constraint: must use ONE computational tool per variant, not multiple.
  REVEL thresholds: 0.5-0.75 = Supporting, 0.75-0.9 = Moderate, >0.9 = Strong
- 28 criteria breakdown confirmed: PVS1(1) + PS1-4(4) + PM1-6(6) + PP1-5(5)
  = 16 pathogenic; BA1(1) + BS1-4(4) + BP1-7(7) = 12 benign
- No official revision to the 28 criteria codes. ClinGen SVI issues
  per-criterion refinements, not a replacement framework.

### 2.2 Gene-Disease Validity

| Standard | Citation | Verified IDs |
|----------|----------|-------------|
| ClinGen GDV SOP v11 | ClinGen, September 2024 | clinicalgenome.org/docs/gene-disease-validity-standard-operating-procedures-version-11 |

**Correction**: **7 tiers**, not 6 — "No Known Disease Relationship" is the
seventh classification. Full list: Definitive, Strong, Moderate, Limited,
Disputed, Refuted, No Known Disease Relationship.

### 2.3 Reporting Standards

| Standard | Citation | Verified IDs |
|----------|----------|-------------|
| ACMG NGS technical standard (current) | Rehder SA et al. Genet Med 2021 | PMID 33927380, doi:10.1038/s41436-021-01139-4 |
| ACMG NGS standard (historical) | Rehm HL et al. Genet Med 15(9):733-747, 2013 | PMID 23722852, doi:10.1038/gim.2013.92 |
| AMP/ASCO/CAP somatic (Tier I-IV) | Li MM et al. J Mol Diagn 19(1):4-23, 2017 | PMID 27993330 |

**Critical correction**: Li et al. 2017 covers **somatic/cancer variants**
(Tier I-IV system). The germline standard is Richards et al. 2015. Any tool
claiming "Li et al. 2017 germline variant reporting" has a factual error.

**Rehder 2021 supersedes Rehm 2013** for NGS technical standards. Phase 1
currently cites REHM_2013 — this should be updated to reference both.

### 2.4 Secondary Findings

| Version | Year | Gene Count | Citation | Verified IDs |
|---------|------|------------|----------|-------------|
| SF v3.0 | 2021 | 73 | Miller DT et al. Genet Med 23:1381-1390 | PMID 34012068 |
| SF v3.1 | 2022 | 73 | Miller DT et al. Genet Med 24:1407-1414 | PMID 35802134 |
| SF v3.2 | 2023 | 81 | Miller DT et al. Genet Med 25:100866 | PMID 37347242, PMC10524344 |
| SF v3.3 | 2025 | **84** | **Lee K** et al. Genet Med 27:101454 | PMID 40568962, PMC12318660 |

**Corrections**: SF v3.3 first author is **Kristy Lee** (Miller is last/
senior). Phase 1 harness references `MILLER_2023` for SF v3.2 — Phase 2
must update to SF v3.3 (84 genes).

### 2.5 HGVS Nomenclature

| Standard | Citation | Verified IDs |
|----------|----------|-------------|
| HGVS 2024 update | Hart RK et al. Genome Med 16:149, 2024 | PMID 39702242, PMC11660784, doi:10.1186/s13073-024-01421-5 |
| HGVS specification | hgvs-nomenclature.org | Verified v21.1.0 (v21.1.3 not confirmed) |
| HGVS tool discordance | Hsu et al. Hum Genomics 19:70, 2025 | PMID 40542397, PMC12181866, doi:10.1186/s40246-025-00778-x |

MANE Select is now the preferred transcript reference. LRG recommendation
formally withdrawn in HGVS v21.1 (December 2024).

### 2.6 GA4GH Standards

| Standard | Version | Approval Date | Status |
|----------|---------|--------------|--------|
| VRS | v2.0 | 2025-03-27 | Approved — emerging standard |
| Cat-VRS | v1.0 | 2025-06-12 | Approved — categorical variants |
| VA-Spec | v1.0.1 | 2025-06-12 | Approved — variant annotation model |
| Phenopackets | v2.0 | 2022-02-02 | Approved — stable |

### 2.7 Pharmacogenomics

| Standard | Citation | Verified IDs |
|----------|----------|-------------|
| CPIC allele function SOP | Tibben BM et al. Am J Hum Genet 112(12):2842-2859, 2025 | PMID 41175864 |
| PharmVar star allele dynamics | van der Maas S et al. Front Pharmacol, 2025 | doi:10.3389/fphar.2025.1584658, PMID 40487404 |
| Star allele nomenclature contradictions | Ahn SH, Kim JH. Genes 15(4):521, 2024 | PMC11050392, doi:10.3390/genes15040521 |
| PharmGKB clinical annotation levels | Whirl-Carrillo M et al. Clin Pharmacol Ther 110(3):563-572, 2021 | PMC8457105, doi:10.1002/cpt.2350 |
| ACMG MTHFR guidance | Hickey SE et al. Genet Med 15(2):153-156, 2013 | PMID 23288205 |
| ACMG MTHFR addendum | Bashford MT et al. Genet Med 22(9):1570, 2020 | PMID 32533132 |
| CYP2D6 structural variation tutorial | Gaedigk A et al. Clin Pharmacol Ther 114(6):1220-1237, 2023 | PMC10840842, doi:10.1002/cpt.3044 |

**Verified allele function tiers**: No Function, Decreased Function, Normal
Function, Increased Function, Uncertain Function, Unknown Function (6 tiers).

**CYP2D6 activity scores** (verified from NCBI NBK574601):
- *1, *2, *35 = 1.0 (Normal Function)
- *9, *17, *29, *41 = 0.5 (Decreased Function)
- *10 = **0.25** (Decreased Function — NOT 0.5)
- *3, *4, *5, *6, *40 = 0 (No Function)

**MTHFR**: No CPIC guideline exists. ACMG explicitly advises against routine
testing (2013 + 2020 addendum).

**FDA PGx biomarkers**: 676 drug-biomarker entries (as of 2026-03-03) — this
is row count, not unique drugs (~300-350 unique drugs estimated).

### 2.8 Regulatory

| Standard | Key Finding |
|----------|-------------|
| FDA CDS guidance (Jan 29, 2026) | Names VCF datasets as "patterns" — variant tools likely fall under device regulation unless exempt |
| Rehder 2021 supersedes Rehm 2013 | Current ACMG NGS technical standard |
| CAP checklist | Requires pipeline version traceability per patient result |

### 2.9 Benchmark Papers

| Paper | Citation | Verified IDs | Key Finding |
|-------|----------|-------------|-------------|
| Automated ACMG tool evaluation | Ghasemnejad T et al. Bioinformatics 42(2):btaf623, 2026 | PMC12916173, doi:10.1093/bioinformatics/btaf623 | Substantial inter-tool discordance against curated gold standards |
| BIAS-2015 vs FDA eRepo | Eisenhart C et al. Genome Med 17:148, 2025 | PMID 41382245, PMC12706976, doi:10.1186/s13073-025-01581-y | First benchmark against FDA-approved eRepo dataset (v2.1.1) |
| HGVS tool discordance | Hsu et al. Hum Genomics, 2025 | PMID 40542397 | Major annotation tools produce discrepant HGVS for same ClinVar variants |

---

## 3. Phase 2 Architecture

### 3.1 Design Principles (from GPT-5.2-pro architectural review)

1. **Score criteria-first, classification-second.** Many tools get the final
   label right for the wrong reasons. Criteria-level scoring reveals *what
   kind of safety failure* occurred.

2. **Fail closed on unverifiable reasoning.** If the tool can't emit
   machine-parseable criteria + strengths + evidence pointers, Phase 2 emits
   `criteria_not_machine_parseable` rather than guessing correctness.

3. **Gold/Silver truth tiers.** Gold = VCEP/EP-curated with criterion-level
   expectations. Silver = broader datasets (FDA eRepo) scored for consistency
   and obvious failures, not nuanced VUS adjudication.

4. **Versioned, frozen truth sets.** Each truth set gets a manifest with
   source URLs, retrieval date, SHA-256, and standard revision pinning.

5. **Multi-level truth assertions per variant:**
   - `expected_criteria[]` with strength (e.g., `PVS1_Moderate`, `PM2_Supporting`)
   - `expected_classification` (only when unambiguous)
   - `acceptable_alternates[]` (bounded discretion)

### 3.2 Sub-Phase Strategy

Phase 2 is split into three sub-phases, ordered by dependency:

```
Phase 2c (Variant Identity)     -- prerequisite for 2a
  |
  v
Phase 2a (ACMG Correctness)    -- core mission
  |
  v
Phase 2b (PGx Correctness)     -- separate domain
```

**Rationale**: If the tool can't stably represent/normalize variants, any
ACMG correctness claim is fragile. 2c catches "variant identity wrong"
before 2a misattributes it as "ACMG wrong."

---

## 4. Phase 2c — Variant Identity & Representation

**Scope**: Validate variant normalization, HGVS syntax, transcript
selection, and assembly consistency. No classification correctness.

**Target**: ~10-15 test cases.

### 4.1 Rubric Categories

| Category | Severity | Description |
|----------|----------|-------------|
| `variant_identity_correct` | Green | All representation checks pass |
| `hgvs_syntax_error` | Red | HGVS expression violates v21.1 rules (missing version, wrong separator, spaces, incomplete frameshift) |
| `hgvs_semantic_mismatch` | Red | HGVS string doesn't match the variant it claims to describe |
| `transcript_selection_error` | Orange | Non-MANE transcript used when MANE Select available; or wrong transcript for PVS1 |
| `variant_normalization_error` | Orange | Left-alignment/trimming/representation mismatch (dup described as ins, 3' rule violation) |
| `assembly_coordinate_mismatch` | Red | Coordinates inconsistent with stated assembly |
| `liftover_mapping_error` | Orange | Incorrect cross-build coordinate translation |
| `harness_error` | Gray | Infrastructure failure |

### 4.2 Proposed Test Cases

| Test ID | Focus | Input | Checks |
|---------|-------|-------|--------|
| `cvr_10_hgvs_snv` | SNV HGVS syntax | Synthetic VCF with known SNVs | c. notation, p. notation with parentheses for predicted, versioned transcript |
| `cvr_11_hgvs_indel` | Indel representation | Synthetic VCF with indels | Underscore separator (not hyphen), 3' rule, dup priority over ins |
| `cvr_12_hgvs_frameshift` | Frameshift completeness | Synthetic VCF with frameshifts | Full `p.Arg456GlyfsTer17` not truncated `p.Arg456fs` |
| `cvr_13_mane_select` | Transcript selection | Known genes with MANE Select | MANE Select transcript used; NM_ versioned |
| `cvr_14_assembly_coords` | Coordinate consistency | Multi-variant VCF on GRCh38 | All coordinates match GRCh38; no hg19 coordinates leaked |
| `cvr_15_normalization` | Left-alignment | Ambiguous indels in repetitive regions | Correct normalized representation per VCF spec |

### 4.3 Ground Truth References

- HGVS v21.1 specification (hgvs-nomenclature.org)
- Hart et al. 2024 (PMID 39702242) — HGVS 2024 improvements
- Hsu et al. 2025 (PMID 40542397) — annotation tool discordance
- MANE Select project (NCBI/EBI)
- GA4GH VRS v2.0 (future: variant identity validation)

---

## 5. Phase 2a — ACMG Classification Correctness

**Scope**: Validate ACMG criteria application and final classification
against frozen, unambiguous gold-standard variants.

**Target**: 15-25 test cases (initial); expandable.

### 5.1 Rubric Categories

| Category | Severity | Layer | Description |
|----------|----------|-------|-------------|
| `classification_correct` | Green | Aggregation | All criteria and classification match ground truth |
| `pvs1_strength_error` | Red | Criterion logic | Wrong PVS1 strength tier per SVI decision tree |
| `pvs1_applicability_error` | Red | Criterion logic | PVS1 applied when LoF not the disease mechanism |
| `population_frequency_error` | Red | Evidence extraction | BA1/BS1/PM2 thresholds misapplied |
| `pm2_threshold_misapplied` | Orange | Evidence extraction | "Absent only" instead of "absent OR extremely low" per PM2 v1.0 |
| `in_silico_overcounting` | Red | Criterion logic | Multiple in-silico tools counted as multiple PP3/BP4 evidence units |
| `pp3_bp4_calibration_error` | Orange | Criterion logic | Wrong strength level for calibrated tool score |
| `criterion_double_counting` | Orange | Criterion logic | Correlated evidence counted twice (PS1/PM5 interactions) |
| `clinvar_strength_misuse` | Orange | Evidence extraction | Single-submitter treated as definitive; conflict handling absent |
| `vcep_rules_ignored` | Red | Rule supersession | Generic ACMG applied when gene-specific VCEP exists |
| `vcep_rule_version_mismatch` | Orange | Rule supersession | Correct VCEP used but wrong revision |
| `gene_disease_validity_error` | Orange | Evidence extraction | Variant reported without GDV tier caveat (Limited/Disputed/No Known) |
| `sf_list_outdated` | Orange | Policy compliance | Tool uses SF v3.2 (81 genes) or earlier instead of SF v3.3 (84 genes) |
| `classification_aggregation_error` | Red | Aggregation | Correct criteria listed but wrong 5-tier outcome |
| `criteria_not_machine_parseable` | Gray | Auditability | Tool output cannot be programmatically scored for criterion correctness |
| `harness_error` | Gray | Infrastructure | Harness infrastructure failure |

### 5.2 Test Case Design Strategy (Gold + Silver)

#### Gold Tier — Synthetic Rule-Isolating Cases

Each case freezes ALL evidence inputs and asserts **criterion-level**
expectations (not just final label):

| Test ID | Focus | Frozen Evidence | Expected Criteria | Expected Classification |
|---------|-------|-----------------|-------------------|------------------------|
| `cvr_20_ba1_common_benign` | BA1 stand-alone benign | gnomAD AF=8% variant | BA1 | Benign |
| `cvr_21_pm2_absent` | PM2 absent variant | gnomAD AF=0, no coverage gap | PM2_Supporting | (contributes to combination) |
| `cvr_22_pm2_extremely_low` | PM2 v1.0 update | gnomAD AF=0.00005 (below threshold) | PM2_Supporting | (contributes to combination) |
| `cvr_23_pvs1_lof_definitive` | PVS1 in LoF-intolerant gene | Nonsense in BRCA1 (ClinGen: Definitive LoF) | PVS1 | (contributes to combination) |
| `cvr_24_pvs1_wrong_mechanism` | PVS1 inapplicable | LoF variant in gain-of-function gene | PVS1 must NOT be applied | (absence check) |
| `cvr_25_pvs1_strength_mod` | PVS1 strength modulation | Splice variant, last exon, partial LoF | PVS1_Moderate or PVS1_Supporting | (strength check) |
| `cvr_26_pp3_single_tool` | PP3 single-tool rule | REVEL=0.85 (Moderate), CADD=28 | PP3_Moderate (from ONE tool) | (not PP3 x2) |
| `cvr_27_pp3_overcounting` | PP3 overcounting detection | Multiple in-silico tools all agreeing | Only 1 PP3 unit | (detect if tool counts >1) |
| `cvr_28_classification_combo` | Richards Table 5 combination | PS1 + PM2 | Likely Pathogenic | LP |
| `cvr_29_benign_combo` | Benign combination rules | BS1 + BP1 | Likely Benign | LB |

#### Gold Tier — VCEP Supersession Cases

| Test ID | Focus | Gene/VCEP | Expected Behavior |
|---------|-------|-----------|-------------------|
| `cvr_30_vcep_brca1` | ENIGMA BREP supersession | BRCA1 | Tool must apply ENIGMA-specific PM2 threshold, not generic |
| `cvr_31_vcep_lynch_mlh1` | InSiGHT supersession | MLH1 | Tool must apply Lynch VCEP rules |
| `cvr_32_no_vcep_generic` | Non-VCEP gene uses generic | Gene without VCEP | Generic ACMG rules acceptable |

#### Silver Tier — ClinVar Expert Panel Calibration

Select ~10 variants from ClinVar with **3-star (expert panel)** or
**4-star (practice guideline)** review status, low conflict volatility,
and clear condition + inheritance mapping. Score for:
- Final classification concordance (not criterion-level)
- Gross errors (BA1 miss, PVS1 on GoF gene)
- Mark `truth_stability: "volatile"` for variants with historical reclassification

#### Silver Tier — FDA eRepo Alignment (Future)

Following Eisenhart 2025 (BIAS-2015 v2.1.1), incorporate FDA eRepo
variants as a broader realism set. Score for consistency and obvious
failures, not nuanced VUS adjudication.

### 5.3 Truth Set Architecture

Each truth set is a versioned, frozen snapshot:

```
truth_sets/
  cvr_phase2a_v1/
    manifest.json          # source URLs, retrieval date, SHA-256, standard revisions
    variants/
      cvr_20_ba1.yaml      # per-variant truth with criterion-level expectations
      cvr_21_pm2.yaml
      ...
    frozen_evidence/
      gnomad_v4.1_subset.tsv
      clinvar_2026-03-15.tsv
      ...
```

Manifest includes:
```json
{
  "truth_set_id": "cvr_phase2a_v1_2026-04",
  "standards_pinned": {
    "acmg_amp": "Richards_2015",
    "svi_pvs1": "AbouTayoun_2018",
    "svi_pm2": "v1.0_2020-09-04",
    "svi_pp3_bp4": "Pejaver_2022",
    "sf_list": "v3.3_Lee_2025",
    "hgvs": "v21.1",
    "gnomad": "v4.1",
    "clinvar_snapshot": "2026-03-15"
  },
  "retrieval_date": "2026-04-07",
  "sha256_manifest": "..."
}
```

**Drift handling**: Never update in place. New truth set = new version.
Volatile variants move from Gold to Silver. Benchmark correctness is
always relative to a specified standard revision.

---

## 6. Phase 2b — Pharmacogenomics Correctness

**Scope**: Validate PGx-specific reporting: star alleles, diplotypes,
activity scores, metabolizer phenotypes, CPIC/DPWG recommendations.
Separate harness module from germline ACMG.

**Target**: 10-15 test cases.

### 6.1 Rubric Categories

| Category | Severity | Description |
|----------|----------|-------------|
| `pgx_report_correct` | Green | All PGx elements correct |
| `star_allele_error` | Red | Incorrect star allele call |
| `diplotype_error` | Red | Incorrect diplotype assignment |
| `activity_score_error` | Red | Wrong activity score for allele (e.g., *10=0.5 instead of 0.25) |
| `phenotype_prediction_error` | Red | Wrong metabolizer phenotype from diplotype |
| `cpic_recommendation_error` | Orange | Wrong CPIC prescribing recommendation |
| `pharmvar_version_missing` | Orange | PharmVar version not stated |
| `cyp2d6_cnv_not_assessed` | Orange | CYP2D6 CNV/SV not addressed when relevant |
| `mthfr_overclaim` | Red | MTHFR variants reported as clinically actionable (ACMG 2013/2020 violation) |
| `dpwg_discrepancy_unmentioned` | Orange | CPIC/DPWG discrepancy exists but not disclosed |
| `harness_error` | Gray | Infrastructure failure |

### 6.2 Proposed Test Cases

| Test ID | Focus | Input | Expected |
|---------|-------|-------|----------|
| `cvr_40_cyp2d6_pm` | CYP2D6 Poor Metabolizer | *4/*4 diplotype | AS=0, PM phenotype |
| `cvr_41_cyp2d6_im` | CYP2D6 Intermediate (*10) | *1/*10 diplotype | AS=1.25 (1.0+0.25), NM or IM per guideline |
| `cvr_42_cyp2d6_um` | CYP2D6 Ultrarapid | *1/*2x2 diplotype | AS≥3, UM phenotype |
| `cvr_43_cyp2c19_clopidogrel` | CYP2C19 + clopidogrel | *2/*2 diplotype | PM, alternative antiplatelet recommended (CPIC Level A) |
| `cvr_44_dpyd_fluoropyrimidine` | DPYD + capecitabine | *2A carrier | Reduced function, dose reduction per CPIC |
| `cvr_45_mthfr_rejection` | MTHFR not clinically actionable | MTHFR C677T | Must NOT report as clinically actionable per ACMG 2013 |
| `cvr_46_pharmvar_versioning` | PharmVar version disclosed | Any PGx gene | PharmVar version stated in report |
| `cvr_47_cpic_dpwg_divergence` | CPIC/DPWG discrepancy | Gene-drug pair where they differ | Both recommendations mentioned or discrepancy noted |
| `cvr_48_cyp2d6_cnv` | CYP2D6 CNV assessment | Gene deletion (*5) | CNV detection status addressed in report |

### 6.3 Key Thresholds (Verified)

| Gene | Allele | Function | Activity Score |
|------|--------|----------|---------------|
| CYP2D6 | *1, *2, *35 | Normal | 1.0 |
| CYP2D6 | *9, *17, *29, *41 | Decreased | 0.5 |
| CYP2D6 | *10 | Decreased (special) | **0.25** |
| CYP2D6 | *3, *4, *5, *6, *40 | No Function | 0 |
| CYP2D6 | *1xN, *2xN | Increased | value × copy count |

Source: NCBI Bookshelf NBK574601, CPIC allele functionality table.

---

## 7. Phase 1 Updates Required

Before Phase 2, Phase 1 needs these corrections based on the verification
round:

### 7.1 Reference Updates

| Current | Correction | Action |
|---------|------------|--------|
| `REHM_2013` as reporting standard | Rehder 2021 (PMID 33927380) supersedes | Add `REHDER_2021` to `GROUND_TRUTH_REFS`; keep `REHM_2013` as historical |
| `MILLER_2023` for SF v3.2 (81 genes) | SF v3.3 (84 genes, Lee 2025, PMID 40568962) is current | Add `LEE_2025` ref; update test case comments |
| `CLINGEN_SVI_PVS1` journal listed as Genet Med | Correct journal is Human Mutation | Verify docstring accuracy |
| No Pejaver 2022 reference | PP3/BP4 calibration is critical for Phase 2 | Add `PEJAVER_2022` ref |

### 7.2 Structural

- ClinGen GDV is **7 tiers** (add "No Known Disease Relationship")
- Phase 1 `REHM_2013` comments in test cases should note supersession by
  Rehder 2021 without breaking existing tests

---

## 8. Scope Boundaries

### 8.1 Explicitly OUT of Scope

- **Wet-lab validation**: sample handling, capture efficiency, coverage,
  sequencing chemistry
- **Variant calling performance**: alignment, caller sensitivity/specificity,
  recalibration, joint-calling behavior
- **Complex variant types unless the tool claims them**: CNVs, SVs, repeat
  expansions, mosaicism, mtDNA heteroplasmy
- **Phenotype-driven interpretation**: integrating patient phenotype,
  penetrance, expressivity, variable onset
- **Family-based criteria requiring pedigrees** (PS2/PM6, PP1) — unless we
  supply a synthetic pedigree dataset (separate project)
- **Clinical actionability beyond policy**: we check "SF gene flagged" but
  not "counseling text is clinically optimal"
- **Any network-dependent evidence retrieval** (offline-only rule)
- **Somatic variant classification** (Li 2017 Tier I-IV is a different
  framework; out of scope for germline harness)

### 8.2 In Scope (Even If It Feels Clinical)

- Whether the tool **applies published rules correctly** given frozen evidence
- Whether it **overstates certainty** relative to its own evidence trail
- Whether it respects **VCEP supersession** and **policy lists** (SF v3.3)
- Whether **gene-disease validity** caveats are present for Limited/Disputed
  genes

---

## 9. Implementation Roadmap

### Phase 2c (Variant Identity) — Target: v0.2.0

**Prerequisites**: None (independent of 2a/2b)

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Define HGVS validation regex suite (v21.1 rules) | S |
| 2 | Create synthetic VCF fixtures for 10-15 representation cases | M |
| 3 | Implement `analyze_variant_identity()` function | M |
| 4 | Write scoring function `score_variant_identity_verdict()` | M |
| 5 | Create test cases `cvr_10` through `cvr_15` | S |
| 6 | Register as sub-harness or extend existing harness | S |

### Phase 2a (ACMG Correctness) — Target: v0.3.0

**Prerequisites**: Phase 2c complete (variant identity must be validated
before classification correctness)

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Design truth set manifest schema (YAML) | S |
| 2 | Create Gold tier synthetic test variants (BA1, PM2, PVS1, PP3/BP4) with frozen evidence | L |
| 3 | Create VCEP supersession test cases (BRCA1, MLH1) | M |
| 4 | Implement structured criteria extraction from tool output (JSON/TSV sidecar parsing) | L |
| 5 | Implement `score_acmg_correctness_verdict()` with criterion-level comparison | L |
| 6 | Create Silver tier ClinVar EP-derived cases (3-4 star, low volatility) | M |
| 7 | Write test cases `cvr_20` through `cvr_32` | M |
| 8 | Update `GROUND_TRUTH_REFS` (Rehder 2021, Pejaver 2022, Lee 2025 SF v3.3) | S |

### Phase 2b (PGx Correctness) — Target: v0.4.0

**Prerequisites**: Phase 2a complete (shares infrastructure)

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Define PGx-specific rubric categories | S |
| 2 | Create star allele → diplotype → phenotype test fixtures | M |
| 3 | Implement CPIC activity score validation | M |
| 4 | Implement MTHFR overclaim detection | S |
| 5 | Create PharmVar version / CPIC-DPWG divergence checks | M |
| 6 | Write test cases `cvr_40` through `cvr_48` | M |

### Phase 1 Maintenance (Parallel)

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Add `REHDER_2021` and `PEJAVER_2022` to `GROUND_TRUTH_REFS` | S |
| 2 | Add `LEE_2025` (SF v3.3, 84 genes) reference | S |
| 3 | Fix Abou Tayoun journal reference (Hum Mutat, not Genet Med) | S |
| 4 | Update GDV documentation to 7 tiers | S |

---

## 10. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| ClinVar reclassification invalidates Gold tier cases | Medium | High | Versioned truth sets; volatility tracking; never update in place |
| VCEP rule revisions change expected criteria | Medium | Medium | Pin VCEP revision in truth set manifest; update = new truth set version |
| Tool output not machine-parseable for criterion extraction | High | High | `criteria_not_machine_parseable` verdict; require structured sidecar (JSON/TSV) |
| PP3/BP4 calibration thresholds evolve | Low | Medium | Pin Pejaver 2022 thresholds in truth set; document tool-specific calibration |
| SF list expands (v3.4+) | Medium | Low | Truth set manifest pins SF version; new version = new truth set |
| PharmVar star allele redefinition changes diplotype calls | Medium | High | Pin PharmVar version in truth set manifest |
| Synthetic test variants don't capture real-world complexity | Medium | Medium | Silver tier supplements Gold; expand iteratively |

---

## 11. Success Criteria

Phase 2 is complete when:

1. **Phase 2c**: ≥10 variant identity test cases pass on a correctly-
   functioning reference implementation
2. **Phase 2a**: ≥15 ACMG correctness test cases (Gold tier) with criterion-
   level scoring; ≥5 VCEP supersession cases; ≥10 Silver tier ClinVar EP cases
3. **Phase 2b**: ≥8 PGx test cases covering CYP2D6, CYP2C19, DPYD activity
   scores, MTHFR rejection, and PharmVar versioning
4. All truth sets have versioned manifests with SHA-256 chain of custody
5. Phase 1 references updated (Rehder 2021, SF v3.3, Pejaver 2022)
6. No network calls at runtime (offline-only constraint preserved)
7. Backward compatibility: Phase 1 test cases continue to pass unchanged

---

## Appendix A: Corrections Log

Corrections identified during triple-verification that affect existing code
or documentation:

| Item | Prior State | Corrected State | Source |
|------|-------------|-----------------|--------|
| Abou Tayoun 2018 journal | Referenced as "Genet Med" in some docs | **Human Mutation** (Hum Mutat) | PMC6185798 |
| PP3/BP4 strength | "Always supporting only" | Calibrated: Supporting/Moderate/Strong per Pejaver 2022 | PMID 36413997 |
| ClinGen GDV tiers | 6 tiers | **7 tiers** (includes "No Known Disease Relationship") | clinicalgenome.org |
| SF v3.2 gene count | Sometimes cited as 73 | **81 genes** | PMID 37347242 |
| SF v3.3 first author | "Miller et al." | **Lee K** et al. | PMID 40568962 |
| CYP2D6 *10 activity score | Sometimes stated as 0.5 | **0.25** | NCBI NBK574601 |
| HGVS current version | "v21.1.3" | **v21.1.0** confirmed; .3 unverified | hgvs-nomenclature.org |
| Rehm 2013 status | Cited as current NGS standard | **Superseded** by Rehder 2021 (PMID 33927380) | PubMed |
| Li 2017 scope | Sometimes conflated with germline | **Somatic only** (Tier I-IV) | PMID 27993330 |
| CPIC level system | "A/B/C/D = prescribing strength" | Two systems: A/B/C/D = gene-drug prioritization; Strong/Moderate/Optional = prescribing strength | cpicpgx.org |

---

## Appendix B: Key File Paths

| Component | Path |
|-----------|------|
| Phase 1 harness | `src/clawbio_bench/harnesses/clinical_variant_reporter_harness.py` |
| Phase 1 test cases | `src/clawbio_bench/test_cases/clinical_variant_reporter/cvr_01-05/` |
| ClawBio CVR module | `skills/clinical-variant-reporter/clinical_variant_reporter.py` (in ClawBio repo) |
| Harness registry | `src/clawbio_bench/cli.py` → `HARNESS_REGISTRY` |
| Core framework | `src/clawbio_bench/core.py` |
