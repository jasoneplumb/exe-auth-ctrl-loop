"""
v3 Step 09, tier one: the estimator-level driver (experiments/statistics.py). Checks that it
reads the shipped bound exactly, times labels the way the shipped harness does, allocates
alpha as each rule says, labels assumption violations (criterion 5), and covers the
pre-registered grid of plan Step 09 (criterion 2). Criterion 3's lemma is checked here
against the implementation.
"""

import math
import random
import unittest
from dataclasses import replace

from exe_auth_ctrl_loop.sequential import beta_mixture_lower_bound
from experiments.statistics import (
    SPENDING,
    Cell,
    _chunk,
    expand,
    run_cells,
    run_unit,
    success_threshold,
    summarize,
)

BASE = Cell("test", "test/base", p=1.0)


def false_authorization(cell: Cell, sequences: int) -> float:
    _, _, fa, _ = _chunk((replace(cell, sequences=sequences), 0, sequences))
    return sum(fa) / sequences


class BoundTests(unittest.TestCase):
    def test_the_bound_is_nondecreasing_in_successes_at_fixed_n(self):
        # statistical-method.md §11.1, on the computed values
        for alpha in (0.05, 0.01):
            for n in range(1, 401):
                previous = -1.0
                for s in range(n + 1):
                    bound = beta_mixture_lower_bound(s, n - s, alpha)
                    self.assertGreaterEqual(bound, previous, (alpha, n, s))
                    previous = bound

    def test_the_threshold_table_matches_direct_evaluation(self):
        alphas = (0.05, 0.05 / 100, 0.05 * SPENDING, 0.05 * SPENDING / 100**2)
        for alpha in alphas:
            table = success_threshold(alpha, 0.90, 400)
            for n, s_min in enumerate(table):
                with self.subTest(alpha=alpha, n=n):
                    if s_min <= n:
                        bound = beta_mixture_lower_bound(s_min, n - s_min, alpha)
                        self.assertGreaterEqual(bound, .9)
                    if s_min >= 1:
                        below = s_min - 1
                        self.assertLess(beta_mixture_lower_bound(below, n - below, alpha), .9)
        # the documented qualification point of a perfect record (§9): 53 trials
        self.assertEqual(success_threshold(0.05, 0.90, 400)[53], 53)
        self.assertEqual(success_threshold(0.05, 0.90, 400)[52], 53)


class TimingTests(unittest.TestCase):
    """Deterministic cases (p = 1) pin when the gate sees each label."""

    def first(self, **fields) -> int | None:
        return run_unit(replace(BASE, **fields), 1.0, 0.05, random.Random(0))

    def test_a_label_without_delay_is_seen_at_the_next_step(self):
        # trial i's label is visible from step i + 1, so at step t, t trials are labelled
        self.assertEqual(self.first(), 53)
        self.assertEqual(self.first(pending="pessimistic"), 53)

    def test_positive_delay_costs_the_pessimistic_rule_more(self):
        # trial i is seen from step i + 10, so at step t there are t - 9 labelled trials
        self.assertEqual(self.first(positive_delay=10), 62)
        pessimistic = self.first(positive_delay=10, pending="pessimistic")
        self.assertGreater(pessimistic, 62)  # n = t frozen, 9 of them still failures

    def test_block_counting_freezes_one_trial_per_block(self):
        # counting trials at 0, 5, ..., the 53rd is frozen at 260 and seen from 261
        self.assertEqual(self.first(block_size=5, counting="block"), 261)

    def test_a_withheld_label_is_censored_after_the_deadline(self):
        withheld = replace(BASE, censor_target="positives", censor_action="withhold",
                           deadline=5, horizon=100)
        self.assertIsNone(run_unit(withheld, 1.0, 0.05, random.Random(0)))  # all failures


class AllocationTests(unittest.TestCase):
    def test_each_rule_allocates_alpha_as_stated(self):
        cell = replace(BASE, partitions=10, partition_alpha="bonferroni")
        self.assertAlmostEqual(cell.unit_alpha(3, 1), 0.005)
        online = replace(BASE, partitions=100, partition_alpha="online")
        self.assertAlmostEqual(online.unit_alpha(1, 1), 0.05 * 6 / math.pi**2)
        self.assertLess(sum(online.unit_alpha(k, 1) for k in range(1, 10**5)), 0.05)
        lineage = replace(BASE, epochs=10, epoch_alpha="lineage")
        self.assertLess(sum(lineage.unit_alpha(1, e) for e in range(1, 11)), 0.05)
        self.assertEqual(replace(BASE, epochs=10).unit_alpha(1, 7), 0.05)


