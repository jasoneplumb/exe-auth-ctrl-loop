"""
Step 04: the Beta-mixture confidence-sequence gate.

Deterministic checks come first (closed forms, independent reference computation, boundary
behaviour, lifecycle integration). The simulations at the end are illustrative support for
the stated proposition, not a proof, and use fixed seeds with wide non-flaky margins.
"""

import math
import random
import unittest
from datetime import datetime, timedelta, timezone
from fractions import Fraction

from exe_auth_ctrl_loop import (
    AuthorityController,
    EvidenceSnapshot,
    EvidenceStore,
    PartitionKey,
    Policy,
    Proposal,
    Route,
    beta_mixture_lower_bound,
    log_mixture_martingale,
)
from exe_auth_ctrl_loop.authority import wilson_lower_bound
from exe_auth_ctrl_loop.sequential import crosses

# Reference vectors: (successes, failures, alpha, lower bound). Generated once by the
# implementation and verified below by an independent exact computation, so a change
# in either the bisection or the lgamma path shows up as a mismatch.
VECTORS = [
    (0, 0, 0.05, 0.0000000000),
    (1, 0, 0.05, 0.0250000000),
    (0, 10, 0.05, 0.0000000000),
    (10, 0, 0.05, 0.6230126490),
    (20, 0, 0.05, 0.7759938880),
    (40, 0, 0.05, 0.8733738782),
    (60, 0, 0.05, 0.9106264163),
    (99, 1, 0.05, 0.9159776855),
    (30, 25, 0.05, 0.3327577461),
    (1000, 10, 0.05, 0.9740847543),
    (5, 5, 0.05, 0.1175629241),
    (40, 10, 0.05, 0.5858137807),
    (60, 0, 0.01, 0.8865245045),
    (60, 0, 0.1, 0.9212073855),
    (100000, 1000, 0.05, 0.9887198075),
]


def exact_log_mixture(successes: int, failures: int, p: float) -> float:
    """
    Independent route to log M_t(p) for the Jeffreys prior, avoiding lgamma entirely:
    B(1/2+S, 1/2+F) / B(1/2, 1/2) = prod_{i<S}(1/2+i) prod_{j<F}(1/2+j) / prod_{k<S+F}(1+k)
    as an exact rational up to a few thousand trials, and as a sum of logs of the same
    factors beyond that, where exact bignum arithmetic is impractical.
    """
    total = successes + failures
    if total <= 2000:
        half = Fraction(1, 2)
        ratio = Fraction(1)
        for i in range(successes):
            ratio *= half + i
        for j in range(failures):
            ratio *= half + j
        for k in range(total):
            ratio /= 1 + k
        log_ratio = math.log(ratio.numerator) - math.log(ratio.denominator)
    else:
        log_ratio = (
            sum(math.log(0.5 + i) for i in range(successes))
            + sum(math.log(0.5 + j) for j in range(failures))
            - sum(math.log(1 + k) for k in range(total))
        )
    return log_ratio - successes * math.log(p) - failures * math.log1p(-p)


class ReferenceVectorTests(unittest.TestCase):
    def test_vectors_reproduce(self):
        for s, f, alpha, expected in VECTORS:
            self.assertAlmostEqual(beta_mixture_lower_bound(s, f, alpha), expected, places=9)

    def test_vectors_sit_on_the_boundary_by_independent_computation(self):
        for s, f, alpha, lower in VECTORS:
            if s == 0:
                continue
            threshold = math.log(1 / alpha)
            self.assertAlmostEqual(exact_log_mixture(s, f, lower), threshold, delta=1e-6)
            self.assertGreater(exact_log_mixture(s, f, lower * (1 - 1e-6)), threshold)
            self.assertLess(exact_log_mixture(s, f, lower * (1 + 1e-6)), threshold)

    def test_single_success_closed_form(self):
        # S=1, F=0: M(p) = Gamma(3/2)/Gamma(1/2) / p = 1/(2p); M >= 1/alpha iff p <= alpha/2
        for alpha in (0.01, 0.05, 0.2):
            self.assertAlmostEqual(beta_mixture_lower_bound(1, 0, alpha), alpha / 2, places=12)

    def test_lgamma_path_matches_exact_path(self):
        for s, f in [(3, 2), (60, 0), (99, 1), (30, 25), (1000, 10)]:
            for p in (0.1, 0.5, 0.9, 0.999):
                self.assertAlmostEqual(
                    log_mixture_martingale(s, f, p), exact_log_mixture(s, f, p), delta=1e-7
                )


