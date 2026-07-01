import json
from llm import call_llm
from models import Citation, CitationFinding


class CitationVerifierAgent:
    def __init__(self):
        self.name = "CitationVerifier"

    def run(self, citations: list[Citation]) -> list[CitationFinding]:
        if not citations:
            return []

        findings = []
        for citation in citations:
            try:
                finding = self._verify_single_citation(citation)
                findings.append(finding)
            except Exception:
                findings.append(
                    CitationFinding(
                        citation=citation,
                        verdict="error",
                        reasoning="Failed to process citation",
                        confidence=0,
                        confidence_reasoning="Processing error occurred",
                    )
                )

        return findings

    def _verify_single_citation(self, citation: Citation) -> CitationFinding:
        prompt = f"""You are a legal research expert tasked with verifying whether cited cases actually exist and support the propositions attributed to them.

Analyze the following case citation:
- Case Name: {citation.case_name}
- Citation: {citation.citation}
- Stated Proposition: "{citation.proposition}"

Based on your knowledge:
1. Does this case appear to exist? (Be honest about uncertainty)
2. Is the quoted text/holding accurate?
3. Does the case actually support the stated proposition?

Respond with a JSON object:
{{
  "verdict": "one of: 'supports', 'does_not_support', 'likely_fabricated', 'could_not_verify'",
  "reasoning": "Explanation of why you reached this verdict",
  "confidence": 0.0 to 1.0,
  "confidence_reasoning": "Why you have this level of confidence"
}}

Be conservative. If you're not certain a case exists, say 'could_not_verify' rather than guessing.
If the citation looks fabricated (unusual format, unknown court, suspicious details), flag it as 'likely_fabricated'.
"""

        response = call_llm([{"role": "user", "content": prompt}])

        try:
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start == -1 or json_end == 0:
                return CitationFinding(
                    citation=citation,
                    verdict="error",
                    reasoning="Could not parse response",
                    confidence=0,
                    confidence_reasoning="Response was not valid JSON",
                )

            data = json.loads(response[json_start:json_end])
            return CitationFinding(
                citation=citation,
                verdict=data.get("verdict", "could_not_verify"),
                reasoning=data.get("reasoning", ""),
                confidence=float(data.get("confidence", 0.5)),
                confidence_reasoning=data.get("confidence_reasoning", ""),
            )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            return CitationFinding(
                citation=citation,
                verdict="error",
                reasoning=f"Failed to parse response: {str(e)}",
                confidence=0,
                confidence_reasoning="JSON parsing error",
            )
