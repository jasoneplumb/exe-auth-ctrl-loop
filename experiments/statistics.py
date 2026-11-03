"""
Intent: Measure false authorization under the five scenario families of v3 plan Step 09, at
        enough sequences per cell (>= 1000) to tell a rate near alpha from one above it
Context: Tier one of the owner's two-tier decision (issue #40). This driver replays one
        partition's evidence stream at the level the gate reads it: one trial per step, a
        label that arrives after a delay or never, a counting rule, and the shipped
        Beta-mixture bound. Tier two, the shipped-loop harness in simulate.py, drives the
        real loop and is checked against this driver on a subsample.
Pattern: The bound is the shipped one (sequential.beta_mixture_lower_bound), read through a
        per-n success threshold. The threshold is exact because the bound is nondecreasing
        in successes at fixed n (docs/v2/statistical-method.md §11.1), and a test checks the
        table against direct evaluation. Everything else here is the scenario, stated as
        code so the counting rules are reviewable.
Future: Candidate rules (pessimistic pending, block counting, alpha spending) exist only
        here until the owner adopts one (O-3, O-4, O-8); an adopted rule becomes a Policy
        field in src/ and is then measured through the shipped loop as well.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import random
import statistics
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from exe_auth_ctrl_loop.sequential import beta_mixture_lower_bound

FAMILIES = Path(__file__).resolve().parent / "families.json"
SPENDING = 6 / math.pi**2  # sum over k >= 1 of SPENDING / k^2 is 1


@dataclass(frozen=True)
class Cell:
    """
    One scenario cell. Field names are the vocabulary of experiments/families.json.

    Truth and timing: `p` is every trial's verdict rate (within a block, see `rho`); a
    NEGATIVE verdict's label arrives `negative_delay` steps after freezing and a POSITIVE
    one's `positive_delay` steps after; a label later than `deadline` steps, or never sent,
    is censored at the first step past the deadline and counts as a failure.
    """

    family: str
    cell_id: str
    p: float
    # False for a family added after Step 00's pre-registration; reported, never hidden
    pre_registered: bool = True
    r: float = 0.90
    alpha: float = 0.05
    n_min: int = 30
    horizon: int = 400
    sequences: int = 1000
    cost_p: float = 0.97
    deadline: int = 250
    # counting rule for pending trials: "deadline" (shipped: outside n until resolved or
    # censored) or "pessimistic" (O-3: in n from freezing, a failure until a POSITIVE)
    pending: str = "deadline"
    negative_delay: int = 0
    positive_delay: int = 0
    # adversarial censoring: which verdicts the labeller interferes with, and how
    censor_target: str = "none"  # none | negatives | positives | random
    censor_action: str = "none"  # none | inconclusive | withhold
    # correlated failures: blocks of `block_size` trials share a rate drawn from a Beta
    # with mean p and intra-block correlation rho; "block" counting freezes one trial per
    # block as the counting trial (the others are not trials)
    block_size: int = 1
    rho: float = 0.0
    counting: str = "trial"  # trial | block
    # repeated qualification epochs of one lineage, and the alpha each epoch gets
    epochs: int = 1
    epoch_alpha: str = "per_epoch"  # per_epoch | lineage
    # partitions registered over time, and the alpha each gets
    partitions: int = 1
    partition_alpha: str = "per_partition"  # per_partition | bonferroni | online

    def unit_alpha(self, partition: int, epoch: int) -> float:
        """The alpha of the given partition's given epoch (both counted from 1)."""
        alpha = self.alpha
        if self.partition_alpha == "bonferroni":
            alpha /= self.partitions
        elif self.partition_alpha == "online":
            alpha *= SPENDING / partition**2
        if self.epoch_alpha == "lineage":
            alpha *= SPENDING / epoch**2
        return alpha

    def method(self) -> str:
        """The counting rule and alpha allocation, in words, for the summary."""
        parts = [
            "pending trials outside n until resolved or censored (shipped)"
            if self.pending == "deadline"
            else "pending trials counted as failures from freezing (O-3 candidate)",
            "every trial counted" if self.counting == "trial"
            else f"one counting trial per block of {self.block_size}",
        ]
        if self.epochs > 1 or self.epoch_alpha != "per_epoch":
            parts.append("alpha per epoch (shipped)" if self.epoch_alpha == "per_epoch"
                         else "lineage alpha spent as 6/(pi^2 e^2) over epochs (O-8 candidate)")
        if self.partitions > 1 or self.partition_alpha != "per_partition":
            parts.append({
                "per_partition": "alpha per partition (shipped)",
                "bonferroni": f"alpha/K with declared K = {self.partitions} (O-4 alternative)",
                "online": "alpha spent as 6/(pi^2 k^2) by registration order (O-4 candidate)",
            }[self.partition_alpha])
        return "; ".join(parts)

    def assumptions_violated(self) -> list[str]:
        """
        Which of A1-A5 (statistical-method.md §3) the cell breaks for the rule it uses,
        so no summary reads as covered by the proposition when it is not (criterion 5)
        """
        violated = []
        if self.pending == "deadline" and self.negative_delay != self.positive_delay:
            violated.append("A1: under the deadline-only count, labels enter n in an order "
                            "that depends on the label (label-dependent latency)")
        if self.pending == "deadline" and self.censor_action == "withhold":
            violated.append("A4: withheld labels stay outside n until the deadline, so "
                            "non-response can raise the bound inside the window (L8)")
        if self.block_size > 1 and self.rho > 0 and self.counting == "trial":
            violated.append("A1: trials in a block share a rate, so counted labels are "
                            "not independent")
        return violated

    def claim_scope(self) -> str:
        """What the alpha in force covers, beside the A1-A5 check."""
        if self.epochs > 1 and self.epoch_alpha == "per_epoch":
            return ("alpha covers each epoch only; the lineage has no lifetime bound (L11), "
                    "so the measured lifetime rate is outside the proposition")
        if self.partitions > 1 and self.partition_alpha == "per_partition":
            return ("alpha covers each partition only; no family-wise bound across K "
                    "partitions (C12), so the measured family-wise rate is outside the "
                    "proposition")
        if self.epochs > 1 or self.partitions > 1:
            return "alpha covers the whole lineage or family by the union bound"
        return "alpha covers the partition's epoch"


