# VeriBrief — Reflection Document

## Overview

This document reflects on design decisions made during development of the VeriBrief prototype and production readiness plan. It serves both as a record of reasoning and as a guide for future iterations.

---

## Part 1: Choices We Made & Why

### 1.1 Multi-Agent Decomposition

**Choice:** Four distinct agents (CitationExtractor → CitationVerifier → FactConsistency → JudicialMemo)

**Why:** 
- Separation of concerns: each agent has one job, making prompts precise and failures isolatable
- Evals become modular: can test each agent independently
- Extensibility: easy to add agents (e.g., statute-of-limitations checker) without rewriting pipeline

**Alternative and tradeoffs:** Monolithic agent (one prompt does all four jobs)
- Simpler code, but harder to debug; one bad extraction ruins verification

---

### 1.2 Confidence Scoring (Objective vs Interpretive Facts)

**Choice:** Facts get `confidence = 1.0` (verifiable), interpretations get variable confidence

**Why:**
- Legal reasoning requires precision: a date mismatch is certain, PPE usage is interpretive
- Forces agent prompts to distinguish objective from subjective
- Eval harness can be strict (threshold 0.9) without being unfair

**Alternative and tradeoffs:** All findings weighted equally
- Would drown real issues in noise

---

### 1.3 Provider Registry Pattern (Config-Driven)

**Choice:** Single `llm.py` with `PROVIDERS` dict; swap provider via `LLM_PROVIDER` env var

**Why:**
- Zero code duplication (OpenAI SDK works for both Gemini + OpenAI)
- Non-engineers can switch providers (ops team, not code team)
- Enables A/B testing (measure cost/quality of each provider)

**Alternative and tradeoffs:** Separate LLM adapters per provider
- More abstraction, harder to debug; not worth it for 2 providers

---

### 1.4 Fixture-Based Evals (Not LLM-as-Judge)

**Choice:** Hardcoded `KNOWN_FLAWS` matched against pipeline output via keyword search

**Why:**
- Zero API cost (eval harness runs infinitely)
- Deterministic: same output always produces same metrics
- Forces precision in matching logic (no hand-waving)

**Trade-off:** Only measures recall/precision on known issues, not hallucination depth
- Acceptable for prototype; LLM-as-judge deferred to production

**Self-critical update (post-mortem):** this trade-off was more damaging than the
original write-up admits. The eval had **no negative controls** — no set of
citations known in advance to be correct. As a direct consequence, any correct,
non-obvious finding the pipeline raised outside the five originally catalogued
flaws was silently counted as a false positive / hallucination, with no way to
tell it apart from an actual model error. That is a real methodological bug, not
a stylistic simplification: it punishes the pipeline for being more thorough
than the test oracle, and it made the reported precision (38.5%) essentially
uninterpretable. Two concrete things that were wrong, found by rebuilding the
harness:

1. The synthetic brief's footnote string-cite contained **four additional
   fabricated citations** (Torres, Blackwell, Nguyen, Reeves) that were never
   added to `KNOWN_FLAWS` — an oversight in constructing the test oracle, not a
   pipeline failure. Once added as flaw F6, the pipeline caught all four in the
   same run.
2. A **real, accurately-cited precedent** (SeaBright Insurance Co. v. US
   Airways) was present in the brief and never labeled as a negative control.
   Adding it revealed a genuine false positive: the verifier flagged it as
   `does_not_support` with confidence 1.0, which the original protocol could
   not distinguish from a correct-but-uncatalogued finding. This is the actual
   hallucination the original eval should have been able to catch and wasn't
   built to.

A second, unrelated bug turned up in the matching logic itself while fixing
this: keyword matching was originally run against `case_name + reasoning`
combined, and because the whole brief revolves around the Privette doctrine,
the LLM's reasoning for *unrelated* citations often mentioned "Privette" in
passing — silently and incorrectly crediting flaw F1 to findings that had
nothing to do with it. Matching now uses `case_name` alone. This is exactly the
kind of silent, hard-to-notice error that keyword-based fixture evals are
prone to, and it would not have been caught without deliberately trying to
break the harness.

