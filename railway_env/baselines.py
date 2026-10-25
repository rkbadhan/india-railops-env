"""Reservation-based dispatch heuristics; neither calls the mathematical solver."""

import heapq
from collections import defaultdict
from itertools import count

from .models import Plan, Visit, integer
from .simulator import RailwaySimulator


def free_windows(intervals, horizon):
    """Free half-open intervals, merging overlapping or touching reservations."""
    cursor = 0
    for start, end in sorted(intervals):
        start, end = max(0, start), min(horizon, end)
        if start > cursor:
            yield cursor, min(start, horizon)
        cursor = max(cursor, end)
        if cursor >= horizon:
            return
    if cursor < horizon:
        yield cursor, horizon


def plan_train(scenario, train, reservations, holds):
    """Earliest completion path for one train against earlier fixed reservations.

    Free-window search chooses a berth and departure at every station. It can
    wait outside the entry boundary and in a fitting main/loop, but never blocks
    an already reserved train. All route stations are distinct.
    """
    h, clearance = scenario.horizon, scenario.clearance
    eligible = {code: [t.id for t in scenario.corridor.station(code).tracks if t.length_m >= train.length_m]
                for code in train.route}
    track_windows = {(code, track): tuple(free_windows(reservations["track", code, track], h))
                     for code in train.route for track in eligible[code]}
    block_windows = {(a, b): tuple(free_windows(reservations[scenario.corridor.block_resource(a, b)], h))
                     for a, b in zip(train.route, train.route[1:])}
    sequence = count()
    queue = []
    best = {}

    def offer(i, track, window, arrival, prefix):
        start, end = track_windows[train.route[i], track][window]
        departure = arrival if i == len(train.route) - 1 else max(
            arrival + train.dwell_times[i], train.scheduled_departures[i],
            holds.get((train.id, train.route[i]), 0))
        if arrival < start or departure + clearance > end:
            return
        key = i, track, window
        if arrival < best.get(key, h + 1):
            best[key] = arrival
            heapq.heappush(queue, (arrival, next(sequence), i, track, window, prefix))

    min_remaining = sum(train.running_times) + sum(train.dwell_times)
    for track in eligible[train.route[0]]:
        for window, (start, _) in enumerate(track_windows[train.route[0], track]):
            arrival = max(train.release, start)
            if arrival + min_remaining + clearance <= h:
                offer(0, track, window, arrival, ())
    while queue:
        arrival, _, i, track, window, prefix = heapq.heappop(queue)
        # Earlier arrival dominates every later arrival in the same free berth
        # window: it can wait for the same outgoing slot. Keep one such label.
        key = i, track, window
        if arrival != best[key]:
            continue
        code = train.route[i]
        if i == len(train.route) - 1:
            return prefix + (Visit(train.id, code, arrival, arrival, track),)
        first = max(arrival + train.dwell_times[i], train.scheduled_departures[i],
                    holds.get((train.id, code), 0))
        last = track_windows[code, track][window][1] - clearance
        nxt = train.route[i + 1]
        runtime = train.running_times[i]
        for start, end in block_windows[code, nxt]:
            earliest = max(first, start)
            latest = min(last, end - runtime - clearance)
            if earliest > latest:
                continue
            for destination in eligible[nxt]:
                for next_window, (next_start, next_end) in enumerate(track_windows[nxt, destination]):
                    departure = max(earliest, next_start - runtime)
                    limit = min(latest, next_end - runtime - train.dwell_times[i + 1] - clearance)
                    if i + 1 < len(train.route) - 1 and max(train.scheduled_departures[i + 1],
                            holds.get((train.id, nxt), 0)) + clearance > next_end:
                        continue
                    if departure <= limit:
                        visit = Visit(train.id, code, arrival, departure, track)
                        offer(i + 1, destination, next_window, departure + runtime, prefix + (visit,))
    raise ValueError(f"cannot schedule {train.id} inside horizon {h}")


def dispatch(scenario, policy="controller_rule", holds=(), priority_order=()):
    if policy not in ("passenger_first", "controller_rule"):
        raise ValueError("unknown dispatch policy")
    trains = {t.id: t for t in scenario.trains}
    hold_map = {}
    for hold in holds:
        if set(hold) != {"train_id", "station", "until"}:
            raise ValueError("a hold needs train_id, station, and until")
        train = trains.get(hold["train_id"])
        if not train or hold["station"] not in train.route[:-1]:
            raise ValueError("a hold must reference a known train and a non-terminal route station")
        integer(hold["until"], "hold until")
        if hold["until"] >= scenario.horizon:
            raise ValueError("hold exceeds scenario horizon")
        key = train.id, hold["station"]
        hold_map[key] = max(hold_map.get(key, 0), hold["until"])
    if len(set(priority_order)) != len(priority_order) or any(t not in trains for t in priority_order):
        raise ValueError("priority_order must contain distinct, known train ids")
    if policy == "passenger_first":
        ordered = sorted(trains, key=lambda k: (trains[k].kind != "passenger", -trains[k].weight,
                                                trains[k].release, k))
    else:
        # Human-inspired first-ready rule: priority and shorter clearance time break ties.
        ordered = sorted(trains, key=lambda k: (trains[k].release, -trains[k].weight,
                                                sum(trains[k].running_times), k))
    ordered = list(priority_order) + [t for t in ordered if t not in priority_order]
    reservations = defaultdict(list)
    for resource, start, end in scenario.closures():
        reservations[resource].append((start, end))
    visits = []
    for train_id in ordered:
        route = plan_train(scenario, trains[train_id], reservations, hold_map)
        visits.extend(route)
        for i, visit in enumerate(route):
            reservations["track", visit.station, visit.track].append(
                (visit.arrival, visit.departure + scenario.clearance))
            if i:
                previous = route[i - 1]
                reservations[scenario.corridor.block_resource(previous.station, visit.station)].append(
                    (previous.departure, visit.arrival + scenario.clearance))
    plan = Plan(tuple(visits))
    result = RailwaySimulator(scenario).evaluate(plan)
    if not result["valid"]:
        raise RuntimeError("dispatch produced an unsafe plan: " + "; ".join(result["errors"]))
    return plan
