import json
from llm import call_llm
from models import CitationFinding, FactFinding


class JudgeAgent:
    """Independent critic stage (LLM-as-judge). Re-reviews the pipeline's own
    risky, high-confidence findings against the source evidence and can veto
    a finding down to a safe `could_not_verify` abstention when its own
    reasoning does not hold up under a second, independent read. This is a
    generator/critic cascade: the judge never invents new findings, it only
    audits and can soften ones already proposed.
    """

    def __init__(self):
        self.name = "Judge"

    def _ask(self, prompt: str) -> dict | None:
        # Deliberately pinned to a larger model in the *same* family as the
        # generator (Gemini flash-lite -> Gemini flash), rather than the
        # generator's own model. This isolates one variable: does a stronger
        # model in the same family avoid the blind spots a same-capability
        # judge shared with the generator, or is the failure mode tied to the
        # model family regardless of scale?
        response = call_llm(
            [{"role": "user", "content": prompt}],
            temperature=0,
            provider="gemini",
            model="gemini-2.5-flash",
        )
        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start == -1 or json_end == 0:
                return None
            return json.loads(response[json_start:json_end])
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    def review_citation(self, finding: CitationFinding) -> CitationFinding:
        if finding.verdict not in ("likely_fabricated", "does_not_support") or finding.confidence < 0.5:
            return finding

        prompt = f"""You are an independent reviewer auditing another AI system's judgment about a legal citation. You did not make the original call and have no stake in defending it.

Citation: {finding.citation.case_name}, {finding.citation.citation}
Proposition it is cited for: "{finding.citation.proposition}"

The other system's verdict: {finding.verdict}
The other system's reasoning: {finding.reasoning}
The other system's stated confidence: {finding.confidence}

Decide whether this verdict is well-grounded enough to act on, or whether it
should be walked back to "could not verify" out of caution. Consider: is the
reasoning specific and falsifiable, or generic? Could this plausibly be a
real, obscure, or foreign precedent the first system simply doesn't
recognize? Is there real risk of penalizing a legitimate citation?

Respond with JSON only:
{{
  "uphold": true or false,
  "judge_reasoning": "why you agree or disagree",
  "judge_confidence": 0.0 to 1.0
}}
"""
        data = self._ask(prompt)
        if data is None:
            return finding

        if not data.get("uphold", True) and float(data.get("judge_confidence", 0)) > 0.5:
            return finding.model_copy(
                update={
                    "verdict": "could_not_verify",
                    "reasoning": f"{finding.reasoning} [vetoed by judge: {data.get('judge_reasoning', '')}]",
                    "confidence": 0.0,
                    "confidence_reasoning": "Overridden by independent judge review",
                }
            )
        return finding

    def review_fact(self, finding: FactFinding, source_documents: dict[str, str]) -> FactFinding:
        if finding.verdict != "contradicts" or finding.confidence < 0.5:
            return finding

        source_text = source_documents.get(finding.source_doc, "")
        prompt = f"""You are an independent reviewer auditing another AI system's claim that two documents contradict each other. Read the evidence yourself before agreeing.

Claim from the main document: "{finding.claim}"
Alleged contradiction: "{finding.contradiction}"
Quoted source text: "{finding.source_quote}"

Full source document ({finding.source_doc}):
{source_text}

Verify, by reading the source document yourself, whether the quoted text
actually appears (or is faithfully paraphrased) in that document and whether
it truly contradicts the claim.

Respond with JSON only:
{{
  "uphold": true or false,
  "judge_reasoning": "why you agree or disagree",
  "judge_confidence": 0.0 to 1.0
}}
"""
        data = self._ask(prompt)
        if data is None:
            return finding

        if not data.get("uphold", True) and float(data.get("judge_confidence", 0)) > 0.5:
            return finding.model_copy(
                update={
                    "verdict": "could_not_verify",
                    "confidence": 0.0,
                    "confidence_reasoning": f"Overridden by independent judge review: {data.get('judge_reasoning', '')}",
                }
            )
        return finding
