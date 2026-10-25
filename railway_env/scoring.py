"""Comparable terminal metrics, defined only against a certified optimum."""

import math

REWARD_FLOOR_FRACTION = 0.05  # headroom below 5% of the optimum is treated as noise
INVALID_REWARD = -1.0
WORST_VALID_REWARD = -0.9  # a valid plan always beats an invalid one


def reward_scale(passenger_first, optimum):
    """Denominator for normalized_reward: the headroom, but never below a floor.

    Passenger-first can equal the optimum, or beat it by only a minute or two, so
    dividing by the raw headroom would be undefined or let one-minute misses
    swing a reward by tens of points. The floor keeps those cases bounded.
    """
    return max(passenger_first - optimum, 1, math.ceil(REWARD_FLOOR_FRACTION * optimum))


def normalized_score(cost, passenger_first, oracle):
    """Terminal metrics.

    `improvement_share` stays undefined when there is no headroom. The training
    target is `normalized_reward = clip(1 - excess / scale, -0.9, 1)`: 1.0 is the
    certified optimum, 0 is `scale` minutes above it, -0.9 is the floor for any
    valid plan. Where
    headroom is at least the floor it equals the clipped improvement share, so
    0 still means passenger-first. If passenger-first is already optimal, matching
    it earns 1.0 and each missed minute costs 1/scale. Invalid plans score -1,
    strictly below every valid plan, so a struggling policy still learns to stay valid.
    """
    result = {"excess_delay_over_optimum": None, "improvement_share": None, "normalized_reward": None,
              "reward_scale": None, "normalization_status": "optimum_not_proven"}
    if not oracle["proven_optimal"]:
        if cost is None:
            result["normalization_status"] = "invalid_plan"
        return result
    optimum = oracle["weighted_delay"]
    headroom = passenger_first - optimum
    if headroom < 0:
        raise RuntimeError("score beats a certified optimum")
    result["reward_scale"] = reward_scale(passenger_first, optimum)
    if cost is None:
        result.update(normalization_status="invalid_plan", normalized_reward=INVALID_REWARD)
        return result
    if cost < optimum:
        raise RuntimeError("score beats a certified optimum")
    excess = cost - optimum
    result["excess_delay_over_optimum"] = excess
    result["improvement_share"] = (passenger_first - cost) / headroom if headroom else None
    result["normalized_reward"] = max(WORST_VALID_REWARD, min(1.0, 1 - excess / result["reward_scale"]))
    result["normalization_status"] = "defined" if headroom else "no_baseline_headroom"
    return result
