"""Failure labels, default-plan outcomes and best-of-n summaries used by the gap report."""

import unittest

from railway_env import gap_report as g


def action(tool, **arguments):
    return {"response": {"tool": tool, "arguments": arguments}}


def episode(actions, answered=True, score=100, default=100, optimum=90, error=None, seed=1, repeat=0, ledger=()):
    result = {"valid": True, "weighted_delay": score, "normalized_reward": 0.5,
              "excess_delay_over_optimum": score - optimum} if answered else None
    return {"seed": seed, "repeat": repeat, "model": "m", "trial_budget": 4, "transcript": actions, "result": result,
            "error": None if answered else (error or {"type": "model_error", "message": "out of output"}),
            "comparison": {"passenger_first": default, "scripted_search": default},
            "reference": {"incumbent": optimum}, "trial_ledger": list(ledger),
            "usage": {"input_tokens": 1, "output_tokens": 1, "estimated_cost_usd": 0.001}}


class LabelTests(unittest.TestCase):
    def test_near_duplicates_need_same_order_same_hold_keys_and_times_within_five_minutes(self):
        base = {"policy": "passenger_first", "priority_order": ["A", "B"],
                "holds": [{"train_id": "A", "station": "X", "until": 50}]}
        near = {**base, "holds": [{"train_id": "A", "station": "X", "until": 55}]}
        far = {**base, "holds": [{"train_id": "A", "station": "X", "until": 56}]}
        self.assertTrue(g.near_duplicate(base, base))
        self.assertTrue(g.near_duplicate(base, near))
        self.assertFalse(g.near_duplicate(base, far))
        self.assertFalse(g.near_duplicate(base, {**base, "priority_order": ["B", "A"]}))
        self.assertFalse(g.near_duplicate(base, {**base, "holds": []}))
        self.assertFalse(g.near_duplicate(base, {**base, "policy": "controller_rule"}))
        # an omitted policy means controller_rule, as the environment treats it
        self.assertTrue(g.near_duplicate({"priority_order": ["A"]}, {"policy": "controller_rule", "priority_order": ["A"]}))

    def test_repeated_proposals_count_only_try_plan_repeats_not_the_final_submit(self):
        a = {"priority_order": ["A", "B"]}
        record = episode([action("try_plan", **a), action("try_plan", **{"priority_order": ["B", "A"]}),
                          action("try_plan", **a), action("submit", **a)])
        self.assertEqual(g.repeated_count(record), 1)
        self.assertIn("repeated_proposals", g.labels(record))
        self.assertNotIn("repeated_proposals", g.labels(episode([action("try_plan", **a), action("submit", **a)])))

    def test_default_plan_and_holds_labels(self):
        default = action("try_plan", policy="passenger_first", holds=[], priority_order=[])
        held = action("try_plan", policy="passenger_first", holds=[{"train_id": "A", "station": "X", "until": 5}])
        self.assertTrue(g.is_default_plan(default["response"]["arguments"]))
        self.assertFalse(g.is_default_plan(held["response"]["arguments"]))
        with_both = g.labels(episode([default, held, action("submit", policy="passenger_first")]))
        self.assertNotIn("never_compared_to_default", with_both)
        self.assertNotIn("never_used_holds", with_both)
        neither = g.labels(episode([action("try_plan", priority_order=["A"]), action("submit", priority_order=["A"])]))
        self.assertIn("never_compared_to_default", neither)
        self.assertIn("never_used_holds", neither)

    def test_broke_a_good_plan_only_when_headroom_is_zero_or_tiny(self):
        zero = episode([], score=120, default=100, optimum=100)
        tiny = episode([], score=120, default=104, optimum=100)  # 5% of 100 is 5
        roomy = episode([], score=120, default=200, optimum=100)
        same = episode([], score=100, default=100, optimum=100)
        self.assertIn("broke_a_good_plan", g.labels(zero))
        self.assertIn("broke_a_good_plan", g.labels(tiny))
        self.assertNotIn("broke_a_good_plan", g.labels(roomy))
        self.assertNotIn("broke_a_good_plan", g.labels(same))

    def test_no_answer_and_invalid_submission_and_infrastructure_exclusion(self):
        self.assertIn("no_answer", g.labels(episode([], answered=False)))
        invalid = episode([])
        invalid["result"] = {"valid": False, "weighted_delay": None}
        found = g.labels(invalid)
        self.assertTrue({"no_answer", "invalid_submission"} <= found)
        infra = episode([], answered=False, error={"type": "provider_error", "message": "429"})
        self.assertEqual(g.labels(infra), set())
        table = g.label_table([infra, episode([], answered=False)])
        self.assertEqual(table["eligible"], 1)
        self.assertEqual(table["counts"]["no_answer"], 1)


