"""answer, judge-sheet and judge-agreement subcommands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..runs import DEFAULT_RUNS_DIR, RunRecord, config_hash, now_iso
from ..scoring import ScoreError


def _dataset_sha(dataset_dir: Path) -> str:
    import hashlib

    return hashlib.sha256((dataset_dir / "cases.jsonl").read_bytes()).hexdigest()


def _answer(args: argparse.Namespace) -> int:
    from .. import answer as ans

    if args.split == "test" and not args.milestone_report:
        print(
            "ERROR: --split test is refused (INV-12): the test split is used only for milestone "
            "reports, never for tuning. Pass --milestone-report if this is a milestone report.",
            file=sys.stderr,
        )
        return 2
    dataset_dir = Path(args.dataset)
    fake_run = args.generator == "fake" or args.verifier == "fake"
    if fake_run and args.out is None:
        print("ERROR: a run with a fake generator/verifier needs an explicit --out so it can never land among "
              "measurements", file=sys.stderr)
        return 2
    out_dir = args.out or str(DEFAULT_RUNS_DIR)
    started = now_iso()
    try:
        from ..dataset import load_cases

        cases = [c for c in load_cases(dataset_dir / "cases.jsonl") if c.split == args.split]
        if not cases:
            raise ScoreError(f"no cases in split '{args.split}' of {dataset_dir}")
        rankings = ans.load_rankings_lenient(Path(args.rankings))
        missing = [c.id for c in cases if c.id not in rankings]
        if missing:
            raise ScoreError(f"rankings file is missing {len(missing)} case(s) of split '{args.split}': {', '.join(missing[:10])}")
        system, chat_version = ans.load_chat_prompt()
        if args.generator == "fake":
            generator: ans.Generator = ans.FakeGenerator()
        else:
            generator = ans.GeminiGenerator()
            if not generator.configured:  # type: ignore[attr-defined]
                raise ScoreError("real generator needs GOOGLE_GENERATIVE_AI_API_KEY in the environment")
        verifier: ans.ClaimVerifier | None
        if args.verifier == "fake":
            verifier = ans.FakeVerifier()
        elif args.verifier == "none":
            verifier = None
        else:
            verifier = ans.GeminiClaimVerifier()
            if not verifier.configured:  # type: ignore[attr-defined]
                raise ScoreError("real verifier needs GOOGLE_GENERATIVE_AI_API_KEY in the environment")
    except (ScoreError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    results, verifier_states = ans.run_answers(cases, rankings, generator, verifier, system=system,
                                               top_k=args.top_k, tau=args.tau)
    summary = ans.summarize_answers(results)
    summary["verifier_states"] = verifier_states
    fake_components = [n for n, c in (("generator", generator), ("verifier", verifier)) if c is not None and c.is_fake]
    from ..scoring import dataset_version as _dv

    meta = {}
    if (dataset_dir / "meta.json").is_file():
        meta = json.loads((dataset_dir / "meta.json").read_text(encoding="utf-8"))
    unreviewed = sum(1 for c in cases if c.reviewed is False)
    review = {"status": meta.get("review_status", "NOT YET HUMAN-REVIEWED"), "cases_in_split": len(cases),
              "unreviewed_in_split": unreviewed}
    model_ids = {"generator": generator.model_id}
    prompt_versions = {"chat": chat_version}
    if verifier is not None:
        from scribe_intel.verification import PROMPT_VERSION

        model_ids["verifier"] = verifier.model
        prompt_versions["claim_verify"] = PROMPT_VERSION
    config = {"top_k": args.top_k, "tau": args.tau, "verifier": args.verifier, "generator": args.generator}
    run = RunRecord(
        dataset_version=_dv(dataset_dir), split=args.split, profile_name=args.profile,
        config_hash=config_hash(args.profile, config), model_ids=model_ids, prompt_versions=prompt_versions,
        started_at=started, summary={**summary, "review": review},
        measurement=not fake_components, stamp=ans.FAKE_STAMP if fake_components else None,
        dataset_sha256=_dataset_sha(dataset_dir),
    )
    run_dir = run.write(out_dir, [])
    rec = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    rec["fake_components"] = fake_components
    (run_dir / "run.json").write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
    (run_dir / "answers.jsonl").write_text("".join(json.dumps(r.row(), ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
    (run_dir / "report.md").write_text(ans.report_markdown(rec, summary, review), encoding="utf-8")
    if fake_components:
        print(ans.FAKE_STAMP)
    print(ans.report_markdown(rec, summary, review))
    print(f"run written: {run_dir}")
    return 0


def _judge_sheet(args: argparse.Namespace) -> int:
    from .. import judge as jd
    from ..dataset import load_cases

    run_dir = Path(args.run)
    try:
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        rows = [json.loads(l) for l in (run_dir / "answers.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        cases = {c.id: c for c in load_cases(Path(args.dataset) / "cases.jsonl")}
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.judge == "fake":
        judge: jd.Judge = jd.FakeJudge()
    else:
        judge = jd.GeminiJudge()
        if not judge.configured:  # type: ignore[attr-defined]
            print("ERROR: real judge needs GOOGLE_GENERATIVE_AI_API_KEY in the environment", file=sys.stderr)
            return 2
    answers_fake = not run.get("measurement", False)
    sheet, key = jd.build_sheet(rows, cases, judge, answers_fake=answers_fake, n=args.n)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fake = answers_fake or judge.is_fake
    (out / "labelling_sheet.csv").write_text(jd.sheet_csv(sheet), encoding="utf-8")
    (out / "labelling_sheet.md").write_text(jd.sheet_markdown(sheet, fake=fake), encoding="utf-8")
    (out / "judge_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(f"labelling sheet: {len(sheet)} answers -> {out}/labelling_sheet.csv (+ .md); judge verdicts kept apart in judge_key.json")
    if len(sheet) < args.n:
        print(f"WARNING: only {len(sheet)} answers available, fewer than the {args.n} requested", file=sys.stderr)
    if fake:
        print(jd.FAKE_STAMP)
    print(jd.PENDING)
    return 0


def _judge_agreement(args: argparse.Namespace) -> int:
    from .. import judge as jd

    try:
        key = json.loads(Path(args.key).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    rep = jd.agreement_report(key, Path(args.labels) if args.labels else None)
    text = jd.agreement_markdown(rep)
    out = Path(args.out) if args.out else Path(args.key).with_name("judge_agreement.md")
    out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if rep["status"] in ("ok", "pending") else 1


def register(sub: argparse._SubParsersAction) -> None:
    an = sub.add_parser("answer", help="answer-level scoring: generate, verify citations, score (T28)")
    an.add_argument("--rankings", required=True, help="retrieval bundles, same JSONL format as `score`")
    an.add_argument("--dataset", default="benchmarks/datasets/golden-v1")
    an.add_argument("--split", required=True, choices=["dev", "validation", "test"])
    an.add_argument("--profile", default="unnamed")
    an.add_argument("--out", default=None)
    an.add_argument("--milestone-report", action="store_true", help="required to run the test split (INV-12)")
    an.add_argument("--generator", choices=["real", "fake"], default="real")
    an.add_argument("--verifier", choices=["real", "fake", "none"], default="real")
    an.add_argument("--top-k", type=int, default=6)
    an.add_argument("--tau", type=float, default=0.0, help="gate threshold on the top ranking score (0 = uncalibrated)")
    an.set_defaults(func=_answer)
    js = sub.add_parser("judge-sheet", help="make the 60-answer human labelling sheet from an answer run")
    js.add_argument("--run", required=True)
    js.add_argument("--dataset", default="benchmarks/datasets/golden-v1")
    js.add_argument("--out-dir", required=True)
    js.add_argument("--judge", choices=["real", "fake"], default="real")
    js.add_argument("-n", type=int, default=60)
    js.set_defaults(func=_judge_sheet)
    ja = sub.add_parser("judge-agreement", help="Cohen's kappa between judge verdicts and the user's labels")
    ja.add_argument("--key", required=True)
    ja.add_argument("--labels", default=None)
    ja.add_argument("--out", default=None)
    ja.set_defaults(func=_judge_agreement)
