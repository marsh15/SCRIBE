"""Focused offline coverage for C2 response replay, semantic judging, and INV-12 answer locks."""

import json
from pathlib import Path

import pytest

from scribe_eval import answer as ans
from scribe_eval import cost
from scribe_eval.dataset import Case
from scribe_eval.cli import main


def make_case(answerable=True):
    evidence = [{"doc": "p.md", "version": 1, "quote": "leave is 12 days", "page": None, "grade": 2}] if answerable else []
    return Case.model_validate({"id": "c1", "split": "dev", "category": "test", "question": "How long?",
                                "answerable": answerable, "reference_answer": "Leave is 12 days.",
                                "must_include": ["12 days"] if answerable else [], "evidence": evidence})


class CountingGenerator:
    model_id = "gemini-2.5-flash"
    is_fake = False
    configured = True

    def __init__(self):
        self.calls = 0

    def generate(self, system, question, context):
        self.calls += 1
        return "## Answer\nLeave is 12 days [E1]."


class CountingVerifier:
    model = "gemini-2.5-flash"
    is_fake = False
    configured = True

    def __init__(self):
        self.calls = 0

    def verify(self, claims, quotes):
        self.calls += 1
        return {idx: "SUPPORTED" for idx, _, _ in claims}


class CountingJudge:
    model_id = "gemini-2.5-pro"
    is_fake = False
    configured = True

    def __init__(self):
        self.calls = 0

    def judge(self, question, reference, answer):
        self.calls += 1
        return "correct"


def test_response_cache_key_and_modes(tmp_path):
    path = tmp_path / "responses.jsonl"
    bundle = ans.Bundle("ANSWERABLE", "ok", [], 0.5, 0.0)
    assert ans.ResponseCache.key("m", "p", "b") == "1c3185c6ffa286c5d69edc676e584b21fdd8be772aea29bc9d689607acc61f26"
    assert ans.ResponseCache.key("m", "p", "b") != ans.ResponseCache.key("m", "p2", "b")
    assert ans.ResponseCache.key("m", "p", "b") != ans.ResponseCache.key("m2", "p", "b")
    assert ans.ResponseCache.key("m", "p", "b") != ans.ResponseCache.key("m", "p", "b2")
    assert ans.canonical_bundle(ans.Bundle("ANSWERABLE", "ok", [], 0.5, 0.0)) == (
        '{"items":[],"reason":"ok","state":"ANSWERABLE","tau":0.0,"workspace_id":null}')
    live = ans.ResponseCache(path, "live")
    assert live.call("m", "p", bundle, lambda: "first") == ("first", False)
    assert live.call("m", "p", bundle, lambda: "second") == ("second", False)  # live always calls
    replay = ans.ResponseCache(path, "replay")
    assert replay.call("m", "p", bundle, lambda: pytest.fail("replay called provider")) == ("second", True)
    with pytest.raises(ans.CacheMiss):
        replay.call("m", "missing", bundle, lambda: "never")
    rec = ans.ResponseCache(path, "record-missing")
    assert rec.call("m", "p", bundle, lambda: "never") == ("second", True)
    assert rec.call("m2", "p", bundle, lambda: "new") == ("new", False)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_answer_replay_preserves_verifier_indexes_and_adds_semantic_metrics(tmp_path):
    case = make_case()
    rankings = {case.id: {"ranking": [{"doc": "p.md", "text": "Leave is 12 days", "score": 0.9}]}}
    gen0, verifier0, judge0 = CountingGenerator(), CountingVerifier(), CountingJudge()
    gen, verifier, judge = cost.MeteredGenerator(gen0), cost.MeteredVerifier(verifier0), cost.MeteredJudge(judge0)
    cache_path = tmp_path / "answers.jsonl"
    live = ans.ResponseCache(cache_path, "live")
    first, _ = ans.run_answers([case], rankings, gen, verifier, system="sys", judge=judge, response_cache=live)
    assert first[0].claims[0]["status"] == "VERIFIED"
    assert first[0].semantic_judge_verdict == "correct"
    assert first[0].metrics["abstention_accuracy"] == 1.0
    assert first[0].metrics["citation_validity"] == 1.0
    assert first[0].cost["usd"] > 0

    replay = ans.ResponseCache(cache_path, "replay")
    second, _ = ans.run_answers([case], rankings, gen, verifier, system="sys", judge=judge, response_cache=replay)
    assert second[0].claims[0]["status"] == "VERIFIED"  # JSONL string keys restore to integer indexes
    assert (gen0.calls, verifier0.calls, judge0.calls) == (1, 1, 1)
    assert second[0].cost["usd"] == 0.0 and second[0].cost["cached"] is True


