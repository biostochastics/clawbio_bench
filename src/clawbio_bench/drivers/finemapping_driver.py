#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
finemapping_driver.py — subprocess shim for the clawbio-finemapping harness.

This file is a DATA FILE shipped with the clawbio_bench package. It is
never imported by clawbio_bench itself. It is launched as a subprocess
with its path passed to ``python`` so the skill-side imports run in a
separate interpreter and the "loose coupling" invariant holds: no code
in ``clawbio_bench.*`` touches the target repo.

Usage:
    python finemapping_driver.py \
        --skill-dir <ClawBio>/skills/fine-mapping \
        --inputs <test_case>/inputs.json \
        --output <out>/result.json

Exit codes:
    0  — driver ran to completion; result JSON written to --output and
         echoed to stdout. This is the ONLY path where the harness
         scores a verdict. Non-zero exit codes signal driver infra
         failure (missing skill dir, unloadable inputs, numpy missing)
         and the harness emits ``harness_error``.
    1  — driver infrastructure error (bad args, missing files, import
         failure of numpy/pandas).
    2  — skill module import failure (skill_dir is wrong or skill API
         has changed). The harness can still score this — it means the
         checked-out commit simply doesn't have the fine-mapping skill
         yet. Distinguished from 1 so the harness treats it as
         ``edge_handled`` rather than ``harness_error``.

The driver NEVER raises from its top level. Any unexpected condition
becomes a structured JSON result with ``status`` ∈
{``ok``, ``raised``, ``import_error``, ``driver_error``} and a
non-empty ``error`` payload.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any

DRIVER_VERSION = "0.1.0"


def _emit(result: dict, output_path: Path | None) -> None:
    """Write result to stdout and (if provided) an output file."""
    payload = json.dumps(result, indent=2, default=str)
    sys.stdout.write(payload)
    sys.stdout.write("\n")
    sys.stdout.flush()
    if output_path is not None:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload + "\n", encoding="utf-8")
        except OSError as exc:
            # Non-fatal: stdout is the authoritative channel. Record the
            # write failure in a side field so auditors can see it.
            sys.stderr.write(f"driver: failed to write {output_path}: {exc}\n")


def _driver_error(message: str, output_path: Path | None, exit_code: int = 1) -> int:
    """Emit a driver_error result and return the requested exit code."""
    _emit(
        {
            "driver_version": DRIVER_VERSION,
            "method": None,
            "status": "driver_error",
            "error": {"type": "DriverError", "message": message, "traceback": ""},
            "pips": None,
            "alpha": None,
            "mu": None,
            "mu2": None,
            "converged": None,
            "n_iter": None,
            "elbo_history": None,
            "credible_sets": None,
            "warnings": [],
        },
        output_path,
    )
    return exit_code


def _numeric_list(value: Any) -> list[float] | None:
    """Coerce numpy arrays / lists to plain JSON-serializable floats."""
    if value is None:
        return None
    try:
        return [float(x) for x in value]
    except (TypeError, ValueError):
        return None


def _numeric_matrix(value: Any) -> list[list[float]] | None:
    """Coerce 2-D numpy arrays to nested lists of floats."""
    if value is None:
        return None
    try:
        return [[float(x) for x in row] for row in value]
    except (TypeError, ValueError):
        return None


def _serialize_credsets(credsets: list[dict] | None) -> list[dict] | None:
    """Flatten credible-set dicts for JSON output.

    The skill returns numpy scalars inside the dicts; we must cast them
    or ``json.dumps`` raises on the first ``np.float64``.
    """
    if credsets is None:
        return None
    out: list[dict] = []
    for cs in credsets:
        variants = []
        for v in cs.get("variants", []):
            variants.append(
                {
                    "rsid": str(v.get("rsid", "")),
                    "chr": str(v.get("chr", "")),
                    "pos": None if v.get("pos") is None else int(v.get("pos")),
                    "z": float(v.get("z", 0.0)),
                    "pip": float(v.get("pip", 0.0)),
                    "alpha": float(v.get("alpha", 0.0)),
                }
            )
        purity = cs.get("purity")
        out.append(
            {
                "cs_id": str(cs.get("cs_id", "")),
                "signal_index": int(cs.get("signal_index", 0)),
                "size": int(cs.get("size", 0)),
                "coverage": float(cs.get("coverage", 0.0)),
                "lead_rsid": str(cs.get("lead_rsid", "")),
                "lead_alpha": float(cs.get("lead_alpha", 0.0)),
                "purity": None if purity is None else float(purity),
                "pure": bool(cs.get("pure", False)),
                "variants": variants,
            }
        )
    return out