def expand(path: Path = FAMILIES) -> list[Cell]:
    """Every cell of every family: the defaults, the family's base, one grid point each."""
    spec = json.loads(path.read_text())
    cells = []
    for family in spec["families"]:
        names = list(family["grid"])
        for values in itertools.product(*(family["grid"][n] for n in names)):
            point = dict(zip(names, values))
            cell_id = family["name"] + "/" + ",".join(f"{n}={point[n]}" for n in names)
            # label_delay is shorthand for equal negative and positive delays
            delay = point.pop("label_delay", None)
            if delay is not None:
                point.update(negative_delay=delay, positive_delay=delay)
            cells.append(Cell(family["name"], cell_id, pre_registered=family["pre_registered"],
                              **{**spec["defaults"], **family["base"], **point}))
    return cells


@lru_cache(maxsize=None)
def success_threshold(alpha: float, r: float, n_max: int) -> tuple[int, ...]:
    """
    intent: The least S with beta_mixture_lower_bound(S, n - S, alpha) >= r, for each n
    method: Binary search on S at each n, valid because the bound is nondecreasing in S at
            fixed n (statistical-method.md §11.1); n + 1 when no S qualifies
    """
    table = []
    for n in range(n_max + 1):
        lo, hi = 0, n + 1
        while lo < hi:
            mid = (lo + hi) // 2
            if mid <= n and beta_mixture_lower_bound(mid, n - mid, alpha) >= r:
                hi = mid
            else:
                lo = mid + 1
        table.append(lo)
    return tuple(table)


def _verdicts(cell: Cell, p: float, rng: random.Random) -> list[int]:
    """One verdict per step; blocks share a Beta(mean p, correlation rho) rate."""
    verdicts = []
    rate = p
    for step in range(cell.horizon):
        if step % cell.block_size == 0 and cell.rho > 0:
            k = (1 - cell.rho) / cell.rho
            rate = rng.betavariate(p * k, (1 - p) * k)
        verdicts.append(1 if rng.random() < rate else 0)
    return verdicts


Trial = tuple[int, int, int]  # (frozen at step, visible from step, counted value 1 or 0)


