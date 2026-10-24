"""answer, judge-sheet and judge-agreement subcommands."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

from ..runs import DEFAULT_RUNS_DIR, RunRecord, config_hash, git_commit, now_iso
from ..scoring import ScoreError


def _dataset_sha(dataset_dir: Path) -> str:
    import hashlib

    return hashlib.sha256((dataset_dir / "cases.jsonl").read_bytes()).hexdigest()


def _durable_write(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _estimate_answer_usd(cases, rankings, system, args, generator, verifier, judge=None) -> float:
    """cases x per-case estimate from the REAL bundle text of each case: generator input = system + question +
    context, output = GENERATION_OUTPUT_TOKENS_EST; verifier input = quotes + overhead, output per claim."""
    from .. import answer as ans
    from .. import cost as costmod

    usd = 0.0
    for case in cases:
        bundle = ans.build_bundle(rankings.get(case.id), top_k=args.top_k, tau=args.tau)
        context = ans.format_context(bundle)
        if generator is not None:
            usd += costmod.tokens_usd(
                generator.model_id,
                costmod.estimate_tokens(system) + costmod.estimate_tokens(case.question) + costmod.estimate_tokens(context),
                costmod.GENERATION_OUTPUT_TOKENS_EST)
        if verifier is not None and bundle.items:
            usd += costmod.tokens_usd(
                verifier.model,
                costmod.VERIFY_OVERHEAD_TOKENS_EST + sum(costmod.estimate_tokens(i.text) for i in bundle.items),
                costmod.VERIFY_OUTPUT_TOKENS_PER_CLAIM_EST * costmod.CLAIMS_PER_ANSWER_EST)
        if judge is not None:
            usd += costmod.tokens_usd(judge.model_id,
                                      costmod.estimate_tokens(case.question) + costmod.estimate_tokens(case.reference_answer)
                                      + costmod.estimate_tokens(system) + costmod.GENERATION_OUTPUT_TOKENS_EST,
                                      8)
    return usd


def _answer(args: argparse.Namespace) -> int:
    try:
        return _answer_impl(args)
    except OSError as exc:
        print(f"ERROR: answer output failed ({type(exc).__name__})", file=sys.stderr)
        return 2


def _answer_impl(args: argparse.Namespace) -> int:
    from .. import answer as ans

    args.cache_mode = args.cache_mode or ans.default_cache_mode()

    if args.split == "test" and not args.milestone_report:
        print(
            "ERROR: --split test is refused (INV-12): the test split is used only for milestone "
            "reports, never for tuning. Pass --milestone-report if this is a milestone report.",
            file=sys.stderr,
        )
        return 2
    dataset_dir = Path(args.dataset)
    fake_run = args.generator == "fake" or args.verifier == "fake" or args.judge == "fake"
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
        if args.milestone_report:
            from ..review.require import refusal_message, unreviewed_ids

            unreviewed = unreviewed_ids(cases)
            if unreviewed:
                print(refusal_message(unreviewed, len(cases), f"run a milestone report on split '{args.split}'"),
                      file=sys.stderr)
                return 2
        if args.split == "test" and not args.milestone_id:
            print("ERROR: --split test requires --milestone-id (INV-12)", file=sys.stderr)
            return 2
        rankings = ans.load_rankings_lenient(Path(args.rankings))
        missing = [c.id for c in cases if c.id not in rankings]
        if missing:
            raise ScoreError(f"rankings file is missing {len(missing)} case(s) of split '{args.split}': {', '.join(missing[:10])}")
        config_hashes = {(r.get("meta") or {}).get("config_hash") for r in rankings.values()} - {None, ""}
        if len(config_hashes) > 1:
            raise ScoreError("ranking rows have inconsistent config_hash metadata")
        retrieval_config_hash = next(iter(config_hashes)) if config_hashes else None
        system, chat_version = ans.load_chat_prompt()
        if args.generator == "fake":
            generator: ans.Generator = ans.FakeGenerator()
        else:
            generator = ans.GeminiGenerator(model=ans.DEFAULT_GENERATOR_MODEL)
            if args.cache_mode == "live" and not generator.configured:  # type: ignore[attr-defined]
                raise ScoreError("real generator needs GOOGLE_GENERATIVE_AI_API_KEY in the environment")
        verifier: ans.ClaimVerifier | None
        if args.verifier == "fake":
            verifier = ans.FakeVerifier()
        elif args.verifier == "none":
            verifier = None
        else:
            verifier = ans.GeminiClaimVerifier()
            if args.cache_mode == "live" and not verifier.configured:  # type: ignore[attr-defined]
                raise ScoreError("real verifier needs GOOGLE_GENERATIVE_AI_API_KEY in the environment")
        from .. import judge as jd
        if args.judge == "fake":
            judge = jd.FakeJudge()
        else:
            judge = jd.GeminiJudge(model=args.judge_model)
            if args.cache_mode == "live" and not judge.configured:
                raise ScoreError("real judge needs GOOGLE_GENERATIVE_AI_API_KEY in the environment")
    except (ScoreError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    from .. import cost as costmod

    ledger = getattr(args, "ledger", None)
    real_gen = not generator.is_fake
    real_ver = verifier is not None and not verifier.is_fake
    real_judge = not judge.is_fake
    if real_gen or real_ver or real_judge:
        try:
            estimate = _estimate_answer_usd(cases, rankings, system, args, generator if real_gen else None,
                                            verifier if real_ver else None, judge if real_judge else None)
            cache_mode = args.cache_mode
            if cache_mode == "replay":
                estimate = 0.0
            total, cap = costmod.preflight(estimate, spend_cap=getattr(args, "spend_cap", None), path=ledger,
                                           what=f"answer {len(cases)} case(s) of {args.split}")
        except (costmod.SpendError, costmod.UnknownPrice, ValueError, OSError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"spend: pre-run estimate ${estimate:.4f}, ledger ${total:.4f}, cap ${cap:.2f}")
    import uuid

    run_id = str(uuid.uuid4())
    cache = ans.ResponseCache(args.response_cache, args.cache_mode)
    try:
        cache.validate()
    except (RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    metered_gen = costmod.MeteredGenerator(generator) if real_gen else None
    metered_ver = costmod.MeteredVerifier(verifier) if real_ver else None
    from ..cost import MeteredJudge
    metered_judge = MeteredJudge(judge) if real_judge else None
    with ExitStack() as stack:
        lock_profiles = [f"answer:{args.profile}"]
        if args.split == "test":
            from ..run_profile import RunProfileError, acquire_test_lock, mark_profile_complete, test_split_execution_guard

            try:
                stack.enter_context(test_split_execution_guard(args.runs_dir, dataset_dir.resolve().name))
                todo = acquire_test_lock(args.runs_dir, dataset_dir.resolve().name, _dataset_sha(dataset_dir),
                                         lock_profiles, f"answer:{args.milestone_id}", git_commit())
            except (RunProfileError, OSError, ValueError) as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
            if not todo:
                print("test split already completed for this milestone; skipping")
                return 0

        try:
            results, verifier_states = ans.run_answers(
                cases, rankings, metered_gen or generator, metered_ver or verifier, system=system, top_k=args.top_k,
                tau=args.tau, judge=metered_judge or judge, response_cache=cache)
        except ans.CacheMiss as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        finally:  # spend that happened before a crash is still spend
            spend = costmod.RunSpend("answer", run_id)
            for meter in (metered_gen, metered_ver, metered_judge):
                if meter is not None:
                    for usage in meter.events:
                        spend.add(usage)
            if spend.usages:
                spend.write(ledger)
            spend_usages = spend
        summary = ans.summarize_answers(results)
        summary["verifier_states"] = verifier_states
        fake_components = [n for n, c in (("generator", generator), ("verifier", verifier), ("judge", judge)) if c is not None and c.is_fake]
        from ..scoring import dataset_version as _dv

        meta = {}
        if (dataset_dir / "meta.json").is_file():
            meta = json.loads((dataset_dir / "meta.json").read_text(encoding="utf-8"))
        unreviewed = sum(1 for c in cases if c.reviewed is False)
        review = {"status": meta.get("review_status", "NOT YET HUMAN-REVIEWED"), "cases_in_split": len(cases),
                  "unreviewed_in_split": unreviewed}
        model_ids = {"generator": generator.model_id}
        model_ids["judge"] = judge.model_id
        from ..judge import PROMPT_VERSION as JUDGE_PROMPT_VERSION
        prompt_versions = {"chat": chat_version, "semantic_judge": JUDGE_PROMPT_VERSION}
        if verifier is not None:
            from scribe_intel.verification import PROMPT_VERSION

            model_ids["verifier"] = verifier.model
            prompt_versions["claim_verify"] = PROMPT_VERSION
        config = {"top_k": args.top_k, "tau": args.tau, "verifier": args.verifier, "generator": args.generator,
                  "judge": args.judge, "judge_model": args.judge_model, "cache_mode": args.cache_mode,
                  "milestone_id": args.milestone_id}
        run = RunRecord(
            run_id=run_id, dataset_version=_dv(dataset_dir), split=args.split, profile_name=args.profile,
            config_hash=config_hash(args.profile, config), model_ids=model_ids, prompt_versions=prompt_versions,
            started_at=started, summary={**summary, "review": review},
            measurement=not fake_components, stamp=ans.FAKE_STAMP if fake_components else None,
            dataset_sha256=_dataset_sha(dataset_dir),
        )
        run.dataset_name, run.corpus = dataset_dir.resolve().name, getattr(args, "corpus", None)
        run.milestone_id = args.milestone_id if args.split == "test" else None
        ws_ids = {(r.get("meta") or {}).get("workspace_id") for r in rankings.values()} - {None}
        run.workspace_id = next(iter(ws_ids)) if len(ws_ids) == 1 else None
        run.leak_rate, run.leak_rate_verified = summary["leak"]["leak_rate"], summary["leak"]["leak_rate_verified"]
        if spend_usages.usages:
            run.cost = {"usd": round(sum(u.usd for u in spend_usages.usages), 8),
                        "estimated": any(u.estimated for u in spend_usages.usages),
                        "tokens": {f"{u.model_id}": u.input_tokens + u.output_tokens for u in spend_usages.usages}}
        run_dir = run.write(out_dir, [])
        rec = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        rec["fake_components"] = fake_components
        if retrieval_config_hash:
            rec["retrieval_config_hash"] = retrieval_config_hash
        _durable_write(run_dir / "run.json", json.dumps(rec, indent=2, default=str))
        _durable_write(run_dir / "answers.jsonl", "".join(json.dumps(r.row(), ensure_ascii=False) + "\n" for r in results))
        _durable_write(run_dir / "report.md", ans.report_markdown(rec, summary, review))
        _durable_write(run_dir / "results.jsonl", "")
        directory = os.open(run_dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        if args.split == "test":
            mark_profile_complete(args.runs_dir, dataset_dir.resolve().name, lock_profiles[0], Path(run_dir))
        if fake_components:
            print(ans.FAKE_STAMP)
        print(ans.report_markdown(rec, summary, review))
        print(f"run written: {run_dir}")
        return 0


def _judge_sheet(args: argparse.Namespace) -> int:
    from .. import judge as jd
    from .. import answer as ans
    from .. import cost as costmod
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
        judge = jd.GeminiJudge(model=args.judge_model)
        if args.cache_mode == "live" and not judge.configured:  # type: ignore[attr-defined]
            print("ERROR: real judge needs GOOGLE_GENERATIVE_AI_API_KEY in the environment", file=sys.stderr)
            return 2
    answers_fake = not run.get("measurement", False)
    cache = ans.ResponseCache(args.response_cache, args.cache_mode or ans.default_cache_mode())
    try:
        cache.validate()
        picked = jd.select_for_labelling(rows, args.n)
        mode = cache.mode
        estimate = 0.0 if mode == "replay" or judge.is_fake else sum(
            costmod.tokens_usd(judge.model_id, costmod.estimate_tokens(cases[r["case_id"]].question)
                               + costmod.estimate_tokens(cases[r["case_id"]].reference_answer)
                               + costmod.estimate_tokens(r["answer"]) + 300, 8) for r in picked)
        total, cap = costmod.preflight(estimate, spend_cap=args.spend_cap, path=args.ledger, what="judge-sheet")
    except (OSError, RuntimeError, ValueError, costmod.SpendError, costmod.UnknownPrice) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"spend: pre-run estimate ${estimate:.4f}, ledger ${total:.4f}, cap ${cap:.2f}")

    meter = costmod.MeteredJudge(judge) if not judge.is_fake else None

    class CachedJudge:
        def __init__(self):
            self.model_id = judge.model_id
            self.is_fake = judge.is_fake

        def judge(self, question, reference, answer):
            if hasattr(judge, "prompt_parts"):
                system, prompt = judge.prompt_parts(question, reference, answer)
            else:
                system, prompt = "", f"{question}\n{reference}\n{answer}"
            bundle = ans.Bundle("ANSWERABLE", "judge-sheet", [], None, 0.0)

            def call():
                if not getattr(judge, "configured", True):
                    raise ans.CacheMiss("response cache miss for an unconfigured judge; record the response first")
                return (meter or judge).judge(question, reference, answer)

            result, hit = cache.call(self.model_id, system + "\n" + prompt + "\n" + jd.PROMPT_VERSION, bundle, call)
            if hit and meter is not None:
                meter.events.append(costmod.Usage(self.model_id, costmod.estimate_tokens(system + prompt),
                                                 8, True, cached=True))
            return result

    try:
        sheet, key = jd.build_sheet(rows, cases, CachedJudge(), answers_fake=answers_fake, n=args.n)
    except (ans.CacheMiss, RuntimeError, ValueError, OSError) as exc:
        print(f"ERROR: judge-sheet failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2
    finally:
        spend = costmod.RunSpend("judge-sheet", str(__import__("uuid").uuid4()))
        for usage in meter.events if meter is not None else []:
            spend.add(usage)
        if spend.usages:
            spend.write(args.ledger)
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
    an.add_argument("--corpus", default="fixtures/corpus", help="corpus the rankings were made on (recorded in the run)")
    an.add_argument("--spend-cap", type=float, default=None, help="USD cap for ledger total + estimate; can only lower $10")
    an.add_argument("--ledger", default="benchmarks/spend-ledger.jsonl")
    an.add_argument("--split", required=True, choices=["dev", "validation", "test"])
    an.add_argument("--profile", default="unnamed")
    an.add_argument("--out", default=None)
    an.add_argument("--milestone-report", action="store_true", help="required to run the test split (INV-12)")
    an.add_argument("--milestone-id", default=None, help="id of this once-only milestone; reused only to resume it")
    an.add_argument("--runs-dir", default="benchmarks/runs", help="where <dataset>/test-split.lock lives")
    an.add_argument("--generator", choices=["real", "fake"], default="real")
    an.add_argument("--verifier", choices=["real", "fake", "none"], default="real")
    an.add_argument("--judge", choices=["real", "fake"], default="real")
    an.add_argument("--judge-model", default="gemini-2.5-pro")
    an.add_argument("--response-cache", default="benchmarks/cache/answer-responses.jsonl")
    an.add_argument("--cache-mode", choices=["live", "replay", "record-missing"], default=None)
    an.add_argument("--top-k", type=int, default=6)
    an.add_argument("--tau", type=float, default=0.0, help="gate threshold on the top ranking score (0 = uncalibrated)")
    an.set_defaults(func=_answer)
    js = sub.add_parser("judge-sheet", help="make the 60-answer human labelling sheet from an answer run")
    js.add_argument("--run", required=True)
    js.add_argument("--dataset", default="benchmarks/datasets/golden-v1")
    js.add_argument("--out-dir", required=True)
    js.add_argument("--judge", choices=["real", "fake"], default="real")
    js.add_argument("--judge-model", default="gemini-2.5-pro")
    js.add_argument("--response-cache", default="benchmarks/cache/answer-responses.jsonl")
    js.add_argument("--cache-mode", choices=["live", "replay", "record-missing"], default=None)
    js.add_argument("--ledger", default="benchmarks/spend-ledger.jsonl")
    js.add_argument("--spend-cap", type=float, default=None)
    js.add_argument("-n", type=int, default=60)
    js.set_defaults(func=_judge_sheet)
    ja = sub.add_parser("judge-agreement", help="Cohen's kappa between judge verdicts and the user's labels")
    ja.add_argument("--key", required=True)
    ja.add_argument("--labels", default=None)
    ja.add_argument("--out", default=None)
    ja.set_defaults(func=_judge_agreement)