def test_verifier_and_judge_replay_misses_are_fatal(tmp_path):
    case = make_case()
    rankings = {case.id: {"ranking": [{"doc": "p.md", "text": "Leave is 12 days", "score": 0.9}]}}
    bundle = ans.build_bundle(rankings[case.id])
    context = ans.format_context(bundle)
    gen = CountingGenerator()
    # Put only the generator response in cache. Once it hits, verifier's missing entry must escape the local fallback.
    prompt = f"sys\n\nQUESTION:\n{case.question}\n\nCONTEXT:\n{context}"
    cache = ans.ResponseCache(tmp_path / "miss.jsonl", "live")
    cache.call(gen.model_id, prompt, bundle, lambda: gen.generate("sys", case.question, context))
    replay = ans.ResponseCache(cache.path, "replay")
    with pytest.raises(ans.CacheMiss):
        ans.run_answers([case], rankings, gen, CountingVerifier(), system="sys", judge=CountingJudge(), response_cache=replay)


def test_generator_and_semantic_judge_replay_misses_are_fatal(tmp_path):
    case = make_case()
    rankings = {case.id: {"ranking": [{"doc": "p.md", "text": "Leave is 12 days", "score": 0.9}]}}
    with pytest.raises(ans.CacheMiss, match="cache miss"):
        ans.run_answers([case], rankings, CountingGenerator(), None, system="sys", response_cache=ans.ResponseCache(tmp_path / "empty.jsonl", "replay"))

    gen0, verifier0 = CountingGenerator(), CountingVerifier()
    cache = ans.ResponseCache(tmp_path / "judge-miss.jsonl", "live")
    ans.run_answers([case], rankings, gen0, verifier0, system="sys", response_cache=cache)
    with pytest.raises(ans.CacheMiss, match="cache miss"):
        ans.run_answers([case], rankings, gen0, verifier0, system="sys", judge=CountingJudge(),
                        response_cache=ans.ResponseCache(cache.path, "replay"))


def test_answer_lock_uses_phase_namespace_with_same_user_milestone(tmp_path):
    from scribe_eval.run_profile import acquire_test_lock, mark_profile_complete

    ds = tmp_path / "d"
    ds.mkdir()
    (ds / "cases.jsonl").write_text("[]\n", encoding="utf-8")
    import hashlib
    sha = hashlib.sha256((ds / "cases.jsonl").read_bytes()).hexdigest()
    runs = tmp_path / "runs"
    assert acquire_test_lock(runs, "d", sha, ["p"], "same-id", "commit") == ["p"]
    assert acquire_test_lock(runs, "d", sha, ["answer:p"], "answer:same-id", "commit") == ["answer:p"]
    # An interrupted answer can resume with its own id while retrieval's profile set remains pinned separately.
    assert acquire_test_lock(runs, "d", sha, ["answer:p"], "answer:same-id", "commit") == ["answer:p"]
    mark_profile_complete(runs, "d", "answer:p", tmp_path / "answer-run")
    assert acquire_test_lock(runs, "d", sha, ["answer:p"], "answer:same-id", "commit") == []


def test_ci_cache_mode_and_verified_embedding_two_price():
    assert ans.default_cache_mode({"CI": "1"}) == "replay"
    assert ans.default_cache_mode({"SCRIBE_CACHE_MODE": "replay"}) == "replay"
    assert ans.default_cache_mode({}) == "live"
    assert cost.price_usd("gemini-embedding-2", cost.UNIT_TOKENS) == pytest.approx(0.20)
    assert "TO VERIFY" not in " ".join(p.source_note for p in cost.PRICES)


