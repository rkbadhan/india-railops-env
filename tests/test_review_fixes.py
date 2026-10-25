"""Regression cases from the follow-up review, including an independent route oracle."""

import contextlib
import csv
import io
import json
import random
import tempfile
import unittest
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fetch_rail_data as downloads
import find_more_sources as sources
from extract import sambalpur_wtt as wtt
from railway_env import Block, Corridor, Disruption, Scenario, Station, Track, Train
from railway_env.__main__ import main
from railway_env.baselines import free_windows, plan_train
from railway_env.data import DataWarning, corridors, read_csv
from railway_env.generator import generate


def fixture():
    stations = tuple(Station(code, code, (Track("main", 1000, "main"), Track("loop", 900))) for code in "ABC")
    corridor = Corridor("fixture", stations, (Block("A", "B", 5, 5), Block("B", "C", 5, 5)))
    train = Train("P", "passenger", ("A", "B", "C"), 500, 2, 0,
                  (0, 5, 10), (0, 5, 10), (5, 5), (0, 0, 0))
    return Scenario("fixture", 0, corridor, (train,), Disruption("none"))


def write_table(directory, name, fields, rows):
    with (Path(directory) / name).open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fields)
        writer.writeheader()
        writer.writerows(rows)


def write_processed_fixture(directory):
    write_table(directory, "stations.csv", ["section", "station_code", "station_name"],
                [{"section": "fixture", "station_code": c, "station_name": c} for c in "ABC"])
    write_table(directory, "sections.csv", ["section", "line_type"],
                [{"section": "fixture", "line_type": "double"}])
    tracks = []
    for code in "ABC":
        for line, kind, length in (("1", "UP main", "800"), ("2", "common loop", "700"),
                                   ("5", "3rd line main", "2000")):
            tracks.append(dict(division="ECoR/SBP", status="read", station_code=code,
                               line_no=line, line_type=kind, length_m=length))
    write_table(directory, "loops.csv", list(tracks[0]), tracks)
    edges = [dict(section="fixture", direction=direction, from_station=a, to_station=b,
                  running_time_min="5", distance_km="5")
             for a, b, direction in (("A", "B", "UP"), ("B", "C", "UP"),
                                     ("C", "B", "DN"), ("B", "A", "DN"))]
    write_table(directory, "block_sections.csv", list(edges[0]), edges)
    paths = [dict(section="fixture", days="Daily", suspended="False", train_no="10001", direction="UP",
                  station_code=code, arrival=time, departure=time)
             for code, time in (("A", "06:00"), ("B", "06:05"), ("C", "06:10"))]
    write_table(directory, "passenger_paths.csv", list(paths[0]), paths)


class ExtractReviewTests(unittest.TestCase):
    def test_late_suspension_mark_refreshes_every_earlier_visit(self):
        tables = []
        for page, suspended, code in ((10, False, "A"), (11, True, "B"), (12, False, "C")):
            train = dict(train_no="10001", suspended=suspended, name="Test", days="Daily", mps="100",
                         **{"from": "A", "to": "C"})
            station = dict(code=code, name=code, info="", km="0", cells=[("06.00", "06.01")],
                           after_allow=[""])
            tables.append(dict(page=page, table=0, section="fixture", direction="UP", is_left=True,
                               trains=[train], stations=[station], pending=None, totals=[]))
        rows, _ = wtt.assemble(tables)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["suspended"] for r in rows))

    def test_comma_separates_whole_times_instead_of_becoming_seconds(self):
        for cell in ("12.30, 13.40", "12.30,13.40", "12:30, 13:40", "12,30, 13,40"):
            with self.subTest(cell=cell):
                self.assertEqual(wtt.first_time(cell), "12:30")
        self.assertEqual(wtt.first_time("12:30:15"), "12:30:15")
        self.assertEqual(wtt.first_time("12.30.15"), "12:30:15")
        self.assertEqual(wtt.first_time("12:30:99"), "")


