#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ClawBio Fine-Mapping Benchmark Harness
========================================

Audits ClawBio's ``skills/fine-mapping/core/{abf,susie,credible_sets}.py``
for numerical correctness (Wakefield 2009, Wang et al. 2020), silent
failures on invalid input, and claim-vs-reality mismatches in the
output dicts (``pure``, ``coverage``, ``converged``, variant-level
``pip``).

See ``docs/harnesses/finemapping.md`` for the full spec and per-category
derivation.

Invocation model
----------------

The fine-mapping skill has no CLI. We ship a subprocess driver at
``clawbio_bench/drivers/finemapping_driver.py`` that imports the skill
in a separate interpreter and emits JSON. The harness only ever talks
to the driver via stdout/exit-code — the ``clawbio_bench.*`` import
surface never touches ClawBio code.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from clawbio_bench import core as harness_core

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "clawbio-finemapping"
BENCHMARK_VERSION = "0.1.0"

# Driver shim lives alongside the harness package. Resolved once at import
# time so every verdict embeds the driver's SHA-256 without re-globbing.
_DRIVER_PATH = Path(__file__).resolve().parent.parent / "drivers" / "finemapping_driver.py"

RUBRIC_CATEGORIES = [
    # Pass
    "finemap_correct",
    "edge_handled",
    # Fail — numerical
    "pip_value_incorrect",
    "pip_nan_silent",
    # Fail — SuSiE algorithmic pathologies
    "susie_null_forced_signal",
    "susie_spurious_secondary_signal",
    "susie_nonconvergence_suppressed",
    "susie_moment_field_mislabeled",
    # Fail — credible-set claim-vs-reality
    "credset_pip_is_alpha_mismatch",
    "credset_purity_mean_hides_weak",
    "credset_purity_none_wrongly_pure",
    "credset_coverage_incorrect",
    # Fail — ABF input handling
    "abf_variant_n_collapsed",
    # Fail — input validation
    "input_validation_missing",
    # Fail — safety
    "edge_crash",
    # Infra
    "harness_error",
]

PASS_CATEGORIES = ["finemap_correct", "edge_handled"]
FAIL_CATEGORIES = [
    "pip_value_incorrect",
    "pip_nan_silent",
    "susie_null_forced_signal",
    "susie_spurious_secondary_signal",
    "susie_nonconvergence_suppressed",
    "susie_moment_field_mislabeled",
    "credset_pip_is_alpha_mismatch",
    "credset_purity_mean_hides_weak",
    "credset_purity_none_wrongly_pure",
    "credset_coverage_incorrect",
    "abf_variant_n_collapsed",
    "input_validation_missing",
    "edge_crash",
]

GROUND_TRUTH_REFS = {
    "WAKEFIELD_2009": (
        "Wakefield, J. (2009). Bayes factors for genome-wide association "
        "studies: comparison with p-values. Genetic Epidemiology, 33(1), "
        "79-86. doi:10.1002/gepi.20359"
    ),
    "WANG_2020": (
        "Wang, G., Sarkar, A., Carbonetto, P., & Stephens, M. (2020). A "
        "simple new approach to variable selection in regression, with "
        "application to genetic fine mapping. Journal of the Royal "
        "Statistical Society: Series B, 82(5), 1273-1300. "
        "doi:10.1111/rssb.12388"
    ),
}

CATEGORY_LEGEND = {
    "finemap_correct": {"color": "#22c55e", "label": "PIPs correct + claims match math"},
    "edge_handled": {"color": "#86efac", "label": "Edge case handled cleanly"},
    "pip_value_incorrect": {"color": "#ef4444", "label": "PIPs outside tolerance"},
    "pip_nan_silent": {"color": "#ef4444", "label": "NaN PIPs returned silently"},
    "susie_null_forced_signal": {"color": "#ef4444", "label": "Null locus returns phantom signal"},
    "susie_spurious_secondary_signal": {
        "color": "#ef4444",
        "label": "One-signal locus returns phantom secondary",
    },
    "susie_nonconvergence_suppressed": {
        "color": "#f97316",
        "label": "Non-convergence scored as valid",
    },
    "susie_moment_field_mislabeled": {
        "color": "#f97316",
        "label": "mu/mu2 are alpha-weighted, not posterior",
    },
    "credset_pip_is_alpha_mismatch": {
        "color": "#f97316",
        "label": "Credset 'pip' field is alpha not true PIP",
    },
    "credset_purity_mean_hides_weak": {
        "color": "#f97316",
        "label": "Mean purity hides weak-link variant",
    },
    "credset_purity_none_wrongly_pure": {
        "color": "#f97316",
        "label": "pure=True when purity unknown",
    },
    "credset_coverage_incorrect": {
        "color": "#f97316",
        "label": "Reported coverage != sum of weights",
    },
    "abf_variant_n_collapsed": {"color": "#ef4444", "label": "Per-variant n collapsed to median"},
    "input_validation_missing": {"color": "#ef4444", "label": "Invalid input silently accepted"},
    "edge_crash": {"color": "#ef4444", "label": "Edge case crashed"},
    "harness_error": {"color": "#9ca3af", "label": "Harness infrastructure error"},
}


