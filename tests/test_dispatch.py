"""Safety, exact small-instance optima, and controller-tool contract checks."""

import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from railway_env import Block, Corridor, Disruption, Plan, Scenario, Station, Track, Train, Visit
from railway_env.baselines import dispatch
from railway_env.benchmark import benchmark
from railway_env.data import passenger_templates, unwrap_time
from railway_env.episode import DispatchEpisode
from railway_env.generator import generate
from railway_env.models import load_scenario, save_json
from railway_env.simulator import RailwaySimulator
from railway_env.solver import solve


def board(three=False, loop=600):
    codes = "ABC" if three else "AB"
    stations = tuple(Station(c, c, (Track("main", 1000, "main"), Track("loop", loop))) for c in codes)
    return Corridor("fixture", stations, tuple(Block(a, b, 5, 5) for a, b in zip(codes, codes[1:])))


def train(identifier="P", route=("A", "B"), release=0, weight=2, length=800):
    nominal = tuple(release + 5 * i for i in range(len(route)))
    return Train(identifier, "passenger" if identifier == "P" else "freight", tuple(route), length,
                 weight, release, nominal, nominal, (5,) * (len(route) - 1), (0,) * len(route))


def problem(three=False, loop=600, event=None, passenger_release=0):
    route = ("A", "B", "C") if three else ("A", "B")
    return Scenario("fixture", 0, board(three, loop),
                    (train(route=route, release=passenger_release), train("F", route[::-1], weight=1)),
                    event or Disruption("none"))


def two_leg_plan(p=0, f=6):
    return Plan((Visit("P", "A", p, p, "main"), Visit("P", "B", p + 5, p + 5, "main"),
                 Visit("F", "B", f, f, "main"), Visit("F", "A", f + 5, f + 5, "main")))


