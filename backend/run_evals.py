import os
import sys
import time
import json
import glob as globmod
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from main import load_documents, run_verification_agents, apply_judge, build_report
from models import CitationFinding, FactFinding

EVAL_RUNS_DIR = Path(__file__).parent.parent / "outputs"
METRICS_LOG = EVAL_RUNS_DIR / "metrics_log.jsonl"
HISTORICAL_V1_FILE = EVAL_RUNS_DIR / "historical_v1_mock_api_call.json"

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

# Negative controls and "hard, unverifiable" cases: intentionally empty in the
# current (v3) protocol. We originally shipped one of each (a real-but-
# mischaracterized citation as a negative control, and an unpublished-order
# citation as an unverifiable hard case). Both were retracted after an
# independent judge review — see RETRACTED_LABELS — because on closer
# inspection neither actually tested what it was meant to test. We are leaving
# these registries empty and documented, rather than replacing them with a
# fresh, unaudited guess, so the harness does not silently repeat the same
# mistake.
KNOWN_CORRECT: dict = {}
HARD_CASES: dict = {}

# Historical protocol snapshots (see --protocol-version below). These are not
# hypothetical reconstructions: v1's flaw set and cached response are the
# actual first-commit state of this repo (`0_mock_api_call.json`, deleted in
# the second commit and restored here as eval_runs/historical_v1_mock_api_call.json
# via `git show 4ef3ff1:backend/0_mock_api_call.json`). v2 reuses the current
# cached response with the pre-retraction registry (C1/H1 still labeled as a
# negative control and a hard case, before the judge audit in Section 6).
PROTOCOL_VERSIONS = {
    1: {
        "flaw_ids": ["F1", "F2", "F3", "F4", "F5"],
        "known_correct": {},
        "hard_cases": {},
        "cached_file": HISTORICAL_V1_FILE,
        "note": (
            "Original protocol: 5 known flaws, no negative controls, no hard "
            "cases. Precision reported is the legacy (biased) figure, since "
            "the corrected precision formula did not exist yet at this stage."
        ),
    },
    2: {
        "flaw_ids": ["F1", "F2", "F3", "F4", "F5", "F6"],
        "known_correct": {
            "C1": {
                "keywords": ["seabright", "us airways"],
                "description": (
                    "SeaBright Insurance Co. v. US Airways — labeled (at the "
                    "time) as a real, correctly-supporting citation; retracted "
                    "in Section 6 and reclassified as flaw F7."
                ),
            }
        },
        "hard_cases": {
            "H1": {
                "keywords": ["cornerstone grading", "bc-2019-33021"],
                "description": (
                    "Rivera v. Cornerstone Grading & Paving — labeled (at the "
                    "time) as categorically unverifiable; retracted in Section "
                    "6 and reclassified as flaw F8."
                ),
            }
        },
        "cached_file": None,  # use whatever --cache file the caller loads
        "note": (
            "Corrected protocol: negative control (C1) and hard case (H1) "
            "added, before the judge audit retracted both labels."
        ),
    },
    3: {
        "flaw_ids": list(KNOWN_FLAWS.keys()),
        "known_correct": {},
        "hard_cases": {},
        "cached_file": None,
        "note": (
            "Final protocol, after RETRACTED_LABELS: C1/H1 promoted to F7/F8, "
            "no validated negative control or hard case currently registered."
        ),
    },
}

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


def evaluate_pipeline(
    report_dict: dict,
    known_flaws: dict | None = None,
    known_correct: dict | None = None,
    hard_cases: dict | None = None,
) -> dict:
    """Evaluates a pipeline report against a given protocol registry.
    Defaults to the current (v3) registries so existing callers/behavior are
    unchanged; pass explicit registries to replay an earlier protocol version
    (see PROTOCOL_VERSIONS / --protocol-version)."""
    known_flaws = KNOWN_FLAWS if known_flaws is None else known_flaws
    known_correct = KNOWN_CORRECT if known_correct is None else known_correct
    hard_cases = HARD_CASES if hard_cases is None else hard_cases

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

        for hard_id, info in hard_cases.items():
            if matches_keywords(case_name, info["keywords"]):
                if verdict == "could_not_verify":
                    abstained_hard.add(hard_id)
                else:
                    committed_hard.add(hard_id)

        if verdict in ("likely_fabricated", "does_not_support") and confidence > 0.5:
            legacy_total_flags += 1

            flaw_id = find_match(combined, known_flaws)
            if flaw_id:
                matched_flaws.add(flaw_id)
                continue

            correct_id = find_match(combined, known_correct)
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
            for fid, info in known_flaws.items():
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

    recall = len(matched_flaws) / len(known_flaws) if known_flaws else 0
    # Precision and hallucination rate over the labeled universe are only
    # meaningful if we actually have negative controls to catch a false
    # positive against. With known_correct empty (see the retraction note
    # above the registries), labeled_total collapses to true_positives and
    # the formula below would silently report a trivial, uninformative 100%.
    # We report None (N/A) instead of a number that looks precise but isn't.
    precision = (true_positives / labeled_total) if known_correct and labeled_total > 0 else None
    hallucination_rate = (false_positives / labeled_total) if known_correct and labeled_total > 0 else None
    abstention_rate = (len(abstained_hard) / len(hard_cases)) if hard_cases else None

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


