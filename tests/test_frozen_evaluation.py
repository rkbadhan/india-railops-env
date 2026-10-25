"""Stored answers, server-side budgets, drift and repeated model-evaluation accounting."""

import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_dispatch import problem
from railway_env.benchmark import benchmark
from railway_env.episode import DispatchEpisode, tools_for
from railway_env.model_harness import comparisons, run_episode, summarize, usage_totals, run
from railway_env.models import save_json
from railway_env.references import make_reference, validate_reference
from railway_env.suite import freeze, load_suite, check_drift, file_hash


class FrozenEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = problem()
        cls.benchmark = benchmark(cls.scenario, 2)
        cls.reference = make_reference(cls.scenario, cls.benchmark)

    def episode(self, **kwargs):
        return DispatchEpisode(self.scenario, reference=copy.deepcopy(self.reference), **kwargs)

    def test_submit_does_not_solve_even_when_ortools_is_unavailable(self):
        episode = self.episode()
        with patch("railway_env.solver.solve", side_effect=ImportError("OR-Tools missing")) as solve:
            result = episode.submit({"policy": "passenger_first"})
        solve.assert_not_called()
        self.assertTrue(result["done"])
        self.assertTrue(result["valid"])
        self.assertIn("best_score", result["reference"])

    def test_reference_scoring_failure_is_terminal_environment_error(self):
        episode = self.episode()
        with patch("railway_env.episode.normalized_score", side_effect=RuntimeError("disagreement")):
            result = episode.submit({"policy": "passenger_first"})
        self.assertTrue(result["done"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["normalization_status"], "evaluation_error")
        self.assertIsNone(result["improvement_share"])
        self.assertEqual(result["evaluation_error"]["type"], "RuntimeError")

    def test_no_reference_keeps_raw_score_without_implicit_solve(self):
        with patch("railway_env.solver.solve") as solve:
            result = DispatchEpisode(self.scenario).submit({"policy": "passenger_first"})
        solve.assert_not_called()
        self.assertTrue(result["valid"])
        self.assertEqual(result["normalization_status"], "reference_unavailable")

    def test_unproved_incumbent_never_gets_full_marks(self):
        reference = copy.deepcopy(self.reference)
        reference["oracle"].update(status="FEASIBLE", proven_optimal=False, best_bound=0)
        result = DispatchEpisode(self.scenario, reference=reference).submit({"policy": "passenger_first"})
        self.assertIsNone(result["improvement_share"])
        self.assertIsNone(result["reference"]["best_score"])
        self.assertEqual(result["reference"]["best_bound"], 0)
        self.assertIsNotNone(result["reference"]["incumbent"])

    def test_reference_is_bound_to_contents_not_seed_or_id(self):
        changed = replace(self.scenario, metadata={"changed": True})
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate_reference(changed, self.reference)

    def test_bad_stored_plan_or_false_proof_is_rejected(self):
        for key in ("plan", "bound"):
            reference = copy.deepcopy(self.reference)
            if key == "plan":
                reference["oracle"]["plan"]["visits"].pop()
            else:
                reference["oracle"]["best_bound"] -= 1
            with self.assertRaises(ValueError):
                validate_reference(self.scenario, reference)

    def test_trial_budget_counts_invalid_proposals_and_cannot_be_bypassed(self):
        episode = self.episode(trial_budget=2, allow_timetable=False)
        self.assertFalse(episode.try_plan({"visits": []})["valid"])
        self.assertTrue(episode.try_plan({"policy": "passenger_first"})["valid"])
        self.assertEqual(episode.try_plan({})["error_type"], "trial_budget_exhausted")
        self.assertEqual(episode.trials_used, 2)
        self.assertTrue(episode.submit({"policy": "passenger_first"})["done"])
        self.assertTrue(all("visits" not in t["input_schema"]["properties"] for t in tools_for(False)))
        self.assertTrue(all("visits" in t["input_schema"]["properties"] for t in tools_for(True)))

    def prepare_suite(self, root):
        source = root / "source" / "seed_0"
        save_json(source / "scenario.json", self.scenario.to_dict())
        save_json(source / "benchmark.json", self.benchmark)
        data = root / "input.csv"
        data.write_text("original")
        suite = root / "frozen"
        manifest = freeze(root / "source", suite, [0], {"input.csv": file_hash(data)})
        return suite, manifest, data

    def test_freeze_verify_drift_and_tamper_without_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite, manifest, data = self.prepare_suite(root)
            self.assertNotIn(b"\r\n", (suite / "manifest.json").read_bytes())
            with patch("railway_env.generator.generate", side_effect=AssertionError("must not generate")), \
                    patch("railway_env.solver.solve", side_effect=AssertionError("must not solve")):
                loaded, cases = load_suite(suite)
                self.assertEqual(len(cases), 1)
                self.assertEqual(check_drift(loaded, root), [])
                data.write_text("changed")
                self.assertEqual(len(check_drift(loaded, root)), 1)
                self.assertTrue(cases[0]["reference"]["oracle"]["proven_optimal"])
            (suite / "seed_0" / "scenario.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "changed"):
                load_suite(suite)
            with self.assertRaisesRegex(ValueError, "already exists"):
                freeze(root / "source", suite, [0])

    def case(self):
        return {"seed": 0, "scenario": self.scenario, "reference": copy.deepcopy(self.reference)}

    def test_model_budget_and_usage_are_recorded_per_episode(self):
        def model(command, request, timeout):
            self.assertNotIn("reference", request)
            self.assertNotIn("best_score", json.dumps(request))
            return {"tool": "try_plan" if request["trials_remaining"] else "submit",
                    "arguments": {"policy": "passenger_first"},
                    "usage": {"input_tokens": 10, "output_tokens": 4, "cost_usd": .001}}
        record = run_episode(self.case(), {"id": "test-only", "command": ["unused"]}, 1, 2, False, invoke=model)
        self.assertEqual(record["trials_used"], 2)
        self.assertEqual(record["usage"]["input_tokens"], 30)
        self.assertEqual(record["usage"]["output_tokens"], 12)
        self.assertAlmostEqual(record["usage"]["cost_usd"], .003)
        self.assertTrue(record["result"]["valid"])
        record["comparison"] = comparisons(record, self.reference, record["result"]["weighted_delay"])
        summary = summarize([record])["per_repeat"][0]
        self.assertIsNone(summary["unsafe_submission_rate"])
        self.assertEqual(summary["unsafe_metric_status"], "not_applicable_compiled_actions_only")

    def test_model_that_never_submits_is_not_silently_replaced_with_baseline(self):
        def model(command, request, timeout):
            return {"tool": "try_plan", "arguments": {}}
        record = run_episode(self.case(), {"id": "test-only", "command": ["unused"]}, 0, 2, False, invoke=model)
        self.assertEqual(record["error"]["type"], "no_submission")
        self.assertIsNone(record["result"])
        self.assertEqual(record["trials_used"], 2)
        self.assertIsNone(record["usage"]["input_tokens"])

    def test_comparison_distinguishes_improvement_from_beating_search(self):
        reference = {"passenger_first": {"weighted_delay": 100}}
        for cost, outcome in ((110, "worse_than_passenger_first"), (100, "ties_passenger_first"),
                (90, "beats_passenger_first_but_loses_to_scripted_search"),
                (80, "beats_passenger_first_ties_scripted_search"), (70, "beats_passenger_first_and_scripted_search")):
            row = {"result": {"valid": True, "weighted_delay": cost}, "error": None, "allow_timetable": False}
            self.assertEqual(comparisons(row, reference, 80)["outcome"], outcome)

    def test_missing_or_invalid_usage_stays_unknown(self):
        self.assertIsNone(usage_totals([{}])["cost_usd"])
        self.assertIsNone(usage_totals([{"usage": None}])["input_tokens"])
        self.assertIsNone(usage_totals([{"usage": {"input_tokens": True, "output_tokens": -1}}])["input_tokens"])

    def test_environment_errors_do_not_count_as_model_losses(self):
        def model(command, request, timeout):
            return {"tool": "submit", "arguments": {"policy": "passenger_first"}}
        with patch("railway_env.episode.normalized_score", side_effect=RuntimeError("broken scoring")):
            record = run_episode(self.case(), {"id": "test-only", "command": ["unused"]}, 0, 2, False, invoke=model)
        self.assertEqual(record["error"]["type"], "environment_error")
        self.assertTrue(record["result"]["valid"])
        record["comparison"] = comparisons(record, self.reference, 0)
        summary = summarize([record])
        self.assertEqual(summary["per_repeat"][0]["model_eligible_attempts"], 0)
        self.assertIsNone(summary["repeat_variation"][0]["mean_passenger_first_win_rate"])

    def test_harness_runs_three_independent_repeats_against_frozen_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite, _, _ = self.prepare_suite(root)
            worker = root / "worker.py"
            worker.write_text('import json,sys\nr=json.load(sys.stdin)\nprint(json.dumps({"tool":"submit","arguments":{"policy":"passenger_first"},"usage":{"input_tokens":1,"output_tokens":1}}))\n')
            import sys
            with patch("railway_env.solver.solve", side_effect=AssertionError("must not solve")):
                result = run(suite, [{"id": "test-fixture-not-a-model", "command": [sys.executable, str(worker)]}],
                             root / "results", repeats=3, trial_budget=2, seeds=[0])
            self.assertEqual(len(result["cases"]), 3)
            self.assertEqual({r["repeat"] for r in result["cases"]}, {0, 1, 2})
            self.assertTrue(all(r["result"]["valid"] for r in result["cases"]))
            self.assertTrue((root / "results" / "results.json").is_file())
            self.assertEqual(result["configuration"]["case_seeds"], [0])

    def test_pilot_cannot_regenerate_or_include_a_seed_outside_frozen_suite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite, _, _ = self.prepare_suite(root)
            with patch("railway_env.model_harness.search", side_effect=AssertionError("must not start search")):
                with self.assertRaisesRegex(ValueError, "pilot seeds"):
                    run(suite, [{"id": "test-only", "command": ["unused"]}], root / "results", seeds=[999])

    def test_adapter_crash_is_saved_and_stops_before_more_repeats(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite, _, _ = self.prepare_suite(root)
            with self.assertRaisesRegex(RuntimeError, "adapter/context failure"):
                run(suite, [{"id": "crash-fixture", "command": [sys.executable, "-c", "raise SystemExit(1)"]}],
                    root / "results", repeats=3, trial_budget=2, seeds=[0])
            result = json.loads((root / "results" / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(len(result["cases"]), 1)
            self.assertEqual(result["cases"][0]["error"]["type"], "adapter_error")
            self.assertEqual(result["summary"]["per_repeat"][0]["model_eligible_attempts"], 0)

    def test_worker_starts_in_empty_directory_without_repo_or_answer_keys(self):
        import sys
        from railway_env.model_harness import PROJECT_ROOT, worker_call
        probe = ("import json, os, sys; sys.stdin.read(); "
                 "print(json.dumps({'cwd': os.getcwd(), 'entries': os.listdir('.'), "
                 "'benchmarks': os.path.exists('benchmarks'), 'pythonpath': os.environ.get('PYTHONPATH', '')}))")
        seen = worker_call([sys.executable, "-c", probe], {}, 30)
        self.assertEqual(seen["entries"], [])
        self.assertFalse(seen["benchmarks"])
        self.assertNotEqual(Path(seen["cwd"]).resolve(), PROJECT_ROOT)
        self.assertIn(str(PROJECT_ROOT), seen["pythonpath"].split(os.pathsep))  # package stays importable
        self.assertFalse(Path(seen["cwd"]).exists())  # scratch directory is removed afterwards
