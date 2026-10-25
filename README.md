# IndiaRailOpsEnv

A railway dispatch environment for evaluating (and later training) language-model agents as section controllers. Each problem is a two-hour window on a six-station stretch of the Sambalpur division, with real passenger timetables and infrastructure and synthetic freight and disruptions. The agent decides train priorities and holds; a strict safety checker scores the result against a certified optimum.

> **Not affiliated with Indian Railways.** Built from public Indian Railways documents. It uses no Indian Railways name or logo. Scores describe this training model, not real operations.

## The task

- **Board.** A corridor of six adjacent stations. Each station has one main track and several loops, with measured lengths. Blocks between stations are reserved for a running time plus a one-minute clearance.
- **Traffic.** Real passenger services from the Sambalpur working time table, plus eight synthetic freight trains per problem. Passenger weights are 10 and 20, freight weights 1, 3 and 8. A train only fits a track at least as long as itself.
- **Disruptions.** Every problem has one late train and one signal failure that closes a block for an interval. Both are synthetic.
- **Agent actions.** Text observation, then `try_plan` (a free dry run, within a trial budget) and `submit` (ends the episode). A proposal is `policy` (`passenger_first`, the default plan, or `controller_rule`), an optional `priority_order` of train ids and optional `holds` (train waits at a station until minute T). A dispatcher turns this into a full conflict-free timetable. The observation states the default plan's score.
- **Score.** Weighted final-arrival delay, lower is better. `normalized_reward` is 1.0 at the certified optimum, never below −0.9 for a valid plan and −1.0 for an invalid or missing answer. It equals the share of the possible improvement over the default plan when the headroom is large, and is bounded when the default is already optimal. The details are in [docs/evaluation_protocol.md](docs/evaluation_protocol.md).

What the benchmark covers, so you know what a score means:

- **Mixed traffic.** Passenger and freight trains share the track, with weighted delay.
- **Loop-length limits.** Trains are only assigned berths long enough for them, enforced by the checker, the dispatcher and the solver.
- **Line type.** All 200 frozen problems are double-line sections with a separate block per direction. The generator also supports shared single-line blocks (`--block-mode shared`), but none of the frozen problems use it. Third-line tracks are excluded.
- **The agent does not choose berths.** The dispatcher assigns them. The optimum is still reachable with holds and a priority order on all 200 frozen problems (see the protocol).