# ---------------------------------------------------------------------------
# Ground truth helpers
# ---------------------------------------------------------------------------


def _parse_expected_list(raw: str | None) -> list[float] | None:
    """Parse a stringified JSON array of floats from ground truth."""
    if raw is None or raw == "":
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    try:
        return [float(x) for x in parsed]
    except (TypeError, ValueError):
        return None


def _parse_expected_bool(raw: str | None) -> bool | None:
    """Parse a stringified bool. None / '' / 'null' → None (unchecked)."""
    if raw is None or raw == "" or raw == "null":
        return None
    return str(raw).lower() in ("true", "1", "yes")


def _parse_expected_float(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _has_nan(values: Any) -> bool:
    """True if ``values`` contains any NaN / inf / None."""
    if values is None:
        return False
    for v in values:
        if v is None:
            return True
        try:
            f = float(v)
        except (TypeError, ValueError):
            return True
        if f != f or f == float("inf") or f == float("-inf"):
            return True
    return False


# ---------------------------------------------------------------------------
# Scoring Engine
# ---------------------------------------------------------------------------


def _score_numerical_correctness(
    expected_category: str,
    expected_pips: list[float] | None,
    tolerance: float,
    result: dict,
) -> dict | None:
    """Compare returned PIPs to expected values.

    Returns a verdict dict if a category decision can be made here, or
    None if the caller should fall through to category-specific logic.
    """
    pips = result.get("pips")
    if expected_pips is None or pips is None:
        return None
    if len(expected_pips) != len(pips):
        return {
            "category": "pip_value_incorrect",
            "rationale": (f"PIP length mismatch: expected {len(expected_pips)}, got {len(pips)}"),
            "details": {"expected_pips": expected_pips, "observed_pips": pips},
        }
    max_err = max(abs(float(a) - float(b)) for a, b in zip(expected_pips, pips, strict=True))
    if max_err <= tolerance:
        if expected_category == "finemap_correct":
            return None  # Let the caller also verify claim fields.
        return None
    return {
        "category": "pip_value_incorrect",
        "rationale": (f"max |PIP - expected| = {max_err:.6f} exceeds tolerance {tolerance}"),
        "details": {
            "expected_pips": expected_pips,
            "observed_pips": pips,
            "max_error": max_err,
            "tolerance": tolerance,
        },
    }


def score_finemapping_verdict(
    ground_truth: dict,
    result: dict,
    execution: harness_core.ExecutionResult,
) -> dict:
    """Score a fine-mapping run against its ground truth.

    The ground truth's ``FINDING_CATEGORY`` tells us which category the
    test case is designed to surface; the scorer verifies the condition
    for that category and returns the appropriate verdict. The scorer
    never raises; every unhandled path returns ``harness_error``.
    """
    expected_category = ground_truth.get("FINDING_CATEGORY", "")
    expected_status = ground_truth.get("EXPECTED_EXIT_STATUS", "ok")
    tolerance = float(ground_truth.get("PIP_TOLERANCE", "0.01"))
    expected_pips = _parse_expected_list(ground_truth.get("EXPECTED_PIPS"))

    details: dict[str, Any] = {
        "expected_category": expected_category,
        "expected_status": expected_status,
        "driver_status": result.get("status"),
        "driver_exit_code": execution.exit_code,
        "observed_pips": result.get("pips"),
    }

    status = result.get("status")

    # ── Driver-level failures ──
    if execution.exit_code == 1 or status == "driver_error":
        return {
            "category": "harness_error",
            "rationale": (
                "Driver infrastructure error: "
                f"{(result.get('error') or {}).get('message', 'unknown')}"
            ),
            "details": details,
        }

    # ── Skill-side import failure ──
    # Exit code 2 means the skill dir was missing or unloadable — at
    # older commits the fine-mapping skill may not exist yet. We treat
    # this as edge_handled so longitudinal sweeps don't report
    # spurious failures for pre-existing commits. A test case can
    # override by setting EXPECT_SKILL_IMPORT=true.
    if execution.exit_code == 2 or status == "import_error":
        if ground_truth.get("EXPECT_SKILL_IMPORT", "true").lower() == "false":
            return {
                "category": "edge_handled",
                "rationale": "Fine-mapping skill not present at this commit (expected)",
                "details": details,
            }
        return {
            "category": "harness_error",
            "rationale": (
                "Skill module import failed: "
                f"{(result.get('error') or {}).get('message', 'unknown')}"
            ),
            "details": details,
        }

    # ── Category-specific scoring ──

    # NaN silent detection: any NaN in PIPs while status="ok" is the bug.
    # "ok" here means the skill did NOT raise — it returned NaN garbage.
    if expected_category == "pip_nan_silent":
        if status == "raised":
            return {
                "category": "edge_handled",
                "rationale": (
                    "Skill correctly raised on invalid input: "
                    f"{(result.get('error') or {}).get('type', '?')}"
                ),
                "details": details,
            }
        if status == "ok" and _has_nan(result.get("pips")):
            return {
                "category": "pip_nan_silent",
                "rationale": "NaN/inf in returned PIPs with no exception raised",
                "details": details,
            }
        # Neither raised nor NaN — the bug is absent on this commit.
        return {
            "category": "edge_handled",
            "rationale": "Invalid input handled without NaN propagation",
            "details": details,
        }

    if expected_category == "input_validation_missing":
        if status == "raised":
            return {
                "category": "edge_handled",
                "rationale": "Skill raised on invalid parameter (validation present)",
                "details": details,
            }
        # Did the skill return numeric output for an invalid parameter?
        if status == "ok" and result.get("pips") is not None:
            return {
                "category": "input_validation_missing",
                "rationale": "Invalid input silently accepted; numeric output returned",
                "details": details,
            }
        if status == "ok" and result.get("credible_sets") is not None:
            return {
                "category": "input_validation_missing",
                "rationale": "Invalid coverage silently accepted; credible set returned",
                "details": details,
            }
        return {
            "category": "harness_error",
            "rationale": f"Unexpected driver status {status!r} for validation test",
            "details": details,
        }

    if expected_category == "susie_null_forced_signal":
        pips = result.get("pips") or []
        if not pips:
            return {
                "category": "harness_error",
                "rationale": "No PIPs returned for null-locus test",
                "details": details,
            }
        max_pip = max(float(x) for x in pips)
        threshold = float(ground_truth.get("NULL_PIP_THRESHOLD", "0.1"))
        details["max_pip"] = max_pip
        details["null_threshold"] = threshold
        if max_pip > threshold:
            return {
                "category": "susie_null_forced_signal",
                "rationale": (
                    f"Null locus produced max PIP {max_pip:.4f} > {threshold}; "
                    "IBSS forced every single-effect row to place mass"
                ),
                "details": details,
            }
        return {
            "category": "edge_handled",
            "rationale": f"Null locus correctly produced max PIP {max_pip:.4f}",
            "details": details,
        }

    if expected_category == "susie_spurious_secondary_signal":
        pips = result.get("pips") or []
        if len(pips) < 2:
            return {
                "category": "harness_error",
                "rationale": "Need at least 2 variants for spurious-secondary test",
                "details": details,
            }
        primary = float(pips[0])
        secondary = float(pips[1])
        threshold = float(ground_truth.get("SECONDARY_PIP_THRESHOLD", "0.05"))
        details["primary_pip"] = primary
        details["secondary_pip"] = secondary
        details["secondary_threshold"] = threshold
        # The bug fires when the null variant's PIP exceeds the phantom
        # threshold. Don't require primary > 0.9 — an impl that dilutes
        # the real signal AND creates a phantom is doubly-wrong and we
        # want to catch it here rather than fall through to edge_handled.
        if secondary > threshold:
            return {
                "category": "susie_spurious_secondary_signal",
                "rationale": (
                    f"Null variant PIP {secondary:.4f} exceeds phantom "
                    f"threshold {threshold} (primary variant PIP {primary:.4f})"
                ),
                "details": details,
            }
        return {
            "category": "edge_handled",
            "rationale": f"Single-signal locus produced clean PIPs {pips}",
            "details": details,
        }

    if expected_category == "susie_nonconvergence_suppressed":
        converged = result.get("converged")
        pips = result.get("pips")
        details["converged"] = converged
        details["n_iter"] = result.get("n_iter")
        # A non-converged run that ALSO produced NaN PIPs is a more
        # specific failure (pip_nan_silent); fire that category first so
        # auditors see the worse bug. A non-converged run with finite
        # PIPs is the nonconvergence-suppressed pattern.
        if converged is False and pips is not None and _has_nan(pips):
            return {
                "category": "pip_nan_silent",
                "rationale": (
                    f"SuSiE non-converged AND returned NaN PIPs (n_iter={result.get('n_iter')})"
                ),
                "details": details,
            }
        if converged is False and pips is not None and not _has_nan(pips):
            return {
                "category": "susie_nonconvergence_suppressed",
                "rationale": (
                    f"SuSiE returned scored PIPs despite converged=False "
                    f"(n_iter={result.get('n_iter')})"
                ),
                "details": details,
            }
        if converged is True:
            return {
                "category": "edge_handled",
                "rationale": (
                    f"SuSiE converged (n_iter={result.get('n_iter')}); "
                    "non-convergence probe did not trip"
                ),
                "details": details,
            }
        return {
            "category": "harness_error",
            "rationale": f"Ambiguous convergence state: converged={converged}",
            "details": details,
        }

    if expected_category == "susie_moment_field_mislabeled":
        expected_mu00 = _parse_expected_float(ground_truth.get("EXPECTED_MOMENT_MU_00"))
        mu = result.get("mu")
        moment_tol = float(ground_truth.get("MOMENT_TOLERANCE", "0.1"))
        if expected_mu00 is None or mu is None or not mu or not mu[0]:
            return {
                "category": "harness_error",
                "rationale": "Missing EXPECTED_MOMENT_MU_00 or mu output",
                "details": details,
            }
        observed_mu00 = float(mu[0][0])
        details["expected_mu00"] = expected_mu00
        details["observed_mu00"] = observed_mu00
        details["moment_tolerance"] = moment_tol
        if abs(observed_mu00 - expected_mu00) > moment_tol:
            return {
                "category": "susie_moment_field_mislabeled",
                "rationale": (
                    f"mu[0][0]={observed_mu00:.4f} differs from "
                    f"conditional-posterior-mean reference {expected_mu00:.4f} "
                    f"by more than {moment_tol}"
                ),
                "details": details,
            }
        return {
            "category": "finemap_correct",
            "rationale": f"mu[0][0]={observed_mu00:.4f} matches reference",
            "details": details,
        }

    if expected_category == "credset_pip_is_alpha_mismatch":
        credsets = result.get("credible_sets") or []
        alpha = result.get("alpha")
        if not credsets or alpha is None:
            return {
                "category": "harness_error",
                "rationale": "No credible sets or alpha returned",
                "details": details,
            }
        # Recompute true PIP from alpha: PIP_i = 1 - prod_ell (1 - alpha[ell][i])
        # (rename the loop variable away from single-char ``l`` which ruff
        # E741 rejects as ambiguous against digit ``1`` in some fonts).
        p = len(alpha[0])
        n_layers = len(alpha)
        true_pips = [
            1.0 - float(_product(1.0 - float(alpha[ell][i]) for ell in range(n_layers)))
            for i in range(p)
        ]
        details["true_pips"] = true_pips
        # Map rsid → index. Prefer the synthetic rs_syn_<N> convention
        # used by our test cases; if that fails, fall back to the rsid's
        # position within the credible set's variants list matched
        # against the alpha row's argmax ordering. This lets the scorer
        # handle real-world rsids too, not just synthetic ones.
        rsids_in_order = [v.get("rsid", "") for cs in credsets for v in cs.get("variants", [])]
        # Unique rsids, in first-seen order, for positional fallback.
        seen: set[str] = set()
        ordered_rsids: list[str] = []
        for r in rsids_in_order:
            if r not in seen:
                seen.add(r)
                ordered_rsids.append(r)
        # For each credset variant, compare "pip" against true PIP at
        # that index. The bug is: "pip" equals alpha[l][i] (one row),
        # not the true PIP across all rows.
        max_err = 0.0
        mismatched: list[dict] = []
        for cs in credsets:
            for v in cs.get("variants", []):
                rsid = v.get("rsid", "")
                idx = _rsid_to_idx(rsid, p)
                if idx is None:
                    # Real-world rsid: try positional fallback based on
                    # order of appearance across all credsets.
                    try:
                        pos = ordered_rsids.index(rsid)
                    except ValueError:
                        continue
                    if 0 <= pos < p:
                        idx = pos
                    else:
                        continue
                reported = float(v.get("pip", 0.0))
                truth = true_pips[idx]
                err = abs(reported - truth)
                if err > max_err:
                    max_err = err
                if err > tolerance:
                    mismatched.append({"rsid": rsid, "reported": reported, "true": truth})
        details["max_pip_error"] = max_err
        details["mismatched_variants"] = mismatched
        if mismatched:
            return {
                "category": "credset_pip_is_alpha_mismatch",
                "rationale": (
                    f"Credset 'pip' field differs from true PIP for "
                    f"{len(mismatched)} variants; max error {max_err:.4f}"
                ),
                "details": details,
            }
        return {
            "category": "finemap_correct",
            "rationale": "All credset 'pip' fields match true PIP",
            "details": details,
        }

    if expected_category == "credset_purity_mean_hides_weak":
        credsets = result.get("credible_sets") or []
        R_list = ground_truth.get("REFERENCE_R")
        if not credsets:
            return {
                "category": "harness_error",
                "rationale": "No credible sets returned",
                "details": details,
            }
        cs0 = credsets[0]
        reported_pure = bool(cs0.get("pure"))
        reported_purity = cs0.get("purity")
        # Compute min pairwise |r| ourselves if R provided
        min_r = None
        if R_list:
            try:
                R = json.loads(R_list)
                indices = list(range(len(R)))
                pair_rs = [abs(float(R[i][j])) for i in indices for j in indices if i < j]
                min_r = min(pair_rs) if pair_rs else None
            except (json.JSONDecodeError, TypeError, IndexError, ValueError):
                pass
        details["reported_pure"] = reported_pure
        details["reported_purity_mean"] = reported_purity
        details["min_pairwise_r"] = min_r
        min_threshold = float(ground_truth.get("PURITY_MIN_THRESHOLD", "0.5"))
        if min_r is not None and min_r < min_threshold and reported_pure:
            return {
                "category": "credset_purity_mean_hides_weak",
                "rationale": (
                    f"Credset reported pure=True (mean r={reported_purity}) "
                    f"but min pairwise |r|={min_r:.4f} < {min_threshold}"
                ),
                "details": details,
            }
        return {
            "category": "finemap_correct",
            "rationale": "Purity flag consistent with min pairwise correlation",
            "details": details,
        }

    if expected_category == "credset_coverage_incorrect":
        credsets = result.get("credible_sets") or []
        if not credsets:
            return {
                "category": "harness_error",
                "rationale": "No credible sets returned for coverage-field audit",
                "details": details,
            }
        coverage_tol = float(ground_truth.get("COVERAGE_TOLERANCE", "0.001"))
        max_err = 0.0
        mismatches: list[dict] = []
        for cs in credsets:
            reported = float(cs.get("coverage", 0.0))
            sum_alpha = sum(float(v.get("alpha", 0.0)) for v in cs.get("variants", []))
            err = abs(reported - sum_alpha)
            if err > max_err:
                max_err = err
            if err > coverage_tol:
                mismatches.append(
                    {
                        "cs_id": cs.get("cs_id"),
                        "reported_coverage": reported,
                        "sum_alpha": sum_alpha,
                        "error": err,
                    }
                )
        details["max_coverage_error"] = max_err
        details["coverage_mismatches"] = mismatches
        details["coverage_tolerance"] = coverage_tol
        if mismatches:
            return {
                "category": "credset_coverage_incorrect",
                "rationale": (
                    f"Credset 'coverage' field differs from sum of selected "
                    f"alpha weights for {len(mismatches)} set(s); max error {max_err:.6f}"
                ),
                "details": details,
            }
        return {
            "category": "finemap_correct",
            "rationale": (
                f"All credset 'coverage' fields match sum of selected alpha "
                f"within tolerance {coverage_tol}"
            ),
            "details": details,
        }

    if expected_category == "credset_purity_none_wrongly_pure":
        credsets = result.get("credible_sets") or []
        if not credsets:
            return {
                "category": "harness_error",
                "rationale": "No credible sets returned",
                "details": details,
            }
        cs0 = credsets[0]
        reported_pure = bool(cs0.get("pure"))
        reported_purity = cs0.get("purity")
        details["reported_pure"] = reported_pure
        details["reported_purity"] = reported_purity
        if reported_purity is None and reported_pure is True:
            return {
                "category": "credset_purity_none_wrongly_pure",
                "rationale": (
                    "Credset reports pure=True with purity=None — no LD "
                    "matrix supplied, so the purity claim has no evidence"
                ),
                "details": details,
            }
        return {
            "category": "finemap_correct",
            "rationale": "Purity flag correctly reflects missing LD matrix",
            "details": details,
        }

    if expected_category == "abf_variant_n_collapsed":
        # Direct numeric comparison: the bug is [0.5, 0.5] vs reference
        # [0.43130, 0.56870].
        observed = result.get("pips")
        if expected_pips is None or observed is None:
            return {
                "category": "harness_error",
                "rationale": "Missing expected or observed PIPs",
                "details": details,
            }
        max_err = max(
            abs(float(a) - float(b)) for a, b in zip(expected_pips, observed, strict=True)
        )
        details["max_error"] = max_err
        details["tolerance"] = tolerance
        if max_err > tolerance:
            return {
                "category": "abf_variant_n_collapsed",
                "rationale": (
                    f"ABF used uniform n_eff instead of per-variant n; "
                    f"PIPs {observed} differ from reference {expected_pips} "
                    f"by {max_err:.4f}"
                ),
                "details": details,
            }
        return {
            "category": "finemap_correct",
            "rationale": "Per-variant n honored; PIPs match reference",
            "details": details,
        }

    if expected_category == "finemap_correct":
        # Straight numerical comparison with optional claim checks.
        num_verdict = _score_numerical_correctness(
            expected_category, expected_pips, tolerance, result
        )
        if num_verdict is not None:
            return num_verdict
        # Optional: converged check
        expected_converged = _parse_expected_bool(ground_truth.get("EXPECTED_CONVERGED"))
        if expected_converged is not None:
            observed_converged = result.get("converged")
            if observed_converged != expected_converged:
                details["expected_converged"] = expected_converged
                details["observed_converged"] = observed_converged
                return {
                    "category": "pip_value_incorrect",
                    "rationale": (
                        f"converged mismatch: expected {expected_converged}, "
                        f"got {observed_converged}"
                    ),
                    "details": details,
                }
        # Any NaN in the output blocks a finemap_correct verdict.
        if _has_nan(result.get("pips")):
            return {
                "category": "pip_nan_silent",
                "rationale": "NaN in PIPs despite finemap_correct expectation",
                "details": details,
            }
        return {
            "category": "finemap_correct",
            "rationale": (f"PIPs within tolerance {tolerance} and all claim fields match"),
            "details": details,
        }

    if expected_category == "edge_handled":
        # Expected: clean handling (no NaN, no traceback). Skill may
        # raise OR return correct output.
        if status == "raised":
            return {
                "category": "edge_handled",
                "rationale": "Skill raised a clean exception on edge input",
                "details": details,
            }
        if _has_nan(result.get("pips")):
            return {
                "category": "edge_crash",
                "rationale": "Edge input produced NaN PIPs (not a clean raise)",
                "details": details,
            }
        return {
            "category": "edge_handled",
            "rationale": "Edge input produced finite PIPs without raising",
            "details": details,
        }

    # ── Fallback ──
    return {
        "category": "harness_error",
        "rationale": f"Unhandled FINDING_CATEGORY: {expected_category!r}",
        "details": details,
    }


def _product(iterable: Iterable[float]) -> float:
    """Stdlib-free product (avoids math.prod for Python 3.7 callers)."""
    total = 1.0
    for x in iterable:
        total *= x
    return total


def _rsid_to_idx(rsid: str, p: int) -> int | None:
    """Map a synthetic rsid of form 'rs_syn_<idx>' back to its index."""
    if rsid.startswith("rs_syn_"):
        tail = rsid[len("rs_syn_") :]
        if tail.isdigit():
            idx = int(tail)
            if 0 <= idx < p:
                return idx
    return None


# ---------------------------------------------------------------------------
# Single Run Executor
# ---------------------------------------------------------------------------


def run_single_finemapping(
    repo_path: Path,
    commit_sha: str,
    test_case_path: Path,
    ground_truth: dict,
    payload_path: Path | None,
    output_base: Path,
    commit_meta: dict,
) -> dict:
    """Execute the fine-mapping driver for one (commit, test_case) pair."""
    tc_name = test_case_path.name if test_case_path.is_dir() else test_case_path.stem
    run_output_dir = output_base / commit_sha / tc_name
    tool_output_dir = run_output_dir / "tool_output"
    tool_output_dir.mkdir(parents=True, exist_ok=True)
    result_json_path = tool_output_dir / "result.json"

    if payload_path is None:
        return harness_core.harness_error_verdict(
            tc_name,
            commit_meta,
            ValueError(f"fine-mapping test case {tc_name} has no inputs.json payload"),
            ground_truth=ground_truth,
        )

    skill_dir = repo_path / "skills" / "fine-mapping"
    timeout = harness_core.validate_timeout(ground_truth.get("TIMEOUT", "60"))

    if not _DRIVER_PATH.exists():
        return harness_core.harness_error_verdict(
            tc_name,
            commit_meta,
            FileNotFoundError(f"fine-mapping driver shim missing: {_DRIVER_PATH}"),
            ground_truth=ground_truth,
        )

    cmd = [
        sys.executable,
        str(_DRIVER_PATH),
        "--skill-dir",
        str(skill_dir),
        "--inputs",
        str(payload_path),
        "--output",
        str(result_json_path),
    ]

    execution = harness_core.capture_execution(
        cmd=cmd,
        cwd=repo_path,
        timeout=timeout,
    )

    harness_core.save_execution_logs(execution, run_output_dir)

    # Parse driver output. The driver is contracted to emit JSON to
    # stdout regardless of outcome; a failure to parse is a harness
    # error.
    result: dict[str, Any]
    try:
        result = json.loads(execution.stdout) if execution.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        return harness_core.harness_error_verdict(
            tc_name,
            commit_meta,
            RuntimeError(f"driver emitted non-JSON stdout: {exc}"),
            ground_truth=ground_truth,
        )

    verdict = score_finemapping_verdict(ground_truth, result, execution)

    driver_path_info = harness_core.artifact_info(_DRIVER_PATH)
    outputs = {
        "driver_result_json": harness_core.artifact_info(result_json_path),
        "driver_path_sha256": driver_path_info.get("sha256"),
    }

    # Include the full driver result in the verdict's report_analysis so
    # auditors can reproduce the scoring decision from the verdict alone.
    report_analysis = {
        "driver_result": result,
        "driver_path": str(_DRIVER_PATH),
        "driver_sha256": driver_path_info.get("sha256"),
    }

    driver_path = (
        test_case_path / "ground_truth.txt" if test_case_path.is_dir() else test_case_path
    )

    verdict_doc = harness_core.build_verdict_doc(
        benchmark_name=BENCHMARK_NAME,
        benchmark_version=BENCHMARK_VERSION,
        commit_meta=commit_meta,
        test_case_name=tc_name,
        ground_truth=ground_truth,
        ground_truth_refs=GROUND_TRUTH_REFS,
        execution=execution,
        outputs=outputs,
        report_analysis=report_analysis,
        verdict=verdict,
        driver_path=driver_path,
        payload_path=payload_path,
    )

    harness_core.save_verdict(verdict_doc, run_output_dir)
    return verdict_doc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    harness_core.run_harness_main(
        benchmark_name=BENCHMARK_NAME,
        benchmark_version=BENCHMARK_VERSION,
        default_inputs_dir="finemapping",
        run_single_fn=run_single_finemapping,
        rubric_categories=RUBRIC_CATEGORIES,
        pass_categories=PASS_CATEGORIES,
        fail_categories=FAIL_CATEGORIES,
        ground_truth_refs=GROUND_TRUTH_REFS,
        category_legend=CATEGORY_LEGEND,
        description="ClawBio Fine-Mapping Benchmark Harness",
    )


if __name__ == "__main__":
    main()
