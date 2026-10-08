"""
Intent: Give the authority gate a lower bound that stays valid when it is checked after
        every trial, which the fixed-z Wilson bound does not
Context: The controller re-evaluates a partition's evidence before each operation, so the
        bound is read at a data-dependent stopping time. A fixed-sample interval read that
        way exceeds its nominal error rate (optional stopping). docs/v2/statistical-method.md
        states the claim, the theorem, and the assumptions this module relies on.
Pattern: Beta-mixture test supermartingale (Robbins 1970; Kaufmann and Koolen 2021). The
        confidence sequence is the set of p whose mixture likelihood ratio has not crossed
        1/alpha; Ville's inequality bounds the chance it ever crosses under the true p.
Future: Pure math in float64 via lgamma, no dependencies. Valid for i.i.d. Bernoulli
        labels within an epoch; it is not a defence against a strategically adaptive agent
        and must not be described as one.
"""

from __future__ import annotations

import math

JEFFREYS = 0.5
_BISECTION_STEPS = 64


def log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def log_mixture_martingale(
    successes: int,
    failures: int,
    p: float,
    prior_a: float = JEFFREYS,
    prior_b: float = JEFFREYS,
) -> float:
    """
    intent: log M_t(p) = log [ B(a+S, b+F) / B(a, b) ] - S log p - F log(1-p)
    context: Under i.i.d. Bernoulli(p), M_t(p) is a nonnegative martingale with M_0 = 1,
             so Pr(sup_t M_t(p) >= 1/alpha) <= alpha (Ville). Computed in log space so
             large n and p near 0 or 1 cannot overflow.
    effect: +inf when p is 0 with successes, or 1 with failures: those p are excluded.
    """
    if successes < 0 or failures < 0:
        raise ValueError("counts must be non-negative")
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must be in [0, 1]")
    if prior_a <= 0 or prior_b <= 0:
        raise ValueError("prior parameters must be positive")
    mixture = log_beta(prior_a + successes, prior_b + failures) - log_beta(prior_a, prior_b)
    if successes and p == 0.0:
        return math.inf
    if failures and p == 1.0:
        return math.inf
    likelihood = 0.0
    if successes:
        likelihood += successes * math.log(p)
    if failures:
        likelihood += failures * math.log1p(-p)
    return mixture - likelihood


def beta_mixture_lower_bound(
    successes: int,
    failures: int,
    alpha: float,
    prior_a: float = JEFFREYS,
    prior_b: float = JEFFREYS,
) -> float:
    """
    intent: The lower endpoint of the (1-alpha) confidence sequence for p after S, F
    method: log M_t(p) is convex in p with its minimum at S/n, so the set where it stays
            below log(1/alpha) is an interval. Bisect on (0, S/n] for the left endpoint.
    effect: Zero for an empty record and whenever there are no successes, so an unknown or
            all-failure partition cannot clear any positive threshold. Deterministic: the
            same inputs always yield the same float.
    constraint: Pr_p(exists t: L_t > p) <= alpha for every fixed p, under the assumptions
                in docs/v2/statistical-method.md. Nothing more is claimed.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    total = successes + failures
    if total <= 0 or successes <= 0:
        return 0.0
    threshold = math.log(1.0 / alpha)
    hi = successes / total
    if log_mixture_martingale(successes, failures, hi, prior_a, prior_b) >= threshold:
        # Cannot happen: at the MLE the mixture likelihood is at most the maximum
        # likelihood, so log M <= 0 < threshold. Guarded so a numerical surprise fails
        # closed rather than returning the MLE.
        return 0.0
    lo = 0.0
    for _ in range(_BISECTION_STEPS):
        mid = (lo + hi) / 2
        if log_mixture_martingale(successes, failures, mid, prior_a, prior_b) >= threshold:
            lo = mid
        else:
            hi = mid
    return max(0.0, lo)


def crosses(
    successes: int,
    failures: int,
    required: float,
    alpha: float,
    prior_a: float = JEFFREYS,
    prior_b: float = JEFFREYS,
) -> bool:
    """
    intent: Answer "is L_t >= required" without computing L_t, for simulations
    method: L_t >= r exactly when r is at or below the confidence sequence: the martingale
            at r has reached 1/alpha and r is not above the empirical rate
    """
    if successes + failures <= 0:
        return False
    if required > successes / (successes + failures):
        return False
    return log_mixture_martingale(successes, failures, required, prior_a, prior_b) >= math.log(
        1.0 / alpha
    )
