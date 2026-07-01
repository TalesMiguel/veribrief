import json
from llm import call_llm
from models import Citation


class CitationExtractorAgent:
    def __init__(self):
        self.name = "CitationExtractor"

    def run(self, msj_text: str) -> list[Citation]:
        prompt = f"""You are a legal document analyst specializing in extracting case citations.

Extract ALL case citations from the following Motion for Summary Judgment. For each citation:
1. Identify the full citation as it appears
2. Extract the case name
3. Identify the legal proposition or claim that the citation allegedly supports

Return your response as a JSON array with this structure:
[
  {{
    "text": "full text of citation as it appears",
    "case_name": "Name v. Name",
    "citation": "123 F.2d 456 (9th Cir. 1999)",
    "proposition": "The legal claim this citation supports"
  }},
  ...
]

Be thorough. Include every citation you can find, even if it appears in footnotes.

MOTION FOR SUMMARY JUDGMENT:
{msj_text}
"""

        response = call_llm([{"role": "user", "content": prompt}])

        try:
            json_start = response.find("[")
            json_end = response.rfind("]") + 1
            if json_start == -1 or json_end == 0:
                return []

            citations_data = json.loads(response[json_start:json_end])
            return [Citation(**item) for item in citations_data]
        except (json.JSONDecodeError, ValueError, TypeError):
            return []