def trials(cell: Cell, p: float, rng: random.Random) -> list[Trial]:
    """
    intent: One partition-epoch's counting trials, with when each resolves and how it counts
    method: Trial i (one per step, or one per block under block counting) is frozen at step
            i. A label sent with delay d <= deadline is visible from step i + max(d, 1), as
            in the shipped harness (a label is delivered at the start of a step, before that
            step's proposal); otherwise the trial is censored and visible, as a failure,
            from step i + deadline + 1 (the shipped expire_unlabeled: now > frozen +
            deadline)
    """
    verdicts = _verdicts(cell, p, rng)
    out = []
    for step, verdict in enumerate(verdicts):
        if cell.counting == "block" and step % cell.block_size:
            continue  # not a counting trial
        delay = cell.negative_delay if verdict == 0 else cell.positive_delay
        value = verdict
        targeted = (
            (cell.censor_target == "negatives" and verdict == 0)
            or (cell.censor_target == "positives" and verdict == 1)
            or (cell.censor_target == "random" and rng.random() < 0.5)
        )
        if targeted and cell.censor_action == "inconclusive":
            value = 0
        if (targeted and cell.censor_action == "withhold") or delay > cell.deadline:
            out.append((step, step + cell.deadline + 1, 0))
        else:
            out.append((step, step + max(delay, 1), value))
    return out


def first_crossing(cell: Cell, alpha: float, counted: list[Trial]) -> int | None:
    """
    intent: The first step at which the gate opens for these trials, or None
    method: At each step t the gate reads the trials frozen before t, counted by the cell's
            pending rule, against the shipped bound's success threshold. This is the only
            gate logic in the driver; the agreement check (agreement()) feeds it the
            shipped loop's own trials.
    effect: The step is the number of proposals seen before autonomy, which is the "trials
            to qualify" reported at cost_p
    """
    threshold = success_threshold(alpha, cell.r, cell.horizon)
    due: dict[int, list[int]] = {}
    by_step = {step: (visible, value) for step, visible, value in counted}
    if len(by_step) != len(counted):
        raise ValueError("two trials frozen at the same step")  # PR #42 review
    frozen = resolved = successes = 0
    for t in range(cell.horizon):
        for value in due.pop(t, ()):
            resolved += 1
            successes += value
        n = frozen if cell.pending == "pessimistic" else resolved
        if n >= cell.n_min and successes >= threshold[n]:
            return t
        if t in by_step:
            frozen += 1
            visible, value = by_step[t]
            due.setdefault(visible, []).append(value)
    return None


def run_unit(cell: Cell, p: float, alpha: float, rng: random.Random) -> int | None:
    """One partition, one epoch: the first step at which the gate opens, or None."""
    return first_crossing(cell, alpha, trials(cell, p, rng))


def run_sequence(cell: Cell, p: float, rng: random.Random) -> list[int | None]:
    """Every unit of one sequence, in registration then epoch order."""
    return [
        run_unit(cell, p, cell.unit_alpha(k, e), rng)
        for k in range(1, cell.partitions + 1)
        for e in range(1, cell.epochs + 1)
    ]


def _rng(cell: Cell, kind: str, index: int) -> random.Random:
    # str seeds are hashed with SHA-512 by random.seed (version 2), so this is stable
    # across runs, processes, and PYTHONHASHSEED
    return random.Random(f"{cell.cell_id}/{kind}/{index}")


def _chunk(args: tuple[Cell, int, int]) -> tuple[str, int, list[bool], list[list[int | None]]]:
    cell, start, stop = args
    false_auth = [
        any(t is not None for t in run_sequence(cell, cell.p, _rng(cell, "null", i)))
        for i in range(start, stop)
    ]
    cost = [run_sequence(cell, cell.cost_p, _rng(cell, "cost", i)) for i in range(start, stop)]
    return cell.cell_id, start, false_auth, cost


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    centre = (k + z * z / 2) / (n + z * z)
    half = z * math.sqrt(k * (n - k) / n + z * z / 4) / (n + z * z)
    return max(0.0, centre - half), min(1.0, centre + half)