def _run_abf(inputs: dict, mods: dict) -> dict:
    """Invoke ClawBio's ABF path and return a serialisable result dict."""
    import numpy as np
    import pandas as pd

    compute_abf = mods["abf"].compute_abf

    # Build a DataFrame the skill expects.
    df_data: dict[str, Any] = {"z": inputs["z"]}
    if inputs.get("se") is not None:
        df_data["se"] = inputs["se"]
    n_val = inputs.get("n")
    if n_val is not None:
        if isinstance(n_val, list):
            df_data["n"] = n_val
        else:
            df_data["n"] = [n_val] * len(inputs["z"])
    if inputs.get("rsids") is not None:
        df_data["rsid"] = inputs["rsids"]
    df = pd.DataFrame(df_data)

    warnings: list[str] = []
    # Surface numpy runtime warnings — many silent-failure modes manifest
    # as RuntimeWarning (divide-by-zero, invalid values). We want the
    # harness to see them.
    with np.errstate(all="warn"):
        import warnings as _w

        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            pips = compute_abf(df, w=float(inputs.get("w", 0.04)))
            for entry in caught:
                warnings.append(f"{entry.category.__name__}: {entry.message}")

    return {
        "method": "abf",
        "status": "ok",
        "error": None,
        "pips": _numeric_list(pips),
        "alpha": None,
        "mu": None,
        "mu2": None,
        "converged": None,
        "n_iter": None,
        "elbo_history": None,
        "credible_sets": None,
        "warnings": warnings,
    }


def _run_susie(inputs: dict, mods: dict) -> dict:
    """Invoke ClawBio's SuSiE path and return a serialisable result dict."""
    import numpy as np

    run_susie = mods["susie"].run_susie

    z = np.asarray(inputs["z"], dtype=float)
    R_in = inputs.get("R")
    R = np.eye(len(z)) if R_in is None else np.asarray(R_in, dtype=float)
    n = int(inputs.get("n", 5000))
    L = int(inputs.get("L", 10))
    w = float(inputs.get("w", 0.04))
    max_iter = int(inputs.get("max_iter", 100))
    tol = float(inputs.get("tol", 1e-3))
    min_purity = float(inputs.get("min_purity", 0.5))

    warnings: list[str] = []
    with np.errstate(all="warn"):
        import warnings as _w

        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            out = run_susie(
                z=z,
                R=R,
                n=n,
                L=L,
                w=w,
                max_iter=max_iter,
                tol=tol,
                min_purity=min_purity,
            )
            for entry in caught:
                warnings.append(f"{entry.category.__name__}: {entry.message}")

    return {
        "method": "susie",
        "status": "ok",
        "error": None,
        "pips": _numeric_list(out.get("pip")),
        "alpha": _numeric_matrix(out.get("alpha")),
        "mu": _numeric_matrix(out.get("mu")),
        "mu2": _numeric_matrix(out.get("mu2")),
        "converged": bool(out.get("converged", False))
        if out.get("converged") is not None
        else None,
        "n_iter": int(out.get("n_iter", 0)),
        "elbo_history": _numeric_list(out.get("elbo")),
        "credible_sets": None,
        "warnings": warnings,
    }


