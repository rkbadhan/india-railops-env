"""Small controller feedback and bounded model context; full audit stays on disk."""

import json
from collections import defaultdict


FEEDBACK_VERSION = "compact_v2_ledger"
DEFAULT_CONTEXT_CHARS = 24000


def compact_feedback(result):
    """Allowlist trial information; never forward plans, events or answer keys."""
    keys = ("valid", "errors", "error_type", "weighted_delay", "train_delays", "total_delay", "max_delay")
    feedback = {key: result[key] for key in keys if key in result}
    if result.get("valid") and result.get("plan"):
        trains = defaultdict(list)
        for visit in result["plan"]["visits"]:
            trains[visit["train_id"]].append(visit)
        # One route line per train. Keep the berth and arrival/departure minutes
        # useful for judging meets and holds without verbose event duplication.
        lines = []
        for identifier, visits in trains.items():
            lines.append(identifier + ": " + " -> ".join(
                f"{v['station']} {v['arrival']}/{v['departure']} {v['track']}"
                for v in sorted(visits, key=lambda v: v["arrival"])))
        feedback["decisions"] = lines
    return feedback


def trial_ledger_line(number, arguments, result):
    """Keep the entire proposed action and raw outcome on one physical line."""
    proposal = json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    outcome = f"score={result['weighted_delay']}" if result.get("valid") else "invalid"
    if result.get("error_type"):
        outcome += ":" + str(result["error_type"])
    return f"{number} {outcome} {proposal}"


def bounded_request(metadata, messages, tools, best_trial, limit, trial_ledger=()):
    """Retain the board, incumbent and newest whole exchanges within a char cap.

    This is a serialized-input limit, not a tokenizer-specific context guarantee.
    An adapter must still choose a suitable cap for its model's actual tokenizer.
    """
    history = messages[2:]
    memory = ([{"role": "user", "content": "Best valid trial so far (from your own tool feedback): " +
                json.dumps(best_trial, separators=(",", ":"))}] if best_trial is not None else [])
    if trial_ledger:
        memory.append({"role": "user", "content": "All attempted trials (complete proposals and outcomes; do not repeat unintentionally):\n" +
                       "\n".join(trial_ledger)})
    omitted = 0
    while True:
        request = {**metadata, "messages": messages[:2] + memory + history, "tools": tools}
        size = len(json.dumps(request, ensure_ascii=False))
        if size <= limit:
            return request, {"request_chars": size, "omitted_exchanges": omitted,
                             "ledger_trials": len(trial_ledger), "ledger_chars": len("\n".join(trial_ledger))}
        if len(history) <= 2:
            raise ValueError("context cap cannot fit the board, tool schemas, full trial ledger, best-trial memory and latest feedback; increase --context-chars")
        history = history[2:]
        omitted += 1
