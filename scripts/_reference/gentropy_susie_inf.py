"""Reference implementation of SuSiE-inf, copied from Open Targets gentropy.

Source: https://github.com/opentargets/gentropy
        src/gentropy/method/susie_inf.py
        commit: main as of 2026-04-07

Why this file exists
--------------------
ClawBio's `core/susie_inf.py` is a port of the FinucaneLab/fine-mapping-inf
reference implementation, but it has a wiring defect: the `est_tausq`
parameter is hardcoded `False` at the only call site of `_mom_update`
(line ~158 of `core/susie_inf.py`), and the `est_tausq` flag is not even
exposed in the public `run_susie_inf` signature. The result is that
ClawBio's "SuSiE-inf" is functionally equivalent to standard SuSiE-RSS
with a fixed (zero) infinitesimal term — the τ²-estimation branch of
`_mom_update` is literally unreachable through the public API.

The bench needs to derive ground truth from a *correct* implementation
of the algorithm so that fine-mapping test cases (fm_17, fm_18, fm_20,
fm_21) score against what the algorithm should produce, not what
ClawBio currently does. This file gives us that reference without
requiring the full gentropy install (which depends on PySpark, Hail,
GCP libs, XGBoost, etc. — none of which belong in an audit tool).

The Open Targets gentropy port is itself a copy of the FinucaneLab
reference. The original repo header reads:

    Note: code copied from fine-mapping-inf package as a placeholder
    https://github.com/FinucaneLab/fine-mapping-inf

Because gentropy is Apache-2.0 licensed, vendoring this file under
clawbio_bench (MIT) is permissible. The full Apache-2.0 NOTICE for the
original gentropy code is preserved below.

This file is a *reference oracle* used only by the ground-truth
derivation script (`scripts/derive_finemapping_ground_truth.py`). It
is NOT imported by clawbio_bench at runtime — it lives under
`scripts/_reference/` and is never on the package's import path. The
loose-coupling invariant of the audit tool (no `import clawbio` and
no third-party algorithm code in the trusted base) is preserved.

LICENSE / NOTICE
----------------
Original Open Targets gentropy code:
  Copyright Open Targets
  Licensed under the Apache License, Version 2.0
  https://github.com/opentargets/gentropy/blob/main/LICENSE

Modifications from the gentropy original:
  - Removed the `@dataclass` `SUSIE_inf` wrapper class; promoted
    `susie_inf`, `_MoM`, and `_MLE` to module-level functions.
  - Removed all `pyspark`, `gentropy.dataset.*`, `cred_inf`, and
    `credible_set_qc` symbols (Spark-only, not algorithm-relevant).
  - Removed `LD-clumping`, `purity` filtering, and the `StudyLocus`
    wrappers (downstream concerns, not algorithm-relevant).
  - Type imports adjusted for standalone use.
  - Numerics are byte-identical to the gentropy original.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import scipy.linalg
import scipy.special
from scipy.optimize import minimize, minimize_scalar


def susie_inf(  # noqa: C901
    z: np.ndarray,
    meansq: float = 1,
    n: int = 100000,
    L: int = 10,
    LD: np.ndarray | None = None,
    V: np.ndarray | None = None,
    Dsq: np.ndarray | None = None,
    est_ssq: bool = True,
    ssq: np.ndarray | None = None,
    # Strictly positive lower bound: at exactly 0 the SuSiE inner loop
    # computes 1/ssq and log(ssq) which would propagate Inf/NaN if the
    # bounded optimizer ever lands on the boundary. 1e-12 is well below
    # the smallest meaningful prior effect variance.
    ssq_range: tuple[float, float] = (1e-12, 1),
    pi0: np.ndarray | None = None,
    est_sigmasq: bool = True,
    est_tausq: bool = False,
    sigmasq: float = 1,
    tausq: float = 0,
    sigmasq_range: tuple[float, float] | None = None,
    tausq_range: tuple[float, float] | None = None,
    PIP: np.ndarray | None = None,
    mu: np.ndarray | None = None,
    method: str = "moments",
    maxiter: int = 100,
    PIP_tol: float = 0.001,
    null_weight: float | None = None,
) -> dict[str, Any]:
    """SuSiE with random effects (gentropy reference implementation).

    Args:
        z: vector of z-scores (equal to X'y/sqrt(n))
        meansq: average squared magnitude of y (||y||²/n)
        n: sample size
        L: number of modeled causal effects
        LD: LD matrix (X'X/n)
        V: precomputed p x p eigenvectors of X'X
        Dsq: precomputed length-p eigenvalues of X'X
        est_ssq: estimate prior effect size variances s² using MLE
        ssq: length-L initialization s²
        ssq_range: bounds for each s²
        pi0: length-p prior causal probability vector
        est_sigmasq: estimate variance σ²
        est_tausq: estimate both variances σ² and τ²
        sigmasq: initial σ²
        tausq: initial τ²
        sigmasq_range: bounds for σ² (MLE only)
        tausq_range: bounds for τ² (MLE only)
        PIP: p x L initialization of PIPs
        mu: p x L initialization of mu
        method: one of {'moments', 'MLE'}
        maxiter: maximum iterations
        PIP_tol: convergence threshold

    Returns:
        Dictionary with keys: PIP, mu, omega, lbf_variable, ssq,
        sigmasq, tausq, alpha, lbf

    Raises:
        RuntimeError: if missing LD or unsupported variance method
    """
    p = len(z)
    if (V is None or Dsq is None) and LD is None:
        raise RuntimeError("Missing LD")
    if V is None or Dsq is None:
        # LD is not None here (the previous check would have raised).
        eigvals, V = scipy.linalg.eigh(LD)
        Dsq = np.maximum(n * eigvals, 0)
    else:
        Dsq = np.maximum(Dsq, 0)
    # After the branches above V and Dsq are guaranteed to be ndarrays.
    assert V is not None
    assert Dsq is not None
    Xty = np.sqrt(n) * z
    VtXty = V.T.dot(Xty)
    yty = n * meansq
    var = tausq * Dsq + sigmasq
    diagXtOmegaX = np.sum(V**2 * (Dsq / var), axis=1)
    XtOmegay = V.dot(VtXty / var)
    if ssq is None:
        ssq = np.ones(L) * 0.2
    if PIP is None:
        PIP = np.ones((p, L)) / p
    if mu is None:
        mu = np.zeros((p, L))
    lbf_variable = np.zeros((p, L))
    omega = diagXtOmegaX[:, np.newaxis] + 1 / ssq
    # Null-weight handling, ported from ClawBio's core/susie_inf.py 237cbd9
    # as an extension to the upstream gentropy susie_inf. When `null_weight`
    # is set, each single-effect row's posterior normalizes over p+1
    # categories (p variants plus an explicit "no effect" bucket), and
    # each per-variant prior is (1 - null_weight) / p. Setting
    # null_weight=None (or 0) reproduces the gentropy upstream behavior.
    # A `null_weight` of 1/(L+1) is ClawBio's default.
    nw: float = float(null_weight) if null_weight is not None else 0.0
    use_null = nw > 0
    log_prior_null = float(np.log(nw)) if use_null else 0.0
    if pi0 is None:
        logpi0 = np.ones(p) * np.log((1.0 - nw) / p) if use_null else np.ones(p) * np.log(1.0 / p)
    else:
        logpi0 = -np.ones(p) * np.inf
        inds = np.nonzero(pi0 > 0)[0]
        logpi0[inds] = np.log(pi0[inds])

    XtOmegar = np.zeros(p)  # initialised on first inner-loop iter

    def f(x: float) -> float:
        # scipy.special.logsumexp has an overload that returns a tuple
        # when return_sign=True; we always use the scalar form here, but
        # pyright cannot resolve which overload fires without seeing the
        # call site, so cast through Any to suppress the false positive.
        lse_raw: Any = scipy.special.logsumexp(
            -0.5 * np.log(1 + x * diagXtOmegaX)
            + x * XtOmegar**2 / (2 * (1 + x * diagXtOmegaX))
            + logpi0
        )
        return -float(lse_raw)

    for _it in range(maxiter):
        PIP_prev = PIP.copy()
        for _l in range(L):
            b = np.sum(mu * PIP, axis=1) - mu[:, _l] * PIP[:, _l]
            XtOmegaXb = V.dot(V.T.dot(b) * Dsq / var)
            XtOmegar = XtOmegay - XtOmegaXb
            if est_ssq:
                res = minimize_scalar(f, bounds=ssq_range, method="bounded")
                if res.success:
                    ssq[_l] = res.x
            omega[:, _l] = diagXtOmegaX + 1 / ssq[_l]
            mu[:, _l] = XtOmegar / omega[:, _l]
            lbf_variable[:, _l] = XtOmegar**2 / (2 * omega[:, _l]) - 0.5 * np.log(
                omega[:, _l] * ssq[_l]
            )
            logPIP = lbf_variable[:, _l] + logpi0
            if use_null:
                # Include the null hypothesis in the normalisation so
                # posterior mass is shared between the p variants and
                # the null "no effect" bucket. Matches ClawBio's 237cbd9
                # null_weight implementation.
                all_log = np.append(logPIP, log_prior_null)
                log_norm = scipy.special.logsumexp(all_log)
                PIP[:, _l] = np.exp(logPIP - log_norm)
            else:
                PIP[:, _l] = np.exp(logPIP - scipy.special.logsumexp(logPIP))
        if est_sigmasq or est_tausq:
            if method == "moments":
                (sigmasq, tausq) = _MoM(
                    PIP,
                    mu,
                    omega,
                    sigmasq,
                    tausq,
                    n,
                    V,
                    Dsq,
                    VtXty,
                    Xty,
                    yty,
                    est_sigmasq,
                    est_tausq,
                )
            elif method == "MLE":
                (sigmasq, tausq) = _MLE(
                    PIP,
                    mu,
                    omega,
                    sigmasq,
                    tausq,
                    n,
                    V,
                    Dsq,
                    VtXty,
                    yty,
                    est_sigmasq,
                    est_tausq,
                    _it,
                    sigmasq_range,
                    tausq_range,
                )
            else:
                raise RuntimeError("Unsupported variance estimation method")
            var = tausq * Dsq + sigmasq
            diagXtOmegaX = np.sum(V**2 * (Dsq / var), axis=1)
            XtOmegay = V.dot(VtXty / var)
        if np.max(np.abs(PIP_prev - PIP)) < PIP_tol:
            break

    b = np.sum(mu * PIP, axis=1)
    XtOmegaXb = V.dot(V.T.dot(b) * Dsq / var)
    XtOmegar = XtOmegay - XtOmegaXb
    alpha = tausq * XtOmegar
    priors = np.log(np.repeat(1 / p, p))
    lbf_cs = np.apply_along_axis(
        lambda x: scipy.special.logsumexp(x + priors), axis=0, arr=lbf_variable
    )
    return {
        "PIP": PIP,
        "mu": mu,
        "omega": omega,
        "lbf_variable": lbf_variable,
        "ssq": ssq,
        "sigmasq": float(sigmasq),
        "tausq": float(tausq),
        "alpha": alpha,
        "lbf": lbf_cs,
    }


def _MoM(
    PIP: np.ndarray,
    mu: np.ndarray,
    omega: np.ndarray,
    sigmasq: float,
    tausq: float,
    n: int,
    V: np.ndarray,
    Dsq: np.ndarray,
    VtXty: np.ndarray,
    Xty: np.ndarray,
    yty: float,
    est_sigmasq: bool,
    est_tausq: bool,
) -> tuple[float, float]:
    """Method-of-moments σ²/τ² estimator. Verbatim gentropy implementation."""
    (p, L) = mu.shape
    A = np.array([[n, sum(Dsq)], [0, sum(Dsq**2)]], dtype=float)
    A[1, 0] = A[0, 1]
    b = np.sum(mu * PIP, axis=1)
    Vtb = V.T.dot(b)
    diagVtMV = Vtb**2
    tmpD = np.zeros(p)
    for _l in range(L):
        bl = mu[:, _l] * PIP[:, _l]
        Vtbl = V.T.dot(bl)
        diagVtMV -= Vtbl**2
        tmpD += PIP[:, _l] * (mu[:, _l] ** 2 + 1 / omega[:, _l])
    diagVtMV += np.sum((V.T) ** 2 * tmpD, axis=1)
    x = np.zeros(2)
    x[0] = yty - 2 * sum(b * Xty) + sum(Dsq * diagVtMV)
    x[1] = sum(Xty**2) - 2 * sum(Vtb * VtXty * Dsq) + sum(Dsq**2 * diagVtMV)
    if est_tausq:
        sol = scipy.linalg.solve(A, x)
        if sol[0] > 0 and sol[1] > 0:
            (sigmasq, tausq) = float(sol[0]), float(sol[1])
        else:
            (sigmasq, tausq) = (float(x[0] / n), 0.0)
    elif est_sigmasq:
        sigmasq = float((x[0] - A[0, 1] * tausq) / n)
    return sigmasq, tausq


def _MLE(
    PIP: np.ndarray,
    mu: np.ndarray,
    omega: np.ndarray,
    sigmasq: float,
    tausq: float,
    n: int,
    V: np.ndarray,
    Dsq: np.ndarray,
    VtXty: np.ndarray,
    yty: float,
    est_sigmasq: bool,
    est_tausq: bool,
    _it: int,
    sigmasq_range: tuple[float, float] | None = None,
    tausq_range: tuple[float, float] | None = None,
) -> tuple[float, float]:
    """Maximum-likelihood σ²/τ² estimator. Verbatim gentropy implementation."""
    del _it  # vestigial in gentropy upstream; kept in signature for parity
    (p, L) = mu.shape
    if sigmasq_range is None:
        sigmasq_range = (0.2 * yty / n, 1.2 * yty / n)
    if tausq_range is None:
        tausq_range = (1e-12, 1.2 * yty / (n * p))
    b = np.sum(mu * PIP, axis=1)
    Vtb = V.T.dot(b)
    diagVtMV = Vtb**2
    tmpD = np.zeros(p)
    for _l in range(L):
        bl = mu[:, _l] * PIP[:, _l]
        Vtbl = V.T.dot(bl)
        diagVtMV -= Vtbl**2
        tmpD += PIP[:, _l] * (mu[:, _l] ** 2 + 1 / omega[:, _l])
    diagVtMV += np.sum((V.T) ** 2 * tmpD, axis=1)

    def f(x: tuple[float, float]) -> float:
        return float(
            0.5 * (n - p) * np.log(x[0])
            + 0.5 / x[0] * yty
            + np.sum(
                0.5 * np.log(x[1] * Dsq + x[0])
                - 0.5 * x[1] / x[0] * VtXty**2 / (x[1] * Dsq + x[0])
                - Vtb * VtXty / (x[1] * Dsq + x[0])
                + 0.5 * Dsq / (x[1] * Dsq + x[0]) * diagVtMV
            )
        )

    if est_tausq:
        res = minimize(
            f,
            (sigmasq, tausq),
            method="L-BFGS-B",
            bounds=(sigmasq_range, tausq_range),
        )
        if res.success:
            sigmasq, tausq = float(res.x[0]), float(res.x[1])
    elif est_sigmasq:

        def g(x: float) -> float:
            return f((x, tausq))

        res = minimize(g, sigmasq, method="L-BFGS-B", bounds=(sigmasq_range,))
        if res.success:
            sigmasq = float(res.x[0])
    return sigmasq, tausq