class BoundaryTests(unittest.TestCase):
    def test_empty_sample_is_zero(self):
        self.assertEqual(beta_mixture_lower_bound(0, 0, 0.05), 0.0)
        self.assertEqual(log_mixture_martingale(0, 0, 0.3), 0.0)

    def test_no_successes_is_zero(self):
        for f in (1, 10, 10_000):
            self.assertEqual(beta_mixture_lower_bound(0, f, 0.05), 0.0)

    def test_extremes_do_not_overflow(self):
        self.assertTrue(math.isfinite(beta_mixture_lower_bound(10**7, 0, 0.05)))
        self.assertTrue(math.isfinite(beta_mixture_lower_bound(10**7, 10**7, 0.05)))
        self.assertTrue(math.isfinite(log_mixture_martingale(10**7, 3, 1 - 1e-12)))
        self.assertEqual(log_mixture_martingale(5, 0, 0.0), math.inf)
        self.assertEqual(log_mixture_martingale(0, 5, 1.0), math.inf)

    def test_bound_is_below_empirical_rate_and_monotone(self):
        previous = 0.0
        for n in range(1, 200):
            lower = beta_mixture_lower_bound(n, 0, 0.05)
            self.assertLess(lower, 1.0)
            self.assertGreaterEqual(lower, previous)
            previous = lower
        self.assertLess(beta_mixture_lower_bound(50, 50, 0.05), 0.5)
        self.assertLess(beta_mixture_lower_bound(60, 0, 0.01), beta_mixture_lower_bound(60, 0, 0.1))

    def test_more_conservative_than_fixed_sample_wilson(self):
        for s, f in [(40, 0), (60, 0), (99, 1), (30, 25), (1000, 10)]:
            self.assertLess(beta_mixture_lower_bound(s, f, 0.05), wilson_lower_bound(s, s + f))

    def test_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            beta_mixture_lower_bound(1, 0, 0.0)
        with self.assertRaises(ValueError):
            beta_mixture_lower_bound(1, 0, 1.0)
        with self.assertRaises(ValueError):
            log_mixture_martingale(-1, 0, 0.5)
        with self.assertRaises(ValueError):
            log_mixture_martingale(1, 0, 1.5)

    def test_crosses_agrees_with_lower_bound(self):
        rng = random.Random(4)
        for _ in range(300):
            s, f = rng.randint(0, 80), rng.randint(0, 20)
            r = rng.choice((0.5, 0.8, 0.9, 0.95))
            self.assertEqual(crosses(s, f, r, 0.05), beta_mixture_lower_bound(s, f, 0.05) >= r)


class PolicyIntegrationTests(unittest.TestCase):
    def policy(self, **kw):
        return Policy("pol1", {"low": .90}, 30, timedelta(days=30), 0.0, **kw)

    def test_default_policy_uses_the_sequential_bound(self):
        policy = self.policy()
        self.assertEqual(policy.estimator, "beta_mixture")
        self.assertAlmostEqual(policy.lower_bound(60, 0), 0.9106264163, places=9)
        self.assertLess(policy.lower_bound(40, 0), 0.90)

    def test_legacy_wilson_is_opt_in_only(self):
        legacy = self.policy(estimator="wilson_legacy")
        self.assertEqual(legacy.lower_bound(40, 0), wilson_lower_bound(40, 40, 1.96))
        with self.assertRaises(ValueError):
            self.policy(estimator="bootstrap")

    def test_alpha_is_validated_and_part_of_policy(self):
        with self.assertRaises(ValueError):
            self.policy(alpha=0.0)
        with self.assertRaises(ValueError):
            self.policy(alpha=1.0)
        strict, default = self.policy(alpha=0.01), self.policy()
        self.assertLess(strict.lower_bound(60, 0), default.lower_bound(60, 0))

    def test_n_min_still_floors_even_when_bound_would_pass(self):
        # 100/100 at alpha=.05 clears .90, but n_min=200 must still block in the controller
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        key = PartitionKey("o", "m", "p", "a", "m", "e", "t", "pol1", "env", "c", "b", "low")
        store = EvidenceStore()
        store.put(EvidenceSnapshot("e", 1, key, 100, 0, now, now))
        proposal = Proposal("p", "i", "t", {}, .9, frozenset(), key, "o")
        policy = Policy("pol1", {"low": .90}, 200, timedelta(days=30), 0.0)
        controller = AuthorityController(store, policy, random.Random(1), lambda: now)
        decision = controller.evaluate(proposal)
        self.assertEqual(decision.route, Route.HUMAN_APPROVAL)
        self.assertIn("EVIDENCE_IMMATURE", decision.reason_codes)
        self.assertGreaterEqual(decision.lower_bound, .90)


def ever_crosses(rng, p, required, alpha, horizon, use_wilson=False, z=1.96):
    """Continuous monitoring: one look after every trial; True if the bound ever clears r."""
    s = f = 0
    for _ in range(horizon):
        if rng.random() < p:
            s += 1
        else:
            f += 1
        if use_wilson:
            if wilson_lower_bound(s, s + f, z) >= required:
                return True
        elif crosses(s, f, required, alpha):
            return True
    return False


class IllustrativeSimulationTests(unittest.TestCase):
    """
    Illustrative only. These support the proposition in docs/v2/statistical-method.md
    under its assumptions (i.i.d. Bernoulli within an epoch); they do not establish it.
    Margins are several standard errors wide so that seeded runs never flap.
    """

    RUNS = 1500
    HORIZON = 400

    def test_false_authorization_rate_under_optional_stopping_stays_within_alpha(self):
        alpha, required = 0.05, 0.90
        for seed, p in ((11, 0.90), (12, 0.88), (13, 0.85)):
            rng = random.Random(seed)
            hits = sum(
                ever_crosses(rng, p, required, alpha, self.HORIZON) for _ in range(self.RUNS)
            )
            # true rate <= alpha; 0.08 is ~5 standard errors above 0.05 at 1500 runs
            self.assertLessEqual(hits / self.RUNS, 0.08, (p, hits))

    def test_fixed_sample_wilson_exceeds_its_nominal_rate_when_read_repeatedly(self):
        # At the boundary p = r a one-sided z=1.96 interval is nominally 2.5% per look.
        # Monitored after every trial for 400 trials, seeded runs reach 11-14%, and the
        # rate keeps growing with the horizon. 0.05 is ~6 standard errors below that.
        rng = random.Random(21)
        hits = sum(
            ever_crosses(rng, 0.90, 0.90, 0.05, self.HORIZON, use_wilson=True)
            for _ in range(self.RUNS)
        )
        self.assertGreater(hits / self.RUNS, 0.05)

    def test_qualification_happens_when_the_rate_is_truly_above_threshold(self):
        rng = random.Random(31)
        hits = sum(ever_crosses(rng, 0.97, 0.90, 0.05, self.HORIZON) for _ in range(300))
        self.assertGreater(hits / 300, 0.9)


if __name__ == "__main__":
    unittest.main()
