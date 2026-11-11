# Issue #6 — StockMan analysis-quality baseline V1

This is an **independent evaluation harness**, not a trading strategy backtest.
All checked-in fixtures use invented Alpha/Beta/AAA entities and synthetic responses.
No live portfolio, broker credentials, real news bodies or model responses are committed.

## Evaluation contract (before implementation)

The checked-in `datasets/v1.json` has 40 fixed cases:
8 evidence sufficiency, 8 citations, 8 tool routing, 6 failures,
5 freshness and 5 security. Twenty-one positive cases are explicitly opted
in to live-model runs. Negative cases verify that the *scorer rejects broken
recordings*; they are not prompts asking a real model to deliberately fail.

* **Replay:** all 40 fixed synthetic recordings run through production schema
  validation and independent invariant checks. PASS means 40/40 replay
  oracle matches (including intentional failures), **not** model accuracy.
* **Live:** opt-in, genuine Gemini model; fixed synthetic data/tools;
  default up to 5 positive cases, 1 repetition. Suggested automated-invariant
  threshold >=95% on an agreed 20+ case sample and no critical safety
  violations. Run 3 repeats for a useful stability comparison. **No live
  results have been certified by this PR.**
* **Human semantic evaluation:** evidence entailment, explanation quality,
  factual support and financial judgement are **not scored automatically**
  by this harness. A human-reviewed reference-label dataset is a separate
  deliverable before claims of 95% semantic correctness.
* **Observability:** per-case elapsed wall time; daily-research estimated
  costs and tokens when available; otherwise explicitly `null`/`unknown`.
  No unmeasured values are presented as zero.
* **Safety:** no broker connection, no Telegram sender, no actual external
  search service in the injected evaluation tools. Real model requests
  do send the synthetic prompt/data to the selected provider.

## Commands

Install the project's locked dependencies, then:

```sh
python -m unittest tests.test_evals
python -m stockman.evals run --mode replay --output runtime/evals/replay.json
GEMINI_API_KEY=... python -m stockman.evals run --mode live --max-cases 5 --repeats 1 --output runtime/evals/live.json
python -m stockman.evals compare --baseline runtime/evals/old.json --current runtime/evals/new.json
```

The shell invocation with a key is only an example; avoid storing secrets in
shell history and prefer your existing environment-based secret management.
Live execution costs money. There is no direct DeepSeek provider in this V1;
its implementation belongs to Issue #10. Live on-demand model usage may be
unknown until Issue #17 improves provider usage collection.

## Scoring and limitations

Daily research reuses `validate_output` and real `Evidence.identity`.
The scoring rules also check expected counts, evidence IDs, forbidden text,
bounded calls and tool trace sequences. They are intentionally narrower than
semantic correctness. A correct citation string can still support an incorrect
interpretation. A live run marked `passed` is **only an automated invariant
pass**, with `needs_human_review` tracked separately.

For end-to-end evaluation, a fresh temporary data directory and mock services
are injected into the real Supervisor. Prompts/data are fixed; model choices
can vary. Do not use the production PostgreSQL schema or task/session directories.

Current limitations: cross-provider comparisons, semantic labels, live
subsystem costs, full model response metadata, and broad production
trace correlation remain work for Issues #10/#17/#21 and later V1 expansion.
