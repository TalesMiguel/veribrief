from llm import call_llm
from models import CitationFinding, FactFinding


class JudicialMemoAgent:
    def __init__(self):
        self.name = "JudicialMemo"

    def run(
        self,
        citation_findings: list[CitationFinding],
        fact_findings: list[FactFinding],
    ) -> str:
        critical_findings = self._rank_findings(citation_findings, fact_findings)

        if not critical_findings:
            return "No significant issues found in the verification analysis."

        findings_summary = self._format_findings(critical_findings)

        prompt = f"""You are a judicial clerk tasked with summarizing key findings from a legal document verification analysis.

Write a concise, one-paragraph judicial memo summarizing the most critical issues found during the analysis. The memo should be written for a judge reviewing a motion for summary judgment.

Critical Findings:
{findings_summary}

The memo should:
1. Be one paragraph only (3-4 sentences)
2. State the issues clearly and directly
3. Not speculate beyond what the findings show
4. Be neutral and professional
5. Conclude with the significance of these issues for the court's decision

Judicial Memo:
"""

        try:
            memo = call_llm([{"role": "user", "content": prompt}])
            return memo.strip()
        except Exception:
            return "Unable to generate judicial memo."

    def _rank_findings(
        self,
        citation_findings: list[CitationFinding],
        fact_findings: list[FactFinding],
    ) -> list[str]:
        findings = []

        fabricated = [
            f for f in citation_findings if f.verdict == "likely_fabricated" and f.confidence > 0.6
        ]
        for f in fabricated[:2]:
            findings.append(
                f"Likely fabricated citation: {f.citation.case_name} ({f.reasoning})"
            )

        unsupported = [
            f
            for f in citation_findings
            if f.verdict == "does_not_support" and f.confidence > 0.6
        ]
        for f in unsupported[:1]:
            findings.append(
                f"Citation does not support stated proposition: {f.citation.case_name} ({f.reasoning})"
            )

        contradictions = [f for f in fact_findings if f.verdict == "contradicts" and f.confidence > 0.6]
        for f in contradictions[:3]:
            findings.append(f"Fact contradiction: {f.claim} contradicts {f.source_doc}")

        return findings

    def _format_findings(self, findings: list[str]) -> str:
        if not findings:
            return "No critical findings"
        return "\n".join(f"- {f}" for f in findings)
