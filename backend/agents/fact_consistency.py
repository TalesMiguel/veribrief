import json
from llm import call_llm
from models import FactFinding


class FactConsistencyAgent:
    def __init__(self):
        self.name = "FactConsistency"

    def run(
        self,
        msj_text: str,
        police_report: str,
        medical_records: str,
        witness_statement: str,
    ) -> list[FactFinding]:
        prompt = f"""You are a legal analyst tasked with identifying factual inconsistencies between a Motion for Summary Judgment and supporting documents.

Compare the facts stated in the MSJ against the police report, medical records, and witness statement. Identify any contradictions where:
- The MSJ claims one fact
- The source documents state a different fact
- The contradiction is material and clear

For each contradiction found, return a JSON array with this structure:
[
  {{
    "claim": "The exact claim from the MSJ",
    "claim_source_doc": "Reference to where in MSJ (e.g., 'Paragraph 4')",
    "contradiction": "What the source documents actually say",
    "source_doc": "Which document: 'police_report', 'medical_records', or 'witness_statement'",
    "source_quote": "The exact quote supporting the contradiction",
    "verdict": "contradicts",
    "confidence": 0.0 to 1.0,
    "confidence_reasoning": "Why you're confident this is a real contradiction"
  }},
  ...
]

IMPORTANT CONFIDENCE RULES:
- Objective facts (dates, names, locations, numbers that are verifiable in documents): confidence = 1.0
- Interpretive facts (behavior, intent, claims based on testimony or inference): confidence reflects your uncertainty (0.0 to 1.0)
- Only flag clear, verifiable inconsistencies. Vague or ambiguous facts do not count.

If you find NO clear contradictions, return an empty array [].

MOTION FOR SUMMARY JUDGMENT:
{msj_text}

POLICE REPORT:
{police_report}

MEDICAL RECORDS:
{medical_records}

WITNESS STATEMENT:
{witness_statement}
"""

        response = call_llm([{"role": "user", "content": prompt}], temperature=0.1)

        try:
            json_start = response.find("[")
            json_end = response.rfind("]") + 1
            if json_start == -1 or json_end == 0:
                return []

            findings_data = json.loads(response[json_start:json_end])
            findings = []
            for item in findings_data:
                try:
                    findings.append(FactFinding(**item))
                except (ValueError, TypeError):
                    continue
            return findings
        except (json.JSONDecodeError, ValueError, TypeError):
            return []
