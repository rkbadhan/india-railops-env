"""CP-SAT answer key, replayed through the independent railway safety checker."""

from collections import defaultdict

from .models import Plan, Visit
from .simulator import RailwaySimulator


def solve(scenario, time_limit=10.0):
    if not 0 < time_limit <= 3600:
        raise ValueError("time_limit must be in (0, 3600]")
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    resources = defaultdict(list)
    visits = {}
    choices = {}
    h, clearance = scenario.horizon, scenario.clearance
    costs = []
    for train in scenario.trains:
        for i, code in enumerate(train.route):
            name = f"{train.id}_{i}"
            arrival = model.new_int_var(0, h - clearance, "arr_" + name)
            departure = model.new_int_var(0, h - clearance, "dep_" + name)
            end = model.new_int_var(clearance, h, "clear_" + name)
            size = model.new_int_var(clearance, h, "stay_" + name)
            model.add(end == departure + clearance)
            model.add(size == end - arrival)
            model.add(departure >= arrival + train.dwell_times[i])
            if i == len(train.route) - 1:
                model.add(departure == arrival)
            else:
                model.add(departure >= train.scheduled_departures[i])
            if not i:
                model.add(arrival >= train.release)
            else:
                previous = visits[train.id, i - 1][1]
                model.add(arrival == previous + train.running_times[i - 1])
                resources[scenario.corridor.block_resource(train.route[i - 1], code)].append(
                    model.new_interval_var(previous, train.running_times[i - 1] + clearance,
                                           end=model.new_int_var(0, h, "block_end_" + name),
                                           name="block_" + name))
            options = []
            for track in scenario.corridor.station(code).tracks:
                if track.length_m < train.length_m:
                    continue
                present = model.new_bool_var(f"{name}_{track.id}")
                resources["track", code, track.id].append(
                    model.new_optional_interval_var(arrival, size, end, present, name + "_" + track.id))
                options.append(present)
                choices[train.id, i, track.id] = present
            model.add_exactly_one(options)
            visits[train.id, i] = arrival, departure
        delay = model.new_int_var(0, h, "delay_" + train.id)
        model.add_max_equality(delay, [0, visits[train.id, len(train.route) - 1][0] - train.scheduled_arrivals[-1]])
        costs.append(train.weight * delay)
    for resource, start, end in scenario.closures():
        resources[resource].append(
            model.new_fixed_size_interval_var(start, end - start, f"closure_{resource}_{start}"))
    for intervals in resources.values():
        model.add_no_overlap(intervals)
    model.minimize(sum(costs))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit)
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = scenario.seed % (2**31 - 1)
    status = solver.solve(model)
    result = {"status": solver.status_name(status), "proven_optimal": status == cp_model.OPTIMAL,
              "wall_time_seconds": solver.wall_time, "weighted_delay": None,
              "best_bound": solver.best_objective_bound, "relative_gap": None, "plan": None}
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return result
    output = []
    for train in scenario.trains:
        for i, code in enumerate(train.route):
            track = next(t.id for t in scenario.corridor.station(code).tracks
                         if (train.id, i, t.id) in choices and solver.boolean_value(choices[train.id, i, t.id]))
            arrival, departure = visits[train.id, i]
            output.append(Visit(train.id, code, solver.value(arrival), solver.value(departure), track))
    plan = Plan(tuple(output))
    replay = RailwaySimulator(scenario).evaluate(plan)
    if not replay["valid"]:
        raise RuntimeError("solver/replay disagreement: " + "; ".join(replay["errors"]))
    objective = replay["weighted_delay"]
    if abs(objective - solver.objective_value) > 1e-6:
        raise RuntimeError("solver/replay objective disagreement")
    result.update(weighted_delay=objective, plan=plan.to_dict(), metrics=replay,
                  relative_gap=max(0.0, objective - result["best_bound"]) / max(1, objective))
    return result
