from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pathlib import Path
import json
from agents import (
    CitationExtractorAgent,
    CitationVerifierAgent,
    FactConsistencyAgent,
    JudicialMemoAgent,
)
from models import VerificationReport

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5175"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOCUMENTS_DIR = Path(__file__).parent / "documents"


def load_documents() -> dict[str, str]:
    documents = {}
    for file_path in DOCUMENTS_DIR.glob("*.txt"):
        documents[file_path.stem] = file_path.read_text()
    return documents


def run_pipeline(documents: dict[str, str]) -> VerificationReport:
    msj_text = documents.get("motion_for_summary_judgment", "")
    police_report = documents.get("police_report", "")
    medical_records = documents.get("medical_records_excerpt", "")
    witness_statement = documents.get("witness_statement", "")

    extractor = CitationExtractorAgent()
    verifier = CitationVerifierAgent()
    fact_checker = FactConsistencyAgent()
    memo_agent = JudicialMemoAgent()

    citations = extractor.run(msj_text)
    citation_findings = verifier.run(citations)
    fact_findings = fact_checker.run(
        msj_text, police_report, medical_records, witness_statement
    )
    judicial_memo = memo_agent.run(citation_findings, fact_findings)

    num_issues = sum(
        1
        for f in citation_findings
        if f.verdict in ("likely_fabricated", "does_not_support")
        and f.confidence > 0.5
    ) + sum(1 for f in fact_findings if f.verdict == "contradicts" and f.confidence > 0.5)

    avg_confidence = (
        sum(f.confidence for f in citation_findings + fact_findings)
        / (len(citation_findings) + len(fact_findings))
        if (citation_findings or fact_findings)
        else 0
    )

    return VerificationReport(
        citations=citation_findings,
        facts=fact_findings,
        judicial_memo=judicial_memo,
        summary={
            "num_citations_checked": len(citation_findings),
            "num_issues_found": num_issues,
            "overall_confidence": round(avg_confidence, 2),
            "document_pairs_checked": 4,
        },
    )


@app.post("/analyze")
async def analyze():
    def event_generator():
        documents = load_documents()

        yield f"data: {json.dumps({'stage': 'extracting', 'message': 'Extracting citations from MSJ...'})}\n\n"
        extractor = CitationExtractorAgent()
        msj_text = documents.get("motion_for_summary_judgment", "")
        citations = extractor.run(msj_text)
        yield f"data: {json.dumps({'stage': 'citations_extracted', 'count': len(citations), 'message': f'Found {len(citations)} citations'})}\n\n"

        yield f"data: {json.dumps({'stage': 'verifying', 'message': 'Verifying citations...'})}\n\n"
        verifier = CitationVerifierAgent()
        citation_findings = verifier.run(citations)
        problematic = sum(1 for f in citation_findings if f.verdict in ("likely_fabricated", "does_not_support"))
        yield f"data: {json.dumps({'stage': 'citations_verified', 'count': len(citation_findings), 'problematic': problematic, 'message': f'Verified {len(citation_findings)} citations ({problematic} issues found)'})}\n\n"

        yield f"data: {json.dumps({'stage': 'checking_facts', 'message': 'Cross-checking facts with source documents...'})}\n\n"
        fact_checker = FactConsistencyAgent()
        police_report = documents.get("police_report", "")
        medical_records = documents.get("medical_records_excerpt", "")
        witness_statement = documents.get("witness_statement", "")
        fact_findings = fact_checker.run(msj_text, police_report, medical_records, witness_statement)
        yield f"data: {json.dumps({'stage': 'facts_checked', 'count': len(fact_findings), 'message': f'Found {len(fact_findings)} fact contradictions'})}\n\n"

        yield f"data: {json.dumps({'stage': 'summarizing', 'message': 'Generating judicial summary...'})}\n\n"
        memo_agent = JudicialMemoAgent()
        judicial_memo = memo_agent.run(citation_findings, fact_findings)
        yield f"data: {json.dumps({'stage': 'memo_generated', 'message': 'Summary complete'})}\n\n"

        num_issues = sum(
            1
            for f in citation_findings
            if f.verdict in ("likely_fabricated", "does_not_support")
            and f.confidence > 0.5
        ) + sum(1 for f in fact_findings if f.verdict == "contradicts" and f.confidence > 0.5)

        avg_confidence = (
            sum(f.confidence for f in citation_findings + fact_findings)
            / (len(citation_findings) + len(fact_findings))
            if (citation_findings or fact_findings)
            else 0
        )

        report = VerificationReport(
            citations=citation_findings,
            facts=fact_findings,
            judicial_memo=judicial_memo,
            summary={
                "num_citations_checked": len(citation_findings),
                "num_issues_found": num_issues,
                "overall_confidence": round(avg_confidence, 2),
                "document_pairs_checked": 4,
            },
        )

        yield f"data: {json.dumps({'stage': 'complete', 'report': report.model_dump()})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
