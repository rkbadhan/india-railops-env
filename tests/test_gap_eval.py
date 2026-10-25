"""Gap-evaluation reporting, provider retries, resumable runs and the default-plan observation."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_dispatch import problem
from railway_env import gap_metrics
from railway_env.benchmark import benchmark
from railway_env.episode import ENV_VERSION, DispatchEpisode
from railway_env.model_harness import PROMPT_VERSION, SYSTEM, largest_output_cap, run, run_episode
from railway_env.models import save_json
from railway_env.references import make_reference
from railway_env.suite import file_hash, freeze


def record(seed, answered=True, reward=1.0, score=100, error=None, excess=0, repeat=0, model="m", budget=1,
           scripted=100, usage=None):
    result = {"valid": True, "weighted_delay": score, "normalized_reward": reward,
              "excess_delay_over_optimum": excess} if answered else None
    return {"seed": seed, "repeat": repeat, "model": model, "trial_budget": budget, "result": result,
            "error": error, "comparison": {"scripted_search": scripted},
            "usage": usage or {"input_tokens": 1000, "output_tokens": 500, "cost_usd": None, "estimated_cost_usd": .01}}


class GapMetricTests(unittest.TestCase):
    def rows(self):
        return [record(1, reward=1.0, score=100), record(2, reward=.5, score=120, excess=20),
                record(3, answered=False, error={"type": "model_error", "message": "out of tokens"}),
                record(4, answered=False, error={"type": "provider_error", "message": "HTTP 412"})]

    def test_no_answer_counts_as_minus_one_and_provider_errors_are_excluded(self):
        entry = gap_metrics.headline(self.rows(), resamples=200)[0]
        self.assertEqual(entry["eligible_episodes"], 3)
        self.assertEqual(entry["infrastructure_failures"], 1)
        self.assertAlmostEqual(entry["no_answer_rate"], 1 / 3)
        self.assertAlmostEqual(entry["mean_reward_no_answer_is_minus_one"], (1 + .5 - 1) / 3)
        self.assertAlmostEqual(entry["mean_reward_submitted_only"], .75)
        self.assertAlmostEqual(entry["at_optimum_rate"], 1 / 3)

    def test_invalid_submission_is_a_no_answer(self):
        bad = record(1)
        bad["result"] = {"valid": False, "weighted_delay": None, "normalized_reward": -1.0}
        entry = gap_metrics.headline([bad], resamples=50)[0]
        self.assertEqual(entry["no_answer_rate"], 1.0)
        self.assertEqual(entry["mean_reward_no_answer_is_minus_one"], -1.0)

    def test_paired_win_tie_loss_against_scripted_search(self):
        rows = [record(1, score=90, scripted=100), record(2, score=100, scripted=100), record(3, score=110, scripted=100),
                record(4, answered=False, error={"type": "model_error", "message": "x"}, scripted=100)]
        wins = gap_metrics.headline(rows, resamples=50)[0]["versus_scripted_search"]
        self.assertEqual((wins["win"], wins["tie"], wins["loss"]), (1, 1, 2))
        self.assertAlmostEqual(wins["loss_rate"], .5)

    def test_bootstrap_is_deterministic_brackets_the_mean_and_averages_repeats_per_seed(self):
        rows = [record(s, reward=r, repeat=k) for s, r in enumerate((1.0, .0, .5, -.9, .8)) for k in range(3)]
        first = gap_metrics.headline(rows, resamples=2000)[0]["mean_reward_ci"]
        second = gap_metrics.headline(rows, resamples=2000)[0]["mean_reward_ci"]
        self.assertEqual(first, second)
        self.assertEqual(first["seeds"], 5)  # repeats do not inflate the number of independent units
        self.assertLessEqual(first["low"], first["mean"])
        self.assertLessEqual(first["mean"], first["high"])
        self.assertAlmostEqual(first["mean"], (1.0 + 0 + .5 - .9 + .8) / 5)

    def test_tokens_and_cost_per_episode(self):
        entry = gap_metrics.headline(self.rows(), resamples=50)[0]
        self.assertEqual(entry["mean_input_tokens"], 1000)
        self.assertAlmostEqual(entry["estimated_cost_usd_total"], .04)
        self.assertIsNone(entry["reported_cost_usd_total"])
        self.assertAlmostEqual(gap_metrics.estimated_cost({"input_tokens": 2_000_000, "output_tokens": 1_000_000},
                                                          {"input": 1.0, "output": 3.0}), 5.0)
        self.assertIsNone(gap_metrics.estimated_cost({"input_tokens": None, "output_tokens": 5}, {"input": 1, "output": 1}))
        self.assertIsNone(gap_metrics.estimated_cost({"input_tokens": 1, "output_tokens": 1}, None))


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = problem()
        cls.benchmark = benchmark(cls.scenario, 2)
        cls.reference = make_reference(cls.scenario, cls.benchmark)

    def prepare_suite(self, root):
        source = root / "source" / "seed_0"
        save_json(source / "scenario.json", self.scenario.to_dict())
        save_json(source / "benchmark.json", self.benchmark)
        data = root / "input.csv"
        data.write_text("original")
        return freeze(root / "source", root / "frozen", [0], {"input.csv": file_hash(data)}) and root / "frozen"

    def worker(self, root, body):
        path = root / "worker.py"
        path.write_text("import json, sys\nr = json.load(sys.stdin)\n" + body)
        return {"id": "fixture", "command": [sys.executable, str(path)]}

    def case(self):
        return {"seed": 0, "scenario": self.scenario, "reference": self.reference}

    # -- observation and version ---------------------------------------------------------

    def test_observation_states_default_plan_score_without_the_optimum_or_a_solve(self):
        pf = self.reference["passenger_first"]["weighted_delay"]
        with patch("railway_env.solver.solve", side_effect=AssertionError("must not solve")):
            text = DispatchEpisode(self.scenario).observe()
            with_reference = DispatchEpisode(self.scenario, reference=self.reference).observe()
        self.assertIn(f"Default plan (passenger first) scores {pf}.", text)
        self.assertEqual(text, with_reference)  # the stored answer key changes nothing the agent sees
        self.assertNotIn("optimum", text.lower())

    def test_versions_and_brevity_instruction_are_recorded_in_every_result_file(self):
        self.assertIn("Reason briefly", SYSTEM)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = self.prepare_suite(root)
            model = self.worker(root, 'print(json.dumps({"tool": "submit", "arguments": {"policy": "passenger_first"}}))\n')
            result = run(suite, [model], root / "out", repeats=1, trial_budget=1, resamples=50)
            self.assertEqual(result["configuration"]["env_version"], ENV_VERSION)
            episode = json.loads((root / "out" / "model_0" / "seed_0_repeat_0.json").read_text(encoding="utf-8"))
            self.assertEqual((episode["env_version"], episode["prompt_version"]), (ENV_VERSION, PROMPT_VERSION))
            saved = json.loads((root / "out" / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["configuration"]["prompt_version"], PROMPT_VERSION)
            self.assertIn("default plan", episode["observation"].lower())
            self.assertEqual(saved["headline"][0]["no_answer_rate"], 0.0)
            self.assertIn("scripted_headline", saved)

    def test_largest_output_cap_leaves_the_shared_input_allowance(self):
        model = {"id": "m", "context_window_tokens": 32768}
        self.assertEqual(largest_output_cap(model, 24000, 2.5, 1024), 32768 - 1024 - 9600)
        with self.assertRaisesRegex(ValueError, "context_window_tokens"):
            largest_output_cap({"id": "m"}, 24000, 2.5, 1024)

    def test_max_output_sets_every_models_cap_in_the_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = self.prepare_suite(root)
            model = self.worker(root, 'print(json.dumps({"tool": "submit", "arguments": {"policy": "passenger_first"}, '
                                      '"usage": {"input_tokens": r["max_output_tokens"], "output_tokens": 1}}))\n')
            model["context_window_tokens"] = 32768
            result = run(suite, [model], root / "out", repeats=1, trial_budget=1, max_output=True, resamples=50)
            self.assertEqual(result["cases"][0]["usage"]["input_tokens"], 32768 - 1024 - 9600)

    # -- provider retries ------------------------------------------------------------------

    def test_provider_error_is_retried_with_backoff_then_succeeds(self):
        calls, naps = [], []

        def invoke(command, request, timeout):
            calls.append(1)
            if len(calls) <= 2:
                return {"error": {"type": "provider_error", "message": "HTTP 503"}}
            return {"tool": "submit", "arguments": {"policy": "passenger_first"}}
        episode = run_episode(self.case(), {"id": "m", "command": ["x"]}, 0, 1, False, invoke=invoke, sleep=naps.append)
        self.assertIsNone(episode["error"])
        self.assertEqual(episode["provider_retries"], 2)
        self.assertEqual(naps, [2.0, 4.0])
        self.assertTrue(episode["result"]["valid"])

    def test_provider_error_gives_up_after_three_retries_and_is_marked(self):
        calls, naps = [], []

        def invoke(command, request, timeout):
            calls.append(1)
            return {"error": {"type": "provider_error", "message": "HTTP 412"}}
        episode = run_episode(self.case(), {"id": "m", "command": ["x"]}, 0, 1, False, invoke=invoke, sleep=naps.append)
        self.assertEqual(len(calls), 4)  # the first attempt plus three retries
        self.assertEqual(naps, [2.0, 4.0, 8.0])
        self.assertEqual(episode["error"]["type"], "provider_error")
        self.assertEqual(episode["provider_retries"], 3)

    def test_other_errors_are_not_retried(self):
        calls = []

        def invoke(command, request, timeout):
            calls.append(1)
            return {"error": {"type": "model_error", "message": "output limit"}}
        episode = run_episode(self.case(), {"id": "m", "command": ["x"]}, 0, 1, False, invoke=invoke, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(episode["error"]["type"], "model_error")

    def test_run_continues_after_a_provider_failure_and_excludes_it_from_rates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = self.prepare_suite(root)
            model = self.worker(root, 'print(json.dumps({"error": {"type": "provider_error", "message": "HTTP 412"}}))\n')
            result = run(suite, [model], root / "out", repeats=3, trial_budget=1, retries=1, sleep=lambda s: None, resamples=50)
            self.assertEqual(len(result["cases"]), 3)  # marked and continued, not raised
            entry = result["headline"][0]
            self.assertEqual((entry["eligible_episodes"], entry["infrastructure_failures"]), (0, 3))
            self.assertIsNone(entry["no_answer_rate"])

    def test_repeated_provider_failures_stop_the_run_with_results_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = self.prepare_suite(root)
            model = self.worker(root, 'print(json.dumps({"error": {"type": "provider_error", "message": "HTTP 412"}}))\n')
            with self.assertRaisesRegex(RuntimeError, "repeated provider failures"):
                run(suite, [model], root / "out", repeats=8, trial_budget=1, retries=0, sleep=lambda s: None, resamples=50)
            saved = json.loads((root / "out" / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(len(saved["cases"]), 5)

    # -- resume ----------------------------------------------------------------------------

    def test_resume_skips_finished_episodes_and_reruns_provider_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = self.prepare_suite(root)
            log, marker = root / "calls.log", root / "recovered"
            body = ('open(%r, "a").write("x")\n'
                    'if not __import__("os").path.exists(%r):\n'
                    '    print(json.dumps({"error": {"type": "provider_error", "message": "HTTP 503"}}))\n'
                    'else:\n'
                    '    print(json.dumps({"tool": "submit", "arguments": {"policy": "passenger_first"}}))\n') % (str(log), str(marker))
            model = self.worker(root, body)
            first = run(suite, [model], root / "out", repeats=2, trial_budget=1, retries=0, sleep=lambda s: None, resamples=50)
            self.assertTrue(all(r["error"]["type"] == "provider_error" for r in first["cases"]))
            marker.write_text("ok")
            log.write_text("")
            second = run(suite, [model], root / "out", repeats=2, trial_budget=1, resume=True, retries=0,
                         sleep=lambda s: None, resamples=50)
            self.assertEqual(log.read_text(), "xx")  # both failed episodes were rerun
            self.assertTrue(all(r["result"]["valid"] for r in second["cases"]))
            log.write_text("")
            third = run(suite, [model], root / "out", repeats=2, trial_budget=1, resume=True, retries=0,
                        sleep=lambda s: None, resamples=50)
            self.assertEqual(log.read_text(), "")  # nothing left to run: no worker was started
            self.assertEqual(len(third["cases"]), 2)

    def test_resume_can_add_repeats_but_refuses_a_different_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = self.prepare_suite(root)
            model = self.worker(root, 'print(json.dumps({"tool": "submit", "arguments": {"policy": "passenger_first"}}))\n')
            run(suite, [model], root / "out", repeats=1, trial_budget=1, resamples=50)
            grown = run(suite, [model], root / "out", repeats=2, trial_budget=1, resume=True, resamples=50)
            self.assertEqual(len(grown["cases"]), 2)
            with self.assertRaisesRegex(ValueError, "fresh output directory"):
                run(suite, [model], root / "out", repeats=1, trial_budget=1, resamples=50)
            with self.assertRaisesRegex(ValueError, "trial_budget"):
                run(suite, [model], root / "out", repeats=1, trial_budget=2, resume=True, resamples=50)

    def test_price_change_does_not_block_resume_and_bad_prices_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = self.prepare_suite(root)
            model = self.worker(root, 'print(json.dumps({"tool": "submit", "arguments": {"policy": "passenger_first"}, '
                                      '"usage": {"input_tokens": 1000000, "output_tokens": 1000000}}))\n')
            run(suite, [{**model, "price_per_million": {"input": 1, "output": 2}}], root / "out", repeats=1,
                trial_budget=1, resamples=50)
            again = run(suite, [{**model, "price_per_million": {"input": 5, "output": 5}}], root / "out", repeats=1,
                        trial_budget=1, resume=True, resamples=50)
            self.assertAlmostEqual(again["cases"][0]["usage"]["estimated_cost_usd"], 3.0)  # recorded at run time
            with self.assertRaisesRegex(ValueError, "price_per_million"):
                run(suite, [{**model, "price_per_million": {"input": -1, "output": 1}}], root / "bad", repeats=1, trial_budget=1)


    def test_spending_cap_stops_a_run_resumably_and_needs_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = self.prepare_suite(root)
            model = self.worker(root, 'print(json.dumps({"tool": "submit", "arguments": {"policy": "passenger_first"}, '
                                      '"usage": {"input_tokens": 1000000, "output_tokens": 0}}))\n')
            priced = {**model, "price_per_million": {"input": 1.0, "output": 1.0}}  # $1 per episode
            with self.assertRaisesRegex(RuntimeError, "spending cap reached"):
                run(suite, [priced], root / "out", repeats=5, trial_budget=1, max_cost=2.5, resamples=50)
            saved = json.loads((root / "out" / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(len(saved["cases"]), 3)  # the episode that crossed the cap is kept, nothing after it
            resumed = run(suite, [priced], root / "out", repeats=5, trial_budget=1, resume=True, max_cost=100, resamples=50)
            self.assertEqual(len(resumed["cases"]), 5)
            with self.assertRaisesRegex(ValueError, "spending cap"):
                run(suite, [model], root / "other", repeats=1, trial_budget=1, max_cost=5)


    # -- policy is required ----------------------------------------------------------------

    def test_policy_is_required_and_a_missing_one_is_never_silently_defaulted(self):
        episode = DispatchEpisode(self.scenario, reference=self.reference, trial_budget=2)
        missing = episode.try_plan({"priority_order": []})
        self.assertFalse(missing["valid"])
        self.assertIn("policy is required", missing["errors"][0])
        self.assertIn("passenger_first", missing["errors"][0])
        self.assertEqual(episode.trials_used, 1)  # the failed call still spends a trial
        self.assertTrue(episode.try_plan({"policy": "controller_rule"})["valid"])
        final = episode.submit({"holds": []})
        self.assertFalse(final["valid"])
        self.assertEqual(final["normalized_reward"], -1.0)

    def test_tool_schema_requires_policy_and_explains_the_options(self):
        from railway_env.episode import tools_for
        for tool in tools_for(False):
            schema = tool["input_schema"]
            self.assertEqual(schema["required"], ["policy"])
            description = schema["properties"]["policy"]["description"]
            self.assertIn("passenger_first", description)
            self.assertIn("controller_rule", description)
            self.assertIn("default plan", description)
            self.assertIn("priority_order", tool["description"])
        for tool in tools_for(True):  # a full timetable needs no policy
            self.assertNotIn("required", tool["input_schema"])


    def test_suite_without_its_answer_files_fails_with_a_clear_message(self):
        from railway_env.suite import load_suite
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = self.prepare_suite(root)
            (suite / "seed_0" / "reference.json").unlink()
            with self.assertRaisesRegex(ValueError, "answers are withheld"):
                load_suite(suite)
            (suite / "seed_0" / "scenario.json").unlink()
            with self.assertRaisesRegex(ValueError, "scenario file missing"):
                load_suite(suite)


if __name__ == "__main__":
    unittest.main()
