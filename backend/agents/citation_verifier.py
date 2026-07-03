import json
from llm import call_llm
from models import Citation, CitationFinding


class CitationVerifierAgent:
    """Verifies every extracted citation in a single call, the same way
    FactConsistencyAgent already batches all facts into one call. Verifying
    one-citation-per-request was the original implementation but multiplies
    API usage (and rate-limit exposure) by the citation count for no
    accuracy benefit the eval harness could detect — each citation is still
    judged independently in the prompt, on its own merits, just within one
    request instead of N."""

    def __init__(self):
        self.name = "CitationVerifier"

    def run(self, citations: list[Citation]) -> list[CitationFinding]:
        if not citations:
            return []

        try:
            return self._verify_batch(citations)
        except Exception:
            return [
                CitationFinding(
                    citation=citation,
                    verdict="error",
                    reasoning="Failed to process citation",
                    confidence=0,
                    confidence_reasoning="Processing error occurred",
                )
                for citation in citations
            ]

    def _verify_batch(self, citations: list[Citation]) -> list[CitationFinding]:
        citations_block = "\n".join(
            f"""[{i}]
- Case Name: {c.case_name}
- Citation: {c.citation}
- Stated Proposition: "{c.proposition}\""""
            for i, c in enumerate(citations)
        )

        prompt = f"""You are a legal research expert tasked with verifying whether cited cases actually exist and support the propositions attributed to them.

Below is a numbered list of {len(citations)} case citations extracted from a single legal brief. Evaluate EACH ONE independently and on its own merits — do not let your assessment of one citation influence another.

{citations_block}

For each citation, based on your knowledge:
1. Does this case appear to exist? (Be honest about uncertainty)
2. Is the quoted text/holding accurate?
3. Does the case actually support the stated proposition?

Respond with a JSON array of exactly {len(citations)} objects, in the same order as the numbered list above:
[
  {{
    "index": 0,
    "verdict": "one of: 'supports', 'does_not_support', 'likely_fabricated', 'could_not_verify'",
    "reasoning": "Explanation of why you reached this verdict",
    "confidence": 0.0 to 1.0,
    "confidence_reasoning": "Why you have this level of confidence"
  }},
  ...
]

Be conservative. If you're not certain a case exists, say 'could_not_verify' rather than guessing.
If the citation looks fabricated (unusual format, unknown court, suspicious details), flag it as 'likely_fabricated'.
"""

        response = call_llm([{"role": "user", "content": prompt}])

        json_start = response.find("[")
        json_end = response.rfind("]") + 1
        if json_start == -1 or json_end == 0:
            raise ValueError("Could not find a JSON array in the response")

        results_data = json.loads(response[json_start:json_end])
        by_index = {}
        for item in results_data:
            try:
                by_index[int(item["index"])] = item
            except (KeyError, ValueError, TypeError):
                continue

        findings = []
        for i, citation in enumerate(citations):
            data = by_index.get(i)
            if data is None:
                findings.append(
                    CitationFinding(
                        citation=citation,
                        verdict="error",
                        reasoning="Model response did not include this citation's index",
                        confidence=0,
                        confidence_reasoning="Missing from batched response",
                    )
                )
                continue
            try:
                findings.append(
                    CitationFinding(
                        citation=citation,
                        verdict=data.get("verdict", "could_not_verify"),
                        reasoning=data.get("reasoning", ""),
                        confidence=float(data.get("confidence", 0.5)),
                        confidence_reasoning=data.get("confidence_reasoning", ""),
                    )
                )
            except (ValueError, TypeError) as e:
                findings.append(
                    CitationFinding(
                        citation=citation,
                        verdict="error",
                        reasoning=f"Failed to parse response: {str(e)}",
                        confidence=0,
                        confidence_reasoning="JSON parsing error",
                    )
                )

        return findings
