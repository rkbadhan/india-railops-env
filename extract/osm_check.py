"""
Compare the OpenStreetMap rail extract with stations.csv and loops.csv.

Usage:
    .venv/Scripts/python.exe extract/osm_check.py

Reads  rail_data/infrastructure/osm/sambalpur_rail_osm.json (Overpass, ODbL)
       rail_data/processed/stations.csv, loops.csv
Writes rail_data/processed/osm_stations.csv

OSM station nodes carry the station code in `ref`. Track ways are split into
many short pieces, so rather than counting ways, the number of parallel tracks
at a station is measured geometrically: short cross-lines are laid across the
main line at the station (and 150 m either side) and the tracks each one
crosses are counted. That count (main lines + loops + sidings alongside) is
what gets compared with the running lines in loops.csv.
"""

import csv
import json
import math
import os
import sys
from collections import Counter, defaultdict

OSM = os.path.join("rail_data", "infrastructure", "osm", "sambalpur_rail_osm.json")
STATIONS = os.path.join("rail_data", "processed", "stations.csv")
LOOPS = os.path.join("rail_data", "processed", "loops.csv")
OUT = os.path.join("rail_data", "processed", "osm_stations.csv")

CROSS_HALF_WIDTH = 120  # m either side of the main line
CROSS_OFFSETS = (-150, 0, 150)  # m along the line
NEARBY = 1000  # m, for siding/yard/spur length near a station
MAX_SNAP = 400  # m, station node to nearest main track


def local_xy(lat0, lon0):
    k = math.cos(math.radians(lat0))
    return lambda lat, lon: ((lon - lon0) * 111320 * k, (lat - lat0) * 110540)


