"""Failure labels and the "before training" gap report from finished harness runs.

Reads the per-episode files a `model_harness` run writes, so it never calls a
model, the solver or the generator. Usage:

    python -m railway_env.gap_report --model "GLM-5.3 Flash=runs/v3_glm" --model "gpt-oss-120b=runs/v3_oss" \
        --output docs/gap_eval_2026-10-25

Each --model reads PREFIX_t1, PREFIX_t4, PREFIX_t32 and PREFIX_bo8 run directories.
"""

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from .gap_metrics import bootstrap_interval, episode_metrics, headline, is_answered, is_eligible, scripted_headline

NEAR_DUPLICATE_MINUTES = 5
LABELS = ("no_answer", "invalid_submission", "broke_a_good_plan", "repeated_proposals",
          "never_used_holds", "never_compared_to_default")
LABEL_TEXT = {
    "no_answer": "No valid submission (ran out of output, never submitted, or gave up)",
    "invalid_submission": "Submitted a plan the checker rejected",
    "broke_a_good_plan": "Scored worse than the default plan where headroom is zero or at most 5% of the optimum",
    "repeated_proposals": "Tried a duplicate or near-duplicate proposal (same order, same holds within 5 minutes)",
    "never_used_holds": "Never put a hold in any proposal",
    "never_compared_to_default": "Never ran the default plan itself with try_plan",
}


def load_run(directory):
    directory = Path(directory)
    summary = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    episodes = [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(directory.glob("model_*/seed_*_repeat_*.json"))]
    return {"configuration": summary["configuration"], "scripted": summary["scripted_search"], "episodes": episodes}


def proposals(record):
    """(tool, arguments) for every model action in order, skipping failed calls."""
    return [(t["response"]["tool"], t["response"].get("arguments") or {})
            for t in record["transcript"] if isinstance(t["response"], dict) and "tool" in t["response"]]


def _shape(arguments):
    holds = {(h.get("train_id"), h.get("station")): h.get("until") for h in arguments.get("holds") or []
             if isinstance(h, dict)}
    return (arguments.get("policy", "controller_rule"), tuple(arguments.get("priority_order") or ()), holds)


def near_duplicate(a, b, minutes=NEAR_DUPLICATE_MINUTES):
    policy_a, order_a, holds_a = _shape(a)
    policy_b, order_b, holds_b = _shape(b)
    if policy_a != policy_b or order_a != order_b or holds_a.keys() != holds_b.keys():
        return False
    return all(isinstance(holds_a[k], int) and isinstance(holds_b[k], int) and abs(holds_a[k] - holds_b[k]) <= minutes
               for k in holds_a)


def repeated_count(record):
    trials = [arguments for tool, arguments in proposals(record) if tool == "try_plan"]
    return sum(any(near_duplicate(trial, earlier) for earlier in trials[:i]) for i, trial in enumerate(trials))


def is_default_plan(arguments):
    return arguments.get("policy") == "passenger_first" and not arguments.get("holds") and not arguments.get("priority_order")


def labels(record):
    """Every failure label that applies to an eligible episode."""
    if not is_eligible(record):
        return set()
    found = set()
    result = record["result"]
    if not is_answered(record):
        found.add("no_answer")
    if result and not result["valid"]:
        found.add("invalid_submission")
    default, optimum = record["comparison"]["passenger_first"], record["reference"]["incumbent"]
    if is_answered(record) and result["weighted_delay"] > default and default - optimum <= math.ceil(0.05 * optimum):
        found.add("broke_a_good_plan")
    actions = proposals(record)
    if repeated_count(record):
        found.add("repeated_proposals")
    if not any(arguments.get("holds") for _, arguments in actions):
        found.add("never_used_holds")
    if not any(tool == "try_plan" and is_default_plan(arguments) for tool, arguments in actions):
        found.add("never_compared_to_default")
    return found


def label_table(episodes):
    eligible = [r for r in episodes if is_eligible(r)]
    counts = {label: sum(label in labels(r) for r in eligible) for label in LABELS}
    return {"eligible": len(eligible), "counts": counts}


