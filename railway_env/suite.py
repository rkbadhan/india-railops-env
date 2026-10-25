"""Freeze existing problems/answers and verify them without generating or solving."""

import hashlib
import json
from pathlib import Path

from .models import load_scenario
from .references import canonical_bytes, make_reference, load_reference
from .seeds import parse_seeds


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(source, output, seeds, input_hashes=None, purpose="held-out; no tuning"):
    source, output = Path(source), Path(output)
    if output.exists():
        raise ValueError("frozen suite already exists; choose a new version directory")
    prepared = []
    for seed in seeds:
        folder = source / f"seed_{seed}"
        scenario = load_scenario(folder / "scenario.json")
        if scenario.seed != seed:
            raise ValueError("scenario seed does not match case directory")
        benchmark = json.loads((folder / "benchmark.json").read_text(encoding="utf-8"))
        prepared.append((seed, scenario.to_dict(), make_reference(scenario, benchmark)))
    if not prepared:
        raise ValueError("cannot freeze an empty suite")
    output.mkdir(parents=True)
    cases = []
    for seed, scenario, reference in prepared:
        folder = output / f"seed_{seed}"
        folder.mkdir()
        for filename, value in (("scenario.json", scenario), ("reference.json", reference)):
            (folder / filename).write_bytes(canonical_bytes(value))
        cases.append({"seed": seed, "scenario": f"seed_{seed}/scenario.json",
                      "reference": f"seed_{seed}/reference.json",
                      "scenario_file_sha256": file_hash(folder / "scenario.json"),
                      "reference_file_sha256": file_hash(folder / "reference.json")})
    manifest = {"schema_version": 1, "purpose": purpose, "source_input_sha256": input_hashes or {},
                "cases": cases}
    (output / "manifest.json").write_bytes((json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return manifest


def load_suite(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not manifest.get("cases"):
        raise ValueError("invalid frozen-suite manifest")
    cases, seen = [], set()
    for case in manifest["cases"]:
        seed = case["seed"]
        if type(seed) is not int or seed in seen:
            raise ValueError("duplicate or invalid suite seed")
        seen.add(seed)
        paths = {}
        for kind in ("scenario", "reference"):
            path = (directory / case[kind]).resolve()
            if path.is_relative_to(directory) and not path.is_file():
                hint = "; this suite's answers are withheld (only their hashes are published)" if kind == "reference" else ""
                raise ValueError(f"frozen {kind} file missing for seed {seed}{hint}")
            if not path.is_relative_to(directory) or file_hash(path) != case[kind + "_file_sha256"]:
                raise ValueError(f"frozen {kind} file changed for seed {seed}")
            paths[kind] = path
        scenario = load_scenario(paths["scenario"])
        if scenario.seed != seed:
            raise ValueError("suite seed/scenario mismatch")
        reference = load_reference(paths["reference"], scenario)
        cases.append({"seed": seed, "scenario": scenario, "reference": reference, **{k + "_path": v for k, v in paths.items()}})
    return manifest, cases


def check_drift(manifest, data_dir):
    return [{"file": name, "expected": digest,
             "actual": file_hash(Path(data_dir) / name) if (Path(data_dir) / name).is_file() else None}
            for name, digest in manifest["source_input_sha256"].items()
            if not (Path(data_dir) / name).is_file() or file_hash(Path(data_dir) / name) != digest]


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("freeze")
    p.add_argument("--source", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--seeds", default="100-199")
    p.add_argument("--provenance", default="docs/validation_2026-10-23.json")
    p.add_argument("--purpose", default="held-out; no tuning")
    p = sub.add_parser("verify")
    p.add_argument("suite")
    p.add_argument("--data-dir", default="rail_data/processed")
    args = parser.parse_args()
    if args.command == "freeze":
        hashes = json.loads(Path(args.provenance).read_text(encoding="utf-8"))["input_sha256"]
        result = freeze(args.source, args.output, parse_seeds(args.seeds), hashes, args.purpose)
        print(json.dumps({"frozen_cases": len(result["cases"]), "output": args.output}))
        return 0
    try:
        manifest, cases = load_suite(args.suite)
    except ValueError as error:
        print(f"error: {error}")
        return 2
    drift = check_drift(manifest, args.data_dir)
    print(json.dumps({"verified_cases": len(cases), "source_drift": drift}, indent=2))
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
