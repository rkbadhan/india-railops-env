"""Headline gap metrics per model and trial budget, with bootstrap intervals over seeds.

No-answer episodes (no valid submission) count as reward -1, the same value an
invalid plan earns, so a model cannot look better by failing to answer.
Infrastructure failures (provider, adapter, configuration, environment) are not
model mistakes: they are excluded from every rate and reported as a count.
"""

import random
import statistics
from collections import defaultdict

INFRASTRUCTURE_ERRORS = frozenset(("adapter_error", "worker_error", "environment_error",
                                   "configuration_error", "provider_error"))
NO_ANSWER_REWARD = -1.0


def is_eligible(record):
    return not (record["error"] and record["error"]["type"] in INFRASTRUCTURE_ERRORS)


def is_answered(record):
    result = record["result"]
    return bool(is_eligible(record) and not record["error"] and result and result["valid"])


def episode_metrics(record):
    """Per-episode values for an eligible episode; None for an infrastructure failure."""
    if not is_eligible(record):
        return None
    answered = is_answered(record)
    result = record["result"] if answered else None
    reward = result.get("normalized_reward") if answered else NO_ANSWER_REWARD
    return {"answered": answered, "reward": reward,
            "at_optimum": bool(answered and result.get("excess_delay_over_optimum") == 0),
            "score": result["weighted_delay"] if answered else None}


def paired_outcome(record, scripted_score):
    """Win/tie/loss against the scripted search on the same seed and budget.

    A lower weighted delay wins. An episode with no answer is a loss.
    """
    metrics = episode_metrics(record)
    if metrics is None:
        return None
    if not metrics["answered"] or metrics["score"] > scripted_score:
        return "loss"
    return "tie" if metrics["score"] == scripted_score else "win"


def bootstrap_interval(values_by_seed, resamples=10000, seed=0, level=0.95):
    """Percentile bootstrap of the mean over seeds. Each seed's value is already averaged over repeats."""
    values = [v for v in values_by_seed.values() if v is not None]
    if not values:
        return None
    mean = statistics.mean(values)
    if len(values) == 1:
        return {"mean": mean, "low": mean, "high": mean, "seeds": 1}
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(resamples))
    tail = (1 - level) / 2
    return {"mean": mean, "low": means[int(tail * resamples)], "high": means[int((1 - tail) * resamples) - 1],
            "seeds": len(values)}


def _per_seed_mean(records, value):
    by_seed = defaultdict(list)
    for record in records:
        metrics = episode_metrics(record)
        if metrics is not None:
            by_seed[record["seed"]].append(value(metrics))
    return {seed: statistics.fmean(values) for seed, values in by_seed.items()}


def headline(records, resamples=10000):
    """One entry per (model, trial budget) with the numbers the gap report is built from."""
    groups = defaultdict(list)
    for record in records:
        groups[record["model"], record["trial_budget"]].append(record)
    rows = []
    for (model, budget), rows_in in sorted(groups.items()):
        eligible = [r for r in rows_in if is_eligible(r)]
        metrics = [episode_metrics(r) for r in eligible]
        answered = [m for m in metrics if m["answered"]]
        paired = [paired_outcome(r, r["comparison"]["scripted_search"]) for r in eligible]
        tokens = defaultdict(list)
        for r in rows_in:
            for key in ("input_tokens", "output_tokens", "cost_usd", "estimated_cost_usd"):
                if r["usage"].get(key) is not None:
                    tokens[key].append(r["usage"][key])
        n = len(eligible)
        entry = {
            "model": model, "trial_budget": budget, "episodes": len(rows_in), "eligible_episodes": n,
            "infrastructure_failures": len(rows_in) - n,
            "no_answer_rate": 1 - len(answered) / n if n else None,
            "no_answer_rate_ci": bootstrap_interval(_per_seed_mean(rows_in, lambda m: 0.0 if m["answered"] else 1.0), resamples),
            "mean_reward_no_answer_is_minus_one": statistics.fmean(m["reward"] for m in metrics) if n else None,
            "mean_reward_ci": bootstrap_interval(_per_seed_mean(rows_in, lambda m: m["reward"]), resamples),
            "mean_reward_submitted_only": statistics.fmean(m["reward"] for m in answered) if answered else None,
            "at_optimum_rate": sum(m["at_optimum"] for m in metrics) / n if n else None,
            "at_optimum_rate_ci": bootstrap_interval(_per_seed_mean(rows_in, lambda m: float(m["at_optimum"])), resamples),
            "versus_scripted_search": {name: paired.count(name) for name in ("win", "tie", "loss")},
            "mean_input_tokens": statistics.fmean(tokens["input_tokens"]) if tokens["input_tokens"] else None,
            "mean_output_tokens": statistics.fmean(tokens["output_tokens"]) if tokens["output_tokens"] else None,
            "token_reporting_episodes": len(tokens["input_tokens"]),
            "reported_cost_usd_total": sum(tokens["cost_usd"]) if tokens["cost_usd"] else None,
            "estimated_cost_usd_total": sum(tokens["estimated_cost_usd"]) if tokens["estimated_cost_usd"] else None,
            "estimated_cost_usd_per_episode": statistics.fmean(tokens["estimated_cost_usd"]) if tokens["estimated_cost_usd"] else None,
        }
        wins = entry["versus_scripted_search"]
        entry["versus_scripted_search"].update(
            win_rate=wins["win"] / n if n else None, tie_rate=wins["tie"] / n if n else None,
            loss_rate=wins["loss"] / n if n else None)
        rows.append(entry)
    return rows


def scripted_headline(scripted, resamples=10000):
    """Scripted search is deterministic, so its interval varies only over seeds."""
    rewards = {row["seed"]: row["normalized_reward"] for row in scripted if row.get("normalized_reward") is not None}
    return {"mean_reward": bootstrap_interval(rewards, resamples), "seeds": len(rewards),
            "trial_budget": scripted[0]["trial_budget"] if scripted else None}


def estimated_cost(usage, prices):
    """Dollar estimate from token totals and {"input": x, "output": y} per million tokens, else None."""
    if not prices or usage.get("input_tokens") is None or usage.get("output_tokens") is None:
        return None
    return (usage["input_tokens"] * prices["input"] + usage["output_tokens"] * prices["output"]) / 1e6
