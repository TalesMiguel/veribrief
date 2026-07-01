import sys
from pathlib import Path
from main import load_documents, run_pipeline

# Known injected flaws the pipeline is expected to catch.
KNOWN_FLAWS = {
    "F1": {
        "type": "misquote",
        "keywords": ["privette"],
        "description": "Privette v. Superior Court misquoted with 'never' — real holding narrower",
    },
    "F2": {
        "type": "fabricated_case",
        "keywords": ["whitmore", "delgado", "334 f. supp. 2d 1189"],
        "description": "Whitmore v. Delgado Scaffolding Co. — likely fabricated case",
    },
    "F3": {
        "type": "fabricated_cases",
        "keywords": ["kellerman", "pacific coast", "dixon", "okafor"],
        "description": "Kellerman, Dixon, Okafor citations — likely fabricated",
    },
    "F4": {
        "type": "fact_contradiction",
        "keywords": ["ppe", "not wearing", "hard hat"],
        "description": "MSJ claims Rivera was NOT wearing PPE; police report says he WAS",
    },
    "F5": {
        "type": "date_contradiction",
        "keywords": ["march 14", "march 12"],
        "description": "MSJ says March 14, 2021; documents say March 12, 2021",
    },
    "F6": {
        "type": "fabricated_cases_footnote",
        "keywords": [
            "torres", "granite falls",
            "blackwell", "sunrise contractors",
            "nguyen", "allied pacific",
            "reeves", "summit engineering",
        ],
        "description": (
            "Torres, Blackwell, Nguyen, Reeves citations buried in the footnote "
            "string cite — likely fabricated, but omitted from the original test "
            "oracle (the 'harder defect' identified by external review)"
        ),
    },
}

# Negative controls: citations that are real and accurately used. A finding
# that flags one of these as fabricated/unsupported is a genuine false
# positive, independent of whether it also happens to be a "known flaw".
KNOWN_CORRECT = {
    "C1": {
        "keywords": ["seabright", "us airways"],
        "description": (
            "SeaBright Insurance Co. v. US Airways, Inc. — real precedent, "
            "accurately cited as supporting the due-care presumption from "
            "regulatory compliance"
        ),
    },
}

# Hard cases: items the pipeline cannot possibly verify from parametric
# knowledge alone. The ideal behavior is to abstain ("could_not_verify"),
# not to commit to a confident verdict either way.
HARD_CASES = {
    "H1": {
        "keywords": ["cornerstone grading", "bc-2019-33021"],
        "description": (
            "Unpublished trial-court minute order — no public record exists to "
            "confirm or deny it; correct behavior is to abstain rather than guess"
        ),
    },
}


def normalize_text(text: str) -> str:
    return text.lower().strip()


def matches_keywords(text: str, keywords: list[str]) -> bool:
    normalized = normalize_text(text)
    return any(kw.lower() in normalized for kw in keywords)


def find_match(combined: str, registry: dict) -> str | None:
    for item_id, info in registry.items():
        if matches_keywords(combined, info["keywords"]):
            return item_id
    return None


def evaluate_pipeline(report_dict: dict) -> dict:
    report = report_dict["report"]

    matched_flaws = set()
    matched_correct_as_fp = set()
    unlabeled_flags = []
    abstained_hard = set()
    committed_hard = set()

    # legacy (biased) counters, kept for before/after comparison
    legacy_total_flags = 0
    legacy_false_positives = 0

    for finding in report.get("citations", []):
        case_name = finding["citation"]["case_name"]
        verdict = finding["verdict"]
        confidence = finding["confidence"]
        # Match on case_name only. Matching against `reasoning` free text was
        # tried initially and caused false cross-matches: since the whole
        # brief revolves around the Privette doctrine, the LLM's reasoning
        # for *other* citations often mentions "Privette" in passing, which
        # incorrectly credited F1 for unrelated findings.
        combined = case_name

        for hard_id, info in HARD_CASES.items():
            if matches_keywords(case_name, info["keywords"]):
                if verdict == "could_not_verify":
                    abstained_hard.add(hard_id)
                else:
                    committed_hard.add(hard_id)

        if verdict in ("likely_fabricated", "does_not_support") and confidence > 0.5:
            legacy_total_flags += 1

            flaw_id = find_match(combined, KNOWN_FLAWS)
            if flaw_id:
                matched_flaws.add(flaw_id)
                continue

            correct_id = find_match(combined, KNOWN_CORRECT)
            if correct_id:
                matched_correct_as_fp.add(correct_id)
                if confidence > 0.9:
                    legacy_false_positives += 1
            else:
                unlabeled_flags.append(
                    {"case_name": case_name, "verdict": verdict, "confidence": confidence}
                )
                if confidence > 0.9:
                    legacy_false_positives += 1

    for finding in report.get("facts", []):
        if finding["verdict"] == "contradicts" and finding["confidence"] > 0.5:
            legacy_total_flags += 1
            combined = f"{finding['claim']} {finding['source_quote']}"

            flaw_id = None
            for fid, info in KNOWN_FLAWS.items():
                if info["type"] in ("fact_contradiction", "date_contradiction") and matches_keywords(
                    combined, info["keywords"]
                ):
                    flaw_id = fid
                    break

            if flaw_id:
                matched_flaws.add(flaw_id)
            else:
                unlabeled_flags.append(
                    {
                        "claim": finding["claim"][:80],
                        "verdict": finding["verdict"],
                        "confidence": finding["confidence"],
                    }
                )
                if finding["confidence"] > 0.9:
                    legacy_false_positives += 1

    true_positives = len(matched_flaws)
    false_positives = len(matched_correct_as_fp)
    labeled_total = true_positives + false_positives

    recall = len(matched_flaws) / len(KNOWN_FLAWS) if KNOWN_FLAWS else 0
    precision = true_positives / labeled_total if labeled_total > 0 else 0
    hallucination_rate = false_positives / labeled_total if labeled_total > 0 else 0
    abstention_rate = len(abstained_hard) / len(HARD_CASES) if HARD_CASES else 0

    legacy_precision = true_positives / legacy_total_flags if legacy_total_flags > 0 else 0
    legacy_hallucination_rate = (
        legacy_false_positives / max(legacy_total_flags, 1)
    )

    return {
        "matched_flaws": sorted(matched_flaws),
        "false_positives_on_negative_controls": sorted(matched_correct_as_fp),
        "unlabeled_flags": unlabeled_flags,
        "abstained_hard_cases": sorted(abstained_hard),
        "committed_hard_cases": sorted(committed_hard),
        "recall": recall,
        "precision": precision,
        "hallucination_rate": hallucination_rate,
        "abstention_rate": abstention_rate,
        "legacy_total_flags": legacy_total_flags,
        "legacy_precision": legacy_precision,
        "legacy_hallucination_rate": legacy_hallucination_rate,
    }


