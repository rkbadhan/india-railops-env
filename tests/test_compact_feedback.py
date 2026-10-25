"""Model input stays small while complete tool results remain auditable."""

import copy
import json
import sys
import unittest

from test_dispatch import problem
from railway_env.benchmark import benchmark
from railway_env.references import make_reference
from railway_env.episode import DispatchEpisode
from railway_env.feedback import compact_feedback, bounded_request
from railway_env.model_harness import run_episode, comparisons, summarize


class CompactFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = problem()
        cls.reference = make_reference(cls.scenario, benchmark(cls.scenario, 2))

    def case(self):
        return {"seed": 0, "scenario": self.scenario, "reference": copy.deepcopy(self.reference)}

    def test_feedback_keeps_score_delays_and_route_decisions_without_oracle_or_bulk(self):
        result = DispatchEpisode(self.scenario, reference=self.reference).try_plan({"policy": "passenger_first"})
        result.update(reference={"best_score": 123}, unknown_large_field="x" * 10000)
        original = copy.deepcopy(result)
        feedback = compact_feedback(result)
        self.assertEqual(feedback["weighted_delay"], result["weighted_delay"])
        self.assertEqual(feedback["train_delays"], result["train_delays"])
        self.assertEqual(len(feedback["decisions"]), len(self.scenario.trains))
        self.assertIn("A 0/0 main", feedback["decisions"][0])
        for key in ("plan", "events", "reference", "unknown_large_field"):
            self.assertNotIn(key, feedback)
        self.assertEqual(result, original)
        self.assertLess(len(json.dumps(feedback)), len(json.dumps(result)) / 4)

    def test_invalid_feedback_preserves_validation_and_internal_error(self):
        result = {"valid": False, "errors": ["collision"], "error_type": "internal_error", "weighted_delay": None}
        self.assertEqual(compact_feedback(result), result)

    def test_bounded_history_preserves_board_best_and_latest_complete_exchange(self):
        base = [{"role": "system", "content": "rules"}, {"role": "user", "content": "board"}]
        messages = base[:]
        for i in range(32):
            messages.extend([{"role": "assistant", "content": f"action {i}"},
                             {"role": "user", "content": f"feedback {i}" + "x" * 200}])
        original = copy.deepcopy(messages)
        request, context = bounded_request({}, messages, [], {"weighted_delay": 7, "arguments": {}}, 1500)
        self.assertEqual(request["messages"][:2], base)
        self.assertIn('"weighted_delay":7', request["messages"][2]["content"])
        self.assertEqual(request["messages"][-2:], messages[-2:])
        self.assertGreater(context["omitted_exchanges"], 0)
        self.assertLessEqual(context["request_chars"], 1500)
        self.assertEqual(messages, original)

    def test_32_trial_model_run_is_bounded_and_full_audit_is_kept(self):
        calls = []
        def worker(command, request, timeout):
            calls.append(json.loads(json.dumps(request)))
            self.assertLessEqual(len(json.dumps(request, ensure_ascii=False)), 7500)
            self.assertNotIn("provider-noise", json.dumps(request))
            for message in request["messages"]:
                if message["role"] == "user" and message["content"].startswith("{"):
                    feedback = json.loads(message["content"])
                    self.assertNotIn("plan", feedback)
                    self.assertNotIn("events", feedback)
                    self.assertNotIn("reference", feedback)
            return {"tool": "try_plan" if request["trials_remaining"] else "submit",
                    "arguments": {"policy": "passenger_first"}, "provider_debug": "provider-noise" * 1000,
                    "usage": {"input_tokens": 10, "output_tokens": 3}}
        record = run_episode(self.case(), {"id": "test-only", "command": ["unused"]}, 0, 32, False,
                             invoke=worker, context_chars=7500)
        self.assertIsNone(record["error"])
        self.assertEqual(record["trials_used"], 32)
        self.assertEqual(len(calls), 33)
        self.assertEqual(len(record["trial_ledger"]), 32)
        ledger = next(m["content"] for m in calls[-1]["messages"] if m["content"].startswith("All attempted trials"))
        self.assertEqual(ledger.splitlines()[1:], record["trial_ledger"])
        self.assertGreater(record["model_context"]["max_omitted_exchanges"], 0)
        self.assertEqual(record["usage"]["input_tokens"], 330)
        for row in record["transcript"]:
            self.assertIn("plan", row["tool_result"])
            self.assertIn("events", row["tool_result"])
            self.assertIn("provider-noise", row["response"]["provider_debug"])
            self.assertNotIn("plan", row["model_feedback"])

    def test_too_small_context_is_configuration_failure_without_blame_or_worker_cost(self):
        def worker(*args):
            self.fail("worker must not be called for a context that cannot fit the board")
        record = run_episode(self.case(), {"id": "test-only", "command": ["unused"]}, 0, 2, False,
                             invoke=worker, context_chars=1)
        self.assertEqual(record["error"]["type"], "configuration_error")
        self.assertEqual(record["usage"]["model_calls"], 0)
        record["comparison"] = comparisons(record, self.reference, 0)
        summary = summarize([record])["per_repeat"][0]
        self.assertEqual(summary["configuration_errors"], 1)
        self.assertEqual(summary["model_errors"], 0)
        self.assertEqual(summary["model_eligible_attempts"], 0)

    def test_latest_feedback_is_never_silently_dropped(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "board"},
                    {"role": "assistant", "content": "action"}, {"role": "user", "content": "x" * 5000}]
        with self.assertRaisesRegex(ValueError, "latest feedback"):
            bounded_request({}, messages, [], None, 1000)

    def test_adapter_provider_failure_is_not_a_dispatch_loss(self):
        def worker(*args):
            return {"error": {"type": "provider_error", "message": "service unavailable"},
                    "usage": {"input_tokens": 10, "output_tokens": 0}}
        record = run_episode(self.case(), {"id": "test-only", "command": ["unused"]}, 0, 32, False, invoke=worker)
        self.assertEqual(record["error"]["type"], "provider_error")
        self.assertEqual(record["usage"]["input_tokens"], 10)
        self.assertEqual(record["trials_used"], 0)
        record["comparison"] = comparisons(record, self.reference, 0)
        summary = summarize([record])["per_repeat"][0]
        self.assertEqual(summary["provider_errors"], 1)
        self.assertEqual(summary["model_eligible_attempts"], 0)

    def test_worker_crash_bad_json_and_timeout_are_adapter_errors(self):
        for code in ("import sys; sys.stderr.write('worker crashed'); sys.exit(1)",
                     "print('not JSON')", "import time; time.sleep(10)"):
            with self.subTest(code=code):
                record = run_episode(self.case(), {"id": "test-only", "command": [sys.executable, "-c", code]},
                                     0, 2, False, timeout=0.5)
                self.assertEqual(record["error"]["type"], "adapter_error")
                record["comparison"] = comparisons(record, self.reference, 0)
                summary = summarize([record])["per_repeat"][0]
                self.assertEqual(summary["adapter_errors"], 1)
                self.assertEqual(summary["model_errors"], 0)
                self.assertEqual(summary["model_eligible_attempts"], 0)
                self.assertIsNone(summary["passenger_first_win_rate"])
                self.assertIsNone(summary["scripted_search_win_rate"])

    def test_legacy_worker_errors_are_excluded_but_model_errors_still_count(self):
        for kind in ("worker_error", "model_error"):
            def worker(*args):
                return {"error": {"type": kind, "message": "fixture"}}
            record = run_episode(self.case(), {"id": "test-only", "command": ["unused"]}, 0, 2, False, invoke=worker)
            # Re-summarize the original spelling without rewriting old records.
            record["error"]["type"] = kind
            record["comparison"] = comparisons(record, self.reference, 0)
            summary = summarize([record])["per_repeat"][0]
            self.assertEqual(summary["adapter_errors"], int(kind == "worker_error"))
            self.assertEqual(summary["model_errors"], int(kind == "model_error"))
            self.assertEqual(summary["model_eligible_attempts"], int(kind == "model_error"))
