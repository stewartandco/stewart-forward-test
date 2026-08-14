"""Pure stdlib statistics for the gauntlet battery.

Population moments throughout (m2 = sum((x-mean)^2)/n); skew = m3/m2^1.5,
kurt = m4/m2^2 (non-excess). Deterministic: bootstrap takes an explicit seed.
"""
from __future__ import annotations

import math
import random

EULER_GAMMA = 0.5772156649015329


def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# Acklam's rational approximation to the inverse normal CDF (|err| < 1.15e-9)
_A = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
      1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
_B = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
      6.680131188771972e+01, -1.328068155288572e+01]
_C = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
      -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
_D = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
      3.754408661907416e+00]
_P_LOW, _P_HIGH = 0.02425, 1 - 0.02425


def inv_normal_cdf(p: float) -> float:
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    if p < _P_LOW:
        q = math.sqrt(-2 * math.log(p))
        return (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
               ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1)
    if p > _P_HIGH:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
                ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
           (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1)


def moments(xs: list[float]) -> tuple[float, float, float, float]:
    """(mean, std, skew, kurt) — population moments, kurt non-excess.
    Flat series -> std 0, skew 0, kurt 0 by convention."""
    n = len(xs)
    mean = sum(xs) / n
    m2 = sum((x - mean) ** 2 for x in xs) / n
    if m2 == 0:
        return mean, 0.0, 0.0, 0.0
    m3 = sum((x - mean) ** 3 for x in xs) / n
    m4 = sum((x - mean) ** 4 for x in xs) / n
    return mean, math.sqrt(m2), m3 / m2 ** 1.5, m4 / m2 ** 2


def sharpe(xs: list[float]) -> float:
    """Per-period Sharpe (mean/std, population std); 0 for flat series."""
    mean, std, _, _ = moments(xs)
    return mean / std if std > 0 else 0.0


def percentile(sorted_xs: list[float], q: float) -> float:
    """Linear interpolation on a pre-sorted list; q in [0, 1]."""
    if not sorted_xs:
        raise ValueError("empty list")
    idx = q * (len(sorted_xs) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_xs[lo]
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (idx - lo)
