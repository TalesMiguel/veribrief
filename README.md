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

The eval harness runs the pipeline against a synthetic case file with a known set of
injected flaws (fabricated citations, misquoted holdings, factual contradictions):

```bash
cd backend && python run_evals.py          # calls the LLM API
cd backend && python run_evals.py --cache   # replays the last cached run, no API calls
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
