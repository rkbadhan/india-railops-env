"""Seeded two-hour dispatch problems using measured infrastructure and timetables."""

import math
import random
from dataclasses import replace

from .data import corridors, passenger_templates
from .models import Disruption, Scenario, Train


def generate(seed=0, data_dir="rail_data/processed", station_count=6, freight_count=8,
             disruption=None, section=None, disruption_count=2, block_mode="source", freight_mix=True):
    if type(seed) is not int or type(freight_count) is not int or not 1 <= freight_count <= 8:
        raise ValueError("seed must be an integer and freight_count must be 1-8")
    if disruption not in (None, "late_train", "signal_failure"):
        raise ValueError("disruption must be late_train or signal_failure")
    if type(disruption_count) is not int or disruption_count not in (1, 2):
        raise ValueError("disruption_count must be 1 or 2")
    if block_mode not in ("source", "shared", "directional"):
        raise ValueError("block_mode must be source, shared or directional")
    rng = random.Random(seed)
    choices = [c for c in corridors(data_dir, station_count) if section is None or c.section == section]
    rng.shuffle(choices)
    selected = None
    for corridor in choices:
        templates = passenger_templates(data_dir, corridor)
        candidates = []
        # Start at an entry with no passenger already inside the stretch. This
        # first version therefore requires no fabricated initial block positions.
        for start in sorted({t["arrivals"][0] for t in templates}):
            active = []
            overlaps_start = False
            for template in templates:
                for offset in (-1440, 0, 1440):
                    entry = template["arrivals"][0] + offset
                    exit_time = template["arrivals"][-1] + offset
                    if entry < start < exit_time + 1:
                        overlaps_start = True
                    if start <= entry < start + 120:
                        active.append((template, offset))
            if not overlaps_start and 1 <= len(active) <= 6:
                candidates.append((start, active))
        if candidates:
            start, active = rng.choice(candidates)
            selected = corridor, start, active
            break
    if selected is None:
        raise ValueError("no verified stretch/window with complete daily passenger timings; "
                         "try fewer stations or another section")
    corridor, start, active = selected
    mode = ("directional" if corridor.source_line_type.lower() == "double" else "shared") if block_mode == "source" else block_mode
    corridor = replace(corridor, block_mode=mode)
    max_main = min(max(t.length_m for t in s.tracks if t.kind == "main") for s in corridor.stations)
    trains = []
    for template, offset in active:
        arrivals = tuple(t + offset - start for t in template["arrivals"])
        departures = tuple(t + offset - start for t in template["departures"])
        length = min(rng.choice((420, 520, 600)), max_main)
        # Train categories/lengths are synthetic; no passenger counts are inferred.
        weight = 20 if template["number"].startswith("12") else 10
        trains.append(Train(f"P{template['number']}-{template['direction']}", "passenger", template["route"],
                            length, weight, arrivals[0], arrivals, departures,
                            template["running"], template["dwell"], template["number"]))
    for i in range(freight_count):
        route = tuple(s.code for s in corridor.stations)
        if rng.randrange(2):
            route = route[::-1]
        release = rng.randrange(0, 100)
        speed_factor = rng.choice((1.1, 1.4, 1.7)) if freight_mix else 1.25
        weight = rng.choice((1, 3, 8)) if freight_mix else 1
        running = tuple(math.ceil(speed_factor * corridor.block(a, b).running_time(a, b))
                        for a, b in zip(route, route[1:]))
        arrivals, departures = [release], []
        for duration in running:
            departures.append(arrivals[-1] + 1)
            arrivals.append(departures[-1] + duration)
        departures.append(arrivals[-1])
        lengths = [n for n in (550, 650, 730, 790) if n <= max_main]
        length = rng.choice(lengths or [max_main])
        trains.append(Train(f"F{i + 1}", "freight", route, length, weight, release,
                            tuple(arrivals), tuple(departures), running,
                            (1,) * (len(route) - 1) + (0,)))
    kind = disruption or rng.choice(("late_train", "signal_failure"))
    kinds = [kind] if disruption_count == 1 else [kind, "signal_failure" if kind == "late_train" else "late_train"]
    events = []
    for kind in kinds:
        events.append(make_disruption(kind, trains, len(active), rng))
    trains.sort(key=lambda t: (t.release, t.id))
    metadata = {
        "passenger_source": "Sambalpur WTT 2023 / passenger_paths.csv",
        "infrastructure_sources": ["stations.csv", "block_sections.csv", "loops.csv"],
        "service_filter": "daily, non-suspended, complete through-train timings only",
        "initial_state": "no passenger already in motion; entry queues are outside the board",
        "synthetic": ["freight trains", "all train lengths", "priority weights", "disruption"],
        "model": f"{corridor.operating_mode}; one measured main and measured loops per station",
        "block_mode_selection": block_mode,
        "freight_mix": "synthetic speed factors 1.1/1.4/1.7 and weights 1/3/8" if freight_mix else "uniform speed factor 1.25 and weight 1",
        "main_selection": "longest conventional UP/DN main; third-line tracks excluded",
        "scoring": "weighted final-arrival delay; all trains drain after the 120-minute entry window",
    }
    return Scenario(f"seed-{seed}-{corridor.stations[0].code}-{corridor.stations[-1].code}",
                    seed, corridor, tuple(trains), events[0], start, metadata=metadata,
                    additional_disruptions=tuple(events[1:]))


def make_disruption(kind, trains, passenger_count, rng):
    if kind == "late_train":
        index = rng.randrange(passenger_count)
        delay = rng.randint(8, 20)
        trains[index] = replace(trains[index], release=trains[index].release + delay)
        return Disruption(kind, train_id=trains[index].id, delay=delay)
    else:
        train = rng.choice(trains)
        leg = rng.randrange(len(train.route) - 1)
        begin = min(100, max(0, train.scheduled_departures[leg] - 2))
        return Disruption(kind, block=(train.route[leg], train.route[leg + 1]),
                           start=begin, end=min(120, begin + rng.randint(15, 25)))