def fmt_pct(value):
    return f"{value:.1%}" if value is not None else "N/A"


def run_protocol_version(version: int, cache_file: Path | None):
    """Replays a historical (or current) evaluation protocol against a cached
    pipeline report, per PROTOCOL_VERSIONS. No API calls are made — this is
    purely re-scoring an existing cached response under a different gabarito,
    so every number in the paper's Section 5/7 can be regenerated by a
    reader from files already committed to the repo."""
    spec = PROTOCOL_VERSIONS[version]
    flaw_registry = {fid: KNOWN_FLAWS[fid] for fid in spec["flaw_ids"]}
    resolved_file = spec["cached_file"] or cache_file
    if resolved_file is None or not resolved_file.exists():
        print(f"ERROR: no cached file available for protocol version {version} ({resolved_file})")
        sys.exit(1)

    with open(resolved_file) as f:
        report = json.load(f)

    metrics = evaluate_pipeline(
        report,
        known_flaws=flaw_registry,
        known_correct=spec["known_correct"],
        hard_cases=spec["hard_cases"],
    )

    print("\n" + "=" * 80)
    print(f"PROTOCOL VERSION {version} — {spec['note']}")
    print(f"Cached file: {resolved_file.name}")
    print("=" * 80)
    print(f"Recall:                {fmt_pct(metrics['recall'])} "
          f"({len(metrics['matched_flaws'])}/{len(flaw_registry)})")
    if spec["known_correct"]:
        print(f"Precision:              {fmt_pct(metrics['precision'])}")
        print(f"Hallucination rate:     {fmt_pct(metrics['hallucination_rate'])}")
    else:
        print(f"Precision (legacy, unlabeled counted as FP): {fmt_pct(metrics['legacy_precision'])}")
    if spec["hard_cases"]:
        print(f"Abstention rate (hard): {fmt_pct(metrics['abstention_rate'])} "
              f"({len(metrics['abstained_hard_cases'])}/{len(spec['hard_cases'])})")
    print("=" * 80 + "\n")
    return metrics