class SafetyTests(unittest.TestCase):
    def test_opposing_trains_cannot_share_a_block(self):
        result = RailwaySimulator(problem()).evaluate(two_leg_plan(0, 0))
        self.assertFalse(result["valid"])
        self.assertTrue(any("block/A/B" in e for e in result["errors"]))

    def test_resource_is_free_exactly_at_clearance_end(self):
        self.assertTrue(RailwaySimulator(problem()).evaluate(two_leg_plan(0, 6))["valid"])
        self.assertFalse(RailwaySimulator(problem()).evaluate(two_leg_plan(0, 5))["valid"])

    def test_train_must_fit_its_specific_berth(self):
        plan = two_leg_plan()
        visits = (replace(plan.visits[0], track="loop"), *plan.visits[1:])
        result = RailwaySimulator(problem()).evaluate(Plan(visits))
        self.assertFalse(result["valid"])
        self.assertTrue(any("does not fit" in e for e in result["errors"]))

    def test_signal_closure_checks_whole_traversal(self):
        event = Disruption("signal_failure", block=("A", "B"), start=3, end=8)
        result = RailwaySimulator(problem(event=event)).evaluate(two_leg_plan())
        self.assertFalse(result["valid"])
        self.assertTrue(any("signal failure" in e for e in result["errors"]))

    def test_omitting_a_train_never_earns_a_score(self):
        result = RailwaySimulator(problem()).evaluate(Plan(two_leg_plan().visits[:2]))
        self.assertFalse(result["valid"])
        self.assertIsNone(result["reward"])

    def test_duplicate_visit_is_rejected(self):
        plan = two_leg_plan()
        result = RailwaySimulator(problem()).evaluate(Plan((*plan.visits, plan.visits[0])))
        self.assertTrue(any("duplicate" in e for e in result["errors"]))

    def test_teleporting_to_finish_is_rejected(self):
        plan = two_leg_plan()
        visits = tuple(replace(v, arrival=1, departure=1) if v.train_id == "P" and v.station == "B" else v
                       for v in plan.visits)
        result = RailwaySimulator(problem()).evaluate(Plan(visits))
        self.assertTrue(any("running time" in e for e in result["errors"]))

    def test_release_time_and_timetable_are_enforced(self):
        result = RailwaySimulator(problem(passenger_release=3)).evaluate(two_leg_plan())
        self.assertFalse(result["valid"])
        self.assertTrue(any("before release" in e for e in result["errors"]))
        self.assertTrue(any("before timetable" in e for e in result["errors"]))

    def test_boolean_or_fractional_timestamps_are_rejected(self):
        for bad in (True, 0.5, -1):
            plan = two_leg_plan()
            visits = (replace(plan.visits[0], arrival=bad), *plan.visits[1:])
            self.assertFalse(RailwaySimulator(problem()).evaluate(Plan(visits))["valid"])

    def test_malformed_identifiers_are_rejected_without_crashing(self):
        plan = two_leg_plan()
        visits = (replace(plan.visits[0], track={"id": "main"}), *plan.visits[1:])
        self.assertFalse(RailwaySimulator(problem()).evaluate(Plan(visits))["valid"])

    def test_delay_after_two_hour_window_is_still_counted(self):
        s = problem()
        p = replace(s.trains[0], release=150)
        s = replace(s, trains=(p, s.trains[1]))
        result = RailwaySimulator(s).evaluate(two_leg_plan(150, 0))
        self.assertTrue(result["valid"])
        self.assertEqual(result["train_delays"]["P"], 150)
        self.assertEqual(result["weighted_delay"], 300)

    def test_station_track_capacity_is_independent_of_block_capacity(self):
        s = problem(three=True, loop=900)
        # P at B [5, 16); F reaches B at 11 using the other block.
        plan = Plan((Visit("P", "A", 0, 0, "main"), Visit("P", "B", 5, 15, "main"),
                     Visit("P", "C", 20, 20, "main"), Visit("F", "C", 6, 6, "main"),
                     Visit("F", "B", 11, 11, "main"), Visit("F", "A", 16, 16, "main")))
        result = RailwaySimulator(s).evaluate(plan)
        self.assertFalse(result["valid"])
        self.assertTrue(any("track/B/main" in e for e in result["errors"]))
        self.assertTrue(RailwaySimulator(s).evaluate(Plan(tuple(
            replace(v, track="loop") if v.train_id == "F" and v.station == "B" else v
            for v in plan.visits)))["valid"])

    def test_replay_moves_trains_and_does_not_allow_time_reversal(self):
        sim = RailwaySimulator(problem())
        sim.start(two_leg_plan())
        state = sim.advance(2)
        p = next(t for t in state["trains"] if t["id"] == "P")
        self.assertEqual(p["state"], "on_block")
        self.assertAlmostEqual(p["fraction"], 0.4)
        self.assertEqual(sim.advance(5)["trains"][0]["state"], "completed")
        with self.assertRaises(ValueError):
            sim.advance(4)


class OracleTests(unittest.TestCase):
    def test_optimum_matches_independent_enumeration(self):
        s = problem(passenger_release=3)
        values = [2 * (p - 3) + f for p in range(3, 20) for f in range(20)
                  if p + 6 <= f or f + 6 <= p]
        oracle = solve(s, 3)
        self.assertEqual(oracle["status"], "OPTIMAL")
        self.assertEqual(oracle["weighted_delay"], min(values))
        self.assertEqual(oracle["best_bound"], min(values))
        self.assertEqual(oracle["relative_gap"], 0)

    def test_short_loop_changes_the_proven_optimum(self):
        short = solve(problem(three=True, loop=600), 3)
        long = solve(problem(three=True, loop=900), 3)
        self.assertTrue(short["proven_optimal"] and long["proven_optimal"])
        self.assertEqual(short["weighted_delay"], 11)
        self.assertEqual(long["weighted_delay"], 3)
        self.assertTrue(RailwaySimulator(problem(three=True, loop=600)).evaluate(
            Plan.from_dict(short["plan"]))["valid"])

    def test_both_baselines_and_oracle_obey_signal_failure(self):
        s = problem(three=True, loop=900,
                    event=Disruption("signal_failure", block=("A", "B"), start=2, end=12))
        result = benchmark(s, 3)
        self.assertTrue(result["oracle"]["proven_optimal"])
        for policy in ("passenger_first", "controller_rule"):
            self.assertTrue(result[policy]["valid"])
            self.assertGreaterEqual(result[policy]["weighted_delay"], result["oracle"]["weighted_delay"])