def _run_susie_inf(inputs: dict, mods: dict) -> dict:
    """Invoke ClawBio's SuSiE-inf path (Cui et al. 2023) and return a serialisable result."""
    import numpy as np

    if mods.get("susie_inf") is None:
        raise ImportError("core.susie_inf module not found in this ClawBio commit")

    run_susie_inf = mods["susie_inf"].run_susie_inf

    z = np.asarray(inputs["z"], dtype=float)
    R_in = inputs.get("R")
    R = np.eye(len(z)) if R_in is None else np.asarray(R_in, dtype=float)
    n = int(inputs.get("n", 5000))
    L = int(inputs.get("L", 10))
    w = float(inputs.get("w", 0.04))
    max_iter = int(inputs.get("max_iter", 100))
    tol = float(inputs.get("tol", 1e-3))

    # SuSiE-inf specific: initial tausq. When est_tausq is False in the
    # test case, we pass tausq=0 which makes the algorithm algebraically
    # identical to standard SuSiE (tau^2 stays at 0 through MoM when the
    # initial value is 0 and the locus is sparse).
    est_tausq = inputs.get("est_tausq", True)
    tausq_init = float(inputs.get("tausq", 0.0))
    if not est_tausq:
        # Force tau^2 = 0 to degenerate to standard SuSiE behavior.
        # The ClawBio implementation always runs MoM, but starting from
        # tausq=0 on a sparse locus keeps it near zero.
        tausq_init = 0.0

    meansq = float(inputs.get("meansq", 1.0))
    sigmasq = float(inputs.get("sigmasq", 1.0))
    est_sigmasq = inputs.get("est_sigmasq", True)
    est_ssq = inputs.get("est_ssq", True)
    ssq_init = float(inputs.get("ssq_init", w))

    warnings: list[str] = []
    with np.errstate(all="warn"):
        import warnings as _w

        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            out = run_susie_inf(
                z=z,
                R=R,
                n=n,
                L=L,
                meansq=meansq,
                ssq_init=ssq_init,
                est_ssq=est_ssq,
                est_sigmasq=est_sigmasq,
                sigmasq=sigmasq,
                tausq=tausq_init,
                max_iter=max_iter,
                tol=tol,
            )
            for entry in caught:
                warnings.append(f"{entry.category.__name__}: {entry.message}")

    return {
        "method": "susie_inf",
        "status": "ok",
        "error": None,
        "pips": _numeric_list(out.get("pip")),
        "alpha": _numeric_matrix(out.get("alpha")),
        "mu": _numeric_matrix(out.get("mu")),
        "mu2": None,
        "converged": bool(out.get("converged", False))
        if out.get("converged") is not None
        else None,
        "n_iter": int(out.get("n_iter", 0)),
        "elbo_history": None,
        "credible_sets": None,
        "tausq": float(out.get("tausq", 0.0)),
        "sigmasq": float(out.get("sigmasq", 1.0)),
        "warnings": warnings,
    }


def _run_credset_susie(inputs: dict, mods: dict) -> dict:
    """Directly exercise build_credible_sets_susie with caller-supplied alpha.

    Lets us construct adversarial credible-set shapes (e.g. the alpha
    matrix needed to trigger credset_pip_is_alpha_mismatch) without
    running the full SuSiE loop to produce them.
    """
    import numpy as np
    import pandas as pd

    build_credible_sets_susie = mods["credible_sets"].build_credible_sets_susie

    alpha = np.asarray(inputs["alpha"], dtype=float)
    R_in = inputs.get("R")
    R = None if R_in is None else np.asarray(R_in, dtype=float)
    coverage = float(inputs.get("coverage", 0.95))
    min_purity = float(inputs.get("min_purity", 0.5))

    p = alpha.shape[1]
    rsids = inputs.get("rsids") or [f"rs_syn_{i}" for i in range(p)]
    z_in = inputs.get("z") or [0.0] * p
    df = pd.DataFrame(
        {
            "rsid": rsids,
            "chr": ["1"] * p,
            "pos": list(range(1, p + 1)),
            "z": z_in,
        }
    )

    warnings: list[str] = []
    cs = build_credible_sets_susie(
        alpha=alpha, df=df, R=R, coverage=coverage, min_purity=min_purity
    )
    # Compute the true PIP from alpha so the harness can cross-check
    # credible-set "pip" fields without re-running the math.
    true_pip = 1.0 - np.prod(1.0 - alpha, axis=0)
    return {
        "method": "credset_susie",
        "status": "ok",
        "error": None,
        "pips": _numeric_list(true_pip),
        "alpha": _numeric_matrix(alpha),
        "mu": None,
        "mu2": None,
        "converged": None,
        "n_iter": None,
        "elbo_history": None,
        "credible_sets": _serialize_credsets(cs),
        "warnings": warnings,
    }


def _run_credset_abf(inputs: dict, mods: dict) -> dict:
    """Directly exercise build_credible_set_abf with caller-supplied PIPs."""
    import numpy as np
    import pandas as pd

    build_credible_set_abf = mods["credible_sets"].build_credible_set_abf

    pips = np.asarray(inputs["pips"], dtype=float)
    coverage = float(inputs.get("coverage", 0.95))
    p = len(pips)
    rsids = inputs.get("rsids") or [f"rs_syn_{i}" for i in range(p)]
    z_in = inputs.get("z") or [0.0] * p
    df = pd.DataFrame(
        {
            "rsid": rsids,
            "chr": ["1"] * p,
            "pos": list(range(1, p + 1)),
            "z": z_in,
        }
    )

    cs = build_credible_set_abf(pips, df, coverage=coverage)
    return {
        "method": "credset_abf",
        "status": "ok",
        "error": None,
        "pips": _numeric_list(pips),
        "alpha": None,
        "mu": None,
        "mu2": None,
        "converged": None,
        "n_iter": None,
        "elbo_history": None,
        "credible_sets": _serialize_credsets(cs),
        "warnings": [],
    }


