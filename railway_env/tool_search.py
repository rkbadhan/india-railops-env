"""Codex-authored planning experiment using only the public play tool protocol.

This is deterministic tool search, not a fresh LLM inference for each problem.
It never opens benchmark files or imports the solver. Reference scores arrive
only after submit. Full observations, requests and results are persisted.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from .models import save_json
from .seeds import parse_seeds


HOLD_MINUTES = (5, 10, 15)


def candidate_moves(order, holds, plan, weights, routes, use_holds, holds_only=False):
    """Neighbours of the incumbent: every reordering first, then single-train holds.

    The caller reserves the last part of the budget for holds (`holds_only`),
    because reorderings alone would otherwise use all of it. A hold delays one train's departure from one station by 5, 10 or 15 minutes
    beyond where the incumbent plan has it leave. Lighter trains are tried first.
    """
    def promotions():
        for i in range(1, len(order)):
            for j in range(i):
                candidate = order[:]
                candidate.insert(j, candidate.pop(i))
                yield candidate, holds

    def delays():
        departures = {(v["train_id"], v["station"]): v["departure"] for v in plan["visits"]}
        for minutes in HOLD_MINUTES:
            for train_id in sorted(order, key=lambda k: (weights[k], k)):
                for station in routes[train_id][:-1]:
                    until = departures[train_id, station] + minutes
                    merged = {(h["train_id"], h["station"]): h["until"] for h in holds}
                    merged[train_id, station] = max(merged.get((train_id, station), 0), until)
                    yield order, [{"train_id": t, "station": s, "until": u} for (t, s), u in sorted(merged.items())]

    if not holds_only:
        yield from promotions()
    if use_holds:
        yield from delays()


def search(scenario_path, output, budget=32, use_holds=False):
    started = time.perf_counter()
    if budget < 1:
        raise ValueError("trial budget must be at least 1")
    transcript = []
    process = subprocess.Popen([sys.executable, "-B", "-m", "railway_env", "play", str(scenario_path),
                                "--trial-budget", str(budget), "--no-allow-timetable"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8")
    try:
        def receive():
            line = process.stdout.readline()
            if not line:
                raise RuntimeError(process.stderr.read().strip() or "play ended without a response")
            return json.loads(line)

        opening = receive()
        transcript.append({"opening": opening})

        def call(name, arguments):
            request = {"tool": name, "arguments": arguments}
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            response = receive()
            transcript.append({"request": request, "response": response})
            return response

        best_proposal = {"policy": "passenger_first"}
        best = call("try_plan", best_proposal)
        first_score = best["weighted_delay"]
        controller = {"valid": False}
        if budget >= 2:
            controller = call("try_plan", {"policy": "controller_rule"})
        if controller["valid"] and controller["weighted_delay"] < best["weighted_delay"]:
            best_proposal, best = {"policy": "controller_rule"}, controller
        # Read only the controller report and trial feedback, never the saved scenario/oracle.
        trains = []
        for line in opening["observation"].splitlines():
            if " [passenger," in line or " [freight," in line:
                identifier, rest = line.split(" [", 1)
                kind, _, weight = rest.split("]: ", 1)[0].split(", ")
                ready = int(rest.split("ready ", 1)[1].split(" ", 1)[0])
                trains.append((identifier, kind, int(weight.split()[1]), ready))
        by_id = {t[0]: t for t in trains}
        policy_orders = {
            "passenger_first": sorted(by_id, key=lambda k: (by_id[k][1] != "passenger", -by_id[k][2], by_id[k][3], k)),
            "controller_rule": list(dict.fromkeys(v["train_id"] for v in controller.get("plan", {}).get("visits", [])))
        }
        order = policy_orders[best_proposal["policy"]]
        trials, seen = min(budget, 2), {json.dumps([order, []])}
        weights = {k: by_id[k][2] for k in by_id}
        routes = {}
        for line in opening["observation"].splitlines():
            if line.startswith("  timetable (arrival/departure): "):
                routes[identifier_line] = [item.split()[0] for item in line.split(": ", 1)[1].split(", ")]
            elif " [passenger," in line or " [freight," in line:
                identifier_line = line.split(" [", 1)[0]
        holds = list(best_proposal.get("holds", []))
        # First-improvement hill climb: promote a train earlier in the order and, once
        # no promotion helps, hold one train. Accept strict improvements and restart
        # around the new incumbent, within the fixed budget.
        reserve = budget // 4 if use_holds else 0  # trials kept back for hold moves
        while trials < budget:
            improved = False
            holds_only = trials >= budget - reserve
            for candidate, candidate_holds in candidate_moves(order, holds, best["plan"], weights, routes,
                                                              use_holds, holds_only):
                key = json.dumps([candidate, candidate_holds])
                if key in seen:
                    continue
                seen.add(key)
                proposal = {"policy": "passenger_first", "priority_order": candidate}
                if candidate_holds:
                    proposal["holds"] = candidate_holds
                response = call("try_plan", proposal)
                trials += 1
                if response["valid"] and response["weighted_delay"] < best["weighted_delay"]:
                    order, holds, best_proposal, best = candidate, candidate_holds, proposal, response
                    improved = True
                    break
                if trials >= budget:
                    break
                if not holds_only and trials >= budget - reserve:
                    improved = True  # reorder allowance spent: restart in hold mode
                    break
            if not improved:
                break
        result = call("submit", best_proposal)
        process.stdin.close()
        exit_code = process.wait(timeout=60)
        if exit_code:
            raise RuntimeError(process.stderr.read())
        record = {"method": "Codex-authored deterministic play-tool search; no per-case LLM calls",
                  "scenario_sha256": opening["scenario_sha256"], "env_version": opening.get("env_version"),
                  "wall_time_seconds": time.perf_counter() - started,
                  "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
                  "trial_budget": budget, "trials": trials, "passenger_first": first_score,
                  "proposal": best_proposal, "result": result, "transcript": transcript}
        save_json(output, record)
        return record
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problems", required=True, help="sweep directory containing seed_N/scenario.json")
    parser.add_argument("--output", default="runs/codex_tool_search")
    parser.add_argument("--seeds", default="0-99")
    parser.add_argument("--trials", type=int, default=32)
    parser.add_argument("--holds", action="store_true",
                        help="also try holds in the last quarter of the budget (measured weaker than reorder-only)")
    args = parser.parse_args()
    records = []
    for seed in parse_seeds(args.seeds):
        try:
            record = search(Path(args.problems) / f"seed_{seed}" / "scenario.json",
                            Path(args.output) / f"seed_{seed}.json", args.trials, args.holds)
            result = record["result"]
            row = {"seed": seed, "passenger_first": record["passenger_first"], "trials": record["trials"],
                   **{key: result.get(key) for key in ("valid", "weighted_delay", "excess_delay_over_optimum",
                                                      "improvement_share", "normalized_reward", "normalization_status")}}
            row["beats_passenger_first"] = bool(result["valid"] and result["weighted_delay"] < record["passenger_first"])
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            row = {"seed": seed, "error": str(error)}
        records.append(row)
        shares = [r["improvement_share"] for r in records if r.get("improvement_share") is not None]
        rewards = [r["normalized_reward"] for r in records if r.get("normalized_reward") is not None]
        summary = {"method": "Codex-authored deterministic play-tool search; no per-case LLM calls",
                   "attempted": len(records), "valid": sum(bool(r.get("valid")) for r in records),
                   "errors": sum("error" in r for r in records),
                   "beats_passenger_first": sum(bool(r.get("beats_passenger_first")) for r in records),
                   "reaches_certified_optimum": sum(r.get("excess_delay_over_optimum") == 0 for r in records),
                   "mean_improvement_share": sum(shares) / len(shares) if shares else None,
                   "cases_with_headroom": len(shares),
                   "mean_normalized_reward": sum(rewards) / len(rewards) if rewards else None,
                   "reward_cases": len(rewards), "uses_holds": args.holds, "trial_budget": args.trials}
        save_json(Path(args.output) / "results.json", {"summary": summary, "cases": records})
        print(f"seed {seed}: {row.get('passenger_first')} -> {row.get('weighted_delay', row.get('error'))}", flush=True)
    print(json.dumps(summary, indent=2))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
