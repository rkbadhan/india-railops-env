"""Difficulty accounting, directional safety, multiple faults and final scores."""

import contextlib
import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_dispatch import problem, two_leg_plan, train
from railway_env.baselines import dispatch
from railway_env.benchmark import benchmark
from railway_env.episode import DispatchEpisode
from railway_env.models import Disruption, Plan, Scenario, Visit
from railway_env.scoring import normalized_score
from railway_env.simulator import RailwaySimulator
from railway_env.solver import solve
from railway_env.sweep import parse_seeds, summarize, sweep, format_summary
from railway_env.models import save_json


class SweepTests(unittest.TestCase):
    def test_seed_ranges_are_inclusive_and_deduplicated(self):
        self.assertEqual(parse_seeds("0-3,2,7,-2--1"), [0, 1, 2, 3, 7, -2, -1])
        for value in ("3-1", "hello", "", "0-10001"):
            with self.assertRaises(ValueError):
                parse_seeds(value)

    def test_unproven_and_failed_cases_cannot_inflate_optimal_rate(self):
        cases = [{"passenger_first": {"weighted_delay": 10}, "oracle": {
            "proven_optimal": proven, "weighted_delay": score, "wall_time_seconds": .2}}
                 for proven, score in ((True, 10), (True, 6), (False, 10))]
        cases.append({"error": "failed generation"})
        summary = summarize(cases)
        self.assertEqual(summary["passenger_first_optimal_percent"], 50)
        self.assertEqual(summary["proven_optimal"], 2)
        self.assertEqual(summary["completed"], 3)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["mean_gap"], 2)
        self.assertEqual(summary["max_gap"], 4)

    def test_sweep_runs_both_baselines_and_solver_and_saves_failures(self):
        with tempfile.TemporaryDirectory() as tmp, patch("railway_env.sweep.generate", side_effect=[
                problem(), ValueError("missing data")]), contextlib.redirect_stderr(io.StringIO()):
            result = sweep([1, 2], tmp, 2)
            self.assertTrue(result["cases"][0]["passenger_first"]["valid"])
            self.assertTrue(result["cases"][0]["controller_rule"]["valid"])
            self.assertTrue(result["cases"][0]["oracle"]["proven_optimal"])
            self.assertEqual(result["summary"]["errors"], 1)
            self.assertTrue((Path(tmp) / "sweep.json").exists())
            self.assertIn("1 / 2", format_summary(result["summary"]))

    def test_sweep_cli(self):
        from railway_env.__main__ import main
        with tempfile.TemporaryDirectory() as tmp, patch("railway_env.sweep.generate", return_value=problem()), \
                patch("sys.argv", ["railway_env", "sweep", "--seeds", "0-1", "--output", tmp]), \
                contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(), 0)
            self.assertIn("Passenger-first optimal", output.getvalue())

    def test_public_tool_search_records_real_play_session(self):
        from railway_env.tool_search import search
        with tempfile.TemporaryDirectory() as tmp:
            scenario_path = Path(tmp) / "scenario.json"
            save_json(scenario_path, problem().to_dict())
            result = search(scenario_path, Path(tmp) / "transcript.json", budget=2)
            self.assertTrue(result["result"]["valid"])
            self.assertTrue(result["result"]["done"])
            self.assertEqual([r["request"]["tool"] for r in result["transcript"][1:]],
                             ["try_plan", "try_plan", "submit"])
            for record in result["transcript"][1:-1]:
                self.assertNotIn("reference", record["response"])

    def test_search_moves_include_holds_of_5_10_15_minutes_and_can_exclude_them(self):
        from railway_env.tool_search import candidate_moves
        plan = {"visits": [{"train_id": "A", "station": "X", "departure": 10},
                           {"train_id": "A", "station": "Y", "departure": 20},
                           {"train_id": "B", "station": "X", "departure": 12},
                           {"train_id": "B", "station": "Y", "departure": 22}]}
        args = (["A", "B"], [], plan, {"A": 8, "B": 1}, {"A": ["X", "Y", "Z"], "B": ["X", "Y", "Z"]})
        held = [h for _, h in candidate_moves(*args, True) if h]
        self.assertEqual({h[0]["until"] - {"A": 10, "B": 12}[h[0]["train_id"]] for h in held if h[0]["station"] == "X"},
                         {5, 10, 15})
        self.assertEqual(held[0], [{"train_id": "B", "station": "X", "until": 17}])  # lightest train first
        self.assertEqual([m for m in candidate_moves(*args, False)], [(["B", "A"], [])])
        moves = list(candidate_moves(*args, True))
        self.assertEqual(moves[0], (["B", "A"], []))  # reorderings come before any hold
        self.assertTrue(all(not h for _, h in moves[:1]) and all(h for _, h in moves[1:]))

    def test_search_with_one_trial_tries_only_passenger_first_then_submits(self):
        from railway_env.tool_search import search
        with tempfile.TemporaryDirectory() as tmp:
            scenario_path = Path(tmp) / "scenario.json"
            save_json(scenario_path, problem().to_dict())
            result = search(scenario_path, Path(tmp) / "transcript.json", budget=1)
            self.assertEqual([r["request"]["tool"] for r in result["transcript"][1:]], ["try_plan", "submit"])
            self.assertEqual(result["trials"], 1)

    @unittest.skipUnless(Path("rail_data/processed/passenger_paths.csv").exists(), "real data absent")
    def test_new_default_matches_selected_profile_and_source_line(self):
        from railway_env.generator import generate
        a, b = generate(0), generate(0)
        self.assertEqual(a, b)
        self.assertEqual(len(a.corridor.stations), 6)
        self.assertEqual(sum(t.kind == "freight" for t in a.trains), 8)
        self.assertEqual({e.kind for e in a.disruptions}, {"late_train", "signal_failure"})
        self.assertEqual(a.corridor.source_line_type, "double")
        self.assertEqual(a.corridor.block_mode, "directional")
        self.assertTrue(RailwaySimulator(a).evaluate(dispatch(a))["valid"])


