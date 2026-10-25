"""One declared input allowance shared by every model in a comparison."""

import math

from .feedback import DEFAULT_CONTEXT_CHARS


def context_policy(models, requested_chars=None, chars_per_token=2.5, overhead_tokens=1024):
    if not models or any(not isinstance(m, dict) or not isinstance(m.get("id"), str) or not m["id"] for m in models):
        raise ValueError("provide model objects with nonempty ids")
    if requested_chars is not None and (type(requested_chars) is not int or requested_chars <= 0):
        raise ValueError("context_chars must be a positive integer")
    if type(chars_per_token) not in (float, int) or not math.isfinite(chars_per_token) or chars_per_token <= 0:
        raise ValueError("chars_per_token must be finite and positive")
    if type(overhead_tokens) is not int or overhead_tokens < 0:
        raise ValueError("overhead_tokens must be a nonnegative integer")
    declared = [model.get("context_window_tokens") for model in models]
    if not any(value is not None for value in declared):
        return {"mode": "character_only_unverified", "context_chars": requested_chars or DEFAULT_CONTEXT_CHARS,
                "token_count_verified": False}
    budgets = []
    for model, window in zip(models, declared):
        output = model.get("max_output_tokens", 4096)
        if type(window) is not int or window <= 0 or type(output) is not int or output <= 0:
            raise ValueError("all compared models need positive integer context_window_tokens and max_output_tokens")
        available = window - output - overhead_tokens
        if available <= 0:
            raise ValueError("model context window has no room after output and overhead reservations")
        budgets.append({"model": model["id"], "context_window_tokens": window,
                        "max_output_tokens": output, "available_input_tokens": available})
    limiting = min(budgets, key=lambda item: item["available_input_tokens"])
    recommended = math.floor(limiting["available_input_tokens"] * chars_per_token)
    chosen = requested_chars if requested_chars is not None else recommended
    if chosen > recommended:
        raise ValueError("requested character cap exceeds the declared smallest-model budget estimate")
    return {"mode": "smallest_declared_window_estimate", "context_chars": chosen,
            "limiting_model": limiting["model"], "estimated_chars_per_token": chars_per_token,
            "provider_overhead_tokens": overhead_tokens, "models": budgets, "token_count_verified": False}
