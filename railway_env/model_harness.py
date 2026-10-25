"""Repeated, budgeted model evaluation on frozen problems via a JSON worker."""

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .env_settings import load_env
from .episode import ENV_VERSION, DispatchEpisode, tools_for
from .gap_metrics import INFRASTRUCTURE_ERRORS, estimated_cost, headline, scripted_headline
from .models import save_json
from .references import scenario_hash
from .suite import load_suite, file_hash
from .tool_search import search
from .feedback import compact_feedback, bounded_request, trial_ledger_line, DEFAULT_CONTEXT_CHARS, FEEDBACK_VERSION
from .context_policy import context_policy
from .seeds import parse_seeds


# Include the old spelling so historical adapter crashes can be re-summarized.
ADAPTER_ERRORS = frozenset(("adapter_error", "worker_error"))
PROMPT_VERSION = "prompt_v2_brief"
PROVIDER_RETRIES = 3
PROVIDER_BACKOFF_SECONDS = 2.0
CONSECUTIVE_PROVIDER_FAILURE_LIMIT = 5
DEFAULT_MAX_OUTPUT_CONTEXT_CHARS = 24000


SYSTEM = """You control a railway dispatch board. Minimize weighted final-arrival delay
for every train. Use the supplied try_plan tool to test proposals and submit a
final proposal. You have a fixed trial budget. Each response must contain exactly
one tool action. You may reason before choosing an action; only observations and
tool feedback are available. There is no access to reference answers.
Feedback route lines use station arrival/departure berth. Older trial exchanges
may be omitted to fit the context cap; the board, best valid trial and a complete
one-line ledger of every attempted proposal and score are retained.
Reason briefly: a few short paragraphs at most, then call try_plan early and refine
from its feedback. The default plan named on the board is always available (policy
passenger_first). An answer that runs out of output before a tool call counts as a
failure, so a short plan you submit beats a perfect plan you never finish."""


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def worker_environment():
    """Worker environment: the current one plus .env settings and an import path to this package."""
    environment = dict(os.environ)
    load_env(PROJECT_ROOT / ".env", environment)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(PROJECT_ROOT), environment.get("PYTHONPATH"))))
    return environment


def worker_call(command, request, timeout):
    """Run a worker in an empty scratch directory, not the repository.

    A worker with file tools starting in the repo could open the answer keys
    under benchmarks/. This stops relative-path and directory-listing discovery;
    it is not a sandbox. A worker may still read an absolute path, so isolate
    real agents by running them as another OS user or in a container that does
    not mount the suite directory.
    """
    with tempfile.TemporaryDirectory(prefix="railway_worker_") as scratch:
        completed = subprocess.run(command, input=json.dumps(request), text=True, encoding="utf-8",
                                   capture_output=True, timeout=timeout, check=False,
                                   cwd=scratch, env=worker_environment())
    if completed.returncode:
        raise RuntimeError(f"model worker exited {completed.returncode}: {completed.stderr[-1000:]}")
    return json.loads(completed.stdout)


def usage_totals(responses):
    result = {}
    for key in ("input_tokens", "output_tokens", "cost_usd"):
        values = [r["usage"].get(key) if isinstance(r.get("usage"), dict) else None for r in responses]
        valid = all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values)
        if key.endswith("tokens"):
            valid = valid and all(type(v) is int for v in values)
        result[key] = sum(values) if values and valid else None
    result["model_calls"] = len(responses)
    result["usage_status"] = "reported" if all(result[k] is not None for k in ("input_tokens", "output_tokens")) else "unreported"
    return result