See also: RailSimEnv in [`bp-high/RLSimEnvs`](https://github.com/bp-high/RLSimEnvs).

## Quickstart

Python 3.11 or newer.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip.exe
.venv/bin/python -m unittest discover -s tests   # offline tests

.venv/bin/python -m railway_env observe examples/scenario_seed7.json
.venv/bin/python -m railway_env play examples/scenario_seed7.json
```

`play` reads JSON lines on standard input:

```json
{"tool":"try_plan","arguments":{"policy":"passenger_first"}}
{"tool":"submit","arguments":{"policy":"passenger_first"}}
```

The processed data tables are included in `rail_data/processed/`, so nothing needs downloading to generate problems or run the benchmark. To rebuild the tables from the source documents, see [SOURCES.md](SOURCES.md) and [rail_data/README.md](rail_data/README.md); the raw PDFs are not redistributed (`python fetch_rail_data.py` recreates them, several hundred megabytes).

Run a model on the tuning problems with any OpenAI-compatible provider (Fireworks shown). This makes paid API calls. Copy `.env.example` to `.env` and fill the key locally; never commit it.

```bash
python -m railway_env.model_harness --models examples/models_fireworks_gptoss120b.json \
  --suite benchmarks/tuning_v1 --seeds 0-9 --repeats 1 --trials 4 --max-output --workers 4 --max-cost 1 \
  --output runs/try_it
```

The harness caps spend (`--max-cost`), retries provider errors, can resume (`--resume`) and records the environment version in every result file.

## Results

Two small models and a scripted search on the 100 held-out problems, environment v3, reasoning effort `low` for both models. Mean `normalized_reward`, where a missing answer counts as −1 and the solver scores 1.0. Brackets are 95% bootstrap intervals over seeds.

| Controller | 1 trial | 4 trials | 32 trials (20 problems) |
| --- | ---: | ---: | ---: |
| Solver (certified optimum) | 1.00 | 1.00 | 1.00 |
| Scripted search, same budget | 0.39 [0.30, 0.47] | 0.49 [0.41, 0.57] | 0.90 [0.78, 0.98] |
| GLM-5.3 Flash | 0.30 [0.22, 0.39] | 0.42 [0.35, 0.50] | 0.59 [0.37, 0.78] |
| gpt-oss-120b | 0.18 [0.09, 0.28] | 0.23 [0.14, 0.32] | 0.19 [−0.07, 0.45] |

The 1- and 4-trial columns use all 100 problems with 3 repeats each; the 32-trial column uses 20 problems with 2 repeats, so it is not directly comparable to the other columns. The 0.90 scripted-search result in that column is the seeds 100–119 slice; on all 100 held-out cases the reorder-only script scores 0.847 at 32 trials. At 1 trial the scripted search is the default plan. The reported scripted search only reorders trains; the `--holds` variants score worse at the same budget. The models reach the certified optimum in 18–29% of 1- and 4-trial episodes, and both make an already-good default plan worse in 4–22% of episodes. Neither model is a frontier model, so this shows these two small models falling short, not that all models do. Full tables, failure labels, a best-of-8 analysis and caveats are in [docs/gap_eval_2026-10-25.md](docs/gap_eval_2026-10-25.md).

## How held-out scoring works

- `benchmarks/tuning_v1` (seeds 0-99) is complete, with certified answers. Build and tune against it.
- `benchmarks/heldout_v1` (seeds 100-199) publishes the **problems only**. The answer files are not in this repository.
- **The held-out set is public in effect.** The generator, the data tables and the solver are all public, and the seeds are documented, so anyone can regenerate these problems and solve them. We checked this on a sample: the regenerated problems were identical to the published ones. Treat `heldout_v1` as a public test set. For a test that nobody can train on, generate new problems with seeds you never publish.
- [`benchmarks/heldout_v1/ANSWER_HASHES.txt`](benchmarks/heldout_v1/ANSWER_HASHES.txt) lists the SHA-256 of each answer file kept by the maintainers. It shows that the answers behind a reported score were fixed in advance. It does not stop anyone from computing them.
- The held-out numbers above were produced with the maintainers' answer files. Without them you can still run a controller on a held-out problem (`python -m railway_env play ...`) and get validity and raw weighted delay, but no normalized score. Do not tune against the held-out problems: a held-out score only means something if the controller was built without them, and nothing here can enforce that.
- The frozen files are the benchmark. Do not regenerate problems from seeds; the processed data could change.

## Limitations

- **Synthetic freight and disruptions.** Freight speeds, lengths and weights are chosen to create hard problems and are not measured from operations. Every problem has exactly one late passenger train and one signal closure.
- **Public test set.** The held-out problems can be regenerated from public code and data, so a published score cannot rule out contamination.
- **One region.** Problems come from three sections of the Sambalpur division, as 26 six-station windows. 98 of the 100 held-out windows also appear in the tuning set, so the held-out set tests new seeds, trains and disruptions on familiar track, not a different line.
- **Double-line only in the frozen suites,** as above.
- **Deterministic times.** Running times are fixed; there is no stochastic delay beyond the one late train.
- **Low-signal problems.** About half the held-out problems have little or no headroom over the default plan. They are useful for checking that a controller leaves good plans alone, and are best down-weighted for training.
- **Interface.** The agent chooses priorities and holds, not berths.
- **Results are early.** Two small models at low reasoning effort, with wide intervals at 32 trials.

## Repository layout

| Path | Contents |
| --- | --- |
| `railway_env/` | simulator, dispatcher, solver, generator, episode interface, model harness, metrics and report code |
| `benchmarks/` | frozen problem suites, answers for the tuning suite, hashes for the held-out answers |
| `rail_data/` | provenance guide, download manifests and the processed data tables |
| `extract/`, `fetch_*.py`, `find_more_sources.py` | download and table-extraction scripts |
| `docs/` | simulator guide, evaluation protocol, results report |
| `examples/` | a sample problem and model configurations |
| `tests/` | offline unit tests |

## Licence and attribution

Code: [Apache-2.0](LICENSE). Data: the processed tables hold facts extracted from public documents listed in [SOURCES.md](SOURCES.md), whose publishers' terms apply to the originals. `rail_data/processed/osm_stations.csv` is derived from OpenStreetMap data, (c) OpenStreetMap contributors, ODbL; see [NOTICE](NOTICE).