class ToolTests(unittest.TestCase):
    def test_try_plan_is_isolated_and_submit_ends_episode(self):
        s = problem(three=True, loop=900)
        original = copy.deepcopy(s.to_dict())
        episode = DispatchEpisode(s)
        proposal = {"policy": "passenger_first", "holds": [{"train_id": "F", "station": "B", "until": 12}],
                    "priority_order": ["P"]}
        trial = episode.try_plan(proposal)
        self.assertTrue(trial["valid"])
        self.assertEqual(s.to_dict(), original)
        self.assertFalse(episode.submitted)
        submitted = episode.submit(proposal)
        self.assertEqual(trial["weighted_delay"], submitted["weighted_delay"])
        self.assertTrue(submitted["done"])
        with self.assertRaises(ValueError):
            episode.try_plan({})

    def test_invalid_submission_ends_without_score(self):
        episode = DispatchEpisode(problem())
        result = episode.submit({"visits": []})
        self.assertFalse(result["valid"])
        self.assertTrue(result["done"])
        self.assertIsNone(result["weighted_delay"])
        self.assertLess(result["reward"], -episode.scenario.horizon * 3)

    def test_bad_holds_and_unknown_actions_return_useful_errors(self):
        episode = DispatchEpisode(problem())
        result = episode.try_plan({"holds": [{"train_id": "missing", "station": "A", "until": 10}]})
        self.assertFalse(result["valid"])
        self.assertTrue(result["errors"])
        with self.assertRaises(ValueError):
            episode.call("reveal_answer_key")

    def test_complete_solver_plan_can_be_submitted_without_policy_compilation(self):
        s = problem()
        oracle = solve(s, 3)
        result = DispatchEpisode(s).submit(oracle["plan"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["weighted_delay"], oracle["weighted_delay"])


class DataTests(unittest.TestCase):
    def test_clock_rollover_and_corrupt_backward_times(self):
        self.assertEqual(unwrap_time("00:04", 1439), 1444)
        with self.assertRaises(ValueError):
            unwrap_time("12:00", 725)

    def test_scenario_json_round_trip(self):
        s = problem(three=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenario.json"
            save_json(path, s.to_dict())
            self.assertEqual(load_scenario(path), s)

    def test_suspended_and_non_daily_trains_are_excluded(self):
        import csv
        with tempfile.TemporaryDirectory() as tmp:
            with (Path(tmp) / "passenger_paths.csv").open("w", newline="", encoding="utf-8") as f:
                fields = ["section", "days", "suspended", "train_no", "direction", "station_code", "arrival", "departure"]
                writer = csv.DictWriter(f, fields)
                writer.writeheader()
                for number, day, suspended in (("1", "Daily", "False"), ("2", "Daily", "True"), ("3", "Mon", "False")):
                    for station, time in (("A", "00:00"), ("B", "00:05")):
                        writer.writerow(dict(zip(fields, ("fixture", day, suspended, number, "UP", station, time, time))))
            templates = passenger_templates(tmp, board())
            self.assertEqual([t["number"] for t in templates], ["1"])

    @unittest.skipUnless(Path("rail_data/processed/passenger_paths.csv").exists(), "downloaded data not present")
    def test_real_data_generator_is_seeded_and_has_one_disruption(self):
        a = generate(7, station_count=5, freight_count=2, disruption_count=1,
                     block_mode="shared", freight_mix=False)
        b = generate(7, station_count=5, freight_count=2, disruption_count=1,
                     block_mode="shared", freight_mix=False)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertEqual(a.duration, 120)
        self.assertTrue(any(t.kind == "passenger" for t in a.trains))
        self.assertEqual(sum(t.kind == "freight" for t in a.trains), 2)
        self.assertIn(a.disruption.kind, ("late_train", "signal_failure"))
        self.assertTrue(RailwaySimulator(a).evaluate(dispatch(a))["valid"])


if __name__ == "__main__":
    unittest.main()
