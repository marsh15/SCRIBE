"""scribe-eval CLI.

    uv run --project intelligence scribe-eval validate benchmarks/datasets/golden-v1
    uv run --project intelligence scribe-eval assign-splits <dataset_dir> --seed N
    uv run --project intelligence scribe-eval score --rankings R.jsonl --dataset benchmarks/datasets/golden-v1 --split dev
    uv run --project intelligence scribe-eval run-profile --profile hybrid-rrf --split dev --database-url URL --out R.jsonl
    uv run --project intelligence scribe-eval compare <run_a_dir> <run_b_dir>
    uv run --project intelligence scribe-eval answer --rankings R.jsonl --split dev|validation [--generator real|fake]
    uv run --project intelligence scribe-eval judge-sheet --run <answer run dir> --out-dir <dir>
    uv run --project intelligence scribe-eval publish-run <run_dir> --database-url URL [--seeded|--mark-fake]
    uv run --project intelligence scribe-eval gate --database-url <test db> [--refresh-cache|--write-baseline]
    uv run --project intelligence scribe-eval judge-agreement --key <dir>/judge_key.json [--labels human_labels.csv]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bootstrap import paired_bootstrap
from .gate import DEFAULT_BASELINE, DEFAULT_CACHE, DEFAULT_CORPUS, DEFAULT_DATASET, DEFAULT_PROFILE, DEFAULT_SPLIT
from .metrics import METRIC_NAMES
from .runs import DEFAULT_RUNS_DIR, RunRecord, config_hash, load_run, now_iso
from .scoring import ScoreError, dataset_version, format_table, score_split, summary_to_json
from .splits import assign_splits_file
from .validate import summary_table, validate_dataset


def _validate(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dataset_dir)
    corpus_dir = Path(args.corpus)
    if not corpus_dir.is_dir():
        print(f"ERROR: corpus directory not found: {corpus_dir}", file=sys.stderr)
        return 2
    cases, errors = validate_dataset(dataset_dir, corpus_dir)
    if cases:
        print(summary_table(cases))
    if errors:
        print(f"\nFAILED: {len(errors)} problem(s)", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("\nOK")
    return 0


def _assign(args: argparse.Namespace) -> int:
    path = Path(args.dataset_dir) / "cases.jsonl"
    if not path.is_file():
        print(f"ERROR: missing {path}", file=sys.stderr)
        return 2
    n = assign_splits_file(Path(args.dataset_dir), args.seed, args.only_origin)
    print(f"assigned splits for {n} cases (seed={args.seed})")
    return 0


def _kv(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        if not sep:
            raise ValueError(f"expected KEY=VALUE, got {it!r}")
        out[k] = v
    return out


def _score(args: argparse.Namespace) -> int:
    if args.split == "test" and not args.milestone_report:
        print(
            "ERROR: --split test is refused (INV-12): the test split is used only for milestone "
            "reports, never for tuning. Pass --milestone-report if this is a milestone report.",
            file=sys.stderr,
        )
        return 2
    dataset_dir = Path(args.dataset)
    started = now_iso()
    try:
        models, prompts = _kv(args.model), _kv(args.prompt)
        _, results, summary = score_split(dataset_dir, args.split, Path(args.rankings))
    except (ScoreError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(format_table(summary))
    run = RunRecord(
        dataset_version=dataset_version(dataset_dir), split=args.split, profile_name=args.profile,
        config_hash=config_hash(args.profile), model_ids=models, prompt_versions=prompts,
        started_at=started, summary=summary_to_json(summary),
    )
    run_dir = run.write(args.out, results)
    print(f"\nrun written: {run_dir}")
    return 0


def _compare(args: argparse.Namespace) -> int:
    try:
        run_a, rows_a = load_run(args.run_a)
        run_b, rows_b = load_run(args.run_b)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"A: {run_a['profile_name']} ({run_a['run_id']})\nB: {run_b['profile_name']} ({run_b['run_id']})")
    print(f"{'metric':<12}{'n':>4}{'mean(B-A)':>11}   95% interval")
    for name in METRIC_NAMES:
        a = {r["case_id"]: r["metrics"][name] for r in rows_a if r.get("metrics")}
        b = {r["case_id"]: r["metrics"][name] for r in rows_b if r.get("metrics")}
        res = paired_bootstrap(a, b)
        flag = " *" if res.significant else ""
        print(f"{name:<12}{res.n:>4}{res.mean_diff:>+11.4f}   [{res.lo:+.4f}, {res.hi:+.4f}]{flag}")
    print("* interval excludes 0")
    return 0


def _dataset_sha(dataset_dir: Path) -> str:
    import hashlib

    return hashlib.sha256((dataset_dir / "cases.jsonl").read_bytes()).hexdigest()


def _answer(args: argparse.Namespace) -> int:
    from . import answer as ans

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
        from .dataset import load_cases

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
    from .scoring import dataset_version as _dv

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
    from . import judge as jd
    from .dataset import load_cases

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
    from . import judge as jd

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


def _gate(args: argparse.Namespace) -> int:
    from .gate import run_gate

    return run_gate(args)


def _ingest_corpus(args: argparse.Namespace) -> int:
    from .ingest_corpus import run

    return run(args)


def _run_profile(args: argparse.Namespace) -> int:
    from .run_profile import run

    return run(args)


def _publish_run(args: argparse.Namespace) -> int:
    from .publish import run

    return run(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scribe-eval")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="validate a golden dataset against the corpus")
    v.add_argument("dataset_dir")
    v.add_argument("--corpus", default="fixtures/corpus")
    v.set_defaults(func=_validate)
    a = sub.add_parser("assign-splits", help="deterministically assign dev/validation/test splits")
    a.add_argument("dataset_dir")
    a.add_argument("--seed", type=int, required=True)
    a.add_argument("--only-origin", default=None, help="assign only cases with this origin; others keep their split")
    a.set_defaults(func=_assign)
    sc = sub.add_parser("score", help="score a rankings JSONL file on a dataset split")
    sc.add_argument("--rankings", required=True)
    sc.add_argument("--dataset", default="benchmarks/datasets/golden-v1")
    sc.add_argument("--split", required=True, choices=["dev", "validation", "test"])
    sc.add_argument("--profile", default="unnamed")
    sc.add_argument("--out", default=str(DEFAULT_RUNS_DIR))
    sc.add_argument("--milestone-report", action="store_true", help="required to score the test split (INV-12)")
    sc.add_argument("--model", action="append", help="KEY=MODEL_ID recorded in the run (repeatable)")
    sc.add_argument("--prompt", action="append", help="KEY=VERSION recorded in the run (repeatable)")
    sc.set_defaults(func=_score)
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
    cp = sub.add_parser("compare", help="paired bootstrap comparison of two run dirs")
    cp.add_argument("run_a")
    cp.add_argument("run_b")
    cp.set_defaults(func=_compare)
    pr = sub.add_parser("publish-run", help="load a run dir into eval_runs/eval_results for the /eval dashboard (T30)")
    pr.add_argument("run_dir")
    pr.add_argument("--database-url", default=None, help="required; the environment is never consulted")
    pr.add_argument("--dataset", default="benchmarks/datasets/golden-v1", help="gold cases, for failure categories")
    pr.add_argument("--seeded", action="store_true", help="label as SEEDED TEST DATA (never a measurement)")
    pr.add_argument("--mark-fake", action="store_true", help="force the FAKE - NOT A MEASUREMENT stamp")
    pr.add_argument("--parse-lost", action="append", default=[], help="gold document known to be lost by the parser (repeatable)")
    pr.set_defaults(func=_publish_run)
    gt = sub.add_parser("gate", help="retrieval regression gate from cached provider responses (T31)")
    gt.add_argument("--database-url", default=None, help="throwaway database whose name contains 'test'; env never consulted")
    gt.add_argument("--dataset", default=DEFAULT_DATASET)
    gt.add_argument("--corpus", default=DEFAULT_CORPUS)
    gt.add_argument("--split", default=DEFAULT_SPLIT, choices=["dev", "validation", "test"])
    gt.add_argument("--profile", default=DEFAULT_PROFILE)
    gt.add_argument("--baseline", default=DEFAULT_BASELINE)
    gt.add_argument("--cache", default=DEFAULT_CACHE, help="committed jsonl.gz of cached embedding + rerank responses")
    gt.add_argument("--tau", type=float, default=None, help="abstention threshold (default: the profile's gate tau)")
    gt.add_argument("--out", default=None, help="also write gate-result.json here")
    gt.add_argument("--refresh-cache", action="store_true", help="LOCAL ONLY: call the providers and rewrite the cache")
    gt.add_argument("--write-baseline", action="store_true", help="replay the cache and write the baseline file")
    gt.add_argument("--status", action="store_true", help="print whether a real baseline exists (and set the GitHub 'armed' output); exit 0")
    gt.add_argument("--seeded", action="store_true", help="label the result as a seeded fixture (never armed)")
    gt.add_argument("--accept-seeded-baseline", action="store_true", help="tests only: treat a seeded baseline as armed")
    gt.set_defaults(func=_gate)
    ic = sub.add_parser("ingest-corpus", help="index the corpus into the eval workspace (T12)")
    ic.add_argument("--corpus", default="fixtures/corpus")
    ic.add_argument("--profile", default="structural-v1", choices=["structural-v1"])
    ic.add_argument("--database-url", default=None, help="required; the environment is never consulted")
    ic.add_argument("--embedder", default="gemini", choices=["fake", "gemini"])
    ic.add_argument("--reset", action="store_true", help="wipe the eval workspace's rows first")
    ic.add_argument("--cache-path", default="benchmarks/cache/py-embeddings.sqlite")
    ic.set_defaults(func=_ingest_corpus)
    rp = sub.add_parser("run-profile", help="run a retrieval profile on the ingested eval workspace, write rankings")
    rp.add_argument("--profile", required=True)
    rp.add_argument("--split", required=True, choices=["dev", "validation", "test"])
    rp.add_argument("--database-url", default=None, help="required; the environment is never consulted")
    rp.add_argument("--dataset", default="benchmarks/datasets/golden-v1")
    rp.add_argument("--out", required=True, help="rankings JSONL to write")
    rp.add_argument("--cache-path", default="benchmarks/cache/py-embeddings.sqlite")
    rp.add_argument("--limit", type=int, default=0, help="first N cases only (smoke test)")
    rp.set_defaults(func=_run_profile)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
