# Ground Truth Derivation

This document explains how reference values are computed for each harness. Every test case ground truth must be independently reproducible from the information here.

## Equity Scorer (FST / HEIM)

### FST Ground Truth

**Formula**: Nei's GST (1973)

```
GST = (HT - HS) / HT

where:
  HT = expected heterozygosity of the total population
     = 1 - sum(p_total_i^2)   for alleles i
  HS = average expected heterozygosity within subpopulations
     = mean(1 - sum(p_sub_j_i^2))  for subpopulations j, alleles i
```

**Citation**: Nei, M. (1973). Analysis of gene diversity in subdivided populations. *PNAS*, 70(12), 3321-3323. doi:10.1073/pnas.70.12.3321

### Derivation Examples

**eq_01 (Fixed difference, GST = 1.0)**:
- POP_A: all AA (p_A=1.0, p_T=0.0)
- POP_B: all TT (p_A=0.0, p_T=1.0)
- p_total = (1.0+0.0)/2 = 0.5 for each allele
- HT = 1 - (0.5^2 + 0.5^2) = 0.5
- HS_A = 1 - (1.0^2 + 0.0^2) = 0.0
- HS_B = 1 - (0.0^2 + 1.0^2) = 0.0
- HS = (0.0 + 0.0) / 2 = 0.0
- GST = (0.5 - 0.0) / 0.5 = **1.000**

**eq_03 (Moderate differentiation, GST = 0.111)**:
- POP_A: p_A=0.8, p_T=0.2
- POP_B: p_A=0.4, p_T=0.6
- p_total = (0.8+0.4)/2=0.6, (0.2+0.6)/2=0.4
- HT = 1 - (0.36+0.16) = 0.48
- HS_A = 1 - (0.64+0.04) = 0.32
- HS_B = 1 - (0.16+0.36) = 0.48
- HS = (0.32 + 0.48) / 2 = 0.40
- GST = (0.48 - 0.40) / 0.48 = **0.167** (note: tolerance 0.050 for sampling variance)

### FST Estimator Label

The equity scorer code computes Nei's GST but labels its output "Hudson FST." This is finding C-06 — the test cases verify both the numerical value AND the label.

Ground truth header: `# GROUND_TRUTH_FST_ESTIMATOR: Nei's GST`

### HEIM Score Bounds

The HEIM (Health Equity Index Metric) is a weighted composite of FST, heterozygosity, and other metrics. With default weights, it should always fall in [0, 100]. Test eq_09 verifies that custom weights `--weights 1,1,1,1` can produce unbounded values (finding U-2).

## PharmGx Reporter

### Ground Truth Source

**Authority**: CPIC (Clinical Pharmacogenetics Implementation Consortium) guidelines, accessed via cpicpgx.org.

For each test case:
1. A synthetic 23andMe-format genotype file is constructed with specific SNP values
2. The expected phenotype is determined from CPIC star allele definitions
3. The expected drug recommendations follow CPIC guideline tables

### Key Test Cases

**cyp2d6_pm (CYP2D6 Poor Metabolizer)**:
- Input SNPs: rs3892097 (CYP2D6*4 defining variant) homozygous
- Expected phenotype: Poor Metabolizer
- Expected drug action: codeine, tramadol flagged as "avoid" or "use alternative"
- Citation: CPIC Guideline for CYP2D6 and Codeine Therapy (2012, updated 2019)

**dpyd_2a_het (DPYD*2A Heterozygous)**:
- Input: rs3918290 G>A heterozygous
- Expected: Intermediate Metabolizer for DPYD
- Expected drug action: fluoropyrimidines (5-FU, capecitabine) dose reduction
- Citation: CPIC Guideline for Fluoropyrimidines and DPYD (2017, updated 2023)
- Note: ~0.5% carrier frequency claimed by ClawBio README (to be verified against gnomAD)

**warfarin_missing_both (Warfarin, missing VKORC1 + CYP2C9)**:
- Input: no VKORC1 rs9923231 or CYP2C9 rs1799853/rs1057910
- Expected: warfarin should appear in report as "indeterminate" (missing key SNPs)
- Finding: tool omits warfarin entirely from report (category: `omission`)
- Citation: CPIC Guideline for Pharmacogenetics-Guided Warfarin Dosing (2017)

### Indel / CNV Limitations

The PharmGx reporter uses `alt_count = genotype.count(alt_allele)` which cannot detect:
- **Deletions**: rs5030655 (CYP2D6*6, single T deletion) — `.count("del")` returns 0
- **Insertions**: rs41303343 (CYP2D6*15, single T insertion) — `.count("insT")` returns 0
- **TA repeats**: rs8175347 (UGT1A1*28, TA7 vs TA6) — `.count("TA7")` returns 0
- **Copy number**: CYP2D6 gene duplications/deletions — not addressable from DTC genotype data

These are tested as `disclosure_failure` or `incorrect_determinate` depending on whether the tool warns about the limitation.

## NutriGx Advisor

### Score Computation

Scores are computed as weighted sums of individual SNP risk factors:

```
total_score = sum(weight_i * risk_score_i) for each SNP
category = "Low Risk" if total_score < threshold_low
         = "Moderate Risk" if total_score < threshold_high
         = "High Risk" otherwise
```

Weights and thresholds were verified against `data/snp_panel.json`:
- Weight range: 0.40 to 0.85 (nutrition-specific calibration)
- Threshold boundaries documented in each test case

### Allele Mismatch Bug (NEW finding)

Test ng_09 exposes an allele mismatch where homozygous-reference genotypes are scored as "Unknown" instead of the expected risk level. The bug is in the allele lookup logic where the reference allele is not recognized as a valid genotype.

## Orchestrator

### Ground Truth Source

The orchestrator's own `EXTENSION_MAP` and `KEYWORD_MAP` (verified at commit 3c9383b) define correct routing. Test cases verify:

1. **Extension routing**: `.vcf` -> equity-scorer, `.csv` -> equity-scorer, `.fastq` -> (stub), etc.
2. **Keyword routing**: "Analyse diversity" -> equity-scorer, "Annotate variants" -> (stub), etc.
3. **Error handling**: unknown extensions, path traversal attempts, conflicting keyword+extension

Ground truth is the tool's own documented behavior — we verify the tool does what it claims, and flag where it doesn't (e.g., `.vcf.gz` routes to equity-scorer, but equity-scorer can't read gzip files).

## Metagenomics Profiler

### Security Ground Truth

**Authority**: OWASP Command Injection Prevention Cheat Sheet (2024)

Test cases verify:
1. No `shell=True` in subprocess calls (AST-verified, not grep-based)
2. First argument to subprocess calls is a list, not a string
3. `shlex.quote` used for user-influenced path arguments
4. Generated `commands.sh` does not contain unescaped path variables
5. Non-zero exit codes from subprocesses are treated as errors (not suppressed)

### Demo Mode Verification

Demo mode produces synthetic outputs without executing real bioinformatics tools (Kraken2, Bracken, RGI, HUMAnN3). Tests verify that demo mode:
1. Completes without error
2. Produces report.md and tables/
3. Does not make subprocess calls to real tools
