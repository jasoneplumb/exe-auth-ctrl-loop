"""
Intent: The one command that turns configs into artifacts, and a manifest that lets a
        reader check the artifacts are the ones the code produced
Context: `python -m experiments.run --config experiments/configs/stable.json --output
        results/stable` runs one scenario; `--all --output results` runs every config,
        draws the cross-scenario figures, and writes results/manifest.json.
Pattern: Canonical hash over numeric artifacts only. Timestamps, platform, Python
        version, and the code SHA are recorded but excluded, so a rerun on another day
        or machine must reproduce the same canonical hash or something changed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from exe_auth_ctrl_loop import __version__

from .plots import bar_chart, line_chart
from .simulate import ScenarioConfig, Simulation

CONFIG_DIR = Path(__file__).resolve().parent / "configs"
CANONICAL_SUFFIXES = {".csv", ".json", ".jsonl", ".svg"}


def load_config(path: Path) -> ScenarioConfig:
    return ScenarioConfig.from_dict(json.loads(path.read_text()))


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "mean": None, "max": None}
    return {
        "min": min(values), "median": statistics.median(values),
        "mean": round(statistics.fmean(values), 4), "max": max(values),
    }


def summarize(cfg: ScenarioConfig, rows: list[dict[str, Any]]) -> dict[str, Any]:
    tta = [r["time_to_authority"] for r in rows if r["time_to_authority"] is not None]
    delays = [r["intervention_delay"] for r in rows if r["intervention_delay"] is not None]
    before = [
        r["qualified_before_turnover"] for r in rows
        if r["qualified_before_turnover"] is not None
    ]

    def total(name: str) -> int:
        return sum(int(r[name]) for r in rows)

    def mean(name: str) -> float | None:
        values = [r[name] for r in rows if r[name] is not None]
        return round(statistics.fmean(values), 4) if values else None

    return {
        "scenario": cfg.name,
        "comparator": cfg.comparator,
        "assumptions_violated": list(cfg.assumptions_violated),
        "runs": len(rows),
        "horizon": cfg.horizon,
        "qualified_runs": sum(1 for r in rows if r["qualified"]),
        "qualified_fraction": round(sum(1 for r in rows if r["qualified"]) / len(rows), 4),
        "time_to_authority": {**_quantiles(tta), "censored_runs": len(rows) - len(tta)},
        "qualified_before_turnover_fraction": (
            round(sum(before) / len(before), 4) if before else None
        ),
        "turnovers_total": total("turnovers"),
        "autonomous_executions_total": total("autonomous_executions"),
        "audit_executions_total": total("audit_executions"),
        "audit_overhead_mean": mean("audit_overhead"),
        "human_reviews_total": total("human_reviews"),
        "human_approved_executions_total": total("human_approved_executions"),
        "failures_human_missed_total": total("failures_human_missed"),
        "policy_denials_total": total("policy_denials"),
        "issue_denials_total": total("issue_denials"),
        "stale_token_denials_total": total("stale_token_denials"),
        "shadow_trials_total": total("shadow_trials"),
        "shadow_censored_total": total("shadow_censored"),
        "censored_fraction_mean": mean("censored_fraction"),
        "failures_after_autonomy_total": total("failures_after_autonomy"),
        "severe_after_autonomy_total": total("severe_after_autonomy"),
        "suspensions_total": total("suspensions"),
        "releases_total": total("releases"),
        "requalifications_total": total("requalifications"),
        "intervention_delay": {**_quantiles(delays), "censored_runs": sum(
            1 for r in rows if r["intervention_delay"] is None and (
                r["failures_after_autonomy"] > 0 and cfg.assumptions_violated
            )
        )},
        "unauthorized_side_effects_total": total("unauthorized_side_effects"),
        "final_states": {
            state: sum(1 for r in rows if r["final_state"] == state)
            for state in sorted({r["final_state"] for r in rows})
        },
    }


def run_scenario(cfg: ScenarioConfig, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] | None = None
    for i, seed in enumerate(cfg.seeds):
        sim = Simulation(cfg, seed, trace=(i == 0))
        rows.append(sim.run().row())
        if i == 0:
            trace = sim.trace
    _write_json(out_dir / "config.json", json.loads(json.dumps(cfg.__dict__)))
    with (out_dir / "runs.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with (out_dir / "trace.jsonl").open("w") as fh:
        for record in trace or []:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    summary = summarize(cfg, rows)
    _write_json(out_dir / "summary.json", summary)
    draw_trace(out_dir / "trace.svg", cfg, trace or [])
    return summary


def draw_trace(path: Path, cfg: ScenarioConfig, trace: list[dict[str, Any]]) -> None:
    bound = [(r["t"], r["lower_bound"]) for r in trace if r.get("lower_bound") is not None]
    n_pts = [(r["t"], r["n"]) for r in trace]
    n_max = max((n for _, n in n_pts), default=1) or 1
    n_scaled = [(t, n / n_max) for t, n in n_pts]
    bands = []
    start, state = None, None
    for r in trace:
        if r["state"] != state:
            if start is not None and state == "AUTONOMOUS":
                bands.append((start, r["t"], "#7fbf7f"))
            if start is not None and state == "SUSPENDED":
                bands.append((start, r["t"], "#d98080"))
            start, state = r["t"], r["state"]
    if start is not None and state in ("AUTONOMOUS", "SUSPENDED"):
        bands.append((start, cfg.horizon, "#7fbf7f" if state == "AUTONOMOUS" else "#d98080"))
    line_chart(
        path, f"{cfg.name}: seed {cfg.seeds[0]} trace (green = AUTONOMOUS, red = SUSPENDED)",
        "simulated step", "lower bound / n (scaled)",
        [("lower bound at issue", bound, "#4a6fa5"), (f"n / {n_max}", n_scaled, "#999999")],
        h_line=cfg.required_low, bands=bands,
    )


def draw_figures(summaries: list[dict[str, Any]], fig_dir: Path) -> None:
    fig_dir.mkdir(parents=True, exist_ok=True)
    labels = [s["scenario"] for s in summaries]
    bar_chart(fig_dir / "qualified_fraction.svg", "Runs reaching AUTONOMOUS from n = 0",
              labels, [s["qualified_fraction"] for s in summaries], "fraction of runs", 1.0)
    bar_chart(fig_dir / "time_to_authority.svg",
              "Median steps to first authority (qualified runs only; see censored_runs)",
              labels, [s["time_to_authority"]["median"] for s in summaries], "steps")
    bar_chart(fig_dir / "failures_after_autonomy.svg",
              "Unacceptable autonomous/audited executions, total over runs",
              labels, [float(s["failures_after_autonomy_total"]) for s in summaries], "count")
    bar_chart(fig_dir / "human_reviews.svg", "Human reviews, total over runs",
              labels, [float(s["human_reviews_total"]) for s in summaries], "count")
    bar_chart(fig_dir / "intervention_delay.svg",
              "Median steps from first post-change failure to loss of autonomy",
              labels, [s["intervention_delay"]["median"] for s in summaries], "steps")
    bar_chart(fig_dir / "censored_fraction.svg", "Mean censored fraction of shadow trials",
              labels, [s["censored_fraction_mean"] for s in summaries], "fraction", 1.0)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def write_manifest(root: Path, configs: list[ScenarioConfig]) -> dict[str, Any]:
    files = {
        str(p.relative_to(root)): sha256(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix in CANONICAL_SUFFIXES and p.name != "manifest.json"
    }
    canonical = hashlib.sha256(
        "\n".join(f"{k} {v}" for k, v in sorted(files.items())).encode()
    ).hexdigest()
    manifest = {
        "canonical_hash": canonical,
        "canonical_hash_covers": "sorted '<relative path> <sha256>' lines of every "
                                 ".csv/.json/.jsonl/.svg under the output root except this file",
        "files": files,
        "scenarios": [c.name for c in configs],
        "seeds": {c.name: list(c.seeds) for c in configs},
        "horizon": {c.name: c.horizon for c in configs},
        "metadata_excluded_from_canonical_hash": {
            "code_sha": git_sha(),
            "package_version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    _write_json(root / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline v2 evaluation.")
    parser.add_argument("--config", type=Path, help="one scenario JSON")
    parser.add_argument("--all", action="store_true", help="every config in experiments/configs")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, default=None, help="override: use the first N seeds")
    parser.add_argument("--horizon", type=int, default=None, help="override the horizon")
    args = parser.parse_args(argv)
    if bool(args.config) == bool(args.all):
        parser.error("choose exactly one of --config or --all")

    paths = sorted(CONFIG_DIR.glob("*.json")) if args.all else [args.config]
    configs = []
    for path in paths:
        cfg = load_config(path)
        if args.seeds:
            cfg = ScenarioConfig.from_dict({**cfg.__dict__, "seeds": cfg.seeds[: args.seeds]})
        if args.horizon:
            cfg = ScenarioConfig.from_dict({**cfg.__dict__, "horizon": args.horizon})
        configs.append(cfg)

    summaries = []
    for cfg in configs:
        out_dir = args.output / cfg.name if args.all else args.output
        summary = run_scenario(cfg, out_dir)
        summaries.append(summary)
        print(f"{cfg.name:<24} qualified {summary['qualified_fraction']:.2f}  "
              f"tta median {summary['time_to_authority']['median']}  "
              f"failures after autonomy {summary['failures_after_autonomy_total']}  "
              f"denials {summary['policy_denials_total']}")
    if args.all:
        _write_json(args.output / "summary.json", summaries)
        draw_figures(summaries, args.output / "figures")
    manifest = write_manifest(args.output, configs)
    print(f"canonical hash {manifest['canonical_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
