from types import SimpleNamespace

import pytest

from scribe_eval.run_profile import RunProfileError, check_profile, run


def test_test_split_refused(capsys):
    args = SimpleNamespace(split="test", database_url="postgresql://x/y_test")
    assert run(args) == 2
    assert "INV-12" in capsys.readouterr().err


def test_requires_database_url(capsys):
    assert run(SimpleNamespace(split="dev", database_url=None)) == 2


def test_rerank_profiles_refused_without_voyage_key():
    for name in ("dense-rerank", "hybrid-rrf-rerank"):
        with pytest.raises(RunProfileError, match="VOYAGE_API_KEY"):
            check_profile(name, {})
        check_profile(name, {"VOYAGE_API_KEY": "x"})


def test_no_rerank_profiles_need_no_key():
    for name in ("dense-only", "lexical-only", "hybrid-rrf"):
        check_profile(name, {})


def test_unknown_profile():
    with pytest.raises(RunProfileError, match="unknown profile"):
        check_profile("nope", {})


def test_refuses_non_test_database(capsys):
    assert run(SimpleNamespace(split="dev", database_url="postgresql://x@h/prod", profile="hybrid-rrf")) == 2
    assert "test" in capsys.readouterr().err