def run_episode(case, model, repeat, trial_budget, allow_timetable, timeout=120, invoke=worker_call,
                context_chars=DEFAULT_CONTEXT_CHARS, retries=PROVIDER_RETRIES,
                backoff=PROVIDER_BACKOFF_SECONDS, sleep=time.sleep):
    started = time.perf_counter()
    episode = DispatchEpisode(case["scenario"], reference=case["reference"],
                              trial_budget=trial_budget, allow_timetable=allow_timetable)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": episode.observe()}]
    transcript, responses, final, error = [], [], None, None
    best_trial, context_sizes, trial_ledger = None, [], []
    provider_retries = 0
    for turn in range(trial_budget + 2):
        tools = tools_for(allow_timetable)
        if episode.trials_used >= trial_budget:
            tools = [tool for tool in tools if tool["name"] == "submit"]
        metadata = {"model_id": model["id"], "seed": case["seed"], "repeat": repeat,
                    "trials_remaining": trial_budget - episode.trials_used}
        for key in ("context_window_tokens", "max_output_tokens"):
            if key in model:
                metadata[key] = model[key]
        try:
            request, context = bounded_request(metadata, messages, tools, best_trial, context_chars, trial_ledger)
        except ValueError as exc:
            error = {"type": "configuration_error", "message": str(exc)}
            break
        context_sizes.append(context)
        try:
            response = invoke(model["command"], request, timeout)
            attempt = 0
            while attempt < retries and isinstance(response, dict) and isinstance(response.get("error"), dict)                     and response["error"].get("type") == "provider_error":
                sleep(backoff * 2 ** attempt)
                attempt += 1
                provider_retries += 1
                response = invoke(model["command"], request, timeout)
            if not isinstance(response, dict):
                raise ValueError("worker response must be an object")
            responses.append(response)
            if "error" in response:
                supplied = response["error"]
                kind = supplied.get("type") if isinstance(supplied, dict) else "adapter_error"
                if kind not in ("configuration_error", "provider_error", "model_error"):
                    kind = "adapter_error"
                error = {"type": kind, "message": supplied.get("message", "adapter failed") if isinstance(supplied, dict) else str(supplied)}
                transcript.append({"response": response, "tool_result": None, "context": context})
                break
            name, arguments = response["tool"], response.get("arguments", {})
            if name not in ("try_plan", "submit"):
                result = {"valid": False, "errors": ["choose try_plan or submit"]}
            else:
                result = episode.call(name, arguments)
            if name == "try_plan":
                trial_ledger.append(trial_ledger_line(len(trial_ledger) + 1, arguments, result))
            feedback = compact_feedback(result)
            feedback["trials_remaining"] = trial_budget - episode.trials_used
            transcript.append({"response": response, "tool_result": result, "model_feedback": feedback, "context": context})
            action = {"tool": name, "arguments": arguments}
            messages.extend([{"role": "assistant", "content": json.dumps(action, separators=(",", ":"))},
                             {"role": "user", "content": json.dumps(feedback, separators=(",", ":"))}])
            if name == "try_plan" and result.get("valid") and (
                    best_trial is None or result["weighted_delay"] < best_trial["weighted_delay"]):
                best_trial = {"arguments": arguments, "weighted_delay": result["weighted_delay"],
                              "train_delays": result.get("train_delays", {})}
            if result.get("error_type") == "internal_error" or result.get("evaluation_error"):
                error = {"type": "environment_error", "message": result.get("evaluation_error", result.get("errors"))}
                final = result if episode.submitted else None
                break
            if episode.submitted:
                final = result
                break
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as exc:
            error = {"type": "adapter_error", "message": str(exc)}
            break
    if final is None and error is None:
        error = {"type": "no_submission", "message": "model-call limit reached without submit"}
    usage = usage_totals(responses)
    usage["estimated_cost_usd"] = estimated_cost(usage, model.get("price_per_million"))
    return {"seed": case["seed"], "model": model["id"], "repeat": repeat,
            "env_version": ENV_VERSION, "prompt_version": PROMPT_VERSION, "provider_retries": provider_retries,
            "scenario_sha256": scenario_hash(case["scenario"]), "trial_budget": trial_budget,
            "observation": episode.observe(), "wall_time_seconds": time.perf_counter() - started,
            "trials_used": episode.trials_used, "allow_timetable": allow_timetable,
            "usage": usage, "result": final, "error": error, "transcript": transcript,
            "trial_ledger": trial_ledger,
            "model_context": {"feedback_version": FEEDBACK_VERSION, "limit_chars": context_chars,
                "max_request_chars": max((c["request_chars"] for c in context_sizes), default=0),
                "total_request_chars": sum(c["request_chars"] for c in context_sizes),
                "max_omitted_exchanges": max((c["omitted_exchanges"] for c in context_sizes), default=0)}}


