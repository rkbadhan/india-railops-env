# Frozen model-evaluation protocol

> **Public copy.** The held-out problems are published but their answer files are withheld; only their SHA-256 hashes are in `benchmarks/heldout_v1/ANSWER_HASHES.txt`. Statements below that mention stored held-out answers describe the maintainers' private copy. `benchmarks/tuning_v1` is complete, with answers. The held-out set is public in effect, because the generator, data and solver are public and anyone can regenerate and solve it.

## Fixed problems and reference answers

Use `benchmarks/heldout_v1/`, containing the actual 100 scenarios previously
generated for seeds 100–199. The maintainers' private copy also has a stored
answer for each case; the public copy publishes only the scenarios and the
answer hashes. This set is held out because no generator parameters were
selected using its outcomes. Different seed numbers alone do not establish that
independence. Do not regenerate these cases from processed data for model
comparisons.

`benchmarks/tuning_v1/` holds seeds 0–99 and the improved, certified seed-87
answer. Each suite has a manifest with file SHA-256 hashes and the original
processed-input hashes. Every tuning case contains `scenario.json` and
`reference.json`; held-out cases publish `scenario.json` only. References are
bound to the entire canonical scenario, not just its seed or id. Where the
answers are present, loading a suite checks file hashes and replays both
baselines and the oracle plan. Source-data drift is reported by:

```powershell
python -m unittest tests.test_public_benchmarks   # public held-out scenarios and answer hashes
python -m railway_env.suite verify benchmarks/tuning_v1
```

`python -m railway_env.suite verify benchmarks/heldout_v1` also replays the
answers, so it fails in a public checkout with `answers are withheld`. Run it
only where the private `reference.json` files are present.

A changed CSV does not change the frozen problem. A changed frozen scenario or
answer fails verification. Git preserves LF bytes for the frozen JSON files on
every platform. Create a new suite version when replacing cases or answers;
`freeze` refuses to overwrite an existing directory. Future suites can be made
from saved sweeps without solving or generating again:

```powershell
python -m railway_env.suite freeze --source runs/sweep_holdout --output benchmarks/heldout_v2 --seeds 100-199 --provenance docs/validation_2026-10-23.json
```

## Corrected denominators and seed 87

| Measurement | Certified cases | Positive-gap cases | Mean gap, all certified | Mean gap, positive cases only |
| --- | ---: | ---: | ---: | ---: |
| Historical tuning, before longer solve | 99 | 70 | 75.232 | 106.400 |
| Frozen tuning, with longer seed-87 proof | 100 | 71 | 76.570 | 107.845 |
| Frozen held-out | 100 | 78 | 88.890 | 113.962 |

The historical 75-point average included the 29 zero-gap cases. Seed 87's
ten-second answer (1592, lower bound 587) was never eligible for normalized
scores. A one-off solve with a 600-second allowance finished in 11.153 seconds,
proving optimum **1512** with matching lower bound **1512**. This new answer is
stored in the frozen tuning suite; all 200 frozen cases now have proved optima.
This does not mean every answer was proved in ten seconds.

For any future unproved case, the harness emits a separate `unproved_cases`
section with incumbent and lower bound. Such cases never enter optimum-based
averages; reaching an incumbent does not earn an improvement share of one.
The machine-readable follow-up preserves
the old statistics, the new proof, suite hashes and held-out search results.

## Cheap, terminal submissions

`DispatchEpisode(..., reference=...)` accepts a validated stored reference.
The `play` CLI reads sibling `reference.json`, or `--reference FILE`.
`benchmark` and new sweeps write references beside their scenario files.
`submit` performs safety replay and reads those scores. It never imports or
calls the solver and never recompiles a baseline merely for normalization.
Without an answer key, raw score/validity remain available and normalization is
`reference_unavailable`. Internal scoring failures return a terminal
`evaluation_error` while retaining the raw plan score. Harness summaries label
environment errors separately and exclude them from model win-rate denominators.
Malformed or unsafe agent plans remain ordinary invalid results.

## Budget, action space and repeated runs

```powershell
python -m railway_env.model_harness --suite benchmarks/heldout_v1 --models models.json --output runs/model_test --repeats 3 --trials 32
```