def main():
    import json
    import argparse

    parser = argparse.ArgumentParser(description="VeriBrief Evaluation Harness")
    parser.add_argument(
        "--cache",
        action="store_true",
        help="Use cached pipeline output (mock_api_call.json) instead of calling API",
    )
    args = parser.parse_args()

    print("\n" + "=" * 80)
    print("VERIBRIEF EVALUATION HARNESS")
    print("=" * 80 + "\n")

    cached_result_file = Path(__file__).parent / "mock_api_call.json"

    if args.cache:
        if not cached_result_file.exists():
            print(f"ERROR: --cache flag used but {cached_result_file.name} not found")
            sys.exit(1)
        print(f"Loading cached pipeline output from {cached_result_file.name}...")
        try:
            with open(cached_result_file) as f:
                report = json.load(f)
            print("Loaded from cache (no API calls made)")
        except Exception as e:
            print(f"ERROR: Failed to load cache: {e}")
            sys.exit(1)
    else:
        print("Running pipeline (this will use API quota)...")
        documents = load_documents()
        print(f"Loaded {len(documents)} documents:")
        for doc_name in sorted(documents.keys()):
            print(f"  - {doc_name}")

        print("\nRunning pipeline...")
        try:
            report = {"report": run_pipeline(documents).model_dump()}
            with open(cached_result_file, "w") as f:
                json.dump(report, f, indent=2)
            print(f"Cached result to {cached_result_file.name}")
        except Exception as e:
            print(f"ERROR: Pipeline failed: {e}")
            import traceback

            traceback.print_exc()
            sys.exit(1)

    print("Evaluating results...\n")
    metrics = evaluate_pipeline(report)

    print("KNOWN FLAWS:")
    for flaw_id, flaw_info in KNOWN_FLAWS.items():
        status = "DETECTED" if flaw_id in metrics["matched_flaws"] else "MISSED"
        print(f"  {flaw_id}: {flaw_info['description']}")
        print(f"       [{status}]\n")

    print("NEGATIVE CONTROLS (known-correct citations):")
    for correct_id, info in KNOWN_CORRECT.items():
        status = (
            "FALSE POSITIVE"
            if correct_id in metrics["false_positives_on_negative_controls"]
            else "correctly left unflagged"
        )
        print(f"  {correct_id}: {info['description']}")
        print(f"       [{status}]\n")

    print("HARD CASES (unverifiable — abstention expected):")
    for hard_id, info in HARD_CASES.items():
        status = (
            "ABSTAINED (could_not_verify)"
            if hard_id in metrics["abstained_hard_cases"]
            else "COMMITTED TO A VERDICT (risk of overconfidence)"
        )
        print(f"  {hard_id}: {info['description']}")
        print(f"       [{status}]\n")

    if metrics["unlabeled_flags"]:
        print("UNLABELED FLAGS (not counted for/against precision — needs human review):")
        for item in metrics["unlabeled_flags"]:
            print(f"  - {item}")
        print()

    print("=" * 80)
    print("CORRECTED METRICS (with negative controls)")
    print("=" * 80)
    print(
        f"Recall (known flaws detected):        {metrics['recall']:.1%} "
        f"({len(metrics['matched_flaws'])}/{len(KNOWN_FLAWS)})"
    )
    print(f"Precision (over labeled universe):    {metrics['precision']:.1%}")
    print(f"Hallucination rate (over labeled universe): {metrics['hallucination_rate']:.1%}")
    print(
        f"Abstention rate on hard cases:         {metrics['abstention_rate']:.1%} "
        f"({len(metrics['abstained_hard_cases'])}/{len(HARD_CASES)})"
    )
    print(f"Unlabeled flags (pending human review): {len(metrics['unlabeled_flags'])}")

    print("\n" + "=" * 80)
    print("LEGACY METRICS (original protocol — no negative controls, for comparison)")
    print("=" * 80)
    print(f"Legacy precision (unlabeled counted as FP): {metrics['legacy_precision']:.1%}")
    print(f"Legacy hallucination rate:                  {metrics['legacy_hallucination_rate']:.1%}")
    print(f"Legacy total flags:                         {metrics['legacy_total_flags']}")
    print("=" * 80 + "\n")

    if metrics["recall"] < 0.4:
        print("WARNING: Low recall. Pipeline is missing many known issues.")
    if metrics["hallucination_rate"] > 0.3:
        print("WARNING: High hallucination rate on the labeled universe.")
    if metrics["precision"] > 0.5 and metrics["recall"] > 0.4 and metrics["abstention_rate"] == 1.0:
        print("Results look strong: good precision/recall balance and honest abstention on hard cases.")

    print()


if __name__ == "__main__":
    main()
