"""Seed-list parsing shared by benchmark and isolated controller clients."""

import re


def parse_seeds(value):
    seeds = []
    for part in value.split(","):
        match = re.fullmatch(r"\s*(-?\d+)(?:-(-?\d+))?\s*", part)
        if not match:
            raise ValueError("seeds must be integers or inclusive ranges, e.g. 0-99 or 1,4-8")
        first = int(match[1])
        last = int(match[2]) if match[2] is not None else first
        if last < first or last - first > 10000:
            raise ValueError("seed ranges must ascend and contain at most 10001 seeds")
        seeds.extend(range(first, last + 1))
    return list(dict.fromkeys(seeds))