Every model/repeat/case starts with a fresh observation and conversation.
`--repeats` accepts 2 or 3 and defaults to 3. `--trials` fixes the same budget for
the model and scripted search. The episode enforces this limit, including bad
`try_plan` proposals. Once exhausted, the model is offered only `submit`.
At most `budget + 2` model responses are accepted; failing to submit is recorded
as a model failure, not silently replaced with a baseline plan. Worker timeouts
are configurable with `--timeout` (seconds per call).

Model feedback uses `compact_v2_ledger`: validity, errors, weighted score, each train's
delay, trials remaining, and one route line per train. A route line contains
`station arrival/departure berth`. Full machine-readable plans, event lists,
reference scores and verbose repeated decision sentences are excluded from the
model conversation. Full results and unmodified worker responses remain in the
saved transcript, beside the exact compact `model_feedback` and context-size
statistics. Worker usage/debug fields are not repeated as assistant messages;
the conversation retains only each requested tool action.

Every `try_plan` attempt also gets a persistent one-line ledger entry containing
the complete proposal and its score (or invalid status). All ledger entries
remain visible after detailed trial exchanges are trimmed. Holds and train
orders are kept exactly; there is no hidden deduplication or proposal repair.

The harness retains the complete board, the entire ledger, a reminder of the
best valid tried proposal/score/train delays, and the newest complete trial
exchanges that fit. It omits older detailed exchanges from model input while
keeping them in the audit. It never silently discards the latest feedback.
If the board, schemas, ledger, best-trial reminder and latest exchange cannot fit,
evaluation stops with a configuration error, excluded from model win-rate
denominators. A large explicit-timetable ledger may require a larger cap; it is
never silently shortened. The feedback version and cap are recorded.

Declare `context_window_tokens` and `max_output_tokens` for every compared model.
The former is the evaluation window and may deliberately be lower than the
provider's actual maximum. One common cap is estimated from the smallest
available input allowance, after reserving output and `--overhead-tokens`
(default 1,024). `--chars-per-token` is an explicit sizing estimate (default
2.5), not a tokenizer. With a 32,768-token evaluation window and 4,096 output
tokens, this yields `(32768 - 4096 - 1024) * 2.5 = 69120` characters for **every**
model. `--context-chars` can select a lower shared cap; a higher one is rejected
under the same estimate. If any model declares a window, all must declare one.
Legacy/synthetic configurations without windows keep the 24,000-character
fallback, labelled `character_only_unverified`.

Characters are not tokens: set the cap for the smallest model's actual tokenizer
and leave room for output/provider overhead. Adapters still need to obey their
provider's token window. A lower cap can be selected with, for example,
`--context-chars 12000`. `--preflight` prints the common policy without inference
or API credentials. The report always sets `token_count_verified: false` for
this character estimate. Report the same policy/cap when comparing models.

In the historical `compact_v1` measurement on all 100 frozen held-out cases,
compact feedback averaged **1,539 characters**, versus **24,349** for full
results (93.7% smaller). Every request fit within 24,000 characters; total
serialized input averaged 667,563 characters per episode versus 15,111,055
with full results and complete history (95.6% smaller). These are measured
characters from synthetic transport, not measured model tokens or billed cost.
The size report records the method and
all cases. A full three-case, three-repeat worker smoke test also completed all
32 trials per episode and nine valid submissions. No real model was called.

The subsequent ledger measurement also covered all 100 frozen cases with 32
rotating/reversing proposals. All final requests retained all 32 ledger entries.
Lines averaged **95.92 characters**; the largest complete ledger was **3,926**
characters. Under the 69,120-character shared cap, the largest request was
**69,102** characters and at most six detailed exchanges were omitted. Mean
cumulative input was **1,214,914 characters** per episode. The larger cap retains
more detail and costs more input than the old 24k policy; do not reuse the old
token/cost estimate. The ledger report records
the exact method, case hashes and measurements. This remains synthetic
transport validation, not model performance or actual token usage.

The default action space permits holds and train orders only. The compiler makes
every successfully compiled proposal safe. Thus unsafe-timetable rate is **not
applicable**, not evidence of learned safety. Badly formed proposals can still
fail and are counted separately. `--allow-timetable` enables full `visits`
submissions for a separate experiment; the reported unsafe metric then counts
invalid explicit-timetable submissions (including validation failures). Record
the action-space flag when comparing models. Scripted search always uses the
compiled action space; allowing a model explicit schedules expands its choices.