class URLReviewTests(unittest.TestCase):
    def test_page_keys_preserve_functional_blank_and_repeated_parameters(self):
        base = "https://cr.indianrailways.gov.in/view_section.jsp"
        self.assertNotEqual(sources.page_key(base + "?category=1"), sources.page_key(base + "?category=2"))
        self.assertNotEqual(sources.page_key(base + "?id=1&page=1"), sources.page_key(base + "?id=1&page=2"))
        key = sources.page_key(base + "?q=&tag=b&tag=a&id=1&fontSize=12&theme=blue&lang=1")
        self.assertIn("q=", key)
        self.assertIn("tag=b&tag=a", key)
        self.assertNotIn("fontSize", key)
        self.assertNotIn("theme", key)
        self.assertEqual(sources.page_key(base + "?id=1&page=2&lang=1&fontColor=red"),
                         sources.page_key(base + "?page=2&id=1&lang=0"))

    def test_crawler_visits_both_menu_pages_addressed_without_id(self):
        home = b'''<a href="/view_section.jsp?category=one">Working Time Table</a>
                   <a href="/view_section.jsp?category=two">Working Time Table</a>'''

        def fetch(url, **kwargs):
            if "category=" not in url:
                return home, "text/html"
            category = url.rsplit("=", 1)[1]
            return f'<a href="/uploads/files/WTT-{category}.pdf">WTT</a>'.encode(), "text/html"

        with patch.object(sources, "fetch", side_effect=fetch) as calls, \
                patch.object(sources.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            result = sources.crawl("CR", "cr")
        self.assertTrue(result.complete)
        self.assertEqual(calls.call_count, 4)
        self.assertEqual(len(result.rows), 2)

    def test_osm_schema_survives_a_filename_change_for_cached_and_new_data(self):
        error = b'{"elements": [], "remark": "runtime error: timeout"}'
        self.assertEqual(downloads.check_kind(error, "renamed.json", expected="osm"), "overpass_error")
        self.assertEqual(downloads.check_kind(b'{}', "renamed.json", expected="osm"), "not_osm_json")
        with tempfile.TemporaryDirectory() as tmp, patch.object(downloads, "OUT_DIR", tmp), \
                patch.object(downloads, "fetch", return_value=(b'{"elements": []}', "application/json")) as fetch, \
                contextlib.redirect_stdout(io.StringIO()):
            downloads.save(error, str(Path(tmp) / "osm" / "renamed.json"))
            row, requested = downloads.download("osm", "renamed.json", "url", "note", expected="osm")
            self.assertTrue(requested)
            self.assertEqual(row[3], "ok")
            fetch.assert_called_once()


class InputReviewTests(unittest.TestCase):
    def test_runtime_dispatch_error_returns_an_invalid_trial_and_keeps_play_alive(self):
        requests = '\n'.join((json.dumps({"tool": "try_plan", "arguments": {"policy": "passenger_first"}}),
                              json.dumps({"tool": "observe"}))) + '\n'
        output = io.StringIO()
        with patch("railway_env.episode.dispatch", side_effect=RuntimeError("forced dispatch failure")), \
                patch("railway_env.__main__.load_scenario", return_value=fixture()), \
                patch("sys.argv", ["railway_env", "play", "fixture.json"]), \
                patch("sys.stdin", io.StringIO(requests)), contextlib.redirect_stdout(output):
            self.assertEqual(main(), 0)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 3)
        self.assertFalse(responses[1]["valid"])
        self.assertEqual(responses[1]["error_type"], "internal_error")
        self.assertIn("forced dispatch failure", responses[1]["errors"][0])
        self.assertIn("observation", responses[2])

    def test_invalid_seed_is_rejected_during_scenario_loading(self):
        for seed in ("broken", 2.5, None, True):
            data = fixture().to_dict()
            data["seed"] = seed
            with self.subTest(seed=seed), self.assertRaisesRegex(ValueError, "seed must be an integer"):
                Scenario.from_dict(data)
        self.assertEqual(replace(fixture(), seed=-7).seed, -7)

    def test_bad_numeric_rows_are_diagnosed_without_losing_valid_stretches(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_processed_fixture(tmp)
            for name, field, values in (("loops.csv", "length_m", ("bad", "nan", "-2", "inf")),
                                        ("block_sections.csv", "running_time_min", ("bad", "0", "inf"))):
                rows = read_csv(tmp, name)
                fields = list(rows[0])
                for value in values:
                    row = {**rows[0], field: value}
                    rows.append(row)
                write_table(tmp, name, fields, rows)
            with self.assertWarns(DataWarning):
                scenario = generate(7, tmp, station_count=3)
            self.assertTrue(any(t.kind == "passenger" for t in scenario.trains))
            self.assertEqual(len(scenario.corridor.stations), 3)

    def test_conventional_main_is_used_even_when_the_third_line_is_longer(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_processed_fixture(tmp)
            corridor = corridors(tmp, 3)[0]
        for station in corridor.stations:
            main = next(t for t in station.tracks if t.kind == "main")
            self.assertEqual((main.id, main.length_m), ("main:1", 800))
            self.assertNotIn("main:5", [t.id for t in station.tracks])

    def test_missing_columns_fail_clearly_instead_of_silently_fabricating_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_table(tmp, "loops.csv", ["station_code"], [{"station_code": "A"}])
            with self.assertRaisesRegex(ValueError, "missing columns"):
                read_csv(tmp, "loops.csv")


class WindowSearchTests(unittest.TestCase):
    def test_touching_and_overlapping_reservations_form_one_blocked_window(self):
        self.assertEqual(list(free_windows([(7, 9), (3, 5), (4, 7)], 12)), [(0, 3), (9, 12)])

    def test_earliest_completion_matches_exhaustive_minute_search(self):
        scenario = fixture()
        small = SimpleNamespace(corridor=scenario.corridor, horizon=28, clearance=1)
        rng = random.Random(42)

        def available(intervals, start, end):
            return all(end <= a or b <= start for a, b in intervals)

        for case in range(25):
            dwell = rng.randrange(4)
            train = replace(scenario.trains[0], release=rng.randrange(4), length_m=rng.choice((500, 950)),
                            scheduled_arrivals=(0, 5, 10 + dwell), scheduled_departures=(0, 5 + dwell, 10 + dwell),
                            dwell_times=(0, dwell, 0))
            reservations = defaultdict(list)
            for code in "ABC":
                for track in ("main", "loop"):
                    start = rng.randrange(0, 22)
                    reservations["track", code, track].append((start, start + rng.randrange(1, 6)))
            for a, b in (("A", "B"), ("B", "C")):
                start = rng.randrange(0, 18)
                reservations["block", a, b].append((start, start + rng.randrange(1, 6)))
            holds = {("P", "A"): rng.randrange(7), ("P", "B"): rng.randrange(18)}
            expected = None
            for departure_a in range(max(train.release, holds["P", "A"]), 18):
                arrival_b = departure_a + 5
                for departure_b in range(max(arrival_b + dwell, holds["P", "B"]), 23):
                    arrival_c = departure_b + 5
                    if not available(reservations["block", "A", "B"], departure_a, arrival_b + 1) or \
                            not available(reservations["block", "B", "C"], departure_b, arrival_c + 1):
                        continue
                    tracks = ("main", "loop") if train.length_m <= 900 else ("main",)
                    if all(any(available(reservations["track", code, track], start, end)
                               for track in tracks) for code, start, end in
                           (("A", departure_a, departure_a + 1), ("B", arrival_b, departure_b + 1),
                            ("C", arrival_c, arrival_c + 1))):
                        expected = arrival_c if expected is None else min(expected, arrival_c)
            with self.subTest(case=case):
                if expected is None:
                    with self.assertRaises(ValueError):
                        plan_train(small, train, reservations, holds)
                else:
                    result = plan_train(small, train, reservations, holds)
                    self.assertEqual(result[-1].arrival, expected)


if __name__ == "__main__":
    unittest.main()
