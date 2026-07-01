import sys
from pathlib import Path
from main import load_documents, run_pipeline, run_verification_agents, apply_judge, build_report

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
    # F7 and F8 were NOT originally part of the test oracle. They were
    # promoted to known flaws only after a second, independent, larger-model
    # judge audited two items we had originally labeled the opposite way
    # (see RETRACTED_LABELS below) and gave specific, checkable reasoning for
    # why the pipeline's original verdict was in fact correct. We are
    # deliberately keeping this promotion visible in the code, rather than
    # quietly rewriting history, because it is itself a finding: constructing
    # reliable ground truth for this kind of task is error-prone even for the
    # person building the test case.
    "F7": {
        "type": "subtle_misattribution",
        "keywords": ["seabright", "us airways"],
        "description": (
            "SeaBright Insurance Co. v. US Airways is a real, accurately named "
            "precedent, but the proposition attributed to it in the brief "
            "mischaracterizes its actual holding — a subtler variant of a "
            "misquote (F1) because the case name and existence are correct "
            "and only the attributed holding is wrong"
        ),
    },
    "F8": {
        "type": "fabricated_case_format_anomaly",
        "keywords": ["cornerstone grading", "bc-2019-33021"],
        "description": (
            "Rivera v. Cornerstone Grading & Paving — flagged as fabricated on "
            "the basis of a docket-number format inconsistent with the court's "
            "real numbering conventions, not on case-name plausibility alone"
        ),
    },
}

# Negative controls and "hard, unverifiable" cases: intentionally empty.
# We originally shipped one of each (a real-but-mischaracterized citation as
# a negative control, and an unpublished-order citation as an unverifiable
# hard case). Both were retracted after an independent judge review — see
# RETRACTED_LABELS — because on closer inspection neither actually tested
# what it was meant to test. We are leaving these registries empty and
# documented, rather than replacing them with a fresh, unaudited guess, so
# the harness does not silently repeat the same mistake.
KNOWN_CORRECT: dict = {}
HARD_CASES: dict = {}

