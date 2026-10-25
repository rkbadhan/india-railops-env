"""CLI for generating problems, benchmarking, and a JSON-lines controller session."""

import argparse
import json
import sys
from pathlib import Path

from .benchmark import benchmark
from .episode import ENV_VERSION, DispatchEpisode, tools_for
from .references import load_reference, scenario_hash, make_reference
from .generator import generate
from .models import load_scenario, save_json
from .sweep import sweep, parse_seeds, format_summary


def write_benchmark(scenario, directory, time_limit):
    out = Path(directory)
    save_json(out / "scenario.json", scenario.to_dict())
    (out / "controller.txt").write_text(DispatchEpisode(scenario).observe() + "\n", encoding="utf-8")
    result = benchmark(scenario, time_limit)
    save_json(out / "benchmark.json", result)
    save_json(out / "reference.json", make_reference(scenario, result))
    for name in ("passenger_first", "controller_rule", "oracle"):
        if result[name].get("plan"):
            save_json(out / (name + "_plan.json"), result[name]["plan"])
    print(json.dumps({"scenario": scenario.id, "directory": str(out.resolve()),
                      "passenger_first": result["passenger_first"]["weighted_delay"],
                      "controller_rule": result["controller_rule"]["weighted_delay"],
                      "oracle": result["oracle"]["weighted_delay"],
                      "oracle_status": result["oracle"]["status"],
                      "oracle_bound": result["oracle"]["best_bound"],
                      "normalized": {policy: {key: result[policy][key] for key in (
                          "excess_delay_over_optimum", "improvement_share", "normalized_reward",
                          "normalization_status")}
                          for policy in ("passenger_first", "controller_rule")}}, indent=2))
    return 0 if result["oracle"]["plan"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("generate", "demo", "sweep"):
        p = sub.add_parser(command)
        if command == "sweep":
            p.add_argument("--seeds", default="0-99")
        else:
            p.add_argument("--seed", type=int, default=7)
        p.add_argument("--data-dir", default="rail_data/processed")
        p.add_argument("--stations", type=int, default=6)
        p.add_argument("--freight", type=int, default=8)
        p.add_argument("--section")
        p.add_argument("--disruption", choices=("late_train", "signal_failure"))
        p.add_argument("--disruptions", type=int, choices=(1, 2), default=2)
        p.add_argument("--block-mode", choices=("source", "shared", "directional"), default="source")
        p.add_argument("--freight-mix", action=argparse.BooleanOptionalAction, default=True,
                       help="synthetic heterogeneous freight speeds and priorities")
        p.add_argument("--output", default={"demo": "runs/demo", "generate": "runs/scenario.json", "sweep": "runs/sweep"}[command])
        if command in ("demo", "sweep"):
            p.add_argument("--time-limit", type=float, default=10.0)
    p = sub.add_parser("benchmark")
    p.add_argument("scenario")
    p.add_argument("--output", default="runs/benchmark")
    p.add_argument("--time-limit", type=float, default=10.0)
    for command in ("observe", "play"):
        p = sub.add_parser(command)
        p.add_argument("scenario")
        if command == "play":
            p.add_argument("--reference", help="stored answer; defaults to sibling reference.json when present")
            p.add_argument("--trial-budget", type=int)
            p.add_argument("--allow-timetable", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    try:
        if args.command == "sweep":
            result = sweep(parse_seeds(args.seeds), args.output, args.time_limit,
                           data_dir=args.data_dir, station_count=args.stations,
                           freight_count=args.freight, disruption=args.disruption, section=args.section,
                           disruption_count=args.disruptions, block_mode=args.block_mode, freight_mix=args.freight_mix)
            print(format_summary(result["summary"]))
            return 1 if result["summary"]["errors"] else 0
        if args.command in ("generate", "demo"):
            scenario = generate(args.seed, args.data_dir, args.stations, args.freight, args.disruption, args.section,
                                disruption_count=args.disruptions, block_mode=args.block_mode, freight_mix=args.freight_mix)
            if args.command == "demo":
                return write_benchmark(scenario, args.output, args.time_limit)
            save_json(args.output, scenario.to_dict())
            print(str(Path(args.output).resolve()))
            return 0
        scenario = load_scenario(args.scenario)
        if args.command == "benchmark":
            return write_benchmark(scenario, args.output, args.time_limit)
        reference = None
        if args.command == "play":
            reference_path = Path(args.reference) if args.reference else Path(args.scenario).with_name("reference.json")
            if reference_path.exists() or args.reference:
                reference = load_reference(reference_path, scenario)
        episode = DispatchEpisode(scenario, reference=reference,
                                  trial_budget=getattr(args, "trial_budget", None),
                                  allow_timetable=getattr(args, "allow_timetable", True))
        if args.command == "observe":
            print(episode.observe())
            return 0
        print(json.dumps({"observation": episode.observe(), "tools": tools_for(episode.allow_timetable),
                          "scenario_sha256": scenario_hash(scenario), "trial_budget": episode.trial_budget,
                          "env_version": ENV_VERSION}), flush=True)
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                result = episode.call(request["tool"], request.get("arguments"))
            except (ValueError, TypeError, KeyError) as error:
                result = {"error": str(error)}
            print(json.dumps(result), flush=True)
            if episode.submitted:
                break
        return 0
    except (ValueError, TypeError, KeyError, OSError, ImportError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