METHOD_RUNNERS = {
    "abf": _run_abf,
    "susie": _run_susie,
    "susie_inf": _run_susie_inf,
    "credset_susie": _run_credset_susie,
    "credset_abf": _run_credset_abf,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="ClawBio fine-mapping driver shim")
    parser.add_argument("--skill-dir", required=True, type=Path)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--output", required=False, type=Path, default=None)
    args = parser.parse_args()

    # Load inputs JSON first so we can emit structured errors even when
    # the skill dir is bad.
    try:
        inputs = json.loads(args.inputs.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _driver_error(
            f"failed to load inputs JSON {args.inputs}: {exc}", args.output, exit_code=1
        )

    method = inputs.get("method")
    if method not in METHOD_RUNNERS:
        return _driver_error(
            f"unknown method {method!r}; expected one of {sorted(METHOD_RUNNERS)}",
            args.output,
            exit_code=1,
        )

    # Numpy/pandas are runtime dependencies for the driver. If they're
    # missing this is a driver-side infra problem, not a skill problem.
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
    except ImportError as exc:
        return _driver_error(
            f"numpy/pandas unavailable in driver interpreter: {exc}",
            args.output,
            exit_code=1,
        )

    # Skill dir validation. A missing directory is an ``edge_handled``
    # situation (commit is pre-fine-mapping), so we use exit code 2 to
    # distinguish from infra errors.
    if not args.skill_dir.exists():
        _emit(
            {
                "driver_version": DRIVER_VERSION,
                "method": method,
                "status": "import_error",
                "error": {
                    "type": "SkillDirectoryMissing",
                    "message": f"skill dir does not exist: {args.skill_dir}",
                    "traceback": "",
                },
                "pips": None,
                "alpha": None,
                "mu": None,
                "mu2": None,
                "converged": None,
                "n_iter": None,
                "elbo_history": None,
                "credible_sets": None,
                "warnings": [],
            },
            args.output,
        )
        return 2

    # Munge sys.path so ``from core.abf import ...`` resolves against
    # the target repo's skill directory. Insert at position 0 so the
    # target's modules win over any local stubs.
    sys.path.insert(0, str(args.skill_dir.resolve()))

    try:
        core_abf = __import__("core.abf", fromlist=["compute_abf"])
        core_susie = __import__("core.susie", fromlist=["run_susie"])
        core_credsets = __import__(
            "core.credible_sets",
            fromlist=["build_credible_sets_susie", "build_credible_set_abf"],
        )
        # SuSiE-inf is optional — only present in ClawBio commits after PR #105.
        # If absent, susie_inf method calls will fail with import_error status.
        try:
            core_susie_inf = __import__("core.susie_inf", fromlist=["run_susie_inf"])
        except ImportError:
            core_susie_inf = None
    except Exception as exc:  # noqa: BLE001 — we intentionally catch everything
        _emit(
            {
                "driver_version": DRIVER_VERSION,
                "method": method,
                "status": "import_error",
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
                "pips": None,
                "alpha": None,
                "mu": None,
                "mu2": None,
                "converged": None,
                "n_iter": None,
                "elbo_history": None,
                "credible_sets": None,
                "warnings": [],
            },
            args.output,
        )
        return 2

    mods = {
        "abf": core_abf,
        "susie": core_susie,
        "susie_inf": core_susie_inf,
        "credible_sets": core_credsets,
    }

    # Run the method under a blanket exception handler. A raise from the
    # skill becomes ``status="raised"`` and the harness scores it — it
    # is NOT a driver failure.
    try:
        result = METHOD_RUNNERS[method](inputs, mods)
    except Exception as exc:  # noqa: BLE001
        result = {
            "method": method,
            "status": "raised",
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
            "pips": None,
            "alpha": None,
            "mu": None,
            "mu2": None,
            "converged": None,
            "n_iter": None,
            "elbo_history": None,
            "credible_sets": None,
            "warnings": [],
        }

    result["driver_version"] = DRIVER_VERSION
    _emit(result, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
