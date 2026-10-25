"""Build verified stretches and passenger templates from the processed CSVs."""

import csv
import math
import re
import warnings
from collections import defaultdict
from pathlib import Path

from .models import Block, Corridor, Station, Track, integer


class DataWarning(UserWarning):
    """A malformed source row was excluded without discarding the other data."""


REQUIRED_COLUMNS = {
    "stations.csv": {"section", "station_code", "station_name"},
    "sections.csv": {"section", "line_type"},
    "loops.csv": {"division", "status", "station_code", "line_no", "line_type", "length_m"},
    "block_sections.csv": {"section", "direction", "from_station", "to_station", "running_time_min", "distance_km"},
    "passenger_paths.csv": {"section", "days", "suspended", "train_no", "direction", "station_code", "arrival", "departure"},
}


def bad_row(name, index, reason):
    warnings.warn(f"{name} record {index}: {reason}; excluded", DataWarning, stacklevel=2)


def read_csv(directory, name):
    path = Path(directory) / name
    if not path.exists():
        raise ValueError(f"missing {path}; run the download/extraction steps in README.md")
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = REQUIRED_COLUMNS.get(name, set())
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path}: missing columns {', '.join(sorted(missing))}; rerun the relevant extractor")
        rows = []
        for index, row in enumerate(reader, start=2):
            if None in row or any(row[key] is None for key in required):
                bad_row(name, index, "wrong number of CSV fields")
                continue
            rows.append(row)
        return rows


def corridors(directory, station_count=5):
    """Contiguous block-table stretches with known holding lengths and both running times."""
    integer(station_count, "station_count", 3)
    stations = {(r["section"], r["station_code"]): r for r in read_csv(directory, "stations.csv")
                if r["station_code"] and r["station_code"] != "IBS"}
    types = {r["section"]: r["line_type"] for r in read_csv(directory, "sections.csv")}
    lengths = defaultdict(dict)
    rejected_tracks = set()
    for index, row in enumerate(read_csv(directory, "loops.csv"), start=2):
        if row["division"] != "ECoR/SBP" or row["status"] != "read":
            continue
        kind = row["line_type"].lower()
        # Version 1 retains a conventional main, not a longer third-line berth.
        if re.search(r"\b(?:3rd|third)\b", kind):
            continue
        track_kind = "main" if "main" in kind and "loop" not in kind else \
            "loop" if "loop" in kind and "main" not in kind else None
        if track_kind is None:
            continue
        try:
            if not row["station_code"].strip() or not row["line_no"].strip():
                raise ValueError("missing station code or line number")
            track = Track(track_kind + ":" + row["line_no"], math.floor(float(row["length_m"])), track_kind)
        except (ValueError, TypeError, OverflowError) as error:
            bad_row("loops.csv", index, str(error))
            continue
        key = row["station_code"], track.id
        if key in rejected_tracks:
            continue
        existing = lengths[key[0]].get(track.id)
        if existing is not None and existing != track:
            # Conflicting lengths are unknown, rather than whichever row came first.
            del lengths[key[0]][track.id]
            rejected_tracks.add(key)
            bad_row("loops.csv", index, f"conflicting lengths for {key[0]}/{track.id}")
        else:
            lengths[key[0]][track.id] = track
    blocks = defaultdict(dict)
    for index, row in enumerate(read_csv(directory, "block_sections.csv"), start=2):
        if row["from_station"] == "TOTAL":
            continue
        try:
            minutes = math.ceil(float(row["running_time_min"]))
            distance = float(row["distance_km"])
            if minutes < 1 or not math.isfinite(distance) or distance <= 0:
                raise ValueError("running time and distance must be positive and finite")
            if row["direction"] not in ("UP", "DN") or not all(
                    row[key].strip() for key in ("section", "from_station", "to_station")):
                raise ValueError("invalid direction or missing section/station code")
            if row["from_station"] == row["to_station"]:
                raise ValueError("block endpoints must differ")
        except (ValueError, TypeError, OverflowError) as error:
            bad_row("block_sections.csv", index, str(error))
            continue
        blocks[row["section"]][(row["from_station"], row["to_station"])] = (minutes, distance, row["direction"])
    result = []
    for section, edges in blocks.items():
        up = {a: b for (a, b), (_, _, direction) in edges.items() if direction == "UP"}
        for start in up:
            codes = [start]
            while len(codes) < station_count and codes[-1] in up:
                nxt = up[codes[-1]]
                if nxt in codes:
                    break
                codes.append(nxt)
            if len(codes) != station_count:
                continue
            selected = []
            for code in codes:
                row = stations.get((section, code))
                mains = [t for t in lengths[code].values() if t.kind == "main"]
                loops = [t for t in lengths[code].values() if t.kind == "loop"]
                if not row or not mains:
                    break
                # One measured main is retained for the shared-block training abstraction.
                main = max(mains, key=lambda t: (t.length_m, t.id))
                selected.append(Station(code, row["station_name"], (main, *loops)))
            if len(selected) != station_count or not any(t.kind == "loop" for s in selected[1:-1] for t in s.tracks):
                continue
            stretch = []
            for a, b in zip(codes, codes[1:]):
                if (a, b) not in edges or (b, a) not in edges:
                    break
                stretch.append(Block(a, b, edges[a, b][0], edges[b, a][0], edges[a, b][1]))
            if len(stretch) == station_count - 1:
                result.append(Corridor(section, tuple(selected), tuple(stretch), types.get(section, "unknown")))
    return result


