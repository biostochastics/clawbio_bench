<!-- clawbio-bench-report -->

## clawbio-bench audit

**Status:** FINDINGS · **Commit:** `bb9ffff` · **Mode:** `smoke` · **Version:** `0.1.2` · **Runtime:** 71.2s · **Date:** 2026-04-06

### Summary

| Harness | Status | Pass | Fail | Errors | Rate |
|---|:---:|---:|---:|---:|---:|
| `bio-orchestrator` | ❌ | 50/54 | 4 | 0 | 92.6% |
| `claw-metagenomics` | ✅ | 7/7 | 0 | 0 | 100.0% |
| `clawbio-finemapping` | ❌ | 16/20 | 4 | 0 | 80.0% |
| `clinical-variant-reporter` | ❌ | 4/5 | 1 | 0 | 80.0% |
| `equity-scorer` | ❌ | 11/15 | 4 | 0 | 73.3% |
| `nutrigx-advisor` | ✅ | 10/10 | 0 | 0 | 100.0% |
| `pharmgx-reporter` | ❌ | 27/36 | 9 | 0 | 75.0% |
| **total** | ❌ | **125/147** | **22** | **0** | **85.0%** |

### Category breakdown

**`bio-orchestrator`** category breakdown:

| Category | Count |
|---|---:|
| `routed_correct` | 28 |
| `stub_silent` | 3 |
| `stub_warned` | 9 |
| `unroutable_crash` | 1 |
| `unroutable_handled` | 13 |

**`claw-metagenomics`** category breakdown:

| Category | Count |
|---|---:|
| `demo_functional` | 2 |
| `exit_handled` | 1 |
| `injection_blocked` | 4 |

**`clawbio-finemapping`** category breakdown:

| Category | Count |
|---|---:|
| `edge_handled` | 6 |
| `finemap_correct` | 10 |
| `pip_value_incorrect` | 3 |
| `susie_nonconvergence_suppressed` | 1 |

**`clinical-variant-reporter`** category breakdown:

| Category | Count |
|---|---:|
| `data_source_version_missing` | 1 |
| `report_structure_complete` | 4 |

**`equity-scorer`** category breakdown:

| Category | Count |
|---|---:|
| `csv_honest` | 2 |
| `edge_crash` | 1 |
| `edge_handled` | 4 |
| `fst_incorrect` | 1 |
| `fst_mislabeled` | 2 |
| `heim_bounded` | 5 |

**`nutrigx-advisor`** category breakdown:

| Category | Count |
|---|---:|
| `repro_functional` | 1 |
| `score_correct` | 4 |
| `snp_valid` | 3 |
| `threshold_consistent` | 2 |

**`pharmgx-reporter`** category breakdown:

| Category | Count |
|---|---:|
| `correct_determinate` | 18 |
| `correct_indeterminate` | 4 |
| `disclosure_failure` | 4 |
| `incorrect_determinate` | 5 |
| `scope_honest_indeterminate` | 5 |

### Findings

_No baseline provided — showing all 22 current findings._

<details open><summary>Current findings (22)</summary>