## Model worker contract

The included `railway_env.openai_worker` connects to OpenAI-compatible Chat
Completions. It sends one native function-call request per model action and
accepts exactly one available tool call. No automatic retries or alternate
models are used. Full provider responses, returned model id, finish reason,
prompt/completion token usage (including provider cache/reasoning details) are
audited. Cost stays null unless an adapter has a documented charge calculation.
Only compact actions/feedback are repeated in the model conversation.

### Fireworks setup for the selected model

The original 4,096-output-token configuration is `examples/models_fireworks_deepseek.json`, targeting
exactly `accounts/fireworks/models/deepseek-v4p1-flash-1017` at
`https://api.fireworks.ai/inference/v1`. It deliberately uses a **32k evaluation
window**, not a claim that this is the model's maximum. Fireworks' current
[model card](https://fireworks.ai/models/deepseek-ai/deepseek-v4p1-flash-1017)
lists a much larger context and function calling. The generic two-model template
in `examples/models_fireworks.example.json` still needs real ids/windows.

The first pilot exhausted that output allowance during reasoning. The
separate `examples/models_fireworks_deepseek_16k.json` pilot reserves 16,384
output tokens in the same 32,768-token evaluation
window, giving a shared character estimate of 38,400 for inputs. Its worker
HTTP timeout is 180 seconds; give the parent 210 seconds. Apply the same policy
to every model in a comparison. The larger allowance is a change in evaluation
configuration, so preserve the 4k results and use a fresh directory.

One of those default-effort repeats still exhausted its output budget. The
operational pilot profile is `examples/models_fireworks_deepseek_low.json`,
which keeps the 16k output allowance and explicitly adds `--reasoning-effort low`.
Both repeats submitted valid plans.
The worker's optional effort flag preserves provider defaults when omitted;
use explicit settings for reproducible future comparisons. See
the complete audit, including failed phases.

From the repository root in PowerShell:

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
# Edit .env locally and fill OPENAI_API_KEY. Do not paste a key into chat.
.venv/Scripts/Activate.ps1
python -m railway_env.model_harness --models examples/models_fireworks_deepseek_low.json --preflight
python -m railway_env.model_harness --models examples/models_fireworks_deepseek_low.json --suite benchmarks/heldout_v1 --seeds 100 --repeats 2 --trials 32 --timeout 210 --output runs/fireworks_pilot_low
```

The worker loads local `.env` automatically, or `--env-file PATH`. Existing
environment values and explicit command flags take precedence. `.env` and
`.env.*` are ignored; only the blank `.env.example` template is tracked.
`FIREWORKS_API_KEY` is accepted when `OPENAI_API_KEY` is empty. `OPENAI_MODEL` is
only a fallback: a harness model id or explicit `--model` always wins, preventing
one environment default from silently replacing every compared model.
The loader supports literal assignments, optional `export`, quotes and comments;
it never evaluates shell commands or expands variables.

`--seeds` selects an existing frozen subset for a pilot; it never regenerates
cases. After checking the pilot, omit it for the full 100-case suite and use
`--repeats 3` with a new output directory. Set the shared window/output policy
before comparing models, and leave output allowance for both reasoning and
visible action tokens.

Fireworks receives `context_length_exceeded_behavior="error"`, preventing its
default reduction of the requested output allowance. Reported prompt usage is
also checked against the declared evaluation window plus reserved output.
Context/authentication/configuration failures, provider outages and adapter
failures are recorded separately from model mistakes and excluded from model
win-rate denominators. Worker crashes, timeouts and invalid worker JSON are
`adapter_error`; historical `worker_error` records are treated the same when
summarized again. The harness stops after saving the first such
failure rather than issuing the same failing request across the entire suite.
A malformed or multiple model tool call is a model failure, with usage kept.
A completion with `finish_reason="length"` is explicitly labelled
`failure_reason="output_limit"` and executes no action. It remains a resource
failure under that run's fixed output budget, rather than an adapter crash;
do not silently retry with a larger budget inside the same episode.

Implementation references: [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
and [Fireworks Chat Completions](https://docs.fireworks.ai/api-reference/post-chatcompletions).
Local fixture tests exercise the real CLI and HTTP path without live inference.
The separately reported pilots use actual provider calls and locally
configured credentials.

The harness is provider-neutral. `models.json` is an array with distinct ids and
argv commands, for example (replace paths with a real model adapter):

```json
[
  {"id":"large-model-version","command":["python","C:/adapters/model_worker.py","--model","large-model-version"]},
  {"id":"small-model-version","command":["python","C:/adapters/model_worker.py","--model","small-model-version"]}
]
```

For each model action, the harness starts that command without a shell, writes
one UTF-8 JSON request to stdin, and expects one UTF-8 JSON response on stdout.
The included worker reads and writes bytes explicitly, so Windows console and
pipe code pages cannot garble observations or crash on Unicode reasoning.
The request
contains `model_id`, `seed`, `repeat`, `messages`, `tools` (the available schemas),
and `trials_remaining`. The adapter converts these messages/schemas to its
provider's API, calls the selected model, and translates one tool action back.
Reference scores, plans, scenario-file paths and baseline-search results are
never passed to the worker. Adapters must forward only the supplied observation
and feedback to the model; they must not inspect local answer files. This is an
auditable protocol, not an OS sandbox for untrusted worker executables.

Expected response:

```json
{
  "tool":"try_plan",
  "arguments":{"policy":"passenger_first","priority_order":["F2","P1"]},
  "usage":{"input_tokens":1200,"output_tokens":150,"cost_usd":0.003},
  "provider_model":"actual-version-returned-by-provider"
}
```

`tool` must be `try_plan` or `submit`. Token counts are total nonnegative integer
input/output tokens reported by the provider; include cached input in the input
total. Dollar cost is optional and must be the reported charge or a documented
adapter calculation. Missing or invalid usage stays `null`, never zero. Partial
episodes retain any reported usage. No credentials are stored in the config;
the worker obtains them from the user's configured environment or provider
sign-in. The harness does not invent a model result when none is configured.
Its tests use explicitly labelled synthetic workers, not live model evidence.

## Metrics and the search comparator

Results preserve four primary metrics and their denominators:

1. Unsafe/invalid explicit-timetable submission rate when enabled; otherwise
   null with `not_applicable_compiled_actions_only`.
2. Mean excess over a certified optimum among valid, scored submissions.
3. Mean share `(passenger_first - model) / (passenger_first - optimum)` among
   valid, certified cases with positive headroom. Negative values are retained.
4. Passenger-first win rate. Malformed model actions, output-limit failures and
   non-submissions count as failed attempts under the fixed run budget. Worker
   crashes/timeouts, provider/configuration errors and environment failures are
   counted separately and excluded.

Next to them are per-case/mean input tokens, output tokens, optional dollar cost,
wall time and coverage of usage reporting. Per-repeat summaries and win-rate
mean, standard deviation, minimum and maximum show variation across fresh runs;
do not select only the best repeat. Invalid submissions and unproved cases have
no normalized score and their counts remain visible.

The same-budget scripted search is run on the same frozen case and stored in
the output. Every model submission is classified as beating both baselines,
beating passenger-first but tying search, beating passenger-first but losing to
search, tying passenger-first, doing worse, or unscored. Search wins/ties are
also reported directly, including ties on zero-headroom cases.

The verified 32-trial search on `heldout_v1` made 100 valid submissions, beat
passenger-first on **67/100**, reached **59/100** certified optima (including 22
zero-headroom cases), and recovered **71.8654%** of improvement on the 78 cases
with headroom. It is a stronger held-out comparator than the historical tuning
result of 45.19%. The full local transcripts are in
`runs/scripted_search_heldout_v1_verified/`; compact scores and budget checks are
kept privately. Live pilot results and adapter validation are not published here.

## Keeping a future held-out test private

This local repository contains held-out answers in `reference.json` files and
in per-case validation reports; some scores can also be derived from reported
improvement shares. Keep the answer-containing repository and its history
private if future model contamination matters. A public source release should
use a clean export/new history that omits those artifacts, with held-out answers
stored separately in a private suite directory. Adding an ignore rule or
deleting files in a later commit does not remove earlier committed answers.
The harness already accepts an arbitrary private directory through `--suite`.

## Control reachability (2026-10-24)

The agent submits only `holds` and a `priority_order`; `dispatch()` chooses
berths and times. `python -m railway_env.controls_check` converts each stored
certified plan into holds at the solver's departures plus three candidate train
orders, replays them through `dispatch()`, and compares with the optimum. If that
fails it runs a bounded, seeded hill climb over train orders. Results, including
per-attempt scores, are kept privately because they contain held-out answers.

| Suite | Optimum reached | Constructively | By order search | With holds | Priority order only |
| --- | ---: | ---: | ---: | ---: | ---: |
| `heldout_v1` | 100/100 | 99 | 1 | 99 | 88 |
| `tuning_v1` | 100/100 | 100 | 0 | 100 | 80 |

A score of 1.0 is reachable on all 200 frozen cases. Holds matter: ordering
alone reaches the optimum on only 88 and 80 cases. The controls cannot pick a
berth, which is why one held-out case failed the direct translation and needed
a different order, which the search found. Berth choice therefore limits
how easy the optimum is to find, not whether it exists. Reachability here is
shown for the frozen cases, not proved for new scenarios. Loop length is already
enforced: `Scenario` rejects a train that fits no track at a station, and both
`dispatch()` and the solver only assign berths of sufficient length.

## Reward when passenger-first is already best

`improvement_share` stays `null` when headroom is zero (22 held-out, 29 tuning
cases), so it cannot be the training signal. `normalized_reward` is defined for
every certified valid or invalid plan:

`normalized_reward = clip(1 - excess_over_optimum / scale, -0.9, 1)`,
with `scale = max(passenger_first - optimum, 1, ceil(0.05 * optimum))`.

- The certified optimum scores 1.0. Valid plans never score below -0.9 and an
  invalid plan scores -1.0, so staying valid always pays.
- With headroom at least the floor, it equals the improvement share clipped at
  -0.9, so 0 still means passenger-first.
- With no headroom, matching passenger-first scores 1.0 and each extra minute
  costs `1/scale`.
- Headroom below the floor (32 held-out and 25 tuning cases beyond the 22 and
  29 zero cases) uses the floor, so passenger-first scores above 0 there and
  one-minute headroom cannot produce a -199 reward.
- Unproved optima give `null`. The floor fraction is a design choice; revisit
  it before training if too much of the suite is compressed.

The harness reports `mean_normalized_reward` per repeat and the scripted
search's `normalized_reward` per case.

## Keeping answer keys away from the agent

The episode holds the answer key in memory and exposes it only after `submit`.
Model workers run through `model_harness.worker_call`, which now starts them in
an empty scratch directory with the repository on `PYTHONPATH` (and `.env`
values passed through the environment). A worker therefore cannot find
`benchmarks/` by relative path or directory listing. This is not a sandbox: a
worker with file tools can still read an absolute path. For real agents or RL
rollouts, run them as a separate OS user or in a container that does not mount
the repository or suite directory, and pass the evaluator a private `--suite`.
Custom workers should use absolute paths for anything they need, since their
working directory is no longer the repository.

## Scripted-search bar with holds, in `normalized_reward` (2026-10-24)

Because the original search only reorders trains, a model that uses holds could
look better than a script that could also have used them. The search now accepts
holds (`--holds`: delay one train's departure from one station by 5, 10 or 15
minutes past the incumbent plan, lightest trains first). Measured on
`heldout_v1` with the shared trial budget, mean `normalized_reward` over all 100
cases ([data](scripted_baseline_2026-10-24.json)):

| Trials | Passenger-first only | Reorder-only | Holds in last quarter | Holds alternating |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.386 | 0.386 | 0.386 | 0.386 |
| 4 | | 0.490 | 0.462 | 0.462 |
| 32 | | **0.847** | 0.809 | 0.720 |

Reorder-only mean improvement share is 0.719 at 32 trials (0.158 at 4; 0 at 1).
These holds are worse than reordering under a fixed budget: every trial spent on
a hold is one not spent on a reordering, and hold moves rarely help. So the
reorder-only search stays the default bar, and holds are not what makes it
strong. A smarter hold heuristic could raise the bar and has not been tried. The
bar a model must clear is 0.386, 0.490 and 0.847 at 1, 4 and 32 trials; at 1
trial the script is just passenger-first. The harness now accepts a trial budget
of 1 and up. At budget 1 the script submits passenger-first, so a model beating
0.386 there has reasoned out a better plan without any feedback.