def seg_intersect(p1, p2, q1, q2):
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1, d2 = cross(q1, q2, p1), cross(q1, q2, p2)
    d3, d4 = cross(p1, p2, q1), cross(p1, p2, q2)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def nearest_on_segment(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length2 = dx * dx + dy * dy or 1e-9
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
    q = (ax + t * dx, ay + t * dy)
    return math.dist(p, q), q, (dx, dy)


def clipped_length(points, centre, radius):
    """Length of a polyline inside a circle, approximated per segment by its midpoint."""
    total = 0.0
    for a, b in zip(points, points[1:]):
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if math.dist(mid, centre) <= radius:
            total += math.dist(a, b)
    return total


def main():
    data = json.load(open(OSM, encoding="utf-8"))
    # Overpass lists a station that sits on a track twice: with its tags, then
    # again as a bare way point ("out skel"). Keep the tagged copy.
    nodes = {}
    for e in data["elements"]:
        if e["type"] == "node" and ("tags" in e or e["id"] not in nodes):
            nodes[e["id"]] = e
    ways = [e for e in data["elements"] if e["type"] == "way" and e.get("tags", {}).get("railway") == "rail"]
    lat0 = sum(n["lat"] for n in nodes.values()) / len(nodes)
    lon0 = sum(n["lon"] for n in nodes.values()) / len(nodes)
    xy = local_xy(lat0, lon0)

    tracks = []  # (service, usage, [xy points])
    for w in ways:
        pts = [xy(nodes[n]["lat"], nodes[n]["lon"]) for n in w["nodes"] if n in nodes]
        if len(pts) >= 2:
            tracks.append((w["tags"].get("service", ""), w["tags"].get("usage", ""), pts))

    tagged = [n for n in nodes.values() if "tags" in n]
    osm_stations = [n for n in tagged if n["tags"].get("railway") in ("station", "halt")]
    counts = {
        "station nodes": sum(n["tags"]["railway"] == "station" for n in osm_stations),
        "halt nodes": sum(n["tags"]["railway"] == "halt" for n in osm_stations),
        "switch nodes": sum(n["tags"].get("railway") == "switch" for n in tagged),
        "track ways (railway=rail)": len(ways),
    }
    for service, n in Counter(w["tags"].get("service", "(running line)") for w in ways).most_common():
        counts[f"  service={service}"] = n

    by_ref = defaultdict(list)
    for n in osm_stations:
        ref = n["tags"].get("ref", "").upper().strip()
        if ref:
            by_ref[ref].append(n)

    ours = list(csv.DictReader(open(STATIONS, encoding="utf-8")))
    loops = list(csv.DictReader(open(LOOPS, encoding="utf-8")))
    sections = defaultdict(set)
    for s in ours:
        if s["station_code"] and s["station_code"] != "IBS":
            sections[s["station_code"]].add(s["section"])
    running = defaultdict(lambda: {"lines": 0, "loops": 0, "mains": 0, "status": ""})
    for r in loops:
        if r["division"] != "ECoR/SBP":
            continue
        entry = running[r["station_code"]]
        entry["status"] = entry["status"] or r["status"]
        if r["line_no"] and r["line_type"]:
            entry["lines"] += 1
            entry["loops"] += "loop" in r["line_type"]
            entry["mains"] += "main" in r["line_type"] and "loop" not in r["line_type"]

    mains = [t for t in tracks if t[0] == "" or t[1] == "main"]
    rows = []
    by_name = defaultdict(list)
    for n in osm_stations:
        by_name[n["tags"].get("name", "").lower().replace(" ", "")].append(n)
    names = {s["station_code"]: s["station_name"] for s in ours}

    for code in sorted(sections):
        found = by_ref.get(code, [])
        if not found:
            # Some OSM stations have no ref; fall back to the name.
            found = [n for n in by_name.get(names.get(code, "").lower().replace(" ", "").rstrip("."), [])
                     if not n["tags"].get("ref")]
        row = {"station_code": code, "sections": "; ".join(sorted(sections[code])), "in_osm": bool(found),
               "osm_name": "", "osm_railway": "", "tracks_across": "", "tracks_across_by_service": "",
               "osm_running_plus_siding": "",
               "siding_m_within_1km": "", "yard_m_within_1km": "", "spur_m_within_1km": "",
               "running_lines_loops_csv": running[code]["lines"] if code in running else "",
               "loops_loops_csv": running[code]["loops"] if code in running else "", "verdict": ""}
        if not found:
            row["verdict"] = "missing from OSM"
            rows.append(row)
            continue
        node = found[0]
        p = xy(node["lat"], node["lon"])
        row["osm_name"], row["osm_railway"] = node["tags"].get("name", ""), node["tags"]["railway"]
        best = min((nearest_on_segment(p, a, b) for _, _, pts in mains for a, b in zip(pts, pts[1:])),
                   key=lambda x: x[0], default=None)
        if not best or best[0] > MAX_SNAP:
            row["verdict"] = "OSM station has no running line nearby"
            rows.append(row)
            continue
        _, q, (dx, dy) = best
        norm = math.hypot(dx, dy) or 1
        ux, uy = dx / norm, dy / norm  # along the line
        nx, ny = -uy, ux               # across it
        best_count, best_services = 0, Counter()
        for off in CROSS_OFFSETS:
            c = (q[0] + ux * off, q[1] + uy * off)
            a = (c[0] - nx * CROSS_HALF_WIDTH, c[1] - ny * CROSS_HALF_WIDTH)
            b = (c[0] + nx * CROSS_HALF_WIDTH, c[1] + ny * CROSS_HALF_WIDTH)
            services = Counter()
            for service, _, pts in tracks:
                if service == "crossover":
                    continue
                if any(seg_intersect(a, b, s1, s2) for s1, s2 in zip(pts, pts[1:])):
                    services[service or "running"] += 1
            if sum(services.values()) > best_count:
                best_count, best_services = sum(services.values()), services
        row["tracks_across"] = best_count
        row["tracks_across_by_service"] = " ".join(f"{k}:{v}" for k, v in sorted(best_services.items()))
        for service in ("siding", "yard", "spur"):
            row[f"{service}_m_within_1km"] = round(sum(clipped_length(pts, q, NEARBY)
                                                       for s, _, pts in tracks if s == service))
        # Indian stations' passing loops are usually tagged service=siding in OSM;
        # yard and spur tracks are goods/industrial lines, so leave those out here.
        lines = running[code]["lines"] if code in running else 0
        main_count = max(1, running[code]["mains"]) if code in running else 1
        osm_lines = best_services["running"] + best_services["siding"]
        row["osm_running_plus_siding"] = osm_lines
        if not lines:
            row["verdict"] = f"OSM shows {osm_lines} running/siding tracks; no line count from SWR/diagram"
        elif osm_lines >= lines:
            row["verdict"] = "OSM has all running lines"
        elif osm_lines > main_count:
            row["verdict"] = f"OSM has some loops ({osm_lines} of {lines} lines)"
        elif osm_lines == main_count:
            row["verdict"] = f"OSM has main line(s) only ({osm_lines} of {lines} lines)"
        else:
            row["verdict"] = (f"OSM has fewer tracks than main lines "
                              f"({osm_lines} of {lines} lines, {main_count} main)")
        rows.append(row)

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"OSM extract ({data.get('osm3s', {}).get('timestamp_osm_base', '?')}):")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    ours_codes = set(sections)
    osm_codes = set(by_ref)
    print(f"stations.csv codes: {len(ours_codes)}; found in OSM by ref: {len(ours_codes & osm_codes)}")
    print("  missing from OSM:", sorted(ours_codes - osm_codes))
    print(f"  OSM station refs in the box but not in stations.csv: {len(osm_codes - ours_codes)}")
    print(Counter(r["verdict"].split(" (")[0] for r in rows).most_common())
    print(f"  wrote {OUT}: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
