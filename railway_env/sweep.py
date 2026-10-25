"""Reproducible difficulty measurements; only certified optima count as best scores."""

import statistics
import sys
from pathlib import Path

from .benchmark import benchmark
from .generator import generate
from .models import save_json
from .seeds import parse_seeds
from .references import make_reference


def summarize(cases):
    proven = [c for c in cases if c.get("oracle", {}).get("proven_optimal")]
    gaps = [c["passenger_first"]["weighted_delay"] - c["oracle"]["weighted_delay"] for c in proven]
    times = sorted(c["oracle"]["wall_time_seconds"] for c in cases if "oracle" in c)
    optimal = sum(g == 0 for g in gaps)
    return {"attempted": len(cases), "completed": len(times), "proven_optimal": len(proven),
            "errors": sum("error" in c for c in cases), "passenger_first_optimal": optimal,
            "passenger_first_optimal_percent": 100 * optimal / len(proven) if proven else None,
            "mean_gap": statistics.mean(gaps) if gaps else None,
            "cases_with_headroom": sum(g > 0 for g in gaps),
            "mean_gap_when_suboptimal": statistics.mean(g for g in gaps if g > 0) if any(g > 0 for g in gaps) else None,
            "max_gap": max(gaps) if gaps else None,
            "solver_mean_seconds": statistics.mean(times) if times else None,
            "solver_p95_seconds": times[max(0, (95 * len(times) + 99) // 100 - 1)] if times else None,
            "solver_max_seconds": max(times) if times else None}


def format_summary(summary):
    rows = [("Problems completed / attempted", f"{summary['completed']} / {summary['attempted']}"),
            ("Solver proven optimal", f"{summary['proven_optimal']} / {summary['attempted']}"),
            ("Errors", str(summary["errors"]))]
    rate = summary["passenger_first_optimal_percent"]
    rows.append(("Passenger-first optimal (certified cases)",
                 "n/a" if rate is None else f"{summary['passenger_first_optimal']} / {summary['proven_optimal']} ({rate:.1f}%)"))
    for key, label in (("mean_gap", "Mean PF excess (all certified cases)"),
                       ("mean_gap_when_suboptimal", "Mean PF excess (suboptimal cases only)"),
                       ("max_gap", "Largest passenger-first excess delay"),
                       ("solver_mean_seconds", "Mean solver seconds"),
                       ("solver_p95_seconds", "P95 solver seconds"),
                       ("solver_max_seconds", "Maximum solver seconds")):
        rows.append((label, "n/a" if summary[key] is None else f"{summary[key]:.3f}"))
    return "Metric                                      | Value\n" + "-" * 65 + "\n" + "\n".join(
        f"{label:43} | {value}" for label, value in rows)


def sweep(seeds, output="runs/sweep", time_limit=10.0, **generation):
    seeds = list(seeds)
    if not seeds or not 0 < time_limit <= 3600:
        raise ValueError("provide at least one seed and time_limit in (0, 3600]")
    cases = []
    directory = Path(output)
    for i, seed in enumerate(seeds):
        try:
            scenario = generate(seed=seed, **generation)
            save_json(directory / f"seed_{seed}" / "scenario.json", scenario.to_dict())
            case = {"seed": seed, **benchmark(scenario, time_limit)}
            save_json(directory / f"seed_{seed}" / "benchmark.json", case)
            save_json(directory / f"seed_{seed}" / "reference.json", make_reference(scenario, case))
        except (ValueError, RuntimeError, OSError) as error:
            case = {"seed": seed, "error": str(error)}
        cases.append(case)
        # Checkpoint all results, including failures; never silently select easy seeds.
        result = {"configuration": {"seeds": list(seeds), "time_limit": time_limit, **generation},
                  "summary": summarize(cases), "cases": cases}
        save_json(directory / "sweep.json", result)
        if (i + 1) % 10 == 0 or i + 1 == len(seeds):
            print(f"Sweep: {i + 1}/{len(seeds)} complete", file=sys.stderr, flush=True)
    return result