def clock_minute(text):
    if not isinstance(text, str):
        raise ValueError("clock time must be text")
    parts = [int(p) for p in text.split(":")]
    if len(parts) not in (2, 3) or not (0 <= parts[0] < 24 and 0 <= parts[1] < 60):
        raise ValueError("invalid clock time")
    if len(parts) == 3 and not 0 <= parts[2] < 60:
        raise ValueError("invalid seconds")
    return parts[0] * 60 + parts[1] + (1 if len(parts) == 3 and parts[2] else 0)


def unwrap_time(text, previous):
    value = clock_minute(text)
    while value < previous - 720:
        value += 1440
    if value < previous:
        raise ValueError("non-monotonic timetable time")
    return value


def passenger_templates(directory, corridor):
    """Daily, non-suspended through trains with complete times on this stretch.

    Train-specific booked running times retain timetable allowances. Other-day
    services are excluded until a source-date calendar is implemented.
    """
    grouped = defaultdict(list)
    excluded = set()
    rows = read_csv(directory, "passenger_paths.csv")
    for index, row in enumerate(rows, start=2):
        if row["section"] != corridor.section:
            continue
        key = row["train_no"], row["direction"]
        suspended = row["suspended"].lower().strip()
        if not row["train_no"].strip() or row["direction"] not in ("UP", "DN") or \
                suspended not in ("false", "0", "true", "1"):
            excluded.add(key)
            bad_row("passenger_paths.csv", index, "invalid train identity, direction, or suspended flag")
            continue
        if suspended in ("true", "1"):
            excluded.add(key)
        if row["days"].lower().strip() == "daily":
            grouped[key].append(row)
    codes = tuple(s.code for s in corridor.stations)
    out = []
    for (number, direction), visits in sorted(grouped.items()):
        if (number, direction) in excluded:
            continue
        selected = [r for r in visits if r["station_code"] in codes]
        route = tuple(r["station_code"] for r in selected)
        if route not in (codes, codes[::-1]):
            continue
        arrivals, departures = [], []
        previous = 0
        try:
            for row in selected:
                arrival = unwrap_time(row["arrival"] or row["departure"], previous)
                departure = unwrap_time(row["departure"] or row["arrival"], arrival)
                arrivals.append(arrival)
                departures.append(departure)
                previous = departure
        except (ValueError, TypeError):
            continue
        running = [arrivals[i + 1] - departures[i] for i in range(len(route) - 1)]
        if not all(0 < t <= 90 for t in running) or arrivals[-1] - arrivals[0] >= 120:
            continue
        offset = (arrivals[0] // 1440) * 1440
        arrivals = tuple(t - offset for t in arrivals)
        departures = tuple(t - offset for t in departures[:-1]) + (arrivals[-1],)
        out.append({"number": number, "direction": direction, "route": route,
                    "arrivals": arrivals, "departures": departures, "running": tuple(running),
                    "dwell": tuple(d - a for a, d in zip(arrivals, departures))})
    return out
