# Dispatch simulator and controller interface

## The four pieces

| Piece | Implementation | Contract |
| --- | --- | --- |
| Game board | `railway_env/models.py`, `simulator.py` | Validate a complete plan, score it, and replay train positions over time |
| Problem generator | `data.py`, `generator.py` | Reproducible stretch, 120-minute entry window, real daily passenger services, synthetic freight, one or two disruptions |
| Answer key | `solver.py`, `baselines.py`, `benchmark.py` | CP-SAT bound/solution plus passenger-first and first-ready dispatch baselines, all replayed by the same checker |
| Agent interface | `episode.py`, `__main__.py` | Text report, isolated `try_plan`, terminal `submit`, JSON-lines command-line adapter |

## Quick start

Run from the repository directory with the virtual environment from the root
README. A self-contained example is committed under `examples/`; generating a
new one requires the processed CSVs.

```powershell
.venv/Scripts/python.exe -m railway_env benchmark examples/scenario_seed7.json --output runs/example
.venv/Scripts/python.exe -m railway_env play examples/scenario_seed7.json
.venv/Scripts/python.exe -m railway_env demo --seed 7 --output runs/demo
```

`runs/demo/` contains:

- `scenario.json`: complete, portable problem with source and synthetic-data notes.
- `controller.txt`: the initial controller observation, without the answer key.
- `passenger_first_plan.json` and `controller_rule_plan.json`: safe baseline decisions.
- `oracle_plan.json`: incumbent solver plan, when one is found.
- `benchmark.json`: scores, per-train delays, solver status, bound, and optimality gap.

The seed-7 reference has six trains between JSGR and SSN. Its late-train problem
scores 428 weighted delay-minutes for passenger-first, 740 for the controller
rule, and a proven optimum of 351. The scores describe this training model.

## Board and safety rules

Time is an integer number of minutes from the beginning of the window. A train
follows a fixed route through adjacent stations, with fixed running times and
minimum dwell at each station. It may wait outside the entry boundary before
entering; these external queues have unlimited capacity. It cannot stop between
stations, reverse direction, skip stations, or depart before its timetable.

Each adjacent station pair has either one shared resource across both directions
(`block_mode="shared"`) or one resource per direction (`"directional"`). Opposing
trains may traverse simultaneously in directional mode; trains following each
other still cannot share a block. A traversal reserves its resource from
departure until arrival plus the one-minute clearance.
A station visit reserves one measured main or loop from arrival until departure
plus clearance. Both types of berth must be long enough for the whole train.
The extra clearance gives a passing train a nonzero station reservation even
when arrival and departure have the same minute. Reservations are half-open:
`[start, end)`, so a resource ending at minute 6 can be reused at minute 6.

A failed signal closes both directions of a block for a fixed interval. A movement that would still
occupy that block when the closure starts is forbidden. A late-train disruption
changes one train's actual entry release while retaining its nominal timetable.
All disruptions apply together. Overlapping closures on a resource are merged,
and multiple late-entry delays on a train add together exactly once.

The generator's default `--block-mode source` uses directional blocks for source
sections labelled `double`. Single, unknown and mixed triple/double source labels
retain shared blocks: the source label alone cannot locate every change of line
type. `--block-mode shared` or `directional` explicitly overrides this choice.
Old saved scenarios without a block mode retain shared blocks. Both modes use
one measured main per station, with measured loops alongside.
The loader chooses the longest conventional UP/DN main; third-line tracks are
excluded rather than allowing their larger holding lengths to change this model.
This is a conservative dispatch-puzzle abstraction, labelled in the observation
and scenario. It does not implement full signalling/interlocking, station-throat
conflicts, acceleration, braking, variable speeds, or direction-specific access
to tracks. It is not an operational control system.

`RailwaySimulator.evaluate(plan)` independently checks every train visit and
resource reservation. `start(plan)` rejects unsafe plans; `advance(minute)` moves
the clock forward and returns positions, including fractions along a block.
`events(plan)` returns the corresponding arrival/departure trace.

## Generating realistic practice problems

```powershell
.venv/Scripts/python.exe -m railway_env generate --seed 12 --stations 5 --freight 2 --disruption late_train --output runs/problem12.json
.venv/Scripts/python.exe -m railway_env generate --seed 13 --section SBP-KPJG --disruption signal_failure --output runs/problem13.json
```

The loader selects contiguous block-table stations with known main-track holding
lengths and running times in both directions. Missing lengths are excluded;
fractional lengths are rounded down to integer metres. The generator uses
complete, daily, non-suspended through-train paths on the selected stretch.
Other operating-day services and incomplete paths are outside version 1's
service filter. If older processed paths lack the `suspended` flag, rerun:

```powershell
.venv/Scripts/python.exe extract/sambalpur_wtt.py
```

The extractor combines suspension marks across every page for each train/section/
direction and refreshes all of that train's rows. Invalid numeric or malformed CSV
records emit `DataWarning` diagnostics and are excluded locally. Conflicting track
lengths are treated as unknown. Missing required columns stop loading with an
extraction error, rather than inventing replacements. Scenario seeds must be
integers, including when a saved JSON problem is loaded.