def summarize(cell: Cell, false_auth: list[bool], cost: list[list[int | None]]) -> dict[str, Any]:
    count, n = sum(false_auth), len(false_auth)
    low, high = wilson_interval(count, n)
    every = [t for seq in cost for t in seq if t is not None]
    last = [seq[-1] for seq in cost if seq[-1] is not None]
    violated = cell.assumptions_violated()
    return {
        "family": cell.family,
        "cell_id": cell.cell_id,
        "pre_registered": cell.pre_registered,
        "config": {k: v for k, v in asdict(cell).items()
                   if k not in ("family", "cell_id", "pre_registered")},
        "method": cell.method(),
        "assumptions_violated": violated,
        "assumptions_hold": not violated,
        "claim_scope": cell.claim_scope(),
        "sequences": n,
        "false_authorizations": count,
        "false_authorization_rate": round(count / n, 4),
        "false_authorization_ci95": [round(low, 4), round(high, 4)],
        "exceeds_alpha": low > cell.alpha,
        "cost_p": cell.cost_p,
        "qualified_fraction_at_cost_p": round(
            sum(t is not None for seq in cost for t in seq) / sum(len(s) for s in cost), 4),
        "median_trials_to_qualify": float(statistics.median(every)) if every else None,
        "median_trials_to_qualify_last_unit": float(statistics.median(last)) if last else None,
    }


def run_cells(cells: Iterable[Cell], workers: int | None = None,
              chunk: int = 50) -> list[dict[str, Any]]:
    """Every cell's summary, in cell order; parallel over chunks, identical for any workers."""
    cells = list(cells)
    tasks = [(c, s, min(s + chunk, c.sequences)) for c in cells
             for s in range(0, c.sequences, chunk)]
    parts: dict[str, list[tuple[int, list[bool], list[list[int | None]]]]] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for cell_id, start, fa, cost in pool.map(_chunk, tasks):
            parts.setdefault(cell_id, []).append((start, fa, cost))
    summaries = []
    for cell in cells:
        ordered = sorted(parts[cell.cell_id], key=lambda part: part[0])
        summaries.append(summarize(
            cell, [x for _, fa, _ in ordered for x in fa], [x for *_, cs in ordered for x in cs]
        ))
    return summaries


# Tier two's check (owner decision, issue #40): shipped-loop runs whose qualification time
# the driver must reproduce from the loop's own trials. Each covers a different path:
# label delay, missing labels censored at the deadline, inconclusive labels, and a rate
# near the threshold so some runs never qualify.
AGREEMENT_CONFIGS: tuple[dict[str, Any], ...] = (
    {"name": "agree_fast_labels", "p_good": 0.97, "label_delay": 0},
    {"name": "agree_delayed_labels", "p_good": 0.97, "label_delay": 5},
    {"name": "agree_censored", "p_good": 0.99, "label_delay": 2, "label_missing": 0.02,
     "oracle_inconclusive": 0.01, "label_deadline": 20},
)
AGREEMENT_BASE = {"horizon": 200, "severe_given_bad": 0.0, "audit_rate": 0.0}