- **`bio-orchestrator` / `ext_09_pdb`** — `stub_silent` — Correctly routed to stub 'struct-predictor' but NO warning that it's a stub (expected_executable=False)
- **`bio-orchestrator` / `ext_10_cif`** — `stub_silent` — Correctly routed to stub 'struct-predictor' but NO warning that it's a stub (expected_executable=False)
- **`bio-orchestrator` / `inj_03_flock_routing_hijack`** — `unroutable_crash` — No skill detected (expected 'equity-scorer')
- **`bio-orchestrator` / `kw_05_alphafold`** — `stub_silent` — Correctly routed to stub 'struct-predictor' but NO warning that it's a stub (expected_executable=False)
- **`clawbio-finemapping` / `fm_12_susie_nonconvergence`** — `susie_nonconvergence_suppressed` — SuSiE returned scored PIPs despite converged=False (n_iter=1)
- **`clawbio-finemapping` / `fm_17_susie_inf_polygenic_background`** — `pip_value_incorrect` — max |PIP - expected| = 0.449007 exceeds tolerance 0.15
- **`clawbio-finemapping` / `fm_18_susie_inf_null_locus`** — `pip_value_incorrect` — max |PIP - expected| = 0.288042 exceeds tolerance 0.15
- **`clawbio-finemapping` / `fm_20_susie_inf_est_tausq_activation`** — `pip_value_incorrect` — max |PIP - expected| = 0.399862 exceeds tolerance 0.25
- **`clinical-variant-reporter` / `cvr_01_demo_structure`** — `data_source_version_missing` — Report does not pin a ClinVar release/version
- **`equity-scorer` / `eq_01_fst_known_af`** — `fst_mislabeled` — FST value 1.0000 correct, but no estimator label was emitted (expected 'Nei's GST')
- **`equity-scorer` / `eq_02_fst_identical`** — `fst_mislabeled` — FST value 0.0000 correct, but no estimator label was emitted (expected 'Nei's GST')
- **`equity-scorer` / `eq_12_haploid_x`** — `edge_crash` — Tool crashed (exit 1) with traceback
- **`equity-scorer` / `eq_15_three_populations`** — `fst_incorrect` — FST 1.0000 outside tolerance of expected 0.5000 (tol=0.02)
- **`pharmgx-reporter` / `cyp2d6_del`** — `disclosure_failure` — Disclosure failure for CYP2D6
- **`pharmgx-reporter` / `cyp3a5_7_ins`** — `disclosure_failure` — Disclosure failure for CYP3A5
- **`pharmgx-reporter` / `grch37_reference_mismatch`** — `incorrect_determinate` — Wrong: Poor Metabolizer (expected: CYP2D6 Indeterminate (reference genome mismatch))
- **`pharmgx-reporter` / `mthfr_677cc_normal`** — `incorrect_determinate` — Wrong: Unknown (unmapped diplotype: 677GG/1298TT) (expected: Normal MTHFR enzyme activity (677CC))
- **`pharmgx-reporter` / `mthfr_677ct_het`** — `incorrect_determinate` — Wrong: Unknown (unmapped diplotype: 677AG/1298TT) (expected: Reduced MTHFR enzyme activity (677CT))
- **`pharmgx-reporter` / `mthfr_677tt_methotrexate`** — `incorrect_determinate` — Wrong: Unknown (unmapped diplotype: 677AA/1298TT) (expected: Strongly reduced MTHFR enzyme activity (677TT))
- **`pharmgx-reporter` / `ugt1a1_28_absent`** — `incorrect_determinate` — Incorrect as expected: Normal Metabolizer
- **`pharmgx-reporter` / `ugt1a1_28_het`** — `disclosure_failure` — Disclosure failure for UGT1A1
- **`pharmgx-reporter` / `ugt1a1_28_hom`** — `disclosure_failure` — Disclosure failure for UGT1A1

</details>

### Per-test breakdown

<details open><summary>Detailed findings (22)</summary>

🟠 **1. `bio-orchestrator` / `ext_09_pdb`** — `stub_silent`
  - **Rationale:** Correctly routed to stub 'struct-predictor' but NO warning that it's a stub (expected_executable=False)
  - **Finding:** T09-ext-pdb stub
  - **Finding category:** `stub_silent`

🟠 **2. `bio-orchestrator` / `ext_10_cif`** — `stub_silent`
  - **Rationale:** Correctly routed to stub 'struct-predictor' but NO warning that it's a stub (expected_executable=False)
  - **Finding:** T09-ext-cif stub
  - **Finding category:** `stub_silent`

🟠 **3. `bio-orchestrator` / `inj_03_flock_routing_hijack`** — `unroutable_crash`
  - **Rationale:** No skill detected (expected 'equity-scorer')
  - **Finding:** inj-03 LLM-path routing hijack (FLock-only, gated on credentials)
  - **Finding category:** `routed_correct`

🟠 **4. `bio-orchestrator` / `kw_05_alphafold`** — `stub_silent`
  - **Rationale:** Correctly routed to stub 'struct-predictor' but NO warning that it's a stub (expected_executable=False)
  - **Finding:** T09-kw-alphafold stub
  - **Finding category:** `stub_silent`

🟠 **5. `clawbio-finemapping` / `fm_12_susie_nonconvergence`** — `susie_nonconvergence_suppressed`
  - **Rationale:** SuSiE returned scored PIPs despite converged=False (n_iter=1)
  - **Finding:** FM-12 SuSiE returns scored PIPs with converged=False
  - **Hazard metric:** run_susie has max_iter=1 explicitly — the IBSS loop exits before the first convergence check can fire (the check is guarded by `iteration &gt; 0`). The returned dict contains converged=False but also ...
  - **Derivation:** 5-variant locus with modest AR(1) LD, L=5, max_iter=1. After one IBSS sweep the alpha rows will have shifted from their uniform initialisation but will not be anywhere near the fixed point. A corre...
  - **Finding category:** `susie_nonconvergence_suppressed`

🟠 **6. `clawbio-finemapping` / `fm_17_susie_inf_polygenic_background`** — `pip_value_incorrect`
  - **Rationale:** max |PIP - expected| = 0.449007 exceeds tolerance 0.15
  - **Finding:** FM-17 SuSiE-inf polygenic background: sparse signal amid small-effect variants
  - **Hazard metric:** SuSiE-inf with est_tausq=True should absorb polygenic background into
  - **Derivation:** 10 variants, n=10000, L=5. Variant 0 is the true causal (z=5.0).
  - **Finding category:** `finemap_correct`

🟠 **7. `clawbio-finemapping` / `fm_18_susie_inf_null_locus`** — `pip_value_incorrect`
  - **Rationale:** max |PIP - expected| = 0.288042 exceeds tolerance 0.15
  - **Finding:** FM-18 SuSiE-inf null locus: tau^2 should collapse to zero via MoM fallback
  - **Hazard metric:** On a null locus (all z near zero), SuSiE-inf's method-of-moments
  - **Derivation:** 5 variants, z=[0.1, -0.05, 0.08, -0.12, 0.03], R=I, n=5000, L=3.
  - **Finding category:** `finemap_correct`

🟠 **8. `clawbio-finemapping` / `fm_20_susie_inf_est_tausq_activation`** — `pip_value_incorrect`
  - **Rationale:** max |PIP - expected| = 0.399862 exceeds tolerance 0.25
  - **Finding:** FM-20 SuSiE-inf activation guard: est_tausq=False should produce standard SuSiE output
  - **Hazard metric:** The critical activation switch for SuSiE-inf is est_tausq. When
  - **Derivation:** Same input as fm_17 (10 variants, polygenic LD structure, z=[5, 2,
  - **Finding category:** `finemap_correct`

🟠 **9. `clinical-variant-reporter` / `cvr_01_demo_structure`** — `data_source_version_missing`
  - **Rationale:** Report does not pin a ClinVar release/version
  - **Finding:** CVR Phase 1 baseline — demo mode must produce a structurally complete report
  - **Hazard metric:** An ACMG classification report missing any of reference build, transcript, data source versions, limitations, disclaimer, evidence trail, or disease context is unreviewable by a clinical auditor and...
  - **Finding category:** `report_structure_complete`

🟠 **10. `equity-scorer` / `eq_01_fst_known_af`** — `fst_mislabeled`
  - **Rationale:** FST value 1.0000 correct, but no estimator label was emitted (expected 'Nei's GST')
  - **Finding:** C-06 FST naming mismatch
  - **Hazard metric:** Report says Hudson FST but computes Nei's GST
  - **Derivation:** 2 pops x 5 samples. POP_A all 1/1 (p=1.0), POP_B all 0/0 (p=0.0).
  - **Finding category:** `fst_mislabeled`

🟠 **11. `equity-scorer` / `eq_02_fst_identical`** — `fst_mislabeled`
  - **Rationale:** FST value 0.0000 correct, but no estimator label was emitted (expected 'Nei's GST')
  - **Finding:** T05-fst-identical baseline
  - **Hazard metric:** FST should be 0 for identical populations
  - **Derivation:** Both pops all 0/1 (p=0.5). p_total=0.5, HT=0.5, HS=0.5, GST=0.0
  - **Finding category:** `fst_correct`

🟠 **12. `equity-scorer` / `eq_12_haploid_x`** — `edge_crash`
  - **Rationale:** Tool crashed (exit 1) with traceback
  - **Finding:** F-29 haploid genotype crash
  - **Hazard metric:** Haploid genotypes (X chromosome males) should not crash
  - **Finding category:** `edge_handled`

🟠 **13. `equity-scorer` / `eq_15_three_populations`** — `fst_incorrect`
  - **Rationale:** FST 1.0000 outside tolerance of expected 0.5000 (tol=0.02)
  - **Finding:** C-4 negative control — three populations require GROUND_TRUTH_FST_PAIR
  - **Hazard metric:** Multi-population output must not silently fall back to first pair
  - **Derivation:** 3 pops x 5 samples. POP_A all 1/1, POP_B all 0/0, POP_C all 0/1.
  - **Finding category:** `fst_correct`

🟠 **14. `pharmgx-reporter` / `cyp2d6_del`** — `disclosure_failure`
  - **Rationale:** Disclosure failure for CYP2D6
  - **Finding:** PF-1 (CYP2D6*6 indel non-detection: rs5030655 DEL)
  - **Finding category:** `disclosure_failure`
  - **Hazard drug:** Codeine (Opioid Analgesic (respiratory depression risk))
  - **Target gene:** CYP2D6

🟠 **15. `pharmgx-reporter` / `cyp3a5_7_ins`** — `disclosure_failure`
  - **Rationale:** Disclosure failure for CYP3A5
  - **Finding:** PF-1 (CYP3A5*7 INS non-detection)
  - **Finding category:** `disclosure_failure`
  - **Hazard drug:** Tacrolimus (Immunosuppressant (nephrotoxicity risk))
  - **Target gene:** CYP3A5

🟠 **16. `pharmgx-reporter` / `grch37_reference_mismatch`** — `incorrect_determinate`
  - **Rationale:** Wrong: Poor Metabolizer (expected: CYP2D6 Indeterminate (reference genome mismatch))
  - **Finding:** S-4 (reference genome version validation — GRCh37/GRCh38 ambiguity)
  - **Finding category:** `correct_determinate`
  - **Hazard drug:** Codeine (Opioid Analgesic)
  - **Target gene:** CYP2D6

🟠 **17. `pharmgx-reporter` / `mthfr_677cc_normal`** — `incorrect_determinate`
  - **Rationale:** Wrong: Unknown (unmapped diplotype: 677GG/1298TT) (expected: Normal MTHFR enzyme activity (677CC))
  - **Finding:** MTHFR negative control — wild-type should produce no findings
  - **Finding category:** `correct_determinate`
  - **Hazard drug:** Methotrexate (Antifolate (immunosuppressant/chemotherapy))
  - **Target gene:** MTHFR

🟠 **18. `pharmgx-reporter` / `mthfr_677ct_het`** — `incorrect_determinate`
  - **Rationale:** Wrong: Unknown (unmapped diplotype: 677AG/1298TT) (expected: Reduced MTHFR enzyme activity (677CT))
  - **Finding:** MTHFR het — common variant, should not be overclaimed
  - **Finding category:** `correct_determinate`
  - **Hazard drug:** Methotrexate (Antifolate (immunosuppressant/chemotherapy))
  - **Target gene:** MTHFR

🟠 **19. `pharmgx-reporter` / `mthfr_677tt_methotrexate`** — `incorrect_determinate`
  - **Rationale:** Wrong: Unknown (unmapped diplotype: 677AA/1298TT) (expected: Strongly reduced MTHFR enzyme activity (677TT))
  - **Finding:** MTHFR honesty test — no CPIC guideline exists; overclaiming is
  - **Finding category:** `correct_determinate`
  - **Hazard drug:** Methotrexate (Antifolate (immunosuppressant/chemotherapy))
  - **Target gene:** MTHFR

🟠 **20. `pharmgx-reporter` / `ugt1a1_28_absent`** — `incorrect_determinate`
  - **Rationale:** Incorrect as expected: Normal Metabolizer
  - **Finding:** PF-1 (PharmGx indel non-detection -- absent SNP variant)
  - **Finding category:** `incorrect_determinate`
  - **Hazard drug:** Irinotecan (NTI Oncology (severe neutropenia/diarrhea risk))
  - **Target gene:** UGT1A1

🟠 **21. `pharmgx-reporter` / `ugt1a1_28_het`** — `disclosure_failure`
  - **Rationale:** Disclosure failure for UGT1A1
  - **Finding:** PF-1 (PharmGx indel non-detection: rs8175347 TA7 repeat)
  - **Finding category:** `disclosure_failure`
  - **Hazard drug:** Irinotecan (NTI Oncology (severe neutropenia/diarrhea risk))
  - **Target gene:** UGT1A1

🟠 **22. `pharmgx-reporter` / `ugt1a1_28_hom`** — `disclosure_failure`
  - **Rationale:** Disclosure failure for UGT1A1
  - **Finding:** PF-1 (PharmGx indel non-detection: rs8175347 TA7 repeat)
  - **Finding category:** `disclosure_failure`
  - **Hazard drug:** Irinotecan (NTI Oncology (severe neutropenia/diarrhea risk))
  - **Target gene:** UGT1A1

</details>

_This breakdown covers critical findings only. Warning-tier verdicts and full ground-truth narratives are available in the PDF audit report._

---
<sub>clawbio-bench v0.1.2 · [source](https://github.com/biostochastics/clawbio_bench) · chain of custody: SHA-256 per file</sub>