---

### 1.5 Async Job Queue (Celery + RabbitMQ)

**Choice:** Async from day one in production plan (not sync HTTP)

**Why:**
- Document processing is latency-tolerant (30s acceptable for legal work)
- Async = graceful degradation: slow LLM doesn't timeout user
- Enables retry/fallback without user re-uploading

**Alternative and tradeoffs:** Sync HTTP (prototype approach)
- Works for single user, breaks at 100+ concurrent

---

### 1.6 Graceful Degradation Over Fail-Fast

**Choice:** Pipeline continues even if CitationVerifier fails on 3/10 citations

**Why:**
- Partial results are better than no results (lawyer sees 7 verified, 3 failed)
- Matches legal practice: incomplete discovery is still useful
- Reduces frustration (one slow API call doesn't block entire analysis)

**Risk:** Lawyer might miss something
- Mitigated by UI showing which findings are "error" status

---

### 1.7 Multitenancy (Single DB, Not Sharded)

**Choice:** One PostgreSQL with `tenant_id` in every table

**Why:**
- Simpler backups, disaster recovery, ACID guarantees
- the client organization likely <1000 orgs; sharding overhead not justified
- Easier audit compliance (all data in one place)

**Assumption:** Scales to ~100k orgs before hitting limits
- If wrong, sharding is a later migration

---

### 1.8 S3 with Lifecycle (Not Just References)

**Choice:** Store documents in S3 with 90-day auto-delete

**Why:**
- Audit trail: can re-analyze old cases
- CCPA-compliant: user can request deletion, it happens automatically
- Cost negligible (~$2/month for 100 org-cases)

**Alternative and tradeoffs:** Delete immediately after analysis
- Would break compliance audit ("where's the original document?")

---

## Part 2: Questions for You

### 2.1 What Would You Do Differently (If Starting Over)?

**In the prototype:**
- Add unit tests per agent (verify each works in isolation before integration)
- Validate prompts with legal expert before shipping (not just iteration on output)

**In the production plan:**
- Build more robust eval harness: LLM-as-judge instead of just fixtures (more nuanced quality assessment)
- Implement smarter fallback strategy (not just provider switching, but cost/latency optimization)

**Takeaway:** Prototype was right to move fast, but production needs stronger quality gates and expert validation.

---

### 2.2 Tradeoffs Assessment

**Fixture evals vs LLM-as-judge:**
Made reasonable choices for the current stage, but as the product grows, we may need to re-evaluate. For example, LLM-as-judge could provide deeper insights into the quality of analyses, and a smarter provider selection could optimize costs and performance. 

**Config-driven fallback vs smart provider selection:**
Same reasoning applies. Simpler for now, but revisit when there's more data.

**Async queue from day one:**
Essential for scalability, but it also increases operational complexity. However, in this specific case, I consider it fundamental for good product performance, even at small scale.

**Conclusion:** Reasonable choices for this stage, but we should re-evaluate as we grow and get more data.

---

### 2.3 Lessons Learned

**What worked well:**
- LLM output quality was high (legal document analysis is well within LLM capability)
- Multi-agent orchestration was easier than expected (modular design + clear responsibilities = robust)
- Structured JSON outputs from agents worked seamlessly (typed Pydantic models prevented parsing errors)

**What was harder:**
- Confidence scoring required iterative prompt tuning (not a simple heuristic; needed explicit instructions)
- Prompt precision matters enormously (small word changes = big difference in output quality)
- Distinguishing objective facts (1.0 confidence) from interpretive findings (variable confidence) required explicit reasoning in prompts

**Insight:** The bottleneck is prompt engineering, not architecture. Better prompts > better algorithms.

---

### 2.4 Integration with Real Users

**Confidence ratings usage:**
I think lawyers would use confidence ratings as long as they are reliable enough. Detailed explanations of why citations fail would be very useful, especially for justifying decisions in legal cases. 

**Compliance features:**
It would be interesting to implement automatic checks for attorney-client privilege and alerts about possible confidentiality violations.

---

### 2.5 Priority: One More Week

**Better UI, for sure.** Visualizing results in a clear and intuitive way would help lawyers understand the analyses quickly and make informed decisions.

**Continuous eval pipeline with LLM-as-judge** could provide valuable feedback on the quality of analyses in real time, allowing quick adjustments and continuous improvements to the system.

**Smarter provider switching** would also be useful to ensure service reliability, especially during high demand or provider failures.

---

## Part 3: Implementation Notes

### Tier 3: UI & Streaming

Implemented two significant UX improvements:

**Server-Sent Events (SSE) Streaming:**
- `/analyze` endpoint now streams real-time progress instead of returning JSON after completion
- Backend sends event for each agent completion: extraction → verification → fact-checking → summary
- Frontend displays spinner with stage + count details (e.g., "Verifying citations... 11 checked, 3 issues")
- No additional API calls — same 4 LLM calls, just streamed with progress events
- Significantly better UX: user sees work happening, not just "Analyzing..."

**UI Design (Tier 3):**
- Replaced simple JSON display with structured findings interface
- Card-based layout (not table) for better responsiveness
- Inline expandable rows for details (reasoning, source quotes)
- Filters: verdict, confidence (low/medium/high/100%), type, plus asc/desc sort toggle
- Design inspired by minimalist web standards: generous whitespace, clear typography, subtle colors

### Summary

The LLM analyzed the documents accordingly, but the precision of the responses varied depending on the prompt. Confidence scoring was harder to calibrate than expected, requiring fine-tuning in the prompts and evaluation logic. However, multi-agent orchestration was easier than expected to implement, thanks to the modularity of the design and the clarity of each agent's responsibilities.

Architecture has a huge impact, but prompt engineering can work miracles. The right prompt can make a big difference in output quality, usually with minimal architectural changes.

---

## Part 4: What the Corrected Eval Actually Found (Self-Critical)

**Update, see Part 5:** findings 1 and 2 below were later re-examined with an
independent, larger-model judge and both turned out to be mislabeled test-oracle
items, not real system errors. We are leaving this section as originally written,
rather than editing it after the fact, because the retraction is itself the more
interesting result — read this section for what we believed at the time, and Part
5 for what an independent audit found.

Rebuilding the harness with negative controls, a harder uncatalogued defect, and
an explicit abstention check surfaced three gaps worth naming plainly, without
softening them:

1. **The pipeline does hallucinate, at a measurable rate.** With negative
   controls in place, the corrected run showed a real false positive on
   SeaBright — a case that exists, is correctly reported, and does support the
   proposition it was cited for, flagged anyway with full confidence. The
   original "0% hallucination" headline number was never a real measurement of
   this; it was a measurement of "0% of flags fell outside my incomplete list,"
   which is a different and weaker claim.
2. **Abstention is not reliable under real uncertainty.** The one hard,
   genuinely unverifiable citation added to the case (an unpublished trial
   court minute order with no public record) was not met with
   `could_not_verify` — the verifier committed to `likely_fabricated` at 0.9
   confidence instead. The prompt's abstention instruction works for citations
   the model can partially reason about, but breaks down exactly where it
   matters most: citations for which no evidence, real or fabricated, is
   available to the model at all. This is a more honest and more concerning
   finding than anything in the original reflection, and it directly
   contradicts the "smart abstention posture" framing this project was
   originally praised for — abstention worked in the cases that were easy to
   abstain on, and failed in the one case built to be hard.
3. **Recall is not as clean as one run makes it look.** Re-running the pipeline
   (rather than reusing a single cached response) dropped recall from 5/5 to
   5/6 on the very next call, because the misquote (F1) was missed. A
   single-run evaluation, cached and reused across a whole report, overstates
   how stable these numbers are. Any claim about this pipeline's quality should
   be read as a description of one run's behavior, not a stable property of the
   system — this was true of the original results too, it just wasn't stated.

None of this means the architecture is wrong; the four-agent decomposition,
structured outputs, and abstention *option* are still sound design choices. What
was wrong was reporting single-run, oracle-incomplete numbers as if they
characterized the system's reliability. The fix was not a bigger model or a
cleverer prompt — it was admitting the test itself was under-specified.

---

## Part 5: Adding a Judge, and What It Actually Changed

We added a fifth agent (`agents/judge.py`): an independent critic that re-reviews
the verifier's and fact-checker's riskiest, highest-confidence findings against
the source evidence, and can veto a finding down to `could_not_verify` when its
own reasoning doesn't hold up. This is the standard generator/critic (LLM-as-judge)
pattern, and the goal was to see whether a second pass could catch the two
problems flagged in Part 4: the SeaBright false positive and the failure to
abstain on the unpublished-order citation.

**First attempt — same-family judge.** We ran the judge using the same model as
the generator agents (Gemini flash-lite). It upheld both original verdicts
unchanged. Read narrowly, this looks like the critic stage did nothing. Read as a
finding, it's actually informative: a same-family, same-capability judge
reviewing its own sibling's output has no obvious reason to disagree with it —
both instances were trained the same way and are, in effect, being asked to grade
their own homework. This is a known concern in the LLM-as-judge literature (the
evaluator can share the evaluated system's blind spots), and this run reproduced
it directly rather than just citing it.

**Second attempt — larger model, same family.** We then pinned the judge to a
larger Gemini model than the generator, to isolate one variable: is the failure
mode about model *family*, or about model *capability*? The result was the same:
both verdicts upheld, unchanged. This ruled out "just use a bigger model in the
same family" as a fix, at least for this case.

**What the larger judge's reasoning actually said.** Because the judge always
returns its reasoning even when it upholds a verdict, we could read *why* it
agreed, not just *that* it agreed — and this is where the real finding is. For
SeaBright, the judge gave a specific, checkable legal explanation for why the
brief's attributed proposition does not match the case's actual holding: the
citation is real, but *our own synthetic test document* mischaracterized what it
stands for. For the unpublished-order citation, the judge pointed out that the
docket-number format ("BC-2019-33021") doesn't match the real court's numbering
conventions — a legitimate, checkable signal we hadn't considered when we
designed that item to be "impossible to verify."

**The actual result: two test-oracle errors found, not two system errors.** Both
items we had built specifically to expose weaknesses — one negative control, one
hard/unverifiable case — turned out to be mislabeled by us, the test authors, not
mishandled by the pipeline. We retracted both labels in the code
(`RETRACTED_LABELS` in `backend/run_evals.py`) rather than quietly deleting them,
promoted the underlying findings to two new known flaws (F7, F8), and recomputed
recall: 7/8 (87.5%), up from 5/6, entirely from correcting our own labeling
mistakes — not from the system getting anything new right, and not from
re-running until we got a better number.

**Why we didn't just build a new negative control to replace the retracted one.**
We considered it, and decided against it under time pressure: doing it carelessly
would just risk shipping a *third* mislabeled item. The honest state of this
project right now is that we do not have a validated negative control or hard
case, and the harness says so explicitly (`N/A`, not a fabricated number) rather
than hiding the gap.

**The general lesson, stripped of the legal specifics:** building reliable ground
truth for an LLM evaluation is not a one-time setup step you get right by
inspection — it required a second, independent, differently-scaled model, plus a
human actually reading its reasoning, to catch two labeling mistakes that looked
obviously correct when we wrote them. If constructing gold labels this carefully
is this easy to get wrong even for a toy case with five sentences of relevant
context, it should be treated as a first-class part of the system, not an
afterthought bolted onto the README.