def shipped_trials(sim: Any, key: Any) -> list[Trial]:
    """
    intent: The partition's SHADOW trials as the shipped loop recorded them, in the driver's
            form: freeze step, the step its label or censoring took effect, counted value
    method: Read from the ledger the loop wrote, steps being the simulation clock's minutes
    """
    from .simulate import T0

    def step(when: Any) -> int:
        return int((when - T0).total_seconds() // 60)

    frozen: dict[str, int] = {}
    resolved: dict[str, tuple[int, int]] = {}
    for event in sim.ledger.events:
        if (event.event_type == "shadow.frozen" and event.payload["provenance"] == "shadow"
                and event.payload["partition"] == asdict(key)):
            frozen[event.aggregate_id] = step(event.occurred_at)
        elif event.event_type == "shadow.labeled":
            resolved[event.aggregate_id] = (
                step(event.occurred_at), int(event.payload["label"] == "positive"))
        elif event.event_type == "shadow.censored":
            resolved[event.aggregate_id] = (step(event.occurred_at), 0)
    never = 10**9  # unresolved by the horizon: never visible
    return sorted((at, *resolved.get(trial_id, (never, 0))) for trial_id, at in frozen.items())


def agreement(seeds: Iterable[int] = range(1, 51)) -> list[dict[str, Any]]:
    """
    intent: Show the driver and the shipped loop make the same qualification decision
    method: For each config and seed, run the shipped-loop harness, rebuild its trials from
            the ledger, and compare the harness's time to authority with first_crossing on
            those trials under the shipped (deadline-only) rule. A mismatch is reported,
            not raised, so the artifact records it.
    """
    from .simulate import ScenarioConfig, Simulation

    out = []
    seeds = list(seeds)
    for config in AGREEMENT_CONFIGS:
        cfg = ScenarioConfig.from_dict({**AGREEMENT_BASE, **config, "seeds": seeds})
        cell = Cell("agreement", f"agreement/{cfg.name}", p=cfg.p_good, r=cfg.required_low,
                    alpha=cfg.alpha, n_min=cfg.n_min, horizon=cfg.horizon,
                    deadline=cfg.label_deadline, pending="deadline", pre_registered=False)
        rows = []
        for seed in seeds:
            sim = Simulation(cfg, seed)
            metrics = sim.run()
            driver = first_crossing(cell, cell.alpha, shipped_trials(sim, sim.key))
            rows.append({"seed": seed, "shipped_loop": metrics.time_to_authority,
                         "driver": driver})
        out.append({
            "config": cfg.name, "sequences": len(rows),
            "agree": sum(r["shipped_loop"] == r["driver"] for r in rows),
            "qualified": sum(r["shipped_loop"] is not None for r in rows),
            "mismatches": [r for r in rows if r["shipped_loop"] != r["driver"]],
        })
    return out


CSV_FIELDS = (
    "family", "cell_id", "pre_registered", "assumptions_hold", "false_authorization_rate",
    "false_authorization_ci95", "exceeds_alpha", "median_trials_to_qualify",
    "median_trials_to_qualify_last_unit", "qualified_fraction_at_cost_p", "method",
)


def write_results(summaries: list[dict[str, Any]], output: Path) -> str:
    """summary.json, cells.csv, the families config, and a manifest; returns the hash."""
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(summaries, indent=2, sort_keys=True) + "\n")
    with (output / "cells.csv").open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(CSV_FIELDS)
        for s in summaries:
            writer.writerow([json.dumps(s[f]) if isinstance(s[f], list) else s[f]
                             for f in CSV_FIELDS])
    (output / "families.json").write_text(FAMILIES.read_text())
    files = sorted(p for p in output.iterdir()
                   if p.suffix in {".csv", ".json"} and p.name != "manifest.json")
    lines = [f"{p.name} {hashlib.sha256(p.read_bytes()).hexdigest()}" for p in files]
    canonical = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    (output / "manifest.json").write_text(json.dumps({
        "canonical_hash": canonical,
        "canonical_hash_covers": "sorted '<file name> <sha256>' lines of every .csv and "
                                 ".json file in this directory except manifest.json",
        "files": lines,
    }, indent=2, sort_keys=True) + "\n")
    return canonical


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the v3 Step 09 scenario families.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sequences", type=int, default=None,
                        help="override: sequences per cell (the committed results use 1000)")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    parser.add_argument("--family", action="append", help="only these families")
    parser.add_argument("--agreement", action="store_true",
                        help="write only the shipped-loop agreement check, to --output")
    args = parser.parse_args(argv)
    if args.agreement:
        result = agreement()
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "agreement.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n")
        for row in result:
            print(f"{row['config']:<24} agree {row['agree']}/{row['sequences']}"
                  f" (qualified {row['qualified']})")
        return 0 if all(r["agree"] == r["sequences"] for r in result) else 1
    cells = [c for c in expand() if not args.family or c.family in args.family]
    if args.sequences:
        cells = [replace(c, sequences=args.sequences) for c in cells]
    summaries = run_cells(cells, args.workers)
    for s in summaries:
        flag = "" if s["assumptions_hold"] else "  [assumption violated]"
        print(f"{s['cell_id']:<75} FA {s['false_authorization_rate']:.4f} "
              f"{s['false_authorization_ci95']}  median-to-qualify "
              f"{s['median_trials_to_qualify']}{flag}")
    print("canonical hash", write_results(summaries, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