def comparisons(record, reference, scripted_score):
    result = record["result"]
    score = result.get("weighted_delay") if result and result.get("valid") else None
    usable = score is not None and not record["error"]
    pf = reference["passenger_first"]["weighted_delay"]
    if not usable:
        outcome = "unscored"
    elif score >= pf:
        outcome = "ties_passenger_first" if score == pf else "worse_than_passenger_first"
    elif score < scripted_score:
        outcome = "beats_passenger_first_and_scripted_search"
    elif score == scripted_score:
        outcome = "beats_passenger_first_ties_scripted_search"
    else:
        outcome = "beats_passenger_first_but_loses_to_scripted_search"
    proposal = record["transcript"][-1]["response"].get("arguments", {}) if record.get("transcript") else {}
    return {"passenger_first": pf, "scripted_search": scripted_score, "outcome": outcome,
            "beats_passenger_first": bool(usable and score < pf),
            "beats_scripted_search": bool(usable and score < scripted_score),
            "ties_scripted_search": bool(usable and score == scripted_score),
            "unsafe_timetable_submission": (bool(result and not result["valid"] and
                isinstance(proposal, dict) and "visits" in proposal)
                if record["allow_timetable"] else None)}


def summarize(records):
    groups = defaultdict(list)
    for row in records:
        groups[row["model"], row["repeat"]].append(row)
    summaries = []
    for (model, repeat), rows in sorted(groups.items()):
        scored = [r for r in rows if r["result"] and r["result"]["valid"] and not r["error"]]
        certified = [r for r in scored if r["result"].get("excess_delay_over_optimum") is not None]
        shares = [r["result"]["improvement_share"] for r in certified if r["result"]["improvement_share"] is not None]
        rewards = [r["result"]["normalized_reward"] for r in rows
                   if r["result"] and not r["error"] and r["result"].get("normalized_reward") is not None]
        summary = {"model": model, "repeat": repeat, "attempted": len(rows), "valid_scored": len(scored),
            "environment_errors": sum(bool(r["error"] and r["error"]["type"] == "environment_error") for r in rows),
            "configuration_errors": sum(bool(r["error"] and r["error"]["type"] == "configuration_error") for r in rows),
            "provider_errors": sum(bool(r["error"] and r["error"]["type"] == "provider_error") for r in rows),
            "adapter_errors": sum(bool(r["error"] and r["error"]["type"] in ADAPTER_ERRORS) for r in rows),
            "model_errors": sum(bool(r["error"] and r["error"]["type"] not in INFRASTRUCTURE_ERRORS) for r in rows),
            "invalid_submissions": sum(bool(r["result"] and not r["result"]["valid"]) for r in rows),
            "unsafe_metric_status": "enabled" if rows[0]["allow_timetable"] else "not_applicable_compiled_actions_only",
            "mean_excess_over_optimum": statistics.mean(r["result"]["excess_delay_over_optimum"] for r in certified) if certified else None,
            "certified_scored_cases": len(certified), "mean_improvement_share": statistics.mean(shares) if shares else None,
            "cases_with_headroom": len(shares),
            "mean_normalized_reward": statistics.mean(rewards) if rewards else None, "reward_cases": len(rewards),
            "beats_passenger_first": sum(r["comparison"]["beats_passenger_first"] for r in rows),
            "beats_scripted_search": sum(r["comparison"]["beats_scripted_search"] for r in rows),
            "ties_scripted_search": sum(r["comparison"]["ties_scripted_search"] for r in rows)}
        eligible = [r for r in rows if not (r["error"] and r["error"]["type"] in INFRASTRUCTURE_ERRORS)]
        summary["model_eligible_attempts"] = len(eligible)
        summary["unsafe_submission_rate"] = (sum(r["comparison"]["unsafe_timetable_submission"] is True for r in eligible) / len(eligible)
                                              if eligible and rows[0]["allow_timetable"] else None)
        summary["passenger_first_win_rate"] = summary["beats_passenger_first"] / len(eligible) if eligible else None
        summary["scripted_search_win_rate"] = summary["beats_scripted_search"] / len(eligible) if eligible else None
        summary["outcomes"] = {name: sum(r["comparison"]["outcome"] == name for r in rows) for name in
            ("beats_passenger_first_and_scripted_search", "beats_passenger_first_ties_scripted_search",
             "beats_passenger_first_but_loses_to_scripted_search", "ties_passenger_first", "worse_than_passenger_first", "unscored")}
        summary["mean_wall_time_seconds"] = statistics.mean(r["wall_time_seconds"] for r in rows)
        for key in ("input_tokens", "output_tokens", "cost_usd"):
            values = [r["usage"][key] for r in rows if r["usage"][key] is not None]
            summary["mean_" + key] = statistics.mean(values) if values else None
            summary[key + "_reported_cases"] = len(values)
        summaries.append(summary)
    by_model = defaultdict(list)
    for row in summaries:
        by_model[row["model"]].append(row)
    variation = []
    for model, rows in by_model.items():
        values = [r["passenger_first_win_rate"] for r in rows if r["passenger_first_win_rate"] is not None]
        variation.append({"model": model, "repeats": len(rows), "mean_passenger_first_win_rate": statistics.mean(values) if values else None,
                          "stddev_passenger_first_win_rate": statistics.stdev(values) if len(values) > 1 else None,
                          "min_win_rate": min(values) if values else None, "max_win_rate": max(values) if values else None})
    return {"per_repeat": summaries, "repeat_variation": variation}