Clock times are unwrapped across midnight; unexplained small backward jumps are
rejected. Passenger running/dwell times are their booked timetable differences.
Freight length, release, speed and priority are synthetic. The default freight
mix samples running-time multipliers 1.1/1.4/1.7 and weights 1/3/8 per train;
runtimes are rounded up. `--no-freight-mix` restores uniform multiplier 1.25 and
weight 1. Passenger lengths and weights are also synthetic (weights 10, or 20
for numbers beginning with 12). Defaults are six stations, eight freight trains,
and two disruptions; `--disruptions 1` restores a single event. With two events,
`--disruption` selects the first; the other kind is also generated.

The generator chooses an entry window with no eligible passenger already inside
at the start, avoiding invented initial block positions. It includes all eligible
entries in that window, choosing windows with one to six passenger trains rather
than dropping excess trains. This does not represent every train that ran on the
historical railway: the service filter and source completeness limit the board.

## Objective and answer-key guarantees

For each train, delay is `max(0, actual_final_arrival - scheduled_final_arrival)`.
The objective is the sum of train weight times that delay. Reward for a valid
submission is its negative. Intermediate delays are reported through the event
trace but are not separate terms in the objective. Trains entering during the
two-hour window continue to completion; leaving one unfinished earns no valid
score. Plans must finish within the scenario's conservative serial-clearance
horizon, which is longer than the entry window.

The answer key uses OR-Tools CP-SAT intervals for blocks and optional berth
intervals for each fitting track, with non-overlap and timetable constraints.
See Google's [scheduling model documentation](https://developers.google.com/optimization/scheduling/job_shop)
for the underlying interval constraints. A single search worker and fixed seed
are used. `OPTIMAL` means the optimum was proved for this finite model.
`FEASIBLE` means only an incumbent was found before the time limit; its lower
bound and relative gap remain in the output. `UNKNOWN` or `INFEASIBLE` produces
no answer-key plan. Every solver incumbent is independently replayed and its
score checked before it is returned.

The baselines reserve complete feasible routes with a single-train search over
free reservation windows. Only the earliest feasible arrival in each berth window
is retained; block and destination windows give feasible departures directly,
without enumerating every minute. Passenger-first prioritises all passengers,
then weight and release. The controller rule takes earliest-ready trains, using
weight and shorter running time to break ties. It is a human-inspired heuristic;
it has not been fitted to or validated against human controller decisions.

## What the agent sees and submits

`DispatchEpisode.observe()` returns train readiness/delay, future schedules,
routes, lengths, priorities, track lengths, reference running times, all disruptions,
and the objective. No oracle solution or baseline score is exposed.

The first JSON-lines response to `play` contains this observation and schemas for
`try_plan` and `submit`. Each following input line is a tool request. For example:

```json
{"tool":"try_plan","arguments":{"holds":[{"train_id":"F1","station":"BXQ","until":50}],"priority_order":["P18108-DN","F1"]}}
{"tool":"submit","arguments":{"policy":"passenger_first"}}
```

The station must be on the chosen train's non-terminal route. `until` is a
minimum departure minute, not a claim that a berth is available then. The compiler
finds fitting tracks and safe departures. `priority_order` can list a subset of
trains; remaining trains follow the selected baseline policy. Each successful
trial returns the score, per-train delays, full compiled plan, readable decisions,
and event trace. Trials leave the episode unchanged.
An internal dispatch failure returns an invalid result labelled `internal_error`
so the `play` session can report the problem and continue handling tool requests.

An agent can instead supply a complete schedule as the sole `visits` field:

```json
{"visits":[{"train_id":"P1","station":"A","arrival":0,"departure":2,"track":"main"}]}
```

That fragment is illustrative: a valid complete plan requires exactly one visit
per route station for every train. Track ids must match the scenario (for the
real example they include ids such as `main:3` and `loop:1`). Arrival times must
equal the prior departure plus running time; the checker never repairs an unsafe
explicit schedule. Each final-station departure equals its arrival.

`submit` ends the episode even if invalid. It uses a previously stored
passenger-first reference and answer key for normalized scoring; it never
imports or calls OR-Tools. `play` reads a sibling `reference.json` or an explicit
`--reference` path, verified against the complete scenario hash and replayed.
Without a reference, normalization is `reference_unavailable` and the raw score
remains valid. Internal reference-scoring exceptions produce a terminal
`evaluation_error`; they do not erase the submission or count as agent failures.
`excess_delay_over_optimum` is score minus
proved optimum; `improvement_share` is `(passenger_first - score) /
(passenger_first - optimum)`. Shares may be negative and are not clipped.
No headroom, no optimum proof, or invalid submissions produce a null share and
an explicit `normalization_status`. Trials never expose the answer key.
`--trial-budget N` caps all `try_plan` attempts, including invalid proposals;
submission remains available after the cap. `--no-allow-timetable` restricts
proposals to holds/orders and removes `visits` from both tool schemas. The model
harness defaults to this restricted action space and a 32-call budget.
Invalid terminal submissions get a
reward below every feasible plan's reward, with `weighted_delay: null` and the
validation errors. Additional trial/submission calls then fail. The Python
interface is a planning-tool episode, ready for an agent adapter; this repository
does not yet train an AI or implement a Gymnasium wrapper.

## Verification

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -v
```

Tests include both-direction block collisions, independent berth capacity,
short loops, closures, exact boundary clearance, missing/duplicate visits, time
rollover, suspended services, tool isolation, and a solver optimum checked by
independent enumeration. A separate real-data sweep in `runs/validation/` checks
both disruption types over seeds 0-4; all ten reference cases proved optimality.
