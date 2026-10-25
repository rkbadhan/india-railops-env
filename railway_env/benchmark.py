"""Replay baselines and oracle against the same scenario and scoring rules."""

from .baselines import dispatch
from .episode import decisions_text
from .simulator import RailwaySimulator
from .solver import solve
from .scoring import normalized_score


def benchmark(scenario, time_limit=10.0):
    simulator = RailwaySimulator(scenario)
    results = {}
    for policy in ("passenger_first", "controller_rule"):
        plan = dispatch(scenario, policy)
        results[policy] = {**simulator.evaluate(plan), "plan": plan.to_dict(),
                           "decisions": decisions_text(scenario, plan)}
    results["oracle"] = solve(scenario, time_limit)
    oracle = results["oracle"]
    for policy in ("passenger_first", "controller_rule"):
        results[policy].update(normalized_score(results[policy]["weighted_delay"],
                              results["passenger_first"]["weighted_delay"], oracle))
    return {"scenario_id": scenario.id, "objective": "weighted final-arrival delay in minutes", **results}
