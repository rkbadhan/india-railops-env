"""Can holds + priority_order reproduce the certified optimum through dispatch()?

The agent never submits a timetable; dispatch() compiles its holds and train
order. If no combination of those controls reaches the solver's best weighted
delay, a perfect score is unreachable and the reward is misleading. This
translates each stored optimal plan into controls and replays it.
"""

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from .baselines import dispatch
from .models import Plan
from .simulator import RailwaySimulator
from .suite import load_suite


def plan_controls(scenario, plan):
    """Holds pinning every non-terminal departure, plus candidate train orders."""
    trains = {t.id: t for t in scenario.trains}
    by_train = defaultdict(list)
    for visit in plan.visits:
        by_train[visit.train_id].append(visit)
    holds, first_departure, last_arrival = [], {}, {}
    for train_id, visits in by_train.items():
        route = trains[train_id].route
        visits.sort(key=lambda v: route.index(v.station))
        first_departure[train_id] = visits[0].departure
        last_arrival[train_id] = visits[-1].arrival
        holds.extend({"train_id": train_id, "station": v.station, "until": v.departure}
                     for v in visits[:-1] if v.departure > 0)
    ids = sorted(trains)
    orders = {
        "by_first_departure": sorted(ids, key=lambda k: (first_departure[k], k)),
        "by_final_arrival": sorted(ids, key=lambda k: (last_arrival[k], k)),
        "by_weight_then_departure": sorted(ids, key=lambda k: (-trains[k].weight, first_departure[k], k)),
    }
    return holds, orders


def check_case(scenario, optimum_plan, optimum, search_budget=300):
    simulator = RailwaySimulator(scenario)
    holds, orders = plan_controls(scenario, optimum_plan)
    attempts = []
    for use_holds in (True, False):
        for name, order in orders.items():
            try:
                plan = dispatch(scenario, "passenger_first", holds if use_holds else (), order)
                cost = simulator.evaluate(plan)["weighted_delay"]
            except (ValueError, RuntimeError) as error:
                attempts.append({"holds": use_holds, "order": name, "cost": None, "error": str(error)})
                continue
            attempts.append({"holds": use_holds, "order": name, "cost": cost})
    scored = [a for a in attempts if a["cost"] is not None]
    best = min(scored, key=lambda a: a["cost"]) if scored else None
    with_holds = [a for a in scored if a["holds"]]
    priority_only = [a for a in scored if not a["holds"]]
    best_holds = min((a["cost"] for a in with_holds), default=None)
    best_priority = min((a["cost"] for a in priority_only), default=None)
    reached_by = "constructive" if best and best["cost"] == optimum else None
    best_cost, search_evaluations = best["cost"] if best else None, 0
    if reached_by is None:
        # Berths are chosen by dispatch(), not the agent, so copying the solver's
        # departures can fail while another train order still reaches the optimum.
        best_cost, search_evaluations = order_search(scenario, holds, orders["by_first_departure"], optimum,
                                                     best_cost, search_budget)
        reached_by = "order_search" if best_cost == optimum else None
    return {"optimum": optimum, "best_cost": best_cost, "reached": reached_by is not None, "reached_by": reached_by,
            "gap": best_cost - optimum if best_cost is not None else None, "search_evaluations": search_evaluations,
            "reached_with_holds": best_holds == optimum, "reached_priority_only": best_priority == optimum,
            "winning": {k: best[k] for k in ("holds", "order")} if best else None, "attempts": attempts}


def order_search(scenario, holds, order, optimum, best_cost, budget):
    """Deterministic hill climb over train orders, keeping the solver's holds."""
    rng = random.Random(scenario.seed)
    simulator = RailwaySimulator(scenario)
    current, current_cost = list(order), best_cost if best_cost is not None else float("inf")
    for evaluations in range(1, budget + 1):
        candidate = current[:]
        i, j = rng.sample(range(len(candidate)), 2)
        candidate.insert(j, candidate.pop(i))
        try:
            cost = simulator.evaluate(dispatch(scenario, "passenger_first", holds, candidate))["weighted_delay"]
        except (ValueError, RuntimeError):
            continue
        if cost <= current_cost or rng.random() < 0.02:
            current, current_cost = candidate, cost
        if best_cost is None or cost < best_cost:
            best_cost = cost
        if best_cost == optimum:
            return best_cost, evaluations
    return best_cost, budget


def check_suite(suite):
    _, cases = load_suite(suite)
    rows = []
    for case in cases:
        oracle = case["reference"]["oracle"]
        if not oracle["proven_optimal"]:
            rows.append({"seed": case["seed"], "skipped": "optimum_not_proven"})
            continue
        row = check_case(case["scenario"], Plan.from_dict(oracle["plan"]), oracle["weighted_delay"])
        row["seed"] = case["seed"]
        row["passenger_first"] = case["reference"]["passenger_first"]["weighted_delay"]
        rows.append(row)
    checked = [r for r in rows if "skipped" not in r]
    short = [r for r in checked if not r["reached"]]
    return {"suite": str(suite), "cases": len(rows), "checked": len(checked),
            "reached_optimum": len(checked) - len(short),
            "reached_constructively": sum(r["reached_by"] == "constructive" for r in checked),
            "reached_by_order_search": [r["seed"] for r in checked if r["reached_by"] == "order_search"],
            "reached_with_holds": sum(r["reached_with_holds"] for r in checked),
            "reached_priority_only": sum(r["reached_priority_only"] for r in checked),
            "unreachable_cases": [{"seed": r["seed"], "optimum": r["optimum"], "best_cost": r["best_cost"],
                                   "gap": r["gap"], "passenger_first": r["passenger_first"]} for r in short],
            "mean_gap_when_unreachable": sum(r["gap"] for r in short) / len(short) if short else 0.0,
            "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suites", nargs="*", default=["benchmarks/heldout_v1", "benchmarks/tuning_v1"])
    parser.add_argument("--output")
    args = parser.parse_args()
    reports = [check_suite(suite) for suite in args.suites]
    for report in reports:
        print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    return int(any(r["unreachable_cases"] for r in reports))


if __name__ == "__main__":
    raise SystemExit(main())