def git_commit_hash() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent.parent,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def append_metrics_log(entry: dict):
    EVAL_RUNS_DIR.mkdir(exist_ok=True)
    with open(METRICS_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def run_repeated(
    n: int,
    model: str | None,
    judge_model: str | None,
    sleep_seconds: float,
    skip_judge: bool = False,
    temperature: float | None = None,
):
    """Runs the full live pipeline N times against the current (v3) registry,
    persisting each run's raw output and metrics so the paper can report a
    mean/std over real repeated executions instead of a single cached run."""
    EVAL_RUNS_DIR.mkdir(exist_ok=True)

    if model:
        os.environ["GEMINI_MODEL_OVERRIDE"] = model
    if temperature is not None:
        os.environ["LLM_TEMPERATURE_OVERRIDE"] = str(temperature)
    label = model or os.getenv("GEMINI_MODEL_OVERRIDE") or "default"
    if temperature is not None:
        label = f"{label}_temp{temperature}"
    judge_label = "skipped" if skip_judge else (judge_model or "default-larger-model")

    print(f"Running {n} live pipeline execution(s) — generator model: {label}, judge model: {judge_label}, temperature: {temperature if temperature is not None else 'default (0, except facts=0.1)'}")
    documents = load_documents()

    pre_recalls, post_recalls = [], []
    run_records = []

    for k in range(1, n + 1):
        print(f"\n--- run {k}/{n} ---")
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # Generator stage and judge stage are decoupled: a judge-stage failure
        # (e.g. the judge model's free-tier daily quota running out) should
        # not discard a generator run that already succeeded and already
        # spent its own API quota.
        try:
            raw_citations, raw_facts = run_verification_agents(documents)
        except Exception as e:
            print(f"  run {k} FAILED (generator stage): {e}")
            continue

        pre_report = {"report": build_report(raw_citations, raw_facts, judicial_memo=None).model_dump()}
        pre_metrics = evaluate_pipeline(pre_report)
        pre_file = EVAL_RUNS_DIR / f"{ts}_{label}_run{k}_pre.json"
        with open(pre_file, "w") as f:
            json.dump(pre_report, f, indent=2)
        print(f"  pre-judge recall:  {fmt_pct(pre_metrics['recall'])} ({len(pre_metrics['matched_flaws'])}/{len(KNOWN_FLAWS)})")
        pre_recalls.append(pre_metrics["recall"])

        post_metrics = None
        if skip_judge:
            print("  judge stage skipped (--skip-judge)")
        else:
            try:
                judged_citations, judged_facts = apply_judge(
                    raw_citations, raw_facts, documents, judge_model=judge_model
                )
                post_report = {"report": build_report(judged_citations, judged_facts, judicial_memo=None).model_dump()}
                post_metrics = evaluate_pipeline(post_report)
                post_file = EVAL_RUNS_DIR / f"{ts}_{label}_run{k}_post.json"
                with open(post_file, "w") as f:
                    json.dump(post_report, f, indent=2)
                print(f"  post-judge recall: {fmt_pct(post_metrics['recall'])} ({len(post_metrics['matched_flaws'])}/{len(KNOWN_FLAWS)})")
                post_recalls.append(post_metrics["recall"])
            except Exception as e:
                print(f"  run {k}: judge stage FAILED, pre-judge result kept: {e}")

        record = {
            "timestamp": ts,
            "commit": git_commit_hash(),
            "generator_model": label,
            "judge_model": judge_label,
            "run_index": k,
            "pre_judge_recall": pre_metrics["recall"],
            "post_judge_recall": post_metrics["recall"] if post_metrics else None,
            "pre_judge_matched_flaws": pre_metrics["matched_flaws"],
            "post_judge_matched_flaws": post_metrics["matched_flaws"] if post_metrics else None,
            "judge_stage_failed": post_metrics is None,
        }
        append_metrics_log(record)
        run_records.append(record)

        if k < n and sleep_seconds > 0:
            print(f"  sleeping {sleep_seconds}s before next run (rate-limit pacing)...")
            time.sleep(sleep_seconds)

    if not pre_recalls:
        print("\nAll runs failed — no metrics to summarize.")
        return

    def mean_std(values):
        if not values:
            return None, None
        mean = statistics.mean(values)
        std = statistics.pstdev(values) if len(values) > 1 else 0.0
        return mean, std

    pre_mean, pre_std = mean_std(pre_recalls)
    post_mean, post_std = mean_std(post_recalls)

    print("\n" + "=" * 80)
    print(f"SUMMARY — {len(pre_recalls)}/{n} generator run(s), {len(post_recalls)}/{n} judge run(s) succeeded; generator={label}, judge={judge_label}")
    print("=" * 80)
    print(f"Pre-judge recall:  mean={pre_mean:.1%} std={pre_std:.1%}  (values: {[f'{v:.1%}' for v in pre_recalls]})")
    if post_recalls:
        print(f"Post-judge recall: mean={post_mean:.1%} std={post_std:.1%}  (values: {[f'{v:.1%}' for v in post_recalls]})")
    else:
        print("Post-judge recall: N/A (judge stage failed on every run — see per-run errors above)")
    print("=" * 80 + "\n")

    summary = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "commit": git_commit_hash(),
        "generator_model": label,
        "judge_model": judge_label,
        "n_requested": n,
        "n_successful_generator": len(pre_recalls),
        "n_successful_judge": len(post_recalls),
        "pre_judge_recall_mean": pre_mean,
        "pre_judge_recall_std": pre_std,
        "post_judge_recall_mean": post_mean,
        "post_judge_recall_std": post_std,
        "runs": run_records,
    }
    summary_file = EVAL_RUNS_DIR / f"summary_{label}_{summary['timestamp']}.json"
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved summary to {summary_file.name}")


