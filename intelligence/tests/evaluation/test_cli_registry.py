"""The CLI registry must expose exactly the pre-refactor subcommands and options."""

from __future__ import annotations

import argparse
from importlib import import_module

from scribe_eval import cli
from scribe_eval.commands import MODULES, register_all

EXPECTED_COMMANDS = [
    "validate",
    "assign-splits",
    "score",
    "answer",
    "judge-sheet",
    "judge-agreement",
    "compare",
    "publish-run",
    "gate",
    "ingest-corpus",
    "run-profile",
    "review",
]

EXPECTED_OPTIONS = {
    "validate": {"--corpus", "--help", "--require-reviewed", "-h"},
    "assign-splits": {"--help", "--only-origin", "--seed", "-h"},
    "score": {"--dataset", "--help", "--milestone-report", "--model", "--out", "--profile", "--prompt", "--rankings", "--split", "-h"},
    "answer": {"--dataset", "--generator", "--help", "--milestone-report", "--out", "--profile", "--rankings", "--split", "--tau", "--top-k", "--verifier", "-h"},
    "judge-sheet": {"--dataset", "--help", "--judge", "--out-dir", "--run", "-h", "-n"},
    "judge-agreement": {"--help", "--key", "--labels", "--out", "-h"},
    "compare": {"--help", "-h"},
    "publish-run": {"--database-url", "--dataset", "--help", "--mark-fake", "--parse-lost", "--seeded", "-h"},
    "gate": {"--accept-seeded-baseline", "--baseline", "--cache", "--corpus", "--database-url", "--dataset", "--help", "--out", "--profile", "--refresh-cache", "--seeded", "--split", "--status", "--tau", "--write-baseline", "-h"},
    "ingest-corpus": {"--cache-path", "--corpus", "--database-url", "--embedder", "--help", "--profile", "--reset", "-h"},
    "run-profile": {"--cache-path", "--database-url", "--dataset", "--help", "--limit", "--out", "--profile", "--split", "-h"},
    "review": {"--corpus", "--help", "--items", "--kind", "--order", "--port", "--reviewer", "-h"},
}


def _built_subparsers() -> dict[str, argparse.ArgumentParser]:
    parser = argparse.ArgumentParser(prog="scribe-eval")
    sub = parser.add_subparsers(dest="command", required=True)
    register_all(sub)
    return dict(sub.choices)


def test_subcommand_names_and_order_unchanged() -> None:
    assert list(_built_subparsers()) == EXPECTED_COMMANDS


def test_option_strings_unchanged() -> None:
    subs = _built_subparsers()
    for name, expected in EXPECTED_OPTIONS.items():
        actual = {s for a in subs[name]._actions for s in a.option_strings}
        assert actual == expected, name


def test_register_all_on_fresh_parser_and_modules_have_register() -> None:
    register_all(argparse.ArgumentParser().add_subparsers())
    for name in MODULES:
        assert callable(import_module(f"scribe_eval.commands.{name}").register), name


def test_every_subcommand_has_a_handler() -> None:
    for name, p in _built_subparsers().items():
        assert callable(p.get_default("func")), name


def test_cli_main_still_exported() -> None:
    assert callable(cli.main)
