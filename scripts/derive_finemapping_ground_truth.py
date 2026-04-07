#!/usr/bin/env python3
"""Derive ground-truth values for fine-mapping test cases from a reference oracle.

For test cases where the analytic answer is non-trivial (fm_17, fm_18, fm_20,
fm_21), the bench's previous approach of writing EXPECTED_PIPS by hand led to
five transcription bugs in v0.1.3 (eq_15, fm_17, fm_18, fm_20 and an implicit
issue in the SuSiE PIP aggregation formula).

This script avoids that whole class of bug by deriving ground truth from a
*reference oracle* — the gentropy port of FinucaneLab/fine-mapping-inf,
vendored under ``scripts/_reference/gentropy_susie_inf.py``. The oracle
implements the algorithm correctly (notably: ``est_tausq=True`` actually
estimates τ² via method-of-moments, which ClawBio's current implementation
fails to do because the parameter is hardcoded ``False`` at the only call
site of ``_mom_update``).

The script writes:
  - The full inputs.json the harness driver consumes
  - A ``derived/<test>.expected.json`` file with the oracle output

The harness ground_truth.txt files reference these by ``DERIVED_FROM`` so
auditors can re-run the script and diff against the committed expected
values to detect oracle drift.

Run with the [dev] or [finemapping] extra installed:

    .venv/bin/python3 scripts/derive_finemapping_ground_truth.py

The script is idempotent: re-running it produces byte-identical output
unless the oracle or the input geometry changes.

Why this lives under scripts/ and not in the package
----------------------------------------------------
The reference oracle is third-party numerical code copied from gentropy
(Apache-2.0). Vendoring it inside ``src/clawbio_bench/`` would put a
Spark-port-of-a-port of a finetuned algorithm into the trusted base of an
audit tool — a contradiction in terms. The bench package itself never
imports it; only this offline derivation script does.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "_reference"))

import gentropy_susie_inf as oracle  # noqa: E402
import numpy as np  # noqa: E402

TEST_CASES_DIR = REPO_ROOT / "src" / "clawbio_bench" / "test_cases" / "finemapping"


# ---------------------------------------------------------------------------
# Geometry constructors. Each test case is fully deterministic — no random
# seeds, no time-dependent values. Re-running the script must produce
# byte-identical inputs.json AND byte-identical oracle output.
# ---------------------------------------------------------------------------


def _block_R(p: int, n_blocks: int, rho: float) -> np.ndarray:
    """Equicorrelated block-LD matrix.

    p must be divisible by n_blocks. Within-block off-diagonal entries are
    rho; between-block entries are 0; diagonal is 1.
    """
    assert p % n_blocks == 0, f"p={p} not divisible by n_blocks={n_blocks}"
    R = np.eye(p)
    block_size = p // n_blocks
    for b in range(n_blocks):
        lo, hi = b * block_size, (b + 1) * block_size
        for i in range(lo, hi):
            for j in range(lo, hi):
                if i != j:
                    R[i, j] = rho
    return R


def _block_uniform_z(p: int, base: float, bumps: dict[int, float]) -> np.ndarray:
    """Block-uniform z: first half +base, second half -base, with bumps."""
    z = np.array([base if i < p // 2 else -base for i in range(p)], dtype=float)
    for idx, val in bumps.items():
        z[idx] = val
    return z


def fm_20_inputs() -> dict[str, Any]:
    """fm_20: SuSiE-inf est_tausq=True activation honesty test.

    Geometry: p=200, n=5000, L=5, two-block LD with rho=0.20,
    block-uniform alternating z (1.5/-1.5) with two bumps at indices 17
    and 123 (one per block) at +3.5/-3.5.

    Expected behavior on the gentropy reference (with est_tausq=True):
      tausq estimated > 0 via method-of-moments
      bumps at 17 and 123 are SUPPRESSED to PIP ~0.03 because the
        polygenic background is absorbed into the infinitesimal component
      max|PIP_T - PIP_F| > 0.10 across all variants

    A tool that ignores est_tausq (e.g. ClawBio's current build) will
    produce identical output to fm_21 with tausq=0 and PIP[17]/PIP[123]
    around 0.16, which the harness flags as susie_inf_est_tausq_ignored.
    """
    p = 200
    R = _block_R(p, n_blocks=2, rho=0.20)
    z = _block_uniform_z(p, base=1.5, bumps={17: 3.5, 123: -3.5})
    return {
        "method": "susie_inf",
        "z": z.tolist(),
        "R": R.tolist(),
        "n": 5000,
        "L": 5,
        "w": 0.04,
        "est_tausq": True,
        "max_iter": 200,
        "tol": 0.001,
        "min_purity": 0.5,
        "rsids": [f"rs_{i:03d}" for i in range(p)],
    }


def fm_21_inputs() -> dict[str, Any]:
    """fm_21: SuSiE-inf est_tausq=False guard partner to fm_20.

    Same geometry as fm_20 but with est_tausq=False. On a correct
    implementation this produces:
      tausq stays at 0
      bumps at 17 and 123 stand OUT at PIP ~0.16 because there is no
        infinitesimal component to absorb them
      output differs meaningfully from fm_20 (max|dPIP| ~0.13)

    On a tool that ignores est_tausq, fm_21 and fm_20 produce identical
    output, which is the cross-test signal.
    """
    inputs = fm_20_inputs()
    inputs["est_tausq"] = False
    return inputs


GEOMETRIES = {
    "fm_20_susie_inf_est_tausq_activation": fm_20_inputs,
    "fm_21_susie_inf_est_tausq_guard": fm_21_inputs,
}


# ---------------------------------------------------------------------------
# Oracle execution. Loads the gentropy reference (which is pure numpy/scipy)
# and runs it against each geometry. Captures full PIP, alpha, sigmasq,
# tausq, and a SHA-256 of the canonical-encoded inputs JSON for chain of
# custody.
# ---------------------------------------------------------------------------


def _aggregate_pip(per_effect_pip: np.ndarray) -> list[float]:
    """SuSiE per-variant PIP aggregation across L single-effect rows.

    PIP_i = 1 - prod_l (1 - PIP_{l,i}). Wang et al. 2020 Eq. 3.
    """
    aggr: np.ndarray = 1.0 - np.prod(1.0 - per_effect_pip, axis=1)
    return [float(x) for x in aggr]


def _run_oracle(inputs: dict[str, Any]) -> dict[str, Any]:
    z = np.asarray(inputs["z"], dtype=float)
    R = np.asarray(inputs["R"], dtype=float)
    out = oracle.susie_inf(
        z=z,
        LD=R,
        n=int(inputs["n"]),
        L=int(inputs["L"]),
        est_tausq=bool(inputs["est_tausq"]),
        est_sigmasq=True,
        method="moments",
        maxiter=int(inputs.get("max_iter", 200)),
        PIP_tol=float(inputs.get("tol", 0.001)),
    )
    pip_aggr = _aggregate_pip(out["PIP"])
    return {
        "tausq": float(out["tausq"]),
        "sigmasq": float(out["sigmasq"]),
        "pip_aggregated": pip_aggr,
        "pip_per_effect": out["PIP"].tolist(),
        "n_variants": len(pip_aggr),
        "n_effects": int(out["PIP"].shape[1]),
    }


def _canonical_json(obj: dict[str, Any]) -> bytes:
    """Stable byte representation for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _format_pip_array(pips: list[float], decimals: int = 6) -> str:
    """Compact JSON array of rounded PIPs for ground_truth.txt embedding."""
    rounded = [round(p, decimals) for p in pips]
    return json.dumps(rounded, separators=(", ", ": "))


def _write_fm21_ground_truth(tc_dir: Path, oracle_out: dict[str, Any]) -> None:
    """Write the fm_21 ground_truth.txt with the oracle PIPs embedded.

    fm_21 needs the full per-variant PIP array (not just bump scalars)
    because the harness scores it as a normal finemap_correct test
    against EXPECTED_PIPS. To avoid hand-transcription bugs in a
    200-element array, generate the file deterministically from the
    oracle output. The header text is templated so re-derivation is
    fully reproducible.
    """
    pip_array = _format_pip_array(oracle_out["pip_aggregated"])
    body = f"""# BENCHMARK: clawbio-finemapping v0.1.0
# PAYLOAD: inputs.json
# METHOD: susie_inf
# FINDING: FM-21 SuSiE-inf est_tausq=False guard partner to fm_20
# FINDING_CATEGORY: finemap_correct
#
# HAZARD_METRIC: This is the est_tausq=False half of the SuSiE-inf
#   activation honesty test (paired with fm_20). With est_tausq=False
#   the algorithm should NOT estimate tau^2 — it should leave tausq at
#   its initial value (0) and produce standard SuSiE-RSS output. This
#   test verifies two things:
#     (1) The tool can correctly run standard SuSiE-RSS when the toggle
#         is off (sanity check that est_tausq=False is honored).
#     (2) Cross-checked against fm_20 (same input, est_tausq=True), the
#         outputs should DIFFER. Specifically, the bump variants 17 and
#         123 should have PIP ~0.16 here (no infinitesimal absorption)
#         vs ~0.026 in fm_20 (with infinitesimal absorption). A tool
#         that ignores the est_tausq parameter will produce identical
#         output for fm_20 and fm_21 — that diagnosis lives in fm_20's
#         susie_inf_est_tausq_ignored category.
#
#   On ClawBio's current build (HEAD as of 2026-04-07), fm_21 PASSES
#   because the buggy "always est_tausq=False" behavior coincidentally
#   matches what est_tausq=False is supposed to produce. Only fm_20
#   catches the defect. fm_21's job is to confirm the cross-check
#   diagnosis from a second angle and to keep the toggle test
#   discriminating after ClawBio fixes the dead code.
#
# DERIVED_FROM: derived/oracle_expected.json
#               (re-generate via scripts/derive_finemapping_ground_truth.py)
#
# ── Expected output (gentropy oracle, est_tausq=False) ──
# Bumps at variants 17 and 123 stand out at PIP ~0.1609. All other
# variants are at the diffuse-row baseline ~0.0233. PIP_TOLERANCE 0.05
# is loose enough to absorb minor numerical differences between scipy
# versions and tight enough to catch a real divergence.
#
# EXPECTED_PIPS: {pip_array}
# PIP_TOLERANCE: 0.05
#
# ── tau^2 upper bound ──
# With est_tausq=False the algorithm should not modify tau^2 from its
# initial value (0). Any positive tau^2 here would indicate the
# parameter is being ignored in the OPPOSITE direction (estimating
# despite the user asking for it not to be).
# EXPECTED_TAUSQ_MAX: 1.0e-9
#
# EXPECTED_EXIT_STATUS: ok
# CUI_REF: CUI_2023
#
# DERIVATION:
#   Geometry: identical to fm_20 (p=200, n=5000, L=5, block LD rho=0.20,
#   block-uniform alternating z with bumps at 17, 123) but with
#   est_tausq=False.
#
#   Reference ground truth (gentropy SuSiE-inf port, est_tausq=False):
#     tausq      = {oracle_out["tausq"]}
#     sigmasq    = {oracle_out["sigmasq"]:.6f}
#     PIP[17]    = {oracle_out["pip_aggregated"][17]:.6f}
#     PIP[123]   = {oracle_out["pip_aggregated"][123]:.6f}
#
#   ClawBio's current build returns identical output for fm_20 and
#   fm_21 (PIP[17] = PIP[123] = 0.1609, tausq = 0.0). On fm_21 this
#   coincidentally matches the correct est_tausq=False output, so
#   fm_21 PASSES on the buggy build. After ClawBio fixes the dead-
#   code defect (propagating est_tausq from the public API into the
#   _mom_update call site), fm_20 will start passing too and the
#   fm_20/fm_21 pair becomes a discriminating regression test.
#
#   The reference oracle is the gentropy port of FinucaneLab/fine-mapping-inf,
#   vendored under scripts/_reference/gentropy_susie_inf.py. To re-derive
#   the expected values run scripts/derive_finemapping_ground_truth.py.
#
# CITATION: Cui R et al. (2023). Nature Genetics 56(1):162-169. doi:10.1038/s41588-023-01597-3
# WANG_REF: Wang G et al. (2020). JRSS-B 82(5):1273-1300, Eq. 3 (PIP aggregation across L rows)
# UPSTREAM_REF: FinucaneLab/fine-mapping-inf (master branch); gentropy port at opentargets/gentropy/main/src/gentropy/method/susie_inf.py
"""
    (tc_dir / "ground_truth.txt").write_text(body, encoding="utf-8")


def main() -> int:
    print("=" * 78)
    print("Fine-mapping ground-truth derivation")
    print("Oracle: gentropy port (vendored at scripts/_reference/gentropy_susie_inf.py)")
    print("=" * 78)
    print()

    fm20_oracle = None
    fm21_oracle = None

    for tc_name, geometry_fn in GEOMETRIES.items():
        tc_dir = TEST_CASES_DIR / tc_name
        tc_dir.mkdir(parents=True, exist_ok=True)
        inputs = geometry_fn()
        inputs_path = tc_dir / "inputs.json"
        # Write inputs.json with stable formatting (sort_keys + 2-space indent
        # for human readability; the bench harness will hash this byte-for-byte
        # so consistency matters more than aesthetics).
        inputs_text = json.dumps(inputs, sort_keys=True, indent=2)
        inputs_path.write_text(inputs_text + "\n", encoding="utf-8")
        inputs_sha = hashlib.sha256(_canonical_json(inputs)).hexdigest()

        oracle_out = _run_oracle(inputs)

        derived_dir = tc_dir / "derived"
        derived_dir.mkdir(exist_ok=True)
        expected_path = derived_dir / "oracle_expected.json"
        expected = {
            "test_case": tc_name,
            "oracle": "gentropy_susie_inf (vendored from opentargets/gentropy)",
            "oracle_method": "moments",
            "inputs_sha256_canonical": inputs_sha,
            "expected_tausq": oracle_out["tausq"],
            "expected_sigmasq": oracle_out["sigmasq"],
            "expected_pip_aggregated": oracle_out["pip_aggregated"],
            "n_variants": oracle_out["n_variants"],
            "n_effects": oracle_out["n_effects"],
        }
        expected_path.write_text(
            json.dumps(expected, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        # fm_21's ground_truth.txt has a 200-element EXPECTED_PIPS array
        # which is too error-prone to maintain by hand. Generate it
        # deterministically from the oracle output so the array length
        # and bump positions can never drift from the inputs.
        if tc_name == "fm_21_susie_inf_est_tausq_guard":
            _write_fm21_ground_truth(tc_dir, oracle_out)

        # Pretty-print the salient values
        pip = oracle_out["pip_aggregated"]
        bump_indices = [17, 123] if tc_name.startswith("fm_2") else []
        print(f"  {tc_name}")
        print(f"    inputs.json:  {inputs_path}")
        print(f"    oracle file:  {expected_path}")
        print(f"    inputs SHA-256 (canonical): {inputs_sha[:16]}...")
        print(f"    tausq estimated by oracle:  {oracle_out['tausq']:.7f}")
        print(f"    sigmasq estimated by oracle: {oracle_out['sigmasq']:.7f}")
        for idx in bump_indices:
            print(f"    PIP[{idx:3d}] (bump):           {pip[idx]:.4f}")
        max_other = max(pip[i] for i in range(len(pip)) if i not in bump_indices)
        mean_other = sum(pip[i] for i in range(len(pip)) if i not in bump_indices) / max(
            1, len(pip) - len(bump_indices)
        )
        print(f"    max non-bump PIP:           {max_other:.4f}")
        print(f"    mean non-bump PIP:          {mean_other:.4f}")
        print()

        if tc_name.startswith("fm_20"):
            fm20_oracle = oracle_out
        elif tc_name.startswith("fm_21"):
            fm21_oracle = oracle_out

    # Sanity-check the cross-pair discriminating signal so the test design
    # invariants are visible at derivation time, not deferred to the harness.
    if fm20_oracle and fm21_oracle:
        pip_20 = np.array(fm20_oracle["pip_aggregated"])
        pip_21 = np.array(fm21_oracle["pip_aggregated"])
        max_diff = float(np.max(np.abs(pip_20 - pip_21)))
        tausq_diff = abs(fm20_oracle["tausq"] - fm21_oracle["tausq"])
        print("Cross-pair discriminating signal (fm_20 vs fm_21):")
        print(f"  max |PIP_20 - PIP_21|        = {max_diff:.4f}  (must be > 0.05)")
        print(f"  |tausq_20 - tausq_21|        = {tausq_diff:.6f}  (must be > 0)")
        print(f"  fm_20 tausq > 0 (MoM active): {fm20_oracle['tausq'] > 1e-9}")
        print(f"  fm_21 tausq == 0:             {fm21_oracle['tausq'] == 0.0}")
        if max_diff < 0.05:
            print()
            print("  WARNING: cross-pair discriminating signal is weak.")
            print("  The toggle test will not have meaningful power.")
            return 1
        if fm20_oracle["tausq"] <= 1e-9:
            print()
            print("  WARNING: fm_20 oracle did not activate MoM.")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