def test_cli_ci_defaults_to_replay_without_keys_and_exits_cleanly_on_miss(tmp_path, monkeypatch, capsys):
    ds = tmp_path / "dataset"
    ds.mkdir()
    case = make_case()
    (ds / "cases.jsonl").write_text(json.dumps(case.model_dump()) + "\n", encoding="utf-8")
    ranking = tmp_path / "rankings.jsonl"
    ranking.write_text(json.dumps({"case_id": case.id, "ranking": [{"doc": "p.md", "text": "Leave is 12 days", "score": 0.9}]}) + "\n",
                       encoding="utf-8")
    monkeypatch.setenv("CI", "1")
    monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
    result = main(["answer", "--dataset", str(ds), "--rankings", str(ranking), "--split", "dev", "--out", str(tmp_path / "out"),
                   "--verifier", "none", "--response-cache", str(tmp_path / "responses.jsonl"),
                   "--ledger", str(tmp_path / "ledger.jsonl"), "--runs-dir", str(tmp_path / "runs")])
    assert result == 2
    assert "cache miss" in capsys.readouterr().err
    assert not (tmp_path / "ledger.jsonl").exists()


def cli_inputs(tmp_path, split="dev", count=1):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    rows, rankings = [], []
    for index in range(count):
        row = make_case().model_dump()
        row.update(id=f"c{index}", split=split, reviewed=True, question=f"How long for policy {index}?")
        rows.append(row)
        rankings.append({"case_id": row["id"], "ranking": [
            {"doc": "p.md", "text": "Leave is 12 days", "score": 0.9}],
            "meta": {"config_hash": "retrieval-hash"}})
    (dataset / "cases.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    ranking_path = tmp_path / "rankings.jsonl"
    ranking_path.write_text("".join(json.dumps(row) + "\n" for row in rankings))
    argv = ["answer", "--dataset", str(dataset), "--rankings", str(ranking_path), "--split", split,
            "--profile", "p", "--out", str(tmp_path / "out"), "--cache-mode", "live",
            "--response-cache", str(tmp_path / "responses.jsonl"), "--ledger", str(tmp_path / "ledger.jsonl"),
            "--runs-dir", str(tmp_path / "runs")]
    return dataset, argv


def test_cli_answer_records_models_and_zero_cost_replay(tmp_path, monkeypatch):
    from scribe_eval import judge as jd

    _, argv = cli_inputs(tmp_path)
    generator, verifier, judge = CountingGenerator(), CountingVerifier(), CountingJudge()
    requested_models = []

    def build_generator(**kwargs):
        requested_models.append(kwargs["model"])
        return generator

    monkeypatch.setenv("GOOGLE_CHAT_MODEL", "must-not-override-flash")
    monkeypatch.setattr(ans, "GeminiGenerator", build_generator)
    monkeypatch.setattr(ans, "GeminiClaimVerifier", lambda: verifier)
    monkeypatch.setattr(jd, "GeminiJudge", lambda **kwargs: judge)
    assert main(argv) == 0
    run = json.loads(next((tmp_path / "out").glob("*/run.json")).read_text())
    assert requested_models == ["gemini-2.5-flash"]
    assert run["model_ids"] == {"generator": "gemini-2.5-flash", "verifier": "gemini-2.5-flash",
                                "judge": "gemini-2.5-pro"}
    assert run["retrieval_config_hash"] == "retrieval-hash"
    assert run["prompt_versions"]["semantic_judge"] == jd.PROMPT_VERSION
    paid = cost.spend_total(tmp_path / "ledger.jsonl")
    assert paid > 0
    generator.configured = verifier.configured = judge.configured = False
    assert main(argv + ["--cache-mode", "replay"]) == 0
    assert (generator.calls, verifier.calls, judge.calls) == (1, 1, 1)
    ledger = cost.read_ledger(tmp_path / "ledger.jsonl")
    assert ledger[-1]["usd"] == 0.0
    assert ledger[-1]["cached"] is True
    assert ledger[-1]["cached_calls"] == 3 and ledger[-1]["live_calls"] == 0
    assert cost.spend_total(tmp_path / "ledger.jsonl") == paid


def test_cli_milestone_output_failure_resumes_before_completion(tmp_path, monkeypatch):
    from scribe_eval.commands import answer as command
    from scribe_eval import run_profile as rp

    dataset, argv = cli_inputs(tmp_path, split="test")
    argv += ["--generator", "fake", "--verifier", "fake", "--judge", "fake",
             "--milestone-report", "--milestone-id", "m1"]
    durable_write = command._durable_write

    def fail_answer_write(path, text):
        if path.name == "answers.jsonl":
            raise OSError("scripted disk failure")
        durable_write(path, text)

    monkeypatch.setattr(command, "_durable_write", fail_answer_write)
    assert main(argv) == 2
    lock = rp.lock_path(tmp_path / "runs", dataset.name)
    assert json.loads(lock.read_text())["entries"]["answer:p"]["status"] == "started"
    with rp.test_split_execution_guard(tmp_path / "runs", dataset.name):
        pass  # a failed output write releases execution ownership, but does not complete the milestone

    completed = []
    mark_complete = rp.mark_profile_complete

    def check_complete(runs, name, profile, out):
        assert (out / "answers.jsonl").read_text().strip()
        assert (out / "report.md").read_text().startswith("# FAKE")
        assert json.loads((out / "run.json").read_text())["retrieval_config_hash"] == "retrieval-hash"
        with pytest.raises(rp.RunProfileError, match="already in progress"):
            with rp.test_split_execution_guard(runs, name):
                pytest.fail("execution guard was released before completion")
        completed.append(profile)
        mark_complete(runs, name, profile, out)

    monkeypatch.setattr(command, "_durable_write", durable_write)
    monkeypatch.setattr(rp, "mark_profile_complete", check_complete)
    assert main(argv) == 0
    assert completed == ["answer:p"]
    assert json.loads(lock.read_text())["entries"]["answer:p"]["status"] == "completed"
    monkeypatch.setattr(ans, "run_answers", lambda *a, **kw: pytest.fail("completed milestone was rescored"))
    assert main(argv) == 0
    assert main(argv + ["--milestone-id", "m2"]) == 2


def test_judge_sheet_caps_spend_and_preserves_partial_failure_usage(tmp_path, monkeypatch):
    from scribe_eval import judge as jd

    dataset, _ = cli_inputs(tmp_path, count=2)
    answer_run = tmp_path / "answer-run"
    answer_run.mkdir()
    (answer_run / "run.json").write_text(json.dumps({"measurement": True}))
    (answer_run / "answers.jsonl").write_text("".join(json.dumps({
        "case_id": f"c{i}", "category": "test", "answer": "Leave is 12 days."}) + "\n" for i in range(2)))

    class FailingJudge(CountingJudge):
        def judge(self, question, reference, answer):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("scripted failure")
            return "correct"

    judge = FailingJudge()
    monkeypatch.setattr(jd, "GeminiJudge", lambda **kwargs: judge)
    argv = ["judge-sheet", "--run", str(answer_run), "--dataset", str(dataset),
            "--out-dir", str(tmp_path / "sheet"), "--cache-mode", "live",
            "--response-cache", str(tmp_path / "judge-responses.jsonl"),
            "--ledger", str(tmp_path / "judge-ledger.jsonl")]
    assert main(argv + ["--spend-cap", "0"]) == 2
    assert judge.calls == 0
    assert main(argv) == 2
    assert judge.calls == 2
    entries = cost.read_ledger(tmp_path / "judge-ledger.jsonl")
    assert len(entries) == 1 and entries[0]["live_calls"] == 2
    assert entries[0]["usd"] > 0
    assert not (tmp_path / "sheet" / "judge_key.json").exists()
    assert main(argv + ["--cache-mode", "replay"]) == 2  # first hit, then fatal miss
    assert judge.calls == 2
    replay = cost.read_ledger(tmp_path / "judge-ledger.jsonl")[-1]
    assert replay["cached_calls"] == 1 and replay["live_calls"] == 0 and replay["usd"] == 0.0
