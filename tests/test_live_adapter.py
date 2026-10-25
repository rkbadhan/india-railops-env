"""Native tool parsing, usage, provider failures and shared context declarations."""

import copy
import io
import json
import unittest
import urllib.error
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from railway_env.context_policy import context_policy
from railway_env.feedback import bounded_request, trial_ledger_line
from railway_env.openai_worker import completion
from railway_env.episode import tools_for


class LedgerAndWindowTests(unittest.TestCase):
    def test_every_proposal_and_score_survives_exchange_trimming(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "board"}]
        ledger = []
        for i in range(32):
            arguments = {"priority_order": [f"F{i}", "P1"], "holds": [{"train_id": "F1", "station": "A", "until": i}]}
            ledger.append(trial_ledger_line(i + 1, arguments, {"valid": True, "weighted_delay": 100 - i}))
            messages.extend([{"role": "assistant", "content": "action"}, {"role": "user", "content": "x" * 1000}])
        request, sizes = bounded_request({}, messages, [], {"weighted_delay": 69}, 6000, ledger)
        self.assertGreater(sizes["omitted_exchanges"], 0)
        self.assertEqual(sizes["ledger_trials"], 32)
        self.assertLessEqual(sizes["request_chars"], 6000)
        memory = next(m["content"] for m in request["messages"] if m["content"].startswith("All attempted trials"))
        self.assertEqual(memory.splitlines()[1:], ledger)
        self.assertIn('"until":0', memory)
        self.assertIn('32 score=69', memory)
        self.assertEqual(request["messages"][-2:], messages[-2:])

    def test_invalid_proposals_are_logged_on_one_line_without_invented_scores(self):
        line = trial_ledger_line(1, {"unknown": "a\nb"}, {"valid": False, "weighted_delay": None})
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn("1 invalid ", line)
        self.assertNotIn("score=", line)
        self.assertIn('a\\nb', line)

    def test_ledger_cannot_be_dropped_to_make_the_request_fit(self):
        with self.assertRaisesRegex(ValueError, "full trial ledger"):
            bounded_request({}, [{"role": "system", "content": "rules"}, {"role": "user", "content": "board"}],
                            [], None, 1000, ["x" * 2000])

    def test_common_cap_uses_smallest_available_window_and_reserves_output(self):
        models = [{"id": "large", "context_window_tokens": 131072, "max_output_tokens": 4096},
                  {"id": "small", "context_window_tokens": 32768, "max_output_tokens": 4096}]
        policy = context_policy(models)
        self.assertEqual(policy["context_chars"], 69120)
        self.assertEqual(policy["limiting_model"], "small")
        self.assertFalse(policy["token_count_verified"])
        self.assertEqual(context_policy(models, 60000)["context_chars"], 60000)
        with self.assertRaises(ValueError):
            context_policy(models, 80000)

    def test_cannot_silently_ignore_a_model_with_undeclared_window(self):
        with self.assertRaises(ValueError):
            context_policy([{"id": "small", "context_window_tokens": 32768}, {"id": "unknown"}])
        self.assertEqual(context_policy([{"id": "test-only"}])["mode"], "character_only_unverified")


