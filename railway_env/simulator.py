"""Independent safety checking, scoring, and event replay of complete plans."""

from collections import defaultdict
from dataclasses import asdict

from .models import Plan, Scenario, integer


def overlaps(a, b):
    """Half-open reservations: an interval ending at t frees its resource at t."""
    return a[0] < b[1] and b[0] < a[1]


class RailwaySimulator:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.clock = 0
        self.plan = None

    def evaluate(self, plan: Plan):
        """Reject incomplete/unsafe plans. Only complete safe plans receive a score."""
        errors = []
        grouped = defaultdict(dict)
        train_map = {t.id: t for t in self.scenario.trains}
        for visit in plan.visits:
            if not all(isinstance(value, str) and value for value in (visit.train_id, visit.station, visit.track)):
                errors.append("visit train_id, station, and track must be nonempty strings")
                continue
            if visit.train_id not in train_map:
                errors.append(f"unknown train {visit.train_id}")
                continue
            if visit.station not in train_map[visit.train_id].route:
                errors.append(f"{visit.train_id}: station {visit.station} is not on its route")
                continue
            if visit.station in grouped[visit.train_id]:
                errors.append(f"{visit.train_id}: duplicate visit at {visit.station}")
            grouped[visit.train_id][visit.station] = visit
            try:
                integer(visit.arrival, "arrival")
                integer(visit.departure, "departure")
            except ValueError as exc:
                errors.append(f"{visit.train_id}/{visit.station}: {exc}")
        if errors:
            return self._invalid(errors)

        reservations = defaultdict(list)
        delays = {}
        for train in self.scenario.trains:
            visits = grouped[train.id]
            missing = set(train.route) - set(visits)
            if missing:
                errors.append(f"{train.id}: missing visits at {', '.join(sorted(missing))}")
                continue
            ordered = [visits[code] for code in train.route]
            if ordered[0].arrival < train.release:
                errors.append(f"{train.id}: enters before release {train.release}")
            for i, visit in enumerate(ordered):
                tracks = {t.id: t for t in self.scenario.corridor.station(visit.station).tracks}
                track = tracks.get(visit.track)
                if not track:
                    errors.append(f"{train.id}: unknown track {visit.track} at {visit.station}")
                elif track.length_m < train.length_m:
                    errors.append(f"{train.id}: {train.length_m}m train does not fit "
                                  f"{visit.station}/{track.id} ({track.length_m}m)")
                if visit.departure < visit.arrival + train.dwell_times[i]:
                    errors.append(f"{train.id}: insufficient dwell at {visit.station}")
                if i == len(ordered) - 1:
                    if visit.departure != visit.arrival:
                        errors.append(f"{train.id}: terminal departure must equal arrival")
                elif visit.departure < train.scheduled_departures[i]:
                    errors.append(f"{train.id}: departs {visit.station} before timetable")
                if visit.departure + self.scenario.clearance > self.scenario.horizon:
                    errors.append(f"{train.id}: plan exceeds horizon")
                reservations[("track", visit.station, visit.track)].append(
                    (visit.arrival, visit.departure + self.scenario.clearance, train.id))
                if i:
                    previous = ordered[i - 1]
                    if visit.arrival != previous.departure + train.running_times[i - 1]:
                        errors.append(f"{train.id}: incorrect running time {previous.station}-{visit.station}")
                    resource = self.scenario.corridor.block_resource(previous.station, visit.station)
                    reservations[resource].append(
                        (previous.departure, visit.arrival + self.scenario.clearance, train.id))
            delays[train.id] = max(0, ordered[-1].arrival - train.scheduled_arrivals[-1])

        for resource, start, end in self.scenario.closures():
            reservations[resource].append((start, end, "signal failure"))
        for resource, intervals in reservations.items():
            furthest = None
            for interval in sorted(intervals):
                if furthest and overlaps(furthest, interval):
                    errors.append(f"{'/'.join(resource)} occupied by {furthest[2]} and {interval[2]} "
                                  f"during overlapping intervals")
                if furthest is None or interval[1] > furthest[1]:
                    furthest = interval
        if errors:
            return self._invalid(errors)
        weighted = sum(t.weight * delays[t.id] for t in self.scenario.trains)
        return {"valid": True, "errors": [], "weighted_delay": weighted, "reward": -weighted,
                "total_delay": sum(delays.values()), "max_delay": max(delays.values(), default=0),
                "train_delays": delays, "completed_trains": len(delays),
                "makespan": max(v.departure for v in plan.visits)}

    @staticmethod
    def _invalid(errors):
        return {"valid": False, "errors": errors, "weighted_delay": None, "reward": None}

    def start(self, plan):
        result = self.evaluate(plan)
        if not result["valid"]:
            raise ValueError("unsafe plan: " + "; ".join(result["errors"]))
        self.plan = plan
        self.clock = 0
        return self.snapshot()

    def advance(self, minute):
        integer(minute, "simulation time", self.clock)
        if self.plan is None:
            raise ValueError("start a validated plan before advancing")
        self.clock = minute
        return self.snapshot()

    def snapshot(self):
        if self.plan is None:
            raise ValueError("no plan is running")
        trains = []
        for train in self.scenario.trains:
            visits = {v.station: v for v in self.plan.visits if v.train_id == train.id}
            ordered = [visits[code] for code in train.route]
            t = self.clock
            state = {"id": train.id, "kind": train.kind, "length_m": train.length_m}
            if t < ordered[0].arrival:
                state.update(state="not_released" if t < train.release else "entry_queue",
                             station=train.route[0], release=train.release)
            elif t >= ordered[-1].arrival:
                state.update(state="completed", station=train.route[-1],
                             delay=max(0, ordered[-1].arrival - train.scheduled_arrivals[-1]))
            else:
                for i, visit in enumerate(ordered[:-1]):
                    if visit.arrival <= t < visit.departure:
                        state.update(state="at_station", station=visit.station, track=visit.track,
                                     departs=visit.departure,
                                     delay=max(0, t - train.scheduled_departures[i]))
                        break
                    nxt = ordered[i + 1]
                    if visit.departure <= t < nxt.arrival:
                        state.update(state="on_block", origin=visit.station, destination=nxt.station,
                                     arrives=nxt.arrival,
                                     fraction=(t - visit.departure) / train.running_times[i])
                        break
            trains.append(state)
        return {"minute": self.clock, "trains": trains}

    def events(self, plan):
        events = []
        for visit in plan.visits:
            events.extend([{"minute": visit.arrival, "event": "arrival", **asdict(visit)},
                           {"minute": visit.departure, "event": "departure", **asdict(visit)}])
        return sorted(events, key=lambda e: (e["minute"], e["train_id"], e["station"], e["event"]))
