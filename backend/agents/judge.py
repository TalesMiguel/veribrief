import os
import json
from llm import call_llm
from models import CitationFinding, FactFinding

# Default judge model: a larger model in the *same* family as the generator
# (Gemini flash-lite -> Gemini flash), rather than the generator's own model.
# This isolates one variable: does a stronger model in the same family avoid
# the blind spots a same-capability judge shared with the generator, or is
# the failure mode tied to the model family regardless of scale? Overridable
# via JUDGE_MODEL so the same-family-judge condition (judge model == generator
# model) can actually be run and cached, instead of only described in prose.
DEFAULT_JUDGE_MODEL = "gemini-2.5-flash"


class JudgeAgent:
    """Independent critic stage (LLM-as-judge). Re-reviews the pipeline's own
    risky, high-confidence findings against the source evidence and can veto
    a finding down to a safe `could_not_verify` abstention when its own
    reasoning does not hold up under a second, independent read. This is a
    generator/critic cascade: the judge never invents new findings, it only
    audits and can soften ones already proposed.
    """

    def __init__(self, judge_model: str | None = None):
        self.name = "Judge"
        self.judge_model = judge_model or os.getenv("JUDGE_MODEL", DEFAULT_JUDGE_MODEL)

    def _ask_batch(self, prompt: str) -> list[dict] | None:
        response = call_llm(
            [{"role": "user", "content": prompt}],
            temperature=0,
            provider="gemini",
            model=self.judge_model,
        )
        try:
            json_start = response.find("[")
            json_end = response.rfind("]") + 1
            if json_start == -1 or json_end == 0:
                return None
            return json.loads(response[json_start:json_end])
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    @staticmethod
    def _is_risky_citation(finding: CitationFinding) -> bool:
        return finding.verdict in ("likely_fabricated", "does_not_support") and finding.confidence >= 0.5

    @staticmethod
    def _is_risky_fact(finding: FactFinding) -> bool:
        return finding.verdict == "contradicts" and finding.confidence >= 0.5

    def review_citations(self, findings: list[CitationFinding]) -> list[CitationFinding]:
        """Audits every risky citation finding in a single call (instead of
        one call per finding — see the CitationVerifierAgent docstring for
        why this matters for API quota). Findings that aren't risky enough to
        need review are returned unchanged without spending a call on them."""
        risky_indices = [i for i, f in enumerate(findings) if self._is_risky_citation(f)]
        if not risky_indices:
            return findings

        items_block = "\n".join(
            f"""[{idx}]
Citation: {findings[i].citation.case_name}, {findings[i].citation.citation}
Proposition it is cited for: "{findings[i].citation.proposition}"
The other system's verdict: {findings[i].verdict}
The other system's reasoning: {findings[i].reasoning}
The other system's stated confidence: {findings[i].confidence}"""
            for idx, i in enumerate(risky_indices)
        )

        prompt = f"""You are an independent reviewer auditing another AI system's judgment about {len(risky_indices)} legal citations. You did not make the original calls and have no stake in defending them. Review each one independently, on its own merits.

{items_block}

For each item, decide whether the verdict is well-grounded enough to act on, or
whether it should be walked back to "could not verify" out of caution.
Consider: is the reasoning specific and falsifiable, or generic? Could this
plausibly be a real, obscure, or foreign precedent the first system simply
doesn't recognize? Is there real risk of penalizing a legitimate citation?

Respond with a JSON array of exactly {len(risky_indices)} objects, in the same order as the numbered list above:
[
  {{
    "index": 0,
    "uphold": true or false,
    "judge_reasoning": "why you agree or disagree",
    "judge_confidence": 0.0 to 1.0
  }},
  ...
]
"""
        results = self._ask_batch(prompt)
        if results is None:
            return findings

        by_index = {}
        for item in results:
            try:
                by_index[int(item["index"])] = item
            except (KeyError, ValueError, TypeError):
                continue

        reviewed = list(findings)
        for idx, i in enumerate(risky_indices):
            data = by_index.get(idx)
            if data is None:
                continue
            if not data.get("uphold", True) and float(data.get("judge_confidence", 0)) > 0.5:
                reviewed[i] = findings[i].model_copy(
                    update={
                        "verdict": "could_not_verify",
                        "reasoning": f"{findings[i].reasoning} [vetoed by judge: {data.get('judge_reasoning', '')}]",
                        "confidence": 0.0,
                        "confidence_reasoning": "Overridden by independent judge review",
                    }
                )
        return reviewed

    def review_facts(self, findings: list[FactFinding], source_documents: dict[str, str]) -> list[FactFinding]:
        """Audits every risky fact-contradiction finding in a single call.
        See review_citations for why batching replaces one-call-per-finding."""
        risky_indices = [i for i, f in enumerate(findings) if self._is_risky_fact(f)]
        if not risky_indices:
            return findings

        items_block = "\n".join(
            f"""[{idx}]
Claim from the main document: "{findings[i].claim}"
Alleged contradiction: "{findings[i].contradiction}"
Quoted source text: "{findings[i].source_quote}"
Source document ({findings[i].source_doc}):
{source_documents.get(findings[i].source_doc, "")}"""
            for idx, i in enumerate(risky_indices)
        )

        prompt = f"""You are an independent reviewer auditing another AI system's claims that pairs of documents contradict each other. Read the evidence yourself before agreeing with any of them — do not just trust the other system's quote.

{items_block}

For each item, verify by reading the source document yourself whether the
quoted text actually appears (or is faithfully paraphrased) in that document
and whether it truly contradicts the claim.

Respond with a JSON array of exactly {len(risky_indices)} objects, in the same order as the numbered list above:
[
  {{
    "index": 0,
    "uphold": true or false,
    "judge_reasoning": "why you agree or disagree",
    "judge_confidence": 0.0 to 1.0
  }},
  ...
]
"""
        results = self._ask_batch(prompt)
        if results is None:
            return findings

        by_index = {}
        for item in results:
            try:
                by_index[int(item["index"])] = item
            except (KeyError, ValueError, TypeError):
                continue

        reviewed = list(findings)
        for idx, i in enumerate(risky_indices):
            data = by_index.get(idx)
            if data is None:
                continue
            if not data.get("uphold", True) and float(data.get("judge_confidence", 0)) > 0.5:
                reviewed[i] = findings[i].model_copy(
                    update={
                        "verdict": "could_not_verify",
                        "confidence": 0.0,
                        "confidence_reasoning": f"Overridden by independent judge review: {data.get('judge_reasoning', '')}",
                    }
                )
        return reviewed