def trim(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def example(record, label):
    actions = proposals(record)
    lines = [f"seed {record['seed']}, repeat {record['repeat']}, {record['trial_budget']} trial(s): "
             f"default plan {record['comparison']['passenger_first']}, optimum {record['reference']['incumbent']}, "
             + (f"submitted {record['result']['weighted_delay']}" if is_answered(record)
                else f"no valid answer ({(record['error'] or {}).get('message', 'invalid')})")]
    first = next((t["response"] for t in record["transcript"] if isinstance(t["response"], dict)
                  and "raw_provider_response" in t["response"]), None)
    if first:
        message = first["raw_provider_response"]["choices"][0]["message"]
        thought = message.get("reasoning_content") or message.get("content")
        if thought:
            lines.append("model, first response: " + trim(thought, 260))
    for number, line in enumerate(record["trial_ledger"][:3], 1):
        lines.append("trial " + trim(line, 190))
    if actions:
        lines.append("last action: " + trim(json.dumps(actions[-1][1]), 210))
    return lines


def pick_examples(episodes, label, count=2):
    matching = sorted((r for r in episodes if label in labels(r)), key=lambda r: (r["seed"], r["repeat"]))
    distinct, seen = [], set()
    for record in matching:
        if record["seed"] not in seen:
            distinct.append(record)
            seen.add(record["seed"])
    if len(distinct) <= count:
        return distinct
    step = (len(distinct) - 1) / (count - 1)
    return [distinct[round(i * step)] for i in range(count)]


def default_outcomes(episodes):
    """How final plans compare with the default plan, split by how much headroom the case has.

    "Low headroom" means the optimum is the default plan or within 5% of it (the same cut as the
    broke-a-good-plan label); "headroom" is every other case.
    """
    buckets = {"low_headroom": defaultdict(int), "headroom": defaultdict(int)}
    rewards = {"low_headroom": [], "headroom": []}
    for record in episodes:
        metrics = episode_metrics(record)
        if metrics is None:
            continue
        default, optimum = record["comparison"]["passenger_first"], record["reference"]["incumbent"]
        bucket = "low_headroom" if default - optimum <= math.ceil(0.05 * optimum) else "headroom"
        outcome = ("no_answer" if not metrics["answered"] else "better" if metrics["score"] < default
                   else "same" if metrics["score"] == default else "worse")
        buckets[bucket][outcome] += 1
        buckets[bucket]["episodes"] += 1
        rewards[bucket].append(metrics["reward"])
    return {name: {**{k: counts.get(k, 0) for k in ("episodes", "better", "same", "worse", "no_answer")},
                   "mean_reward": statistics.fmean(rewards[name]) if rewards[name] else None}
            for name, counts in buckets.items()}


def paired_difference(run, resamples=10000):
    """Model minus scripted-search reward per seed, bootstrapped over seeds (positive = model ahead)."""
    scripted = {row["seed"]: row["normalized_reward"] for row in run["scripted"]}
    by_seed = defaultdict(list)
    for record in run["episodes"]:
        metrics = episode_metrics(record)
        if metrics is not None:
            by_seed[record["seed"]].append(metrics["reward"])
    return bootstrap_interval({seed: statistics.fmean(values) - scripted[seed] for seed, values in by_seed.items()},
                              resamples)


def seed_subset(run, seeds):
    keep = set(seeds)
    return {"episodes": [r for r in run["episodes"] if r["seed"] in keep],
            "scripted": [s for s in run["scripted"] if s["seed"] in keep]}


def best_of_n(episodes):
    """Per seed: best and mean reward over the samples; no answer counts as -1."""
    by_seed = defaultdict(list)
    for record in episodes:
        metrics = episode_metrics(record)
        if metrics is not None:
            by_seed[record["seed"]].append((metrics["reward"], metrics["at_optimum"], record["comparison"]["passenger_first"],
                                            record["reference"]["incumbent"], metrics["score"]))
    rows = {}
    for seed, samples in by_seed.items():
        default, optimum = samples[0][2], samples[0][3]
        rows[seed] = {"best": max(s[0] for s in samples), "mean": statistics.fmean(s[0] for s in samples),
                      "any_at_optimum": any(s[1] for s in samples), "samples": len(samples),
                      "headroom": default - optimum,
                      "any_better_than_default": any(s[4] is not None and s[4] < default for s in samples),
                      "answered": sum(s[4] is not None for s in samples)}
    return rows


def best_of_summary(rows, floor_fraction=0.05):
    with_room = {s: r for s, r in rows.items() if r["headroom"] > 0}
    meaningful = {s: r for s, r in rows.items() if r["headroom"] > 0 and r["any_better_than_default"] is not None}
    summary = {"seeds": len(rows), "samples_per_seed": sorted({r["samples"] for r in rows.values()}),
               "best_of_n_reward": bootstrap_interval({s: r["best"] for s, r in rows.items()}),
               "mean_of_n_reward": bootstrap_interval({s: r["mean"] for s, r in rows.items()}),
               "seeds_with_headroom": len(with_room),
               "headroom_best_of_n_reward": bootstrap_interval({s: r["best"] for s, r in with_room.items()}),
               "headroom_mean_of_n_reward": bootstrap_interval({s: r["mean"] for s, r in with_room.items()}),
               "headroom_seeds_where_some_sample_beats_default":
                   sum(r["any_better_than_default"] for r in with_room.values()),
               "headroom_seeds_where_some_sample_is_optimal": sum(r["any_at_optimum"] for r in with_room.values()),
               "all_seeds_where_some_sample_is_optimal": sum(r["any_at_optimum"] for r in rows.values())}
    return summary


def chart(path, points):
    """Mean reward against trials: each model, scripted search, and the solver ceiling.

    `points` maps a model label to its per-budget points; scripted search is identical for every
    model (same seeds, deterministic), so it is drawn once from the first model's points.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ink, muted, grid, surface = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
    palette = ["#2a78d6", "#1baf7a", "#6250d6", "#e87ba4"]  # categorical slots 1, 3, 7, 5; orange is scripted search
    orange = "#eb6834"
    figure, axes = plt.subplots(figsize=(7.2, 4.4), dpi=160, facecolor=surface)
    axes.set_facecolor(surface)
    first = next(iter(points.values()))
    xs = [p["trials"] for p in first]
    series = [("scripted", orange, "Scripted search", first)]
    series += [("model", palette[i % len(palette)], label, pts) for i, (label, pts) in enumerate(points.items())]
    for key, color, label, pts in series:
        mean = [p[key]["mean"] for p in pts]
        axes.fill_between(xs, [p[key]["low"] for p in pts], [p[key]["high"] for p in pts], color=color, alpha=0.14, linewidth=0)
        axes.plot(xs, mean, color=color, linewidth=2, marker="o", markersize=7, markeredgecolor=surface, markeredgewidth=2, label=label)
    axes.plot([min(xs), max(xs)], [1, 1], color=muted, linewidth=1.5, linestyle=(0, (4, 3)), label="Solver (certified optimum)")
    axes.set_xscale("log", base=2)
    axes.set_xticks(xs)
    axes.set_xticklabels([str(x) for x in xs], color=muted)
    axes.set_xlim(0.8, 40)
    axes.set_ylim(-1.05, 1.1)
    axes.set_xlabel("try_plan budget per episode (trials)", color=muted)
    axes.set_ylabel("Mean reward (no answer = -1)", color=muted)
    axes.set_title("Mean reward against trials, seeds 100-119", color=ink, loc="left", fontsize=11, fontweight="bold")
    axes.grid(axis="y", color=grid, linewidth=0.8)
    axes.tick_params(colors=muted, length=0)
    for side in ("top", "right", "left"):
        axes.spines[side].set_visible(False)
    axes.spines["bottom"].set_color(grid)
    axes.legend(loc="lower right", frameon=False, labelcolor=muted, fontsize=9)
    figure.tight_layout()
    figure.savefig(path, facecolor=surface)
    plt.close(figure)


def fmt(interval, digits=2):
    if not interval:
        return "n/a"
    return f"{interval['mean']:.{digits}f} [{interval['low']:.{digits}f}, {interval['high']:.{digits}f}]"


def pct(interval):
    if not interval:
        return "n/a"
    return f"{100 * interval['mean']:.0f}% [{100 * interval['low']:.0f}, {100 * interval['high']:.0f}]"


def headline_row(entry, scripted_interval, label):
    wins = entry["versus_scripted_search"]
    return (f"| {label} | {entry['trial_budget']} | {entry['eligible_episodes']} | {pct(entry['no_answer_rate_ci'])} | "
            f"**{fmt(entry['mean_reward_ci'])}** | {entry['mean_reward_submitted_only']:.2f} | {pct(entry['at_optimum_rate_ci'])} | "
            f"{wins['win']} / {wins['tie']} / {wins['loss']} | {fmt(scripted_interval)} | "
            f"{entry['mean_input_tokens']:,.0f} / {entry['mean_output_tokens']:,.0f} | ${entry['estimated_cost_usd_per_episode']:.4f} |")


def build(runs, best_of_8, resamples=10000):
    data = {"runs": {}, "labels": {}, "examples": {}, "spend": {}}
    rows = []
    points = []
    common = list(range(100, 120))
    for budget, run in runs.items():
        scripted_all = scripted_headline(run["scripted"], resamples)
        slices = {"all seeds": run, "seeds 100-119": seed_subset(run, common),
                  "seeds 110-199 (excluding 100-109)": seed_subset(run, [s for s in range(110, 200)])}
        data["runs"][budget] = {}
        for name, part in slices.items():
            if not part["episodes"]:
                continue
            if name != "all seeds" and len(part["episodes"]) == len(run["episodes"]):
                data["runs"][budget][name] = data["runs"][budget]["all seeds"]  # same episodes: no duplicate table row
                continue
            entry = headline(part["episodes"], resamples)[0]
            sc = scripted_headline(part["scripted"], resamples)["mean_reward"]
            data["runs"][budget][name] = {"model": entry, "scripted_reward": sc}
            rows.append((budget, name, entry, sc))
        sub = data["runs"][budget].get("seeds 100-119")
        if sub:
            points.append({"trials": budget, "model": sub["model"]["mean_reward_ci"], "scripted": sub["scripted_reward"]})
        data["paired_difference"] = data.get("paired_difference", {})
        data["paired_difference"][budget] = paired_difference(run, resamples)
        data["labels"][budget] = label_table(run["episodes"])
        data.setdefault("default_outcomes", {})[budget] = default_outcomes(run["episodes"])
        data["examples"][budget] = {label: [example(r, label) for r in pick_examples(run["episodes"], label)] for label in LABELS}
        data["spend"][budget] = sum(r["usage"].get("estimated_cost_usd") or 0 for r in run["episodes"])
    if best_of_8:
        bo = best_of_8
        data["best_of_8"] = best_of_summary(best_of_n(bo["episodes"]))
        data["labels"]["best_of_8"] = label_table(bo["episodes"])
        data["default_outcomes"]["best_of_8"] = default_outcomes(bo["episodes"])
        data["spend"]["best_of_8"] = sum(r["usage"].get("estimated_cost_usd") or 0 for r in bo["episodes"])
    data["spend"]["total_estimated_usd"] = sum(v for v in data["spend"].values())
    return data, rows, points


def markdown(data, rows):
    out = ["| Slice | Trials | Episodes | No-answer rate (95% CI) | Mean reward, no answer = -1 (95% CI) | Mean reward, submitted only | At optimum (95% CI) | vs scripted search W / T / L | Scripted search reward (95% CI) | Tokens in / out per episode | Est. cost per episode |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for budget, name, entry, sc in rows:
        out.append(headline_row(entry, sc, name))
    return "\n".join(out)


def labels_markdown(data):
    budgets = [b for b in data["labels"]]
    head = "| Label | " + " | ".join(f"{('best-of-8 run' if b == 'best_of_8' else str(b) + ' trial(s)')} (n={data['labels'][b]['eligible']})" for b in budgets) + " |"
    out = [head, "| --- | " + " | ".join("---:" for _ in budgets) + " |"]
    for label in LABELS:
        cells = [f"{data['labels'][b]['counts'][label]} ({100 * data['labels'][b]['counts'][label] / max(1, data['labels'][b]['eligible']):.0f}%)" for b in budgets]
        out.append(f"| {LABEL_TEXT[label]} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def outcomes_markdown(data):
    out = ["| Run | Case type | Episodes | Better than default | Same as default | Worse than default | No answer | Mean reward |",
           "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for run, buckets in data["default_outcomes"].items():
        name = "best-of-8 run (1 trial)" if run == "best_of_8" else f"{run} trial(s)"
        for bucket, label in (("headroom", "headroom above 5% of optimum"), ("low_headroom", "zero or low headroom")):
            b = buckets[bucket]
            if b["episodes"]:
                out.append(f"| {name} | {label} | {b['episodes']} | {b['better']} | {b['same']} | {b['worse']} | "
                           f"{b['no_answer']} | {b['mean_reward']:.2f} |")
    return "\n".join(out)


def examples_markdown(data, budget=4):
    out = []
    for label in LABELS:
        picks = data["examples"].get(budget, {}).get(label) or []
        if not picks:
            picks = data["examples"].get(1, {}).get(label) or []
        out.append(f"**{LABEL_TEXT[label]}**\n")
        if not picks:
            out.append("_No episodes carry this label._\n")
        for lines in picks:
            out.append("```text\n" + "\n".join(lines) + "\n```\n")
    return "\n".join(out)


def load_model(prefix):
    """Runs written as PREFIX_t1, PREFIX_t4, PREFIX_t32 and (optionally) PREFIX_bo8."""
    runs = {int(name[1:]): load_run(f"{prefix}_{name}") for name in ("t1", "t4", "t32")
            if Path(f"{prefix}_{name}/results.json").exists()}
    best = load_run(f"{prefix}_bo8") if Path(f"{prefix}_bo8/results.json").exists() else None
    return dict(sorted(runs.items())), best


def per_model(sections, render):
    """One sub-section per model, with the model's label as a bold heading."""
    return "\n\n".join(f"**{label}**\n\n{render(data, rows)}" for label, (data, rows) in sections.items())


def best_of_markdown(data):
    best = data.get("best_of_8")
    if not best:
        return "_No best-of-8 run._"
    return "\n".join([
        "| | Best of 8 | Mean of 8 |", "| --- | ---: | ---: |",
        f"| All {best['seeds']} seeds, reward (95% CI) | {fmt(best['best_of_n_reward'])} | {fmt(best['mean_of_n_reward'])} |",
        f"| {best['seeds_with_headroom']} seeds with headroom, reward (95% CI) | {fmt(best['headroom_best_of_n_reward'])} | "
        f"{fmt(best['headroom_mean_of_n_reward'])} |",
        "", f"Seeds where some sample is optimal: {best['all_seeds_where_some_sample_is_optimal']} of {best['seeds']} "
        f"({best['headroom_seeds_where_some_sample_is_optimal']} of the {best['seeds_with_headroom']} with headroom). "
        f"Seeds with headroom where some sample beats the default: {best['headroom_seeds_where_some_sample_beats_default']} "
        f"of {best['seeds_with_headroom']}."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", required=True,
                        help="LABEL=PREFIX; reads PREFIX_t1, PREFIX_t4, PREFIX_t32 and PREFIX_bo8 run directories. Repeatable")
    parser.add_argument("--output", required=True, help="path prefix for the .json, .png and .md fragments")
    parser.add_argument("--resamples", type=int, default=10000)
    args = parser.parse_args()
    prefix = Path(args.output)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    everything, sections, points = {}, {}, {}
    for item in args.model:
        label, run_prefix = item.split("=", 1)
        runs, best = load_model(run_prefix)
        data, rows, model_points = build(runs, best, args.resamples)
        everything[label], sections[label], points[label] = data, (data, rows), model_points
    scripted = {label: [p["scripted"]["mean"] for p in pts] for label, pts in points.items()}
    for label, values in scripted.items():  # the same seeds and the same deterministic search for every model
        if any(abs(a - b) > 1e-9 for a, b in zip(values, next(iter(scripted.values())))):
            raise ValueError(f"scripted search differs for {label}: the models were not run on the same seeds")
    chart(str(prefix) + "_reward_vs_trials.png", points)
    Path(str(prefix) + "_data.json").write_text(json.dumps({"models": everything}, indent=2) + "\n", encoding="utf-8")
    Path(str(prefix) + "_tables.md").write_text(
        "<!-- headline -->\n" + per_model(sections, markdown) +
        "\n<!-- labels -->\n" + per_model(sections, lambda d, r: labels_markdown(d)) +
        "\n<!-- paired -->\n" + "\n".join(f"- {label}, {b} trial(s): model minus scripted search = {fmt(d)}"
                                           for label, (data, _) in sections.items()
                                           for b, d in data["paired_difference"].items()) +
        "\n<!-- outcomes -->\n" + per_model(sections, lambda d, r: outcomes_markdown(d)) +
        "\n<!-- bestof8 -->\n" + per_model(sections, lambda d, r: best_of_markdown(d)) +
        "\n<!-- examples -->\n" + per_model(sections, lambda d, r: examples_markdown(d)) + "\n", encoding="utf-8")
    print(json.dumps({label: {"spend": d["spend"], "best_of_8": d.get("best_of_8")} for label, d in everything.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