class GridTests(unittest.TestCase):
    """Criterion 2: the five families with plan Step 09's variants, at 1000 sequences."""

    def test_the_families_cover_the_pre_registered_variants(self):
        cells = expand()
        by_family: dict[str, list[Cell]] = {}
        for cell in cells:
            by_family.setdefault(cell.family, []).append(cell)
        pre_registered = {f for f, cs in by_family.items() if cs[0].pre_registered}
        self.assertEqual(pre_registered, {"delayed_negatives", "adversarial_censoring",
                                          "correlated_failures", "repeated_epochs",
                                          "error_budget"})
        # one family added at Step 09 for O-3's cost, marked as such in every summary
        self.assertEqual(set(by_family) - pre_registered, {"symmetric_delays"})
        self.assertEqual({(c.negative_delay, c.positive_delay)
                          for c in by_family["symmetric_delays"]}, {(2, 2), (10, 10), (50, 50)})
        self.assertTrue(all(c.sequences >= 1000 for c in cells))

        def values(family, name):
            return {getattr(c, name) for c in by_family[family]}

        self.assertEqual(values("delayed_negatives", "negative_delay"), {0, 10, 50, 200})
        self.assertEqual(values("delayed_negatives", "positive_delay"), {0})
        self.assertEqual(values("delayed_negatives", "pending"), {"deadline", "pessimistic"})
        self.assertEqual(values("adversarial_censoring", "censor_target"),
                         {"negatives", "positives", "random"})
        self.assertEqual(values("adversarial_censoring", "censor_action"),
                         {"inconclusive", "withhold"})
        self.assertEqual(values("correlated_failures", "block_size"), {1, 5, 20})
        self.assertEqual(values("correlated_failures", "rho"), {0.0, 0.1, 0.3, 0.6})
        self.assertEqual(values("repeated_epochs", "epochs"), {1, 3, 10})
        self.assertEqual(values("error_budget", "partitions"), {1, 10, 100})
        self.assertIn("online", values("error_budget", "partition_alpha"))
        self.assertIn("per_partition", values("error_budget", "partition_alpha"))
        # "p just below r" for the epoch and partition families
        self.assertTrue(all(c.p < c.r for f in ("repeated_epochs", "error_budget")
                            for c in by_family[f]))
        self.assertEqual(len({c.cell_id for c in cells}), len(cells))

    def test_every_violation_is_labelled(self):
        # criterion 5: the rule is per cell, and a covered cell names none
        labels = {c.cell_id: c.assumptions_violated() for c in expand()}
        self.assertTrue(labels["delayed_negatives/negative_delay=50,pending=deadline"])
        self.assertFalse(labels["delayed_negatives/negative_delay=50,pending=pessimistic"])
        self.assertFalse(labels["delayed_negatives/negative_delay=0,pending=deadline"])
        self.assertTrue(labels[
            "adversarial_censoring/censor_target=negatives,censor_action=withhold,"
            "pending=deadline"])
        self.assertTrue(labels["correlated_failures/block_size=20,rho=0.6,counting=trial"])
        self.assertFalse(labels["correlated_failures/block_size=20,rho=0.6,counting=block"])
        self.assertFalse(labels["correlated_failures/block_size=1,rho=0.6,counting=trial"])
        summary = summarize(BASE, [False, True], [[None], [3]])
        self.assertTrue(summary["assumptions_hold"])
        self.assertIn("claim_scope", summary)


class BehaviourTests(unittest.TestCase):
    """Small-sample sanity on the directions the full runs measure precisely."""

    def test_late_negatives_inflate_false_authorization_only_under_the_deadline_rule(self):
        late = replace(BASE, p=0.9, negative_delay=50)
        deadline = false_authorization(late, 300)
        pessimistic = false_authorization(replace(late, pending="pessimistic"), 300)
        self.assertGreater(deadline, 0.2)  # L2 measured about 0.39
        self.assertLess(pessimistic, 0.05)

    def test_results_do_not_depend_on_the_number_of_workers(self):
        cells = [replace(c, sequences=40, horizon=120) for c in expand()[:3]]
        self.assertEqual(run_cells(cells, workers=1, chunk=15),
                         run_cells(cells, workers=2, chunk=7))


if __name__ == "__main__":
    unittest.main()
