# Frozen dispatch suites

- `tuning_v1`: 100 problems (seeds 0-99) with certified optimal answers
  (`reference.json`). Use it to build and tune a controller. It is not a held-out
  test.
- `heldout_v1`: 100 problems (seeds 100-199) used for the reported results. The
  **problems are published; the answer files are not included.** `ANSWER_HASHES.txt`
  lists the SHA-256 of each `reference.json` the maintainers hold, in `sha256sum`
  format. If the maintainers share the answers, anyone can check them:

  ```bash
  cd benchmarks/heldout_v1 && sha256sum -c ANSWER_HASHES.txt
  ```

No generator choice was tuned on `heldout_v1` outcomes. Do not tune on it either:
a held-out score is only meaningful if the controller was built without it.

**This set is public in effect.** The generator, the data tables, the solver and the
seeds are all public, so anyone can regenerate these problems and compute their
optima; a sample check gave identical problems. The hashes show that the answers
were fixed before scores were reported, not that nobody could compute them. For
a test nobody can train on, generate new problems with seeds you never publish.

Each `manifest.json` records the SHA-256 of every scenario file (and, for the
tuning suite, every answer file). Verify the published problems with:

```bash
python -m unittest tests.test_public_benchmarks   # run from the repository root
python -m railway_env.suite verify benchmarks/tuning_v1
```

`python -m railway_env.suite verify benchmarks/heldout_v1` needs the answers and
fails with a clear message when they are withheld. Running a model on a held-out
problem without the answer files still works through `python -m railway_env play`;
you then get the raw weighted delay and validity but no normalized score. Do not
regenerate the problems from seeds: the frozen files are the benchmark.
