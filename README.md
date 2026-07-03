# VeriBrief

VeriBrief is a multi-agent LLM pipeline that checks legal briefs for two classes of
problems: citations that misrepresent or fabricate case law, and factual claims that
contradict the supporting documents in the case file (police reports, medical records,
witness statements, etc.).

This project started as a personal exploration of multi-agent LLM architectures and
evaluation methodology, and is used as a case study for a graduate paper on
architecture and quantitative evaluation of decision-oriented LLM systems.

## Architecture

Five agents, each a single-purpose LLM call communicating through typed, structured
outputs (no free-text handoffs):

1. **Citation Extractor** — pulls every case citation out of the brief, along with the
   proposition it's alleged to support.
2. **Citation Verifier** — for each citation, judges whether the case plausibly exists,
   whether the quoted text is accurate, and whether it actually supports the stated
   proposition. Returns a verdict plus a confidence score, with an explicit instruction
   to abstain (`could_not_verify`) rather than guess.
3. **Fact Consistency** — cross-checks factual claims in the brief against the other
   case documents, flagging contradictions. Objective facts (dates, names) get
   confidence 1.0; interpretive facts get a variable confidence score.
4. **Judge (LLM-as-judge critic)** — independently re-reviews the verifier's and fact
   checker's riskiest, highest-confidence findings against the source evidence, and can
   veto a finding down to `could_not_verify` when its own reasoning doesn't hold up on a
   second read. Pinned to a different/larger model than the generator agents by default,
   so it doesn't share the generator's blind spots. See `docs/reflection.md` for what
   this stage did and didn't catch in practice.
5. **Synthesis** — summarizes the findings into a short memo.

## Setup

```bash
cp .env.example .env      # add your Gemini or OpenAI API key
docker compose up --build
```

Backend: `http://localhost:8002`. Frontend: `http://localhost:5175`.

## Evaluation

The eval harness (`backend/run_evals.py`) runs the pipeline against a synthetic case
file with a known set of injected flaws (fabricated citations, misquoted holdings,
factual contradictions). All commands below assume `cd backend` first.

### Single run

```bash
python run_evals.py          # calls the LLM API once, full pipeline
python run_evals.py --cache  # replays the last cached run, no API calls
```

It reports recall against the known flaws, and separately reports precision,
hallucination rate, and abstention rate on hard cases *only when* the harness actually
has negative controls / hard cases to measure them against — it prints `N/A` rather than
a misleading number otherwise. It also prints a pre-judge vs. post-judge comparison to
show what the critic stage (agent 4) changed, if anything.

The harness currently ships with **no negative controls and no hard cases** — both of
the ones we originally built turned out, on independent audit, to be mislabeled by the
test author rather than mishandled by the pipeline (see `RETRACTED_LABELS` in
`backend/run_evals.py` and `docs/reflection.md`). We chose to leave the registries empty
and documented rather than replace them with a fresh, unaudited guess.

### Historical protocol versions

Replay any of the three successive evaluation protocols (see the paper, Section 5)
against a cached report, with no API calls:

```bash
python run_evals.py --protocol-version 1   # v1: no negative controls (recall 100%, precision 38.5%)
python run_evals.py --protocol-version 2   # v2: + negative control and hard case (pre-audit gabarito)
python run_evals.py --protocol-version 3   # v3: current registries, post-audit
```

### Repeated live runs (variance / model comparison)

Runs the full pipeline N times against the real API and reports mean/std recall.
Generator and judge failures are decoupled, so a judge-side quota error doesn't
discard a successful generator run.

```bash
python run_evals.py --repeat 10 \
  --model gemini-3.1-flash-lite \
  --judge-model gemini-3.1-flash-lite \
  --temperature 0.5 \
  --sleep 15
```

Flags:

- `--repeat N` — number of live pipeline executions.
- `--model` — generator model override (e.g. `gemini-3.1-flash-lite`, `gemini-2.5-pro`).
- `--judge-model` — judge model override (pass the same value as `--model` to run the
  same-family-critic condition).
- `--temperature` — overrides the generator temperature for every LLM call in the run
  (default: each agent's own default, 0 for extraction/verification).
- `--sleep` — seconds between runs, to stay under free-tier rate limits (default: 15).
- `--skip-judge` — skip the judge stage entirely, e.g. when the judge model's daily
  quota is already exhausted and retries would just waste time.

Each run is saved as `outputs/<timestamp>_<label>_run<k>_pre.json` (and `_post.json` if
the judge ran), a `summary_<label>_<timestamp>.json` is written at the end, and every
run appends one line to `outputs/metrics_log.jsonl`.

### Judge replay (isolating the judge-model variable)

Re-applies a critic to already-saved `*_pre.json` outputs without re-running the
generator, so you can compare judge models/temperatures against the exact same
generator outputs without spending generator API quota:

```bash
python run_evals.py --judge-replay "../outputs/*_pre.json" \
  --judge-model gemini-3.5-flash \
  --temperature 0.5 \
  --sleep 5
```

This writes `*_post_judgereplay_<judge_label>.json` next to each matched input and
prints a mean/std summary of post-judge recall across the batch.

### Environment variable overrides

These apply to any command above and are useful for scripting sweeps without editing
code:

| Variable | Effect |
|---|---|
| `GEMINI_MODEL_OVERRIDE` | Overrides the default Gemini model used by the generator agents. |
| `JUDGE_MODEL` | Overrides the default judge model (same effect as `--judge-model`). |
| `LLM_TEMPERATURE_OVERRIDE` | Forces this temperature on every LLM call, generator and judge alike, overriding each agent's own default. |
| `LLM_CALL_DELAY_SECONDS` | Sleeps this many seconds before every individual LLM call (independent of `--sleep`, which only sleeps between full runs). |
| `LLM_PROVIDER` | Selects the provider config (`gemini` by default). |
