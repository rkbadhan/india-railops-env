"""Stored answer keys bound to complete scenario contents, independent of source CSVs."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .models import Plan
from .simulator import RailwaySimulator
from .scoring import normalized_score


def canonical_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def scenario_hash(scenario):
    return hashlib.sha256(canonical_bytes(scenario.to_dict())).hexdigest()


def make_reference(scenario, benchmark):
    reference = {"schema_version": 1, "scenario_sha256": scenario_hash(scenario),
                 "passenger_first": deepcopy(benchmark["passenger_first"]),
                 "controller_rule": deepcopy(benchmark["controller_rule"]),
                 "oracle": deepcopy(benchmark["oracle"])}
    validate_reference(scenario, reference)
    for name in ("passenger_first", "controller_rule"):
        reference[name].update(normalized_score(reference[name]["weighted_delay"],
                              reference["passenger_first"]["weighted_delay"], reference["oracle"]))
    return reference


def validate_reference(scenario, reference):
    if reference.get("schema_version") != 1 or reference.get("scenario_sha256") != scenario_hash(scenario):
        raise ValueError("stored answer does not match this scenario")
    simulator = RailwaySimulator(scenario)
    for name in ("passenger_first", "controller_rule", "oracle"):
        item = reference[name]
        if item.get("plan") is not None:
            score = simulator.evaluate(Plan.from_dict(item["plan"]))
            if not score["valid"] or score["weighted_delay"] != item["weighted_delay"]:
                raise ValueError(f"stored {name} plan/score failed replay")
        elif name != "oracle" or item.get("weighted_delay") is not None:
            raise ValueError(f"stored {name} has no plan")
    oracle = reference["oracle"]
    if oracle["proven_optimal"]:
        if oracle["status"] != "OPTIMAL" or oracle.get("plan") is None:
            raise ValueError("certified answer needs an OPTIMAL plan")
        if abs(oracle["weighted_delay"] - oracle["best_bound"]) > 1e-6:
            raise ValueError("certified answer does not equal its lower bound")
        if any(reference[name]["weighted_delay"] < oracle["weighted_delay"]
               for name in ("passenger_first", "controller_rule")):
            raise ValueError("stored baseline beats certified answer")
    elif oracle["status"] == "OPTIMAL":
        raise ValueError("inconsistent stored proof status")
    return reference


def load_reference(path, scenario):
    return validate_reference(scenario, json.loads(Path(path).read_text(encoding="utf-8")))
