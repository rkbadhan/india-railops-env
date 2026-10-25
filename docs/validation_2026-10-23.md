# Difficulty and planning validation — October 23, 2026

This is the historical initial report. [The evaluation protocol](evaluation_protocol.md)
and follow-up measurements correct the
positive-gap denominator, freeze the actual cases, resolve seed 87 to a proved
1512, and add equal-budget search measurements on the held-out files.

All measurements use the local processed Sambalpur data, Python 3.12.9,
OR-Tools 9.15.6755, one solver worker, and fixed generator seeds. The full
machine-readable report, input SHA-256 hashes, configurations and compact
per-case scores are in validation_2026-10-23.json.
Wall times reflect this machine and concurrent local work.

## Profile selection

| Profile | Seeds | Passenger-first optimal | Solver proofs | Limit per case |
| --- | --- | ---: | ---: | ---: |
| Original: 5 stations, 2 uniform freight, 1 event, shared blocks | 0–99 | 64/100 | 100/100 | 3 s |
| 6 stations, 4 uniform freight, 2 events, shared blocks | 0–99 | 28/100 | 100/100 | 10 s |
| 7 stations, 6 uniform freight, 2 events, shared blocks | 0–29 | 1/7 certified | 7/30 | 3 s |
| 6 stations, 4 uniform freight, 2 events, source blocks | 0–29 | 29/30 | 30/30 | 10 s |
| 6 stations, 6 mixed freight (weights 1/2/4), 2 events, source blocks | 0–99 | 44/100 | 100/100 | 10 s |
| 6 stations, 6 mixed freight (weights 1/3/8), 2 events, source blocks | 0–99 | 41/99 certified | 99/100 | 10 s |
| Selected: 6 stations, 8 mixed freight (weights 1/3/8), 2 events, source blocks | 0–99 | 29/99 certified | 99/100 | 10 s |
| Selected profile, held-out seeds (no further tuning) | 100–199 | 22/100 | 100/100 | 10 s |

Mixed freight uses independent per-train runtime multipliers 1.1/1.4/1.7.
Passenger weights remain 10 or 20. These synthetic weights and speeds create
competing decisions; they are not inferred from observed freight operations.
No seed was removed or selected according to its oracle score. Seeds 0–99 were
used for tuning, so the separate held-out sweep is the stronger validation.
Across both final sweeps, passenger-first is optimal on 51/199 certified cases
(25.6%), and 199/200 optima are proved within ten seconds.

The four-freight shared-block profile passed the original difficulty target.
Correct directional blocks removed many artificial conflicts, requiring more
freight and differentiated speeds/priorities to retain useful headroom. Eight
freight trains go beyond the initial suggested 4–6 range; the intermediate
measurements explain that choice. Station berths remain one measured main plus
measured loops, and mixed/unknown source line types retain shared blocks.

## Final sweep

```powershell
python -m railway_env sweep --seeds 0-99 --output runs/sweep_final --time-limit 10
```

```text
Metric                                      | Value
-----------------------------------------------------------------
Problems completed / attempted              | 100 / 100
Solver proven optimal                       | 99 / 100
Errors                                      | 0
Passenger-first optimal (certified cases)   | 29 / 99 (29.3%)
Mean passenger-first excess delay           | 75.232
Largest passenger-first excess delay        | 623.000
Mean solver seconds                         | 0.949
P95 solver seconds                          | 3.055
Maximum solver seconds                      | 10.001
```

Seed 87 is FEASIBLE with incumbent 1592 and lower bound 587; that incumbent is
not called the best answer and is excluded from optimal-rate/gap statistics.
All 100 scenarios and both baselines completed successfully.

## Held-out sweep

```powershell
python -m railway_env sweep --seeds 100-199 --output runs/sweep_holdout --time-limit 10
```

```text
Metric                                      | Value
-----------------------------------------------------------------
Problems completed / attempted              | 100 / 100
Solver proven optimal                       | 100 / 100
Errors                                      | 0
Passenger-first optimal (certified cases)   | 22 / 100 (22.0%)
Mean passenger-first excess delay           | 88.890
Largest passenger-first excess delay        | 433.000
Mean solver seconds                         | 0.937
P95 solver seconds                          | 3.190
Maximum solver seconds                      | 7.393
```

## Direct Codex trial and reproducible tool search

For the direct seed-0 trial, Codex read the public observation, tried the
passenger-first order, then put F8 ahead of F3 because F8 has the same weight
and a faster run. The dry-run score improved from 974 to 782. The submitted
plan was valid; only afterward did the answer key reveal optimum 767.
Excess over optimum is 15 and recovered improvement is 192/207 = 92.75%.
The trial and submission transcript is in `runs/codex_manual/seed0_trials.json`.
A portable board is committed as `examples/scenario_hard_seed0.json`.

Codex then authored a bounded, deterministic train-order search. It starts an
actual `python -m railway_env play` subprocess for every case, compares both
baselines, tries train promotions using only trial feedback, and submits its
best proposal. It neither reads benchmark files nor calls the oracle during
planning. Every observation, request, response, final proposal and score is
recorded in `runs/codex_tool_search/seed_N.json`.

```powershell
python -m railway_env.tool_search --problems runs/sweep_final --seeds 0-99 --trials 32 --output runs/codex_tool_search
```

- Valid submissions: 100/100; execution errors: 0.
- Beats passenger-first: 48/100.
- Reaches a certified optimum: 46 cases, including 29 with no baseline headroom.
- Mean improvement share: 45.19% over 70 certified cases with positive headroom.
- One submission has no proved optimum; its normalized score remains null.

This experiment is Codex-directed tool search, not an independent LLM inference
on every case. It is not a Claude evaluation or a trained policy. The first
manual trial and automated search are exploratory; a separate model evaluation
would still be needed to measure general LLM dispatch skill.

## Live crawl and checks

The live ECoR/NCR/NER re-crawl read 46/57/51 pages versus 39/51/51 before the URL
fix, with unchanged document counts 170/616/582. All queues drained with zero
failed requests; no zone hit 120 pages. The saved catalog remains 1,444 URLs.
Before-files, log and exact comparison are in `runs/crawl_live/`. Functional URL
parameters remain distinct, and known display variants share a canonical key.

All 68 tests pass, including direction-specific conflicts and closures,
overlapping faults, backward-compatible scenario loading, normalized edge cases,
sweep accounting/CLI, and a real play-protocol search session. The warning during
OR-Tools import comes from constant `~False`/`~True` expressions in its
`cp_model.py` (9.15 lines 251/267); it is documented, not suppressed or patched.
The solver now uses `num_workers`.

Generated `runs/` and processed data remain ignored. The tracked JSON report
preserves the measurements without committing the large run transcripts or
source downloads. The initial work was committed first as `94020de` (data fixes)
and `9e97fd0` (environment), as requested.
