from pydantic import BaseModel, Field


class Citation(BaseModel):
    text: str = Field(description="The full citation as it appears in the document")
    case_name: str = Field(description="Name of the case")
    citation: str = Field(description="Case citation (e.g., 334 F. Supp. 2d 1189)")
    proposition: str = Field(description="The legal proposition the citation allegedly supports")


class CitationFinding(BaseModel):
    citation: Citation
    verdict: str = Field(
        description="One of: 'supports', 'does_not_support', 'likely_fabricated', 'could_not_verify', 'error'"
    )
    reasoning: str = Field(description="Explanation of the verdict")
    confidence: float = Field(ge=0, le=1, description="Confidence score for this finding (0-1)")
    confidence_reasoning: str = Field(description="Explanation of the confidence score")


class FactFinding(BaseModel):
    claim: str = Field(description="The claim from the Motion for Summary Judgment")
    claim_source_doc: str = Field(description="Reference to where in MSJ this claim appears")
    contradiction: str = Field(description="The contradicting fact from source documents")
    source_doc: str = Field(description="Which document contains the contradiction (e.g., 'police_report')")
    source_quote: str = Field(description="The actual quote/text supporting the contradiction")
    verdict: str = Field(
        description="One of: 'contradicts', 'consistent', 'could_not_verify', 'error'"
    )
    confidence: float = Field(ge=0, le=1, description="Confidence in this finding")
    confidence_reasoning: str = Field(description="Explanation of the confidence score")


class VerificationReport(BaseModel):
    citations: list[CitationFinding] = Field(description="Findings for each citation in the MSJ")
    facts: list[FactFinding] = Field(description="Cross-document fact consistency findings")
    judicial_memo: str | None = Field(
        default=None, description="One-paragraph summary for a judge (if available)"
    )
    summary: dict = Field(
        description="Quick stats: num_citations, num_issues_found, overall_credibility_score"
    )
