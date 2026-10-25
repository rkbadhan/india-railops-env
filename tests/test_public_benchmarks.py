"""The frozen suites in a public checkout, with or without the held-out answer files."""

import hashlib
import json
import unittest
from pathlib import Path

from railway_env.suite import load_suite
from railway_env.models import load_scenario

ROOT = Path(__file__).resolve().parents[1]
HELDOUT = ROOT / "benchmarks" / "heldout_v1"
TUNING = ROOT / "benchmarks" / "tuning_v1"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def answers_present():
    manifest = json.loads((HELDOUT / "manifest.json").read_text(encoding="utf-8"))
    return all((HELDOUT / case["reference"]).is_file() for case in manifest["cases"])


class PublicBenchmarkTests(unittest.TestCase):
    def test_tuning_suite_loads_with_its_answers(self):
        manifest, cases = load_suite(TUNING)
        self.assertEqual(len(cases), 100)
        self.assertTrue(all(case["reference"]["oracle"]["proven_optimal"] for case in cases))

    def test_heldout_problems_match_their_published_hashes_without_needing_answers(self):
        manifest = json.loads((HELDOUT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["cases"]), 100)
        for case in manifest["cases"]:
            path = HELDOUT / case["scenario"]
            self.assertEqual(digest(path), case["scenario_file_sha256"], case["scenario"])
            self.assertEqual(load_scenario(path).seed, case["seed"])

    @unittest.skipUnless((HELDOUT / "ANSWER_HASHES.txt").is_file(), "answer hash list not published here")
    def test_answer_hash_list_matches_the_manifest(self):
        manifest = json.loads((HELDOUT / "manifest.json").read_text(encoding="utf-8"))
        listed = {}
        for line in (HELDOUT / "ANSWER_HASHES.txt").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                value, name = line.split(None, 1)
                listed[name.strip().lstrip("*")] = value
        expected = {case["reference"]: case["reference_file_sha256"] for case in manifest["cases"]}
        self.assertEqual(listed, expected)

    @unittest.skipIf(answers_present(), "held-out answers are present in this checkout")
    def test_loading_heldout_without_answers_fails_with_a_clear_message(self):
        with self.assertRaisesRegex(ValueError, "answers are withheld"):
            load_suite(HELDOUT)

    @unittest.skipUnless(answers_present(), "held-out answers are not published in this checkout")
    def test_heldout_answers_match_their_hashes_and_replay(self):
        manifest, cases = load_suite(HELDOUT)  # verifies hashes and replays every stored plan
        self.assertEqual(len(cases), 100)


if __name__ == "__main__":
    unittest.main()