RETRACTED_LABELS = {
    "C1 (retracted)": (
        "Originally labeled SeaBright Insurance Co. v. US Airways as a "
        "negative control (a real, correctly-supporting citation), on the "
        "assumption that a real, correctly-named case is a safe negative "
        "control. An independent, larger-model judge review showed the "
        "brief's attributed proposition does not match SeaBright's actual "
        "holding — the citation is real, but the negative control was "
        "mislabeled by the test author, not by the pipeline. Reclassified "
        "as flaw F7."
    ),
    "H1 (retracted)": (
        "Originally labeled an unpublished trial-court minute order as "
        "categorically unverifiable, on the assumption that an LLM has no "
        "way to assess a citation with no public record. An independent, "
        "larger-model judge review showed that docket-number format "
        "conventions are themselves a legitimate, checkable signal — the "
        "pipeline's 'likely_fabricated' verdict was well-grounded, not an "
        "overconfident guess. Reclassified as flaw F8."
    ),
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
    # Precision and hallucination rate over the labeled universe are only
    # meaningful if we actually have negative controls to catch a false
    # positive against. With KNOWN_CORRECT empty (see the retraction note
    # above the registries), labeled_total collapses to true_positives and
    # the formula below would silently report a trivial, uninformative 100%.
    # We report None (N/A) instead of a number that looks precise but isn't.
    precision = (true_positives / labeled_total) if KNOWN_CORRECT and labeled_total > 0 else None
    hallucination_rate = (false_positives / labeled_total) if KNOWN_CORRECT and labeled_total > 0 else None
    abstention_rate = (len(abstained_hard) / len(HARD_CASES)) if HARD_CASES else None

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
    cached_pre_judge_file = Path(__file__).parent / "mock_api_call_pre_judge.json"

    pre_judge_metrics = None

    if args.cache:
        if not cached_result_file.exists():
            print(f"ERROR: --cache flag used but {cached_result_file.name} not found")
            sys.exit(1)
        print(f"Loading cached pipeline output from {cached_result_file.name}...")
        try:
            with open(cached_result_file) as f:
                report = json.load(f)
            print("Loaded from cache (no API calls made)")
            if cached_pre_judge_file.exists():
                with open(cached_pre_judge_file) as f:
                    pre_judge_report = json.load(f)
                pre_judge_metrics = evaluate_pipeline(pre_judge_report)
        except Exception as e:
            print(f"ERROR: Failed to load cache: {e}")
            sys.exit(1)
    else:
        print("Running pipeline (this will use API quota)...")
        documents = load_documents()
        print(f"Loaded {len(documents)} documents:")
        for doc_name in sorted(documents.keys()):
            print(f"  - {doc_name}")

        print("\nRunning generator agents (extraction, citation verification, fact consistency)...")
        try:
            raw_citations, raw_facts = run_verification_agents(documents)
            pre_judge_report = {
                "report": build_report(raw_citations, raw_facts, judicial_memo=None).model_dump()
            }
            with open(cached_pre_judge_file, "w") as f:
                json.dump(pre_judge_report, f, indent=2)
            pre_judge_metrics = evaluate_pipeline(pre_judge_report)

            print("Running judge (LLM-as-judge critic stage)...")
            judged_citations, judged_facts = apply_judge(raw_citations, raw_facts, documents)
            final_report_obj = build_report(judged_citations, judged_facts, judicial_memo=None)
            report = {"report": final_report_obj.model_dump()}
            with open(cached_result_file, "w") as f:
                json.dump(report, f, indent=2)
            print(f"Cached pre-judge result to {cached_pre_judge_file.name}")
            print(f"Cached post-judge result to {cached_result_file.name}")
        except Exception as e:
            print(f"ERROR: Pipeline failed: {e}")
            import traceback

            traceback.print_exc()
            sys.exit(1)

    def fmt_pct(value):
        return f"{value:.1%}" if value is not None else "N/A"

    print("Evaluating results...\n")
    metrics = evaluate_pipeline(report)

    if RETRACTED_LABELS:
        print("RETRACTED LABELS (test-oracle errors found via independent audit):")
        for label, reason in RETRACTED_LABELS.items():
            print(f"  {label}: {reason}")
            print()

    print("KNOWN FLAWS:")
    for flaw_id, flaw_info in KNOWN_FLAWS.items():
        status = "DETECTED" if flaw_id in metrics["matched_flaws"] else "MISSED"
        print(f"  {flaw_id}: {flaw_info['description']}")
        print(f"       [{status}]\n")

    if KNOWN_CORRECT:
        print("NEGATIVE CONTROLS (known-correct citations):")
        for correct_id, info in KNOWN_CORRECT.items():
            status = (
                "FALSE POSITIVE"
                if correct_id in metrics["false_positives_on_negative_controls"]
                else "correctly left unflagged"
            )
            print(f"  {correct_id}: {info['description']}")
            print(f"       [{status}]\n")
    else:
        print("NEGATIVE CONTROLS: none currently in the harness (see RETRACTED LABELS above).\n")

    if HARD_CASES:
        print("HARD CASES (unverifiable — abstention expected):")
        for hard_id, info in HARD_CASES.items():
            status = (
                "ABSTAINED (could_not_verify)"
                if hard_id in metrics["abstained_hard_cases"]
                else "COMMITTED TO A VERDICT (risk of overconfidence)"
            )
            print(f"  {hard_id}: {info['description']}")
            print(f"       [{status}]\n")
    else:
        print("HARD CASES: none currently in the harness (see RETRACTED LABELS above).\n")

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
    print(f"Precision (over labeled universe):    {fmt_pct(metrics['precision'])}")
    print(f"Hallucination rate (over labeled universe): {fmt_pct(metrics['hallucination_rate'])}")
    print(
        f"Abstention rate on hard cases:         {fmt_pct(metrics['abstention_rate'])} "
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
    if metrics["hallucination_rate"] is not None and metrics["hallucination_rate"] > 0.3:
        print("WARNING: High hallucination rate on the labeled universe.")
    if (
        metrics["precision"] is not None
        and metrics["precision"] > 0.5
        and metrics["recall"] > 0.4
        and metrics["abstention_rate"] == 1.0
    ):
        print("Results look strong: good precision/recall balance and honest abstention on hard cases.")

    print()

    if pre_judge_metrics is not None:
        print("=" * 80)
        print("GENERATOR-ONLY vs. GENERATOR+JUDGE (effect of the critic stage)")
        print("=" * 80)
        print(f"{'Metric':<28}{'Pre-judge':>15}{'Post-judge':>15}")
        print(f"{'Recall':<28}{fmt_pct(pre_judge_metrics['recall']):>15} {fmt_pct(metrics['recall']):>14}")
        print(
            f"{'Precision':<28}{fmt_pct(pre_judge_metrics['precision']):>15} {fmt_pct(metrics['precision']):>14}"
        )
        print(
            f"{'Hallucination rate':<28}{fmt_pct(pre_judge_metrics['hallucination_rate']):>15} "
            f"{fmt_pct(metrics['hallucination_rate']):>14}"
        )
        print(
            f"{'Abstention rate (hard)':<28}{fmt_pct(pre_judge_metrics['abstention_rate']):>15} "
            f"{fmt_pct(metrics['abstention_rate']):>14}"
        )
        print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