class DirectionalTests(unittest.TestCase):
    def directional(self):
        s = problem()
        return replace(s, corridor=replace(s.corridor, block_mode="directional"))

    def test_opposite_directions_can_run_simultaneously(self):
        s = self.directional()
        self.assertIn("separate block per direction", DispatchEpisode(s).observe())
        self.assertTrue(RailwaySimulator(s).evaluate(two_leg_plan(0, 0))["valid"])
        self.assertFalse(RailwaySimulator(problem()).evaluate(two_leg_plan(0, 0))["valid"])
        result = benchmark(s, 2)
        for policy in ("passenger_first", "controller_rule", "oracle"):
            self.assertEqual(result[policy]["weighted_delay"], 0)
        self.assertTrue(result["oracle"]["proven_optimal"])

    def test_same_direction_still_excludes_simultaneous_trains(self):
        s = self.directional()
        s = replace(s, trains=(train(), train("F", ("A", "B"), length=500, weight=1)))
        plan = Plan((Visit("P", "A", 0, 0, "main"), Visit("P", "B", 5, 5, "main"),
                     Visit("F", "A", 0, 0, "loop"), Visit("F", "B", 5, 5, "loop")))
        result = RailwaySimulator(s).evaluate(plan)
        self.assertTrue(any("block/A/B" in e for e in result["errors"]))
        self.assertEqual(solve(s, 2)["weighted_delay"], 6)
        for policy in ("passenger_first", "controller_rule"):
            self.assertTrue(RailwaySimulator(s).evaluate(dispatch(s, policy))["valid"])

    def test_closure_blocks_both_directions_and_overlaps_merge(self):
        s = replace(self.directional(),
                    disruption=Disruption("signal_failure", block=("A", "B"), start=0, end=10),
                    additional_disruptions=(Disruption("signal_failure", block=("B", "A"), start=5, end=15),))
        self.assertEqual(len(list(s.closures())), 2)
        self.assertFalse(RailwaySimulator(s).evaluate(two_leg_plan(0, 0))["valid"])
        result = benchmark(s, 2)
        for policy in ("passenger_first", "controller_rule", "oracle"):
            self.assertEqual(result[policy]["weighted_delay"], 45)

    def test_multiple_faults_and_mode_round_trip(self):
        s = self.directional()
        s = replace(s, trains=(replace(s.trains[0], release=8), s.trains[1]),
                    disruption=Disruption("late_train", train_id="P", delay=8),
                    additional_disruptions=(Disruption("signal_failure", block=("A", "B"), start=0, end=12),))
        self.assertEqual(Scenario.from_dict(s.to_dict()), s)
        self.assertIn("CLOSED", DispatchEpisode(s).observe())
        self.assertTrue(solve(s, 2)["proven_optimal"])


