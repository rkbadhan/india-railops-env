"""Local credential loading preserves environment precedence and hides secrets."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from railway_env.env_settings import load_env


class EnvSettingsTests(unittest.TestCase):
    def test_literal_values_quotes_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('# comment\nOPENAI_BASE_URL="https://example.test/v1"\nOPENAI_API_KEY=local-placeholder\nexport OPENAI_MODEL=\'model-id\' # comment\nEMPTY=\nLITERAL=$(never_execute)\n', encoding="utf-8")
            environment = {"OPENAI_API_KEY": "existing-placeholder"}
            load_env(path, environment)
            self.assertEqual(environment["OPENAI_API_KEY"], "existing-placeholder")
            self.assertEqual(environment["OPENAI_BASE_URL"], "https://example.test/v1")
            self.assertEqual(environment["OPENAI_MODEL"], "model-id")
            self.assertEqual(environment["EMPTY"], "")
            self.assertEqual(environment["LITERAL"], "$(never_execute)")

    def test_invalid_file_reports_line_only_and_applies_no_partial_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('GOOD=value\nOPENAI_API_KEY="secret-placeholder\n', encoding="utf-8")
            environment = {}
            with self.assertRaisesRegex(ValueError, "line 2") as error:
                load_env(path, environment)
            self.assertNotIn("secret-placeholder", str(error.exception))
            self.assertEqual(environment, {})

    def test_missing_default_is_optional_but_explicit_file_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing"
            self.assertEqual(load_env(path, {}), [])
            with self.assertRaises(ValueError):
                load_env(path, {}, required=True)

    def test_worker_cli_loads_env_file_without_printing_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text('OPENAI_BASE_URL=https://api.fireworks.ai/inference/v1\nOPENAI_API_KEY=\nOPENAI_MODEL=accounts/fireworks/models/deepseek-v4p1-flash-1017\n', encoding="utf-8")
            environment = {**os.environ, "OPENAI_API_KEY": "", "FIREWORKS_API_KEY": ""}
            environment.pop("OPENAI_BASE_URL", None)
            environment.pop("OPENAI_MODEL", None)
            result = subprocess.run([sys.executable, "-B", "-m", "railway_env.openai_worker", "--env-file", str(path)],
                input=json.dumps({"messages": [], "tools": [], "trials_remaining": 1}), text=True,
                capture_output=True, timeout=10, env=environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            response = json.loads(result.stdout)
            self.assertEqual(response["error"]["type"], "configuration_error")
            self.assertIn("configure OPENAI_API_KEY", response["error"]["message"])

    def test_local_env_files_are_ignored_and_the_blank_template_is_visible(self):
        result = subprocess.run(["git", "check-ignore", "-z", "--stdin"],
            input=b".env\0.env.production\0.env.example\0", capture_output=True, check=False)
        self.assertEqual(set(result.stdout.decode().strip("\0").split("\0")), {".env", ".env.production"})