class CompatibleWorkerTests(unittest.TestCase):
    def request(self):
        return {"model_id": "accounts/test/models/example", "trials_remaining": 32,
                "max_output_tokens": 4096, "context_window_tokens": 32768,
                "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "board"}],
                "tools": tools_for(False)}

    def response(self):
        return {"model": "actual-provider-version", "choices": [{"finish_reason": "tool_calls", "message": {
            "tool_calls": [{"function": {"name": "try_plan", "arguments": '{"policy":"passenger_first"}'}}]}}],
            "usage": {"prompt_tokens": 123, "completion_tokens": 45, "prompt_tokens_details": {"cached_tokens": 12}}}

    def call(self, response=None, request=None, **kwargs):
        self.sent = []
        def opener(http, timeout):
            self.sent.append(http)
            return io.BytesIO(json.dumps(response if response is not None else self.response()).encode())
        return completion(request or self.request(), "https://api.fireworks.ai/inference/v1", "test-key-not-real", opener=opener, **kwargs)

    def test_fireworks_request_and_native_call_usage_roundtrip(self):
        result = self.call()
        http = self.sent[0]
        self.assertEqual(http.full_url, "https://api.fireworks.ai/inference/v1/chat/completions")
        self.assertEqual(http.get_header("Authorization"), "Bearer test-key-not-real")
        body = json.loads(http.data)
        self.assertEqual(body["context_length_exceeded_behavior"], "error")
        self.assertEqual(body["tool_choice"], "required")
        self.assertFalse(body["parallel_tool_calls"])
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(body["max_completion_tokens"], 4096)
        self.assertNotIn("reference", body)
        self.assertTrue(all(t["function"]["name"] in ("try_plan", "submit") for t in body["tools"]))
        self.assertEqual(result["arguments"], {"policy": "passenger_first"})
        self.assertEqual(result["usage"], {"input_tokens": 123, "output_tokens": 45, "cost_usd": None})
        self.assertEqual(result["provider_model"], "actual-provider-version")
        self.assertEqual(result["raw_provider_response"], self.response())

    def test_reasoning_setting_is_explicit_and_invalid_values_do_not_reach_provider(self):
        result = self.call(reasoning_effort="low")
        self.assertEqual(result["tool"], "try_plan")
        self.assertEqual(json.loads(self.sent[0].data)["reasoning_effort"], "low")
        result = self.call(reasoning_effort="unsupported")
        self.assertEqual(result["error"]["type"], "configuration_error")
        self.assertEqual(self.sent, [])

    def test_budget_exhaustion_offers_only_submit(self):
        request = self.request()
        request["trials_remaining"] = 0
        request["tools"] = [t for t in request["tools"] if t["name"] == "submit"]
        result = self.call(request=request)
        self.assertEqual(result["error"]["type"], "model_error")
        self.assertEqual(result["usage"]["input_tokens"], 123)

    def test_no_tools_or_multiple_calls_are_model_failures_with_usage(self):
        for count in (0, 2):
            response = self.response()
            response["choices"][0]["message"]["tool_calls"] *= count
            result = self.call(response)
            self.assertEqual(result["error"]["type"], "model_error")
            self.assertEqual(result["usage"]["output_tokens"], 45)

    def test_invalid_tool_json_keeps_usage_and_executes_nothing(self):
        for arguments in ("{", "[]"):
            response = self.response()
            response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = arguments
            result = self.call(response)
            self.assertEqual(result["error"]["type"], "model_error")
            self.assertNotIn("tool", result)

    def test_output_exhaustion_is_explicit_with_usage_and_no_executed_action(self):
        response = self.response()
        response["choices"][0]["finish_reason"] = "length"
        response["choices"][0]["message"] = {"reasoning_content": "still reasoning →"}
        result = self.call(response)
        self.assertEqual(result["error"]["type"], "model_error")
        self.assertEqual(result["failure_reason"], "output_limit")
        self.assertEqual(result["provider_finish_reason"], "length")
        self.assertEqual(result["usage"]["output_tokens"], 45)
        self.assertNotIn("tool", result)

    def test_http_errors_are_classified_without_echoing_credentials(self):
        for status, kind in ((400, "configuration_error"), (401, "configuration_error"), (429, "provider_error"), (503, "provider_error")):
            def opener(http, timeout):
                raise urllib.error.HTTPError(http.full_url, status, "failed", {}, io.BytesIO(b"secret-key-not-for-logs"))
            result = completion(self.request(), "https://api.fireworks.ai/inference/v1", "test-key-not-real", opener=opener)
            self.assertEqual(result["error"]["type"], kind)
            self.assertNotIn("secret-key", json.dumps(result))

    def test_token_usage_can_detect_a_bad_declared_window(self):
        request = self.request()
        request["context_window_tokens"] = 4200
        result = self.call(request=request)
        self.assertEqual(result["error"]["type"], "configuration_error")
        self.assertEqual(result["usage"]["input_tokens"], 123)

    def test_malformed_provider_completion_is_not_a_model_failure(self):
        response = self.response()
        response["choices"] = [None]
        result = self.call(response)
        self.assertEqual(result["error"]["type"], "provider_error")
        self.assertEqual(result["usage"]["input_tokens"], 123)

    def test_missing_auth_and_zero_output_reject_before_network(self):
        def opener(*args, **kwargs):
            self.fail("invalid configuration must not reach the network")
        self.assertEqual(completion(self.request(), "https://api.fireworks.ai/inference/v1", opener=opener)["error"]["type"], "configuration_error")
        request = self.request()
        request["max_output_tokens"] = 0
        self.assertEqual(completion(request, "https://api.fireworks.ai/inference/v1", "test-key", opener=opener)["error"]["type"], "configuration_error")

    def test_real_worker_utf8_roundtrip_under_cp1252_pipes(self):
        received = []
        reply = self.response()
        reasoning = "München → दिल्ली: test the next train order."
        reply["choices"][0]["message"]["reasoning_content"] = reasoning
        request = self.request()
        request["messages"][-1]["content"] = "Track report: Bamur → दिल्ली, café"
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                data = json.dumps(reply, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            process = subprocess.run([sys.executable, "-B", "-m", "railway_env.openai_worker", "--base-url",
                f"http://127.0.0.1:{server.server_port}/v1"],
                input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
                capture_output=True, timeout=10,
                env={**os.environ, "OPENAI_API_KEY": "", "FIREWORKS_API_KEY": "",
                     "PYTHONIOENCODING": "cp1252:strict", "PYTHONUTF8": "0"})
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout.decode("utf-8"))
            self.assertEqual(result["tool"], "try_plan")
            self.assertEqual(result["usage"]["input_tokens"], 123)
            self.assertEqual(received[0][0], "/v1/chat/completions")
            self.assertEqual(received[0][1]["model"], self.request()["model_id"])
            self.assertEqual(received[0][1]["messages"][-2]["content"], request["messages"][-1]["content"])
            self.assertEqual(result["raw_provider_response"]["choices"][0]["message"]["reasoning_content"], reasoning)
            self.assertIn("→".encode("utf-8"), process.stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