class NormalizedTests(unittest.TestCase):
    def test_scores_and_negative_improvement(self):
        oracle = {"proven_optimal": True, "weighted_delay": 10}
        self.assertEqual(normalized_score(15, 20, oracle)["improvement_share"], .5)
        self.assertEqual(normalized_score(30, 20, oracle)["improvement_share"], -1)
        self.assertEqual(normalized_score(15, 20, oracle)["excess_delay_over_optimum"], 5)

    def test_reward_is_defined_and_bounded_without_baseline_headroom(self):
        oracle = {"proven_optimal": True, "weighted_delay": 200}  # scale = max(0, 1, 5% of 200) = 10
        tie = normalized_score(200, 200, oracle)
        self.assertEqual((tie["normalized_reward"], tie["reward_scale"]), (1.0, 10))
        self.assertIsNone(tie["improvement_share"])
        self.assertEqual(normalized_score(205, 200, oracle)["normalized_reward"], .5)
        self.assertEqual(normalized_score(210, 200, oracle)["normalized_reward"], 0)
        self.assertEqual(normalized_score(5000, 200, oracle)["normalized_reward"], -.9)
        invalid = normalized_score(None, 200, oracle)["normalized_reward"]
        self.assertEqual(invalid, -1)
        self.assertLess(invalid, normalized_score(10**9, 200, oracle)["normalized_reward"])

    def test_reward_matches_clipped_share_when_headroom_exceeds_floor(self):
        oracle = {"proven_optimal": True, "weighted_delay": 100}
        for cost, expected in ((100, 1), (150, .5), (200, 0), (260, -.6), (900, -.9)):
            result = normalized_score(cost, 200, oracle)
            self.assertAlmostEqual(result["normalized_reward"], expected)
            self.assertAlmostEqual(result["normalized_reward"], max(-.9, result["improvement_share"]))

    def test_tiny_headroom_cannot_swing_reward_by_tens_of_points(self):
        oracle = {"proven_optimal": True, "weighted_delay": 800}
        result = normalized_score(1000, 801, oracle)
        self.assertEqual(result["improvement_share"], -199)
        self.assertEqual(result["normalized_reward"], -.9)
        self.assertGreater(normalized_score(801, 801, oracle)["normalized_reward"], .9)

    def test_unproved_optimum_has_no_reward(self):
        result = normalized_score(15, 20, {"proven_optimal": False, "weighted_delay": 10})
        self.assertIsNone(result["normalized_reward"])
        self.assertIsNone(normalized_score(None, 20, {"proven_optimal": False, "weighted_delay": 10})["normalized_reward"])

    def test_undefined_scores_are_explicit(self):
        for cost, pf, oracle, status in (
                (10, 10, {"proven_optimal": True, "weighted_delay": 10}, "no_baseline_headroom"),
                (10, 20, {"proven_optimal": False, "weighted_delay": 10}, "optimum_not_proven"),
                (None, 20, {"proven_optimal": True, "weighted_delay": 10}, "invalid_plan")):
            result = normalized_score(cost, pf, oracle)
            self.assertIsNone(result["improvement_share"])
            self.assertEqual(result["normalization_status"], status)

    def test_stored_oracle_is_available_only_after_irrevocable_submission(self):
        from railway_env.references import make_reference
        scenario = problem()
        reference = make_reference(scenario, benchmark(scenario, 2))
        episode = DispatchEpisode(scenario, reference=reference)
        with patch("railway_env.solver.solve", wraps=solve) as oracle:
            episode.observe()
            episode.try_plan({"policy": "passenger_first"})
            oracle.assert_not_called()
            result = episode.submit({"policy": "passenger_first"})
            oracle.assert_not_called()
        self.assertTrue(result["done"])
        self.assertIn("improvement_share", result)
        self.assertIn("best_score", result["reference"])
        with self.assertRaises(ValueError):
            episode.try_plan({})
