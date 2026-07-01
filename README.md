# VeriBrief

VeriBrief is a multi-agent LLM pipeline that checks legal briefs for two classes of
problems: citations that misrepresent or fabricate case law, and factual claims that
contradict the supporting documents in the case file (police reports, medical records,
witness statements, etc.).

This project started as a personal exploration of multi-agent LLM architectures and
evaluation methodology, and is used as a case study for a graduate paper on
architecture and quantitative evaluation of decision-oriented LLM systems.

## Architecture

Four sequential agents, each a single-purpose LLM call communicating through typed,
structured outputs (no free-text handoffs):

1. **Citation Extractor** — pulls every case citation out of the brief, along with the
   proposition it's alleged to support.
2. **Citation Verifier** — for each citation, judges whether the case plausibly exists,
   whether the quoted text is accurate, and whether it actually supports the stated
   proposition. Returns a verdict plus a confidence score, with an explicit instruction
   to abstain (`could_not_verify`) rather than guess.
3. **Fact Consistency** — cross-checks factual claims in the brief against the other
   case documents, flagging contradictions. Objective facts (dates, names) get
   confidence 1.0; interpretive facts get a variable confidence score.
4. **Synthesis** — summarizes the findings into a short memo.

## Setup

```bash
cp .env.example .env      # add your Gemini or OpenAI API key
docker compose up --build
```

Backend: `http://localhost:8002`. Frontend: `http://localhost:5175`.

## Evaluation

The eval harness runs the pipeline against a synthetic case file with a known set of
injected flaws (fabricated citations, misquoted holdings, factual contradictions) and
**negative controls** (citations and facts known to be correct):

```bash
cd backend && python run_evals.py          # calls the LLM API
cd backend && python run_evals.py --cache   # replays the last cached run, no API calls
```

It reports recall, precision, hallucination rate, and abstention rate on hard cases.
See `docs/reflection.md` for the design rationale and known limitations of both the
pipeline and the evaluation methodology.