RESUME_KEYS = ("suite_manifest_sha256", "models", "trial_budget", "allow_timetable", "feedback_version",
               "context_chars", "env_version", "prompt_version")


def largest_output_cap(model, context_chars, chars_per_token, overhead_tokens):
    """Largest output allowance the declared window leaves after the shared input allowance."""
    window = model.get("context_window_tokens")
    if type(window) is not int or window <= 0:
        raise ValueError("--max-output needs a positive integer context_window_tokens for every model")
    cap = window - overhead_tokens - math.ceil(context_chars / chars_per_token)
    if cap < 1024:
        raise ValueError(f"{model['id']}: window leaves under 1024 output tokens after the input allowance")
    return cap


def validate_prices(models):
    for model in models:
        prices = model.get("price_per_million")
        if prices is not None and (not isinstance(prices, dict) or set(prices) != {"input", "output"} or
                any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in prices.values())):
            raise ValueError('price_per_million must be {"input": dollars, "output": dollars}, non-negative')


def comparable_models(models):
    """Model configuration without prices, which may be corrected without invalidating finished episodes."""
    return [{k: v for k, v in model.items() if k != "price_per_million"} for model in models]


def run(suite, models, output, repeats=3, trial_budget=32, allow_timetable=False, timeout=120,
        context_chars=None, chars_per_token=2.5, overhead_tokens=1024, seeds=None, workers=1, resume=False,
        max_output=False, retries=PROVIDER_RETRIES, backoff=PROVIDER_BACKOFF_SECONDS, sleep=time.sleep,
        resamples=10000, max_cost=None):
    if type(repeats) is not int or not 1 <= repeats <= 8 or type(trial_budget) is not int or not 1 <= trial_budget <= 100:
        raise ValueError("use 1-8 repeats and a trial budget of 1-100")
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("workers must be 1-32")
    if not models or any(not isinstance(m.get("id"), str) or not m["id"] for m in models) or len({m["id"] for m in models}) != len(models):
        raise ValueError("provide distinct model ids")
    for model in models:
        if not isinstance(model.get("command"), list) or not model["command"] or any(not isinstance(a, str) for a in model["command"]):
            raise ValueError("each model needs an argv command array")
    validate_prices(models)
    if max_cost is not None and (type(max_cost) not in (int, float) or max_cost <= 0 or
                                 any(not m.get("price_per_million") for m in models)):
        raise ValueError("a spending cap needs a positive amount and price_per_million for every model")
    if not 0 < timeout <= 3600:
        raise ValueError("worker timeout must be in (0, 3600]")
    if max_output:
        context_chars = context_chars or DEFAULT_MAX_OUTPUT_CONTEXT_CHARS
        models = [{**model, "max_output_tokens": largest_output_cap(model, context_chars, chars_per_token, overhead_tokens)}
                  for model in models]
    policy = context_policy(models, context_chars, chars_per_token, overhead_tokens)
    context_chars = policy["context_chars"]
    manifest, cases = load_suite(suite)
    if seeds is not None:
        requested = set(seeds)
        available = {case["seed"] for case in cases}
        if not requested or not requested <= available:
            raise ValueError("pilot seeds must be present in the frozen suite")
        cases = [case for case in cases if case["seed"] in requested]
    output = Path(output)
    configuration = {"suite_manifest_sha256": file_hash(Path(suite) / "manifest.json"), "models": models,
                     "repeats": repeats, "trial_budget": trial_budget, "allow_timetable": allow_timetable,
                     "feedback_version": FEEDBACK_VERSION, "context_chars": context_chars,
                     "context_policy": policy, "env_version": ENV_VERSION, "prompt_version": PROMPT_VERSION,
                     "provider_retries": retries, "workers": workers,
                     "case_seeds": [case["seed"] for case in cases],
                     "reference_policy": "stored certified optima only; unproved cases listed separately"}
    if (output / "results.json").exists():
        if not resume:
            raise ValueError("choose a fresh output directory to preserve earlier runs, or pass resume")
        previous = json.loads((output / "results.json").read_text(encoding="utf-8"))["configuration"]
        changed = [key for key in RESUME_KEYS
                   if (comparable_models(previous.get(key)) if key == "models" else previous.get(key)) !=
                   (comparable_models(configuration[key]) if key == "models" else configuration[key])]
        if changed:
            raise ValueError("cannot resume: configuration differs in " + ", ".join(changed))
    scripted = []
    for case in cases:
        path = output / "scripted_search" / f"seed_{case['seed']}.json"
        script = None
        if path.exists():
            script = json.loads(path.read_text(encoding="utf-8"))
            if script.get("trial_budget") != trial_budget:
                script = None
        if script is None:
            script = search(case["scenario_path"], path, trial_budget)
        script_score = script["result"]["weighted_delay"]
        if not script["result"]["valid"] or script_score is None:
            raise RuntimeError("scripted reference failed")
        scripted.append({"seed": case["seed"], "trial_budget": trial_budget,
                         "trials_used": script["trials"], "score": script_score,
                         "improvement_share": script["result"]["improvement_share"],
                         "normalized_reward": script["result"].get("normalized_reward"),
                         "excess_delay_over_optimum": script["result"]["excess_delay_over_optimum"]})
    scores = {row["seed"]: row["score"] for row in scripted}
    records, lock, stop = [], threading.Lock(), threading.Event()
    state = {"failure": None, "consecutive_provider": 0}

    def persist(final=False):
        unproved = [r for r in records if not r["reference"]["proven_optimal"]]
        ordered = sorted(records, key=lambda r: (r["seed"], r["model"], r["repeat"]))
        document = {"configuration": configuration, "summary": summarize(ordered), "scripted_search": scripted,
                    "unproved_cases": unproved, "cases": ordered}
        if final:
            document["headline"] = headline(ordered, resamples)
            document["scripted_headline"] = scripted_headline(scripted, resamples)
        save_json(output / "results.json", document)
        return document

    def episode(case, model_index, model, repeat):
        if stop.is_set():
            return
        path = output / f"model_{model_index}" / f"seed_{case['seed']}_repeat_{repeat}.json"
        record = None
        if resume and path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if not (saved["error"] and saved["error"]["type"] in INFRASTRUCTURE_ERRORS):
                record = saved
        if record is None:
            record = run_episode(case, model, repeat, trial_budget, allow_timetable, timeout, context_chars=context_chars,
                                 retries=retries, backoff=backoff, sleep=sleep)
            record["comparison"] = comparisons(record, case["reference"], scores[case["seed"]])
            record["reference"] = {"proven_optimal": case["reference"]["oracle"]["proven_optimal"],
                "incumbent": case["reference"]["oracle"]["weighted_delay"], "best_bound": case["reference"]["oracle"]["best_bound"]}
            save_json(path, record)
        kind = record["error"]["type"] if record["error"] else None
        with lock:
            records.append({k: v for k, v in record.items() if k not in ("transcript", "observation")})
            persist()
            state["consecutive_provider"] = state["consecutive_provider"] + 1 if kind == "provider_error" else 0
            if kind in ADAPTER_ERRORS | {"configuration_error"}:
                state["failure"] = "stopped on adapter/context failure; inspect saved results before retrying"
            elif max_cost is not None and sum(r["usage"].get("estimated_cost_usd") or 0 for r in records) >= max_cost:
                state["failure"] = (f"spending cap reached: estimated ${sum(r['usage'].get('estimated_cost_usd') or 0 for r in records):.2f} "
                                    f"of ${max_cost:.2f}; resume with a higher cap to continue")
            elif state["consecutive_provider"] >= CONSECUTIVE_PROVIDER_FAILURE_LIMIT:
                state["failure"] = "stopped after repeated provider failures; resume the run when the provider recovers"
            if state["failure"]:
                stop.set()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(episode, case, model_index, model, repeat)
                   for case in cases for model_index, model in enumerate(models) for repeat in range(repeats)]
        for future in futures:
            future.result()
    document = persist(final=True)
    if state["failure"]:
        raise RuntimeError(state["failure"])
    return {"configuration": configuration, "summary": document["summary"], "headline": document["headline"],
            "cases": document["cases"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default="benchmarks/heldout_v1")
    parser.add_argument("--models", required=True, help="JSON array of {id, command: [argv...], optional price_per_million}")
    parser.add_argument("--seeds", help="optional subset of the frozen suite, e.g. 100-102")
    parser.add_argument("--output")
    parser.add_argument("--preflight", action="store_true", help="print shared context policy without calling models")
    parser.add_argument("--repeats", type=int, default=3, help="independent fresh conversations per case (1-8)")
    parser.add_argument("--trials", type=int, default=32)
    parser.add_argument("--allow-timetable", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--workers", type=int, default=1, help="episodes run concurrently (1-32)")
    parser.add_argument("--resume", action="store_true", help="continue an interrupted run in --output, skipping finished episodes")
    parser.add_argument("--max-cost", type=float, help="stop (resumably) once estimated spend reaches this many dollars")
    parser.add_argument("--max-output", action="store_true",
                        help="give every model the largest output cap its window allows after the input allowance")
    parser.add_argument("--context-chars", type=int, help="shared cap; estimated from declared model windows, otherwise 24000")
    parser.add_argument("--chars-per-token", type=float, default=2.5, help="explicit sizing estimate, not a tokenizer (default: 2.5)")
    parser.add_argument("--overhead-tokens", type=int, default=1024, help="token reserve beyond output allowance (default: 1024)")
    args = parser.parse_args()
    models = json.loads(Path(args.models).read_text(encoding="utf-8"))
    if args.max_output:
        chars = args.context_chars or DEFAULT_MAX_OUTPUT_CONTEXT_CHARS
        models = [{**m, "max_output_tokens": largest_output_cap(m, chars, args.chars_per_token, args.overhead_tokens)} for m in models]
        args.context_chars = chars
    if args.preflight:
        print(json.dumps(context_policy(models, args.context_chars, args.chars_per_token, args.overhead_tokens), indent=2))
        return 0
    if not args.output:
        parser.error("--output is required unless using --preflight")
    try:
        result = run(args.suite, models, args.output,
                     args.repeats, args.trials, args.allow_timetable, args.timeout, args.context_chars,
                     args.chars_per_token, args.overhead_tokens, parse_seeds(args.seeds) if args.seeds else None,
                     workers=args.workers, resume=args.resume, max_cost=args.max_cost)
    except RuntimeError as error:
        print(f"STOPPED: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"headline": result["headline"], "summary": result["summary"]}, indent=2))
    return int(any(r["error"] for r in result["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