def run_judge_replay(
    glob_pattern: str,
    judge_model: str | None,
    sleep_seconds: float,
    temperature: float | None = None,
):
    """Applies the judge to already-saved pre-judge reports (from a prior
    --repeat run) instead of regenerating them, so the same set of generator
    outputs can be judged by multiple judge models/configurations without
    spending any additional generator API calls — only judge calls."""
    matches = sorted(Path(__file__).parent.glob(glob_pattern) if not os.path.isabs(glob_pattern) else globmod.glob(glob_pattern))
    if not matches:
        matches = sorted(Path(p) for p in globmod.glob(str(Path(__file__).parent / glob_pattern)))
    if not matches:
        print(f"No files matched pattern: {glob_pattern}")
        sys.exit(1)

    if temperature is not None:
        os.environ["LLM_TEMPERATURE_OVERRIDE"] = str(temperature)
    else:
        os.environ.pop("LLM_TEMPERATURE_OVERRIDE", None)

    judge_label = judge_model or "default-larger-model"
    if temperature is not None:
        judge_label = f"{judge_label}_temp{temperature}"
    print(
        f"Replaying judge ({judge_label}) over {len(matches)} saved pre-judge file(s), "
        f"temperature={temperature if temperature is not None else 'default (0)'}, "
        "no generator calls will be made"
    )
    documents = load_documents()

    post_recalls = []
    for i, pre_file in enumerate(matches, start=1):
        print(f"\n--- replay {i}/{len(matches)}: {pre_file.name} ---")
        with open(pre_file) as f:
            pre_report = json.load(f)

        citation_findings = [CitationFinding(**c) for c in pre_report["report"]["citations"]]
        fact_findings = [FactFinding(**fct) for fct in pre_report["report"]["facts"]]

        try:
            judged_citations, judged_facts = apply_judge(
                citation_findings, fact_findings, documents, judge_model=judge_model
            )
            post_report = {"report": build_report(judged_citations, judged_facts, judicial_memo=None).model_dump()}
            post_metrics = evaluate_pipeline(post_report)
        except Exception as e:
            print(f"  judge replay FAILED: {e}")
            if i < len(matches) and sleep_seconds > 0:
                time.sleep(sleep_seconds)
            continue

        post_file = pre_file.parent / f"{pre_file.stem.replace('_pre', '')}_post_judgereplay_{judge_label}.json"
        with open(post_file, "w") as f:
            json.dump(post_report, f, indent=2)

        print(f"  post-judge recall: {fmt_pct(post_metrics['recall'])} ({len(post_metrics['matched_flaws'])}/{len(KNOWN_FLAWS)})")
        post_recalls.append(post_metrics["recall"])

        append_metrics_log({
            "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "commit": git_commit_hash(),
            "judge_replay_source": pre_file.name,
            "judge_model": judge_label,
            "post_judge_recall": post_metrics["recall"],
            "post_judge_matched_flaws": post_metrics["matched_flaws"],
        })

        if i < len(matches) and sleep_seconds > 0:
            print(f"  sleeping {sleep_seconds}s before next replay (rate-limit pacing)...")
            time.sleep(sleep_seconds)

    if post_recalls:
        mean = statistics.mean(post_recalls)
        std = statistics.pstdev(post_recalls) if len(post_recalls) > 1 else 0.0
        print("\n" + "=" * 80)
        print(f"JUDGE REPLAY SUMMARY — judge={judge_label}, {len(post_recalls)}/{len(matches)} succeeded")
        print(f"Post-judge recall: mean={mean:.1%} std={std:.1%}  (values: {[f'{v:.1%}' for v in post_recalls]})")
        print("=" * 80 + "\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="VeriBrief Evaluation Harness")
    parser.add_argument(
        "--cache",
        action="store_true",
        help="Use cached pipeline output (mock_api_call.json) instead of calling API",
    )
    parser.add_argument(
        "--protocol-version",
        type=int,
        choices=[1, 2, 3],
        default=None,
        help=(
            "Replay a historical gabarito version (1, 2, or 3) against a "
            "cached report instead of running the current (v3) evaluation. "
            "No API calls made. See PROTOCOL_VERSIONS for what each version "
            "represents."
        ),
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=None,
        help="Run the live pipeline N times (real API calls) and report mean/std recall across runs.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override the generator model for --repeat runs (e.g. gemini-2.5-flash, gemini-2.5-pro).",
    )
    parser.add_argument(
        "--judge-model",
        type=str,
        default=None,
        help="Override the judge model for --repeat runs (e.g. same as --model, to run the same-family-judge condition).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Override generator temperature for --repeat runs (default: whatever each agent already uses, 0 for extraction/verification, 0.1 for fact-checking).",
    )
    parser.add_argument(
        "--judge-replay",
        type=str,
        default=None,
        help=(
            "Instead of running the generator, apply the judge to already-saved "
            "*_pre.json files matching this glob (relative to backend/), without "
            "spending any generator API calls. Requires --judge-model or uses the "
            "default judge model."
        ),
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=15.0,
        help="Seconds to sleep between repeated full-pipeline runs, to avoid bursting rate limits (default: 15).",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="Skip the judge stage entirely for --repeat runs (e.g. when the judge model's daily quota is already exhausted, to avoid wasting time on doomed retries).",
    )
    args = parser.parse_args()

    if args.judge_replay is not None:
        run_judge_replay(args.judge_replay, args.judge_model, args.sleep, temperature=args.temperature)
        return

    if args.repeat is not None:
        run_repeated(
            args.repeat, args.model, args.judge_model, args.sleep,
            skip_judge=args.skip_judge, temperature=args.temperature,
        )
        return

    if args.protocol_version is not None:
        cache_file = Path(__file__).parent / "mock_api_call.json"
        run_protocol_version(args.protocol_version, cache_file if args.cache else None)
        return

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