class AggregationTests(unittest.TestCase):
    def test_default_outcomes_split_by_headroom_and_count_no_answers(self):
        rows = [episode([], score=150, default=200, optimum=100),
                episode([], score=250, default=200, optimum=100),
                episode([], score=200, default=200, optimum=100),
                episode([], score=100, default=100, optimum=100),
                episode([], score=130, default=100, optimum=100),
                episode([], answered=False, default=200, optimum=100)]
        out = g.default_outcomes(rows)
        self.assertEqual({k: out["headroom"][k] for k in ("episodes", "better", "same", "worse", "no_answer")},
                         {"episodes": 4, "better": 1, "same": 1, "worse": 1, "no_answer": 1})
        self.assertEqual({k: out["low_headroom"][k] for k in ("episodes", "better", "same", "worse")},
                         {"episodes": 2, "better": 0, "same": 1, "worse": 1})

    def test_best_of_n_uses_the_best_sample_and_no_answer_is_minus_one(self):
        samples = []
        for repeat, (score, answered) in enumerate(((120, True), (95, True), (0, False), (100, True))):
            record = episode([], score=score, answered=answered, default=100, optimum=90, repeat=repeat)
            if answered:
                record["result"]["normalized_reward"] = {120: -0.9, 95: 0.5, 100: 0.0}[score]
            samples.append(record)
        row = g.best_of_n(samples)[1]
        self.assertEqual(row["best"], 0.5)
        self.assertAlmostEqual(row["mean"], (-0.9 + 0.5 - 1.0 + 0.0) / 4)
        self.assertTrue(row["any_better_than_default"])
        self.assertFalse(row["any_at_optimum"])
        self.assertEqual(row["samples"], 4)

    def test_examples_are_trimmed_and_quote_the_ledger(self):
        record = episode([action("try_plan", priority_order=["A"]), action("submit", priority_order=["A"])],
                         ledger=["1 score=100 " + "x" * 400])
        lines = g.example(record, "never_used_holds")
        self.assertTrue(lines[0].startswith("seed 1, repeat 0, 4 trial(s)"))
        self.assertTrue(all(len(line) <= 280 for line in lines))
        self.assertEqual(g.trim("a  b\nc", 10), "a b c")

    def test_paired_difference_is_model_minus_scripted_per_seed(self):
        episodes = [episode([], seed=s, repeat=r) for s in (1, 2, 3) for r in (0, 1)]
        for record in episodes:
            record["result"]["normalized_reward"] = 0.5
        run = {"episodes": episodes, "scripted": [{"seed": s, "normalized_reward": 0.8} for s in (1, 2, 3)]}
        difference = g.paired_difference(run, resamples=200)
        self.assertAlmostEqual(difference["mean"], -0.3)
        self.assertEqual(difference["seeds"], 3)


    def test_best_of_markdown_and_per_model_sections(self):
        summary = g.best_of_summary(g.best_of_n([episode([], seed=1, repeat=r, score=95 + r) for r in range(3)]))
        text = g.best_of_markdown({"best_of_8": summary})
        self.assertIn("Best of 8", text)
        self.assertIn("Seeds where some sample is optimal", text)
        self.assertIn("No best-of-8", g.best_of_markdown({}))
        sections = {"A": ("a-data", "a-rows"), "B": ("b-data", "b-rows")}
        rendered = g.per_model(sections, lambda data, rows: f"{data}/{rows}")
        self.assertEqual(rendered, "**A**\n\na-data/a-rows\n\n**B**\n\nb-data/b-rows")

    def test_load_model_reads_the_run_directories_it_finds(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("t1", "t4"):
                directory = Path(tmp) / f"m_{name}"
                (directory / "model_0").mkdir(parents=True)
                (directory / "results.json").write_text(json.dumps({"configuration": {"trial_budget": 1}, "scripted_search": []}))
            runs, best = g.load_model(str(Path(tmp) / "m"))
            self.assertEqual(sorted(runs), [1, 4])
            self.assertIsNone(best)


if __name__ == "__main__":
    unittest.main()
